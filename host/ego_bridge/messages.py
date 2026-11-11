"""各消息的 dataclass 与 pack/unpack（契约 v2 §3）。

TYPE 的 **bit7 是方向位**：

* ``0`` = PC→FPGA
* ``1`` = FPGA→PC

因此同一个消息号在两侧的线上值不同。本模块用 **消息号（bit6..0）** 定义类型，
上线时再补方向位：

======  ======  ==============  ====  ==========================================
消息号  名称    方向            LEN   载荷
======  ======  ==============  ====  ==========================================
0x00    ACK     双向            1     被确认的 TYPE
0x01    TIME_SYNC      PC→FPGA  4     hour, min, sec, day
0x02    STATE_SET      PC→FPGA  3     state, session, pending
0x03    QUESTION_OPEN  PC→FPGA  3     req_id, n_options, q_index
0x04    QUESTION_CLOSE PC→FPGA  1     req_id
0x7F    NACK    双向            1     被拒绝的 TYPE
0x01    KEY_EVT        FPGA→PC  3     key_id, event, state
0x02    ANSWER         FPGA→PC  5     req_id, q_index, option, elapsed_ms(u16 LE)
0x03    STATUS_RPT     FPGA→PC  6     state, session, pending, flags, uptime_s(u16 LE)
======  ======  ==============  ====  ==========================================

.. note::
   ACK / NACK 两个方向都存在。本模块用 ``direction`` 字段区分，
   线上 TYPE 分别是 ``0x00``/``0x7F``（PC→FPGA）与 ``0x80``/``0xFF``（FPGA→PC）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Dict, Optional, Type, Union

from .protocol import Frame, ProtocolError

__all__ = [
    # 消息号
    "MSG_ACK",
    "MSG_TIME_SYNC",
    "MSG_STATE_SET",
    "MSG_QUESTION_OPEN",
    "MSG_QUESTION_CLOSE",
    "MSG_NACK",
    "MSG_KEY_EVT",
    "MSG_ANSWER",
    "MSG_STATUS_RPT",
    # 线上 TYPE
    "TYPE_ACK",
    "TYPE_TIME_SYNC",
    "TYPE_STATE_SET",
    "TYPE_QUESTION_OPEN",
    "TYPE_QUESTION_CLOSE",
    "TYPE_NACK",
    "TYPE_ACK_FPGA",
    "TYPE_KEY_EVT",
    "TYPE_ANSWER",
    "TYPE_STATUS_RPT",
    "TYPE_NACK_FPGA",
    # 语义常量
    "State",
    "STATE_FREE",
    "STATE_RUNNING",
    "STATE_WAITING",
    "STATE_NAMES",
    "KEY_MIDDLE",
    "KEY_LEFT",
    "KEY_RIGHT",
    "KEY_DOWN",
    "KEY_UP",
    "KEY_NAMES",
    "EVENT_PRESS",
    "EVENT_LONG",
    "FLAG_KEY_DOWN",
    "FLAG_TIME_VALID",
    "FLAG_UART_ERROR",
    "MAX_OPTIONS",
    "MAX_Q_INDEX",
    # 消息类
    "TimeSync",
    "StateSet",
    "QuestionOpen",
    "QuestionClose",
    "KeyEvent",
    "Answer",
    "StatusReport",
    "Ack",
    "Nack",
    "UnknownMessage",
    "MESSAGE_TYPES",
    "decode_message",
    "pack_message",
]

# ---------------------------------------------------------------- 消息号

MSG_ACK = 0x00
MSG_NACK = 0x7F

MSG_TIME_SYNC = 0x01
MSG_STATE_SET = 0x02
MSG_QUESTION_OPEN = 0x03
MSG_QUESTION_CLOSE = 0x04

MSG_KEY_EVT = 0x01
MSG_ANSWER = 0x02
MSG_STATUS_RPT = 0x03

# ---------------------------------------------------------------- 线上 TYPE

TYPE_ACK = 0x00  # PC→FPGA
TYPE_TIME_SYNC = 0x01
TYPE_STATE_SET = 0x02
TYPE_QUESTION_OPEN = 0x03
TYPE_QUESTION_CLOSE = 0x04
TYPE_NACK = 0x7F

TYPE_ACK_FPGA = 0x80  # FPGA→PC
TYPE_KEY_EVT = 0x81
TYPE_ANSWER = 0x82
TYPE_STATUS_RPT = 0x83
TYPE_NACK_FPGA = 0xFF

# ---------------------------------------------------------------- 语义常量


class State:
    """状态机取值（契约 §3.1 STATE_SET）。"""

    FREE = 0
    RUNNING = 1
    WAITING = 2


STATE_FREE = State.FREE
STATE_RUNNING = State.RUNNING
STATE_WAITING = State.WAITING

STATE_NAMES: Dict[int, str] = {
    STATE_FREE: "FREE",
    STATE_RUNNING: "RUNNING",
    STATE_WAITING: "WAITING",
}

#: 按键 ID，与 RTL 的 ``key[4:0]`` 一致（契约 §3.3）
KEY_MIDDLE = 0  # R15
KEY_LEFT = 1  # V1
KEY_RIGHT = 2  # R11
KEY_DOWN = 3  # R17
KEY_UP = 4  # U4

KEY_NAMES: Dict[int, str] = {
    KEY_MIDDLE: "middle",
    KEY_LEFT: "left",
    KEY_RIGHT: "right",
    KEY_DOWN: "down",
    KEY_UP: "up",
}

EVENT_PRESS = 0  # 单击
EVENT_LONG = 1  # 长按（>800 ms）

FLAG_KEY_DOWN = 0x01  # bit0 按键中
FLAG_TIME_VALID = 0x02  # bit1 时间有效
FLAG_UART_ERROR = 0x04  # bit2 串口错

#: 提问选项上限（索引 0–8）：提问页第 6 位只能显示一个数字
MAX_OPTIONS = 9
#: 问题序号上限（1–16）
MAX_Q_INDEX = 16


# ---------------------------------------------------------------- 校验工具


def _check_int(name: str, value: object, low: int, high: int) -> int:
    """校验 ``value`` 是 ``[low, high]`` 内的整数，返回 int。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{name} 必须是整数，收到 {value!r}")
    if not low <= value <= high:
        raise ProtocolError(f"{name} 越界：{value} 不在 {low}..{high}")
    return value


