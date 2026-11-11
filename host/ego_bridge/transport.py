"""串口打开/关闭/读写 + 端口自动发现（契约 v2 §1 / §4.2）。

端口发现优先级（**不要瞎猜**）::

    ① 显式指定（命令行 --port / 配置）
    ② 按 CP210x VID=0x10C4 / PID=0xEA60 自动匹配
    ③ 列不出候选 → 明确报错，把全部端口打给用户看

本机还有大量蓝牙虚拟串口（``BTHENUM\\...``），**必须排除**：
它们也带 ``COMx`` 名字，随手取第一个会连到不存在的设备上。

.. note::
   pyserial 3.5 的 ``comports()`` 提供 ``vid`` / ``pid`` / ``hwid`` 属性，
   这里直接用，不再调用 ``GetPortNames()`` 之类的裸接口。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence, runtime_checkable

try:  # pragma: no cover - 只在没装 pyserial 时走这条路
    import serial
    from serial.tools import list_ports as _list_ports

    HAVE_PYSERIAL = True
except ImportError:  # pragma: no cover
    serial = None  # type: ignore[assignment]
    _list_ports = None  # type: ignore[assignment]
    HAVE_PYSERIAL = False

__all__ = [
    "HAVE_PYSERIAL",
    "CP210X_VID",
    "CP210X_PID",
    "DEFAULT_BAUD",
    "BLUETOOTH_MARKERS",
    "TransportError",
    "PortNotFoundError",
    "PortInfo",
    "Transport",
    "SerialTransport",
    "MemoryTransport",
    "list_ports",
    "find_port",
    "format_port_table",
]

# ---------------------------------------------------------------- 常量

CP210X_VID = 0x10C4
CP210X_PID = 0xEA60
DEFAULT_BAUD = 115200

#: 命中这些关键字就判定为蓝牙虚拟串口，一律排除
BLUETOOTH_MARKERS = (
    "bthenum",
    "bluetooth",
    "蓝牙",
    "bthmodem",
    "rfcomm",
)


# ---------------------------------------------------------------- 异常


class TransportError(Exception):
    """串口层错误基类。**所有失败都必须落在这个家族里**，便于 bridge 兜底。"""


class PortNotFoundError(TransportError):
    """没有找到可用端口。携带候选列表，方便上层原样打给用户。"""

    def __init__(self, message: str, ports: Optional[Sequence["PortInfo"]] = None):
        super().__init__(message)
        self.ports: List["PortInfo"] = list(ports or ())


# ---------------------------------------------------------------- 端口信息


@dataclass(frozen=True)
class PortInfo:
    """一个候选端口。"""

    device: str
    description: str = ""
    hwid: str = ""
    vid: Optional[int] = None
    pid: Optional[int] = None

    @property
    def is_bluetooth(self) -> bool:
        """蓝牙虚拟串口（``BTHENUM\\...`` 之类）。"""
        blob = f"{self.hwid} {self.description} {self.device}".lower()
        return any(marker in blob for marker in BLUETOOTH_MARKERS)

    @property
    def is_cp210x(self) -> bool:
        """是否是 CP210x（板载 USB-UART）。"""
        if self.vid is not None and self.pid is not None:
            return self.vid == CP210X_VID and self.pid == CP210X_PID
        # 少数驱动不给 vid/pid，退化为看 hwid/描述
        blob = f"{self.hwid} {self.description}".lower()
        return "cp210" in blob or (
            f"{CP210X_VID:04x}" in blob and f"{CP210X_PID:04x}" in blob
        )

    def describe(self) -> str:
        tags = []
        if self.is_bluetooth:
            tags.append("蓝牙(排除)")
        if self.is_cp210x:
            tags.append("CP210x")
        if self.vid is not None and self.pid is not None:
            tags.append(f"VID:PID={self.vid:04X}:{self.pid:04X}")
        suffix = f"  [{' / '.join(tags)}]" if tags else ""
        return f"{self.device:<8} {self.description}{suffix}"


def list_ports() -> List[PortInfo]:
    """枚举本机串口，**不做任何过滤**（蓝牙也会出现在结果里，带标记）。"""
    if not HAVE_PYSERIAL:  # pragma: no cover
        raise TransportError("pyserial 未安装，无法枚举串口（pip install pyserial）")
    ports: List[PortInfo] = []
    for port in _list_ports.comports():
        ports.append(
            PortInfo(
                device=port.device,
                description=port.description or "",
                hwid=port.hwid or "",
                vid=getattr(port, "vid", None),
                pid=getattr(port, "pid", None),
            )
        )
    ports.sort(key=lambda p: p.device)
    return ports


def format_port_table(ports: Sequence[PortInfo]) -> str:
    """把端口列表格式化成多行文本（给错误信息 / cli ``ports`` 用）。"""
    if not ports:
        return "（没有枚举到任何串口）"
    return "\n".join("  " + p.describe() for p in ports)


def find_port(
    explicit: Optional[str] = None,
    ports: Optional[Sequence[PortInfo]] = None,
) -> str:
    """按优先级挑选端口，返回设备名（如 ``COM7``）。

    :param explicit: 显式指定的端口名。给了就直接返回（不校验是否存在，
        让 ``serial.Serial`` 去报错，这样"端口名打错"和"板子没插"能区分开）。
    :param ports: 注入枚举结果，便于离线测试。
    :raises PortNotFoundError: 没有 CP210x 候选。异常里带全部端口列表。
    """
    if explicit:
        return explicit

    all_ports = list(ports) if ports is not None else list_ports()

    # ② CP210x VID/PID 精确匹配（先排除蓝牙）
    candidates = [p for p in all_ports if not p.is_bluetooth and p.is_cp210x]
    if len(candidates) == 1:
        return candidates[0].device
    if len(candidates) > 1:
        # 多个 CP210x：不猜，报错让用户用 --port 指定
        raise PortNotFoundError(
            "检测到多个 CP210x 串口，无法确定用哪一个；请用 --port 显式指定：\n"
            + format_port_table(candidates),
            all_ports,
        )

    # ③ 找不到就明确报错，绝不瞎猜
    bluetooth = [p for p in all_ports if p.is_bluetooth]
    others = [p for p in all_ports if not p.is_bluetooth]
    hints = []
    if others:
        hints.append("发现这些非蓝牙串口，但它们不是 CP210x（VID=10C4/PID=EA60）：")
        hints.append(format_port_table(others))
        hints.append("如果其中某个就是板卡，请用 --port 显式指定。")
    if all_ports:
        # 契约 §4.2：串口打开失败要"列出可用端口让用户选"，所以全部列出来
        hints.append("本机全部串口如下（请用 --port 显式指定，或插好板卡后重试）：")
        hints.append(format_port_table(all_ports))
    if bluetooth:
        hints.append(
            f"已排除 {len(bluetooth)} 个蓝牙虚拟串口（BTHENUM / Bluetooth），"
            "它们不是板卡。"
        )
    if not all_ports:
        hints.append("（本机没有枚举到任何串口）")

    # The EGO1 presents its CP2102 on J8, not on the J22 (JTAG) connector, and
    # the CP2102 needs the Silicon Labs VCP driver before Windows hands it a
    # COM name. Both facts make "no port found" a very common first result, so
    # name them instead of leaving the user to guess.
    hints.append(
        "注意：EGO1 的 CP2102 在 **J8（USB-UART）** 接口上，不在 J22（JTAG）那条线。"
        "若已插 J8 仍看不到，多半是缺 Silicon Labs CP210x 驱动"
        "（设备管理器里错误码 28）。详见 docs/DESIGN.md §0.1。"
    )

    raise PortNotFoundError(
        "没有找到可用于板卡的串口（期望 CP210x：VID=0x10C4 / PID=0xEA60）。\n"
        + "\n".join(hints),
        all_ports,
    )


# ---------------------------------------------------------------- 传输接口


@runtime_checkable
class Transport(Protocol):
    """bridge 只需要这 5 个能力。

    :class:`SerialTransport` 和 :class:`MemoryTransport`（sim 用）都实现它。
    """

    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def reset_input_buffer(self) -> None: ...


# ---------------------------------------------------------------- 串口实现


class SerialTransport:
    """pyserial 包装。**所有 pyserial 异常都转成** :class:`TransportError`。"""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = DEFAULT_BAUD,
        *,
        timeout: float = 0.1,
        write_timeout: float = 1.0,
        auto_open: bool = True,
        ports: Optional[Sequence[PortInfo]] = None,
    ) -> None:
        """
        :param port: 显式端口名；``None`` 时按 :func:`find_port` 自动发现。
        :param baudrate: 默认 115200。
        :param timeout: 读超时（秒）。**必须非 0**，否则读线程无法被
            ``close()`` 打断。
        :param auto_open: ``False`` 时只构造不打开。
        """
        self._explicit_port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._serial = None
        self._lock = threading.RLock()
        self.last_error: Optional[str] = None

        if not HAVE_PYSERIAL:  # pragma: no cover
            raise TransportError(
                "pyserial 未安装，无法使用真实串口（pip install pyserial）"
            )

        self.port = port if port else find_port(port, ports=ports)
        if auto_open:
            self.open()

    # -- 属性 -------------------------------------------------------

    @property
    def is_open(self) -> bool:
        ser = self._serial
        return bool(ser is not None and getattr(ser, "is_open", False))

    # -- 生命周期 ---------------------------------------------------

    def open(self) -> None:
        """打开端口：115200 8N1，无流控，清空输入缓冲（契约 §3.4 步骤 1）。"""
        with self._lock:
            if self.is_open:
                return
            try:
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    write_timeout=self.write_timeout,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )
            except (serial.SerialException, OSError, ValueError) as exc:
                self._serial = None
                self.last_error = str(exc)
                raise TransportError(f"打开串口 {self.port} 失败：{exc}") from exc
            try:
                self.reset_input_buffer()
            except (serial.SerialException, OSError) as exc:  # pragma: no cover
                self.last_error = str(exc)
                raise TransportError(f"清空 {self.port} 输入缓冲失败：{exc}") from exc

    def close(self) -> None:
        """关闭端口。**不抛异常** —— 关闭失败没有可做的补救。"""
        with self._lock:
            ser, self._serial = self._serial, None
            if ser is None:
                return
            try:
                ser.close()
            except (serial.SerialException, OSError):  # pragma: no cover
                pass

    def reset_input_buffer(self) -> None:
        ser = self._serial
        if ser is None:
            raise TransportError("端口未打开")
        try:
            ser.reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"清空输入缓冲失败：{exc}") from exc

    # -- 读写 -------------------------------------------------------

    def read(self, size: int = 1) -> bytes:
        """读取最多 ``size`` 字节。超时返回 ``b""``（不抛异常）。"""
        ser = self._serial
        if ser is None:
            raise TransportError("端口未打开")
        try:
            data = ser.read(size)
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"读串口失败：{exc}") from exc
        return bytes(data or b"")

    def write(self, data: bytes) -> int:
        """写字节。失败抛 :class:`TransportError`，由 bridge 触发重连。"""
        ser = self._serial
        if ser is None:
            raise TransportError("端口未打开")
        try:
            written = ser.write(bytes(data))
        except (serial.SerialException, OSError, serial.SerialTimeoutException) as exc:
            raise TransportError(f"写串口失败：{exc}") from exc
        return int(written or 0)

    # -- 诊断 -------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        state = "open" if self.is_open else "closed"
        return f"SerialTransport({self.port!r}, {self.baudrate}, {state})"


# ---------------------------------------------------------------- 内存实现


@dataclass
class MemoryTransport:
    """内存双向管道，给 :mod:`ego_bridge.sim` 用。

    两个方向各一个 ``bytearray``，语义与串口一致：
    ``read()`` 在没数据时按 ``timeout`` 等待（默认 0，立即返回空）。
    """

    to_host: bytearray = field(default_factory=bytearray)
    to_fpga: bytearray = field(default_factory=bytearray)
    timeout: float = 0.0
    _open: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def reset_input_buffer(self) -> None:
        with self._lock:
            self.to_host.clear()

    def read(self, size: int = 1) -> bytes:
        if not self._open:
            raise TransportError("管道已关闭")
        deadline = time.monotonic() + max(0.0, self.timeout)
        while True:
            with self._lock:
                if self.to_host:
                    chunk = bytes(self.to_host[:size])
                    del self.to_host[:size]
                    return chunk
            if time.monotonic() >= deadline:
                return b""
            time.sleep(0.0005)

    def write(self, data: bytes) -> int:
        if not self._open:
            raise TransportError("管道已关闭")
        with self._lock:
            self.to_fpga.extend(bytes(data))
        return len(data)
