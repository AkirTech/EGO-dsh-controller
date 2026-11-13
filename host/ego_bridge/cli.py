"""手动调试入口：``python -m ego_bridge.cli``。

子命令::

    ports                            列出候选端口（蓝牙会带「排除」标记）
    monitor    [--sim] [--port X]    连上后持续打印收到的帧（人类可读 + 十六进制）
    send <hex bytes> [--sim]         直接发原始字节
    sync-time  [--sim]               发 TIME_SYNC（本机时间）
    state <0|1|2> [--sim]            发 STATE_SET
    question <req> <n_opt> <qidx> [--sim]   发 QUESTION_OPEN
    close-q <req> [--sim]            发 QUESTION_CLOSE

示例::

    python -m ego_bridge.cli ports
    python -m ego_bridge.cli send "AA 55 01 04 0C 22 38 05 91"      # 契约 §5 第 1 条
    python -m ego_bridge.cli monitor --sim
    python -m ego_bridge.cli state 1 --sim
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from typing import Iterable, List, Optional

from . import sim as sim_module
from .bridge import Bridge, LinkEvent
from .link import FrameParser
from .messages import (
    Answer,
    KeyEvent,
    QuestionClose,
    QuestionOpen,
    StateSet,
    StatusReport,
    TimeSync,
    describe,
    EVENT_PRESS,
    KEY_NAMES,
    MAX_OPTIONS,
    STATE_FREE,
    STATE_NAMES,
)
from .protocol import MAX_PAYLOAD, decode_frame, format_hex
from .transport import (
    DEFAULT_BAUD,
    HAVE_PYSERIAL,
    PortNotFoundError,
    SerialTransport,
    TransportError,
    find_port,
    format_port_table,
    list_ports,
)

__all__ = ["main"]


# ---------------------------------------------------------------- 工具


def _err(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _info(text: str) -> None:
    print(text, flush=True)


def parse_hex(text: str) -> bytes:
    """把 ``"AA 55 01"`` / ``"AA5501"`` / ``"aa,55,01"`` 解析成字节串。

    分隔开写时**每个 token 必须是 2 位十六进制**；不连写的偶数长串也可以。
    这样 ``"AA 5"`` 会报错而不是被静默当成 ``0x05`` —— 联调时这种笔误
    会让人对着"发了但没反应"排查半天。
    """
    cleaned = (
        text.replace(",", " ").replace("0x", " ").replace("0X", " ").strip()
    )
    parts = cleaned.split()
    if not parts:
        return b""
    if len(parts) == 1 and len(parts[0]) > 2:
        token = parts[0]
        if len(token) % 2:
            raise ValueError(f"十六进制串长度必须是偶数：{token!r}")
        parts = [token[i : i + 2] for i in range(0, len(token), 2)]
    out = bytearray()
    for part in parts:
        if len(part) != 2:
            raise ValueError(f"每个字节必须是 2 位十六进制：{part!r}")
        try:
            value = int(part, 16)
        except ValueError as exc:
            raise ValueError(f"不是合法十六进制字节：{part!r}") from exc
        if not 0 <= value <= 0xFF:
            raise ValueError(f"字节越界：{part!r}")
        out.append(value)
    return bytes(out)


def _make_bridge(args) -> Bridge:
    """按参数构造 :class:`Bridge`。``--sim`` 时把模拟器接进去。"""
    sim_fpga = None
    if args.sim or args.port == "sim":
        sim_fpga = sim_module.SimFpga(heartbeat_s=0.5, threaded=True)
        sim_fpga.on_log = lambda text: _err(f"[sim] {text}")
        bridge = Bridge(
            port="sim",
            baudrate=args.baud,
            transport_factory=lambda: sim_fpga.transport,
            logger=lambda text: _err(f"[bridge] {text}"),
        )
        bridge._cli_sim = sim_fpga  # 保存引用，避免被 GC
        return bridge
    return Bridge(
        port=args.port,
        baudrate=args.baud,
        logger=lambda text: _err(f"[bridge] {text}"),
    )


def _describe_frame(frame) -> str:
    try:
        hex_text = format_hex(frame.encode())
    except Exception:  # noqa: BLE001 - 坏帧也要能打出来
        hex_text = "<encode failed>"
    try:
        text = describe(decode_message_for_cli(frame))
    except Exception as exc:  # noqa: BLE001
        text = f"<decode failed: {exc}>"
    return f"{text:<64} | {hex_text}"


def decode_message_for_cli(frame):
    from .messages import decode_message

    return decode_message(frame)


# ---------------------------------------------------------------- ports


def cmd_ports(args) -> int:
    ports = list_ports()
    _info("本机串口：")
    _info(format_port_table(ports))
    _info("")
    try:
        device = find_port(args.port, ports=ports)
        _info(f"自动发现结果：{device}")
        return 0
    except PortNotFoundError as exc:
        _err(f"自动发现失败：{exc}")
        return 2


# ---------------------------------------------------------------- monitor


def cmd_monitor(args) -> int:
    bridge = _make_bridge(args)
    stop = {"flag": False}

    def on_link(event: LinkEvent) -> None:
        direction = "up" if event.up else "down"
        _info(f"[link {direction}] {event.reason}")

    def on_raw(frame) -> None:
        _info(_describe_frame(frame))

    bridge.on_link(on_link)
    bridge.on_raw(on_raw)

    _info("开始监视（Ctrl+C 退出）")
    try:
        while True:
            time.sleep(0.2)
            if args.sim:
                # 模拟器模式下演示按键：可选
                pass
    except KeyboardInterrupt:
        _info("\n退出")
    finally:
        bridge.close()
    return 0


# ---------------------------------------------------------------- send


def cmd_send(args) -> int:
    data = parse_hex(args.hex)
    if len(data) > MAX_PAYLOAD + 5:
        _err(f"警告：{len(data)} 字节超出最长帧 13 字节，仍照发")

    if args.sim:
        # 模拟器模式：直接把字节喂给假 FPGA
        sim_fpga = sim_module.SimFpga(heartbeat_s=0.2, threaded=True)
        sim_fpga.transport.write(data)
        time.sleep(0.05)
        sim_fpga.pump()
        out = bytes(sim_fpga.transport.to_host)
        _info(f"已发送 {len(data)} 字节：{format_hex(data)}")
        _info(f"模拟器回帧（{len(out)} 字节）：{format_hex(out) if out else '（无）'}")
        if sim_fpga.last_time_sync is not None:
            ts = sim_fpga.last_time_sync
            _info(
                f"模拟器已装载时间 {ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}"
                f" day={ts.day}"
            )
        sim_fpga.close()
        return 0

    #------------------------------------------------------------------
    # Bare mode: write the bytes to an open port, no bridge and therefore no
    # handshake.
    #
    # This is the whole point for raw-byte debugging. `Bridge` sends its own
    # TIME_SYNC on connect (host clock) and a STATE_SET, so any frame written
    # through it races the handshake -- a user trying to set 23:59:50 sees the
    # board snap back to the host time instead. The default stays through the
    # bridge (it verifies the link); --raw bypasses it.
    #------------------------------------------------------------------
    if args.raw:
        return _send_raw_bare(args, data)

    bridge = _make_bridge(args)
    deadline = time.monotonic() + 1.5
    while not bridge.connected and time.monotonic() < deadline:
        time.sleep(0.05)
    if not bridge.connected:
        _err("链路未建立，无法发送")
        bridge.close()
        return 3
    ok = bridge.send_raw(data)
    bridge.close()
    if ok:
        _info(f"已发送 {len(data)} 字节：{format_hex(data)}")
        return 0
    _err("发送失败")
    return 3


def _send_raw_bare(args, data: bytes) -> int:
    """直接写串口，**不做任何握手**。

    调试原始字节时握手是噪声：它会发 TIME_SYNC（本机时间）和 STATE_SET，
    把调用者想设置的数值覆盖掉。

    打开端口时刻意把 DTR/RTS 拉低：pyserial 默认两者为 True，CP2102 会因此
    在 UART 线上产生一次电平跳变，板卡的 uart_rx 若正好在这时看到下降沿就会
    当作起始位、读进一个假字节。先置低再等一小段空闲，让接收端回到 IDLE。
    """
    if not HAVE_PYSERIAL:
        _err("pyserial 未安装，无法使用真实串口（pip install pyserial）")
        return 1

    import serial  # 局部导入：模拟器模式下不需要

    port_name = args.port or find_port(args.port)
    try:
        port = serial.Serial()
        port.port = port_name
        port.baudrate = args.baud
        port.timeout = 0.2
        port.write_timeout = 1.0
        # Keep the modem-control lines quiet; see the docstring.
        port.dtr = False
        port.rts = False
        port.open()
    except Exception as exc:  # noqa: BLE001
        _err(f"打开 {port_name} 失败：{exc}")
        _err("提示：DSH 插件在跑时会独占端口，先停掉 dsh web")
        return 1

    try:
        # Drop anything the board already queued so the read-back is the reply
        # to THIS frame, not a stale heartbeat.
        port.reset_input_buffer()
        time.sleep(0.25)          # let any in-flight character finish
        port.reset_input_buffer()

        port.write(data)
        port.flush()
        _info(f"已发送 {len(data)} 字节（裸模式，未握手）：{format_hex(data)}")

        watch = max(0.0, float(args.watch))
        if watch > 0:
            deadline = time.monotonic() + watch
            got = bytearray()
            while time.monotonic() < deadline:
                chunk = port.read(64)
                if chunk:
                    got += chunk
            _info(f"{watch:.1f} 秒内收到 {len(got)} 字节")
            if got:
                _info(f"  {format_hex(bytes(got))}")
        return 0
    finally:
        port.close()


# ---------------------------------------------------------------- 语义命令


def _send_and_wait(args, message, *, watch_sim: bool = True) -> int:
    """发一条消息；``--sim`` 时顺便把模拟器状态打出来。"""
    bridge = _make_bridge(args)
    sim_fpga = getattr(bridge, "_cli_sim", None)

    received: List[str] = []

    def on_raw(frame) -> None:
        received.append(_describe_frame(frame))

    bridge.on_raw(on_raw)

    deadline = time.monotonic() + 2.0
    while not bridge.connected and time.monotonic() < deadline:
        time.sleep(0.02)
    if not bridge.connected:
        _err("链路未建立")
        bridge.close()
        return 3

    ok = bridge.send(message)
    time.sleep(0.15)
    bridge.close()

    if not ok:
        _err("发送失败")
        return 3
    _info(f"已发送：{describe(message)}")
    for line in received:
        _info(f"  收到 | {line}")
    if sim_fpga is not None and watch_sim:
        _info(
            f"模拟器状态：state={STATE_NAMES.get(sim_fpga.state, sim_fpga.state)}"
            f" q_open={sim_fpga.q_open} option={sim_fpga.option_sel}"
            f" time_valid={sim_fpga.time_valid}"
        )
    # 模拟器归本函数所有，这里必须关掉它的后台线程
    if sim_fpga is not None:
        sim_fpga.close()
    return 0


def cmd_sync_time(args) -> int:
    moment = datetime.datetime.now()
    message = TimeSync.from_datetime(moment)
    _info(
        f"本机时间 {moment.strftime('%Y-%m-%d %H:%M:%S')}"
        f"（isoWeekday={moment.isoweekday()}）"
    )
    return _send_and_wait(args, message)


def cmd_state(args) -> int:
    if args.state not in STATE_NAMES:
        _err(f"state 必须是 0/1/2，收到 {args.state}")
        return 2
    message = StateSet(args.state, args.session, args.pending)
    return _send_and_wait(args, message)


def cmd_question(args) -> int:
    if not 1 <= args.n_options <= MAX_OPTIONS:
        _err(f"n_options 必须在 1..{MAX_OPTIONS}（契约 §3.1：超过 9 个上位机直接拒绝）")
        return 2
    message = QuestionOpen(args.req_id, args.n_options, args.q_index)
    return _send_and_wait(args, message)


def cmd_close_q(args) -> int:
    message = QuestionClose(args.req_id)
    return _send_and_wait(args, message)


# ---------------------------------------------------------------- 入口


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ego_bridge.cli",
        description="EGO-dsh-controller 串口调试工具（契约 v2）",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", default=None, help="串口名，缺省自动发现")
    common.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="波特率")
    common.add_argument(
        "--sim", action="store_true", help="无板卡模式（内存模拟 FPGA）"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ports", parents=[common], help="列出候选端口")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("monitor", parents=[common], help="持续打印收到的帧")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("send", parents=[common], help="发送原始十六进制字节")
    p.add_argument("hex", help='十六进制字节串，如 "AA 55 01 04 0C 22 38 05 91"')
    p.add_argument(
        "--raw",
        action="store_true",
        help="裸模式：直接写串口，不做握手（设置时间时必须用它，"
             "否则握手发的 TIME_SYNC 会覆盖你要发的值）",
    )
    p.add_argument(
        "--watch",
        type=float,
        default=0.0,
        help="裸模式下发完继续读取的秒数（默认 0，不读）",
    )
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("sync-time", parents=[common], help="发 TIME_SYNC")
    p.set_defaults(func=cmd_sync_time)

    p = sub.add_parser("state", parents=[common], help="发 STATE_SET")
    p.add_argument("state", type=int, choices=(0, 1, 2), help="0=FREE 1=RUNNING 2=WAITING")
    p.add_argument("--session", type=int, default=0, help="会话号 0-15")
    p.add_argument("--pending", type=int, default=0, help="待处理提问数 0-255")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("question", parents=[common], help="发 QUESTION_OPEN")
    p.add_argument("req_id", type=int, help="请求编号 0-255")
    p.add_argument("n_options", type=int, help="选项个数 1-9")
    p.add_argument("q_index", type=int, default=1, nargs="?", help="问题序号 1-16")
    p.set_defaults(func=cmd_question)

    p = sub.add_parser("close-q", parents=[common], help="发 QUESTION_CLOSE")
    p.add_argument("req_id", type=int, help="请求编号")
    p.set_defaults(func=cmd_close_q)

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = make_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return args.func(args)
    except PortNotFoundError as exc:
        _err(str(exc))
        return 2
    except TransportError as exc:
        _err(f"串口错误：{exc}")
        return 3
    except ValueError as exc:
        _err(f"参数错误：{exc}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