def _check_len(payload: bytes, expected: int, name: str) -> None:
    if len(payload) != expected:
        raise ProtocolError(
            f"{name} 载荷长度应为 {expected}，实际 {len(payload)}（{payload.hex(' ')}）"
        )


def _u16le(name: str, value: object) -> bytes:
    return _check_int(name, value, 0, 0xFFFF).to_bytes(2, "little")


def _read_u16le(name: str, payload: bytes, offset: int) -> int:
    return payload[offset] | (payload[offset + 1] << 8)


# ---------------------------------------------------------------- 基类


class _MessageBase:
    """消息类共有的打包/解包便捷方法。"""

    MESSAGE: ClassVar[int] = -1
    LEN: ClassVar[int] = -1

    @property
    def type_(self) -> int:  # pragma: no cover - 由子类覆盖
        raise NotImplementedError

    def pack(self) -> bytes:  # pragma: no cover - 由子类覆盖
        raise NotImplementedError

    def to_frame(self) -> Frame:
        """打包成 :class:`~ego_bridge.protocol.Frame`。"""
        return Frame(self.type_, self.pack())

    def to_bytes(self) -> bytes:
        """打包成完整帧字节。"""
        return self.to_frame().encode()


# ---------------------------------------------------------------- PC → FPGA


@dataclass(frozen=True)
class TimeSync(_MessageBase):
    """``0x01`` TIME_SYNC —— 校时（LEN=4）。

    :param hour: 0–23，24 小时制
    :param minute: 0–59
    :param second: 0–59
    :param day: **1–7，周一=1 … 周日=7**
    """

    hour: int = 0
    minute: int = 0
    second: int = 0
    day: int = 1

    MESSAGE: ClassVar[int] = MSG_TIME_SYNC
    LEN: ClassVar[int] = 4
    TYPE: ClassVar[int] = TYPE_TIME_SYNC

    def __post_init__(self) -> None:
        _check_int("hour", self.hour, 0, 23)
        _check_int("minute", self.minute, 0, 59)
        _check_int("second", self.second, 0, 59)
        _check_int("day", self.day, 1, 7)

    @property
    def type_(self) -> int:
        return TYPE_TIME_SYNC

    def pack(self) -> bytes:
        return bytes((self.hour, self.minute, self.second, self.day))

    @classmethod
    def unpack(cls, payload: bytes) -> "TimeSync":
        _check_len(payload, cls.LEN, "TIME_SYNC")
        return cls(payload[0], payload[1], payload[2], payload[3])

    #: 便于把 ``datetime`` 转成本消息
    @classmethod
    def from_datetime(cls, moment) -> "TimeSync":
        """用 ``datetime``（本地时间）构造；星期用 ``isoweekday()``（周一=1）。"""
        return cls(moment.hour, moment.minute, moment.second, moment.isoweekday())


