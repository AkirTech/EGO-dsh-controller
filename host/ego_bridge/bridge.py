"""链路管理：连接握手、读线程、心跳监测、重连、时间同步（契约 v2 §3.4 / §4.2）。

设计要点：

* **连接握手**（§3.4）：打开端口 → 发 ``TIME_SYNC``（``datetime.now()``，
  ``day`` 用 ``isoWeekday()``，周一=1）+ ``STATE_SET``（默认 FREE）→ 开始读。
* **独立读线程** + ``queue.Queue`` 交付上层，避免阻塞 UI。
* **心跳监测**：连续 3 秒没有 ``STATUS_RPT`` → 判链路断开 → 指数退避重连
  （1/2/4/8 s，上限 8 s）。
* **板卡重启**：``uptime_s`` 回退 → 重发 ``TIME_SYNC`` + ``STATE_SET``。
* **发送不阻塞等 ACK**（v2 取消）。
* **fail-safe 是硬要求**：串口打不开、拔线、板子没插，都不能抛到宿主进程外面，
  也不能卡住。所有公开方法都在内部吞掉异常并转成 ``on_link(False)`` / 日志。
"""

from __future__ import annotations

import datetime
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

from .link import FrameParser
from .messages import (
    Answer,
    KeyEvent,
    Nack,
    StateSet,
    StatusReport,
    TimeSync,
    decode_message,
    STATE_FREE,
    STATE_NAMES,
)
from .protocol import Frame, ProtocolError
from .transport import (
    DEFAULT_BAUD,
    PortInfo,
    PortNotFoundError,
    SerialTransport,
    Transport,
    TransportError,
)

__all__ = [
    "DEFAULT_HEARTBEAT_TIMEOUT_S",
    "BACKOFF_SCHEDULE_S",
    "TIME_VALID_RESYNC_INTERVAL_S",
    "LinkEvent",
    "Bridge",
]


#: 连续这么久没有 STATUS_RPT 就判链路断开（契约 §4.2：3 秒）
DEFAULT_HEARTBEAT_TIMEOUT_S = 3.0

#: 指数退避重连间隔（秒），到顶后停在最后一个（8 s）
BACKOFF_SCHEDULE_S = (1.0, 2.0, 4.0, 8.0)

#: 板卡报 ``time_valid=0`` 时补发 TIME_SYNC 的最小间隔（秒）。
#: 契约 §3.2 要求「据此判断要不要补发时间，而不是盲目每秒重发」。
TIME_VALID_RESYNC_INTERVAL_S = 5.0


@dataclass
class LinkEvent:
    """``on_link`` 回调的载荷。"""

    up: bool
    reason: str = ""
    port: Optional[str] = None

    def as_dict(self) -> dict:
        payload: dict = {"up": self.up, "reason": self.reason}
        if self.port:
            payload["port"] = self.port
        return payload


