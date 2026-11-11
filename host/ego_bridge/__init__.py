"""``ego_bridge`` —— EGO-dsh-controller 的 Python 串口桥接包。

严格实现 ``docs/SERIAL-PROTOCOL.md`` **v2** 契约。

模块结构（契约 §4）::

    protocol.py   帧编解码 + CRC8（先自检 0xF4）
    transport.py  串口打开/关闭/读写，端口自动发现（VID/PID 优先）
    link.py       帧同步状态机（搜 AA55 → 读 TYPE/LEN → 收载荷 → 校验）
    messages.py   各消息的 dataclass 与 pack/unpack
    bridge.py     状态聚合、心跳监测、重连、时间同步
    sim.py        假 FPGA：没有板子也能跑通全链路
    service.py    stdio JSON 行服务，给 DSH 插件当后端
    cli.py        手动调试入口

快速上手::

    from ego_bridge.protocol import crc8, encode_frame
    from ego_bridge.messages import TimeSync, StateSet

    assert crc8(b"123456789") == 0xF4
    assert encode_frame(TimeSync(12, 34, 56, 5)) == bytes(
        [0xAA, 0x55, 0x01, 0x04, 0x0C, 0x22, 0x38, 0x05, 0x91]
    )

    # 没有板卡？用模拟器：
    from ego_bridge.sim import SimFpga
    from ego_bridge.bridge import Bridge

    fpga = SimFpga(heartbeat_s=0.1)
    bridge = Bridge(port="sim", transport_factory=lambda: fpga.transport)
    bridge.on_status(lambda s: print(s))
"""

from __future__ import annotations

__version__ = "0.1.0"
#: 实现的协议契约版本
PROTOCOL_VERSION = "v2"

__all__ = ["__version__", "PROTOCOL_VERSION"]
