"""帧编解码 + CRC8（契约 v2 §2）。

线上帧格式::

    +--------+--------+--------+--------+----------+--------+
    | 0xAA   | 0x55   | TYPE   | LEN    | PAYLOAD  | CRC8   |
    +--------+--------+--------+--------+----------+--------+
      帧头1    帧头2    类型     载荷长   LEN 字节   校验
            └────  CRC 覆盖 TYPE, LEN, PAYLOAD  ─────┘

约定（SERIAL-PROTOCOL.md §2）：

* 多字节数值一律**小端**（低位字节在前）。
* ``LEN`` 取值 **0–8**，超过 8 视为非法帧（v2 收紧；已定义消息最长 6 字节）。
* CRC 计算范围从 ``TYPE`` 开始到 ``PAYLOAD`` 结束（不含帧头、不含 CRC 自身）。
* CRC 失败或 LEN 越界 → 静默丢弃，**不回 NACK**（避免错误风暴）。

本模块只负责单帧的编解码，不负责字节流同步；流式同步见 :mod:`ego_bridge.link`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

__all__ = [
    "HEADER_1",
    "HEADER_2",
    "HEADER",
    "MAX_PAYLOAD",
    "FRAME_OVERHEAD",
    "MAX_FRAME_SIZE",
    "CRC8_POLY",
    "CRC8_INIT",
    "CRC8_ANCHOR",
    "ProtocolError",
    "crc8",
    "Frame",
    "encode_frame",
    "decode_frame",
    "format_hex",
]

# ---------------------------------------------------------------- 常量

HEADER_1 = 0xAA
HEADER_2 = 0x55
HEADER = bytes((HEADER_1, HEADER_2))

#: PAYLOAD 长度上限（v2 从 16 收紧到 8）
MAX_PAYLOAD = 8

#: 帧头 2 + TYPE 1 + LEN 1 + CRC 1
FRAME_OVERHEAD = 5

#: 最长帧长度 = 5 + 8 = 13 字节（115200 下约 1.13 ms）
MAX_FRAME_SIZE = FRAME_OVERHEAD + MAX_PAYLOAD

CRC8_POLY = 0x07
CRC8_INIT = 0x00
#: CRC-8/ATM 标准自检锚点：crc8(b"123456789") == 0xF4
CRC8_ANCHOR = 0xF4


class ProtocolError(ValueError):
    """帧格式或字段取值非法。"""


# ---------------------------------------------------------------- CRC8


def crc8(data: Union[bytes, bytearray, memoryview]) -> int:
    """CRC-8/ATM: poly 0x07, init 0x00, no reflection, no final xor.

    标准测试向量：``crc8(b"123456789") == 0xF4``。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"crc8 需要 bytes 类参数，收到 {type(data).__name__}")
    crc = CRC8_INIT
    for byte in bytes(data):
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ CRC8_POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc & 0xFF


# 模块加载即自检：多项式或位序写错时直接炸出来，而不是等上板才怪。
if crc8(b"123456789") != CRC8_ANCHOR:  # pragma: no cover - 只在实现被改坏时触发
    raise RuntimeError(
        "CRC8 自检失败：crc8(b'123456789') = 0x%02X，应为 0x%02X"
        % (crc8(b"123456789"), CRC8_ANCHOR)
    )


# ---------------------------------------------------------------- Frame