@dataclass(frozen=True)
class StateSet(_MessageBase):
    """``0x02`` STATE_SET —— 状态置位（LEN=3）。

    :param state: 0=FREE, 1=RUNNING, 2=WAITING（>2 时 FPGA 忽略，不发 NACK）
    :param session: 0–15 会话号
    :param pending: 0–255 待处理提问数
    """

    state: int = STATE_FREE
    session: int = 0
    pending: int = 0

    MESSAGE: ClassVar[int] = MSG_STATE_SET
    LEN: ClassVar[int] = 3
    TYPE: ClassVar[int] = TYPE_STATE_SET

    def __post_init__(self) -> None:
        _check_int("state", self.state, 0, 2)
        _check_int("session", self.session, 0, 15)
        _check_int("pending", self.pending, 0, 255)

    @property
    def type_(self) -> int:
        return TYPE_STATE_SET

    def pack(self) -> bytes:
        return bytes((self.state, self.session, self.pending))

    @classmethod
    def unpack(cls, payload: bytes) -> "StateSet":
        _check_len(payload, cls.LEN, "STATE_SET")
        return cls(payload[0], payload[1], payload[2])


@dataclass(frozen=True)
class QuestionOpen(_MessageBase):
    """``0x03`` QUESTION_OPEN —— 提问开始（LEN=3）。

    :param req_id: 0–255 请求编号，回答时带回
    :param n_options: **1–9**；超过 9 个时上位机直接拒绝下发（契约 §3.1）
    :param q_index: 1–16，当前显示第几个问题
    """

    req_id: int = 0
    n_options: int = 1
    q_index: int = 1

    MESSAGE: ClassVar[int] = MSG_QUESTION_OPEN
    LEN: ClassVar[int] = 3
    TYPE: ClassVar[int] = TYPE_QUESTION_OPEN

    def __post_init__(self) -> None:
        _check_int("req_id", self.req_id, 0, 255)
        _check_int("n_options", self.n_options, 1, MAX_OPTIONS)
        _check_int("q_index", self.q_index, 1, MAX_Q_INDEX)

    @property
    def type_(self) -> int:
        return TYPE_QUESTION_OPEN

    def pack(self) -> bytes:
        return bytes((self.req_id, self.n_options, self.q_index))

    @classmethod
    def unpack(cls, payload: bytes) -> "QuestionOpen":
        _check_len(payload, cls.LEN, "QUESTION_OPEN")
        return cls(payload[0], payload[1], payload[2])


