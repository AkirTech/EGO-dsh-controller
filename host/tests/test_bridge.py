"""bridge.py —— 连接握手、心跳监测、重连、fail-safe（契约 §3.4 / §4.2）。

全部用模拟器（``SimFpga``）跑，**不依赖真实串口**。
"""

from __future__ import annotations

import datetime
import queue
import threading
import time

import pytest

from ego_bridge.bridge import BACKOFF_SCHEDULE_S, Bridge, LinkEvent
from ego_bridge.messages import (
    Answer,
    KeyEvent,
    QuestionClose,
    QuestionOpen,
    StateSet,
    StatusReport,
    TimeSync,
    KEY_DOWN,
    KEY_LEFT,
    KEY_MIDDLE,
    KEY_RIGHT,
    KEY_UP,
    STATE_FREE,
    STATE_RUNNING,
    STATE_WAITING,
)
from ego_bridge.sim import SimFpga
from ego_bridge.transport import MemoryTransport, TransportError

from conftest import wait_until


def make_bridge(fpga: SimFpga, **kwargs) -> Bridge:
    kwargs.setdefault("heartbeat_timeout_s", 0.5)
    return Bridge(
        port="sim", transport_factory=lambda: fpga.transport, **kwargs
    )


# ---------------------------------------------------------------- 5. 端到端


def test_connect_receives_status_report():
    """连接 → 收到 STATUS_RPT。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0), "链路没起来"
            report = bridge.wait_for_status(timeout=3.0)
            assert isinstance(report, StatusReport)
        finally:
            bridge.close()


def test_handshake_sends_time_sync_and_state_set():
    """契约 §3.4 步骤 2/3：打开后立即发 TIME_SYNC + STATE_SET。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(
                lambda: len(fpga.received_messages) >= 2, timeout=3.0
            ), f"握手帧没收全：{fpga.received_messages}"

            types = [type(m).__name__ for m in fpga.received_messages[:2]]
            assert types == ["TimeSync", "StateSet"], types

            time_sync = fpga.received_messages[0]
            assert isinstance(time_sync, TimeSync)
            now = datetime.datetime.now()
            assert abs(time_sync.hour - now.hour) <= 1
            # day 用 isoweekday()：周一=1 … 周日=7
            assert 1 <= time_sync.day <= 7
            assert time_sync.day == now.isoweekday()

            state_set = fpga.received_messages[1]
            assert isinstance(state_set, StateSet)
            assert state_set.state == STATE_FREE, "默认状态必须是 FREE"
        finally:
            bridge.close()


def test_time_sync_day_is_iso_weekday():
    """``day`` 必须是 ``isoweekday()``（周一=1），不是 Python 的 ``weekday()``（周一=0）。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: fpga.received_messages, timeout=3.0)
            time_sync = fpga.received_messages[0]
            assert time_sync.day == datetime.datetime.now().isoweekday()
            assert time_sync.day >= 1, "weekday() 会是 0（周一），契约要求 1"
        finally:
            bridge.close()


def test_set_state_reaches_simulator():
    """``set_state(1)`` → 模拟器状态变 1。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            assert bridge.set_state(STATE_RUNNING, session=3, pending=2) is True
            assert wait_until(lambda: fpga.state == STATE_RUNNING, timeout=3.0)
            assert fpga.session == 3
            assert fpga.pending == 2
        finally:
            bridge.close()


def test_set_state_to_waiting():
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            bridge.set_state(STATE_WAITING)
            assert wait_until(lambda: fpga.state == STATE_WAITING, timeout=3.0)
        finally:
            bridge.close()


def test_press_key_yields_key_event():
    """``press_key`` → bridge 的 ``on_key`` 收到 ``KEY_EVT``。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        events = []
        bridge.on_key(events.append)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            bridge.set_state(STATE_RUNNING)
            assert wait_until(lambda: fpga.state == STATE_RUNNING, timeout=3.0)

            fpga.press_key(KEY_MIDDLE)
            assert wait_until(lambda: events, timeout=3.0), "没收到 KEY_EVT"
            event = events[0]
            assert isinstance(event, KeyEvent)
            assert event.key_id == KEY_MIDDLE
            assert event.state == STATE_RUNNING
        finally:
            bridge.close()


@pytest.mark.parametrize("key_id", [KEY_MIDDLE, KEY_LEFT, KEY_RIGHT])
def test_key_events_carry_key_id(key_id: int):
    """中/左/右三键都会上报 ``KEY_EVT``。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        events = []
        bridge.on_key(events.append)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            bridge.set_state(STATE_RUNNING)
            assert wait_until(lambda: fpga.state == STATE_RUNNING, timeout=3.0)
            fpga.press_key(key_id)
            assert wait_until(lambda: events, timeout=3.0)
            assert events[0].key_id == key_id
        finally:
            bridge.close()


