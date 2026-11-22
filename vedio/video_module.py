"""OpenCV video source used to feed frames to YOLO.

The source preserves the original ``vedio`` directory name while always
returning frames in OpenCV's BGR format.
"""

from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


class VideoFileSource:
    """Frame source backed by an OpenCV video file."""

    kind = "video"

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError("视频文件不存在：{}".format(self.path))
        self.display_name = str(self.path)
        self.cap = cv2.VideoCapture(str(self.path))
        if not self.cap.isOpened():
            raise RuntimeError("无法打开视频：{}".format(self.path))
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.source_fps = float(self.cap.get(cv2.CAP_PROP_FPS))
        self.duration_seconds = (
            self.frame_count / self.source_fps if self.source_fps > 0 else 0.0
        )
        self.reached_end = False
        self.decode_failed = False
        self.last_error = ""

    @property
    def current_frame(self) -> int:
        return int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read one frame and distinguish end-of-file from decoding failure."""
        ret, frame = self.cap.read()
        if ret and frame is not None:
            self.reached_end = False
            self.decode_failed = False
            self.last_error = ""
            return True, frame

        if self.frame_count > 0 and self.current_frame >= self.frame_count:
            self.reached_end = True
            self.decode_failed = False
            return False, None

        self.decode_failed = True
        self.last_error = (
            "视频在第 {} 帧解码失败：{}".format(self.current_frame, self.path)
        )
        return False, None

    def seek_frame(self, frame_index: int) -> bool:
        """Seek to a frame after validating its zero-based index."""
        if frame_index < 0 or frame_index >= self.frame_count:
            return False
        ok = self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        if ok:
            self.reached_end = False
            self.decode_failed = False
            self.last_error = ""
        return ok

    def restart(self) -> bool:
        """Restart playback from frame zero."""
        return self.seek_frame(0)

    def release(self) -> None:
        self.cap.release()
