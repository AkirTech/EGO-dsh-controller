"""link.py —— 帧同步状态机（契约 §4.1）。

覆盖：逐字节分片、粘包、``AA AA 55`` 重同步、CRC 错后重新同步、LEN>8 拒绝。
"""

from __future__ import annotations

import pytest

from ego_bridge.link import FrameParser, ParseState
from ego_bridge.messages import Answer, QuestionClose, StateSet, StatusReport, TimeSync
from ego_bridge.protocol import Frame, encode_frame

from conftest import TIME_SYNC_FRIDAY_123456


FRAME_T = TIME_SYNC_FRIDAY_123456
FRAME_STATE = StateSet(1, 0, 0).to_bytes()
FRAME_STATUS = StatusReport(1, 2, 3, 0x02, 0x1234).to_bytes()


# ---------------------------------------------------------------- 基本


def test_initial_state_is_idle():
    parser = FrameParser()
    assert parser.state is ParseState.IDLE
    assert parser.pending_bytes == 0


def test_feed_one_byte_at_a_time():
    """**分片喂入**：一次 1 字节，最后那一字节才产出帧。"""
    parser = FrameParser()
    out = []
    for index, byte in enumerate(FRAME_T):
        frame = parser.feed(byte)
        if index < len(FRAME_T) - 1:
            assert frame is None, f"第 {index} 字节就出帧了，不应该"
        else:
            out.append(frame)
    assert len(out) == 1
    assert out[0] is not None
    assert out[0].type_ == 0x01
    assert out[0].payload == bytes([12, 34, 56, 5])
    assert parser.state is ParseState.IDLE
    assert parser.stats.frames_ok == 1


def test_feed_bytes_chunked_arbitrary_sizes():
    """任意切分点都不能影响结果。"""
    stream = FRAME_T + FRAME_STATE + FRAME_STATUS
    for chunk in (1, 2, 3, 5, 7, 13, len(stream)):
        parser = FrameParser()
        frames = []
        for start in range(0, len(stream), chunk):
            frames.extend(parser.feed_bytes(stream[start : start + chunk]))
        assert len(frames) == 3, f"chunk={chunk} 时只解出 {len(frames)} 帧"
        assert [f.type_ for f in frames] == [0x01, 0x02, 0x83]


def test_two_frames_glued_together():
    """**两帧粘包**：必须解出两帧，不能只解出第一帧。"""
    parser = FrameParser()
    frames = parser.feed_bytes(FRAME_T + FRAME_STATUS)
    assert len(frames) == 2
    assert frames[0].type_ == 0x01
    assert frames[1].type_ == 0x83
    assert frames[1].payload == bytes([1, 2, 3, 0x02, 0x34, 0x12])  # 小端 uptime
    assert parser.stats.frames_ok == 2


def test_many_frames_in_one_chunk():
    parser = FrameParser()
    stream = FRAME_STATUS * 5
    frames = parser.feed_bytes(stream)
    assert len(frames) == 5


# ---------------------------------------------------------------- 重同步


def test_aa_aa_55_resync():
    """``AA AA 55``：停在 WAIT_55 等第二个 0x55，最终仍能解出帧。"""
    parser = FrameParser()
    out = parser.feed_bytes(b"\xAA\xAA" + FRAME_T)

    # 前两个 AA：第一个进 WAIT_55，第二个也留在 WAIT_55
    assert parser.stats.frames_ok == 1
    assert len(out) == 1
    assert out[0].payload == bytes([12, 34, 56, 5])


def test_garbage_aa_aa_55_then_resyncs():
    """噪声里出现完整的 ``AA AA 55`` 时，解析器会把后面两字节当成 TYPE/LEN。

    这是契约 §4.1 状态机的**既定行为**（``AA AA 55`` 本身就是一个合法帧头后接
    TYPE=0xAA、LEN=0x55），所以紧跟其后的那一帧会被当作坏 LEN 吃掉；
    要求是**不崩、且能从再下一帧恢复**。
    """
    parser = FrameParser()
    stream = b"\x00\x11\xAA\xAA\x55" + FRAME_T + FRAME_T
    frames = parser.feed_bytes(stream)
    assert parser.stats.bad_len >= 1
    assert len(frames) == 1
    assert frames[0].type_ == 0x01


