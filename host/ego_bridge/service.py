"""stdin/stdout JSON Lines 服务，给 DSH 的 TS 插件当后端（契约 v2 §6）。

协议：**每行一个 JSON 对象**，UTF-8，**不要 BOM**；stderr 只放日志。

插件 → Python（命令）::

    {"cmd": "hello", "version": 1}
    {"cmd": "set_state", "state": 1, "session": 0, "pending": 0}
    {"cmd": "sync_time", "epoch_ms": 1700000000000}
    {"cmd": "question_open", "req_id": 5, "n_options": 3, "q_index": 1}
    {"cmd": "question_close", "req_id": 5}
    {"cmd": "ping"}
    {"cmd": "shutdown"}

Python → 插件（事件）::

    {"evt": "ready", "version": 1, "port": "COM7", "sim": false}
    {"evt": "hello_ack", "version": 1, "port": "COM7", "sim": false,
     "link": true, "state": 1, "uptime_s": 42}
    {"evt": "link", "up": true, "reason": "frame received"}
    {"evt": "status", "state": 0, "session": 0, "pending": 0,
     "flags": 0, "uptime_s": 12}
    {"evt": "key", "key_id": 0, "event": 0, "state": 1}
    {"evt": "answer", "req_id": 5, "q_index": 1, "option": 2, "elapsed_ms": 1234}
    {"evt": "error", "code": "port_not_found", "message": "..."}
    {"evt": "pong"}

``ready`` 的语义是 **进程启动完成**，每次进程生命周期内只发一次，且先于
所有其它事件；``hello`` 的应答是 ``hello_ack``，不是第二个 ``ready``。

.. important::
   **stdout 只能有 JSON 行。** 任何调试输出、pyserial 报错、traceback 都必须走
   stderr（``log()`` 已固定写 stderr）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import threading
import traceback
from typing import Any, Dict, Iterable, List, Optional, TextIO

from .bridge import Bridge, LinkEvent
from .messages import (
    Answer,
    KeyEvent,
    QuestionClose,
    QuestionOpen,
    StatusReport,
    STATE_FREE,
)
from .transport import DEFAULT_BAUD, PortInfo, find_port, list_ports

__all__ = [
    "SERVICE_VERSION",
    "Service",
    "make_parser",
    "main",
]

#: 服务版本（与 DSH 插件握手时上报）
SERVICE_VERSION = 1

EXIT_OK = 0
EXIT_ERROR = 1


# ---------------------------------------------------------------- 日志


def log(text: str) -> None:
    """写日志到 **stderr**（stdout 只允许 JSON）。"""
    try:
        print(f"[ego-bridge] {text}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - 日志失败也不能影响服务
        pass


# ---------------------------------------------------------------- 服务


class Service:
    """JSON Lines 服务主体。

    :param port: 显式端口名；``None`` 自动发现；``"sim"`` 用模拟器。
    :param baud: 波特率，默认 115200。
    :param sim: ``True`` 时用 :mod:`ego_bridge.sim`（``--sim`` 等价于 ``--port sim``）。
    :param resync_hourly: 每小时补发一次 ``TIME_SYNC``。
    :param stdin: 命令输入流（默认 ``sys.stdin``）。
    :param stdout: 事件输出流（默认 ``sys.stdout``）。
    :param bridge: 注入已构造的 :class:`Bridge`（测试用）。
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = DEFAULT_BAUD,
        *,
        sim: bool = False,
        resync_hourly: bool = False,
        stdin: Optional[TextIO] = None,
        stdout: Optional[TextIO] = None,
        bridge: Optional[Bridge] = None,
        sim_fpga: Any = None,
    ) -> None:
        self.requested_port = port
        self.baud = baud
        self.sim = bool(sim) or (port == "sim")
        self.resync_hourly = resync_hourly
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout

        self.bridge: Optional[Bridge] = bridge
        self.sim_fpga = sim_fpga
        self.port_name: Optional[str] = "sim" if self.sim else port
        self._write_lock = threading.Lock()
        self._shutdown_evt = threading.Event()
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._started_at = datetime.datetime.now()

    # ---------------------------------------------------------------- 输出

    def emit(self, event: Dict[str, Any]) -> None:
        """输出一行 JSON 到 stdout（UTF-8、无 BOM、行尾 ``\\n``）。"""
        try:
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:  # pragma: no cover
            log(f"事件无法序列化，已丢弃：{exc}")
            return
        with self._write_lock:
            stream = self._stdout
            try:
                stream.write(line + "\n")
                stream.flush()
            except Exception as exc:  # noqa: BLE001 - stdout 断了就退出
                log(f"写 stdout 失败：{exc}")
                self._shutdown_evt.set()

    def emit_error(self, code: str, message: str) -> None:
        """发 ``error`` 事件，同时把细节记进 stderr。"""
        self.emit({"evt": "error", "code": code, "message": message})

    # ---------------------------------------------------------------- 启动

    def start(self) -> None:
        """建立链路并发 ``ready``，然后开始读 stdin。"""
        if self.sim or self.requested_port == "sim":
            self._start_sim()
        else:
            self._start_serial()

    def _start_sim(self) -> None:
        """启动模拟器（无板卡模式）。"""
        from .sim import SimFpga

        if self.sim_fpga is None:
            self.sim_fpga = SimFpga(heartbeat_s=1.0, threaded=True)
        if self.sim_fpga.on_log is None:
            self.sim_fpga.on_log = lambda text: log(f"[sim] {text}")
        if self.bridge is None:
            self.bridge = Bridge(
                port="sim",
                baudrate=self.baud,
                transport_factory=lambda: self.sim_fpga.transport,
                resync_hourly=self.resync_hourly,
                logger=log,
            )
        self.port_name = "sim"
        # ready 由 _wire_callbacks 统一发出，保证它先于任何 link/status。
        self._wire_callbacks()

    def _start_serial(self) -> None:
        """启动真实串口链路。**端口找不到也不崩** —— 发 error 后继续跑。"""
        port = self.requested_port
        try:
            if port is None:
                ports: List[PortInfo] = list_ports()
                port = find_port(None, ports=ports)
            self.port_name = port
        except Exception as exc:  # noqa: BLE001
            log(f"端口发现失败：{exc}")
            self.emit_error("port_not_found", str(exc))
            self.port_name = None

        if self.bridge is None:
            self.bridge = Bridge(
                port=self.port_name,
                baudrate=self.baud,
                resync_hourly=self.resync_hourly,
                logger=log,
            )
        # ready 由 _wire_callbacks 统一发出，保证它先于任何 link/status。
        self._wire_callbacks()

    def _wire_callbacks(self) -> None:
        bridge = self.bridge
        if bridge is None:
            return
        # ready 必须在**注册任何回调之前**发出：on_link() 会在注册时立刻
        # 补发一次当前链路状态，若 ready 排在它后面，订阅方读到的第一条
        # 事件就是 link 而不是 ready。契约 §6.2 规定 ready = 进程启动完成，
        # 它必须是这个进程发往 stdout 的第一条事件。
        self.emit(
            {
                "evt": "ready",
                "version": SERVICE_VERSION,
                "port": self.port_name or "",
                "sim": self.sim,
            }
        )
        bridge.on_link(self._handle_link)
        bridge.on_status(self._handle_status)
        bridge.on_key(self._handle_key)
        bridge.on_answer(self._handle_answer)

    # ---------------------------------------------------------------- 回调 → 事件

    def _handle_link(self, event: LinkEvent) -> None:
        payload = {"evt": "link", "up": event.up, "reason": event.reason}
        port = event.port or self.port_name
        if port:
            payload["port"] = port
        self.emit(payload)

    def _handle_status(self, report: StatusReport) -> None:
        self.emit(
            {
                "evt": "status",
                "state": report.state,
                "session": report.session,
                "pending": report.pending,
                "flags": report.flags,
                "uptime_s": report.uptime_s,
            }
        )

    def _handle_key(self, event: KeyEvent) -> None:
        self.emit(
            {
                "evt": "key",
                "key_id": event.key_id,
                "event": event.event,
                "state": event.state,
            }
        )

    def _handle_answer(self, answer: Answer) -> None:
        self.emit(
            {
                "evt": "answer",
                "req_id": answer.req_id,
                "q_index": answer.q_index,
                "option": answer.option,
                "elapsed_ms": answer.elapsed_ms,
            }
        )

    # ---------------------------------------------------------------- 命令处理

    def handle_command(self, command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理一条命令。返回要额外输出的事件（已由 :meth:`emit` 输出的返回 ``None``）。

        未知命令 → ``error`` 事件，**不退出**（插件发错不该拖垮服务）。
        """
        cmd = command.get("cmd")
        if not isinstance(cmd, str):
            self.emit_error("bad_command", f"缺少字符串字段 cmd：{command!r}")
            return None

        handler_name = f"_cmd_{cmd}"
        handler = getattr(self, handler_name, None)
        if handler is None:
            self.emit_error("unknown_command", f"未知命令：{cmd}")
            return None
        try:
            handler(command)
        except Exception as exc:  # noqa: BLE001 - 命令处理失败不外抛
            log(f"处理命令 {cmd} 失败：{exc}\n{traceback.format_exc()}")
            self.emit_error("command_failed", f"{cmd}: {exc}")
        return None

    def _cmd_hello(self, command: Dict[str, Any]) -> None:
        """应答 ``hello``：上报能力与当前链路快照。

        用 ``hello_ack`` 而不是 ``ready`` —— 契约 §6.2 里 ``ready`` 的语义是
        **进程启动完成**，只该出现一次；把命令应答也叫 ready 会让插件无法
        区分「进程起来了」和「我回了一句话」。
        """
        bridge = self.bridge
        link = bool(bridge.connected) if bridge is not None else False
        status = bridge.last_status if bridge is not None else None
        event: Dict[str, Any] = {
            "evt": "hello_ack",
            "version": SERVICE_VERSION,
            "port": self.port_name or "",
            "sim": self.sim,
            "link": link,
        }
        if status is not None:
            event["state"] = status.state
            event["uptime_s"] = status.uptime_s
        self.emit(event)

    def _cmd_ping(self, command: Dict[str, Any]) -> None:
        self.emit({"evt": "pong"})

    def _cmd_set_state(self, command: Dict[str, Any]) -> None:
        bridge = self.bridge
        state = command.get("state", STATE_FREE)
        session = command.get("session", 0)
        pending = command.get("pending", 0)
        if bridge is None:
            self.emit_error("no_link", "链路未建立，SET_STATE 未发送")
            return
        ok = bridge.set_state(int(state), int(session), int(pending))
        if not ok and not bridge.connected:
            # The link is still coming up. Bridge.set_state() already recorded
            # the desired values and replays them on connect, so this is a
            # deferral, not a loss -- reporting send_failed here would make a
            # perfectly healthy startup look like an error.
            log(f"STATE_SET deferred until the link is up: state={state}")
            return
        if not ok:
            self.emit_error("send_failed", "STATE_SET 发送失败")

    def _cmd_sync_time(self, command: Dict[str, Any]) -> None:
        bridge = self.bridge
        if bridge is None:
            self.emit_error("no_link", "链路未建立，TIME_SYNC 未发送")
            return
        epoch_ms = command.get("epoch_ms")
        moment = None
        if epoch_ms is not None:
            try:
                moment = datetime.datetime.fromtimestamp(int(epoch_ms) / 1000.0)
            except (TypeError, ValueError, OSError, OverflowError) as exc:
                self.emit_error("bad_epoch", f"epoch_ms 非法：{exc}")
                return
        if not bridge.sync_time(moment):
            if not bridge.connected:
                # Same reasoning as STATE_SET: the connect handshake sends
                # TIME_SYNC by itself, so an early request is already covered.
                log("TIME_SYNC deferred; the connect handshake will send one")
                return
            self.emit_error("send_failed", "TIME_SYNC 发送失败")

    def _cmd_question_open(self, command: Dict[str, Any]) -> None:
        bridge = self.bridge
        if bridge is None:
            self.emit_error("no_link", "链路未建立")
            return
        message = QuestionOpen(
            req_id=int(command.get("req_id", 0)),
            n_options=int(command.get("n_options", 1)),
            q_index=int(command.get("q_index", 1)),
        )
        if not bridge.send(message):
            self.emit_error("send_failed", "QUESTION_OPEN 发送失败")

    def _cmd_question_close(self, command: Dict[str, Any]) -> None:
        bridge = self.bridge
        if bridge is None:
            self.emit_error("no_link", "链路未建立")
            return
        message = QuestionClose(req_id=int(command.get("req_id", 0)))
        if not bridge.send(message):
            self.emit_error("send_failed", "QUESTION_CLOSE 发送失败")

    def _cmd_shutdown(self, command: Dict[str, Any]) -> None:
        self._shutdown_evt.set()

    # ---------------------------------------------------------------- 主循环

    def run(self) -> int:
        """启动并阻塞读 stdin，直到 ``shutdown`` / EOF / 出错。"""
        self.start()
        self._running = True
        try:
            self._read_commands()
        except KeyboardInterrupt:  # pragma: no cover - 交互中断
            log("收到 KeyboardInterrupt，退出")
        finally:
            self._running = False
            self.close()
        return EXIT_OK

    def _read_commands(self) -> None:
        stream = self._stdin
        while not self._shutdown_evt.is_set():
            line = stream.readline()
            if not line:
                log("stdin 已关闭，退出")
                break
            line = line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
            except json.JSONDecodeError as exc:
                self.emit_error("bad_json", f"不是合法 JSON：{exc}")
                continue
            if not isinstance(command, dict):
                self.emit_error("bad_command", f"命令行必须是 JSON 对象，收到 {type(command).__name__}")
                continue
            self.handle_command(command)

    def stop(self) -> None:
        self._shutdown_evt.set()

    def close(self) -> None:
        """关闭链路。**任何情况下都不抛异常。**"""
        bridge = self.bridge
        if bridge is not None:
            try:
                bridge.close()
            except Exception as exc:  # noqa: BLE001
                log(f"关闭 bridge 时异常（已忽略）：{exc}")
        if self.sim_fpga is not None:
            try:
                self.sim_fpga.close()
            except Exception as exc:  # noqa: BLE001
                log(f"关闭模拟器时异常（已忽略）：{exc}")


# ---------------------------------------------------------------- 命令行


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ego_bridge.service",
        description="EGO-dsh-controller 串口桥 JSON Lines 服务（契约 v2 §6）",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="串口名（如 COM7）。缺省自动按 CP210x VID/PID 发现；'sim' 用模拟器",
    )
    parser.add_argument(
        "--baud", type=int, default=DEFAULT_BAUD, help="波特率，默认 115200"
    )
    parser.add_argument(
        "--sim", action="store_true", help="使用模拟器（等价于 --port sim）"
    )
    parser.add_argument(
        "--resync-hourly",
        action="store_true",
        help="每小时补发一次 TIME_SYNC",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = make_parser().parse_args(list(argv) if argv is not None else None)

    # stdout 必须是 UTF-8 且不能带 BOM；Windows 默认编码可能是 GBK
    _force_utf8_stdio()

    port = args.port
    if args.sim and not port:
        port = "sim"

    service = Service(
        port=port,
        baud=args.baud,
        sim=bool(args.sim or port == "sim"),
        resync_hourly=args.resync_hourly,
    )
    try:
        return service.run()
    except Exception as exc:  # noqa: BLE001 - 兜底，绝不把 traceback 打到 stdout
        log(f"服务异常退出：{exc}\n{traceback.format_exc()}")
        return EXIT_ERROR


def _force_utf8_stdio() -> None:
    """把 stdin/stdout 强制成 UTF-8（UTF-8 无 BOM）。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover
            pass


if __name__ == "__main__":
    raise SystemExit(main())
