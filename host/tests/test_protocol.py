"""protocol.py —— CRC8 锚点、帧编解码、真实帧常量（契约 §2 / §5）。"""

from __future__ import annotations

import pytest

from ego_bridge.protocol import (
    FRAME_OVERHEAD,
    MAX_FRAME_SIZE,
    MAX_PAYLOAD,
    Frame,
    ProtocolError,
    crc8,
    decode_frame,
    encode_frame,
    format_hex,
)

from conftest import TIME_SYNC_FRIDAY_123456


# ---------------------------------------------------------------- 1. CRC 锚点


def test_crc8_standard_anchor():
    """契约 §2.2：crc8(b"123456789") == 0xF4。"""
    assert crc8(b"123456789") == 0xF4


def test_crc8_empty_input():
    """空数据的 CRC 就是初值 0x00。"""
    assert crc8(b"") == 0x00


def test_crc8_single_byte():
    """单字节 0x00 -> 0x00；0x01 走完 8 次移位。"""
    assert crc8(b"\x00") == 0x00
    assert crc8(b"\x01") == 0x07  # 移位 8 次后等价于 poly
    assert crc8(bytes([0xAA])) == crc8(bytes([0xAA]))


@pytest.mark.parametrize(
    "data, expected",
    [
        (b"123456789", 0xF4),
        (b"", 0x00),
        (bytes([0x01, 0x04, 0x0C, 0x22, 0x38, 0x05]), 0x91),  # TIME_SYNC 头部
        (bytes([0x82, 0x05, 0x05, 0x01, 0x02, 0xD2, 0x04]), 0x1F),  # ANSWER 头部
    ],
)
def test_crc8_known_vectors(data: bytes, expected: int):
    """这几条向量都能从契约 §3 的示例帧反推出来。"""
    assert crc8(data) == expected


# ---------------------------------------------------------------- 2. 真实帧常量


def test_time_sync_frame_byte_exact():
    """契约 §5 第 1 条：TIME_SYNC 12:34:56 星期五 必须逐字节相等。"""
    from ego_bridge.messages import TimeSync

    encoded = TimeSync(12, 34, 56, 5).to_bytes()
    assert encoded == TIME_SYNC_FRIDAY_123456
    assert encoded.hex().upper() == "AA5501040C22380591"
    assert format_hex(encoded) == "AA 55 01 04 0C 22 38 05 91"


def test_time_sync_frame_via_encode_frame():
    """手工组帧与消息对象组帧必须一致。"""
    expected = TIME_SYNC_FRIDAY_123456
    assert encode_frame(0x01, bytes([12, 34, 56, 5])) == expected


def test_state_set_frames_from_checklist():
    """契约 §5 第 2/3/4 条给出的 STATE_SET 帧常量。"""
    from ego_bridge.messages import StateSet

    assert StateSet(1, 0, 0).to_bytes() == bytes(
        [0xAA, 0x55, 0x02, 0x03, 0x01, 0x00, 0x00, 0x95]
    )
    assert StateSet(2, 0, 0).to_bytes() == bytes(
        [0xAA, 0x55, 0x02, 0x03, 0x02, 0x00, 0x00, 0x28]
    )


def test_answer_frame_from_timing_diagram():
    """契约 §3.5 时序图里的 ANSWER 帧：req=5, idx=1, option=2, 1234 ms。"""
    from ego_bridge.messages import Answer

    assert Answer(5, 1, 2, 1234).to_bytes() == bytes(
        [0xAA, 0x55, 0x82, 0x05, 0x05, 0x01, 0x02, 0xD2, 0x04, 0x1F]
    )


def test_question_frames_from_timing_diagram():
    """契约 §3.5 时序图里的 QUESTION_OPEN / QUESTION_CLOSE 帧。"""
    from ego_bridge.messages import QuestionClose, QuestionOpen

    assert QuestionOpen(5, 3, 1).to_bytes() == bytes(
        [0xAA, 0x55, 0x03, 0x03, 0x05, 0x03, 0x01, 0x64]
    )
    assert QuestionClose(5).to_bytes() == bytes([0xAA, 0x55, 0x04, 0x01, 0x05, 0xA5])


# ---------------------------------------------------------------- 3. 帧编解码往返


