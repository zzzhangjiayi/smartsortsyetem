"""统一创建视频文件和实时摄像头输入源。"""

import os
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple

import cv2
import numpy as np

from vedio.video_module import VideoFileSource


DEFAULT_VIDEO = "assets/assessment_demo.mp4"


class FrameSource(ABC):
    kind = "unknown"
    display_name = "未知图像源"

    @abstractmethod
    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        raise NotImplementedError

    @abstractmethod
    def restart(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def release(self) -> None:
        raise NotImplementedError


class CameraFrameSource(FrameSource):
    kind = "camera"

    def __init__(self, camera_index: int) -> None:
        self.source = camera_index
        self.cap = None
        self.backend_name = ""
        attempted_backends = []
        for backend_name, backend in self._backend_candidates():
            capture = cv2.VideoCapture(camera_index, backend)
            attempted_backends.append(backend_name)
            if capture.isOpened():
                self.cap = capture
                self.backend_name = backend_name
                break
            capture.release()

        if self.cap is None:
            raise RuntimeError(
                "无法打开摄像头：{}（已尝试：{}）".format(
                    camera_index,
                    " / ".join(attempted_backends),
                )
            )

        self.display_name = "摄像头 {} · {}".format(
            camera_index,
            self.backend_name,
        )
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame_count = 0
        self.source_fps = float(self.cap.get(cv2.CAP_PROP_FPS))
        self.last_error = ""
        self._read_retries = 3
        self._read_retry_delay = 0.04

    @staticmethod
    def _backend_candidates():
        candidates = []
        if os.name == "nt":
            candidates.extend(
                (
                    ("DirectShow", getattr(cv2, "CAP_DSHOW", 700)),
                    ("Media Foundation", getattr(cv2, "CAP_MSMF", 1400)),
                )
            )
        elif os.name == "posix":
            candidates.append(
                ("V4L2", getattr(cv2, "CAP_V4L2", 200))
            )
        candidates.append(("Default", getattr(cv2, "CAP_ANY", 0)))

        unique = []
        seen = set()
        for name, backend in candidates:
            if backend not in seen:
                unique.append((name, backend))
                seen.add(backend)
        return unique

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        for attempt in range(self._read_retries):
            ok, frame = self.cap.read()
            if ok and frame is not None:
                self.last_error = ""
                return True, frame
            if attempt + 1 < self._read_retries:
                time.sleep(self._read_retry_delay)

        self.last_error = "摄像头 {} 未返回画面，请检查设备连接或占用状态".format(
            self.source
        )
        raise RuntimeError(self.last_error)

    def restart(self) -> bool:
        return False

    def release(self) -> None:
        self.cap.release()


def create_frame_source(kind: str, value: str = "") -> FrameSource:
    if kind == "video":
        return VideoFileSource(value or DEFAULT_VIDEO)
    if kind == "camera":
        try:
            camera_index = int(value or 0)
        except ValueError as exc:
            raise ValueError("摄像头编号必须是整数") from exc
        return CameraFrameSource(camera_index)
    raise ValueError("未知图像源：{}；只允许 video 或 camera".format(kind))