class Bridge:
    """串口链路管理器。

    :param port: 显式端口名；``None`` 时自动发现；``"sim"`` 用模拟器。
    :param baudrate: 默认 115200。
    :param transport_factory: 注入传输层（测试用），签名 ``() -> Transport``。
    :param heartbeat_timeout_s: 无心跳判定阈值，默认 3.0 秒。
    :param backoff_schedule_s: 重连退避序列，默认 1/2/4/8 s。
    :param auto_start: ``True``（默认）时构造即开始连接；``False`` 时手动 :meth:`start`。
    :param clock: 注入时钟（测试用），签名 ``() -> float``。
    :param resync_hourly: 是否每小时补发一次 ``TIME_SYNC``（契约 §3.1）。
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = DEFAULT_BAUD,
        *,
        transport_factory: Optional[Callable[[], Transport]] = None,
        heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
        backoff_schedule_s: Sequence[float] = BACKOFF_SCHEDULE_S,
        auto_start: bool = True,
        clock: Optional[Callable[[], float]] = None,
        resync_hourly: bool = False,
        initial_state: int = STATE_FREE,
        initial_session: int = 0,
        initial_pending: int = 0,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self._transport_factory = transport_factory
        self.heartbeat_timeout_s = float(heartbeat_timeout_s)
        self.backoff_schedule_s = tuple(backoff_schedule_s) or (8.0,)
        self.resync_hourly = resync_hourly
        self._clock = clock or time.monotonic
        self._logger = logger

        self._transport: Optional[Transport] = None
        self._parser = FrameParser(on_error=self._on_parse_error)

        # -- 上层交付队列 --
        self.queue: "queue.Queue[Any]" = queue.Queue()

        # -- 回调 --
        self._on_status_cb: Optional[Callable[[StatusReport], None]] = None
        self._on_key_cb: Optional[Callable[[KeyEvent], None]] = None
        self._on_answer_cb: Optional[Callable[[Answer], None]] = None
        self._on_link_cb: Optional[Callable[[LinkEvent], None]] = None
        self._on_raw_cb: Optional[Callable[[Frame], None]] = None

        # -- 状态 --
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._connected = False
        self._connected_at: Optional[float] = None
        self._disconnect_reason = "read failed"
        self._last_status_monotonic: Optional[float] = None
        self.last_status: Optional[StatusReport] = None
        self.last_uptime_s: Optional[int] = None
        self._backoff_index = 0

        # 期望状态（握手时重发用）
        self.desired_state = int(initial_state)
        self.desired_session = int(initial_session)
        self.desired_pending = int(initial_pending)

        # 统计
        self.stats = {
            "frames_rx": 0,
            "frames_tx": 0,
            "bad_frames": 0,
            "connects": 0,
            "disconnects": 0,
            "reboots_detected": 0,
            "time_syncs": 0,
        }
        self.last_error: Optional[str] = None

        self._next_hourly_resync: Optional[float] = None
        self._last_resync_attempt: Optional[float] = None
        self._time_valid_seen = False

        if auto_start:
            self.start()

    # ================================================================ 回调

    def on_status(self, callback: Optional[Callable[[StatusReport], None]]) -> None:
        """注册 ``STATUS_RPT`` 回调。"""
        self._on_status_cb = callback

    def on_key(self, callback: Optional[Callable[[KeyEvent], None]]) -> None:
        """注册 ``KEY_EVT`` 回调。"""
        self._on_key_cb = callback

    def on_answer(self, callback: Optional[Callable[[Answer], None]]) -> None:
        """注册 ``ANSWER`` 回调。"""
        self._on_answer_cb = callback

    def on_link(self, callback: Optional[Callable[[LinkEvent], None]]) -> None:
        """注册链路状态变化回调。

        注册时会**立即用当前链路状态回调一次**：链路线程在构造时就可能已经
        尝试过连接（甚至已经失败），不补发会让订阅方永远看不到初始状态。
        """
        self._on_link_cb = callback
        if callback is not None:
            self._call_safely(
                callback,
                LinkEvent(self._connected, "current", self.port),
                "on_link",
            )

    def on_status(self, callback: Optional[Callable[[StatusReport], None]]) -> None:
        """注册 ``STATUS_RPT`` 回调。

        注册时若已有最近一次上报，立即补发一次，避免订阅方错过首帧。
        """
        self._on_status_cb = callback
        if callback is not None and self.last_status is not None:
            self._call_safely(callback, self.last_status, "on_status")

    def on_raw(self, callback: Optional[Callable[[Frame], None]]) -> None:
        """注册原始帧回调（调试/监控用）。"""
        self._on_raw_cb = callback

    # ================================================================ 生命周期

    def start(self) -> None:
        """启动读线程（幂等）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_evt.clear()
            self._thread = threading.Thread(
                target=self._run, name="ego-bridge", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """停止读线程并关闭端口。**不抛异常，不阻塞超过 ``timeout``**。"""
        self._stop_evt.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None
        self._close_transport()
        self._set_connected(False, "stopped")

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> "Bridge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ================================================================ 发送

    def send_frame(self, frame: Frame) -> bool:
        """发一帧。**失败不抛异常**，返回 ``False`` 并触发重连判定。"""
        data = frame.encode()
        return self._write(data)

    def send(self, message: Any) -> bool:
        """发一个消息对象（:mod:`ego_bridge.messages` 里的 dataclass）。"""
        try:
            data = message.to_bytes()
        except (ProtocolError, ValueError, AttributeError) as exc:
            self._log(f"消息打包失败，未发送：{exc}")
            return False
        return self._write(data)

    def send_raw(self, data: bytes) -> bool:
        """发原始字节（``cli send`` 用）。"""
        return self._write(bytes(data))

    def _write(self, data: bytes) -> bool:
        """写字节。**任何失败都在这里被吞掉**，只标记链路断开。"""
        transport = self._transport
        if transport is None or not getattr(transport, "is_open", False):
            self._log("发送失败：端口未打开")
            self._set_connected(False, "port not open")
            return False
        try:
            transport.write(data)
        except Exception as exc:  # noqa: BLE001 - fail-safe：绝不外抛
            self.last_error = str(exc)
            self._log(f"发送失败：{exc}")
            self._set_connected(False, f"write failed: {exc}")
            return False
        self.stats["frames_tx"] += 1
        return True

    # -- 语义化发送 ---------------------------------------------------

    def sync_time(self, moment: Optional[datetime.datetime] = None) -> bool:
        """发 ``TIME_SYNC``。``moment`` 缺省用本机当前时间（``isoWeekday``，周一=1）。"""
        if moment is None:
            moment = datetime.datetime.now()
        try:
            message = TimeSync.from_datetime(moment)
        except Exception as exc:  # noqa: BLE001
            self._log(f"构造 TIME_SYNC 失败：{exc}")
            return False
        ok = self.send(message)
        if ok:
            self.stats["time_syncs"] += 1
            self._next_hourly_resync = self._clock() + 3600.0
        return ok

    def set_state(
        self,
        state: int,
        session: Optional[int] = None,
        pending: Optional[int] = None,
    ) -> bool:
        """发 ``STATE_SET``，并记住期望值（重连后补发）。"""
        if session is not None:
            self.desired_session = int(session)
        if pending is not None:
            self.desired_pending = int(pending)
        self.desired_state = int(state)
        return self._send_desired_state()

    def _send_desired_state(self) -> bool:
        try:
            message = StateSet(
                self.desired_state, self.desired_session, self.desired_pending
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"构造 STATE_SET 失败：{exc}")
            return False
        return self.send(message)

    # ================================================================ 状态查询

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def describe_link(self) -> dict:
        return {
            "up": self._connected,
            "port": self.port,
            "state": STATE_NAMES.get(self.desired_state, self.desired_state),
            "uptime_s": self.last_uptime_s,
            "stats": dict(self.stats),
        }

    # ================================================================ 读线程

    def _run(self) -> None:
        """读线程主体。**整段包在 try 里**，任何异常都不能冒到线程外面。"""
        while not self._stop_evt.is_set():
            try:
                if self._transport is None:
                    if not self._try_connect():
                        delay = self._next_backoff()
                        self._log(f"重连失败，{delay:.1f} s 后重试")
                        self._stop_evt.wait(delay)
                        continue
                if not self._read_once():
                    # 读失败 / 心跳超时：标记断开、关端口、退避后重连
                    self._set_connected(False, self._disconnect_reason)
                    self._close_transport()
                    delay = self._next_backoff()
                    self._log(f"{delay:.1f} s 后重连")
                    self._stop_evt.wait(delay)
            except Exception as exc:  # noqa: BLE001 - 兜底，绝不外抛
                self.last_error = str(exc)
                self._log(f"读线程异常（已兜底）：{exc}\n{traceback.format_exc()}")
                self._set_connected(False, f"internal error: {exc}")
                self._close_transport()
                self._stop_evt.wait(self._next_backoff())

        self._close_transport()

    def _try_connect(self) -> bool:
        """建链 + 握手（契约 §3.4）。失败返回 ``False``，不抛异常。"""
        self._close_transport()
        try:
            transport = self._make_transport()
        except PortNotFoundError as exc:
            self.last_error = str(exc)
            self._log(str(exc))
            self._notify_down("port not found", force=True)
            return False
        except TransportError as exc:
            self.last_error = str(exc)
            self._log(f"打开端口失败：{exc}")
            self._notify_down(f"open failed: {exc}", force=True)
            return False
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._log(f"打开端口时意外异常：{exc}")
            self._notify_down(f"open failed: {exc}", force=True)
            return False

        self._transport = transport
        self._parser.reset()
        self._last_status_monotonic = None
        self.last_uptime_s = None

        # 步骤 2/3：TIME_SYNC + STATE_SET，不等 ACK
        try:
            self.sync_time()
            self._send_desired_state()
        except Exception as exc:  # noqa: BLE001
            self._log(f"握手发送失败：{exc}")
            self._close_transport()
            return False

        self.stats["connects"] += 1
        self._backoff_index = 0
        # 端口已开、握手已发，但**还不算链路 up** —— 契约 §3.4 步骤 4 要求
        # 「3 s 内收到任意一帧 → 判定链路 up」。计时从这一刻开始，
        # 3 s 内一帧都没有同样要判 down。
        self._connected_at = self._clock()
        self._last_resync_attempt = self._connected_at
        self._log(f"端口 {self.port} 已打开，握手已发送，等待 STATUS_RPT")
        return True

    def _make_transport(self) -> Transport:
        if self._transport_factory is not None:
            transport = self._transport_factory()
            if not getattr(transport, "is_open", False):
                transport.open()
            return transport
        return SerialTransport(port=self.port, baudrate=self.baudrate, auto_open=True)

    def _read_once(self) -> bool:
        """读一轮。返回 ``False`` 表示链路该断。"""
        transport = self._transport
        if transport is None:
            return False
        try:
            chunk = transport.read(64)
        except TransportError as exc:
            self.last_error = str(exc)
            self._log(f"读失败：{exc}")
            self._disconnect_reason = f"read failed: {exc}"
            return False
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._log(f"读异常：{exc}")
            self._disconnect_reason = f"read failed: {exc}"
            return False

        now = self._clock()

        # 没收到任何字节时也要检查超时
        if not chunk:
            if self._heartbeat_expired(now):
                return False
            self._maybe_hourly_resync(now)
            return True

        for frame in self._parser.feed_bytes(chunk):
            self._dispatch(frame, now)

        if self._heartbeat_expired(now):
            return False
        self._maybe_hourly_resync(now)
        return True

    def _heartbeat_expired(self, now: float) -> bool:
        """连续 ``heartbeat_timeout_s`` 没有 ``STATUS_RPT`` → 链路断开（契约 §4.2）。"""
        if not self._connected:
            return False
        # 还没收到过任何 STATUS_RPT：从建链时刻算起
        last = self._last_status_monotonic
        if last is None:
            last = self._connected_at
        if last is None:
            return False
        if now - last > self.heartbeat_timeout_s:
            self._disconnect_reason = "heartbeat timeout"
            self._log(f"{self.heartbeat_timeout_s:.1f} s 无 STATUS_RPT，判定链路断开")
            return True
        return False

    def _maybe_hourly_resync(self, now: float) -> None:
        if not self.resync_hourly or not self._connected:
            return
        deadline = self._next_hourly_resync
        if deadline is not None and now >= deadline:
            self.sync_time()

    # ================================================================ 帧分发

    def _dispatch(self, frame: Frame, now: float) -> None:
        self.stats["frames_rx"] += 1
        self._call_safely(self._on_raw_cb, frame, "on_raw")

        if not self._connected:
            # 收到任意一帧 → 判链路 up（契约 §3.4 步骤 4）
            self._set_connected(True, "frame received")

        try:
            message = decode_message(frame)
        except ProtocolError as exc:
            self._log(f"帧字段非法，已丢弃：{exc}（{frame.hex()}）")
            self.queue.put(frame)
            return

        # 任何帧都刷新活口，但只有 STATUS_RPT 才算心跳
        if isinstance(message, StatusReport):
            self._last_status_monotonic = now
            self._handle_restart(message)
            previous = self.last_status
            self.last_status = message
            self.queue.put(message)
            # 只在变化时回调，避免 1 Hz 心跳刷屏（契约 §6.2 status 去重）
            if previous is None or (
                previous.state,
                previous.session,
                previous.pending,
                previous.flags,
            ) != (
                message.state,
                message.session,
                message.pending,
                message.flags,
            ):
                self._call_safely(self._on_status_cb, message, "on_status")
            return

        if isinstance(message, KeyEvent):
            self.queue.put(message)
            self._call_safely(self._on_key_cb, message, "on_key")
            return

        if isinstance(message, Answer):
            self.queue.put(message)
            self._call_safely(self._on_answer_cb, message, "on_answer")
            return

        if isinstance(message, Nack):
            self._log(f"收到 NACK：被拒 TYPE=0x{message.nack_type:02X}")
            self.queue.put(message)
            return

        self.queue.put(message)

    def _handle_restart(self, report: StatusReport) -> None:
        """``uptime_s`` 回退 → 判板卡重启 → 重发 TIME_SYNC + STATE_SET（契约 §3.4）。"""
        previous = self.last_uptime_s
        self.last_uptime_s = report.uptime_s
        if previous is None:
            return
        if report.uptime_s < previous:
            self.stats["reboots_detected"] += 1
            self._log(
                f"检测到板卡重启（uptime_s {previous} → {report.uptime_s}），重发握手"
            )
            self.sync_time()
            self._send_desired_state()
            self._last_resync_attempt = self._clock()
            return

        # bit1「时间有效」为 0 → 板卡还没收到过 TIME_SYNC，补发一次。
        # 但**必须限速**：契约 §3.2 明确说「而不是盲目每秒重发」，
        # 否则 1 Hz 心跳会变成 1 Hz 校时风暴。这里最多每 5 s 补一次。
        if report.time_valid:
            self._time_valid_seen = True
            return
        now = self._clock()
        last = self._last_resync_attempt
        if last is not None and now - last < TIME_VALID_RESYNC_INTERVAL_S:
            return
        self._last_resync_attempt = now
        self._log("板卡报告 time_valid=0，补发 TIME_SYNC")
        self.sync_time()

    # ================================================================ 链路状态

    def _set_connected(self, up: bool, reason: str) -> None:
        """更新链路状态，**变化时**通知回调。

        注意：持续失败时只在第一次通知，避免退避重连阶段刷屏。
        """
        with self._lock:
            was = self._connected
            self._connected = up
            if up:
                self._connected_at = self._clock()
                self._last_status_monotonic = (
                    self._last_status_monotonic or self._connected_at
                )
            elif was:
                self.stats["disconnects"] += 1
        if was != up:
            if up:
                self._log(f"链路 up（{reason}）")
            else:
                self._log(f"链路 down（{reason}）")
            self._notify_link(LinkEvent(up, reason, self.port))

    def _notify_down(self, reason: str, *, force: bool = False) -> None:
        """通知一次 link down。

        :param force: ``True`` 时即使当前已经是 down 也发一次 ——
            用于「首次连接就失败」这种要立刻告知订阅方的场景。
        """
        if force and not self._connected:
            self._notify_link(LinkEvent(False, reason, self.port))
            return
        self._set_connected(False, reason)

    def _notify_link(self, event: LinkEvent) -> None:
        self._call_safely(self._on_link_cb, event, "on_link")

    # ================================================================ 工具

    def _next_backoff(self) -> float:
        """指数退避 1/2/4/8 s，到顶停在最后一项。"""
        schedule = self.backoff_schedule_s
        index = min(self._backoff_index, len(schedule) - 1)
        self._backoff_index += 1
        return schedule[index]

    def _close_transport(self) -> None:
        """关闭传输层。

        **只关闭自己创建的传输层**：注入的 ``transport_factory``（模拟器管道、
        测试桩）归注入方所有，由注入方自己关 —— 否则 bridge 重连时会把它关掉，
        模拟器就再也起不来了。
        """
        transport, self._transport = self._transport, None
        if transport is None or self._transport_factory is not None:
            return
        try:
            transport.close()
        except Exception:  # noqa: BLE001 - 关闭失败没有补救
            pass

    def _call_safely(self, callback, payload, name: str) -> None:
        """回调异常**绝不外抛** —— 宿主回调写错不能带崩链路线程。"""
        if callback is None:
            return
        try:
            callback(payload)
        except Exception as exc:  # noqa: BLE001
            self._log(f"回调 {name} 抛异常（已忽略）：{exc}")

    def _on_parse_error(self, kind: str, detail: str) -> None:
        self.stats["bad_frames"] += 1
        self._log(f"丢弃坏帧（{kind}）：{detail}")

    def _log(self, text: str) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            logger(text)
        except Exception:  # noqa: BLE001
            pass

    # ================================================================ 等待工具

    def wait_for_status(self, timeout: float = 2.0) -> Optional[StatusReport]:
        """同步等一个 ``STATUS_RPT``（测试/CLI 用）。超时返回 ``None``。"""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                item = self.queue.get(timeout=remaining)
            except queue.Empty:
                return None
            if isinstance(item, StatusReport):
                return item

    def drain(self, limit: int = 1000) -> List[Any]:
        """清空队列并返回内容（测试用）。"""
        items: List[Any] = []
        while len(items) < limit:
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return items
