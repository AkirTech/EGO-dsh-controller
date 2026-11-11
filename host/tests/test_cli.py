"""cli.py —— 手动调试入口（契约 §5 验收清单的工具面）。

CLI 全部走 ``--sim``（无板卡），或用 ``ports`` 做只读枚举。
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ego_bridge.cli import main, parse_hex

from conftest import HOST_DIR, TIME_SYNC_FRIDAY_123456


# ---------------------------------------------------------------- hex 解析


@pytest.mark.parametrize(
    "text, expected",
    [
        ("AA 55 01 04 0C 22 38 05 91", TIME_SYNC_FRIDAY_123456),
        ("AA5501040C22380591", TIME_SYNC_FRIDAY_123456),
        ("aa 55 01 04 0c 22 38 05 91", TIME_SYNC_FRIDAY_123456),
        ("AA,55,01", bytes([0xAA, 0x55, 0x01])),
        ("0xAA 0x55", bytes([0xAA, 0x55])),
        ("", b""),
    ],
)
def test_parse_hex(text: str, expected: bytes):
    assert parse_hex(text) == expected


@pytest.mark.parametrize("text", ["GG", "AA 5", "AA 555", "1 2 3 4 5"])
def test_parse_hex_rejects_bad_input(text: str):
    with pytest.raises(ValueError):
        parse_hex(text)


# ---------------------------------------------------------------- ports


def test_ports_command_runs():
    """``ports`` 只读枚举，本机无论有没有板卡都应能跑完。"""
    code = main(["ports"])
    # 有板卡 -> 0；只有蓝牙 -> 2（明确报错，不瞎猜）
    assert code in (0, 2), code


# ---------------------------------------------------------------- send（契约 §5 第 1 条）


def test_send_time_sync_frame_in_sim(capsys):
    """``send "AA 55 01 04 0C 22 38 05 91"`` → 模拟器装载 5 12:34:56。"""
    code = main(["send", "AA 55 01 04 0C 22 38 05 91", "--sim"])
    assert code == 0
    out = capsys.readouterr().out
    assert "已发送 9 字节" in out
    assert "AA 55 01 04 0C 22 38 05 91" in out
    # 模拟器装载了时间
    assert "12:34:56" in out
    assert "day=5" in out


def test_send_malformed_hex_exits_nonzero(capsys):
    code = main(["send", "ZZ ZZ", "--sim"])
    assert code == 2


# ---------------------------------------------------------------- sync-time


def test_sync_time_in_sim(capsys):
    code = main(["sync-time", "--sim"])
    assert code == 0
    out = capsys.readouterr().out
    assert "TIME_SYNC" in out
    assert "isoWeekday=" in out


# ---------------------------------------------------------------- state


@pytest.mark.parametrize("state, expected", [(0, "FREE"), (1, "RUNNING"), (2, "WAITING")])
def test_state_command_in_sim(state: int, expected: str, capsys):
    code = main(["state", str(state), "--sim"])
    assert code == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "STATE_SET" in out
    assert expected in out


def test_state_rejects_out_of_range(capsys):
    """state 只接受 0/1/2（argparse choices）。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["state", "3", "--sim"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------- question


def test_question_command_in_sim(capsys):
    code = main(["question", "5", "3", "1", "--sim"])
    assert code == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "QUESTION_OPEN" in out
    assert "req=5" in out
    assert "q_open=True" in out


def test_question_rejects_more_than_nine_options(capsys):
    """契约 §3.1：超过 9 个选项时上位机直接拒绝下发。"""
    code = main(["question", "5", "10", "1", "--sim"])
    assert code == 2
    err = capsys.readouterr().err
    assert "1..9" in err


def test_close_q_command_in_sim(capsys):
    code = main(["close-q", "5", "--sim"])
    assert code == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "QUESTION_CLOSE" in out


# ---------------------------------------------------------------- 子进程入口


def test_module_entry_invokable():
    """``python -m ego_bridge.cli ports`` 必须能跑（有 __main__ 入口）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "ego_bridge.cli", "ports"],
        cwd=str(HOST_DIR),
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode in (0, 2), proc.stderr.decode("utf-8", "replace")
    assert b"COM" in proc.stdout or "串口" in proc.stdout.decode("utf-8", "replace")


def test_module_help_works():
    proc = subprocess.run(
        [sys.executable, "-m", "ego_bridge.cli", "--help"],
        cwd=str(HOST_DIR),
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0
    text = proc.stdout.decode("utf-8")
    for command in ("ports", "monitor", "send", "sync-time", "state", "question"):
        assert command in text, f"--help 里缺少 {command}"


def test_service_module_entry_exists():
    proc = subprocess.run(
        [sys.executable, "-m", "ego_bridge.service", "--help"],
        cwd=str(HOST_DIR),
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0
    text = proc.stdout.decode("utf-8")
    for option in ("--port", "--baud", "--sim", "--resync-hourly"):
        assert option in text, f"--help 里缺少 {option}"
