"""FragEngine desktop frontend: processing, live monitoring, calibration, templates and settings."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import queue
import shutil
import sys
import threading
import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..core import config as config_manager
from ..core import paths
from ..core import picker as native_picker
from ..core import pipeline
from . import theme as t
from .monitor import RunMonitor
from .queue_view import QueueItem, QueueList
from .widgets import (Card, CropPreview, FadeStack, KeyValueList, NavButton, StatusPill, Toast, Toggle, fmt_count,
                      fmt_time)

ROOT = paths.PROJECT_ROOT
PAGES = (("Process", "process", "Process recordings", "Queue local recordings or YouTube URLs, pick an output folder, then start."),
         ("Monitor", "monitor", "Run monitor", "Live telemetry for the active or most recent run."),
         ("Calibrate", "calibrate", "Calibration", "The kill-feed crop and name templates used for matching."),
         ("Templates", "templates", "Templates", "Enabled templates take part in template matching."),
         ("Settings", "settings", "Settings", "Sampling, downloads, detection, clip padding and encoding."))


class RunCancelled(Exception):
    pass


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(t.SURFACE_3))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 15, 15)
    pen = QPen(QColor(t.ACCENT), 4.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(19, 19, 26, 26))
    for a, b in (((32, 8), (32, 17)), ((32, 47), (32, 56)), ((8, 32), (17, 32)), ((47, 32), (56, 32))):
        p.drawLine(*a, *b)
    p.setBrush(QColor(t.VIOLET))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(28.5, 28.5, 7, 7))
    p.end()
    return QIcon(pixmap)


def field(label: str, widget: QWidget, hint: str | None = None) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(QLabel(label, objectName="muted"))
    layout.addWidget(widget)
    if hint:
        note = QLabel(hint, objectName="faint")
        note.setWordWrap(True)
        layout.addWidget(note)
    return box


def page_scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


def spin(minimum: float, maximum: float, decimals: int = 1, step: float = 1, suffix: str = "") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setKeyboardTracking(False)
    return box


class FragEngineWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FragEngine")
        self.setWindowIcon(app_icon())
        self.resize(1360, 880)
        self.setMinimumSize(1120, 720)
        self.config: dict | None = None
        self.messages: queue.Queue = queue.Queue()
        self.running = False
        self.cancel = threading.Event()
        self.started = 0.0
        self.run_output = Path()
        self.calibration = None
        self.probe_info: dict | None = None
        self.queue: list[QueueItem] = []
        self.queue_uid = 0
        self.batch: list[QueueItem] = []
        self.batch_clips = 0
        self.current: QueueItem | None = None
        self._config_signature = None
        self._settings_snapshot = None
        self._loading_settings = False
        self.ffmpeg_ok = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

        self.build()
        self.reload_config(show_error=False)
        self.pump_timer = QTimer(self, interval=50, timeout=self.pump)
        self.pump_timer.start()
        self.watch_timer = QTimer(self, interval=1000, timeout=self.watch_config)
        self.watch_timer.start()
        self.probe_timer = QTimer(self, singleShot=True, interval=400, timeout=self.start_probe)
        if self.config is None:
            QTimer.singleShot(300, self.offer_calibration)

    # ------------------------------------------------------------------ layout

    def build(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(224)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 16)
        side.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(28, 28))
        brand.addWidget(logo)
        name = QVBoxLayout()
        name.setSpacing(0)
        title = QLabel("FragEngine")
        title.setFont(t.font(12, QFont.Weight.DemiBold))
        name.addWidget(title)
        name.addWidget(QLabel("Kill-feed clip detector", objectName="faint"))
        brand.addLayout(name)
        brand.addStretch(1)
        side.addLayout(brand)
        side.addSpacing(22)
        side.addWidget(QLabel("WORKSPACE", objectName="eyebrow"))
        side.addSpacing(4)
        self.nav: dict[str, NavButton] = {}
        for key, icon, _, _ in PAGES:
            button = NavButton(key, icon)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            side.addWidget(button)
            self.nav[key] = button
        side.addStretch(1)
        side.addWidget(QLabel("ENVIRONMENT", objectName="eyebrow"))
        side.addSpacing(4)
        self.env = KeyValueList()
        side.addWidget(self.env)
        self.env.set("FFmpeg", "available" if self.ffmpeg_ok else "missing")
        self.env.rows["FFmpeg"].setStyleSheet(f"color:{t.GREEN if self.ffmpeg_ok else t.RED};")
        outer.addWidget(sidebar)

        main = QVBoxLayout()
        main.setContentsMargins(30, 22, 22, 0)
        main.setSpacing(18)
        topbar = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(2)
        self.page_title = QLabel(objectName="h1")
        self.page_subtitle = QLabel(objectName="muted")
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_subtitle)
        topbar.addLayout(heading, 1)
        self.elapsed = QLabel("", objectName="mono")
        topbar.addWidget(self.elapsed, 0, Qt.AlignmentFlag.AlignVCenter)
        topbar.addSpacing(10)
        self.pill = StatusPill()
        self.pill.set_state("Idle")
        topbar.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        main.addLayout(topbar)

        self.stack = FadeStack()
        self.pages: dict[str, int] = {}
        builders = {"Process": self.build_process, "Monitor": self.build_monitor, "Calibrate": self.build_calibrate,
                    "Templates": self.build_templates, "Settings": self.build_settings}
        for key, *_ in PAGES:
            self.pages[key] = self.stack.addWidget(builders[key]())
        main.addWidget(self.stack, 1)
        holder = QWidget()
        holder.setLayout(main)
        outer.addWidget(holder, 1)
        self.toast = Toast(root)
        t.pad_spinboxes(root)
        self.show_page("Process")

    def build_process(self) -> QWidget:
        content = QWidget()
        grid = QHBoxLayout(content)
        grid.setContentsMargins(0, 0, 8, 20)
        grid.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)

        source = Card("Add source", "A local recording or a YouTube video / live stream URL. Add as many as you like.")
        row = QHBoxLayout()
        self.source_edit = QLineEdit(placeholderText=r"C:\Videos\match.mp4  or  https://youtube.com/watch?v=…")
        self.source_edit.textChanged.connect(self.source_changed)
        self.source_edit.returnPressed.connect(self.add_source)
        browse = QPushButton("Browse…")
        browse.setToolTip("Pick one or more recordings; they are added to the queue")
        browse.clicked.connect(self.browse_video)
        row.addWidget(self.source_edit, 1)
        row.addWidget(browse)
        source.body().addLayout(row)
        self.source_info = QLabel("", objectName="faint")
        self.source_info.setTextFormat(Qt.TextFormat.RichText)
        source.body().addWidget(self.source_info)
        self.source_info.hide()
        row = QHBoxLayout()
        row.setSpacing(14)
        self.start_edit = QLineEdit("00:00:00")
        self.end_edit = QLineEdit(placeholderText="end of recording / stream")
        for widget in (self.start_edit, self.end_edit):
            widget.textChanged.connect(self.update_run_state)
            widget.returnPressed.connect(self.add_source)
        row.addWidget(field("Start", self.start_edit), 1)
        row.addWidget(field("End", self.end_edit), 1)
        self.add_button = QPushButton("Add to queue")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.add_source)
        row.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignBottom)
        source.body().addLayout(row)
        hint = QLabel("Seconds, MM:SS or HH:MM:SS. The range is saved with each item. For live streams keep start at zero.",
                      objectName="faint")
        hint.setWordWrap(True)
        source.body().addWidget(hint)
        left.addWidget(source)

        queue_card = Card("Queue", "Processed top to bottom, one at a time")
        self.queue_count = QLabel("", objectName="faint")
        queue_card.add_header_widget(self.queue_count)
        for label, handler in (("Clear finished", self.clear_finished), ("Clear all", self.clear_queue)):
            button = QPushButton(label, objectName="ghost")
            button.clicked.connect(handler)
            queue_card.add_header_widget(button)
        self.queue_view = QueueList()
        self.queue_view.removed.connect(self.remove_item)
        self.queue_view.moved.connect(self.move_item)
        self.queue_view.retried.connect(self.retry_item)
        queue_card.body().addWidget(self.queue_view)
        left.addWidget(queue_card)

        output = Card("Output", "With several items, each gets its own subfolder: <folder>/01_<name>/clips")
        row = QHBoxLayout()
        self.output_edit = QLineEdit(placeholderText="Choose a folder for clips and logs")
        self.output_edit.textChanged.connect(self.update_run_state)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_output)
        row.addWidget(self.output_edit, 1)
        row.addWidget(browse)
        output.body().addLayout(row)
        left.addWidget(output)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.run_button = QPushButton("Start queue", objectName="primary")
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_button.clicked.connect(self.run_pipeline)
        self.stop_button = QPushButton("Stop", objectName="danger")
        self.stop_button.clicked.connect(self.stop_pipeline)
        self.stop_button.hide()
        self.ready_hint = QLabel("", objectName="faint")
        actions.addWidget(self.run_button)
        actions.addWidget(self.stop_button)
        actions.addSpacing(6)
        actions.addWidget(self.ready_hint, 1)
        left.addLayout(actions)

        command = Card("FFmpeg extraction command", "What the pipeline runs to sample the kill-feed crop")
        copy_button = QPushButton("Copy", objectName="ghost")
        copy_button.clicked.connect(self.copy_command)
        command.add_header_widget(copy_button)
        self.command = QPlainTextEdit(objectName="log")
        self.command.setReadOnly(True)
        self.command.setFixedHeight(92)
        command.body().addWidget(self.command)
        left.addWidget(command)
        left.addStretch(1)
        grid.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(16)
        summary = Card("Run configuration", "Settings this run will use")
        edit = QPushButton("Edit", objectName="ghost")
        edit.clicked.connect(lambda: self.show_page("Settings"))
        summary.add_header_widget(edit)
        self.summary = KeyValueList()
        summary.body().addWidget(self.summary)
        right.addWidget(summary)
        crop = Card("Kill-feed crop")
        self.process_crop = CropPreview()
        self.process_crop.setMinimumHeight(150)
        crop.body().addWidget(self.process_crop)
        right.addWidget(crop)
        right.addStretch(1)
        grid.addLayout(right, 2)
        return page_scroll(content)

    def build_monitor(self) -> QWidget:
        self.monitor = RunMonitor(self.open_output, self.open_path)
        return self.monitor

    def build_calibrate(self) -> QWidget:
        content = QWidget()
        grid = QHBoxLayout(content)
        grid.setContentsMargins(0, 0, 8, 20)
        grid.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)
        current = Card("Current calibration")
        self.calib_status = StatusPill()
        current.add_header_widget(self.calib_status)
        self.calib_crop = CropPreview()
        self.calib_crop.setMinimumHeight(260)
        current.body().addWidget(self.calib_crop)
        self.calib_info = KeyValueList()
        current.body().addWidget(self.calib_info)
        left.addWidget(current)
        preview = Card("Latest calibration preview", "Most recent overlay saved from the workspace")
        self.calib_image = QLabel("No preview saved yet", objectName="faint")
        self.calib_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.calib_image.setMinimumHeight(140)
        preview.body().addWidget(self.calib_image)
        left.addWidget(preview)
        left.addStretch(1)
        grid.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(16)
        steps = Card("Workflow")
        for number, label, hint in (("1", "Load a screenshot", "Full-size gameplay capture at the recording's resolution"),
                                    ("2", "Select the kill feed", "Drag, or type exact X / Y / W / H values"),
                                    ("3", "Select your name", "Inside the crop; becomes a template"),
                                    ("4", "Save template", "Writes a PNG to assets/templates"),
                                    ("5", "Save configuration", "Backs up and replaces config.json")):
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = QLabel(number)
            badge.setFixedSize(24, 24)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"background:{t.SURFACE_3}; border-radius:12px; color:{t.TEXT_2}; font-weight:600;")
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setSpacing(1)
            column.addWidget(QLabel(label))
            column.addWidget(QLabel(hint, objectName="faint"))
            row.addLayout(column, 1)
            steps.body().addLayout(row)
        open_button = QPushButton("Open calibration workspace", objectName="primary")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.clicked.connect(self.open_calibration)
        steps.body().addSpacing(6)
        steps.body().addWidget(open_button)
        note = QLabel("Changes saved in the workspace are picked up here automatically.", objectName="faint")
        note.setWordWrap(True)
        steps.body().addWidget(note)
        right.addWidget(steps)
        right.addStretch(1)
        grid.addLayout(right, 2)
        return page_scroll(content)

    def build_templates(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 20)
        layout.setSpacing(14)
        bar = QHBoxLayout()
        self.template_count = QLabel("", objectName="muted")
        bar.addWidget(self.template_count, 1)
        for label, handler in (("Enable all", lambda: self.set_all_templates(True)),
                               ("Open folder", lambda: self.open_path(self.templates_dir())),
                               ("Add from calibration", self.open_calibration)):
            button = QPushButton(label, objectName="ghost" if label != "Add from calibration" else "")
            button.clicked.connect(handler)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.template_grid = QGridLayout()
        self.template_grid.setSpacing(14)
        layout.addLayout(self.template_grid)
        layout.addStretch(1)
        self.template_columns = 4
        return page_scroll(content)

    def build_settings(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 20)
        layout.setSpacing(16)
        grid = QGridLayout()
        grid.setSpacing(16)

        decoding = Card("Decoding", "How frames are sampled from the source")
        self.s_hwaccel = QComboBox()
        self.s_hwaccel.addItems(["None", "CUDA", "VAAPI", "VideoToolbox"])
        self.s_fps = QLineEdit()
        self.s_fps_hint = QLabel("", objectName="faint")
        decoding.body().addWidget(field("Hardware acceleration", self.s_hwaccel, "Falls back to software automatically if it fails."))
        decoding.body().addWidget(field("Sampling FPS", self.s_fps))
        decoding.body().addWidget(self.s_fps_hint)
        decoding.body().addStretch(1)
        grid.addWidget(decoding, 0, 0)

        youtube = Card("YouTube downloads", "Segmented ingest through slots X / Y / Z")
        self.s_height = QSpinBox()
        self.s_height.setRange(144, 4320)
        self.s_height.setSuffix(" px")
        self.s_segment = QSpinBox()
        self.s_segment.setRange(5, 3600)
        self.s_segment.setSuffix(" s")
        youtube.body().addWidget(field("Maximum height", self.s_height))
        youtube.body().addWidget(field("Segment duration", self.s_segment, "Longer segments mean fewer cuts but more buffering."))
        youtube.body().addStretch(1)
        grid.addWidget(youtube, 0, 1)

        detection = Card("Detection", "Template matching and temporal validation")
        row = QHBoxLayout()
        self.s_threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.s_threshold_slider.setRange(0, 100)
        self.s_threshold = spin(0, 1, 2, .01)
        self.s_threshold.setFixedWidth(80)
        self.s_threshold_slider.valueChanged.connect(lambda v: self.s_threshold.setValue(v/100))
        self.s_threshold.valueChanged.connect(lambda v: self.s_threshold_slider.setValue(round(v*100)))
        row.addWidget(self.s_threshold_slider, 1)
        row.addWidget(self.s_threshold)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        detection.body().addWidget(field("Match threshold", holder, "Higher is stricter: fewer false hits, more missed kills."))
        pair = QHBoxLayout()
        self.s_window = spin(0, 60, 1, .5, " s")
        self.s_min_obs = QSpinBox()
        self.s_min_obs.setRange(1, 50)
        pair.addWidget(field("Temporal window", self.s_window))
        pair.addWidget(field("Min. observations", self.s_min_obs))
        detection.body().addLayout(pair)
        detection.body().addStretch(1)
        grid.addWidget(detection, 1, 0)

        editorial = Card("Clips", "Grouping and padding around detected kills")
        self.s_grouping = spin(0, 600, 1, 1, " s")
        self.s_before = spin(0, 600, 1, 1, " s")
        self.s_after = spin(0, 600, 1, 1, " s")
        editorial.body().addWidget(field("Group kills within", self.s_grouping, "Kills closer than this become one clip."))
        pair = QHBoxLayout()
        pair.addWidget(field("Before first kill", self.s_before))
        pair.addWidget(field("After last kill", self.s_after))
        editorial.body().addLayout(pair)
        editorial.body().addStretch(1)
        grid.addWidget(editorial, 1, 1)

        encoding = Card("Output encoding", "Stream copy keeps the original bitstream and is fastest")
        encoders = {kind: pipeline.available_encoders(kind) for kind in ("video", "audio")}
        self.s_copy, self.s_encoder = {}, {}
        for kind in ("video", "audio"):
            row = QHBoxLayout()
            row.setSpacing(12)
            toggle = Toggle(True)
            combo = QComboBox()
            preferred = {"video": ("libx264", "h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_amf", "libx265"),
                         "audio": ("aac", "libopus", "libmp3lame")}[kind]
            combo.addItems(sorted(encoders[kind], key=lambda name: (name not in preferred,
                                                                    preferred.index(name) if name in preferred else 0, name)))
            combo.setMaxVisibleItems(14)
            combo.setEditable(not encoders[kind])
            combo.setEnabled(False)
            toggle.toggled.connect(lambda checked, c=combo: c.setEnabled(not checked))
            row.addWidget(toggle)
            row.addWidget(QLabel(f"{kind.title()}: stream copy"), 1)
            row.addWidget(combo, 1)
            encoding.body().addLayout(row)
            self.s_copy[kind], self.s_encoder[kind] = toggle, combo
        grid.addWidget(encoding, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        footer = QHBoxLayout()
        self.save_button = QPushButton("Save settings", objectName="primary")
        self.save_button.clicked.connect(self.save_settings)
        self.revert_button = QPushButton("Revert", objectName="ghost")
        self.revert_button.clicked.connect(lambda: self.populate_settings(force=True))
        self.dirty_label = QLabel("", objectName="faint")
        footer.addWidget(self.save_button)
        footer.addWidget(self.revert_button)
        footer.addSpacing(8)
        footer.addWidget(self.dirty_label, 1)
        layout.addLayout(footer)
        layout.addStretch(1)

        for widget in (self.s_hwaccel, *self.s_encoder.values()):
            widget.currentTextChanged.connect(self.settings_changed)
        self.s_fps.textChanged.connect(self.settings_changed)
        for widget in (self.s_height, self.s_segment, self.s_min_obs, self.s_threshold, self.s_window,
                       self.s_grouping, self.s_before, self.s_after):
            widget.valueChanged.connect(self.settings_changed)
        for toggle in self.s_copy.values():
            toggle.toggled.connect(self.settings_changed)
        return page_scroll(content)

    # ------------------------------------------------------------------ navigation

    def show_page(self, key: str) -> None:
        for name, button in self.nav.items():
            button.setChecked(name == key)
        _, _, title, subtitle = next(page for page in PAGES if page[0] == key)
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        self.stack.fade_to(self.pages[key])
        if key == "Templates":
            self.render_templates()
        elif key == "Calibrate":
            self.render_calibration()

    def notify(self, message: str, kind: str = "info") -> None:
        self.toast.show_message(message, kind)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.toast.isVisible():
            self.toast.reposition()
        columns = max(2, min(6, (self.stack.width() - 20)//250))
        if columns != getattr(self, "template_columns", columns) and self.stack.currentIndex() == self.pages.get("Templates"):
            self.template_columns = columns
            self.render_templates()
        self.template_columns = columns

    def showEvent(self, event) -> None:
        super().showEvent(event)
        t.dark_title_bar(self)

    # ------------------------------------------------------------------ configuration

    def reload_config(self, show_error: bool = True) -> None:
        try:
            self.config = config_manager.load()
        except FileNotFoundError:
            self.config = None
            self.env.set("Config", "not found")
        except ValueError as error:
            self.config = None
            self.env.set("Config", "invalid")
            if show_error:
                QMessageBox.critical(self, "Invalid configuration", str(error))
        else:
            self.env.set("Config", "loaded")
        color = t.GREEN if self.config else t.AMBER
        self.env.rows["Config"].setStyleSheet(f"color:{color};")
        self._config_signature = self.config_signature()
        if self.config:
            self.populate_settings()
        self.render_summary()
        self.render_calibration()
        self.update_template_env()
        self.update_run_state()

    @staticmethod
    def config_signature():
        try:
            stat = config_manager.active_path().stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def watch_config(self) -> None:
        signature = self.config_signature()
        if signature != self._config_signature:
            self.reload_config(show_error=False)
            self.notify("Configuration changed on disk and was reloaded.")
        if self.calibration is not None and not self.calibration.isVisible():
            self.calibration = None
            self.render_templates()
            self.update_template_env()

    def settings_values(self) -> tuple:
        return (self.s_hwaccel.currentText(), self.s_fps.text().strip(), self.s_height.value(), self.s_segment.value(),
                round(self.s_threshold.value(), 2), self.s_window.value(), self.s_min_obs.value(), self.s_grouping.value(),
                self.s_before.value(), self.s_after.value(),
                *(self.s_copy[k].isChecked() for k in ("video", "audio")),
                *(self.s_encoder[k].currentText() for k in ("video", "audio")))

    def populate_settings(self, force: bool = False) -> None:
        if not self.config:
            return
        if not force and self._settings_snapshot is not None and self.settings_values() != self._settings_snapshot:
            return  # keep the user's unsaved edits
        cfg = self.config
        self._loading_settings = True
        accel = cfg["ffmpeg"]["hwaccel"]
        self.s_hwaccel.setCurrentText("None" if accel == "none" else {"videotoolbox": "VideoToolbox"}.get(accel, accel.upper()))
        self.s_fps.setText(cfg["sampling"]["fps"])
        self.s_height.setValue(cfg["download"]["height"])
        self.s_segment.setValue(cfg["download"]["segment_seconds"])
        self.s_threshold.setValue(cfg["detection"]["threshold"])
        self.s_window.setValue(cfg["detection"]["temporal_window_seconds"])
        self.s_min_obs.setValue(int(cfg["detection"]["min_observations"]))
        self.s_grouping.setValue(cfg["editorial"]["grouping_seconds"])
        self.s_before.setValue(cfg["editorial"]["clip_before_seconds"])
        self.s_after.setValue(cfg["editorial"]["clip_after_seconds"])
        for kind in ("video", "audio"):
            codec = cfg["encoding"].get(kind, "copy")
            self.s_copy[kind].setChecked(codec == "copy")
            self.s_encoder[kind].setEnabled(codec != "copy")
            if codec != "copy":
                self.s_encoder[kind].setCurrentText(codec)
        self._loading_settings = False
        self._settings_snapshot = self.settings_values()
        self.settings_changed()

    def settings_changed(self, *_args) -> None:
        if self._loading_settings:
            return
        try:
            interval = pipeline.sampling_interval(self.s_fps.text().strip())
            self.s_fps_hint.setText(f"One sample every {interval:.2f}s · {3600/interval:,.0f} samples per hour of footage")
        except RuntimeError:
            self.s_fps_hint.setText(f"<span style='color:{t.RED}'>Use a positive fraction such as 2/3</span>")
        dirty = self._settings_snapshot is not None and self.settings_values() != self._settings_snapshot
        self.dirty_label.setText("● Unsaved changes — they also apply to the next run" if dirty else "All changes saved")
        self.dirty_label.setStyleSheet(f"color:{t.AMBER if dirty else t.TEXT_3};")
        self.revert_button.setEnabled(dirty)
        self.render_summary()
        self.update_preview()

    def collect_settings(self) -> dict:
        """Return a validated config built from the Settings form (saved or not)."""
        if not self.config:
            raise ValueError("Configuration unavailable. Calibrate first to create config.json.")
        cfg = copy.deepcopy(self.config)
        cfg["ffmpeg"]["hwaccel"] = self.s_hwaccel.currentText().lower()
        cfg["sampling"]["fps"] = self.s_fps.text().strip()
        cfg["download"] = {"height": self.s_height.value(), "segment_seconds": self.s_segment.value()}
        cfg["detection"].update(threshold=round(self.s_threshold.value(), 3),
                                temporal_window_seconds=self.s_window.value(), min_observations=self.s_min_obs.value())
        cfg["editorial"].update(grouping_seconds=self.s_grouping.value(), clip_before_seconds=self.s_before.value(),
                                clip_after_seconds=self.s_after.value())
        encoding = {}
        for kind in ("video", "audio"):
            encoding[kind] = "copy" if self.s_copy[kind].isChecked() else self.s_encoder[kind].currentText().strip()
            if not encoding[kind]:
                raise ValueError(f"Choose a {kind} encoder, or enable stream copy.")
        cfg["encoding"] = encoding
        return config_manager.validate(cfg)

    def save_settings(self) -> None:
        try:
            cfg = self.collect_settings()
            config_manager.save(cfg)
        except ValueError as error:
            QMessageBox.warning(self, "Invalid settings", str(error))
            return
        self.config = cfg
        self._config_signature = self.config_signature()
        self._settings_snapshot = self.settings_values()
        self.settings_changed()
        self.notify("Settings saved", "ok")

    def offer_calibration(self) -> None:
        if self.config is None and QMessageBox.question(
                self, "Calibration required", "No usable config.json was found. Open the calibration workspace now?") == QMessageBox.StandardButton.Yes:
            self.open_calibration()

    def open_calibration(self) -> None:
        from .calibration import Calibration
        if self.calibration is not None and self.calibration.isVisible():
            self.calibration.raise_()
            self.calibration.activateWindow()
            return
        self.calibration = Calibration()
        self.calibration.setWindowIcon(app_icon())
        self.calibration.show()
        t.dark_title_bar(self.calibration)
        self.notify("Calibration workspace opened")

    # ------------------------------------------------------------------ summaries

    def render_summary(self) -> None:
        if not self.config:
            for key in ("Calibrated for", "Sampling", "Threshold", "Validation", "Clip padding", "Decoder", "Encoding", "Templates"):
                self.summary.set(key, "—")
            self.process_crop.set_crop((0, 0), None)
            return
        try:
            cfg = self.collect_settings()
        except ValueError:
            cfg = self.config
        res, crop = cfg["source_resolution"], cfg["kill_feed_crop"]
        self.summary.set("Calibrated for", f"{res['width']} × {res['height']}")
        try:
            interval = pipeline.sampling_interval(cfg["sampling"]["fps"])
            self.summary.set("Sampling", f"{cfg['sampling']['fps']} fps  (every {interval:.2f}s)")
        except RuntimeError:
            self.summary.set("Sampling", "invalid")
        self.summary.set("Threshold", f"{cfg['detection']['threshold']:.2f}")
        self.summary.set("Validation", f"{cfg['detection']['temporal_window_seconds']:g}s window · ≥{cfg['detection']['min_observations']} obs")
        ed = cfg["editorial"]
        self.summary.set("Clip padding", f"−{ed['clip_before_seconds']:g}s / +{ed['clip_after_seconds']:g}s · group {ed['grouping_seconds']:g}s")
        self.summary.set("Decoder", cfg["ffmpeg"]["hwaccel"])
        self.summary.set("Encoding", f"v:{cfg['encoding']['video']}  a:{cfg['encoding']['audio']}")
        total, enabled = self.template_stats()
        self.summary.set("Templates", f"{enabled} of {total} enabled")
        try:
            interval = pipeline.sampling_interval(cfg["sampling"]["fps"])
        except RuntimeError:
            interval = None
        waiting = [item for item in self.queue if item.status in {"Queued", "Running"}]
        remote = sum(1 for item in waiting if item.remote)
        self.summary.set("Queue", f"{len(waiting)} to process" + (f" · {remote} YouTube" if remote else ""))
        seconds = 0.0
        for item in waiting:
            if item.remote or not item.info or "duration" not in item.info:
                continue
            try:
                first = pipeline.parse_time(item.start or "0")
                last = pipeline.parse_time(item.end) if item.end.strip() else item.info["duration"]
                seconds += max(0.0, min(last, item.info["duration"]) - first)
            except ValueError:
                pass
        self.summary.set("Local footage", fmt_time(seconds) if seconds else "—")
        self.summary.set("Est. samples", fmt_count(seconds/interval) if seconds and interval else "—")
        self.process_crop.set_crop((res["width"], res["height"]), (crop["x"], crop["y"], crop["width"], crop["height"]))

    def render_calibration(self) -> None:
        if not self.config:
            self.calib_status.set_state("Failed", "Not calibrated")
            self.calib_crop.set_crop((0, 0), None)
            for key in ("Source resolution", "Kill-feed crop", "FFmpeg crop", "Templates"):
                self.calib_info.set(key, "—")
        else:
            res, crop = self.config["source_resolution"], self.config["kill_feed_crop"]
            self.calib_status.set_state("Completed", "Calibrated")
            self.calib_crop.set_crop((res["width"], res["height"]), (crop["x"], crop["y"], crop["width"], crop["height"]))
            self.calib_info.set("Source resolution", f"{res['width']} × {res['height']}")
            self.calib_info.set("Kill-feed crop", f"{crop['width']} × {crop['height']} at ({crop['x']}, {crop['y']})")
            self.calib_info.set("FFmpeg crop", f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}")
            total, enabled = self.template_stats()
            self.calib_info.set("Templates", f"{enabled} of {total} enabled")
        previews = sorted(paths.CALIBRATION_OUTPUT_DIR.glob("preview_*.png"), key=lambda p: p.stat().st_mtime) if paths.CALIBRATION_OUTPUT_DIR.is_dir() else []
        if previews:
            pixmap = QPixmap(str(previews[-1]))
            if not pixmap.isNull():
                width = max(300, self.calib_image.width() - 4)
                self.calib_image.setPixmap(pixmap.scaledToWidth(min(width, 900), Qt.TransformationMode.SmoothTransformation))
                self.calib_image.setToolTip(str(previews[-1]))

    # ------------------------------------------------------------------ templates

    def templates_dir(self) -> Path:
        return paths.project_path(self.config["templates_dir"]) if self.config else paths.TEMPLATES_DIR

    def template_files(self) -> list[Path]:
        directory = self.templates_dir()
        return sorted(directory.glob("*.PNG")) if directory.is_dir() else []

    def template_stats(self) -> tuple[int, int]:
        files = self.template_files()
        disabled = set(self.config["template_settings"].get("disabled", [])) if self.config else set()
        return len(files), sum(1 for f in files if f.name not in disabled)

    def update_template_env(self) -> None:
        total, enabled = self.template_stats()
        self.env.set("Templates", f"{enabled}/{total} on")
        self.env.rows["Templates"].setStyleSheet(f"color:{t.GREEN if enabled else t.RED};")

    def render_templates(self) -> None:
        while self.template_grid.count():
            item = self.template_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        files = self.template_files()
        disabled = set(self.config["template_settings"].get("disabled", [])) if self.config else set()
        total, enabled = self.template_stats()
        self.template_count.setText(f"{total} templates  ·  {enabled} enabled  ·  {self.templates_dir()}")
        hits = self.monitor.templates if hasattr(self, "monitor") else {}
        columns = max(2, self.template_columns)
        for index, path in enumerate(files):
            card = QFrame(objectName="card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(8)
            thumb = QLabel()
            thumb.setFixedHeight(78)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setStyleSheet(f"background:{t.BG}; border:1px solid {t.BORDER}; border-radius:8px;")
            pixmap = QPixmap(str(path))
            size = ""
            if pixmap.isNull():
                thumb.setText("unreadable")
            else:
                size = f"{pixmap.width()}×{pixmap.height()}"
                thumb.setPixmap(pixmap.scaled(200, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            is_on = path.name not in disabled
            if not is_on:
                thumb.setStyleSheet(thumb.styleSheet() + "color:#646d79;")
            layout.addWidget(thumb)
            top = QHBoxLayout()
            name = QLabel(path.stem)
            name.setFont(t.font(10, QFont.Weight.DemiBold))
            top.addWidget(name, 1)
            toggle = Toggle(is_on)
            toggle.setEnabled(self.config is not None)
            toggle.toggled.connect(lambda checked, p=path: self.toggle_template(p, checked))
            top.addWidget(toggle)
            layout.addLayout(top)
            stat = hits.get(path.name)
            meta = f"{size} px" + (f"  ·  {stat[0]} hits, best {stat[1]:.2f}" if stat else "")
            bottom = QHBoxLayout()
            bottom.addWidget(QLabel(meta, objectName="faint"), 1)
            delete = QPushButton("Delete", objectName="ghost")
            delete.clicked.connect(lambda _=False, p=path: self.delete_template(p))
            bottom.addWidget(delete)
            layout.addLayout(bottom)
            self.template_grid.addWidget(card, index//columns, index % columns)
        for column in range(6):
            self.template_grid.setColumnStretch(column, 1 if column < columns else 0)
        if not files:
            empty = QLabel("No templates yet. Create one from the calibration workspace.", objectName="faint")
            self.template_grid.addWidget(empty, 0, 0, 1, columns)

    def toggle_template(self, path: Path, enabled: bool) -> None:
        if not self.config:
            return
        disabled = set(self.config["template_settings"].get("disabled", []))
        (disabled.discard if enabled else disabled.add)(path.name)
        self.config["template_settings"]["disabled"] = sorted(disabled)
        self.persist_config_field()
        self.notify(f"{path.stem} {'enabled' if enabled else 'disabled'}", "ok" if enabled else "info")
        self.update_template_env()
        self.render_summary()
        total, on = self.template_stats()
        self.template_count.setText(f"{total} templates  ·  {on} enabled  ·  {self.templates_dir()}")

    def set_all_templates(self, enabled: bool) -> None:
        if not self.config:
            return
        self.config["template_settings"]["disabled"] = [] if enabled else [p.name for p in self.template_files()]
        self.persist_config_field()
        self.render_templates()
        self.update_template_env()
        self.render_summary()
        self.notify("All templates enabled", "ok")

    def persist_config_field(self) -> None:
        """Save template toggles without also committing unsaved Settings edits."""
        try:
            on_disk = config_manager.load()
        except (FileNotFoundError, ValueError):
            on_disk = copy.deepcopy(self.config)
        on_disk["template_settings"] = copy.deepcopy(self.config["template_settings"])
        config_manager.save(on_disk)
        self._config_signature = self.config_signature()

    def delete_template(self, path: Path) -> None:
        if QMessageBox.question(self, "Delete template", f"Delete {path.name}? This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as error:
            QMessageBox.critical(self, "Delete failed", str(error))
            return
        if self.config:
            self.config["template_settings"]["disabled"] = [n for n in self.config["template_settings"].get("disabled", []) if n != path.name]
            self.persist_config_field()
        self.notify(f"Deleted {path.name}")
        self.render_templates()
        self.update_template_env()
        self.render_summary()

    # ------------------------------------------------------------------ process page

    def source_changed(self) -> None:
        self.probe_info = None
        source = self.source_edit.text().strip()
        self.source_info.setVisible(bool(source))
        if not source:
            self.source_info.setText("")
        elif pipeline.stream.is_youtube(source):
            self.source_info.setText(f"<span style='color:{t.ACCENT}'>●</span>&nbsp; YouTube URL · press Enter or Add to queue it")
        elif "://" in source:
            self.source_info.setText(f"<span style='color:{t.RED}'>●</span>&nbsp; Only YouTube URLs or local files are supported")
        elif Path(source).is_file():
            self.source_info.setText(f"<span style='color:{t.TEXT_3}'>●</span>&nbsp; Reading media info…")
            self.probe_timer.start()
        else:
            self.source_info.setText(f"<span style='color:{t.AMBER}'>●</span>&nbsp; File not found")
        self.update_run_state()

    def start_probe(self, source: str | None = None) -> None:
        source = source or self.source_edit.text().strip()
        if not source or not Path(source).is_file() or not self.ffmpeg_ok:
            return
        def work(path=Path(source)) -> None:
            try:
                info = dict(duration=pipeline.ffprobe_duration(path), size=path.stat().st_size)
                info["width"], info["height"] = pipeline.ffprobe_dimensions(path)
                self.messages.put(("probe", str(path), info))
            except Exception as error:
                self.messages.put(("probe", str(path), {"error": str(error)}))
        threading.Thread(target=work, daemon=True).start()

    def show_probe(self, path: str, info: dict) -> None:
        for item in self.queue:
            if not item.remote and item.source == path:
                item.info = info
                self.queue_view.refresh(item)
        self.render_summary()
        if path != self.source_edit.text().strip():
            return
        if "error" in info:
            self.source_info.setText(f"<span style='color:{t.RED}'>●</span>&nbsp; Unreadable: {info['error'][:120]}")
            return
        self.probe_info = info
        parts = [f"{info['width']}×{info['height']}", fmt_time(info["duration"]), f"{info['size']/1_073_741_824:.2f} GB"
                 if info["size"] >= 1_073_741_824 else f"{info['size']/1_048_576:.0f} MB"]
        dot, note = t.GREEN, ""
        if self.config:
            res = self.config["source_resolution"]
            if (res["width"], res["height"]) != (info["width"], info["height"]):
                dot = t.AMBER
                note = f" — calibrated for {res['width']}×{res['height']}; frames will be rescaled"
        self.source_info.setText(f"<span style='color:{dot}'>●</span>&nbsp; Local file · " + " · ".join(parts) + note)

    # ------------------------------------------------------------------ queue

    def validate_source(self, source: str, start: str, end: str) -> str:
        """Return an error message, or '' when the entry can be queued."""
        if not source:
            return "Paste a path or URL first."
        if "://" in source and not pipeline.stream.is_youtube(source):
            return "Only YouTube URLs or local files are supported."
        if not pipeline.stream.is_youtube(source) and not Path(source).is_file():
            return f"File not found: {source}"
        try:
            first = pipeline.parse_time(start or "0")
            last = pipeline.parse_time(end) if end.strip() else float("inf")
        except ValueError as error:
            return str(error)
        if last <= first:
            return "End time must be later than start time."
        return ""

    def add_source(self, source: str | None = None, quiet: bool = False) -> bool:
        source = (source if source is not None else self.source_edit.text()).strip().strip('"')
        start, end = self.start_edit.text().strip() or "0", self.end_edit.text().strip()
        problem = self.validate_source(source, start, end)
        if problem:
            if not quiet:
                self.notify(problem, "error")
            return False
        self.queue_uid += 1
        item = QueueItem(source, start, end, uid=self.queue_uid)
        if not item.remote and self.probe_info and source == self.source_edit.text().strip():
            item.info = self.probe_info
        self.queue.append(item)
        if not item.remote and item.info is None:
            self.start_probe(source)
        if source == self.source_edit.text().strip().strip('"'):
            self.source_edit.clear()
        self.render_queue()
        if not quiet:
            self.notify(f"Added to queue · {item.name}", "ok")
        return True

    def render_queue(self) -> None:
        self.queue_view.set_items(self.queue, self.running)
        waiting = sum(1 for item in self.queue if item.status == "Queued")
        done = sum(1 for item in self.queue if item.status == "Done")
        self.queue_count.setText(f"{len(self.queue)} items · {waiting} waiting · {done} done" if self.queue else "")
        self.update_run_state()
        self.render_summary()

    def remove_item(self, index: int) -> None:
        if 0 <= index < len(self.queue) and self.queue[index].status != "Running":
            self.queue.pop(index)
            self.render_queue()

    def move_item(self, index: int, delta: int) -> None:
        target = index + delta
        if 0 <= index < len(self.queue) and 0 <= target < len(self.queue):
            self.queue[index], self.queue[target] = self.queue[target], self.queue[index]
            self.render_queue()

    def retry_item(self, index: int) -> None:
        if 0 <= index < len(self.queue):
            item = self.queue[index]
            item.status, item.detail, item.clips, item.progress = "Queued", "", 0, 0.0
            self.render_queue()

    def clear_finished(self) -> None:
        self.queue = [item for item in self.queue if item.status in {"Queued", "Running"}]
        self.render_queue()

    def clear_queue(self) -> None:
        self.queue = [item for item in self.queue if item.status == "Running"]
        self.render_queue()

    def pending(self) -> list[QueueItem]:
        return [item for item in self.queue if item.status == "Queued"]

    # ------------------------------------------------------------------ readiness

    def readiness(self) -> str:
        if self.running:
            return "A run is in progress"
        if not self.ffmpeg_ok:
            return "FFmpeg / ffprobe not found on PATH"
        if not self.config:
            return "Calibrate first to create a configuration"
        if not self.template_stats()[1]:
            return "Enable at least one template"
        typed = self.source_edit.text().strip()
        if not self.pending() and not typed:
            return "Add a recording or YouTube URL to the queue"
        if typed:
            problem = self.validate_source(typed.strip('"'), self.start_edit.text(), self.end_edit.text())
            if problem:
                return problem
        if not self.output_edit.text().strip():
            return "Choose an output folder"
        try:
            self.collect_settings()
        except ValueError as error:
            return str(error)
        return ""

    def update_run_state(self, *_args) -> None:
        reason = self.readiness()
        self.run_button.setEnabled(not reason)
        count = len(self.pending()) + (1 if self.source_edit.text().strip() else 0)
        self.run_button.setText(f"Start queue · {count}" if count > 1 else "Start run")
        self.ready_hint.setText(reason if not self.running else "")
        self.update_preview()

    def browse_video(self) -> None:
        value = native_picker.choose_video()
        if value is not None:
            files = [value] if value else []
        else:
            files, _ = QFileDialog.getOpenFileNames(self, "Select recordings", "", "Video files (*.mkv *.mp4 *.mov *.avi);;All files (*)")
        if len(files) == 1 and not self.queue:
            self.source_edit.setText(files[0])
            return
        added = sum(1 for path in files if self.add_source(path, quiet=True))
        if added:
            self.notify(f"Added {added} recording{'s' if added != 1 else ''} to the queue", "ok")

    def browse_output(self) -> None:
        value = native_picker.choose_folder()
        if value is None:
            value = QFileDialog.getExistingDirectory(self, "Select output folder")
        if value:
            self.output_edit.setText(value)

    def request_for(self, item: QueueItem, position: int) -> pipeline.RunRequest:
        output = Path(self.output_edit.text().strip())
        if len(self.queue) > 1:
            output = output / f"{position:02d}_{item.slug()}"
        video = item.source if item.remote else Path(item.source)
        start = pipeline.parse_time(item.start or "0")
        end = pipeline.parse_time(item.end) if item.end.strip() else float("inf")
        return pipeline.RunRequest(video, output, start, end, self.collect_settings())

    def update_preview(self) -> None:
        typed = self.source_edit.text().strip().strip('"')
        candidate = None
        if typed and not self.validate_source(typed, self.start_edit.text(), self.end_edit.text()):
            candidate = QueueItem(typed, self.start_edit.text().strip() or "0", self.end_edit.text().strip())
        elif self.pending():
            candidate = self.pending()[0]
        try:
            if candidate is None or not self.output_edit.text().strip():
                raise ValueError
            request = self.request_for(candidate, 1)
            if isinstance(request.video, str):
                text = "YouTube → yt-dlp → FFmpeg segments X / Y / Z → crop + template matching (per segment)"
            else:
                text = " ".join(f'"{part}"' if " " in part else part for part in pipeline.extraction_command(request))
        except (ValueError, KeyError, RuntimeError):
            text = "Add a source and choose an output folder to preview the FFmpeg command."
        self.command.setPlainText(text)

    def copy_command(self) -> None:
        QApplication.clipboard().setText(self.command.toPlainText())
        self.notify("Command copied to clipboard", "ok")

    # ------------------------------------------------------------------ running

    def run_pipeline(self) -> None:
        if self.source_edit.text().strip() and not self.add_source():
            return
        if self.readiness():
            QMessageBox.warning(self, "Cannot run", self.readiness())
            return
        self.running = True
        self.cancel.clear()
        self.started = time.monotonic()
        self.batch = list(self.pending())
        self.batch_clips = 0
        self.pill.set_state("Running")
        self.run_button.hide()
        self.stop_button.show()
        self.stop_button.setEnabled(True)
        self.show_page("Monitor")
        self.run_next()

    def run_next(self) -> None:
        pending = [item for item in self.batch if item.status == "Queued"]
        if not pending:
            self.finish_queue()
            return
        item = pending[0]
        position = self.queue.index(item) + 1 if item in self.queue else 1
        try:
            request = self.request_for(item, position)
        except (ValueError, KeyError) as error:
            item.status, item.detail = "Failed", str(error)
            self.render_queue()
            QTimer.singleShot(0, self.run_next)
            return
        self.current = item
        item.status, item.detail, item.clips, item.progress = "Running", "", 0, 0.0
        item.output = request.output
        self.run_output = request.output
        self.cancel.clear()
        done = sum(1 for entry in self.batch if entry.status != "Queued") - 1
        label = f"Item {done + 1} of {len(self.batch)}" if len(self.batch) > 1 else None
        self.monitor.reset(request.video, item.remote, request.output, request.config, label)
        self.nav["Monitor"].set_badge("live")
        self.render_queue()
        threading.Thread(target=self.worker, args=(request,), daemon=True).start()

    def stop_pipeline(self) -> None:
        if self.running and not self.cancel.is_set():
            self.cancel.set()
            self.stop_button.setEnabled(False)
            self.pill.set_state("Stopping")
            self.monitor.pill.set_state("Stopping")
            self.notify("Stopping after the current step… the rest of the queue stays queued", "warn")

    def worker(self, request: pipeline.RunRequest) -> None:
        def check() -> None:
            if self.cancel.is_set():
                raise RunCancelled()
        def on_log(text: str) -> None:
            self.messages.put(("log", text))
            check()
        def on_monitor(stage: str, detail: str) -> None:
            self.messages.put(("monitor", time.time(), stage, detail))
            check()
        def on_metrics(values: dict) -> None:
            self.messages.put(("metrics", values))
            check()
        try:
            result = pipeline.run(request, on_log, lambda *_: check(), on_monitor, on_metrics)
            self.messages.put(("done", result))
        except RunCancelled:
            self.messages.put(("cancelled",))
        except Exception as error:
            self.messages.put(("error", str(error) or error.__class__.__name__))

    def pump(self) -> None:
        try:
            for _ in range(400):
                message = self.messages.get_nowait()
                kind = message[0]
                if kind == "monitor":
                    self.monitor.on_event(*message[1:])
                elif kind == "metrics":
                    values = message[1]
                    self.monitor.update_metrics(values)
                    if values.get("stream") and self.current is not None:
                        self.current.title = values["stream"].get("title")
                        self.queue_view.refresh(self.current)
                    if values.get("clip"):
                        self.notify(f"Clip saved · {values['clip']['file']}", "ok")
                        self.nav["Monitor"].set_badge(f"{len(self.monitor.records)}")
                elif kind == "probe":
                    self.show_probe(message[1], message[2])
                elif kind == "error":
                    self.item_finished("Failed", message[1])
                elif kind == "cancelled":
                    self.item_finished("Stopped", "Run stopped by user. Clips already written were kept.")
                elif kind == "done":
                    result = message[1]
                    self.item_finished("Completed", f"{result['raw_detections']} raw hits · {result['validated_kills']} kill events · "
                                                    f"{result['clips']} clips in {fmt_time(result['elapsed'])}", result["clips"])
        except queue.Empty:
            pass
        if self.running:
            self.elapsed.setText(fmt_time(time.monotonic() - self.started))
            item = self.current
            if item is not None and item.status == "Running":
                m = self.monitor.m
                end = m["end"]
                if end is not None and math.isfinite(end) and end > m["start"]:
                    progress = max(0.0, min(1.0, (m["position"] - m["start"])/(end - m["start"])))
                    if abs(progress - item.progress) >= .01:
                        item.progress = progress
                        self.queue_view.refresh(item)

    def item_finished(self, state: str, detail: str, clips: int = 0) -> None:
        item = self.current
        self.monitor.finish(state, detail)
        if item is not None:
            item.status = {"Completed": "Done", "Failed": "Failed", "Stopped": "Stopped"}[state]
            item.detail, item.clips = detail, clips
            if state == "Completed":
                item.progress = 1.0
        self.batch_clips += clips
        if state == "Failed" and item is not None and item.output is not None:
            try:
                item.output.rmdir()  # only succeeds when the failed run left it empty
            except OSError:
                pass
        if state == "Failed":
            self.notify(f"Failed · {item.name if item else ''}: {detail[:90]}", "error")
        elif state == "Completed":
            self.notify(f"Done · {item.name if item else ''} · {clips} clip{'s' if clips != 1 else ''}", "ok")
        self.render_queue()
        if state == "Stopped":
            self.finish_queue("Stopped")
        else:
            QTimer.singleShot(800, self.run_next)

    def finish_queue(self, state: str | None = None) -> None:
        self.current = None
        failed = sum(1 for item in self.batch if item.status == "Failed")
        if state is None:
            state = "Failed" if failed and failed == len(self.batch) else "Completed"
        self.finished(state)
        if len(self.batch) > 1 and state != "Stopped":
            note = f" · {failed} failed" if failed else ""
            self.notify(f"Queue finished · {len(self.batch)} items · {self.batch_clips} clips{note}", "warn" if failed else "ok")
        if len(self.batch) == 1 and failed:
            QMessageBox.critical(self, "Pipeline failed", self.batch[0].detail)

    def finished(self, state: str) -> None:
        self.running = False
        self.pill.set_state(state)
        self.elapsed.setText(fmt_time(time.monotonic() - self.started))
        self.nav["Monitor"].set_badge(str(len(self.monitor.records)) if self.monitor.records else "")
        self.stop_button.hide()
        self.run_button.show()
        self.render_queue()
        self.render_templates()

    def open_output(self) -> None:
        self.open_path(self.run_output / "clips")

    def open_path(self, path: Path) -> None:
        try:
            native_picker.open_folder(path)
        except OSError as error:
            QMessageBox.warning(self, "Cannot open", str(error))

    def closeEvent(self, event) -> None:
        if self.running:
            answer = QMessageBox.question(self, "Run in progress", "A run is still in progress. Stop it and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel.set()
        if self.calibration is not None:
            self.calibration.close()
        super().closeEvent(event)


def main() -> int:
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FragEngine")
    t.apply(app)
    window = FragEngineWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
