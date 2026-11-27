"""Processing queue: several local recordings and/or YouTube URLs run one after another."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import stream
from . import theme as t
from .widgets import fmt_time

STATUS_COLORS = {"Queued": t.TEXT_2, "Running": t.ACCENT, "Done": t.GREEN, "Failed": t.RED, "Stopped": t.AMBER}


def stream_start(value: str) -> float:
    from ..core.pipeline import parse_time
    return parse_time(value or "0")


def youtube_id(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname and parts.hostname.endswith("youtu.be"):
        return parts.path.strip("/").split("/")[0]
    query = parse_qs(parts.query).get("v")
    if query:
        return query[0]
    match = re.search(r"/(?:live|shorts|embed)/([^/?#]+)", parts.path)
    return match.group(1) if match else "youtube"


@dataclass
class QueueItem:
    source: str
    start: str = "0"
    end: str = ""
    status: str = "Queued"
    detail: str = ""
    info: dict | None = None
    title: str | None = None
    output: Path | None = None
    clips: int = 0
    progress: float = 0.0
    uid: int = field(default=0)

    @property
    def remote(self) -> bool:
        return stream.is_youtube(self.source)

    @property
    def name(self) -> str:
        if self.remote:
            return self.title or self.source
        return Path(self.source).name

    def slug(self) -> str:
        base = youtube_id(self.source) if self.remote else Path(self.source).stem
        return re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")[:60] or "source"

    def has_range(self) -> bool:
        try:
            return bool(self.end.strip()) or stream_start(self.start) > 0
        except ValueError:
            return True

    def range_text(self) -> str:
        start = self.start.strip() or "0"
        end = self.end.strip()
        return f"{start} → {end or 'end'}"

    def meta(self) -> str:
        if self.remote:
            return "YouTube · " + (self.source if self.title else "title shown when the run starts")
        info = self.info or {}
        if "error" in info:
            return "unreadable: " + info["error"][:80]
        if "duration" in info:
            size = info["size"]
            size_text = f"{size/1_073_741_824:.2f} GB" if size >= 1_073_741_824 else f"{size/1_048_576:.0f} MB"
            return f"{info['width']}×{info['height']} · {fmt_time(info['duration'])} · {size_text}"
        return "reading media info…"

    def status_text(self) -> str:
        if self.status == "Running":
            return f"Running · {self.progress*100:.0f}%" if self.progress else "Running"
        if self.status == "Done":
            return f"Done · {self.clips} clip{'s' if self.clips != 1 else ''}"
        return self.status


class QueueRow(QFrame):
    def __init__(self, index: int, item: QueueItem, locked: bool, owner: "QueueList"):
        super().__init__()
        self.item = item
        self.setObjectName("queueRow")
        running = item.status == "Running"
        border = t.color(t.ACCENT, 140).name(t.QColor.NameFormat.HexArgb) if running else t.BORDER
        self.setStyleSheet(f"QFrame#queueRow {{ background: {t.BG}; border: 1px solid {border}; border-radius: 10px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 8, 9)
        row.setSpacing(12)
        number = QLabel(str(index + 1))
        number.setFixedSize(24, 24)
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        number.setStyleSheet(f"background:{t.SURFACE_3}; border-radius:12px; color:{t.TEXT_2}; font-weight:600; font-size:9pt;")
        row.addWidget(number)
        kind = QLabel("YT" if item.remote else "FILE")
        kind.setFixedWidth(40)
        kind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tint = t.RED if item.remote else t.CYAN
        kind.setStyleSheet(f"color:{tint}; border:1px solid {t.color(tint, 90).name(t.QColor.NameFormat.HexArgb)};"
                           f" border-radius:6px; padding:2px 0; font-size:7.5pt; font-weight:600;")
        row.addWidget(kind)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.name = QLabel()
        self.name.setToolTip(item.source)
        self.name.setMinimumWidth(80)
        self.meta = QLabel(objectName="faint")
        self.meta.setMinimumWidth(80)
        text.addWidget(self.name)
        text.addWidget(self.meta)
        row.addLayout(text, 1)
        span = QLabel(item.range_text(), objectName="mono")
        span.setToolTip("Start → end for this item")
        span.setVisible(item.has_range())
        row.addWidget(span)
        self.status = QLabel()
        self.status.setMinimumWidth(104)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.status)
        buttons = (("↑", "Move up", lambda: owner.moved.emit(index, -1), index > 0 and not locked),
                   ("↓", "Move down", lambda: owner.moved.emit(index, 1), not locked and index < owner.count - 1),
                   ("↻", "Queue again", lambda: owner.retried.emit(index), item.status in {"Done", "Failed", "Stopped"}),
                   ("✕", "Remove", lambda: owner.removed.emit(index), not running))
        for label, tip, handler, enabled in buttons:
            button = QPushButton(label, objectName="ghost")
            button.setFixedSize(28, 28)
            button.setToolTip(tip)
            button.setEnabled(enabled)
            button.setStyleSheet("padding:0;")
            button.clicked.connect(handler)
            row.addWidget(button)
        self.refresh()

    def refresh(self) -> None:
        item = self.item
        metrics = self.name.fontMetrics()
        self.name.setText(metrics.elidedText(item.name, Qt.TextElideMode.ElideMiddle, max(120, self.name.width())))
        self.meta.setText(self.meta.fontMetrics().elidedText(item.meta(), Qt.TextElideMode.ElideRight, max(120, self.meta.width())))
        color = STATUS_COLORS.get(item.status, t.TEXT_2)
        self.status.setText(f"<span style='color:{color}'>● {item.status_text()}</span>")
        self.status.setToolTip(item.detail)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh()


class QueueList(QWidget):
    removed = Signal(int)
    moved = Signal(int, int)
    retried = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)
        self.rows: list[QueueRow] = []
        self.count = 0
        self.empty = QLabel("Queue is empty. Paste a path or URL above and press Add, or browse for several files.",
                            objectName="faint")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(56)
        self.layout_.addWidget(self.empty)

    def set_items(self, items: list[QueueItem], running: bool) -> None:
        for row in self.rows:
            self.layout_.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.count = len(items)
        for index, item in enumerate(items):
            locked = running and item.status == "Running"
            row = QueueRow(index, item, locked, self)
            self.layout_.addWidget(row)
            self.rows.append(row)
        self.empty.setVisible(not items)

    def refresh(self, item: QueueItem) -> None:
        for row in self.rows:
            if row.item is item:
                row.refresh()
