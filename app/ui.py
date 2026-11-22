"""Dashboard-style PyQt5 interface for the smart waste sorting system."""

import datetime
import os
from pathlib import Path
from typing import Dict

from PyQt5 import QtCore, QtGui, QtWidgets

from .detectors import Detection
from .protocol import WASTE_NAMES, WasteType
from .worker import SortingWorker


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEP_VIDEO_PATH = PROJECT_ROOT / "vedio" / "dep.mp4"
IS_DOCKER = Path("/.dockerenv").exists() or os.environ.get("RUNNING_IN_DOCKER") == "1"


class GlowButton(QtWidgets.QPushButton):
    """Push button with an animated focus glow and pressed light effect."""

    GLOW_COLORS = {
        "primary": QtGui.QColor(54, 222, 207, 215),
        "secondary": QtGui.QColor(78, 164, 255, 195),
        "quiet": QtGui.QColor(169, 193, 214, 175),
        "danger": QtGui.QColor(255, 103, 128, 205),
    }

    def __init__(self, text: str, role: str = "secondary", parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", role)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAutoDefault(False)
        self.setMinimumHeight(44)

        self._glow = QtWidgets.QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 5)
        self._glow.setBlurRadius(16)
        self._glow.setColor(self.GLOW_COLORS.get(role, self.GLOW_COLORS["secondary"]))
        self.setGraphicsEffect(self._glow)

        self._glow_animation = QtCore.QPropertyAnimation(
            self._glow,
            b"blurRadius",
            self,
        )
        self._glow_animation.setDuration(170)
        self._glow_animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)

    def _animate_glow(self, blur_radius: float, offset_y: int) -> None:
        self._glow.setOffset(0, offset_y)
        self._glow_animation.stop()
        self._glow_animation.setStartValue(self._glow.blurRadius())
        self._glow_animation.setEndValue(blur_radius)
        self._glow_animation.start()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._glow.setColor(
            self.GLOW_COLORS.get(
                self.property("role"),
                self.GLOW_COLORS["secondary"],
            )
        )
        self._animate_glow(30, 7)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._animate_glow(14, 4)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._glow.setColor(QtGui.QColor(102, 255, 221, 235))
            self._animate_glow(48, 0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        self._animate_glow(26, 6)


class GripSplitterHandle(QtWidgets.QSplitterHandle):
    """Visible vertical grip used to resize the two main panels."""

    def __init__(self, orientation, parent) -> None:
        super().__init__(orientation, parent)
        self.setCursor(QtCore.Qt.SplitHCursor)
        self._hovered = False

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        color = QtGui.QColor("#54c8df" if self._hovered else "#b9d0dc")
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QtCore.QRectF(center_x - 3.0, 24, 6.0, max(42.0, self.height() - 48)),
            3,
            3,
        )
        painter.setBrush(QtGui.QColor("#ffffff"))
        for offset in (-12, 0, 12):
            painter.drawEllipse(
                QtCore.QPointF(center_x, center_y + offset),
                1.7,
                1.7,
            )


class GripSplitter(QtWidgets.QSplitter):
    def createHandle(self):
        return GripSplitterHandle(self.orientation(), self)


class ChineseFileDialogProxy(QtCore.QSortFilterProxyModel):
    """Translate file-system labels without changing dialog behavior."""

    TRANSLATIONS = {
        "File Folder": "文件夹",
        "File": "文件",
    }

    def data(self, index, role=QtCore.Qt.DisplayRole):
        value = super().data(index, role)
        if role == QtCore.Qt.DisplayRole and index.column() == 2:
            return self.TRANSLATIONS.get(value, value)
        return value


