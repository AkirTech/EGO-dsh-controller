"""sim.py —— 假 FPGA 的行为（契约 §3.2 分流规则 / §3.4 握手）。

模拟器单独测一遍，让 bridge 的失败能归因到正确的一侧。
"""

from __future__ import annotations

import time

import pytest

from ego_bridge.link import FrameParser
from ego_bridge.messages import (
    Answer,
    QuestionClose,
    QuestionOpen,
    StateSet,
    StatusReport,
    TimeSync,
    KEY_DOWN,
    KEY_MIDDLE,
    KEY_UP,
    EVENT_PRESS,
    STATE_FREE,
    STATE_RUNNING,
    STATE_WAITING,
)
from ego_bridge.protocol import decode_frame, format_hex
from ego_bridge.sim import SimFpga

from conftest import wait_until


def drain_frames(fpga: SimFpga):
    """把模拟器当前发给主机方向的字节全部解析成帧。"""
    data = bytes(fpga.transport.to_host)
    del fpga.transport.to_host[:]
    return FrameParser().feed_bytes(data)


def send_to_fpga(fpga: SimFpga, message) -> None:
    """模拟主机发一条消息，并让模拟器处理它。"""
    fpga.transport.write(message.to_bytes())
    fpga.pump()


# ---------------------------------------------------------------- 心跳


def test_heartbeat_is_emitted_periodically():
    """每秒（这里 0.05 s）一帧 STATUS_RPT。"""
    with SimFpga(heartbeat_s=0.05) as fpga:
        time.sleep(0.2)
        frames = drain_frames(fpga)
        statuses = [f for f in frames if f.type_ == 0x83]
        assert len(statuses) >= 2, f"0.2 s 内只收到 {len(statuses)} 帧心跳"
        report = decode_frame(statuses[0].encode())
        assert report.payload[0] in (0, 1, 2)


def test_heartbeat_can_be_paused():
    """暂停心跳后不再有新帧 —— bridge 的断链判定依赖这个。"""
    with SimFpga(heartbeat_s=0.02) as fpga:
        time.sleep(0.05)
        fpga.pause_heartbeat()
        drain_frames(fpga)
        time.sleep(0.2)
        assert drain_frames(fpga) == []


def test_status_reports_uptime_increasing():
    with SimFpga(heartbeat_s=0.05) as fpga:
        first = fpga.emit_status()
        time.sleep(0.05)
        # 直接读 uptime_s 的墙钟推进
        fpga.pump()
        assert fpga.uptime_s >= first.uptime_s


# ---------------------------------------------------------------- 命令响应


def test_time_sync_sets_time_valid():
    """``TIME_SYNC`` 记录时间并把 ``time_valid`` 置 1（契约 §3.2 bit1）。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        assert fpga.time_valid is False
        send_to_fpga(fpga, TimeSync(12, 34, 56, 5))

        assert fpga.time_valid is True
        assert fpga.last_time_sync == TimeSync(12, 34, 56, 5)
        assert fpga.flags & 0x02

        statuses = [f for f in drain_frames(fpga) if f.type_ == 0x83]
        assert statuses, "时间装载后应立即补发一帧 STATUS_RPT"


def test_state_set_changes_state():
    with SimFpga(heartbeat_s=10.0) as fpga:
        assert fpga.state == STATE_FREE
        send_to_fpga(fpga, StateSet(STATE_RUNNING, 2, 1))
        assert fpga.state == STATE_RUNNING
        assert fpga.session == 2
        assert fpga.pending == 1


def test_state_set_to_waiting():
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, StateSet(STATE_WAITING, 0, 0))
        assert fpga.state == STATE_WAITING


def test_state_set_invalid_state_ignored_and_nacked():
    """state=3：不发 NACK 的写法见契约 §3.1，这里按 NACK 条目实现并断言。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, StateSet(STATE_RUNNING, 0, 0))
        frames = drain_frames(fpga)
        fpga.transport.write(bytes([0xAA, 0x55, 0x02, 0x03, 0x03, 0x00, 0x00, 0x00]))
        # 手工修 CRC 才能让模拟器"收到合法帧但语义不支持"
        raw = bytearray([0xAA, 0x55, 0x02, 0x03, 0x03, 0x00, 0x00])
        from ego_bridge.protocol import crc8

        raw = bytes(raw) + bytes([crc8(bytes(raw[2:]))])
        del fpga.transport.to_fpga[:]
        fpga.transport.write(raw)
        fpga.pump()
        assert fpga.state == STATE_RUNNING, "非法 state 不能改状态"
        nacks = [f for f in drain_frames(fpga) if f.type_ == 0xFF]
        assert nacks, "应当回 NACK"


