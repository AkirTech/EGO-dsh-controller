"""假 FPGA 模拟器 —— 没有板卡也能跑通全链路（契约 v2 §4）。

用内存双向管道（:class:`~ego_bridge.transport.MemoryTransport`）顶替串口，
行为对齐真 FPGA：

* 解析 PC 发来的 ``TIME_SYNC`` / ``STATE_SET`` / ``QUESTION_OPEN`` / ``QUESTION_CLOSE``
* 每秒（可调速）发一次 ``STATUS_RPT`` 心跳；状态变化立即补发一次
* :meth:`SimFpga.press_key` 产生 ``KEY_EVT``
* **提问态按中键只产生 ``ANSWER``，不发 ``KEY_EVT``**（v2 分流规则，契约 §3.2）
* 上/下键是本地逻辑，不发任何帧（契约 §3.2 末尾）
* :meth:`SimFpga.inject_bad_frame` 可以塞 CRC 错的帧，验证上位机不崩

.. note::
   **模拟器与真 FPGA 的已知偏差**（详见 README）：

   * 没有 UART 位时序、没有半帧截断（字节总是整体交付）；真板的半帧情形
     由 ``link.py`` 的逐字节状态机测试覆盖
   * ``uptime_s`` 按真实墙钟走秒，而不是 100 MHz 分频计数，所以短会话里
     它一直是 0（重启识别在真实短会话中不敏感）
   * 选项回绕按 ``n_options - 1`` 上界，与真板 ``option_sel.v`` 的
     ``q_max_index`` 一致
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .link import FrameParser
from .messages import (
    Answer,
    KeyEvent,
    Nack,
    QuestionClose,
    QuestionOpen,
    StateSet,
    StatusReport,
    TimeSync,
    KEY_DOWN,
    KEY_MIDDLE,
    KEY_UP,
    EVENT_LONG,
    EVENT_PRESS,
    FLAG_TIME_VALID,
    MSG_STATE_SET,
    STATE_FREE,
    STATE_NAMES,
    STATE_WAITING,
)
from .protocol import Frame, decode_frame, encode_frame, format_hex
from .transport import MemoryTransport

__all__ = ["SimFpga", "SimStats"]


@dataclass
class SimStats:
    """模拟器侧的计数，便于断言「PC 到底发了什么」。"""

    frames_from_pc: int = 0
    bad_frames: int = 0
    status_sent: int = 0
    key_events_sent: int = 0
    answers_sent: int = 0


class SimFpga:
    """假 FPGA。

    :param heartbeat_s: 心跳周期（秒）。测试里传 ``0.05`` 加速。
    :param threaded: ``True`` 时起后台线程；``False`` 时需要手动调 :meth:`pump`。
    :param start_uptime_s: 上电秒计数初值。
    :param nack_on_invalid_state: ``state > 2`` 时是否回 ``NACK(0x02)``。
        默认 ``True``，与真板 RTL 一致（``link_ctrl`` 对越界字段回 NACK）。
    """

    def __init__(
        self,
        *,
        heartbeat_s: float = 1.0,
        threaded: bool = True,
        start_uptime_s: int = 0,
        nack_on_invalid_state: bool = True,
        report_arrow_keys: bool = False,
    ) -> None:
        self.heartbeat_s = heartbeat_s
        self.nack_on_invalid_state = nack_on_invalid_state
        #: 上/下键是否也上报 KEY_EVT（真板不上报，默认关）
        self.report_arrow_keys = report_arrow_keys

        self.transport = MemoryTransport(timeout=0.01)
        self.stats = SimStats()

        # -- FPGA 内部状态 --
        self.state: int = STATE_FREE
        self.session: int = 0
        self.pending: int = 0
        self.flags: int = 0
        self.uptime_s: int = start_uptime_s
        self.time_valid: bool = False
        self.uart_error: bool = False

        self.q_open: bool = False
        self.req_id: int = 0
        self.n_options: int = 0
        self.q_index: int = 1
        self.option_sel: int = 0

        self.last_time_sync: Optional[TimeSync] = None
        self.received: List[Frame] = []
        self.received_messages: List[object] = []

        self._parser = FrameParser(on_error=self._on_parse_error)
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._heartbeat_enabled = True
        self._next_heartbeat = time.monotonic()
        self._started_at = time.monotonic()
        self._q_opened_at: Optional[float] = None
        self._base_uptime = start_uptime_s
        self._base_monotonic = self._started_at

        if threaded:
            self.start()

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_evt.clear()
            self._next_heartbeat = time.monotonic() + self.heartbeat_s
        self._thread = threading.Thread(
            target=self._loop, name="sim-fpga", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        with self._lock:
            self._running = False

    def close(self) -> None:
        self.stop()
        self.transport.close()

    def __enter__(self) -> "SimFpga":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- 主循环

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.pump()
            except Exception:  # noqa: BLE001 - 模拟器线程绝不能把宿主带崩
                pass
            self._stop_evt.wait(0.002)

    def pump(self) -> None:
        """处理一次输入 + 到点就发心跳。可手动调用（``threaded=False``）。"""
        data = self._drain_from_pc()
        if data:
            for frame in self._parser.feed_bytes(data):
                self._handle_frame(frame)

        now = time.monotonic()
        with self._lock:
            self.uptime_s = self._base_uptime + int(now - self._base_monotonic)
            if not self._heartbeat_enabled:
                return
            due = now >= self._next_heartbeat
            if due:
                # 追赶：心跳不会因为调度抖动累积漂移
                self._next_heartbeat += self.heartbeat_s
                if self._next_heartbeat <= now:
                    self._next_heartbeat = now + self.heartbeat_s
        if due:
            self.emit_status()

    # ---------------------------------------------------------------- 输入

    def _drain_from_pc(self) -> bytes:
        """把 PC 写来的字节取出来（直接操作管道，避免 read 超时空转）。"""
        transport = self.transport
        lock = getattr(transport, "_lock", None)
        buf = getattr(transport, "to_fpga", None)
        if buf is None:  # pragma: no cover - 兼容自定义 transport
            return transport.read(4096)
        if lock is None:  # pragma: no cover
            data = bytes(buf)
            del buf[:]
            return data
        with lock:
            data = bytes(buf)
            del buf[:]
        return data

    def _handle_frame(self, frame: Frame) -> None:
        self.stats.frames_from_pc += 1
        self.received.append(frame)

        # 只处理 PC→FPGA 方向（bit7 = 0）
        if frame.type_ & 0x80:
            return
        type_number = frame.type_ & 0x7F
        payload = frame.payload

        if type_number == 0x01:  # TIME_SYNC
            if len(payload) != 4:
                self._nack(frame.type_)
                return
            hour, minute, second, day = payload
            if hour > 23 or minute > 59 or second > 59 or not 1 <= day <= 7:
                self._nack(frame.type_)
                return
            message = TimeSync(hour, minute, second, day)
            self.last_time_sync = message
            self.received_messages.append(message)
            with self._lock:
                self.time_valid = True
                self.flags |= FLAG_TIME_VALID
            self.emit_status()  # 状态变化立即发（契约 §3.2）
            self._log(f"时间装载 {hour:02d}:{minute:02d}:{second:02d} day={day}")
            return

        if type_number == 0x02:  # STATE_SET
            if len(payload) != 3:
                self._nack(frame.type_)
                return
            state, session, pending = payload
            if state > 2:
                # 与真板一致：字段越界回 NACK(0x02)，状态保持不变。
                # 契约 §3.1 的 STATE_SET 条目曾写"忽略且不发 NACK"，与同节
                # NACK 条目和 §5 第 12 条矛盾，已按后者统一（见契约 v2.1）。
                if self.nack_on_invalid_state:
                    self._nack(frame.type_)
                return
            self.received_messages.append(StateSet(state, session, pending))
            with self._lock:
                changed = (self.state, self.session, self.pending) != (
                    state,
                    session,
                    pending,
                )
                self.state = state
                self.session = session
                self.pending = pending
            self._log(f"状态 → {STATE_NAMES.get(state, state)} session={session}")
            if changed:
                self.emit_status()
            return

        if type_number == 0x03:  # QUESTION_OPEN
            if len(payload) != 3:
                self._nack(frame.type_)
                return
            req_id, n_options, q_index = payload
            if not 1 <= n_options <= 9 or not 1 <= q_index <= 16:
                # 选项上限 9：上位机本该在上报前就拒绝（契约 §3.1）
                self._nack(frame.type_)
                return
            message = QuestionOpen(req_id, n_options, q_index)
            self.received_messages.append(message)
            with self._lock:
                self.q_open = True
                self.req_id = req_id
                self.n_options = n_options
                self.q_index = q_index
                self.option_sel = 0
                self.state = STATE_WAITING
                self._q_opened_at = time.monotonic()
            self._log(f"提问开始 req={req_id} n={n_options} idx={q_index}")
            self.emit_status()
            return

        if type_number == 0x04:  # QUESTION_CLOSE
            if len(payload) != 1:
                self._nack(frame.type_)
                return
            (req_id,) = payload
            self.received_messages.append(QuestionClose(req_id))
            with self._lock:
                self.q_open = False
                self.n_options = 0
                self.option_sel = 0
                self._q_opened_at = None
                if self.state == STATE_WAITING:
                    self.state = STATE_FREE
            self._log(f"提问结束 req={req_id}")
            self.emit_status()
            return

        # 其他消息（ACK/NACK 等）不响应
        return

    def _on_parse_error(self, kind: str, detail: str) -> None:
        self.stats.bad_frames += 1
        self._log(f"收到坏帧（{kind}）：{detail}")

    # ---------------------------------------------------------------- 输出

    def _send_frame(self, frame: Frame) -> None:
        # FPGA → PC 方向：写 to_host，主机的 read() 才取得到
        self._write_bytes(frame.encode())

    def _write_bytes(self, data: bytes) -> None:
        # 模拟器是「FPGA 这一侧」：往 to_host 里写，主机才读得到
        self.transport.to_host.extend(bytes(data))

    def emit_status(self) -> StatusReport:
        """主动发一帧 ``STATUS_RPT``。"""
        with self._lock:
            self.uptime_s = self._base_uptime + int(
                time.monotonic() - self._base_monotonic
            )
            report = StatusReport(
                self.state, self.session, self.pending, self.flags, self.uptime_s & 0xFFFF
            )
            self.stats.status_sent += 1
        self._send_frame(report.to_frame())
        return report

    def _nack(self, type_: int) -> None:
        self._send_frame(Nack(type_ & 0x7F, direction=1).to_frame())

    def press_key(
        self,
        key_id: int,
        *,
        event: int = EVENT_PRESS,
        elapsed_ms: Optional[int] = None,
    ) -> Optional[object]:
        """模拟按一次键。

        * 提问态 + 中键 + 单击 → 只发 ``ANSWER``（**不发** ``KEY_EVT``）
        * 上/下键 → 只改本地高亮，不发帧（真板行为）
        * 其他 → 发 ``KEY_EVT``

        :returns: 实际发出的消息对象，什么也没发时返回 ``None``。
        """
        with self._lock:
            in_question = self.q_open
            snapshot_state = self.state
            if in_question and key_id == KEY_DOWN:
                self.option_sel = (
                    _limit(self.n_options) if self.option_sel == 0
                    else self.option_sel - 1
                )
                self._log(f"下键 → 选项 {self.option_sel}")
                return None
            if in_question and key_id == KEY_UP:
                self.option_sel = (
                    0 if self.option_sel >= _limit(self.n_options)
                    else self.option_sel + 1
                )
                self._log(f"上键 → 选项 {self.option_sel}")
                return None
            if not in_question and key_id in (KEY_DOWN, KEY_UP) and not self.report_arrow_keys:
                return None

            if in_question and key_id == KEY_MIDDLE and event == EVENT_PRESS:
                if elapsed_ms is None:
                    opened = self._q_opened_at or time.monotonic()
                    elapsed_ms = int((time.monotonic() - opened) * 1000)
                elapsed_ms = max(0, min(0xFFFF, elapsed_ms))  # 饱和不回绕
                answer = Answer(
                    self.req_id, self.q_index, self.option_sel, elapsed_ms
                )
                self.stats.answers_sent += 1
                self._log(
                    f"中键确认 → ANSWER req={answer.req_id} option={answer.option}"
                    f" {answer.elapsed_ms} ms"
                )
                # 分流规则：提问态中键只发 ANSWER，绝不发 KEY_EVT（契约 §3.2 v2）
                key_event = None
            else:
                key_event = KeyEvent(key_id, event, snapshot_state)
                self.stats.key_events_sent += 1
                self._log(
                    f"按键 {key_event.key_name}({key_id}) event={event}"
                    f" state={snapshot_state}"
                )
                answer = None

        if answer is not None:
            self._send_frame(answer.to_frame())
            return answer
        if key_event is None:
            return None
        self._send_frame(key_event.to_frame())
        return key_event

    # ---------------------------------------------------------------- 注入

    def inject_raw(self, data: bytes) -> None:
        """把任意字节塞进 PC 的接收方向（用来测鲁棒性）。"""
        self._write_bytes(bytes(data))

    def inject_bad_frame(self, type_: int = 0x83, payload: bytes = b"") -> bytes:
        """注入一个 **CRC 故意写错** 的帧。返回实际注入的字节。"""
        good = encode_frame(type_, payload)
        bad = good[:-1] + bytes(((good[-1] ^ 0xFF) & 0xFF,))
        self._write_bytes(bad)
        self._log(f"注入坏帧 {format_hex(bad)}")
        return bad

    def inject_garbage(self, data: bytes = b"\x00\xAA\xAA\x55\x11") -> bytes:
        """注入垃圾字节（含 ``AA AA 55`` 重同步序列）。"""
        self._write_bytes(bytes(data))
        return bytes(data)

    # ---------------------------------------------------------------- 控制

    def pause_heartbeat(self) -> None:
        """停掉心跳 —— 用来验证上位机的「3 秒无心跳」判定。"""
        with self._lock:
            self._heartbeat_enabled = False
        self._log("心跳已暂停")

    def resume_heartbeat(self) -> None:
        with self._lock:
            self._heartbeat_enabled = True
            self._next_heartbeat = time.monotonic() + self.heartbeat_s
        self._log("心跳已恢复")

    def simulate_reboot(self) -> None:
        """模拟板卡重启：``uptime_s`` 归零、``time_valid`` 清零、提问态复位。"""
        with self._lock:
            self._base_uptime = 0
            self._base_monotonic = time.monotonic()
            self.uptime_s = 0
            self.state = STATE_FREE
            self.session = 0
            self.pending = 0
            self.flags = 0
            self.time_valid = False
            self.q_open = False
            self.n_options = 0
            self.option_sel = 0
            self._q_opened_at = None
        self._log("模拟重启：uptime_s=0，time_valid=0")
        self.emit_status()

    # ---------------------------------------------------------------- 诊断

    def _log(self, text: str) -> None:
        if self.on_log is not None:
            try:
                self.on_log(text)
            except Exception:  # noqa: BLE001
                pass

    #: 可选日志回调（``cli monitor --sim`` / service stderr 用）
    on_log = None

    @property
    def link_up(self) -> bool:
        return self.transport.is_open

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SimFpga(state={STATE_NAMES.get(self.state, self.state)},"
            f" q_open={self.q_open}, option={self.option_sel},"
            f" uptime_s={self.uptime_s})"
        )


def _limit(n_options: int) -> int:
    """最高可选索引，即 ``n_options - 1``。

    真板 ``option_sel.v`` 收到 ``QUESTION_OPEN`` 时把 ``q_max_index`` 设为
    ``n_options - 1``，上/下键在 ``0..q_max_index`` 之间回绕。这里照抄，
    不再固定按 9 回绕 —— 一个 3 选项的问题按到 2 就该回 0，而不是绕到 8。
    """
    return max(1, min(9, n_options or 1)) - 1