@dataclass(frozen=True)
class QuestionClose(_MessageBase):
    """``0x04`` QUESTION_CLOSE —— 提问结束（LEN=1）。"""

    req_id: int = 0

    MESSAGE: ClassVar[int] = MSG_QUESTION_CLOSE
    LEN: ClassVar[int] = 1
    TYPE: ClassVar[int] = TYPE_QUESTION_CLOSE

    def __post_init__(self) -> None:
        _check_int("req_id", self.req_id, 0, 255)

    @property
    def type_(self) -> int:
        return TYPE_QUESTION_CLOSE

    def pack(self) -> bytes:
        return bytes((self.req_id,))

    @classmethod
    def unpack(cls, payload: bytes) -> "QuestionClose":
        _check_len(payload, cls.LEN, "QUESTION_CLOSE")
        return cls(payload[0])


# ---------------------------------------------------------------- FPGA → PC


@dataclass(frozen=True)
class KeyEvent(_MessageBase):
    """``0x81`` KEY_EVT —— 按键事件（LEN=3）。

    :param key_id: 0=中, 1=左, 2=右, 3=下, 4=上
    :param event: 0=单击, 1=长按
    :param state: 按下瞬间的状态机快照（0/1/2）
    """

    key_id: int = KEY_MIDDLE
    event: int = EVENT_PRESS
    state: int = STATE_FREE

    MESSAGE: ClassVar[int] = MSG_KEY_EVT
    LEN: ClassVar[int] = 3
    TYPE: ClassVar[int] = TYPE_KEY_EVT

    def __post_init__(self) -> None:
        _check_int("key_id", self.key_id, 0, 4)
        _check_int("event", self.event, 0, 1)
        _check_int("state", self.state, 0, 2)

    @property
    def type_(self) -> int:
        return TYPE_KEY_EVT

    def pack(self) -> bytes:
        return bytes((self.key_id, self.event, self.state))

    @classmethod
    def unpack(cls, payload: bytes) -> "KeyEvent":
        _check_len(payload, cls.LEN, "KEY_EVT")
        return cls(payload[0], payload[1], payload[2])

    @property
    def key_name(self) -> str:
        return KEY_NAMES.get(self.key_id, f"key{self.key_id}")


@dataclass(frozen=True)
class Answer(_MessageBase):
    """``0x82`` ANSWER —— 回答结果（LEN=**5**）。

    :param req_id: 0–255
    :param q_index: 1–16
    :param option: 0–8（与提问页显示的数字一致）
    :param elapsed_ms: 0–65535，小端；FPGA 在 65.535 s 处**饱和**不回绕
    """

    req_id: int = 0
    q_index: int = 1
    option: int = 0
    elapsed_ms: int = 0

    MESSAGE: ClassVar[int] = MSG_ANSWER
    LEN: ClassVar[int] = 5
    TYPE: ClassVar[int] = TYPE_ANSWER

    def __post_init__(self) -> None:
        _check_int("req_id", self.req_id, 0, 255)
        _check_int("q_index", self.q_index, 1, MAX_Q_INDEX)
        _check_int("option", self.option, 0, 8)
        _check_int("elapsed_ms", self.elapsed_ms, 0, 0xFFFF)

    @property
    def type_(self) -> int:
        return TYPE_ANSWER

    def pack(self) -> bytes:
        return bytes((self.req_id, self.q_index, self.option)) + _u16le(
            "elapsed_ms", self.elapsed_ms
        )

    @classmethod
    def unpack(cls, payload: bytes) -> "Answer":
        _check_len(payload, cls.LEN, "ANSWER")
        return cls(
            payload[0],
            payload[1],
            payload[2],
            _read_u16le("elapsed_ms", payload, 3),
        )


