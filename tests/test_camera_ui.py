import os
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise unittest.SkipTest("PyQt5 is not installed in this test environment") from exc

from app.ui import MainWindow


class FakeWorker(QtCore.QObject):
    frame_ready = QtCore.pyqtSignal(QtGui.QImage)
    state_changed = QtCore.pyqtSignal(str)
    counts_changed = QtCore.pyqtSignal(object)
    bins_changed = QtCore.pyqtSignal(object)
    detection_changed = QtCore.pyqtSignal(object)
    model_changed = QtCore.pyqtSignal(str)
    source_changed = QtCore.pyqtSignal(str)
    source_failed = QtCore.pyqtSignal(str, str, bool)
    source_finished = QtCore.pyqtSignal()
    log_message = QtCore.pyqtSignal(str)
    connection_changed = QtCore.pyqtSignal(bool)
    fps_changed = QtCore.pyqtSignal(float)
    video_rate_changed = QtCore.pyqtSignal(float)
    progress_changed = QtCore.pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.source_requests = []
        self.processing = False

    def prepare_model(self):
        return None

    def start(self):
        return None

    def request_source(self, kind, value=""):
        self.source_requests.append((kind, value))

    def request_reset(self):
        return None

    def request_seek(self, frame_index):
        return None

    def start_processing(self):
        self.processing = True

    def pause_processing(self):
        self.processing = False

    def stop(self):
        return None

    def wait(self, timeout):
        return True


class CameraUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QtWidgets.QApplication.instance()
        if cls.application is None:
            cls.application = QtWidgets.QApplication([])

    def setUp(self):
        self.worker = FakeWorker()
        self.window = MainWindow(self.worker)

    def tearDown(self):
        self.window.close()

    @patch("app.ui.QtWidgets.QInputDialog.getInt", return_value=(2, True))
    def test_open_camera_requests_source_and_starts_processing(self, _dialog):
        self.window.open_camera()

        self.assertEqual(self.worker.source_requests, [("camera", "2")])
        self.assertTrue(self.window._processing)
        self.assertFalse(self.window.camera_button.isEnabled())

        self.worker.source_changed.emit("摄像头 2")
        self.assertTrue(self.window.camera_button.isEnabled())
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertEqual(self.window.camera_button.text(), "◎  切换摄像头")
        self.assertIn("摄像头 2", self.window.source_label.text())

    @patch("app.ui.QtWidgets.QInputDialog.getInt", return_value=(4, True))
    def test_camera_failure_restores_a_retryable_ui(self, _dialog):
        self.window.open_camera()
        self.worker.source_failed.emit(
            "camera",
            "摄像头打开失败：设备不可用",
            False,
        )

        self.assertFalse(self.window._processing)
        self.assertTrue(self.window.camera_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertIn("摄像头不可用", self.window.camera_label.text())
        self.assertEqual(self.window.source_label.text(), "摄像头未打开")


if __name__ == "__main__":
    unittest.main()
