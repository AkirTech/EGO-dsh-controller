"""字节流帧同步状态机（契约 v2 §4.1）。

串口是**字节流**，必须自己找帧边界::

    状态 IDLE    : 逐字节读，遇到 0xAA → WAIT_55
    状态 WAIT_55 : 读到 0x55 → READ_HDR
                   若又是 0xAA → 停在 WAIT_55（处理 AA AA 55 这种重同步）
                   否则 → 回 IDLE
    状态 READ_HDR: 收 TYPE + LEN（LEN > 8 视为非法，回 IDLE）
    状态 READ_BODY: 收 LEN 字节载荷 + 1 字节 CRC
                   CRC 通过 → 交付消息，回 IDLE
                   CRC 失败 → 丢弃，回 IDLE

.. important::
   **CRC 失败后不要从载荷中间重新找帧头。** 那样会在错误帧里反复误同步。
   直接丢弃整帧、等下一个真实帧头，更稳。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterator, List, Optional, Union

from .protocol import (
    FRAME_OVERHEAD,
    HEADER_1,
    HEADER_2,
    MAX_FRAME_SIZE,
    MAX_PAYLOAD,
    Frame,
    crc8,
)

__all__ = [
    "ParseState",
    "FrameParser",
    "parse_frames",
]


class ParseState(IntEnum):
    """帧同步状态机的四个状态。"""

    IDLE = 0
    WAIT_55 = 1
    READ_HDR = 2
    READ_BODY = 3


@dataclass
class ParserStats:
    """解析统计，便于诊断链路质量（不参与协议）。"""

    bytes_in: int = 0
    frames_ok: int = 0
    bad_crc: int = 0
    bad_len: int = 0
    stray_bytes: int = 0

    @property
    def frames_bad(self) -> int:
        return self.bad_crc + self.bad_len

    def as_dict(self) -> dict:
        return {
            "bytes_in": self.bytes_in,
            "frames_ok": self.frames_ok,
            "bad_crc": self.bad_crc,
            "bad_len": self.bad_len,
            "stray_bytes": self.stray_bytes,
        }


class FrameParser:
    """增量式帧同步状态机。

    用法::

        parser = FrameParser()
        for byte in stream:
            frame = parser.feed(byte)
            if frame is not None:
                handle(frame)

    也可以一次性喂一段：``for frame in parser.feed_bytes(chunk): ...``。

    :param on_error: 可选回调，收到 ``(kind, detail)``。``kind`` 是
        ``"crc"`` / ``"len"``。**回调异常会被吞掉**，绝不能让它打断解析。
    """

    def __init__(self, on_error=None) -> None:
        self._on_error = on_error
        self.stats = ParserStats()
        self.reset()

    # -- 生命周期 ---------------------------------------------------

    def reset(self) -> None:
        """回到 IDLE，丢弃半帧。"""
        self._state: ParseState = ParseState.IDLE
        self._type: int = 0
        self._len: int = 0
        self._hdr: List[int] = []  # READ_HDR 阶段已收的字节（0 或 1 个）
        self._body: List[int] = []  # READ_BODY 阶段已收的载荷 + CRC

    # -- 只读属性 ---------------------------------------------------

    @property
    def state(self) -> ParseState:
        return self._state

    @property
    def state_name(self) -> str:
        return self._state.name

    @property
    def pending_bytes(self) -> int:
        """当前半帧已缓存的字节数（用于调试与超时判断）。"""
        return len(self._hdr) + len(self._body)

    # -- 核心 -------------------------------------------------------

    def feed(self, byte: int) -> Optional[Frame]:
        """喂入一个字节。

        :returns: 若这一字节刚好补完一帧且 CRC 正确，返回 :class:`Frame`；
            否则返回 ``None``。
        """
        if isinstance(byte, (bytes, bytearray)) and len(byte) == 1:
            byte = byte[0]
        elif not isinstance(byte, int) or isinstance(byte, bool):
            raise TypeError(f"feed() 需要 0..255 的整数，收到 {byte!r}")
        if not 0 <= byte <= 0xFF:
            raise ValueError(f"字节越界：{byte}")

        self.stats.bytes_in += 1

        if self._state is ParseState.IDLE:
            if byte == HEADER_1:
                self._state = ParseState.WAIT_55
            else:
                self.stats.stray_bytes += 1
            return None

        if self._state is ParseState.WAIT_55:
            if byte == HEADER_2:
                self._state = ParseState.READ_HDR
                self._hdr = []
                self._body = []
            elif byte == HEADER_1:
                # AA AA 55：停在 WAIT_55 等第二个帧头字节
                self.stats.stray_bytes += 1
            else:
                self.stats.stray_bytes += 1
                self._state = ParseState.IDLE
            return None

        if self._state is ParseState.READ_HDR:
            self._hdr.append(byte)
            if len(self._hdr) == 1:
                self._type = byte
                return None
            # 第二个字节 = LEN
            length = byte
            if length > MAX_PAYLOAD:
                # LEN 越界：整帧丢弃，回 IDLE（契约 §2.1）
                self.stats.bad_len += 1
                self._fail("len", f"LEN={length} 超过上限 {MAX_PAYLOAD}")
                self._state = ParseState.IDLE
                self._hdr = []
                return None
            self._len = length
            self._body = []
            self._hdr = []  # 头部已用掉，别让它算进 pending_bytes
            self._state = ParseState.READ_BODY
            return None

        # READ_BODY：收 LEN 字节载荷 + 1 字节 CRC
        self._body.append(byte)
        if len(self._body) < self._len + 1:
            return None

        payload = bytes(self._body[: self._len])
        received_crc = self._body[self._len]
        expected_crc = crc8(bytes((self._type, self._len)) + payload)

        self._state = ParseState.IDLE
        self._hdr = []
        self._body = []

        if received_crc != expected_crc:
            # 丢弃整帧，不回退重扫载荷（契约 §4.1）
            self.stats.bad_crc += 1
            self._fail(
                "crc",
                "CRC 不符：收到 0x%02X，计算 0x%02X"
                % (received_crc, expected_crc),
            )
            return None

        self.stats.frames_ok += 1
        return Frame(self._type, payload)

    def feed_bytes(self, data: Union[bytes, bytearray, memoryview]) -> List[Frame]:
        """喂入一段字节，返回期间完成的全部帧（可能 0 个或多个）。"""
        frames: List[Frame] = []
        for byte in bytes(data):
            frame = self.feed(byte)
            if frame is not None:
                frames.append(frame)
        return frames

    def __iter__(self) -> Iterator[Frame]:  # pragma: no cover - 便捷语法糖
        return iter(())

    # -- 内部 -------------------------------------------------------

    def _fail(self, kind: str, detail: str) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(kind, detail)
        except Exception:  # noqa: BLE001 - 诊断回调绝不能影响解析
            pass


def parse_frames(data: Union[bytes, bytearray, memoryview]) -> List[Frame]:
    """一次性解析一段完好的字节流，返回所有有效帧（跳过坏帧）。"""
    parser = FrameParser()
    return parser.feed_bytes(data)


def make_frame_sink(handler) -> FrameParser:  # pragma: no cover - 便捷工具
    """构造一个把每帧送给 ``handler`` 的解析器。"""

    class _Sink(FrameParser):
        def feed(self, byte):  # type: ignore[override]
            frame = super().feed(byte)
            if frame is not None:
                handler(frame)
            return frame

    return _Sink()