def test_stray_bytes_are_counted():
    parser = FrameParser()
    parser.feed_bytes(b"\x00\x01\x02")
    assert parser.stats.stray_bytes == 3
    assert parser.stats.frames_ok == 0


def test_aa_followed_by_wrong_byte_returns_to_idle():
    parser = FrameParser()
    parser.feed(0xAA)
    assert parser.state is ParseState.WAIT_55
    parser.feed(0x00)
    assert parser.state is ParseState.IDLE


# ---------------------------------------------------------------- CRC 错


def test_bad_crc_discards_whole_frame():
    """CRC 错 → 丢弃整帧回 IDLE，**不从载荷中间重扫**（契约 §4.1）。"""
    bad = bytearray(FRAME_T)
    bad[-1] ^= 0xFF
    parser = FrameParser()
    frames = parser.feed_bytes(bytes(bad))
    assert frames == []
    assert parser.state is ParseState.IDLE
    assert parser.stats.bad_crc == 1
    assert parser.stats.frames_ok == 0


def test_resync_after_bad_crc():
    """**CRC 错后能重新同步到下一个好帧**。"""
    bad = bytearray(FRAME_T)
    bad[-1] ^= 0xFF
    parser = FrameParser()
    frames = parser.feed_bytes(bytes(bad) + FRAME_STATE)
    assert len(frames) == 1
    assert frames[0].type_ == 0x02
    assert parser.stats.bad_crc == 1
    assert parser.stats.frames_ok == 1


def test_no_rescan_inside_payload():
    """载荷里含 ``AA 55`` 的坏帧：不能把它当帧头重新同步出来。

    构造：帧载荷里塞 ``AA 55 83 06 ...``。如果实现错误地从载荷中间重扫，
    就会伪造出一个以载荷内容为开头的"帧"。正确实现直接丢弃。
    """
    payload = bytes([0xAA, 0x55, 0x83, 0x06, 0x00, 0x00, 0x00, 0x00])
    good = bytearray(encode_frame(0x83, payload))
    good[-1] ^= 0xFF  # CRC 坏掉
    parser = FrameParser()
    frames = parser.feed_bytes(bytes(good))
    assert frames == [], "CRC 错帧的载荷被误当成新帧了"

    # 紧接着一个真正的好帧，必须能解出来
    frames = parser.feed_bytes(FRAME_STATE)
    assert len(frames) == 1
    assert frames[0].type_ == 0x02


def test_valid_frame_with_aa55_in_payload():
    """载荷里出现 ``AA 55`` 的**合法**帧也必须正常解出（LEN 已知，不会误判）。"""
    payload = bytes([0xAA, 0x55, 0x00, 0x55, 0xAA, 0x00])
    raw = encode_frame(0x83, payload)
    parser = FrameParser()
    frames = parser.feed_bytes(raw)
    assert len(frames) == 1
    assert frames[0].payload == payload


# ---------------------------------------------------------------- LEN 越界


def test_len_over_8_rejected():
    """``LEN > 8`` 必须拒绝并回 IDLE（v2 上限 8）。"""
    parser = FrameParser()
    bad_header = bytes([0xAA, 0x55, 0x83, 0x09])  # LEN=9
    frames = parser.feed_bytes(bad_header + bytes(10))
    assert frames == []
    assert parser.stats.bad_len == 1


@pytest.mark.parametrize("length", [9, 10, 16, 255])
def test_len_above_limit_all_rejected(length: int):
    parser = FrameParser()
    parser.feed_bytes(bytes([0xAA, 0x55, 0x83, length]))
    assert parser.state is ParseState.IDLE
    assert parser.stats.bad_len == 1
    assert parser.stats.frames_ok == 0