@dataclass(frozen=True)
class StatusReport(_MessageBase):
    """``0x83`` STATUS_RPT —— 心跳/状态上报（LEN=6）。

    :param state: 0/1/2
    :param session: 0–15
    :param pending: 0–255
    :param flags: bit0=按键中, bit1=时间有效, bit2=串口错
    :param uptime_s: 0–65535，上电秒计数（用于识别重启）
    """

    state: int = STATE_FREE
    session: int = 0
    pending: int = 0
    flags: int = 0
    uptime_s: int = 0

    MESSAGE: ClassVar[int] = MSG_STATUS_RPT
    LEN: ClassVar[int] = 6
    TYPE: ClassVar[int] = TYPE_STATUS_RPT

    def __post_init__(self) -> None:
        _check_int("state", self.state, 0, 2)
        _check_int("session", self.session, 0, 15)
        _check_int("pending", self.pending, 0, 255)
        _check_int("flags", self.flags, 0, 0xFF)
        _check_int("uptime_s", self.uptime_s, 0, 0xFFFF)

    @property
    def type_(self) -> int:
        return TYPE_STATUS_RPT

    def pack(self) -> bytes:
        return bytes((self.state, self.session, self.pending, self.flags)) + _u16le(
            "uptime_s", self.uptime_s
        )

    @classmethod
    def unpack(cls, payload: bytes) -> "StatusReport":
        _check_len(payload, cls.LEN, "STATUS_RPT")
        return cls(
            payload[0],
            payload[1],
            payload[2],
            payload[3],
            _read_u16le("uptime_s", payload, 4),
        )

    @property
    def key_down(self) -> bool:
        return bool(self.flags & FLAG_KEY_DOWN)

    @property
    def time_valid(self) -> bool:
        return bool(self.flags & FLAG_TIME_VALID)

    @property
    def uart_error(self) -> bool:
        return bool(self.flags & FLAG_UART_ERROR)

    @property
    def state_name(self) -> str:
        return STATE_NAMES.get(self.state, f"state{self.state}")


# ---------------------------------------------------------------- 双向


@dataclass(frozen=True)
class Ack(_MessageBase):
    """ACK —— 被确认的 TYPE（LEN=1）。v2 **不依赖**它，仅保留编号。"""

    ack_type: int = 0
    direction: int = 0  # 0 = PC→FPGA (0x00)，1 = FPGA→PC (0x80)

    MESSAGE: ClassVar[int] = MSG_ACK
    LEN: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _check_int("ack_type", self.ack_type, 0, 0xFF)
        _check_int("direction", self.direction, 0, 1)

    @property
    def type_(self) -> int:
        return TYPE_ACK | (self.direction << 7)

    def pack(self) -> bytes:
        return bytes((self.ack_type,))

    @classmethod
    def unpack(cls, payload: bytes, direction: int = 0) -> "Ack":
        _check_len(payload, cls.LEN, "ACK")
        return cls(payload[0], direction)


@dataclass(frozen=True)
class Nack(_MessageBase):
    """NACK —— 被拒绝的 TYPE（LEN=1）。

    只在收到**合法 CRC/长度、但语义不支持**的帧时发；CRC 错一律静默丢弃。
    """

    nack_type: int = 0
    direction: int = 0  # 0 = PC→FPGA (0x7F)，1 = FPGA→PC (0xFF)

    MESSAGE: ClassVar[int] = MSG_NACK
    LEN: ClassVar[int] = 1

    def __post_init__(self) -> None:
        _check_int("nack_type", self.nack_type, 0, 0xFF)
        _check_int("direction", self.direction, 0, 1)

    @property
    def type_(self) -> int:
        return TYPE_NACK | (self.direction << 7)

    def pack(self) -> bytes:
        return bytes((self.nack_type,))

    @classmethod
    def unpack(cls, payload: bytes, direction: int = 0) -> "Nack":
        _check_len(payload, cls.LEN, "NACK")
        return cls(payload[0], direction)


@dataclass(frozen=True)
class UnknownMessage(_MessageBase):
    """未定义 TYPE 的帧。**不抛异常**，交给上层决定是否忽略。

    这样将来 FPGA 新增消息时，旧版 Python 桥不会因为一个未知帧而崩溃。
    """

    type_value: int = 0
    payload: bytes = b""

    def __post_init__(self) -> None:
        _check_int("type_value", self.type_value, 0, 0xFF)
        object.__setattr__(self, "payload", bytes(self.payload))

    @property
    def type_(self) -> int:
        return self.type_value

    def pack(self) -> bytes:
        return self.payload