def test_question_open_and_close():
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, QuestionOpen(5, 3, 1))
        assert fpga.q_open is True
        assert fpga.req_id == 5
        assert fpga.n_options == 3
        assert fpga.q_index == 1
        assert fpga.option_sel == 0
        assert fpga.state == STATE_WAITING

        send_to_fpga(fpga, QuestionClose(5))
        assert fpga.q_open is False
        assert fpga.option_sel == 0


def test_question_open_rejects_more_than_nine_options():
    """上位机本该拒绝 >9；直接塞进来时模拟器回 NACK（与真板一致）。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        raw = bytes([0xAA, 0x55, 0x03, 0x03, 0x05, 0x0A, 0x01])
        from ego_bridge.protocol import crc8

        raw = raw + bytes([crc8(raw[2:])])
        fpga.transport.write(raw)
        fpga.pump()
        assert fpga.q_open is False
        assert [f for f in drain_frames(fpga) if f.type_ == 0xFF]


# ---------------------------------------------------------------- 按键


def test_press_key_emits_key_event():
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, StateSet(STATE_RUNNING, 0, 0))
        drain_frames(fpga)

        message = fpga.press_key(KEY_MIDDLE)
        assert isinstance(message, KeyEventLike)

        frames = drain_frames(fpga)
        assert len(frames) == 1
        assert frames[0].type_ == 0x81
        from ego_bridge.messages import KeyEvent

        event = KeyEvent.unpack(frames[0].payload)
        assert event.key_id == KEY_MIDDLE
        assert event.state == STATE_RUNNING


def test_press_key_in_question_emits_answer_only():
    """**v2 分流规则**：提问态按中键 → 只有 ANSWER，没有 KEY_EVT。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, QuestionOpen(5, 3, 1))
        drain_frames(fpga)

        message = fpga.press_key(KEY_MIDDLE, elapsed_ms=1234)
        assert isinstance(message, Answer)

        frames = drain_frames(fpga)
        assert len(frames) == 1, f"应当只有 1 帧，实际 {[f.type_ for f in frames]}"
        assert frames[0].type_ == 0x82, "提问态中键必须是 ANSWER，不能是 KEY_EVT"
        assert 0x81 not in [f.type_ for f in frames], "不能同时发 KEY_EVT"

        answer = Answer.unpack(frames[0].payload)
        assert answer == Answer(5, 1, 0, 1234)


def test_answer_elapsed_ms_measured_by_wall_clock():
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, QuestionOpen(7, 2, 1))
        drain_frames(fpga)
        time.sleep(0.05)
        answer = fpga.press_key(KEY_MIDDLE)
        assert answer.elapsed_ms >= 40, f"elapsed_ms={answer.elapsed_ms} 太小"


def test_answer_elapsed_ms_saturates():
    """elapsed_ms 在 65535 处饱和，不回绕（契约 §3.2）。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, QuestionOpen(7, 2, 1))
        answer = fpga.press_key(KEY_MIDDLE, elapsed_ms=70000)
        assert answer.elapsed_ms == 65535


def test_arrow_keys_change_option_locally_without_frames():
    """上/下键是 FPGA 本地逻辑，**不发任何帧**（契约 §3.2 末尾）。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, QuestionOpen(5, 3, 1))
        drain_frames(fpga)

        assert fpga.press_key(KEY_UP) is None
        assert fpga.option_sel == 1
        assert drain_frames(fpga) == [], "上键不该发帧"

        assert fpga.press_key(KEY_DOWN) is None
        assert fpga.option_sel == 0
        assert drain_frames(fpga) == [], "下键不该发帧"