def test_arrow_keys_outside_question_report_nothing():
    """非提问态按上/下键：FPGA 本地逻辑，**不上报** ``KEY_EVT``（契约 §3.2）。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        events = []
        bridge.on_key(events.append)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            bridge.set_state(STATE_RUNNING)
            assert wait_until(lambda: fpga.state == STATE_RUNNING, timeout=3.0)
            assert fpga.press_key(KEY_UP) is None
            assert fpga.press_key(KEY_DOWN) is None
            time.sleep(0.2)
            assert events == [], f"上/下键不该上报，却收到 {events}"
        finally:
            bridge.close()


def test_question_mode_answer_without_key_event():
    """**v2 分流规则端到端**：提问态按中键 → 收到 ANSWER，且**没有** KEY_EVT。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        answers = []
        keys = []
        bridge.on_answer(answers.append)
        bridge.on_key(keys.append)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)

            assert bridge.send(QuestionOpen(5, 3, 1)) is True
            assert wait_until(lambda: fpga.q_open, timeout=3.0)

            fpga.press_key(KEY_UP)  # 选到 1
            fpga.press_key(KEY_MIDDLE, elapsed_ms=1234)

            assert wait_until(lambda: answers, timeout=3.0), "没收到 ANSWER"
            answer = answers[0]
            assert isinstance(answer, Answer)
            assert answer.req_id == 5
            assert answer.q_index == 1
            assert answer.option == 1
            assert answer.elapsed_ms == 1234
            assert keys == [], f"提问态不该有 KEY_EVT，却收到 {keys}"
        finally:
            bridge.close()


def test_question_close_clears_question_state():
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            bridge.send(QuestionOpen(5, 3, 1))
            assert wait_until(lambda: fpga.q_open, timeout=3.0)
            bridge.send(QuestionClose(5))
            assert wait_until(lambda: not fpga.q_open, timeout=3.0)
        finally:
            bridge.close()


def test_send_does_not_block_waiting_for_ack():
    """v2 取消 ACK 等待：``send`` 之后立刻返回，不等任何确认。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            start = time.monotonic()
            for _ in range(20):
                bridge.set_state(STATE_RUNNING)
            elapsed = time.monotonic() - start
            assert elapsed < 1.0, f"20 次发送花了 {elapsed:.2f}s，像在等 ACK"
        finally:
            bridge.close()


# ---------------------------------------------------------------- 6. 心跳丢失


def test_heartbeat_loss_marks_link_down():
    """**连续 3 秒没有 STATUS_RPT → on_link(False)**（测试里用 0.3 s 加速）。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga, heartbeat_timeout_s=0.3)
        links = []
        bridge.on_link(links.append)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0), "链路没起来"
            assert wait_until(
                lambda: any(e.up for e in links), timeout=3.0
            ), f"链路没先 up 起来：{links}"

            fpga.pause_heartbeat()
            assert wait_until(
                lambda: any(not e.up for e in links[1:]), timeout=3.0
            ), f"心跳停了却没有 down 事件：{links}"

            down = [e for e in links if not e.up][-1]
            assert down.up is False
            assert "heartbeat" in down.reason.lower()
            assert bridge.connected is False
        finally:
            bridge.close()


def test_heartbeat_timeout_default_is_three_seconds():
    """契约 §4.2 的默认值是 3 秒。"""
    from ego_bridge.bridge import DEFAULT_HEARTBEAT_TIMEOUT_S

    assert DEFAULT_HEARTBEAT_TIMEOUT_S == 3.0

    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = Bridge(port="sim", transport_factory=lambda: fpga.transport)
        try:
            assert bridge.heartbeat_timeout_s == 3.0
        finally:
            bridge.close()