class MainWindow(QtWidgets.QMainWindow):
    WASTE_TONES = {
        WasteType.RECYCLABLE: "recyclable",
        WasteType.KITCHEN: "kitchen",
        WasteType.HAZARDOUS: "hazardous",
        WasteType.OTHER: "other",
    }

    def __init__(self, worker: SortingWorker, auto_start: bool = False) -> None:
        super().__init__()
        self.worker = worker
        self._processing = False
        self._video_fps = 0.0
        self._last_video_frame = 0
        self._last_video_total = 0
        self._camera_request = None
        self.count_labels = {}  # type: Dict[str, QtWidgets.QLabel]
        self.bin_labels = {}  # type: Dict[str, QtWidgets.QLabel]

        translator = QtCore.QTranslator(self)
        translations_path = QtCore.QLibraryInfo.location(
            QtCore.QLibraryInfo.TranslationsPath
        )
        if translator.load("qt_zh_CN", translations_path):
            if application := QtWidgets.QApplication.instance():
                application.installTranslator(translator)
                self._qt_translator = translator

        font = QtGui.QFont("SimSun", 11)
        font.setStyleStrategy(QtGui.QFont.PreferAntialias)
        try:
            font.setHintingPreference(QtGui.QFont.PreferFullHinting)
        except AttributeError:
            pass
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.setFont(font)
        self.setFont(font)

        self._build_ui()
        self._connect_signals()
        self._auto_start = auto_start
        QtCore.QTimer.singleShot(50, self._initialize_worker)

    @staticmethod
    def _refresh_style(widget: QtWidgets.QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    @staticmethod
    def _add_card_shadow(widget: QtWidgets.QWidget, blur: int = 30) -> None:
        shadow = QtWidgets.QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(24, 53, 82, 32))
        widget.setGraphicsEffect(shadow)

    def _new_card(self, minimum_height: int = 0):
        card = QtWidgets.QFrame()
        card.setProperty("card", True)
        card.setMinimumHeight(minimum_height)
        self._add_card_shadow(card)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        return card, layout

    @staticmethod
    def _add_section_heading(
        layout: QtWidgets.QVBoxLayout,
        title: str,
        kicker: str = "",
    ) -> None:
        if kicker:
            eyebrow = QtWidgets.QLabel(kicker)
            eyebrow.setObjectName("sectionKicker")
            layout.addWidget(eyebrow)
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

    def _make_status_label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setProperty("role", "status")
        label.setProperty("tone", "info")
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setMinimumWidth(128)
        label.setMinimumHeight(30)
        return label

    def _build_ui(self) -> None:
        self.setWindowTitle("智能垃圾分拣系统 · Sorting Console")
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        if available is not None:
            width = min(1440, max(1080, available.width() - 80))
            height = min(900, max(640, available.height() - 80))
        else:
            width, height = 1360, 780
        self.resize(width, height)
        self.setMinimumSize(1060, 620)

        central = QtWidgets.QFrame()
        central.setObjectName("root")
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hero = QtWidgets.QFrame()
        hero.setObjectName("heroCard")
        hero_layout = QtWidgets.QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 14, 22, 14)
        hero_layout.setSpacing(12)

        mark = QtWidgets.QLabel("S")
        mark.setObjectName("heroMark")
        mark.setAlignment(QtCore.Qt.AlignCenter)
        mark.setFixedSize(48, 48)
        hero_layout.addWidget(mark)

        title_column = QtWidgets.QVBoxLayout()
        title_column.setSpacing(1)
        title = QtWidgets.QLabel("智能垃圾分拣控制台")
        title.setObjectName("titleLabel")
        subtitle = QtWidgets.QLabel("SMART SORT CONTROL CENTER  /  YOLO · PROTOCOL · STATE MACHINE")
        subtitle.setObjectName("heroSubtitle")
        self.hero_subtitle = subtitle
        title_column.addWidget(title)
        title_column.addWidget(subtitle)
        hero_layout.addLayout(title_column)
        hero_layout.addStretch(1)

        self.state_label = self._make_status_label("● 等待启动")
        self.state_label.setMinimumWidth(116)
        hero_layout.addWidget(self.state_label)

        self.connection_label = self._make_status_label("○ 未连接")
        self.connection_label.setMinimumWidth(86)
        hero_layout.addWidget(self.connection_label)

        self.fps_label = QtWidgets.QLabel("0 FPS")
        self.fps_label.setObjectName("fpsValue")
        self.fps_label.setAlignment(QtCore.Qt.AlignCenter)
        self.fps_label.setMinimumWidth(58)
        hero_layout.addWidget(self.fps_label)

        cursor_row = QtWidgets.QHBoxLayout()
        cursor_row.setSpacing(4)
        cursor_group = QtWidgets.QButtonGroup(self)
        cursor_group.setExclusive(True)
        cursor_patterns = (
            ("↖", "标准箭头"),
            ("＋", "检测十字"),
            ("◎", "圆形准星"),
            ("✦", "星芒指北"),
            ("☝", "手形"),
        )
        self.cursor_buttons = []
        for index, (symbol, tooltip) in enumerate(cursor_patterns):
            button = QtWidgets.QPushButton(symbol)
            button.setProperty("role", "cursor")
            button.setCheckable(True)
            button.setFixedSize(30, 30)
            button.setToolTip(tooltip)
            button.clicked.connect(
                lambda _checked=False, value=index: self._apply_cursor_shape(value)
            )
            cursor_group.addButton(button, index)
            cursor_row.addWidget(button)
            self.cursor_buttons.append(button)
        self.cursor_buttons[0].setChecked(True)
        self.cursor_group = cursor_group
        hero_layout.addLayout(cursor_row)
        root.addWidget(hero)

        splitter = GripSplitter(QtCore.Qt.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(14)
        splitter.setMinimumHeight(180)
        splitter.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Ignored,
        )

        vision_card, vision_layout = self._new_card(0)
        vision_heading = QtWidgets.QHBoxLayout()
        vision_title_column = QtWidgets.QVBoxLayout()
        vision_title_column.setSpacing(1)
        vision_kicker = QtWidgets.QLabel("LIVE VISION")
        vision_kicker.setObjectName("sectionKicker")
        vision_title = QtWidgets.QLabel("目标检测画面")
        vision_title.setObjectName("sectionTitle")
        vision_title_column.addWidget(vision_kicker)
        vision_title_column.addWidget(vision_title)
        vision_heading.addLayout(vision_title_column)
        vision_heading.addStretch(1)
        live_badge = QtWidgets.QLabel("● YOLO FEED")
        live_badge.setObjectName("liveBadge")
        live_badge.setAlignment(QtCore.Qt.AlignCenter)
        live_badge.setMinimumSize(106, 28)
        vision_heading.addWidget(live_badge)
        vision_layout.addLayout(vision_heading)

        self.camera_label = QtWidgets.QLabel("等待视频源\n导入视频或打开摄像头，然后开始识别")
        self.camera_label.setObjectName("videoViewport")
        self.camera_label.setAlignment(QtCore.Qt.AlignCenter)
        self.camera_label.setMinimumSize(560, 240)
        self.camera_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        vision_layout.addWidget(self.camera_label, 1)

        progress_row = QtWidgets.QHBoxLayout()
        progress_row.setSpacing(10)
        self.video_progress = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.video_progress.setObjectName("videoProgress")
        self.video_progress.setRange(0, 0)
        self.video_progress.setSingleStep(1)
        self.video_progress.setPageStep(30)
        self.video_progress.setTracking(True)
        self.video_progress.setToolTip("拖动以跳转视频位置")
        self.video_progress_label = QtWidgets.QLabel("已播 --:-- · 剩余 --:--")
        self.video_progress_label.setObjectName("videoProgressLabel")
        self.video_progress_label.setAlignment(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        self.video_progress_label.setMinimumWidth(190)
        progress_row.addWidget(self.video_progress, 1)
        progress_row.addWidget(self.video_progress_label)
        vision_layout.addLayout(progress_row)

        vision_hint = QtWidgets.QLabel("BGR FRAME  →  YOLO  →  STABLE × 3  →  WASTE_RESULT")
        vision_hint.setObjectName("pipelineHint")
        vision_hint.setAlignment(QtCore.Qt.AlignCenter)
        vision_layout.addWidget(vision_hint)
        splitter.addWidget(vision_card)

        side = QtWidgets.QWidget()
        side.setObjectName("sidePanel")
        side.setMinimumWidth(370)
        side_layout = QtWidgets.QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)

        source_card, source_layout = self._new_card(88)
        self._add_section_heading(source_layout, "输入与模型", "SOURCE / MODEL")
        source_grid = QtWidgets.QGridLayout()
        source_grid.setHorizontalSpacing(14)
        source_grid.setVerticalSpacing(6)
        source_grid.addWidget(QtWidgets.QLabel("当前输入"), 0, 0)
        self.source_label = QtWidgets.QLabel("正在打开默认考核视频…")
        self.source_label.setObjectName("ellipsisValue")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        source_grid.addWidget(self.source_label, 0, 1)
        source_grid.addWidget(QtWidgets.QLabel("YOLO 模型"), 1, 0)
        self.model_label = QtWidgets.QLabel("等待加载…")
        self.model_label.setObjectName("ellipsisValue")
        self.model_label.setWordWrap(True)
        source_grid.addWidget(self.model_label, 1, 1)
        source_grid.setColumnStretch(1, 1)
        source_layout.addLayout(source_grid)
        side_layout.addWidget(source_card)

        result_card, result_layout = self._new_card(138)
        self._add_section_heading(result_layout, "当前识别", "DETECTION")
        result_grid = QtWidgets.QGridLayout()
        result_grid.setHorizontalSpacing(16)
        result_grid.setVerticalSpacing(4)
        class_caption = QtWidgets.QLabel("模型类别")
        class_caption.setObjectName("fieldCaption")
        self.class_label = QtWidgets.QLabel("—")
        self.class_label.setObjectName("classValue")
        waste_caption = QtWidgets.QLabel("垃圾分类")
        waste_caption.setObjectName("fieldCaption")
        self.waste_label = QtWidgets.QLabel("—")
        self.waste_label.setObjectName("wasteValue")
        confidence_caption = QtWidgets.QLabel("置信度")
        confidence_caption.setObjectName("fieldCaption")
        self.confidence_label = QtWidgets.QLabel("—")
        self.confidence_label.setObjectName("confidenceValue")
        result_grid.addWidget(class_caption, 0, 0)
        result_grid.addWidget(self.class_label, 0, 1)
        result_grid.addWidget(waste_caption, 1, 0)
        result_grid.addWidget(self.waste_label, 1, 1)
        result_grid.addWidget(confidence_caption, 2, 0)
        result_grid.addWidget(self.confidence_label, 2, 1)
        result_grid.setColumnStretch(1, 1)
        result_layout.addLayout(result_grid)
        self.confidence_bar = QtWidgets.QProgressBar()
        self.confidence_bar.setObjectName("confidenceBar")
        self.confidence_bar.setRange(0, 100)
        self.confidence_bar.setValue(0)
        self.confidence_bar.setTextVisible(False)
        self.confidence_bar.setFixedHeight(6)
        result_layout.addWidget(self.confidence_bar)
        side_layout.addWidget(result_card)

        stats_card, stats_layout = self._new_card(190)
        self._add_section_heading(stats_layout, "分类统计", "CONFIRMED COUNT")
        stats_grid = QtWidgets.QGridLayout()
        stats_grid.setHorizontalSpacing(12)
        stats_grid.setVerticalSpacing(7)
        for column, heading in enumerate(("类别", "数量", "桶状态")):
            label = QtWidgets.QLabel(heading)
            label.setObjectName("tableHeading")
            label.setAlignment(
                QtCore.Qt.AlignLeft if column == 0 else QtCore.Qt.AlignCenter
            )
            stats_grid.addWidget(label, 0, column)
        for row, waste_type in enumerate(
            (
                WasteType.RECYCLABLE,
                WasteType.KITCHEN,
                WasteType.HAZARDOUS,
                WasteType.OTHER,
            ),
            start=1,
        ):
            name = WASTE_NAMES[waste_type]
            name_label = QtWidgets.QLabel("●  " + name)
            name_label.setProperty("waste", self.WASTE_TONES[waste_type])
            count = QtWidgets.QLabel("0")
            count.setProperty("role", "count")
            count.setProperty("active", False)
            count.setAlignment(QtCore.Qt.AlignCenter)
            status = QtWidgets.QLabel("● 正常")
            status.setProperty("role", "bin")
            status.setProperty("binState", "normal")
            status.setAlignment(QtCore.Qt.AlignCenter)
            self.count_labels[name] = count
            self.bin_labels[name] = status
            stats_grid.addWidget(name_label, row, 0)
            stats_grid.addWidget(count, row, 1)
            stats_grid.addWidget(status, row, 2)
        stats_grid.setColumnStretch(0, 1)
        stats_layout.addLayout(stats_grid)
        side_layout.addWidget(stats_card)

        log_card, log_layout = self._new_card(92)
        log_header = QtWidgets.QHBoxLayout()
        log_title = QtWidgets.QLabel("通信流")
        log_title.setObjectName("sectionTitle")
        log_tag = QtWidgets.QLabel("TX / RX")
        log_tag.setObjectName("tinyTag")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(log_tag)
        log_layout.addLayout(log_header)
        self.log_browser = QtWidgets.QPlainTextEdit()
        self.log_browser.setReadOnly(True)
        self.log_browser.setMaximumBlockCount(500)
        self.log_browser.setMinimumHeight(72)
        log_layout.addWidget(self.log_browser, 1)
        side_layout.addWidget(log_card, 1)

        side_scroll = QtWidgets.QScrollArea()
        side_scroll.setObjectName("sideScroll")
        side_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        side_scroll.setMinimumWidth(380)
        side_scroll.setMinimumHeight(0)
        side.setMinimumHeight(0)
        side_scroll.setWidget(side)
        splitter.addWidget(side_scroll)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([900, 450])
        self.main_splitter = splitter
        root.addWidget(splitter, 1)

        control_card = QtWidgets.QFrame()
        control_card.setObjectName("controlCard")
        control_card.setFixedHeight(68)
        control_card.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        controls = QtWidgets.QHBoxLayout(control_card)
        controls.setContentsMargins(18, 12, 18, 12)
        controls.setSpacing(10)
        action_title = QtWidgets.QVBoxLayout()
        action_title.setSpacing(0)
        action_kicker = QtWidgets.QLabel("QUICK ACTIONS")
        action_kicker.setObjectName("sectionKicker")
        action_hint = QtWidgets.QLabel("输入源、识别与统计控制")
        action_hint.setObjectName("controlHint")
        action_title.addWidget(action_kicker)
        action_title.addWidget(action_hint)
        controls.addLayout(action_title)
        controls.addStretch(1)

        self.video_button = GlowButton("◉  导入视频", "secondary")
        self.dep_video_button = GlowButton("▶  播放 dep.mp4", "secondary")
        self.camera_button = GlowButton("◎  打开摄像头", "secondary")
        self.start_button = GlowButton("▶  开始识别", "primary")
        self.reset_button = GlowButton("↺  重置统计", "quiet")
        self.quit_button = GlowButton("×  退出", "danger")
        self.video_button.setToolTip("选择本地视频文件")
        self.dep_video_button.setToolTip("停止当前视频并自动播放 vedio/dep.mp4")
        self.camera_button.setToolTip("选择摄像头设备")
        self.start_button.setToolTip("开始、暂停或重新播放")
        self.reset_button.setToolTip("清空计数与满载状态")
        self.quit_button.setToolTip("安全退出工作线程")
        controls.addWidget(self.video_button)
        controls.addWidget(self.dep_video_button)
        controls.addWidget(self.camera_button)
        controls.addWidget(self.start_button)
        controls.addWidget(self.reset_button)
        controls.addWidget(self.quit_button)
        root.addWidget(control_card)

        self.setStyleSheet(self._style_sheet())
        self._apply_cursor_shape(0)
        self._restore_layout_state()

    def _restore_layout_state(self) -> None:
        settings = QtCore.QSettings("SmartSortAssessment", "SmartWasteUI")
        state = settings.value("layout/main_splitter")
        if state:
            self.main_splitter.restoreState(state)

    def _style_sheet(self) -> str:
        return """
            QWidget {
                font-family: "SimSun";
                color: #173247;
                font-size: 14px;
            }
            QMainWindow, QWidget#root {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #eaf0f7, stop: 0.52 #f5f8fb, stop: 1 #edf4f2
                );
            }
            QWidget#sidePanel, QScrollArea#sideScroll,
            QScrollArea#sideScroll > QWidget > QWidget {
                background: transparent;
                border: none;
            }
            QFrame#heroCard {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #071f35, stop: 0.48 #0b3346, stop: 1 #0d5357
                );
                border: 1px solid #28646c;
                border-radius: 22px;
            }
            QLabel#heroMark {
                background: #35d7c4;
                color: #062a38;
                border-radius: 24px;
                font-size: 24px;
                font-weight: 900;
            }
            QLabel#titleLabel {
                color: #ffffff;
                font-size: 27px;
                font-weight: 700;
            }
            QLabel#heroSubtitle {
                color: #a8c9da;
                font-size: 10px;
            }
            QLabel#statusCaption {
                color: #9fc2d3;
                font-size: 11px;
                margin-left: 5px;
            }
            QLabel[role="status"] {
                border-radius: 14px;
                padding: 5px 11px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[role="status"][tone="info"] {
                color: #d9ece5;
                background: rgba(255, 255, 255, 16);
                border: 1px solid rgba(255, 255, 255, 35);
            }
            QLabel[role="status"][tone="active"] {
                color: #7ff4d8;
                background: rgba(54, 224, 185, 24);
                border: 1px solid #2c9e87;
            }
            QLabel[role="status"][tone="success"] {
                color: #8ff5d4;
                background: rgba(54, 224, 185, 30);
                border: 1px solid #45c6a4;
            }
            QLabel[role="status"][tone="warning"] {
                color: #ffd997;
                background: rgba(241, 174, 70, 25);
                border: 1px solid #d7a045;
            }
            QLabel[role="status"][tone="danger"] {
                color: #ffb1b1;
                background: rgba(237, 91, 91, 24);
                border: 1px solid #c66060;
            }
            QLabel#fpsValue {
                color: #f5c46f;
                background: rgba(255, 255, 255, 10);
                border-radius: 8px;
                padding: 5px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton[role="cursor"] {
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
                color: #b9d7e4;
                background: rgba(255, 255, 255, 10);
                border: 1px solid rgba(167, 211, 226, 75);
                border-radius: 9px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton[role="cursor"]:hover {
                color: #ffffff;
                background: rgba(69, 187, 232, 38);
                border-color: #42cae7;
            }
            QPushButton[role="cursor"]:checked {
                color: #072c3b;
                background: #42dfc8;
                border: 1px solid #9bf7e8;
            }
            QFrame[card="true"], QFrame#controlCard {
                background: rgba(255, 255, 255, 248);
                border: 1px solid #d8e4ec;
                border-radius: 19px;
            }
            QFrame#controlCard {
                background: rgba(251, 253, 255, 252);
            }
            QLabel#sectionKicker {
                color: #148fae;
                font-size: 9px;
                font-weight: 700;
            }
            QLabel#sectionTitle {
                color: #173247;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#controlHint {
                color: #6c8294;
                font-size: 11px;
            }
            QLabel#liveBadge {
                color: #49e3c8;
                background: #0a2939;
                border: 1px solid #2e6372;
                border-radius: 14px;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#videoViewport {
                color: #b5ccd8;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #081923, stop: 1 #14303e
                );
                border: 1px solid #2c5868;
                border-radius: 16px;
                font-size: 16px;
            }
            QLabel#pipelineHint {
                color: #7c929f;
                background: #eef5f9;
                border: 1px solid #dce8ef;
                border-radius: 10px;
                padding: 7px;
                font-size: 10px;
            }
            QSlider#videoProgress {
                min-height: 22px;
            }
            QSlider#videoProgress::groove:horizontal {
                height: 6px;
                background: #dce8ee;
                border-radius: 3px;
            }
            QSlider#videoProgress::sub-page:horizontal {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #20b9bd, stop: 1 #38d2ba
                );
                border-radius: 3px;
            }
            QSlider#videoProgress::add-page:horizontal {
                background: #dce8ee;
                border-radius: 3px;
            }
            QSlider#videoProgress::handle:horizontal {
                width: 16px;
                margin: -5px 0;
                background: #ffffff;
                border: 2px solid #28bdb7;
                border-radius: 8px;
            }
            QSlider#videoProgress::handle:horizontal:hover {
                background: #eafffb;
                border-color: #149f9a;
            }
            QSlider#videoProgress:disabled::groove:horizontal,
            QSlider#videoProgress:disabled::add-page:horizontal {
                background: #e8eef2;
            }
            QLabel#videoProgressLabel {
                color: #547182;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#fieldCaption, QLabel#tableHeading {
                color: #7d919e;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#ellipsisValue {
                color: #2e536b;
                font-size: 11px;
            }
            QLabel#classValue {
                color: #173247;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#wasteValue {
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#wasteValue[waste="recyclable"] { color: #2478a7; }
            QLabel#wasteValue[waste="kitchen"] { color: #3b9b5d; }
            QLabel#wasteValue[waste="hazardous"] { color: #c64040; }
            QLabel#wasteValue[waste="other"] { color: #687873; }
            QLabel#confidenceValue {
                color: #c5902e;
                font-size: 15px;
                font-weight: 700;
            }
            QProgressBar#confidenceBar {
                background: #edf1ef;
                border: none;
                border-radius: 3px;
            }
            QProgressBar#confidenceBar::chunk {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #38c6ab, stop: 1 #f1ba58
                );
                border-radius: 3px;
            }
            QLabel[waste="recyclable"] { color: #2478a7; }
            QLabel[waste="kitchen"] { color: #3b9b5d; }
            QLabel[waste="hazardous"] { color: #c64040; }
            QLabel[waste="other"] { color: #687873; }
            QLabel[role="count"] {
                color: #82938e;
                background: #f1f5f3;
                border-radius: 9px;
                padding: 3px 10px;
                font-weight: 700;
            }
            QLabel[role="count"][active="true"] {
                color: #0b7260;
                background: #dcf6ee;
            }
            QLabel[role="bin"] {
                border-radius: 9px;
                padding: 3px 8px;
                font-size: 9px;
                font-weight: 700;
            }
            QLabel[role="bin"][binState="normal"] {
                color: #267a4a;
                background: #e5f5eb;
            }
            QLabel[role="bin"][binState="full"] {
                color: #b63c3c;
                background: #fde8e8;
            }
            QLabel#tinyTag {
                color: #148dac;
                background: #e5f4fa;
                border-radius: 8px;
                padding: 2px 8px;
                font-size: 9px;
                font-weight: 700;
            }
            QPlainTextEdit {
                color: #b4e2ef;
                background: #081d2a;
                border: 1px solid #23485b;
                border-radius: 11px;
                padding: 7px;
                selection-background-color: #1e8092;
                font-family: "SimSun";
                font-size: 10px;
            }
            QPushButton {
                min-width: 112px;
                padding: 0 16px;
                border-radius: 13px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton[role="primary"] {
                color: #ffffff;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #17b9b7, stop: 1 #176d9a
                );
                border: 1px solid #39d3d2;
            }
            QPushButton[role="primary"]:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #25d2ca, stop: 1 #2386b7
                );
                border-color: #a5f3df;
            }
            QPushButton[role="primary"]:pressed {
                background: #125d80;
                border: 2px solid #7ef5ec;
            }
            QPushButton[role="primary"][running="true"] {
                color: #442d05;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #f4c66e, stop: 1 #db9a33
                );
                border: 1px solid #f9d995;
            }
            QPushButton[role="secondary"] {
                color: #225878;
                background: #eaf3fb;
                border: 1px solid #c9dff0;
            }
            QPushButton[role="secondary"]:hover {
                color: #164666;
                background: #dcecfb;
                border-color: #80b9e8;
            }
            QPushButton[role="secondary"]:pressed {
                background: #c7e2f5;
                border: 2px solid #72b8ee;
            }
            QPushButton[role="quiet"] {
                color: #617687;
                background: #f0f5f9;
                border: 1px solid #d9e4eb;
            }
            QPushButton[role="quiet"]:hover {
                color: #263e38;
                background: #e4ece9;
            }
            QPushButton[role="danger"] {
                color: #a13f3f;
                background: #fff3f2;
                border: 1px solid #f1cccc;
            }
            QPushButton[role="danger"]:hover {
                color: #842a2a;
                background: #ffe1df;
                border-color: #eb9d9d;
            }
            QPushButton:focus {
                border: 2px solid #43cfb0;
            }
            QSplitter::handle {
                background: transparent;
            }
            QToolTip {
                color: #eaf7f2;
                background: #173c35;
                border: 1px solid #397267;
                padding: 5px;
            }
            QScrollBar:vertical {
                width: 9px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                min-height: 24px;
                background: #bfd5cc;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """

    def _file_dialog_style(self) -> str:
        return """
            QFileDialog {
                background: #eef4f9;
            }
            QFileDialog QWidget {
                font-family: "SimSun";
                font-size: 14px;
                color: #173247;
            }
            QFileDialog QLabel {
                color: #315267;
                font-size: 13px;
            }
            QFileDialog QLineEdit, QFileDialog QComboBox {
                min-height: 34px;
                padding: 0 10px;
                color: #173247;
                background: #ffffff;
                border: 1px solid #c8dbe8;
                border-radius: 9px;
            }
            QFileDialog QLineEdit:focus, QFileDialog QComboBox:focus {
                border: 2px solid #31b8cf;
            }
            QFileDialog QComboBox::drop-down {
                width: 25px;
                border: none;
            }
            QFileDialog QAbstractItemView {
                color: #173247;
                background: #ffffff;
                border: 1px solid #d2e1ea;
                selection-background-color: #dcf3f7;
                selection-color: #16445b;
                alternate-background-color: #f7fafc;
                outline: none;
            }
            QFileDialog QTreeView, QFileDialog QListView {
                background: #ffffff;
                border: 1px solid #d1e0e9;
                border-radius: 10px;
                padding: 4px;
            }
            QFileDialog QHeaderView::section {
                color: #6c8292;
                background: #edf4f8;
                border: none;
                border-bottom: 1px solid #d1e0e9;
                padding: 7px;
                font-size: 11px;
            }
            QFileDialog QToolButton {
                min-width: 32px;
                min-height: 30px;
                color: #28546b;
                background: #e8f1f8;
                border: 1px solid #cbdeea;
                border-radius: 8px;
                padding: 3px;
            }
            QFileDialog QToolButton:hover {
                background: #dcecf8;
                border-color: #76bcdd;
            }
            QFileDialog QPushButton {
                min-width: 88px;
                min-height: 36px;
                padding: 0 15px;
                color: #20536b;
                background: #e8f2f9;
                border: 1px solid #c4dce9;
                border-radius: 10px;
                font-weight: 700;
            }
            QFileDialog QPushButton:hover {
                color: #123f56;
                background: #d8ebf6;
                border-color: #75bfd9;
            }
            QFileDialog QPushButton:default {
                color: #ffffff;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #21b9bc, stop: 1 #176f9e
                );
                border: 1px solid #39d6d2;
            }
            QFileDialog QPushButton[dialogPrimary="true"] {
                color: #ffffff;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #21b9bc, stop: 1 #176f9e
                );
                border: 1px solid #39d6d2;
            }
            QFileDialog QSplitter::handle {
                background: #d7e4ec;
                border-radius: 3px;
            }
        """

    def _create_video_dialog(self) -> QtWidgets.QFileDialog:
        settings = QtCore.QSettings("SmartSortAssessment", "SmartWasteUI")
        last_directory = settings.value("video/last_directory", "")
        if not last_directory or not Path(last_directory).is_dir():
            assets_directory = Path.cwd() / "assets"
            last_directory = str(
                assets_directory if assets_directory.is_dir() else Path.home()
            )

        dialog = QtWidgets.QFileDialog(
            self,
            "导入待识别视频",
            last_directory,
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*.*)",
        )
        dialog.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        dialog.setViewMode(QtWidgets.QFileDialog.Detail)
        dialog.setNameFilter("视频文件 (*.mp4 *.avi *.mov *.mkv)")
        dialog.setLabelText(QtWidgets.QFileDialog.LookIn, "当前位置")
        dialog.setLabelText(QtWidgets.QFileDialog.FileName, "视频文件")
        dialog.setLabelText(QtWidgets.QFileDialog.FileType, "文件类型")
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setProxyModel(ChineseFileDialogProxy(dialog))

        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            dialog_width = min(1040, max(840, available.width() - 180))
            dialog_height = min(720, max(540, available.height() - 180))
            dialog.resize(dialog_width, dialog_height)
        else:
            dialog.resize(980, 640)

        dialog.setStyleSheet(self._style_sheet() + self._file_dialog_style())
        QtCore.QTimer.singleShot(
            0,
            lambda current=dialog: self._polish_video_dialog(current),
        )
        return dialog

    def _select_video_file(self) -> str:
        dialog = self._create_video_dialog()
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return ""

        selected_files = dialog.selectedFiles()
        if not selected_files:
            return ""
        settings = QtCore.QSettings("SmartSortAssessment", "SmartWasteUI")
        settings.setValue(
            "video/last_directory",
            str(Path(selected_files[0]).parent),
        )
        return selected_files[0]

    @staticmethod
    def _polish_video_dialog(dialog: QtWidgets.QFileDialog) -> None:
        button_text = {
            "open": "导入视频",
            "&open": "导入视频",
            "打开": "导入视频",
            "打开(&o)": "导入视频",
            "cancel": "取消",
            "&cancel": "取消",
            "取消": "取消",
        }
        for button in dialog.findChildren(QtWidgets.QPushButton):
            normalized = button.text().strip().lower()
            replacement = button_text.get(normalized)
            if replacement:
                button.setText(replacement)
                if replacement == "导入视频":
                    button.setProperty("dialogPrimary", True)
                    button.setDefault(True)
                    button.style().unpolish(button)
                    button.style().polish(button)

        tree = dialog.findChild(QtWidgets.QTreeView)
        if tree is not None and tree.model() is not None:
            for column, label in enumerate(("名称", "大小", "类型", "修改日期")):
                tree.model().setHeaderData(
                    column,
                    QtCore.Qt.Horizontal,
                    label,
                )

        splitters = dialog.findChildren(QtWidgets.QSplitter)
        if splitters:
            dialog_splitter = splitters[0]
            total_width = max(760, dialog.width() - 40)
            dialog_splitter.setSizes([180, total_width - 180])

    def _custom_cursor(self, shape: str) -> QtGui.QCursor:
        if shape == "standard":
            return QtGui.QCursor(QtCore.Qt.ArrowCursor)
        if shape == "hand":
            return QtGui.QCursor(QtCore.Qt.PointingHandCursor)

        pixmap = QtGui.QPixmap(36, 36)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor("#1fc7a5"), 2.2)
        painter.setPen(pen)

        if shape == "crosshair":
            painter.drawEllipse(QtCore.QPointF(18, 18), 7, 7)
            painter.drawLine(18, 2, 18, 11)
            painter.drawLine(18, 25, 18, 34)
            painter.drawLine(2, 18, 11, 18)
            painter.drawLine(25, 18, 34, 18)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.2))
            painter.drawPoint(18, 18)
        elif shape == "ring":
            glow_pen = QtGui.QPen(QtGui.QColor(31, 199, 165, 55), 7)
            painter.setPen(glow_pen)
            painter.drawEllipse(QtCore.QPointF(18, 18), 8, 8)
            painter.setPen(QtGui.QPen(QtGui.QColor("#20c9a7"), 2.2))
            painter.drawEllipse(QtCore.QPointF(18, 18), 7, 7)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.2))
            painter.drawEllipse(QtCore.QPointF(18, 18), 2, 2)
        else:
            center = QtCore.QPointF(18, 18)
            painter.setPen(QtGui.QPen(QtGui.QColor(69, 177, 255, 75), 6))
            painter.drawEllipse(center, 8, 8)
            painter.setPen(QtGui.QPen(QtGui.QColor("#4eb7ff"), 2.2))
            painter.drawLine(18, 3, 18, 33)
            painter.drawLine(3, 18, 33, 18)
            painter.drawLine(8, 8, 28, 28)
            painter.drawLine(28, 8, 8, 28)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1.2))
            painter.drawEllipse(center, 2.5, 2.5)

        painter.end()
        return QtGui.QCursor(pixmap, 18, 18)

    @QtCore.pyqtSlot(int)
    def _apply_cursor_shape(self, index: int) -> None:
        shapes = ("standard", "crosshair", "ring", "star", "hand")
        cursor = self._custom_cursor(shapes[max(0, min(index, 4))])
        central = self.centralWidget()
        if central is None:
            return
        central.setCursor(cursor)
        for widget in central.findChildren(QtWidgets.QWidget):
            if isinstance(widget, GripSplitterHandle):
                widget.setCursor(QtCore.Qt.SplitHCursor)
            else:
                widget.setCursor(cursor)

    @QtCore.pyqtSlot()
    def _initialize_worker(self) -> None:
        try:
            self.worker.prepare_model()
        except Exception as exc:
            self.model_label.setText("加载失败")
            self.update_state("运行异常")
            self.append_log("YOLO 模型加载失败：{}".format(exc))
            self.start_button.setEnabled(False)
            return
        self.worker.start()
        if self._auto_start:
            QtCore.QTimer.singleShot(500, self.toggle_processing)

    def _connect_signals(self) -> None:
        self.video_button.clicked.connect(self.import_video)
        self.dep_video_button.clicked.connect(self.play_dep_video)
        self.camera_button.clicked.connect(self.open_camera)
        self.start_button.clicked.connect(self.toggle_processing)
        self.reset_button.clicked.connect(self.reset_statistics)
        self.quit_button.clicked.connect(self.close)
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.state_changed.connect(self.update_state)
        self.worker.counts_changed.connect(self.update_counts)
        self.worker.bins_changed.connect(self.update_bins)
        self.worker.detection_changed.connect(self.update_detection)
        self.worker.model_changed.connect(self.model_label.setText)
        self.worker.source_changed.connect(self.on_source_changed)
        self.worker.source_failed.connect(self.on_source_failed)
        self.worker.source_finished.connect(self.on_source_finished)
        self.worker.video_rate_changed.connect(self.update_video_rate)
        self.worker.progress_changed.connect(self.update_video_progress)
        self.worker.log_message.connect(self.append_log)
        self.worker.connection_changed.connect(self.update_connection)
        self.worker.fps_changed.connect(
            lambda value: self.fps_label.setText("{:.0f} FPS".format(value))
        )
        self.video_progress.sliderMoved.connect(self.preview_video_progress)
        self.video_progress.sliderReleased.connect(self.seek_video)

    @QtCore.pyqtSlot()
    def import_video(self) -> None:
        file_path = self._select_video_file()
        if not file_path:
            return
        self.worker.request_source("video", file_path)
        self.append_log("已选择视频源：{}".format(file_path))

    @QtCore.pyqtSlot()
    def play_dep_video(self) -> None:
        if not DEP_VIDEO_PATH.is_file():
            self.update_state("视频不存在")
            self.append_log("找不到指定视频：{}".format(DEP_VIDEO_PATH))
            return

        # The worker releases the active source before opening the replacement.
        self.worker.request_source("video", str(DEP_VIDEO_PATH))
        self._set_processing(True)
        self.update_state("正在切换视频")
        self.append_log("正在停止当前视频并播放：{}".format(DEP_VIDEO_PATH))

    def _format_video_progress(self, current: int, total: int) -> str:
        if self._video_fps <= 0 or total <= 0:
            return "已播 --:-- · 剩余 --:--"
        elapsed_seconds = min(max(0, current), total) / self._video_fps
        remaining_seconds = max(0, total - current) / self._video_fps
        return "已播 {} · 剩余 {}".format(
            self._format_clock(elapsed_seconds),
            self._format_clock(remaining_seconds),
        )

    @staticmethod
    def _format_clock(seconds: float) -> str:
        whole_seconds = max(0, int(seconds))
        minutes, second = divmod(whole_seconds, 60)
        hours, minute = divmod(minutes, 60)
        if hours:
            return "{:02d}:{:02d}:{:02d}".format(hours, minute, second)
        return "{:02d}:{:02d}".format(minute, second)

    @QtCore.pyqtSlot(int)
    def preview_video_progress(self, frame_index: int) -> None:
        total = self.video_progress.maximum() + 1
        self.video_progress_label.setText(
            self._format_video_progress(frame_index, total)
        )

    @QtCore.pyqtSlot()
    def seek_video(self) -> None:
        if self.video_progress.maximum() <= 0:
            return
        frame_index = self.video_progress.value()
        self.worker.request_seek(frame_index)
        total = self.video_progress.maximum() + 1
        self.video_progress_label.setText(
            self._format_video_progress(frame_index, total)
        )
        self.update_state("正在跳转视频")
        self.append_log("视频跳转到第 {} 帧".format(frame_index))

    @QtCore.pyqtSlot(float)
    def update_video_rate(self, frames_per_second: float) -> None:
        self._video_fps = max(0.0, float(frames_per_second))
        if self._last_video_total > 0 and not self.video_progress.isSliderDown():
            self.video_progress_label.setText(
                self._format_video_progress(
                    self._last_video_frame,
                    self._last_video_total,
                )
            )

    @QtCore.pyqtSlot(int, int)
    def update_video_progress(self, current: int, total: int) -> None:
        self._last_video_frame = current
        self._last_video_total = total
        if total > 0:
            self.video_progress.setRange(0, max(0, total - 1))
            self.video_progress.setEnabled(True)
            if not self.video_progress.isSliderDown():
                self.video_progress.setValue(
                    max(0, min(current, self.video_progress.maximum()))
                )
        else:
            self.video_progress.setRange(0, 0)
            self.video_progress.setEnabled(False)
        if not self.video_progress.isSliderDown():
            self.video_progress_label.setText(
                self._format_video_progress(current, total)
            )

    @QtCore.pyqtSlot()
    def open_camera(self) -> None:
        settings = QtCore.QSettings("SmartSortAssessment", "SmartWasteUI")
        try:
            last_index = int(settings.value("camera/last_index", 0))
        except (TypeError, ValueError):
            last_index = 0

        camera_index, accepted = QtWidgets.QInputDialog.getInt(
            self,
            "选择摄像头",
            "摄像头设备编号：",
            max(0, min(last_index, 9)),
            0,
            9,
            1,
        )
        if not accepted:
            return

        settings.setValue("camera/last_index", camera_index)
        self._camera_request = {
            "index": camera_index,
            "was_processing": self._processing,
            "previous_source": self.source_label.text(),
        }
        self.video_button.setEnabled(False)
        self.dep_video_button.setEnabled(False)
        self.camera_button.setEnabled(False)
        self.camera_button.setText("◎  正在打开…")

        self.camera_label.clear()
        self.camera_label.setText(
            "正在打开摄像头 {}\n请稍候，首次连接可能需要几秒…".format(camera_index)
        )
        self.update_detection(None)
        self.video_progress.setRange(0, 0)
        self.video_progress.setEnabled(False)
        self.video_progress_label.setText("实时摄像头 · 无时间轴")
        self.source_label.setText("正在打开摄像头 {}…".format(camera_index))
        self.update_state("正在打开摄像头")
        self._set_processing(True)

        self.append_log(
            "正在打开摄像头 {}；连接成功后自动开始实时识别".format(camera_index)
        )
        self.worker.request_source("camera", str(camera_index))

    def _finish_camera_request(self):
        request, self._camera_request = self._camera_request, None
        self.video_button.setEnabled(True)
        self.dep_video_button.setEnabled(True)
        self.camera_button.setEnabled(True)
        self.camera_button.setText("◎  打开摄像头")
        return request

    @QtCore.pyqtSlot(str)
    def on_source_changed(self, display_name: str) -> None:
        self.source_label.setText(display_name)
        self.start_button.setEnabled(True)
        if not display_name.startswith("摄像头"):
            self.camera_button.setText("◎  打开摄像头")
            return

        self.video_progress.setRange(0, 0)
        self.video_progress.setEnabled(False)
        self.video_progress_label.setText("实时摄像头 · 无时间轴")
        request = self._finish_camera_request()
        self.camera_button.setText("◎  切换摄像头")
        if request is None:
            return

        self._set_processing(True)
        self.camera_label.setText("摄像头已打开\n正在获取实时画面…")
        self.update_state("摄像头已打开")
        settings = QtCore.QSettings("SmartSortAssessment", "SmartWasteUI")
        settings.setValue("camera/last_index", request["index"])
        self.append_log(
            "摄像头 {} 已成功打开，实时识别已自动启动".format(request["index"])
        )

    @QtCore.pyqtSlot(str, str, bool)
    def on_source_failed(
        self,
        source_kind: str,
        message: str,
        fallback_available: bool,
    ) -> None:
        request = self._finish_camera_request()
        if source_kind != "camera":
            self.update_state("视频源打开失败")
            return

        if fallback_available and request is not None:
            self.source_label.setText(request["previous_source"])
            if request["was_processing"]:
                self._set_processing(True)
            else:
                self._set_processing(False)
            self.camera_label.clear()
            self.camera_label.setText(
                "摄像头打开失败\n当前图像源保持不变"
            )
        else:
            self._set_processing(False)
            self.start_button.setEnabled(False)
            self.source_label.setText("摄像头未打开")
            self.camera_label.clear()
            if IS_DOCKER:
                self.camera_label.setText(
                    "Docker 容器无法直接访问 Windows 本地摄像头\n"
                    "请在本机 Python 环境运行 app.main 后再打开摄像头"
                )
            else:
                self.camera_label.setText(
                    "摄像头不可用\n"
                    "请检查设备编号、连接状态或是否被其他程序占用"
                )
            self.update_detection(None)
            self.video_progress.setRange(0, 0)
            self.video_progress.setEnabled(False)
            self.video_progress_label.setText("实时摄像头 · 无时间轴")

        self.update_state("摄像头打开失败" if fallback_available else "摄像头不可用")

    @QtCore.pyqtSlot()
    def reset_statistics(self) -> None:
        self.worker.request_reset()
        zero_counts = {name: 0 for name in self.count_labels}
        normal_bins = {name: False for name in self.bin_labels}
        self.update_counts(zero_counts)
        self.update_bins(normal_bins)
        self.append_log("已请求重置统计与桶状态")

    def _set_processing(self, enabled: bool) -> None:
        self._processing = enabled
        self.start_button.setProperty("running", enabled)
        self._refresh_style(self.start_button)
        if enabled:
            self.worker.start_processing()
            self.start_button.setText("Ⅱ  暂停识别")
        else:
            self.worker.pause_processing()
            self.start_button.setText("▶  开始识别")

    @QtCore.pyqtSlot()
    def toggle_processing(self) -> None:
        self._set_processing(not self._processing)
        self.append_log(
            "视觉识别已{}".format("启动" if self._processing else "暂停")
        )

    @QtCore.pyqtSlot()
    def on_source_finished(self) -> None:
        self._processing = False
        self.start_button.setProperty("running", False)
        self._refresh_style(self.start_button)
        self.start_button.setText("↻  重新播放")

    @QtCore.pyqtSlot(QtGui.QImage)
    def update_frame(self, image: QtGui.QImage) -> None:
        pixmap = QtGui.QPixmap.fromImage(image)
        self.camera_label.setPixmap(
            pixmap.scaled(
                self.camera_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

    @QtCore.pyqtSlot(str)
    def update_state(self, text: str) -> None:
        tone = "info"
        if "异常" in text or "错误" in text or "失败" in text:
            tone = "danger"
        elif "完成" in text or "已连接" in text:
            tone = "success"
        elif "暂停" in text or "等待" in text:
            tone = "warning"
        elif "执行" in text or "识别" in text:
            tone = "active"
        self.state_label.setText("● " + text)
        self.state_label.setProperty("tone", tone)
        self._refresh_style(self.state_label)

    @QtCore.pyqtSlot(object)
    def update_detection(self, detection: Detection) -> None:
        if detection is None:
            self.class_label.setText("—")
            self.waste_label.setText("—")
            self.confidence_label.setText("—")
            self.confidence_bar.setValue(0)
            self.waste_label.setProperty("waste", "other")
            self._refresh_style(self.waste_label)
            return
        self.class_label.setText(detection.class_name)
        self.waste_label.setText(WASTE_NAMES[detection.waste_type])
        self.waste_label.setProperty(
            "waste",
            self.WASTE_TONES[detection.waste_type],
        )
        self.confidence_label.setText("{:.1f}%".format(detection.confidence * 100))
        self.confidence_bar.setValue(int(detection.confidence * 100))
        self._refresh_style(self.waste_label)

    @QtCore.pyqtSlot(object)
    def update_counts(self, values: dict) -> None:
        for name, value in values.items():
            label = self.count_labels.get(name)
            if label is None:
                continue
            label.setText(str(value))
            label.setProperty("active", bool(value))
            self._refresh_style(label)

    @QtCore.pyqtSlot(object)
    def update_bins(self, values: dict) -> None:
        for name, is_full in values.items():
            label = self.bin_labels.get(name)
            if label is None:
                continue
            label.setText("● 满载" if is_full else "● 正常")
            label.setProperty("binState", "full" if is_full else "normal")
            self._refresh_style(label)

    @QtCore.pyqtSlot(str)
    def append_log(self, message: str) -> None:
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_browser.appendPlainText("[{}] {}".format(timestamp, message))

    @QtCore.pyqtSlot(bool)
    def update_connection(self, connected: bool) -> None:
        self.connection_label.setText("● 已连接" if connected else "○ 未连接")
        self.connection_label.setProperty(
            "tone",
            "success" if connected else "danger",
        )
        self._refresh_style(self.connection_label)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.hero_subtitle.setVisible(event.size().width() >= 1240)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        settings = QtCore.QSettings("SmartSortAssessment", "SmartWasteUI")
        settings.setValue("layout/main_splitter", self.main_splitter.saveState())
        self.worker.stop()
        if not self.worker.wait(5000):
            self.append_log("工作线程未在规定时间内退出")
        event.accept()
