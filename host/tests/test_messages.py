"""messages.py —— 各消息 dataclass 的 pack/unpack 与边界值（契约 §3）。"""

from __future__ import annotations

import datetime

import pytest

from ego_bridge.messages import (
    Ack,
    Answer,
    KeyEvent,
    Nack,
    QuestionClose,
    QuestionOpen,
    StateSet,
    StatusReport,
    TimeSync,
    UnknownMessage,
    decode_message,
    describe,
    EVENT_LONG,
    EVENT_PRESS,
    FLAG_KEY_DOWN,
    FLAG_TIME_VALID,
    FLAG_UART_ERROR,
    KEY_DOWN,
    KEY_LEFT,
    KEY_MIDDLE,
    KEY_RIGHT,
    KEY_UP,
    MAX_OPTIONS,
    MAX_Q_INDEX,
    STATE_FREE,
    STATE_RUNNING,
    STATE_WAITING,
    TYPE_ACK,
    TYPE_ACK_FPGA,
    TYPE_ANSWER,
    TYPE_KEY_EVT,
    TYPE_NACK,
    TYPE_NACK_FPGA,
    TYPE_QUESTION_CLOSE,
    TYPE_QUESTION_OPEN,
    TYPE_STATE_SET,
    TYPE_STATUS_RPT,
    TYPE_TIME_SYNC,
)
from ego_bridge.protocol import ProtocolError, decode_frame


# ---------------------------------------------------------------- 线上 TYPE 常量


def test_wire_type_constants():
    """契约 §3.1/§3.2：消息号 + 方向位。"""
    assert TYPE_TIME_SYNC == 0x01
    assert TYPE_STATE_SET == 0x02
    assert TYPE_QUESTION_OPEN == 0x03
    assert TYPE_QUESTION_CLOSE == 0x04
    assert TYPE_ACK == 0x00
    assert TYPE_NACK == 0x7F
    assert TYPE_ACK_FPGA == 0x80
    assert TYPE_KEY_EVT == 0x81
    assert TYPE_ANSWER == 0x82
    assert TYPE_STATUS_RPT == 0x83
    assert TYPE_NACK_FPGA == 0xFF


def test_direction_bit_semantics():
    """同一消息号在两侧的线上值差 0x80。"""
    assert KeyEvent(0, 0, 0).type_ == 0x81
    assert TimeSync(0, 0, 0, 1).type_ == 0x01
    assert Ack(0x01, direction=0).type_ == 0x00
    assert Ack(0x01, direction=1).type_ == 0x80
    assert Nack(0x02, direction=0).type_ == 0x7F
    assert Nack(0x02, direction=1).type_ == 0xFF


# ---------------------------------------------------------------- 1. 往返（全部消息类型）


@pytest.mark.parametrize(
    "message",
    [
        TimeSync(12, 34, 56, 5),
        TimeSync(0, 0, 0, 1),
        TimeSync(23, 59, 59, 7),
        StateSet(STATE_FREE, 0, 0),
        StateSet(STATE_RUNNING, 15, 255),
        StateSet(STATE_WAITING, 3, 7),
        QuestionOpen(5, 3, 1),
        QuestionOpen(255, 9, 16),
        QuestionClose(5),
        QuestionClose(0),
        KeyEvent(KEY_MIDDLE, EVENT_PRESS, STATE_FREE),
        KeyEvent(KEY_UP, EVENT_LONG, STATE_WAITING),
        Answer(5, 1, 2, 1234),
        Answer(255, 16, 8, 65535),
        StatusReport(0, 0, 0, 0, 0),
        StatusReport(2, 15, 255, 0xFF, 65535),
        Ack(0x01, direction=0),
        Ack(0x83, direction=1),
        Nack(0x02, direction=1),
        Nack(0x7F, direction=0),
    ],
)
def test_message_roundtrip(message):
    """``to_bytes()`` → ``decode_frame`` → ``decode_message`` 必须还原等值对象。"""
    raw = message.to_bytes()
    assert raw[:2] == b"\xAA\x55"
    frame = decode_frame(raw)
    decoded = decode_message(frame)
    assert decoded == message, f"{message!r} 往返后变成 {decoded!r}"
    assert decoded.type_ == message.type_


def test_all_payload_lengths_match_contract():
    """各消息 LEN 与契约表格一致。"""
    assert len(TimeSync(0, 0, 0, 1).pack()) == 4
    assert len(StateSet(0, 0, 0).pack()) == 3
    assert len(QuestionOpen(0, 1, 1).pack()) == 3
    assert len(QuestionClose(0).pack()) == 1
    assert len(KeyEvent(0, 0, 0).pack()) == 3
    assert len(Answer(0, 1, 0, 0).pack()) == 5  # v2：LEN=5（v1 是 4）
    assert len(StatusReport(0, 0, 0, 0, 0).pack()) == 6
    assert len(Ack(0).pack()) == 1
    assert len(Nack(0).pack()) == 1