@pytest.mark.parametrize(
    "type_, payload",
    [
        (0x01, bytes([12, 34, 56, 5])),  # TIME_SYNC
        (0x02, bytes([1, 0, 0])),  # STATE_SET
        (0x03, bytes([5, 3, 1])),  # QUESTION_OPEN
        (0x04, bytes([5])),  # QUESTION_CLOSE
        (0x00, bytes([0x01])),  # ACK
        (0x7F, bytes([0x02])),  # NACK
        (0x80, bytes([0x01])),  # ACK (FPGA→PC)
        (0x81, bytes([0, 0, 1])),  # KEY_EVT
        (0x82, bytes([5, 1, 2, 0xD2, 0x04])),  # ANSWER
        (0x83, bytes([1, 2, 3, 0x02, 0xE7, 0x03])),  # STATUS_RPT
        (0x83, b""),  # 空载荷也算合法帧
        (0x42, bytes(range(8))),  # 最长载荷（8 字节）
    ],
)
def test_frame_roundtrip(type_: int, payload: bytes):
    """编码 → 解码必须还原出完全相同的 TYPE 与 PAYLOAD。"""
    raw = encode_frame(type_, payload)
    assert raw[:2] == bytes([0xAA, 0x55])
    assert raw[2] == type_
    assert raw[3] == len(payload)
    assert raw[4:-1] == payload

    frame = decode_frame(raw)
    assert frame.type_ == type_
    assert frame.payload == payload
    assert frame.encode() == raw


def test_frame_dataclass_roundtrip():
    frame = Frame(0x83, bytes([0, 1, 2, 3, 0x10, 0x27]))
    assert decode_frame(frame.encode()) == frame
    assert bytes(frame) == frame.encode()


def test_frame_direction_and_type_number():
    """bit7 是方向位：0x80 方向=1，消息号 0x00。"""
    inbound = Frame(0x83, b"\x00" * 6)
    assert inbound.direction == 1
    assert inbound.from_fpga is True
    assert inbound.type_number == 0x03

    outbound = Frame(0x03, b"\x00" * 3)
    assert outbound.direction == 0
    assert outbound.from_fpga is False
    assert outbound.type_number == 0x03


def test_frame_rejects_oversize_payload():
    """LEN 上限 8（v2 收紧），9 字节必须被拒。"""
    assert MAX_PAYLOAD == 8
    with pytest.raises(ProtocolError):
        Frame(0x83, bytes(MAX_PAYLOAD + 1))
    with pytest.raises(ProtocolError):
        encode_frame(0x83, bytes(16))


def test_frame_rejects_bad_type():
    with pytest.raises(ProtocolError):
        Frame(0x100, b"")
    with pytest.raises(ProtocolError):
        Frame(-1, b"")


def test_decode_frame_rejects_bad_crc():
    raw = bytearray(TIME_SYNC_FRIDAY_123456)
    raw[-1] ^= 0xFF
    with pytest.raises(ProtocolError, match="CRC"):
        decode_frame(bytes(raw))


def test_decode_frame_rejects_bad_header():
    raw = bytearray(TIME_SYNC_FRIDAY_123456)
    raw[0] = 0x55
    with pytest.raises(ProtocolError, match="帧头"):
        decode_frame(bytes(raw))


def test_decode_frame_rejects_truncated():
    with pytest.raises(ProtocolError):
        decode_frame(TIME_SYNC_FRIDAY_123456[:-1])
    with pytest.raises(ProtocolError):
        decode_frame(b"\xAA\x55\x01")


def test_decode_frame_rejects_oversize_len():
    """LEN 声明 9 > 上限 8，即使字节够也必须拒绝。"""
    with pytest.raises(ProtocolError, match="LEN"):
        decode_frame(bytes([0xAA, 0x55, 0x83, 0x09]) + bytes(10))


def test_max_frame_size_constant():
    """最长帧 = 4 + 8 + 1 = 13 字节（契约 §2.1）。"""
    assert MAX_FRAME_SIZE == 13
    assert len(encode_frame(0x83, bytes(8))) == 13
    assert FRAME_OVERHEAD == 5


def test_format_hex():
    assert format_hex(bytes([0xAA, 0x55, 0x01])) == "AA 55 01"
    assert format_hex(b"") == ""