def test_link_recovers_after_heartbeat_resumes():
    """心跳恢复后能重新判定链路 up。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga, heartbeat_timeout_s=0.3)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            fpga.pause_heartbeat()
            assert wait_until(lambda: not bridge.connected, timeout=3.0)
            fpga.resume_heartbeat()
            assert wait_until(lambda: bridge.connected, timeout=3.0), "链路没恢复"
        finally:
            bridge.close()


def test_backoff_schedule_matches_contract():
    """指数退避 1/2/4/8 s，上限 8 s。"""
    assert BACKOFF_SCHEDULE_S == (1.0, 2.0, 4.0, 8.0)


def test_backoff_grows_and_caps():
    bridge = Bridge(port="sim", transport_factory=MemoryTransport, auto_start=False)
    try:
        delays = [bridge._next_backoff() for _ in range(6)]
        assert delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0], delays
    finally:
        bridge.close()


# ---------------------------------------------------------------- 板卡重启


def test_uptime_rollback_triggers_rehandshake():
    """``uptime_s`` 回退 → 判定重启 → 重发 TIME_SYNC + STATE_SET（契约 §3.4）。"""
    with SimFpga(heartbeat_s=0.05, start_uptime_s=10) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            wait_until(lambda: bridge.last_uptime_s is not None, timeout=3.0)
            first_time_syncs = bridge.stats["time_syncs"]
            assert first_time_syncs >= 1

            fpga.simulate_reboot()
            assert wait_until(
                lambda: bridge.stats["reboots_detected"] >= 1, timeout=3.0
            ), "没检测到重启"
            assert wait_until(
                lambda: bridge.stats["time_syncs"] > first_time_syncs, timeout=3.0
            ), "重启后没有重发 TIME_SYNC"
        finally:
            bridge.close()


def test_time_valid_false_triggers_resync():
    """``flags`` bit1 为 0（板卡没收到过 TIME_SYNC）→ 补发时间。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            assert wait_until(lambda: fpga.time_valid, timeout=3.0)
        finally:
            bridge.close()


def test_time_valid_resync_is_throttled():
    """``time_valid=0`` 时**限速**补发，不能变成每秒一帧校时风暴。

    契约 §3.2：「Python 层据此判断要不要补发时间，而不是盲目每秒重发」。
    """
    from ego_bridge.bridge import TIME_VALID_RESYNC_INTERVAL_S

    assert TIME_VALID_RESYNC_INTERVAL_S >= 1.0

    with SimFpga(heartbeat_s=0.02) as fpga:
        # 让模拟器永远报 time_valid=0
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            fpga.flags &= ~0x02  # 清掉「时间有效」
            before = bridge.stats["time_syncs"]
            time.sleep(0.5)  # 期间有 ~25 个心跳
            after = bridge.stats["time_syncs"]
            assert after - before <= 1, (
                f"0.5 s 内补发了 {after - before} 次 TIME_SYNC，没有限速"
            )
        finally:
            bridge.close()


# ---------------------------------------------------------------- fail-safe


def test_bridge_survives_open_failure():
    """串口打不开（没插板子）→ **不抛异常、不卡住**，只报 link down。"""

    def factory():
        raise TransportError("找不到 COM99")

    bridge = Bridge(port="COM99", transport_factory=factory, heartbeat_timeout_s=0.2)
    links = []
    bridge.on_link(links.append)
    try:
        time.sleep(0.3)
        assert bridge.connected is False
        assert links, "应当有 link down 事件"
        assert all(e.up is False for e in links)
    finally:
        bridge.close()  # 关闭不能抛


def test_port_not_found_is_reported_not_raised():
    """自动发现失败（本机只有蓝牙）也不能崩。"""
    from ego_bridge.transport import PortNotFoundError

    def factory():
        raise PortNotFoundError("没有找到 CP210x 串口", [])

    bridge = Bridge(port=None, transport_factory=factory)
    links = []
    bridge.on_link(links.append)
    try:
        assert wait_until(lambda: len(links) >= 2, timeout=3.0), f"只有 {links}"
        reasons = [e.reason for e in links]
        assert any("port not found" in r for r in reasons), reasons
        assert all(e.up is False for e in links if e.reason != "current")
    finally:
        bridge.close()


