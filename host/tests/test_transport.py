"""transport.py —— 端口发现优先级、蓝牙排除、串口参数（契约 §1 / §4.2）。

**全部离线**：用 :class:`PortInfo` 构造假的枚举结果，不碰真实串口。
"""

from __future__ import annotations

import pytest

from ego_bridge.transport import (
    BLUETOOTH_MARKERS,
    CP210X_PID,
    CP210X_VID,
    DEFAULT_BAUD,
    MemoryTransport,
    PortInfo,
    PortNotFoundError,
    SerialTransport,
    TransportError,
    find_port,
    format_port_table,
    HAVE_PYSERIAL,
    list_ports,
)


#: 本机真实存在的蓝牙串口（任务描述里提到的 BTHENUM 场景）
BT_PORTS = [
    PortInfo(
        device="COM3",
        description="蓝牙链接上的标准串行 (COM3)",
        hwid=r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0046\8&36C8EB55&0&0CAEBD6D2D02_C00000000",
    ),
    PortInfo(
        device="COM7",
        description="蓝牙链接上的标准串行 (COM7)",
        hwid=r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000\8&36C8EB55&0&000000000000_00000004",
    ),
]

#: 板载 CP2102
CP210X_PORT = PortInfo(
    device="COM11",
    description="Silicon Labs CP210x USB to UART Bridge (COM11)",
    hwid=r"USB VID:PID=10C4:EA60 SER=0001 LOCATION=1-1",
    vid=CP210X_VID,
    pid=CP210X_PID,
)


# ---------------------------------------------------------------- 蓝牙识别


def test_bluetooth_detected_by_hwid():
    for port in BT_PORTS:
        assert port.is_bluetooth is True, port.describe()
        assert port.is_cp210x is False


def test_bluetooth_detected_by_description():
    port = PortInfo(
        device="COM20",
        description="Standard Serial over Bluetooth link",
        hwid="",
    )
    assert port.is_bluetooth is True


def test_bluetooth_detected_by_vid_pid_absent():
    """蓝牙串口普遍没有 vid/pid，这也是一个可靠信号。"""
    port = PortInfo(device="COM9", description="串口", hwid="BTHENUM\\...")
    assert port.vid is None and port.pid is None
    assert port.is_bluetooth is True


def test_cp210x_detected_by_vid_pid():
    assert CP210X_PORT.is_cp210x is True
    assert CP210X_PORT.is_bluetooth is False


def test_cp210x_detected_by_hwid_when_vid_missing():
    """少数驱动不给 vid/pid，退化为看 hwid 里的 10C4:EA60。"""
    port = PortInfo(
        device="COM12",
        description="USB Serial",
        hwid="USB VID:PID=10C4:EA60",
    )
    assert port.is_cp210x is True


def test_non_cp210x_usb_serial_not_matched():
    """别的 USB 串口（如 CH340）不能被误认成板卡。"""
    ch340 = PortInfo(
        device="COM13",
        description="USB-SERIAL CH340",
        hwid="USB VID:PID=1A86:7523",
        vid=0x1A86,
        pid=0x7523,
    )
    assert ch340.is_cp210x is False
    assert ch340.is_bluetooth is False


# ---------------------------------------------------------------- 发现优先级


def test_priority_1_explicit_port_wins():
    """① 显式指定优先级最高，即使自动发现能匹配到别的端口。"""
    assert find_port("COM42", ports=[CP210X_PORT]) == "COM42"


def test_priority_1_explicit_not_validated():
    """显式端口不校验是否存在 —— 让 serial.Serial 报错以区分「名字错」和「没插」。"""
    assert find_port("COM99", ports=[]) == "COM99"


def test_priority_2_cp210x_auto_discovered():
    """② 按 VID/PID 自动匹配 CP210x。"""
    ports = BT_PORTS + [CP210X_PORT]
    assert find_port(None, ports=ports) == "COM11"


def test_priority_2_ignores_bluetooth():
    """蓝牙串口即使排在前面也不能被选中。"""
    ports = list(reversed(BT_PORTS)) + [CP210X_PORT]
    assert find_port(None, ports=ports) == "COM11"


