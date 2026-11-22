import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from app.detectors import YoloDetector
from app.frame_sources import create_frame_source
from app.protocol import WasteType


class VideoSourceTests(unittest.TestCase):
    def test_assessment_video_can_be_read_and_restarted(self):
        video = Path("assets/assessment_demo.mp4")
        self.assertTrue(video.is_file())
        source = create_frame_source("video", str(video))
        try:
            ok, frame = source.read()
            self.assertTrue(ok)
            self.assertEqual(frame.shape[:2], (720, 1280))
            self.assertGreater(source.frame_count, 300)
            self.assertTrue(source.restart())
            ok, restarted = source.read()
            self.assertTrue(ok)
            self.assertEqual(restarted.shape, frame.shape)
        finally:
            source.release()

    def test_original_project_video_supports_seek_and_restart(self):
        video = Path("vedio/dep.mp4")
        self.assertTrue(video.is_file())
        source = create_frame_source("video", str(video))
        try:
            self.assertEqual(source.frame_count, 2839)
            self.assertAlmostEqual(source.source_fps, 30.0, places=1)
            ok, first = source.read()
            self.assertTrue(ok)
            self.assertEqual(first.shape[:2], (720, 1280))
            self.assertTrue(source.seek_frame(100))
            ok, later = source.read()
            self.assertTrue(ok)
            self.assertEqual(later.shape, first.shape)
            self.assertTrue(source.restart())
            self.assertLessEqual(source.current_frame, 1)
        finally:
            source.release()

    def test_synthetic_source_is_not_available(self):
        with self.assertRaises(ValueError):
            create_frame_source("synthetic")


class CameraSourceTests(unittest.TestCase):
    @patch("app.frame_sources.cv2.VideoCapture")
    def test_unavailable_camera_is_reported_and_released(self, video_capture):
        capture = video_capture.return_value
        capture.isOpened.return_value = False

        with self.assertRaisesRegex(RuntimeError, "无法打开摄像头"):
            create_frame_source("camera", "2")

        self.assertGreaterEqual(video_capture.call_count, 1)
        for call in video_capture.call_args_list:
            self.assertEqual(call.args[0], 2)
        self.assertEqual(capture.release.call_count, video_capture.call_count)

    @patch("app.frame_sources.time.sleep")
    @patch("app.frame_sources.cv2.VideoCapture")
    def test_camera_reports_a_runtime_disconnect(self, video_capture, _sleep):
        capture = video_capture.return_value
        capture.isOpened.return_value = True
        capture.get.return_value = 30.0
        capture.read.return_value = (False, None)
        source = create_frame_source("camera", "0")

        with self.assertRaisesRegex(RuntimeError, "未返回画面"):
            source.read()

        self.assertEqual(capture.read.call_count, 3)

    @patch("app.frame_sources.os.name", "nt")
    @patch("app.frame_sources.cv2.VideoCapture")
    def test_windows_camera_backend_falls_back_to_media_foundation(
        self,
        video_capture,
    ):
        direct_show = Mock()
        direct_show.isOpened.return_value = False
        media_foundation = Mock()
        media_foundation.isOpened.return_value = True
        media_foundation.get.return_value = 30.0
        video_capture.side_effect = [direct_show, media_foundation]

        source = create_frame_source("camera", "0")

        self.assertIn("Media Foundation", source.display_name)
        self.assertEqual(video_capture.call_count, 2)


class _Scalar:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class _Coordinates:
    def __init__(self, values):
        self.values = np.array(values)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class _Box:
    conf = _Scalar(0.91)
    cls = _Scalar(0)
    xyxy = [_Coordinates([10, 20, 80, 120])]


class _Result:
    names = {0: "battery"}
    boxes = [_Box()]


class _FakeYolo:
    names = {0: "battery"}

    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [_Result()]


class YoloAdapterTests(unittest.TestCase):
    def test_yolo_api_is_adapted_without_loading_real_weights(self):
        fake_model = _FakeYolo()
        detector = YoloDetector(model=fake_model)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        output = detector.detect(frame)
        self.assertEqual(output[0].waste_type, WasteType.HAZARDOUS)
        self.assertEqual(output[0].box, (10, 20, 80, 120))
        self.assertIs(fake_model.calls[0]["source"], frame)

    def test_unknown_model_class_is_rejected(self):
        model = _FakeYolo()
        model.names = {0: "not-configured"}
        with self.assertRaises(ValueError):
            YoloDetector(model=model)


if __name__ == "__main__":
    unittest.main()