# ---------------------------------------------------------------- 2. 边界值（题目要求）


def test_time_sync_boundary_max():
    """hour=23, min=59, sec=59, day=7（周日）。"""
    message = TimeSync(23, 59, 59, 7)
    assert message.pack() == bytes([23, 59, 59, 7])
    assert TimeSync.unpack(message.pack()) == message


def test_time_sync_day_iso_weekday():
    """day 用 ``isoweekday()``：周一=1 … 周日=7（契约 §0 改动 4）。"""
    monday = datetime.datetime(2026, 11, 2, 8, 30, 15)  # 2026-11-02 是周一
    sunday = datetime.datetime(2026, 11, 8, 23, 59, 59)  # 2026-11-08 是周日
    assert monday.isoweekday() == 1
    assert sunday.isoweekday() == 7
    assert TimeSync.from_datetime(monday) == TimeSync(8, 30, 15, 1)
    assert TimeSync.from_datetime(sunday) == TimeSync(23, 59, 59, 7)


def test_time_sync_friday_example():
    """契约示例：星期五 12:34:56。"""
    friday = datetime.datetime(2026, 11, 6, 12, 34, 56)
    assert friday.isoweekday() == 5
    assert TimeSync.from_datetime(friday).to_bytes() == bytes(
        [0xAA, 0x55, 0x01, 0x04, 0x0C, 0x22, 0x38, 0x05, 0x91]
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"hour": 24},
        {"hour": -1},
        {"minute": 60},
        {"second": 60},
        {"day": 0},
        {"day": 8},
    ],
)
def test_time_sync_rejects_out_of_range(kwargs):
    base = dict(hour=0, minute=0, second=0, day=1)
    base.update(kwargs)
    with pytest.raises(ProtocolError):
        TimeSync(**base)


def test_answer_elapsed_max():
    """elapsed_ms = 65535 必须能装下（饱和上界，不回绕）。"""
    message = Answer(5, 1, 2, 65535)
    payload = message.pack()
    assert payload == bytes([5, 1, 2, 0xFF, 0xFF])
    assert Answer.unpack(payload) == message
    assert Answer.unpack(payload).elapsed_ms == 65535


def test_answer_elapsed_little_endian():
    """elapsed_ms 小端：1234 = 0x04D2 → ``D2 04``。"""
    payload = Answer(5, 1, 2, 1234).pack()
    assert payload[3:5] == bytes([0xD2, 0x04])
    assert Answer.unpack(payload).elapsed_ms == 1234


def test_answer_elapsed_overflow_rejected():
    with pytest.raises(ProtocolError):
        Answer(0, 1, 0, 65536)
    with pytest.raises(ProtocolError):
        Answer(0, 1, 0, -1)


def test_answer_option_bounds():
    """option 0–8（提问页只能显示一个数字）。"""
    assert Answer(0, 1, 0, 0).option == 0
    assert Answer(0, 1, 8, 0).option == 8
    with pytest.raises(ProtocolError):
        Answer(0, 1, 9, 0)


def test_state_boundary_max():
    """state=2（WAITING）是合法上界。"""
    assert StateSet(STATE_WAITING, 15, 255).pack() == bytes([2, 15, 255])
    assert (STATE_FREE, STATE_RUNNING, STATE_WAITING) == (0, 1, 2)
    with pytest.raises(ProtocolError):
        StateSet(3, 0, 0)


def test_state_set_rejects_session_overflow():
    with pytest.raises(ProtocolError):
        StateSet(0, 16, 0)
    with pytest.raises(ProtocolError):
        StateSet(0, 0, 256)


def test_question_open_option_limit():
    """n_options 1–9：超过 9 个上位机直接拒绝下发（契约 §3.1）。"""
    assert MAX_OPTIONS == 9
    assert QuestionOpen(1, 1, 1).n_options == 1
    assert QuestionOpen(1, 9, 1).n_options == 9
    with pytest.raises(ProtocolError):
        QuestionOpen(1, 10, 1)
    with pytest.raises(ProtocolError):
        QuestionOpen(1, 0, 1)


def test_question_open_q_index_limit():
    assert MAX_Q_INDEX == 16
    assert QuestionOpen(1, 3, 16).q_index == 16
    with pytest.raises(ProtocolError):
        QuestionOpen(1, 3, 17)
    with pytest.raises(ProtocolError):
        QuestionOpen(1, 3, 0)


