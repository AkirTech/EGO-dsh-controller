"""pytest 共享夹具。

**所有测试必须离线跑通**：不依赖真实串口，不联网。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List

import pytest

# 让 `import ego_bridge` 在不安装包的情况下也能工作
HOST_DIR = Path(__file__).resolve().parents[1]
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

#: 契约 §5 验收清单里的真实帧常量
TIME_SYNC_FRIDAY_123456 = bytes(
    [0xAA, 0x55, 0x01, 0x04, 0x0C, 0x22, 0x38, 0x05, 0x91]
)


@pytest.fixture
def time_sync_frame() -> bytes:
    return TIME_SYNC_FRIDAY_123456


@pytest.fixture
def sim_fpga():
    """默认心跳 0.05 s 的模拟器，测试结束自动关闭。"""
    from ego_bridge.sim import SimFpga

    fpga = SimFpga(heartbeat_s=0.05, threaded=True)
    try:
        yield fpga
    finally:
        fpga.close()


@pytest.fixture
def bridge_to_sim(sim_fpga):
    """已经连到模拟器的 Bridge（加速参数），测试结束自动关闭。"""
    from ego_bridge.bridge import Bridge

    bridge = Bridge(
        port="sim",
        transport_factory=lambda: sim_fpga.transport,
        heartbeat_timeout_s=0.5,
    )
    try:
        yield bridge
    finally:
        bridge.close()


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.005) -> bool:
    """轮询等待条件成立（不 sleep 固定时长，测试更快更稳）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def collect(messages: List[object], kind):
    """从列表里筛出指定类型的消息。"""
    return [m for m in messages if isinstance(m, kind)]