def test_len_exactly_8_accepted():
    """LEN = 8 是合法的上界。"""
    payload = bytes(range(8))
    parser = FrameParser()
    frames = parser.feed_bytes(encode_frame(0x83, payload))
    assert len(frames) == 1
    assert frames[0].payload == payload


def test_recover_after_bad_len():
    parser = FrameParser()
    parser.feed_bytes(bytes([0xAA, 0x55, 0x83, 0x09]))
    frames = parser.feed_bytes(FRAME_T)
    assert len(frames) == 1
    assert frames[0].type_ == 0x01


# ---------------------------------------------------------------- 审计回调


def test_error_callback_receives_crc_and_len():
    events = []
    parser = FrameParser(on_error=lambda kind, detail: events.append(kind))

    bad = bytearray(FRAME_T)
    bad[-1] ^= 0xFF
    parser.feed_bytes(bytes(bad))
    parser.feed_bytes(bytes([0xAA, 0x55, 0x83, 0x09]))

    assert events == ["crc", "len"]


def test_error_callback_exception_is_swallowed():
    """诊断回调抛异常不能打断解析。"""

    def boom(kind, detail):
        raise RuntimeError("callback 炸了")

    parser = FrameParser(on_error=boom)
    bad = bytearray(FRAME_T)
    bad[-1] ^= 0xFF
    parser.feed_bytes(bytes(bad))
    # 之后还能正常解帧
    frames = parser.feed_bytes(FRAME_T)
    assert len(frames) == 1


# ---------------------------------------------------------------- 半帧与重置


def test_partial_frame_state_tracking():
    """半帧时 ``pending_bytes`` 应反映已缓存字节数。"""
    parser = FrameParser()
    parser.feed_bytes(FRAME_T[:2])
    assert parser.state is ParseState.READ_HDR
    parser.feed_bytes(FRAME_T[2:4])  # TYPE + LEN
    assert parser.state is ParseState.READ_BODY
    assert parser.pending_bytes == 0  # 载荷还没开始收
    parser.feed_bytes(FRAME_T[4:7])
    assert parser.pending_bytes == 3


def test_reset_discards_partial_frame():
    parser = FrameParser()
    parser.feed_bytes(FRAME_T[:5])
    parser.reset()
    assert parser.state is ParseState.IDLE
    # 半帧已丢，完整的同一帧现在能正常解出
    frames = parser.feed_bytes(FRAME_T)
    assert len(frames) == 1
    assert frames[0].type_ == 0x01


def test_feed_rejects_bad_argument():
    parser = FrameParser()
    with pytest.raises((TypeError, ValueError)):
        parser.feed("0xAA")
    with pytest.raises(ValueError):
        parser.feed(256)


def test_feed_accepts_single_byte_bytes_object():
    """``serial.read(1)`` 返回 ``b'\\xaa'``，feed 应该也吃得下。"""
    parser = FrameParser()
    frames = []
    for index in range(len(FRAME_T)):
        frames.extend(
            [f for f in [parser.feed(FRAME_T[index : index + 1])] if f is not None]
        )
    assert len(frames) == 1


# ---------------------------------------------------------------- 端到端小场景


def test_realistic_mixed_stream():
    """混合流：噪声 + 好帧 + 坏帧 + 粘包好帧，只交出 3 个好帧。"""
    bad = bytearray(FRAME_T)
    bad[5] ^= 0x01
    stream = (
        b"\xFF\x00\xAA"  # 噪声，末尾一个孤立帧头（AA -> WAIT_55）
        + b"\x00"  # 非 0xAA/0x55 -> 回 IDLE
        + FRAME_STATUS
        + bytes(bad)
        + b"\x00"
        + Answer(5, 1, 2, 1234).to_bytes()
        + QuestionClose(5).to_bytes()
    )
    parser = FrameParser()
    frames = parser.feed_bytes(stream)
    types = [f.type_ for f in frames]
    assert types == [0x83, 0x82, 0x04], types
    assert parser.stats.bad_crc == 1
