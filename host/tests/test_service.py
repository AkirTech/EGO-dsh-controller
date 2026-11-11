"""service.py —— JSON Lines 服务（契约 §6）。

两条路都测：

1. **进程内**直接调 :class:`Service` / :meth:`Service.handle_command`（快、好调试）
2. **子进程**真跑 ``python -m ego_bridge.service --sim``（验证 stdout 干净、无 BOM）
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ego_bridge.bridge import Bridge
from ego_bridge.service import SERVICE_VERSION, Service, make_parser
from ego_bridge.sim import SimFpga
from ego_bridge.messages import StateSet, TimeSync

from conftest import HOST_DIR, wait_until


# ---------------------------------------------------------------- 进程内


class _Capture:
    """把 Service 的 stdout 收集成事件列表。"""

    def __init__(self):
        self.stream = io.StringIO()
        self.service: Service | None = None

    def events(self):
        return [
            json.loads(line)
            for line in self.stream.getvalue().splitlines()
            if line.strip()
        ]


@pytest.fixture
def service_env():
    """构造一个连到模拟器的 Service，捕获它输出的事件。"""
    fpga = SimFpga(heartbeat_s=0.05, threaded=True)
    capture = _Capture()
    bridge = Bridge(
        port="sim",
        transport_factory=lambda: fpga.transport,
        heartbeat_timeout_s=0.5,
    )
    service = Service(
        port="sim",
        sim=True,
        stdout=capture.stream,
        bridge=bridge,
        sim_fpga=fpga,
    )
    service.start()
    capture.service = service
    try:
        yield capture, fpga, service
    finally:
        service.close()


def test_ready_event_on_start(service_env):
    """启动 → 第一条事件必须是 ``ready``，且只发一次。

    ``ready`` 的语义是「进程启动完成」（契约 §6.2）。它必须排在任何
    ``link`` / ``status`` 之前，否则订阅方第一眼看到的是链路状态而不是
    进程已就绪，无法区分「还没起来」和「起来了但没连上」。
    """
    capture, fpga, service = service_env
    assert wait_until(lambda: len(capture.events()) > 1, timeout=3.0)
    events = capture.events()
    assert events[0]["evt"] == "ready", f"第一条事件是 {events[0]['evt']}，不是 ready"
    assert events[0]["version"] == SERVICE_VERSION
    assert events[0]["sim"] is True
    assert events[0]["port"] == "sim"
    # ready 每个进程生命周期只出现一次。
    assert sum(1 for e in events if e["evt"] == "ready") == 1


def test_hello_returns_hello_ack(service_env):
    """``hello`` → ``hello_ack``（不是第二个 ``ready``）。"""
    capture, fpga, service = service_env
    assert wait_until(lambda: capture.events(), timeout=3.0)
    before = len(capture.events())
    service.handle_command({"cmd": "hello", "version": 1})
    assert wait_until(lambda: len(capture.events()) > before, timeout=2.0)
    event = capture.events()[-1]
    assert event["evt"] == "hello_ack"
    assert event["version"] == SERVICE_VERSION
    assert "link" in event


def test_ping_returns_pong(service_env):
    """``ping`` → ``pong``。"""
    capture, fpga, service = service_env
    assert wait_until(lambda: capture.events(), timeout=3.0)
    service.handle_command({"cmd": "ping"})
    assert wait_until(
        lambda: any(e["evt"] == "pong" for e in capture.events()), timeout=2.0
    )


def test_set_state_converted_to_frame(service_env):
    """``set_state`` 被转成 ``STATE_SET`` 帧（模拟器状态真的变了）。"""
    capture, fpga, service = service_env
    assert wait_until(lambda: service.bridge.connected, timeout=3.0)
    service.handle_command(
        {"cmd": "set_state", "state": 1, "session": 4, "pending": 2}
    )
    assert wait_until(lambda: fpga.state == 1, timeout=3.0)
    assert fpga.session == 4
    assert fpga.pending == 2


def test_sync_time_converted_to_frame(service_env):
    """``sync_time`` 带 ``epoch_ms`` → ``TIME_SYNC``。"""
    capture, fpga, service = service_env
    assert wait_until(lambda: service.bridge.connected, timeout=3.0)
    # 2026-11-06 12:34:56 本地时间（周五）
    epoch_ms = int(
        __import__("datetime")
        .datetime(2026, 11, 6, 12, 34, 56)
        .timestamp()
        * 1000
    )
    service.handle_command({"cmd": "sync_time", "epoch_ms": epoch_ms})
    assert wait_until(
        lambda: fpga.last_time_sync == TimeSync(12, 34, 56, 5), timeout=3.0
    ), f"模拟器收到 {fpga.last_time_sync}"


def test_sync_time_without_epoch_uses_now(service_env):
    capture, fpga, service = service_env
    assert wait_until(lambda: service.bridge.connected, timeout=3.0)
    before = len(
        [m for m in fpga.received_messages if isinstance(m, TimeSync)]
    )
    service.handle_command({"cmd": "sync_time"})
    assert wait_until(
        lambda: len([m for m in fpga.received_messages if isinstance(m, TimeSync)])
        > before,
        timeout=3.0,
    )


def test_question_open_and_close(service_env):
    capture, fpga, service = service_env
    assert wait_until(lambda: service.bridge.connected, timeout=3.0)
    service.handle_command(
        {"cmd": "question_open", "req_id": 5, "n_options": 3, "q_index": 1}
    )
    assert wait_until(lambda: fpga.q_open, timeout=3.0)

    service.handle_command({"cmd": "question_close", "req_id": 5})
    assert wait_until(lambda: not fpga.q_open, timeout=3.0)


def test_status_event_is_forwarded(service_env):
    """``STATUS_RPT`` → ``status`` 事件，字段齐全。"""
    capture, fpga, service = service_env
    assert wait_until(
        lambda: any(e["evt"] == "status" for e in capture.events()), timeout=3.0
    ), f"没有 status 事件：{capture.events()}"
    status = [e for e in capture.events() if e["evt"] == "status"][0]
    for field in ("state", "session", "pending", "flags", "uptime_s"):
        assert field in status, f"status 缺字段 {field}"


def test_status_is_deduplicated(service_env):
    """``status`` 只在变化时发（契约 §6.2：已去重）。"""
    capture, fpga, service = service_env
    assert wait_until(
        lambda: any(e["evt"] == "status" for e in capture.events()), timeout=3.0
    )
    # 状态不变的情况下多等几个心跳周期
    before = len([e for e in capture.events() if e["evt"] == "status"])
    time.sleep(0.4)  # 心跳 0.05 s，应该有 ~8 次上报
    after = len([e for e in capture.events() if e["evt"] == "status"])
    assert after == before, f"status 没有去重：{before} → {after}"


def test_key_event_forwarded(service_env):
    capture, fpga, service = service_env
    assert wait_until(lambda: service.bridge.connected, timeout=3.0)
    service.handle_command({"cmd": "set_state", "state": 1})
    assert wait_until(lambda: fpga.state == 1, timeout=3.0)
    fpga.press_key(0)
    assert wait_until(
        lambda: any(e["evt"] == "key" for e in capture.events()), timeout=3.0
    )
    event = [e for e in capture.events() if e["evt"] == "key"][0]
    assert event["key_id"] == 0
    assert "event" in event and "state" in event


def test_answer_event_forwarded(service_env):
    capture, fpga, service = service_env
    assert wait_until(lambda: service.bridge.connected, timeout=3.0)
    service.handle_command(
        {"cmd": "question_open", "req_id": 5, "n_options": 3, "q_index": 1}
    )
    assert wait_until(lambda: fpga.q_open, timeout=3.0)
    fpga.press_key(0, elapsed_ms=1234)
    assert wait_until(
        lambda: any(e["evt"] == "answer" for e in capture.events()), timeout=3.0
    )
    answer = [e for e in capture.events() if e["evt"] == "answer"][0]
    assert answer["req_id"] == 5
    assert answer["option"] == 0
    assert answer["elapsed_ms"] == 1234


def test_unknown_command_yields_error(service_env):
    capture, fpga, service = service_env
    service.handle_command({"cmd": "不存在的命令"})
    errors = [e for e in capture.events() if e["evt"] == "error"]
    assert errors and errors[-1]["code"] == "unknown_command"


def test_bad_json_line_yields_error(service_env):
    capture, fpga, service = service_env
    service._stdin = io.StringIO("这不是 JSON\n")
    service._read_commands()
    errors = [e for e in capture.events() if e["evt"] == "error"]
    assert errors and errors[-1]["code"] == "bad_json"


def test_non_object_line_yields_error(service_env):
    capture, fpga, service = service_env
    service._stdin = io.StringIO("[1, 2, 3]\n")
    service._read_commands()
    errors = [e for e in capture.events() if e["evt"] == "error"]
    assert errors and errors[-1]["code"] == "bad_command"


def test_shutdown_stops_reading(service_env):
    capture, fpga, service = service_env
    service._stdin = io.StringIO('{"cmd": "shutdown"}\n{"cmd": "ping"}\n')
    service._read_commands()
    # shutdown 之后不应再读下一行
    assert not any(e["evt"] == "pong" for e in capture.events())


def test_events_are_single_line_json(service_env):
    """每行一个 JSON 对象，且能逐行解析。"""
    capture, fpga, service = service_env
    assert wait_until(lambda: len(capture.events()) >= 2, timeout=3.0)
    raw = capture.stream.getvalue()
    for line in raw.splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        assert isinstance(parsed, dict)
        assert "evt" in parsed, f"事件缺 evt 字段：{parsed}"


def test_no_bom_in_output(service_env):
    capture, fpga, service = service_env
    assert wait_until(lambda: capture.events(), timeout=3.0)
    raw = capture.stream.getvalue()
    assert not raw.startswith("\ufeff"), "输出不能带 BOM"


# ---------------------------------------------------------------- 端口发现失败


def test_service_survives_missing_port():
    """没有板卡时发 ``error``，然后**继续运行**（fail-safe）。"""
    capture = _Capture()
    service = Service(port=None, sim=False, stdout=capture.stream)
    # 打桩：让端口发现永远失败
    import ego_bridge.service as service_module

    original = service_module.find_port

    def boom(*args, **kwargs):
        raise service_module.PortNotFoundError("没有找到 CP210x 串口", [])

    service_module.find_port = boom
    try:
        service.start()
        events = capture.events()
        assert any(e["evt"] == "error" for e in events), events
        error = [e for e in events if e["evt"] == "error"][0]
        assert error["code"] == "port_not_found"
        # 仍然报 ready（进程活着），只是 port 为空
        assert any(e["evt"] == "ready" for e in events)
    finally:
        service_module.find_port = original
        service.close()


# ---------------------------------------------------------------- 命令行参数


def test_parser_defaults():
    args = make_parser().parse_args([])
    assert args.port is None
    assert args.baud == 115200
    assert args.sim is False
    assert args.resync_hourly is False


def test_parser_sim_flag():
    args = make_parser().parse_args(["--sim"])
    assert args.sim is True


def test_parser_port_and_baud():
    args = make_parser().parse_args(["--port", "COM7", "--baud", "9600"])
    assert args.port == "COM7"
    assert args.baud == 9600


def test_parser_resync_hourly():
    args = make_parser().parse_args(["--resync-hourly"])
    assert args.resync_hourly is True


# ---------------------------------------------------------------- 子进程真跑


def _run_service(lines: list, timeout: float = 15.0):
    """真起一个 ``python -m ego_bridge.service --sim`` 子进程，喂命令、收事件。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HOST_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-m", "ego_bridge.service", "--sim"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(HOST_DIR),
        env=env,
    )
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n"
    try:
        stdout, stderr = proc.communicate(
            payload.encode("utf-8"), timeout=timeout
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise AssertionError(
            f"服务没有在 {timeout}s 内退出\nstdout={stdout!r}\nstderr={stderr!r}"
        )
    return proc.returncode, stdout, stderr


def test_subprocess_hello_ping_shutdown():
    """子进程：``hello`` → ``ready``、``ping`` → ``pong``、``shutdown`` 退出。"""
    code, stdout, stderr = _run_service(
        [
            {"cmd": "hello", "version": 1},
            {"cmd": "ping"},
            {"cmd": "shutdown"},
        ]
    )
    assert code == 0, f"退出码 {code}\nstderr={stderr.decode('utf-8', 'replace')}"

    # stdout 全是 JSON 行
    lines = [l for l in stdout.decode("utf-8").splitlines() if l.strip()]
    assert lines, "没有任何输出"
    events = [json.loads(l) for l in lines]
    kinds = [e["evt"] for e in events]
    assert "ready" in kinds, kinds
    assert "pong" in kinds, kinds


def test_subprocess_stdout_is_clean_json():
    """**stdout 只能有 JSON 行**：任何一行都必须能 ``json.loads``。"""
    code, stdout, stderr = _run_service(
        [{"cmd": "hello"}, {"cmd": "ping"}, {"cmd": "shutdown"}]
    )
    text = stdout.decode("utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        json.loads(line)  # 抛异常就说明 stdout 被污染了

    assert not text.startswith("\ufeff"), "stdout 不能有 BOM"
    # stderr 里才有日志
    assert b"[ego-bridge]" in stderr or stderr == b""


def test_subprocess_set_state_sends_frame():
    """子进程模式下 ``set_state`` 生效：模拟器回 ``status state=1``。

    注意必须给心跳留时间：一次性把所有命令灌进 stdin 后立刻 ``shutdown``，
    板卡来不及发帧，所以这里逐条写并等 ``status`` 变化。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HOST_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-m", "ego_bridge.service", "--sim"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(HOST_DIR),
        env=env,
    )

    def send(command: dict) -> None:
        proc.stdin.write((json.dumps(command) + "\n").encode("utf-8"))
        proc.stdin.flush()

    try:
        send({"cmd": "hello"})
        time.sleep(0.6)  # 等链路起来 + 第一帧 STATUS_RPT
        send({"cmd": "set_state", "state": 1, "session": 2, "pending": 0})
        time.sleep(0.6)
        send({"cmd": "shutdown"})
        stdout, _ = proc.communicate(timeout=15.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    events = [json.loads(l) for l in stdout.decode("utf-8").splitlines() if l.strip()]
    assert not [e for e in events if e["evt"] == "error"], events
    statuses = [e for e in events if e["evt"] == "status"]
    assert any(e.get("state") == 1 for e in statuses), statuses
    assert proc.returncode == 0


def test_subprocess_bad_json_reported_not_fatal():
    """坏 JSON：报 ``error`` 事件，进程不退出。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HOST_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, "-m", "ego_bridge.service", "--sim"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(HOST_DIR),
        env=env,
    )
    payload = '这不是 JSON\n{"cmd": "ping"}\n{"cmd": "shutdown"}\n'
    stdout, stderr = proc.communicate(payload.encode("utf-8"), timeout=15.0)
    events = [json.loads(l) for l in stdout.decode("utf-8").splitlines() if l.strip()]
    assert any(e["evt"] == "error" and e["code"] == "bad_json" for e in events)
    assert any(e["evt"] == "pong" for e in events)
    assert proc.returncode == 0