def test_priority_3_no_match_raises_with_full_list():
    """③ 找不到就**明确报错**，不能瞎猜 —— 异常里要带全部端口。"""
    with pytest.raises(PortNotFoundError) as excinfo:
        find_port(None, ports=BT_PORTS)
    error = excinfo.value
    assert error.ports == BT_PORTS
    message = str(error)
    assert "CP210x" in message
    assert "COM3" in message  # 列出看到的端口
    assert "蓝牙" in message


def test_priority_3_empty_port_list():
    with pytest.raises(PortNotFoundError) as excinfo:
        find_port(None, ports=[])
    assert excinfo.value.ports == []
    assert "没有任何串口" in str(excinfo.value) or "没有找到" in str(
        excinfo.value
    )


def test_ambiguous_multiple_cp210x_raises():
    """多个 CP210x 时不猜，要求用户 --port 指定。"""
    second = PortInfo(
        device="COM12",
        description="Silicon Labs CP210x",
        hwid="USB VID:PID=10C4:EA60",
        vid=CP210X_VID,
        pid=CP210X_PID,
    )
    with pytest.raises(PortNotFoundError, match="多个 CP210x"):
        find_port(None, ports=[CP210X_PORT, second])


def test_non_cp210x_serial_is_listed_but_not_chosen():
    """有非 CP210x 的 USB 串口时仍然报错，但会提示可以用 --port 指定。"""
    ch340 = PortInfo(
        device="COM13",
        description="USB-SERIAL CH340",
        hwid="USB VID:PID=1A86:7523",
        vid=0x1A86,
        pid=0x7523,
    )
    with pytest.raises(PortNotFoundError) as excinfo:
        find_port(None, ports=BT_PORTS + [ch340])
    message = str(excinfo.value)
    assert "COM13" in message
    assert "--port" in message


# ---------------------------------------------------------------- 展示


def test_format_port_table_marks_bluetooth_and_cp210x():
    text = format_port_table(BT_PORTS + [CP210X_PORT])
    assert "COM3" in text
    assert "蓝牙" in text
    assert "CP210X" in text.upper()


def test_format_port_table_empty():
    assert "没有" in format_port_table([])


def test_describe_includes_vid_pid():
    text = CP210X_PORT.describe()
    assert "10C4" in text.upper() and "EA60" in text.upper()


# ---------------------------------------------------------------- 常量


def test_constants_match_contract():
    assert CP210X_VID == 0x10C4
    assert CP210X_PID == 0xEA60
    assert DEFAULT_BAUD == 115200
    assert "bthenum" in BLUETOOTH_MARKERS


def test_have_pyserial():
    assert HAVE_PYSERIAL is True, "pyserial 3.5 应已安装"


def test_list_ports_returns_portinfo():
    """真实枚举：本机只有蓝牙时也应该返回 PortInfo 对象（不崩）。"""
    ports = list_ports()
    assert all(isinstance(p, PortInfo) for p in ports)


# ---------------------------------------------------------------- SerialTransport 参数


class _FakeSerial:
    """记录构造参数的假 serial.Serial。"""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        self.closed = False
        self.input_reset = 0
        self.written = b""
        self.read_queue = b""
        self.raise_on_write = None
        _FakeSerial.instances.append(self)

    def reset_input_buffer(self):
        self.input_reset += 1

    def read(self, size=1):
        chunk, self.read_queue = self.read_queue[:size], self.read_queue[size:]
        return chunk

    def write(self, data):
        if self.raise_on_write is not None:
            raise self.raise_on_write
        self.written += bytes(data)
        return len(data)

    def close(self):
        self.is_open = False
        self.closed = True


@pytest.fixture
def fake_serial(monkeypatch):
    _FakeSerial.instances = []
    import ego_bridge.transport as transport_module

    monkeypatch.setattr(transport_module.serial, "Serial", _FakeSerial)
    return _FakeSerial