def test_send_on_closed_link_returns_false_without_raising():
    """没链路时发送 → 返回 ``False``，绝不抛异常。"""
    bridge = Bridge(
        port="sim",
        transport_factory=lambda: MemoryTransport(),
        auto_start=False,
    )
    try:
        # 没有 start()，transport 为 None
        assert bridge.send(StateSet(1, 0, 0)) is False
        assert bridge.set_state(1) is False
        assert bridge.sync_time() is False
        assert bridge.send_raw(b"\xAA\x55") is False
    finally:
        bridge.close()


def test_write_error_marks_link_down_without_raising():
    """写失败（拔线）→ 标记断开，不抛异常。"""

    class BrokenTransport(MemoryTransport):
        def write(self, data):
            raise TransportError("设备已拔出")

    transport = BrokenTransport()
    bridge = Bridge(
        port="sim",
        transport_factory=lambda: transport,
        heartbeat_timeout_s=0.2,
    )
    try:
        # 找一个「端口已打开」的时刻（握手阶段写就会失败，所以直接调 _write）
        bridge.start()
        time.sleep(0.05)
        bridge._transport = transport  # 确保 transport 已挂上
        assert bridge.send(StateSet(1, 0, 0)) is False
        assert bridge.connected is False
    finally:
        bridge.close()


def test_callback_exception_does_not_kill_link():
    """宿主回调抛异常不能带崩链路线程。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)

        def boom(_):
            raise RuntimeError("宿主回调写错了")

        bridge.on_status(boom)
        bridge.on_link(boom)
        bridge.on_key(boom)
        try:
            assert wait_until(lambda: bridge.is_running, timeout=2.0)
            assert wait_until(lambda: bridge.last_status is not None, timeout=3.0)
            # 回调炸了，链路线程还活着
            assert bridge.connected is True
            assert bridge.is_running is True
        finally:
            bridge.close()


def test_stop_is_idempotent_and_quick():
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        assert wait_until(lambda: bridge.connected, timeout=3.0)
        start = time.monotonic()
        bridge.stop()
        bridge.stop()
        bridge.close()
        elapsed = time.monotonic() - start
        assert elapsed < 3.0, f"stop() 卡了 {elapsed:.2f}s"


def test_bad_frame_from_board_does_not_break_bridge():
    """板卡发来坏帧 → 丢弃，链路继续工作。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        events = []
        bridge.on_key(events.append)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            fpga.inject_bad_frame(0x83, bytes(6))
            fpga.inject_garbage(b"\x00\xFF\xAA\x55")
            assert wait_until(lambda: bridge.stats["bad_frames"] >= 1, timeout=3.0)
            # 之后仍能收到按键
            bridge.set_state(STATE_RUNNING)
            assert wait_until(lambda: fpga.state == STATE_RUNNING, timeout=3.0)
            fpga.press_key(KEY_MIDDLE)
            assert wait_until(lambda: events, timeout=3.0)
        finally:
            bridge.close()


# ---------------------------------------------------------------- 队列交付


def test_queue_delivers_messages():
    """独立读线程 + ``queue.Queue`` 交付上层（契约 §4.2）。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            bridge.set_state(STATE_RUNNING)
            assert wait_until(lambda: fpga.state == STATE_RUNNING, timeout=3.0)
            fpga.press_key(KEY_LEFT)

            kinds = []
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    item = bridge.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                kinds.append(type(item).__name__)
                if "KeyEvent" in kinds and "StatusReport" in kinds:
                    break
            assert "StatusReport" in kinds
            assert "KeyEvent" in kinds
        finally:
            bridge.close()


# ---------------------------------------------------------------- 每小时补发


def test_resync_hourly_off_by_default():
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert bridge.resync_hourly is False
        finally:
            bridge.close()


def test_resync_hourly_enabled_sends_on_connect():
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga, resync_hourly=True)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            assert bridge.stats["time_syncs"] >= 1
        finally:
            bridge.close()


def test_explicit_time_sync_message():
    with SimFpga(heartbeat_s=0.05) as fpga:
        bridge = make_bridge(fpga)
        try:
            assert wait_until(lambda: bridge.connected, timeout=3.0)
            moment = datetime.datetime(2026, 11, 6, 12, 34, 56)  # 周五
            assert bridge.sync_time(moment) is True
            assert wait_until(
                lambda: fpga.last_time_sync == TimeSync(12, 34, 56, 5), timeout=3.0
            )
        finally:
            bridge.close()
