"""上位机通信协议的封包与流式解析实现。"""

from dataclasses import dataclass
from enum import IntEnum
from typing import List


HEADER = b"\xAA\x55"
MAX_BODY_LENGTH = 64


class Command(IntEnum):
    WASTE_RESULT = 0x01
    ACK = 0x02
    ACTION_DONE = 0x03
    BIN_FULL = 0x04
    START_COMPRESS = 0x05
    HEARTBEAT = 0x06
    ERROR = 0x7F


class WasteType(IntEnum):
    HAZARDOUS = 0x01
    OTHER = 0x02
    RECYCLABLE = 0x03
    KITCHEN = 0x04


WASTE_NAMES = {
    WasteType.RECYCLABLE: "可回收垃圾",
    WasteType.KITCHEN: "厨余垃圾",
    WasteType.HAZARDOUS: "有害垃圾",
    WasteType.OTHER: "其他垃圾",
}


@dataclass(frozen=True)
class Packet:
    command: Command
    payload: bytes = b""

    def encode(self) -> bytes:
        """Generate a complete protocol frame."""
        payload = bytes(self.payload)
        length = 1 + len(payload)
        command = int(self.command)
        if not (0 < length <= MAX_BODY_LENGTH):
            raise ValueError("超出长度范围")
        if not (0x00 < command <= 0xFF):
            raise ValueError("指令错误")
        checksum = (length + command + sum(payload)) & 0xFF
        return HEADER + bytes((length, command)) + payload + bytes((checksum,))


class StreamParser:
    """把任意分片的 TCP/串口字节流增量解析为 Packet。"""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.error_count = 0

    def reset(self) -> None:
        self._buffer.clear()
        self.error_count = 0

    def feed(self, data: bytes) -> List[Packet]:
        """Parse fragmented, coalesced and noisy byte streams."""
        self._buffer.extend(data)
        packets = []  # type: List[Packet]
        while True:
            header_index = self._buffer.find(HEADER)
            if header_index == -1:
                if self._buffer.endswith(b"\xAA"):
                    if len(self._buffer) > 1:
                        del self._buffer[:-1]
                else:
                    self._buffer.clear()
                break
            if header_index > 0:
                del self._buffer[:header_index]
            if len(self._buffer) < 5:
                break
            length = self._buffer[2]
            if not (1 <= length <= MAX_BODY_LENGTH):
                self.error_count += 1
                del self._buffer[0]
                continue
            frame_length = 2 + 1 + length + 1
            if len(self._buffer) < frame_length:
                break
            frame = self._buffer[:frame_length]
            checksum_calculated = sum(frame[2 : 3 + length]) & 0xFF
            checksum_received = frame[3 + length]
            if checksum_calculated != checksum_received:
                self.error_count += 1
                del self._buffer[0]
                continue
            command_type = frame[3]
            try:
                command = Command(command_type)
            except ValueError:
                self.error_count += 1
                del self._buffer[0]
                continue
            payload = bytes(frame[4 : 3 + length])
            packets.append(Packet(command=command, payload=payload))
            del self._buffer[:frame_length]
        return packets