def test_open_uses_115200_8n1_no_flow_control(fake_serial):
    """契约 §1/§3.4：115200 8N1，无流控，清空输入缓冲。"""
    import ego_bridge.transport as transport_module

    transport = SerialTransport(port="COM11")
    serial_instance = fake_serial.instances[0]

    assert serial_instance.kwargs["baudrate"] == 115200
    assert serial_instance.kwargs["bytesize"] == transport_module.serial.EIGHTBITS
    assert serial_instance.kwargs["parity"] == transport_module.serial.PARITY_NONE
    assert serial_instance.kwargs["stopbits"] == transport_module.serial.STOPBITS_ONE
    assert serial_instance.kwargs["xonxoff"] is False
    assert serial_instance.kwargs["rtscts"] is False
    assert serial_instance.kwargs["dsrdtr"] is False
    assert serial_instance.kwargs["timeout"] != 0, "读超时必须非 0，否则读线程无法退出"
    assert serial_instance.input_reset == 1, "打开后必须清空输入缓冲"
    assert transport.is_open is True

    transport.close()
    assert serial_instance.closed is True
    assert transport.is_open is False


def test_write_and_read(fake_serial):
    transport = SerialTransport(port="COM11")
    serial_instance = fake_serial.instances[0]

    assert transport.write(b"\xAA\x55") == 2
    assert serial_instance.written == b"\xAA\x55"

    serial_instance.read_queue = b"\x01\x02\x03"
    assert transport.read(3) == b"\x01\x02\x03"
    transport.close()


def test_close_is_idempotent_and_silent(fake_serial):
    transport = SerialTransport(port="COM11")
    transport.close()
    transport.close()  # 第二次关不能抛
    assert transport.is_open is False


def test_read_on_closed_raises_transport_error(fake_serial):
    transport = SerialTransport(port="COM11")
    transport.close()
    with pytest.raises(TransportError):
        transport.read(1)
    with pytest.raises(TransportError):
        transport.write(b"\x00")


def test_open_failure_wrapped_as_transport_error(monkeypatch):
    """打不开端口必须转成 TransportError（bridge 靠它兜底）。"""
    import ego_bridge.transport as transport_module

    class _Boom:
        def __init__(self, **kwargs):
            raise transport_module.serial.SerialException("拒绝访问")

    monkeypatch.setattr(transport_module.serial, "Serial", _Boom)
    with pytest.raises(TransportError, match="拒绝访问"):
        SerialTransport(port="COM99")


def test_write_failure_wrapped_as_transport_error(fake_serial, monkeypatch):
    transport = SerialTransport(port="COM11")
    serial_instance = fake_serial.instances[0]
    import ego_bridge.transport as transport_module

    serial_instance.raise_on_write = transport_module.serial.SerialException("拔线了")
    with pytest.raises(TransportError):
        transport.write(b"\x00")
    transport.close()


def test_auto_open_false_does_not_touch_port(fake_serial):
    transport = SerialTransport(port="COM11", auto_open=False)
    assert fake_serial.instances == []
    assert transport.is_open is False
    transport.open()
    assert len(fake_serial.instances) == 1
    transport.close()


# ---------------------------------------------------------------- MemoryTransport


def test_memory_transport_roundtrip():
    transport = MemoryTransport()
    assert transport.is_open is True
    # host → fpga：write 写进 to_fpga
    assert transport.write(b"\xAA\x55") == 2
    assert bytes(transport.to_fpga) == b"\xAA\x55"
    # fpga → host：只有 to_host 里的字节能被 read 取到
    assert transport.read(2) == b""
    transport.to_host.extend(b"\x01\x02")
    assert transport.read(2) == b"\x01\x02"
    transport.close()
    assert transport.is_open is False


def test_memory_transport_reset_input_buffer():
    transport = MemoryTransport()
    transport.to_host.extend(b"\x01\x02\x03")
    transport.reset_input_buffer()
    assert transport.read(3) == b""


def test_memory_transport_read_after_close_raises():
    transport = MemoryTransport()
    transport.close()
    with pytest.raises(TransportError):
        transport.read(1)
    with pytest.raises(TransportError):
        transport.write(b"\x00")


def test_memory_transport_read_respects_timeout():
    import time

    transport = MemoryTransport(timeout=0.05)
    start = time.monotonic()
    assert transport.read(1) == b""
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04, f"应当等满 timeout，实际只等了 {elapsed:.3f}s"