@dataclass(frozen=True)
class Frame:
    """一帧已解码的原始帧（TYPE 保留方向位）。

    :param type_: 线上 TYPE 字节（bit7 是方向位，0=PC→FPGA，1=FPGA→PC）
    :param payload: 载荷字节，长度 0–8
    """

    type_: int
    payload: bytes = b""

    def __post_init__(self) -> None:
        type_ = self.type_
        if isinstance(type_, bool) or not isinstance(type_, int):
            raise ProtocolError(f"TYPE 必须是 0..255 的整数，收到 {type_!r}")
        if not 0 <= type_ <= 0xFF:
            raise ProtocolError(f"TYPE 越界（0..255）：0x{type_:X}")
        payload = self.payload
        if isinstance(payload, (bytearray, memoryview)):
            payload = bytes(payload)
        if not isinstance(payload, bytes):
            raise ProtocolError(f"PAYLOAD 必须是 bytes，收到 {type(payload).__name__}")
        if len(payload) > MAX_PAYLOAD:
            raise ProtocolError(
                f"PAYLOAD 长度 {len(payload)} 超过上限 {MAX_PAYLOAD}（v2）"
            )
        object.__setattr__(self, "payload", payload)

    # -- 只读属性 ---------------------------------------------------

    @property
    def type_number(self) -> int:
        """去掉方向位的消息号（bit6..0）。"""
        return self.type_ & 0x7F

    @property
    def direction(self) -> int:
        """0 = PC→FPGA，1 = FPGA→PC。"""
        return (self.type_ >> 7) & 0x01

    @property
    def from_fpga(self) -> bool:
        return bool(self.type_ & 0x80)

    @property
    def length(self) -> int:
        return len(self.payload)

    @property
    def crc(self) -> int:
        """本帧应有的 CRC8。"""
        return crc8(bytes((self.type_, len(self.payload))) + self.payload)

    # -- 编码 -------------------------------------------------------

    def header_bytes(self) -> bytes:
        """帧头 + TYPE + LEN + PAYLOAD（不含 CRC）。"""
        return (
            bytes((HEADER_1, HEADER_2, self.type_, len(self.payload))) + self.payload
        )

    def encode(self) -> bytes:
        """编码成完整帧字节（含帧头与 CRC）。"""
        body = self.header_bytes()
        return body + bytes((crc8(body[2:]),))

    #: ``frame.to_bytes()`` 与 ``frame.encode()`` 等价，方便与 ``int.to_bytes`` 习惯对齐
    def to_bytes(self) -> bytes:  # noqa: D401 - 见上
        return self.encode()

    def __bytes__(self) -> bytes:
        return self.encode()

    def hex(self) -> str:
        return format_hex(self.encode())

    def __str__(self) -> str:
        return f"Frame(type=0x{self.type_:02X}, len={len(self.payload)}, {self.hex()})"


# ---------------------------------------------------------------- 函数式接口


def encode_frame(type_: int, payload: Union[bytes, bytearray, memoryview] = b"") -> bytes:
    """把 ``TYPE + PAYLOAD`` 编码成完整帧字节。

    >>> encode_frame(0x01, bytes((12, 34, 56, 5))).hex().upper()
    'AA5501040C22380591'
    """
    return Frame(type_, payload).encode()


def decode_frame(raw: Union[bytes, bytearray, memoryview]) -> Frame:
    """解码**一个完整帧**（从 0xAA 到 CRC，长度必须恰好等于帧长）。

    字节流同步请用 :class:`ego_bridge.link.FrameParser`。

    :raises ProtocolError: 帧头错、LEN 越界、长度不符或 CRC 校验失败
    """
    data = bytes(raw)
    if len(data) < FRAME_OVERHEAD:
        raise ProtocolError(f"帧太短：{len(data)} 字节，至少 {FRAME_OVERHEAD} 字节")
    if data[0] != HEADER_1 or data[1] != HEADER_2:
        raise ProtocolError(
            "帧头不是 AA 55：%02X %02X" % (data[0], data[1])
        )
    type_ = data[2]
    length = data[3]
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"LEN={length} 超过上限 {MAX_PAYLOAD}，非法帧")
    expected = FRAME_OVERHEAD + length
    if len(data) != expected:
        raise ProtocolError(
            f"帧长不符：LEN={length} 要求 {expected} 字节，实际 {len(data)} 字节"
        )
    payload = data[4 : 4 + length]
    received_crc = data[4 + length]
    expected_crc = crc8(data[2 : 4 + length])
    if received_crc != expected_crc:
        raise ProtocolError(
            "CRC 校验失败：收到 0x%02X，计算 0x%02X" % (received_crc, expected_crc)
        )
    return Frame(type_, payload)


def format_hex(data: Union[bytes, bytearray, memoryview]) -> str:
    """把字节串格式化成 ``AA 55 01 04`` 这样的十六进制串。"""
    return " ".join("%02X" % b for b in bytes(data))