# ---------------------------------------------------------------- 分发

#: 线上 TYPE → 消息类。ACK/NACK 两个方向都登记。
MESSAGE_TYPES: Dict[int, Type[_MessageBase]] = {
    TYPE_ACK: Ack,
    TYPE_TIME_SYNC: TimeSync,
    TYPE_STATE_SET: StateSet,
    TYPE_QUESTION_OPEN: QuestionOpen,
    TYPE_QUESTION_CLOSE: QuestionClose,
    TYPE_NACK: Nack,
    TYPE_ACK_FPGA: Ack,
    TYPE_KEY_EVT: KeyEvent,
    TYPE_ANSWER: Answer,
    TYPE_STATUS_RPT: StatusReport,
    TYPE_NACK_FPGA: Nack,
}


def decode_message(frame: Union[Frame, bytes, bytearray]) -> _MessageBase:
    """把 :class:`Frame` 解码成具体消息对象。

    未知 TYPE 返回 :class:`UnknownMessage`；字段越界抛
    :class:`~ego_bridge.protocol.ProtocolError`。

    :raises ProtocolError: 载荷长度或字段取值非法
    """
    if not isinstance(frame, Frame):
        from .protocol import decode_frame

        frame = decode_frame(frame)

    type_ = frame.type_
    payload = frame.payload
    direction = 1 if type_ & 0x80 else 0

    if type_ in (TYPE_ACK, TYPE_ACK_FPGA):
        return Ack.unpack(payload, direction)
    if type_ in (TYPE_NACK, TYPE_NACK_FPGA):
        return Nack.unpack(payload, direction)

    cls = MESSAGE_TYPES.get(type_)
    if cls is None:
        return UnknownMessage(type_, payload)
    return cls.unpack(payload)  # type: ignore[attr-defined]


def pack_message(message: _MessageBase) -> bytes:
    """把任意消息对象编码成完整帧字节。"""
    return message.to_bytes()


def describe(message: _MessageBase) -> str:
    """人类可读的一行描述，供 cli/monitor 与日志使用。"""
    if isinstance(message, TimeSync):
        return (
            f"TIME_SYNC {message.hour:02d}:{message.minute:02d}:{message.second:02d}"
            f" day={message.day}"
        )
    if isinstance(message, StateSet):
        return (
            f"STATE_SET state={STATE_NAMES.get(message.state, message.state)}"
            f" session={message.session} pending={message.pending}"
        )
    if isinstance(message, QuestionOpen):
        return (
            f"QUESTION_OPEN req={message.req_id} n_options={message.n_options}"
            f" q_index={message.q_index}"
        )
    if isinstance(message, QuestionClose):
        return f"QUESTION_CLOSE req={message.req_id}"
    if isinstance(message, KeyEvent):
        return (
            f"KEY_EVT key={message.key_name}({message.key_id})"
            f" event={message.event} state={message.state}"
        )
    if isinstance(message, Answer):
        return (
            f"ANSWER req={message.req_id} q_index={message.q_index}"
            f" option={message.option} elapsed_ms={message.elapsed_ms}"
        )
    if isinstance(message, StatusReport):
        return (
            f"STATUS_RPT state={message.state_name} session={message.session}"
            f" pending={message.pending} flags=0x{message.flags:02X}"
            f" uptime_s={message.uptime_s}"
        )
    if isinstance(message, Ack):
        return f"ACK type=0x{message.ack_type:02X}"
    if isinstance(message, Nack):
        return f"NACK type=0x{message.nack_type:02X}"
    if isinstance(message, UnknownMessage):
        return f"UNKNOWN type=0x{message.type_value:02X} len={len(message.payload)}"
    return repr(message)  # pragma: no cover - 兜底


def try_decode_message(frame: Union[Frame, bytes, bytearray]) -> Optional[_MessageBase]:
    """宽容版 :func:`decode_message`：字段非法时返回 ``None`` 而不抛异常。"""
    try:
        return decode_message(frame)
    except (ProtocolError, ValueError):
        return None