def test_key_event_all_keys():
    """5 个按键 ID 全部可编解码（契约 §3.3）。"""
    for key_id, name in [
        (KEY_MIDDLE, "middle"),
        (KEY_LEFT, "left"),
        (KEY_RIGHT, "right"),
        (KEY_DOWN, "down"),
        (KEY_UP, "up"),
    ]:
        message = KeyEvent(key_id, EVENT_PRESS, STATE_RUNNING)
        decoded = KeyEvent.unpack(message.pack())
        assert decoded == message
        assert decoded.key_name == name

    assert KEY_MIDDLE == 0
    assert KEY_LEFT == 1
    assert KEY_RIGHT == 2
    assert KEY_DOWN == 3
    assert KEY_UP == 4


def test_key_event_rejects_bad_key():
    with pytest.raises(ProtocolError):
        KeyEvent(5, 0, 0)
    with pytest.raises(ProtocolError):
        KeyEvent(0, 2, 0)


def test_status_report_uptime_little_endian():
    message = StatusReport(1, 2, 3, 0x02, 0x1234)
    payload = message.pack()
    assert payload[4:6] == bytes([0x34, 0x12])
    assert StatusReport.unpack(payload).uptime_s == 0x1234


def test_status_report_flags_helpers():
    report = StatusReport(0, 0, 0, FLAG_KEY_DOWN | FLAG_TIME_VALID, 5)
    assert report.key_down is True
    assert report.time_valid is True
    assert report.uart_error is False

    clean = StatusReport(0, 0, 0, 0, 5)
    assert clean.key_down is False
    assert clean.time_valid is False
    assert clean.uart_error is False

    err = StatusReport(0, 0, 0, FLAG_UART_ERROR, 0)
    assert err.uart_error is True


def test_status_report_uptime_boundary():
    assert StatusReport(0, 0, 0, 0, 65535).pack()[4:6] == b"\xFF\xFF"
    with pytest.raises(ProtocolError):
        StatusReport(0, 0, 0, 0, 65536)


# ---------------------------------------------------------------- 3. 解包校验


def test_unpack_rejects_wrong_length():
    with pytest.raises(ProtocolError):
        TimeSync.unpack(bytes(3))
    with pytest.raises(ProtocolError):
        Answer.unpack(bytes(4))  # v1 的 4 字节必须被拒
    with pytest.raises(ProtocolError):
        StatusReport.unpack(bytes(5))
    with pytest.raises(ProtocolError):
        KeyEvent.unpack(bytes(2))


def test_unknown_type_does_not_raise():
    """未知 TYPE 返回 ``UnknownMessage``，旧版桥不会被新消息搞崩。"""
    from ego_bridge.protocol import Frame

    frame = Frame(0x8F, bytes([1, 2, 3]))
    message = decode_message(frame)
    assert isinstance(message, UnknownMessage)
    assert message.type_value == 0x8F
    assert message.payload == bytes([1, 2, 3])


def test_ack_nack_direction_roundtrip():
    """ACK/NACK 两个方向都能解回来，且 direction 正确。"""
    from ego_bridge.protocol import Frame

    inbound_ack = decode_message(Frame(0x80, bytes([0x02])))
    assert isinstance(inbound_ack, Ack)
    assert inbound_ack.ack_type == 0x02
    assert inbound_ack.direction == 1

    outbound_ack = decode_message(Frame(0x00, bytes([0x02])))
    assert outbound_ack.direction == 0

    inbound_nack = decode_message(Frame(0xFF, bytes([0x02])))
    assert isinstance(inbound_nack, Nack)
    assert inbound_nack.direction == 1

    outbound_nack = decode_message(Frame(0x7F, bytes([0x02])))
    assert outbound_nack.direction == 0


def test_message_rejects_non_int_fields():
    with pytest.raises(ProtocolError):
        TimeSync("12", 0, 0, 1)  # type: ignore[arg-type]
    with pytest.raises(ProtocolError):
        StateSet(True, 0, 0)  # bool 不是合法 state


def test_describe_is_human_readable():
    """``describe`` 给 cli/monitor 用，至少得提到关键字段。"""
    assert "12:34:56" in describe(TimeSync(12, 34, 56, 5))
    assert "WAITING" in describe(StateSet(STATE_WAITING, 0, 0))
    assert "req=5" in describe(QuestionOpen(5, 3, 1))
    assert "option=2" in describe(Answer(5, 1, 2, 1234))
    assert "1234" in describe(Answer(5, 1, 2, 1234))
    assert "middle" in describe(KeyEvent(KEY_MIDDLE, EVENT_PRESS, STATE_WAITING))
    assert "uptime_s=999" in describe(StatusReport(1, 2, 3, 0, 999))
