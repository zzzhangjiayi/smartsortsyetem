"""与 UI 无关的分拣状态机。"""

import time
from enum import Enum
from typing import Callable, Dict, Optional

from .detectors import Detection
from .protocol import Command, Packet, WasteType, WASTE_NAMES
from .transport import Transport, TransportError


class SystemState(Enum):
    IDLE = "等待识别"
    WAIT_ACK = "等待下位机确认"
    EXECUTING = "下位机执行中"
    COOLDOWN = "等待物体离开"
    COMPRESSING = "压缩中"
    PAUSED = "已暂停"
    ERROR = "通信异常"


EventCallback = Callable[[str, dict], None]


class SortingController:
    def __init__(
        self,
        transport: Transport,
        event_callback: Optional[EventCallback] = None,
        stable_frames: int = 3,
        ack_timeout: float = 2.0,
        max_retries: int = 2,
        compress_after: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.event_callback = event_callback or (lambda _name, _data: None)
        self.stable_frames = stable_frames
        self.ack_timeout = ack_timeout
        self.max_retries = max_retries
        self.compress_after = compress_after
        self.clock = clock
        self.counts = {item: 0 for item in WasteType}
        self.bin_full = {item: False for item in WasteType}
        self.state = SystemState.IDLE
        self.pending = None  # type: Optional[WasteType]
        self._candidate = None  # type: Optional[WasteType]
        self._stable_count = 0
        self._missing_frames = 0
        self._last_packet = None  # type: Optional[Packet]
        self._deadline = 0.0
        self._retry_count = 0
        self._compress_sent = False
        self._sequence = 0
        self._active_command = None
        self._active_sequence = None  # type: Optional[int]

    def _emit(self, name: str, **data) -> None:
        self.event_callback(name, data)

    def _set_state(self, state: SystemState) -> None:
        if state != self.state:
            self.state = state
            self._emit("state", value=state.value)

    def reset(self) -> None:
        self.counts = {item: 0 for item in WasteType}
        self.bin_full = {item: False for item in WasteType}
        self.pending = None
        self._candidate = None
        self._stable_count = 0
        self._missing_frames = 0
        self._last_packet = None
        self._deadline = 0.0
        self._retry_count = 0
        self._compress_sent = False
        self._active_command = None
        self._active_sequence = None
        self._set_state(SystemState.IDLE)
        self._emit("counts", values=self.named_counts())
        self._emit("bins", values=self.named_bins())

    def named_counts(self) -> Dict[str, int]:
        return {WASTE_NAMES[key]: value for key, value in self.counts.items()}

    def named_bins(self) -> Dict[str, bool]:
        return {WASTE_NAMES[key]: value for key, value in self.bin_full.items()}

    def process_detection(self, detection: Optional[Detection]) -> None:
        """Require consecutive stable frames and wait for the object to leave."""
        if self.state == SystemState.COOLDOWN:
            if detection is None:
                self._missing_frames += 1
                if self._missing_frames >= self.stable_frames:
                    self._candidate = None
                    self._stable_count = 0
                    self._missing_frames = 0
                    self._set_state(SystemState.IDLE)
            else:
                self._missing_frames = 0
            return

        if self.state != SystemState.IDLE:
            return

        if detection is None:
            self._candidate = None
            self._stable_count = 0
            self._missing_frames = 0
            return

        waste_type = detection.waste_type
        if waste_type != self._candidate:
            self._stable_count = 1
            self._candidate = waste_type
        else:
            self._stable_count += 1

        if self._stable_count >= self.stable_frames:
            self._send_classification(waste_type)

    def _send_packet(self, packet: Packet, wait_for_reply: bool = True) -> bool:
        try:
            self.transport.send(packet)
        except TransportError as exc:
            self._set_state(SystemState.ERROR)
            self._emit("log", message="发送失败：{}".format(exc))
            return False
        self._emit("tx", packet=packet.encode().hex(" ").upper())
        if wait_for_reply:
            self._last_packet = packet
            self._deadline = self.clock() + self.ack_timeout
            self._retry_count = 0
        return True

    def _next_sequence(self) -> int:
        self._sequence = self._sequence % 0xFF + 1
        return self._sequence

    def _send_classification(self, waste_type: WasteType) -> None:
        """Send one WASTE_RESULT request after the stable-frame check."""
        if self.bin_full[waste_type]:
            self._candidate = None
            self._stable_count = 0
            self._missing_frames = 0
            self._set_state(SystemState.COOLDOWN)
            return

        sequence = self._next_sequence()
        self._active_command = Command.WASTE_RESULT
        self._active_sequence = sequence
        self.pending = waste_type
        packet = Packet(
            command=Command.WASTE_RESULT,
            payload=bytes((sequence, int(waste_type))),
        )

        if self._send_packet(packet, wait_for_reply=True):
            self._set_state(SystemState.WAIT_ACK)

    def poll(self) -> None:
        """Receive replies in every state and retry unacknowledged requests."""
        now = self.clock()

        for packet in self.transport.poll():
            self._emit("rx", packet=packet.encode().hex(" ").upper())
            self.handle_packet(packet)

        if (
            self.state == SystemState.WAIT_ACK
            and self._last_packet is not None
            and now >= self._deadline
        ):
            if self._retry_count >= self.max_retries:
                self._set_state(SystemState.ERROR)
                self._emit("log", message="应答超时，达到最大重试次数")
                self._last_packet = None
                self._active_command = None
                self._active_sequence = None
                self.pending = None
                return

            self._retry_count += 1
            try:
                self.transport.send(self._last_packet)
            except TransportError as exc:
                self._set_state(SystemState.ERROR)
                self._emit("log", message="重发失败：{}".format(exc))
                return

            self._emit(
                "tx",
                packet=self._last_packet.encode().hex(" ").upper(),
            )
            self._deadline = now + self.ack_timeout

    def handle_packet(self, packet: Packet) -> None:
        """Apply ACK, completion, bin-full and error messages."""
        command = packet.command
        payload = packet.payload

        if command == Command.ACK:
            if len(payload) < 2:
                return
            original_command, sequence = payload[0], payload[1]
            if (
                self._active_command is not None
                and original_command == int(self._active_command)
                and sequence == self._active_sequence
                and self.state == SystemState.WAIT_ACK
            ):
                self._set_state(SystemState.EXECUTING)

        elif command == Command.ACTION_DONE:
            if len(payload) < 1:
                return
            sequence = payload[0]
            if (
                sequence != self._active_sequence
                or self.state != SystemState.EXECUTING
            ):
                return

            completed_command = self._active_command
            completed_type = self.pending
            if (
                completed_command == Command.WASTE_RESULT
                and completed_type is not None
            ):
                self.counts[completed_type] += 1
                self._emit("counts", values=self.named_counts())

            self.pending = None
            self._active_command = None
            self._active_sequence = None
            self._last_packet = None
            self._deadline = 0.0
            self._candidate = None
            self._stable_count = 0
            self._missing_frames = 0
            self._set_state(SystemState.COOLDOWN)

            if completed_command == Command.WASTE_RESULT:
                self._maybe_compress()

        elif command == Command.BIN_FULL:
            if len(payload) < 1:
                return
            try:
                waste_type = WasteType(payload[0])
            except ValueError:
                self._emit("log", message="收到无效的满载垃圾类型")
                return
            self.bin_full[waste_type] = True
            self._emit("bins", values=self.named_bins())

        elif command == Command.ERROR:
            message = payload.decode("utf-8", errors="replace")
            self._set_state(SystemState.ERROR)
            self._emit(
                "log",
                message="下位机错误：{}".format(message or "未知错误"),
            )

    def _maybe_compress(self) -> None:
        """Send one sequenced START_COMPRESS request at the threshold."""
        total = sum(self.counts.values())
        if total < self.compress_after or self._compress_sent:
            return

        sequence = self._next_sequence()
        packet = Packet(Command.START_COMPRESS, bytes((sequence,)))
        self._active_command = Command.START_COMPRESS
        self._active_sequence = sequence
        self.pending = None

        if self._send_packet(packet, wait_for_reply=True):
            self._compress_sent = True
            self._set_state(SystemState.WAIT_ACK)