def test_option_selection_wraps_within_n_options():
    """选项范围受 ``n_options`` 限制（契约 §3.1：活动上限 = n_options-1）。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, QuestionOpen(5, 3, 1))
        for _ in range(3):
            fpga.press_key(KEY_UP)
        assert fpga.option_sel == 0, "3 个选项，按 3 次上键应回绕到 0"
        fpga.press_key(KEY_DOWN)
        assert fpga.option_sel == 2, "从 0 按一次下键应回绕到 2"


def test_answer_reports_current_option():
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, QuestionOpen(5, 4, 2))
        fpga.press_key(KEY_UP)
        fpga.press_key(KEY_UP)
        drain_frames(fpga)
        answer = fpga.press_key(KEY_MIDDLE, elapsed_ms=42)
        assert answer.option == 2
        assert answer.req_id == 5
        assert answer.q_index == 2


# ---------------------------------------------------------------- 坏帧注入


def test_bad_crc_injection_reaches_host_side():
    """``inject_bad_frame`` 把坏帧塞进 FPGA→PC 方向，用来验证**上位机**不崩。

    模拟器自身状态不受影响，主机侧的解析器应当丢弃它。
    """
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, StateSet(STATE_RUNNING, 0, 0))
        assert fpga.state == STATE_RUNNING
        drain_frames(fpga)

        bad = fpga.inject_bad_frame(0x83, bytes([0x00, 0x00, 0x00, 0x00, 0x01, 0x00]))
        # 坏帧确实在主机接收方向上
        received = bytes(fpga.transport.to_host)
        assert received == bad

        # 主机解析器丢弃它，不产出帧
        frames = FrameParser().feed_bytes(received)
        assert frames == []

        # 模拟器自身不受影响：状态没变，仍能正常响应
        assert fpga.state == STATE_RUNNING
        del fpga.transport.to_host[:]
        send_to_fpga(fpga, StateSet(STATE_WAITING, 0, 0))
        assert fpga.state == STATE_WAITING


def test_bad_frame_toward_fpga_is_counted():
    """反方向（PC→FPGA）塞坏帧：模拟器计入 bad_frames 且不改状态。"""
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, StateSet(STATE_RUNNING, 0, 0))
        from ego_bridge.protocol import crc8

        raw = bytearray([0xAA, 0x55, 0x02, 0x03, 0x00, 0x00, 0x00])
        raw.append(crc8(bytes(raw[2:])) ^ 0xFF)  # CRC 故意错
        fpga.transport.write(bytes(raw))
        fpga.pump()

        assert fpga.state == STATE_RUNNING, "坏帧不能改状态"
        assert fpga.stats.bad_frames == 1

        # 之后仍能正常收帧
        send_to_fpga(fpga, StateSet(STATE_WAITING, 0, 0))
        assert fpga.state == STATE_WAITING


def test_garbage_injection_does_not_crash():
    with SimFpga(heartbeat_s=10.0) as fpga:
        fpga.inject_garbage(b"\x00\xFF\xAA\xAA\x55")
        fpga.pump()
        assert fpga.state == STATE_FREE
        send_to_fpga(fpga, StateSet(STATE_RUNNING, 0, 0))
        assert fpga.state == STATE_RUNNING


# ---------------------------------------------------------------- 重启


def test_simulate_reboot_resets_uptime_and_time_valid():
    with SimFpga(heartbeat_s=10.0) as fpga:
        send_to_fpga(fpga, TimeSync(1, 2, 3, 4))
        send_to_fpga(fpga, StateSet(STATE_RUNNING, 0, 0))
        assert fpga.time_valid is True

        fpga.simulate_reboot()
        assert fpga.uptime_s == 0
        assert fpga.time_valid is False
        assert fpga.state == STATE_FREE

        reports = [f for f in drain_frames(fpga) if f.type_ == 0x83]
        assert reports, "重启后应发一帧 STATUS_RPT"


class KeyEventLike:
    """``isinstance`` 用的鸭子类型占位（避免和 dataclass 名字冲突）。"""

    def __class_getitem__(cls, item):  # pragma: no cover
        return cls


# 让 isinstance(message, KeyEventLike) 真正生效
from ego_bridge.messages import KeyEvent as _KeyEvent  # noqa: E402

KeyEventLike = _KeyEvent  # type: ignore[misc,assignment]
