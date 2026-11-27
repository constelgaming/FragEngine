"""Custom-painted, animated widgets used by the FragEngine dashboard."""
from __future__ import annotations

from collections import deque
import html
import math
import threading
import time
from typing import Callable, Iterable

from PySide6.QtCore import (QEasingCurve, QObject, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from . import theme as t


def fmt_time(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "—"
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"


def fmt_count(value: float) -> str:
    value = float(value)
    if abs(value) >= 1_000_000:
        return f"{value/1_000_000:.1f}M"
    if abs(value) >= 10_000:
        return f"{value/1000:.1f}k"
    return f"{int(round(value)):,}"


# --------------------------------------------------------------------------- animation clock

class Ticker(QObject):
    """One shared ~60 FPS clock, running only while some widget needs it."""
    tick = Signal(float)
    _instance: "Ticker | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(lambda: self.tick.emit(time.monotonic()))
        self.users: set[int] = set()

    @classmethod
    def get(cls) -> "Ticker":
        if cls._instance is None:
            cls._instance = Ticker()
        return cls._instance

    def want(self, owner: object, active: bool) -> None:
        key = id(owner)
        if active:
            self.users.add(key)
        else:
            self.users.discard(key)
        if self.users and not self.timer.isActive():
            self.timer.start()
        elif not self.users and self.timer.isActive():
            self.timer.stop()


class Animated:
    """Mixin: a widget that repaints on the shared clock while `animating` is True."""
    def init_animation(self) -> None:
        self._animating = False
        self.phase = 0.0
        Ticker.get().tick.connect(self._on_tick)

    def set_animating(self, value: bool) -> None:
        if value != self._animating:
            self._animating = value
            Ticker.get().want(self, value)
            self.update()

    def _on_tick(self, now: float) -> None:
        if self._animating and self.isVisible():
            self.phase = now
            self.update()


def rounded(painter: QPainter, rect: QRectF, radius: float, fill: QColor | QBrush | None = None,
            border: QColor | None = None) -> None:
    painter.setPen(QPen(border, 1) if border else Qt.PenStyle.NoPen)
    painter.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, radius, radius)


def text(painter: QPainter, rect: QRectF, value: str, color: str | QColor = t.TEXT_2, size: float = 9,
         weight: QFont.Weight = QFont.Weight.Normal, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
         mono: bool = False) -> None:
    painter.setFont(t.font(size, weight, mono))
    painter.setPen(QColor(color))
    painter.drawText(rect, int(align), value)


# --------------------------------------------------------------------------- layout primitives

class Card(QFrame):
    """Rounded surface with an optional header row."""
    def __init__(self, title: str | None = None, subtitle: str | None = None, parent=None, padding: int = 18):
        super().__init__(parent)
        self.setObjectName("card")
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(padding, padding - 2, padding, padding)
        self.outer.setSpacing(12)
        self.header = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(8)
            column = QVBoxLayout()
            column.setSpacing(1)
            self.title_label = QLabel(title, objectName="h2")
            column.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = QLabel(subtitle, objectName="faint")
                column.addWidget(self.subtitle_label)
            self.header.addLayout(column)
            self.header.addStretch(1)
            self.outer.addLayout(self.header)

    def add_header_widget(self, widget: QWidget) -> None:
        if self.header is not None:
            self.header.addWidget(widget)

    def body(self) -> QVBoxLayout:
        return self.outer


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


class KeyValueList(QWidget):
    """Two-column label/value grid with monospace values."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(7)
        self.rows: dict[str, QLabel] = {}

    def set(self, key: str, value: str, tooltip: str | None = None) -> None:
        if key not in self.rows:
            row = QHBoxLayout()
            name = QLabel(key, objectName="muted")
            label = QLabel(objectName="mono")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(name)
            row.addStretch(1)
            row.addWidget(label)
            self.layout_.addLayout(row)
            self.rows[key] = label
        label = self.rows[key]
        metrics = label.fontMetrics()
        label.setText(metrics.elidedText(value, Qt.TextElideMode.ElideMiddle, 260))
        label.setToolTip(tooltip or (value if len(value) > 30 else ""))


# --------------------------------------------------------------------------- navigation

def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    painter.save()
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    c = rect.center()
    s = min(rect.width(), rect.height()) / 2
    if name == "process":
        path = QPainterPath(QPointF(c.x() - s*.45, c.y() - s*.62))
        path.lineTo(c.x() + s*.65, c.y())
        path.lineTo(c.x() - s*.45, c.y() + s*.62)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "monitor":
        path = QPainterPath(QPointF(c.x() - s*.9, c.y()))
        for dx, dy in ((-.45, 0), (-.25, -.6), (.05, .6), (.3, -.25), (.45, 0), (.9, 0)):
            path.lineTo(c.x() + s*dx, c.y() + s*dy)
        painter.drawPath(path)
    elif name == "calibrate":
        painter.drawEllipse(c, s*.55, s*.55)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            painter.drawLine(QPointF(c.x() + dx*s*.3, c.y() + dy*s*.3), QPointF(c.x() + dx*s*.9, c.y() + dy*s*.9))
    elif name == "templates":
        q, g = s*.68, s*.18
        for dx in (-1, 1):
            for dy in (-1, 1):
                painter.drawRoundedRect(QRectF(c.x() + (g/2 if dx > 0 else -g/2 - q),
                                               c.y() + (g/2 if dy > 0 else -g/2 - q), q, q), 2, 2)
    elif name == "settings":
        for i, knob in enumerate((.35, -.3, .1)):
            y = c.y() + (i-1)*s*.55
            painter.drawLine(QPointF(c.x() - s*.8, y), QPointF(c.x() + s*.8, y))
            painter.setBrush(QColor(t.SURFACE))
            painter.drawEllipse(QPointF(c.x() + knob*s*1.6*.5, y), s*.17, s*.17)
            painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.restore()


class NavButton(QAbstractButton):
    def __init__(self, label: str, icon: str, parent=None):
        super().__init__(parent)
        self.label, self.icon = label, icon
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        self.badge = ""
        self.hover = 0.0
        self.anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._set_hover)

    def _set_hover(self, value) -> None:
        self.hover = float(value)
        self.update()

    def _animate(self, target: float) -> None:
        self.anim.stop()
        self.anim.setStartValue(self.hover)
        self.anim.setEndValue(target)
        self.anim.start()

    def enterEvent(self, event) -> None:
        self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate(0.0)
        super().leaveEvent(event)

    def set_badge(self, value: str) -> None:
        self.badge = value
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(180, 38)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        if self.isChecked():
            rounded(p, r, 8, QColor("#182033"))
            rounded(p, QRectF(r.left(), r.top() + 10, 3, r.height() - 20), 1.5, QColor(t.ACCENT))
        elif self.hover > 0:
            rounded(p, r, 8, t.color(t.SURFACE_3, int(200*self.hover)))
        fg = QColor(t.ACCENT_HOVER) if self.isChecked() else QColor(t.TEXT) if self.hover > .5 else QColor(t.TEXT_2)
        draw_icon(p, self.icon, QRectF(r.left() + 14, r.center().y() - 8, 16, 16), fg)
        text(p, QRectF(r.left() + 42, r.top(), r.width() - 80, r.height()), self.label, fg, 10,
             QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal)
        if self.badge:
            p.setFont(t.font(8, QFont.Weight.DemiBold))
            width = p.fontMetrics().horizontalAdvance(self.badge) + 12
            badge = QRectF(r.right() - width - 10, r.center().y() - 9, width, 18)
            rounded(p, badge, 9, QColor("#22304d"))
            text(p, badge, self.badge, t.ACCENT_HOVER, 8, QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignCenter)


class FadeStack(QStackedWidget):
    """QStackedWidget that cross-fades the incoming page."""
    def fade_to(self, index: int) -> None:
        if index == self.currentIndex():
            return
        self.setCurrentIndex(index)
        page = self.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", page)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


# --------------------------------------------------------------------------- small indicators

STATUS_COLORS = {"Idle": t.TEXT_3, "Ready": t.TEXT_2, "Running": t.ACCENT, "Completed": t.GREEN,
                 "Failed": t.RED, "Stopped": t.AMBER, "Stopping": t.AMBER}


class StatusPill(QWidget, Animated):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_animation()
        self.state, self.label = "Idle", "Idle"
        self.setFixedHeight(28)

    def set_state(self, state: str, label: str | None = None) -> None:
        self.state, self.label = state, label or state
        self.set_animating(state in {"Running", "Stopping"})
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.label) + 42, 28)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = STATUS_COLORS.get(self.state, t.TEXT_3)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        rounded(p, r, r.height()/2, t.color(base, 26), t.color(base, 70))
        center = QPointF(r.left() + 15, r.center().y())
        if self._animating:
            wave = (self.phase * .9) % 1.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color(base, int(110*(1-wave))))
            p.drawEllipse(center, 3.5 + 6*wave, 3.5 + 6*wave)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(base))
        p.drawEllipse(center, 3.5, 3.5)
        text(p, QRectF(r.left() + 26, r.top(), r.width() - 30, r.height()), self.label, base, 9, QFont.Weight.DemiBold)


class Toggle(QAbstractButton):
    """Animated on/off switch."""
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 20)
        self.pos_ = 1.0 if checked else 0.0
        self.anim = QVariantAnimation(self, duration=160, easingCurve=QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._move)
        self.toggled.connect(self._start)

    def _move(self, value) -> None:
        self.pos_ = float(value)
        self.update()

    def _start(self, checked: bool) -> None:
        self.anim.stop()
        self.anim.setStartValue(self.pos_)
        self.anim.setEndValue(1.0 if checked else 0.0)
        self.anim.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(t.SURFACE_3), QColor(t.ACCENT)
        mix = QColor.fromRgbF(off.redF() + (on.redF()-off.redF())*self.pos_, off.greenF() + (on.greenF()-off.greenF())*self.pos_,
                              off.blueF() + (on.blueF()-off.blueF())*self.pos_)
        if not self.isEnabled():
            mix.setAlpha(110)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        rounded(p, r, r.height()/2, mix, QColor(t.BORDER_STRONG) if self.pos_ < .5 else None)
        x = r.left() + 3 + (r.width() - r.height())*self.pos_
        p.setBrush(QColor("#0a1020") if self.pos_ > .5 else QColor(t.TEXT_2))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(x, r.top() + 3, r.height() - 6, r.height() - 6))


class Toast(QWidget):
    """Brief, fading notification anchored to the bottom-right of its parent."""
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.message, self.kind = "", "info"
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hold = QTimer(self, singleShot=True)
        self.hold.timeout.connect(lambda: self._fade(0, 400))
        self.hide()

    def show_message(self, message: str, kind: str = "info", ms: int = 2600) -> None:
        self.message, self.kind = message, kind
        p_font = t.font(9.5)
        from PySide6.QtGui import QFontMetrics
        width = min(520, QFontMetrics(p_font).horizontalAdvance(message) + 52)
        self.resize(width, 40)
        self.reposition()
        self.show()
        self.raise_()
        self._fade(1, 180)
        self.hold.start(ms)
        self.update()

    def reposition(self) -> None:
        parent = self.parentWidget()
        self.move(parent.width() - self.width() - 22, parent.height() - self.height() - 22)

    def _fade(self, target: float, ms: int) -> None:
        self.anim.stop()
        self.anim.setDuration(ms)
        self.anim.setStartValue(self.effect.opacity())
        self.anim.setEndValue(target)
        self.anim.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tint = {"info": t.ACCENT, "ok": t.GREEN, "warn": t.AMBER, "error": t.RED}.get(self.kind, t.ACCENT)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        rounded(p, r, 10, QColor(t.SURFACE_3), QColor(t.BORDER_STRONG))
        p.setBrush(QColor(tint))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(r.left() + 18, r.center().y()), 3.5, 3.5)
        text(p, r.adjusted(32, 0, -12, 0), self.message, t.TEXT, 9.5)


# --------------------------------------------------------------------------- dashboard tiles and charts

class StatTile(QWidget):
    """KPI tile with a tweened value and an optional sparkline."""
    def __init__(self, title: str, color: str = t.TEXT, formatter: Callable[[float], str] = fmt_count,
                 spark: bool = False, parent=None):
        super().__init__(parent)
        self.title, self.color, self.formatter = title, color, formatter
        self.caption = ""
        self.value = self.shown = 0.0
        self.text_override: str | None = None
        self.history: deque[float] | None = deque(maxlen=60) if spark else None
        self.anim = QVariantAnimation(self, duration=420, easingCurve=QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._tween)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _tween(self, value) -> None:
        self.shown = float(value)
        self.update()

    def set_value(self, value: float, caption: str | None = None, animate: bool = True) -> None:
        self.text_override = None
        if caption is not None:
            self.caption = caption
        if value != self.value:
            self.value = value
            self.anim.stop()
            if animate:
                self.anim.setStartValue(float(self.shown))
                self.anim.setEndValue(float(value))
                self.anim.start()
            else:
                self.shown = float(value)
        self.update()

    def set_text(self, value: str, caption: str | None = None) -> None:
        self.text_override = value
        if caption is not None:
            self.caption = caption
        self.update()

    def push(self, sample: float) -> None:
        if self.history is not None:
            self.history.append(sample)
            self.update()

    def reset(self) -> None:
        self.anim.stop()
        self.value = self.shown = 0.0
        self.caption = ""
        self.text_override = None
        if self.history is not None:
            self.history.clear()
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        rounded(p, r, 12, QColor(t.SURFACE), QColor(t.BORDER))
        if self.history and len(self.history) > 1:
            values = list(self.history)
            top = max(values) or 1
            area = QRectF(r.left() + 1, r.bottom() - 40, r.width() - 2, 38)
            path = QPainterPath(QPointF(area.left(), area.bottom()))
            for i, v in enumerate(values):
                path.lineTo(area.left() + area.width()*i/(len(values)-1), area.bottom() - area.height()*v/top*.85)
            line = QPainterPath(path)
            path.lineTo(area.right(), area.bottom())
            path.closeSubpath()
            gradient = QLinearGradient(0, area.top(), 0, area.bottom())
            gradient.setColorAt(0, t.color(self.color, 46))
            gradient.setColorAt(1, t.color(self.color, 0))
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(r, 12, 12)
            p.setClipPath(clip)
            p.fillPath(path, gradient)
            p.strokePath(line, QPen(t.color(self.color, 120), 1.2))
            p.restore()
        text(p, QRectF(r.left() + 16, r.top() + 12, r.width() - 32, 16), self.title.upper(), t.TEXT_3, 7.5, QFont.Weight.DemiBold)
        value = self.text_override if self.text_override is not None else self.formatter(self.shown)
        text(p, QRectF(r.left() + 16, r.top() + 32, r.width() - 32, 30), value, self.color, 18, QFont.Weight.DemiBold)
        if self.caption:
            text(p, QRectF(r.left() + 16, r.top() + 64, r.width() - 32, 18), self.caption, t.TEXT_3, 8.5)


class ProgressBar(QWidget, Animated):
    """Thin progress bar with a tweened fill and a shimmer for open-ended runs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_animation()
        self.value = 0.0
        self.indeterminate = False
        self.color = t.ACCENT
        self.setFixedHeight(6)
        self.anim = QVariantAnimation(self, duration=350, easingCurve=QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._tween)

    def _tween(self, value) -> None:
        self.value = float(value)
        self.update()

    def set_value(self, value: float) -> None:
        value = float(max(0.0, min(1.0, value)))
        self.anim.stop()
        self.anim.setStartValue(self.value)
        self.anim.setEndValue(value)
        self.anim.start()

    def set_running(self, running: bool, indeterminate: bool = False, color: str = t.ACCENT) -> None:
        self.indeterminate, self.color = indeterminate, color
        self.set_animating(running)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rounded(p, r, 3, QColor(t.SURFACE_3))
        if self.indeterminate and self._animating:
            x = ((self.phase*.55) % 1.4 - .2) * r.width()
            gradient = QLinearGradient(x - r.width()*.2, 0, x + r.width()*.2, 0)
            gradient.setColorAt(0, t.color(self.color, 0))
            gradient.setColorAt(.5, QColor(self.color))
            gradient.setColorAt(1, t.color(self.color, 0))
            rounded(p, r, 3, gradient)
            return
        if self.value > 0:
            fill = QRectF(r.left(), r.top(), max(r.height(), r.width()*self.value), r.height())
            gradient = QLinearGradient(fill.left(), 0, fill.right(), 0)
            gradient.setColorAt(0, t.color(self.color, 150))
            gradient.setColorAt(1, QColor(self.color))
            rounded(p, fill, 3, gradient)
            if self._animating:
                glow = (math.sin(self.phase*3) + 1)/2
                p.setBrush(t.color(self.color, int(60 + 80*glow)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(fill.right() - 3, fill.center().y()), 5, 5)


STAGE_COLORS = {"Waiting": t.BORDER_STRONG, "Bypassed": t.BORDER_STRONG, "Skipped": t.BORDER_STRONG,
                "Running": t.ACCENT, "Done": t.GREEN, "Failed": t.RED, "Stopped": t.AMBER}


class PipelineStrip(QWidget, Animated):
    """Connected stage nodes with live state and per-stage timing."""
    def __init__(self, stages: Iterable[str], parent=None):
        super().__init__(parent)
        self.init_animation()
        self.stages = list(stages)
        self.states = dict.fromkeys(self.stages, "Waiting")
        self.timing: dict[str, str] = {}
        self.setMinimumHeight(78)

    def set_states(self, states: dict[str, str], timing: dict[str, str]) -> None:
        self.states, self.timing = dict(states), dict(timing)
        self.set_animating("Running" in self.states.values())
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        n = len(self.stages)
        step = r.width()/n
        y = 18
        for i, stage in enumerate(self.stages):
            x = r.left() + step*i + step/2
            state = self.states.get(stage, "Waiting")
            color = QColor(STAGE_COLORS.get(state, t.BORDER_STRONG))
            if i < n - 1:
                done = state == "Done"
                pen = QPen(t.color(t.GREEN, 120) if done else QColor(t.BORDER_STRONG), 1.5)
                if state in {"Bypassed", "Skipped"}:
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(x + 10, y), QPointF(x + step - 10, y))
            if state == "Running":
                wave = (self.phase*.8 + i*.13) % 1.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(t.color(t.ACCENT, int(90*(1-wave))))
                p.drawEllipse(QPointF(x, y), 6 + 9*wave, 6 + 9*wave)
            if state in {"Waiting", "Bypassed", "Skipped"}:
                p.setPen(QPen(color, 1.5))
                p.setBrush(QColor(t.SURFACE))
                p.drawEllipse(QPointF(x, y), 6, 6)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawEllipse(QPointF(x, y), 6.5, 6.5)
                if state == "Done":
                    pen = QPen(QColor(t.SURFACE), 1.6)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawPolyline([QPointF(x - 2.6, y), QPointF(x - .6, y + 2), QPointF(x + 2.8, y - 2.2)])
            label_color = t.TEXT if state in {"Running", "Done", "Failed"} else t.TEXT_2
            text(p, QRectF(x - step/2, y + 14, step, 18), stage, label_color, 9,
                 QFont.Weight.DemiBold if state == "Running" else QFont.Weight.Normal, Qt.AlignmentFlag.AlignCenter)
            sub = self.timing.get(stage) or state.lower()
            text(p, QRectF(x - step/2, y + 31, step, 16), sub, STAGE_COLORS.get(state, t.TEXT_3) if state == "Running" else t.TEXT_3,
                 8, align=Qt.AlignmentFlag.AlignCenter)


class ConfidenceChart(QWidget):
    """Rolling match-confidence plot with threshold and above-threshold highlighting."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scores: deque[float] = deque(maxlen=240)
        self.threshold: float | None = None
        self.hover: float | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(190)

    def reset(self, threshold: float | None = None) -> None:
        self.scores.clear()
        self.threshold = threshold
        self.update()

    def push(self, score: float) -> None:
        self.scores.append(max(0.0, min(1.0, score)))
        self.update()

    def mouseMoveEvent(self, event) -> None:
        self.hover = event.position().x()
        self.update()

    def leaveEvent(self, _event) -> None:
        self.hover = None
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(30, 8, -6, -18)
        for frac in (0, .25, .5, .75, 1):
            y = r.bottom() - r.height()*frac
            p.setPen(QPen(QColor(t.BORDER), 1, Qt.PenStyle.SolidLine if frac in (0, 1) else Qt.PenStyle.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            text(p, QRectF(0, y - 8, 24, 16), f"{frac:.2f}".rstrip("0").rstrip(".") if frac not in (0, 1) else str(int(frac)),
                 t.TEXT_3, 7.5, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        threshold_y = None
        if self.threshold is not None:
            threshold_y = r.bottom() - r.height()*self.threshold
        values = list(self.scores)
        if len(values) < 2:
            text(p, r, "Awaiting samples…", t.TEXT_3, 9, align=Qt.AlignmentFlag.AlignCenter)
        else:
            cap = self.scores.maxlen or len(values)
            dx = r.width()/(cap - 1)
            x0 = r.right() - dx*(len(values) - 1)
            points = [QPointF(x0 + dx*i, r.bottom() - r.height()*v) for i, v in enumerate(values)]
            line = QPainterPath(points[0])
            for a, b in zip(points, points[1:]):
                mid = (a.x() + b.x())/2
                line.cubicTo(QPointF(mid, a.y()), QPointF(mid, b.y()), b)
            area = QPainterPath(line)
            area.lineTo(points[-1].x(), r.bottom())
            area.lineTo(points[0].x(), r.bottom())
            area.closeSubpath()
            gradient = QLinearGradient(0, r.top(), 0, r.bottom())
            gradient.setColorAt(0, t.color(t.ACCENT, 70))
            gradient.setColorAt(1, t.color(t.ACCENT, 0))
            p.fillPath(area, gradient)
            p.strokePath(line, QPen(QColor(t.ACCENT), 1.6))
            if threshold_y is not None:
                p.save()
                p.setClipRect(QRectF(r.left(), r.top() - 4, r.width(), threshold_y - r.top() + 4))
                hot = QLinearGradient(0, r.top(), 0, threshold_y)
                hot.setColorAt(0, t.color(t.VIOLET, 90))
                hot.setColorAt(1, t.color(t.VIOLET, 20))
                p.fillPath(area, hot)
                p.strokePath(line, QPen(QColor(t.VIOLET), 2))
                p.restore()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.VIOLET))
                for point, v in zip(points, values):
                    if v >= self.threshold:
                        p.drawEllipse(point, 2.6, 2.6)
            last = points[-1]
            p.setBrush(t.color(t.ACCENT, 60))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(last, 7, 7)
            p.setBrush(QColor(t.TEXT))
            p.drawEllipse(last, 3, 3)
            if self.hover is not None and r.left() <= self.hover <= r.right():
                index = round((self.hover - x0)/dx)
                if 0 <= index < len(points):
                    point = points[index]
                    p.setPen(QPen(t.color(t.TEXT_2, 90), 1, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(point.x(), r.top()), QPointF(point.x(), r.bottom()))
                    label = f"{values[index]:.3f}"
                    box = QRectF(min(point.x() + 8, r.right() - 56), max(r.top(), point.y() - 26), 52, 20)
                    rounded(p, box, 6, QColor(t.SURFACE_3), QColor(t.BORDER_STRONG))
                    text(p, box, label, t.TEXT, 8.5, align=Qt.AlignmentFlag.AlignCenter, mono=True)
        if threshold_y is not None:
            pen = QPen(QColor(t.AMBER), 1, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4, 4])
            p.setPen(pen)
            p.drawLine(QPointF(r.left(), threshold_y), QPointF(r.right(), threshold_y))
            text(p, QRectF(r.right() - 120, threshold_y - 17, 116, 14), f"threshold {self.threshold:.2f}", t.AMBER, 7.5,
                 align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        text(p, QRectF(r.left(), r.bottom() + 3, r.width(), 14), f"last {self.scores.maxlen} samples", t.TEXT_3, 7.5)
        text(p, QRectF(r.left(), r.bottom() + 3, r.width(), 14), "now", t.TEXT_3, 7.5,
             align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class Timeline(QWidget, Animated):
    """Media timeline: scan progress, raw hits, saved clip ranges and a hover readout."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_animation()
        self.start = 0.0
        self.end: float | None = None
        self.position = 0.0
        self.hits: list[float] = []
        self.clips: list[tuple[float, float]] = []
        self.hover: float | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(92)

    def reset(self) -> None:
        self.start, self.end, self.position = 0.0, None, 0.0
        self.hits.clear()
        self.clips.clear()
        self.update()

    def span(self) -> tuple[float, float]:
        end = self.end if self.end is not None and math.isfinite(self.end) else max(self.position, self.start + 1)
        return self.start, max(end, self.start + .001)

    def mouseMoveEvent(self, event) -> None:
        self.hover = event.position().x()
        self.update()

    def leaveEvent(self, _event) -> None:
        self.hover = None
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 0, -2, 0)
        start, end = self.span()
        def x_of(stamp: float) -> float:
            return r.left() + r.width()*min(1, max(0, (stamp - start)/(end - start)))
        track = QRectF(r.left(), 34, r.width(), 8)
        rounded(p, track, 4, QColor(t.SURFACE_3))
        if self.position > start:
            fill = QRectF(track.left(), track.top(), max(8, x_of(self.position) - track.left()), track.height())
            gradient = QLinearGradient(fill.left(), 0, fill.right(), 0)
            gradient.setColorAt(0, t.color(t.ACCENT, 90))
            gradient.setColorAt(1, QColor(t.ACCENT))
            rounded(p, fill, 4, gradient)
        for first, last in self.clips:
            box = QRectF(x_of(first), 14, max(4, x_of(last) - x_of(first)), 14)
            rounded(p, box, 4, t.color(t.AMBER, 60), QColor(t.AMBER))
        p.setPen(QPen(t.color(t.VIOLET, 200), 1.4))
        for stamp in self.hits[-2000:]:
            x = x_of(stamp)
            p.drawLine(QPointF(x, 46), QPointF(x, 56))
        if self.position > start:
            x = x_of(self.position)
            if self._animating:
                glow = (math.sin(self.phase*4) + 1)/2
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(t.color(t.ACCENT, int(40 + 70*glow)))
                p.drawEllipse(QPointF(x, track.center().y()), 9, 9)
            p.setPen(QPen(QColor(t.BG), 2))
            p.setBrush(QColor(t.TEXT))
            p.drawEllipse(QPointF(x, track.center().y()), 5, 5)
        open_end = self.end is None or not math.isfinite(self.end)
        text(p, QRectF(r.left(), 64, 120, 18), fmt_time(start), t.TEXT_3, 8.5, mono=True)
        text(p, QRectF(r.right() - 140, 64, 140, 18), ("live · " if open_end else "") + fmt_time(end), t.TEXT_3, 8.5,
             align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, mono=True)
        if self.hover is not None and r.left() <= self.hover <= r.right():
            stamp = start + (self.hover - r.left())/r.width()*(end - start)
            p.setPen(QPen(t.color(t.TEXT, 120), 1))
            p.drawLine(QPointF(self.hover, 8), QPointF(self.hover, 58))
            near = sum(1 for h in self.hits if abs(h - stamp) <= (end - start)*.01)
            inside = next((i for i, (a, b) in enumerate(self.clips) if a <= stamp <= b), None)
            label = fmt_time(stamp) + (f" · {near} hits" if near else "") + (f" · clip {inside+1}" if inside is not None else "")
            p.setFont(t.font(8.5, mono=True))
            width = p.fontMetrics().horizontalAdvance(label) + 16
            box = QRectF(min(max(r.left(), self.hover - width/2), r.right() - width), 62, width, 20)
            rounded(p, box, 6, QColor(t.SURFACE_3), QColor(t.BORDER_STRONG))
            text(p, box, label, t.TEXT, 8.5, align=Qt.AlignmentFlag.AlignCenter, mono=True)
        else:
            text(p, QRectF(r.left(), 64, r.width(), 18), f"scanned to {fmt_time(self.position)}", t.TEXT_2, 8.5,
                 align=Qt.AlignmentFlag.AlignCenter, mono=True)


SLOT_COLORS = {"Empty": t.TEXT_3, "Waiting": t.TEXT_3, "Bypassed": t.TEXT_3, "Downloading": t.AMBER,
               "Ready": t.GREEN, "Processing": t.ACCENT}
SLOT_ROLES = ("processing", "next", "after")


class SlotView(QWidget, Animated):
    """Positional X / Y / Z ring: X is being processed, Y is next, Z after that.

    When X finishes, the ring shifts left (Y→X, Z→Y) and a new download fills Z.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_animation()
        self.slots: list[dict | None] = [None, None, None]
        self.footer = ""
        self.bypassed = False
        self.head: int | None = None
        self.shift = 0.0
        self.shift_anim = QVariantAnimation(self, duration=420, easingCurve=QEasingCurve.Type.OutCubic)
        self.shift_anim.valueChanged.connect(self._shift)
        self.setMinimumHeight(128)

    def _shift(self, value) -> None:
        self.shift = float(value)
        self.update()

    def set_ring(self, slots: list[dict | None], footer: str = "", bypassed: bool = False) -> None:
        head = slots[0]["number"] if slots and slots[0] else None
        if head is not None and self.head is not None and head > self.head:
            self.shift_anim.stop()
            self.shift_anim.setStartValue(float(min(3, head - self.head)))
            self.shift_anim.setEndValue(0.0)
            self.shift_anim.start()
        self.head = head
        self.slots = (list(slots) + [None, None, None])[:3]
        self.footer, self.bypassed = footer, bypassed
        self.set_animating(any(s and s["state"] in {"Downloading", "Processing"} for s in self.slots))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        gap = 8
        height = r.height() - 24
        width = (r.width() - gap*2)/3
        p.save()
        p.setClipRect(r)
        for i, name in enumerate("XYZ"):
            slot = self.slots[i]
            state = "Bypassed" if self.bypassed else slot["state"] if slot else "Empty"
            color = SLOT_COLORS.get(state, t.TEXT_3)
            x = r.left() + (i + self.shift)*(width + gap)
            box = QRectF(x, r.top(), width, height)
            fade = max(0.0, 1.0 - max(0.0, x + width - r.right())/width) if self.shift else 1.0
            p.setOpacity(fade)
            active = state in {"Downloading", "Processing"}
            rounded(p, box, 10, QColor(t.BG), t.color(color, 150) if active else QColor(t.BORDER))
            inner = box.adjusted(11, 9, -11, -9)
            text(p, QRectF(inner.left(), inner.top(), 24, 20), name, t.TEXT, 13, QFont.Weight.DemiBold, mono=True)
            text(p, QRectF(inner.left() + 22, inner.top() + 1, inner.width() - 22, 18), SLOT_ROLES[i], t.TEXT_3, 7.5,
                 align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            p.setFont(t.font(9, QFont.Weight.DemiBold))
            label = p.fontMetrics().elidedText(state, Qt.TextElideMode.ElideRight, int(inner.width()))
            text(p, QRectF(inner.left(), inner.top() + 24, inner.width(), 18), label, color, 9, QFont.Weight.DemiBold)
            if slot and not self.bypassed:
                p.setFont(t.font(8))
                sub = p.fontMetrics().elidedText(f"Segment {slot['number']}", Qt.TextElideMode.ElideRight, int(inner.width()))
                text(p, QRectF(inner.left(), inner.top() + 42, inner.width(), 16), sub, t.TEXT_2, 8)
                detail = p.fontMetrics().elidedText(slot.get("detail", ""), Qt.TextElideMode.ElideRight, int(inner.width()))
                text(p, QRectF(inner.left(), inner.top() + 57, inner.width(), 16), detail, t.TEXT_3, 8)
            elif not self.bypassed:
                text(p, QRectF(inner.left(), inner.top() + 42, inner.width(), 16), "—", t.TEXT_3, 8)
            bar = QRectF(inner.left(), box.bottom() - 10, inner.width(), 3)
            if slot and not self.bypassed and state != "Empty":
                rounded(p, bar, 1.5, QColor(t.SURFACE_3))
                progress = slot.get("progress")
                if progress is not None:
                    if progress > 0:
                        rounded(p, QRectF(bar.left(), bar.top(), max(3, bar.width()*min(1, progress)), bar.height()), 1.5, QColor(color))
                elif active:
                    wave = (self.phase*.6 + i*.33) % 1.0
                    head = bar.left() + bar.width()*wave
                    gradient = QLinearGradient(head - 30, 0, head, 0)
                    gradient.setColorAt(0, t.color(color, 0))
                    gradient.setColorAt(1, QColor(color))
                    p.save()
                    p.setClipRect(bar)
                    rounded(p, QRectF(head - 30, bar.top(), 30, bar.height()), 1.5, gradient)
                    p.restore()
            p.setOpacity(1.0)
        p.restore()
        text(p, QRectF(r.left(), r.bottom() - 18, r.width(), 18),
             "Local file: read directly, no download ring" if self.bypassed else self.footer, t.TEXT_3, 8)


class TemplateBars(QWidget):
    """Per-template hit counts and best confidence."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[tuple[str, int, float]] = []
        self.setMinimumHeight(150)

    def set_rows(self, rows: list[tuple[str, int, float]]) -> None:
        self.rows = sorted(rows, key=lambda row: (-row[1], -row[2]))[:7]
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if not self.rows:
            text(p, r, "No template has matched yet", t.TEXT_3, 9, align=Qt.AlignmentFlag.AlignCenter)
            return
        top = max(row[1] for row in self.rows) or 1
        height = min(26, r.height()/max(1, len(self.rows)))
        for i, (name, hits, best) in enumerate(self.rows):
            y = r.top() + i*height
            label = name.rsplit(".", 1)[0]
            text(p, QRectF(r.left(), y, 96, height - 6), label, t.TEXT_2, 8.5)
            bar = QRectF(r.left() + 100, y + height/2 - 6, r.width() - 100 - 86, 6)
            rounded(p, bar, 3, QColor(t.SURFACE_3))
            fill = QRectF(bar.left(), bar.top(), max(6, bar.width()*hits/top), bar.height())
            gradient = QLinearGradient(fill.left(), 0, fill.right(), 0)
            gradient.setColorAt(0, t.color(t.VIOLET, 120))
            gradient.setColorAt(1, QColor(t.VIOLET))
            rounded(p, fill, 3, gradient)
            text(p, QRectF(bar.right() + 8, y, 34, height - 6), fmt_count(hits), t.TEXT, 8.5, QFont.Weight.DemiBold,
                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            text(p, QRectF(bar.right() + 44, y, 42, height - 6), f"{best:.2f}" if best else "—", t.TEXT_3, 8,
                 align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, mono=True)


class CropPreview(QWidget):
    """Proportional sketch of the calibrated frame and kill-feed crop."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame = (1920, 1080)
        self.crop: tuple[int, int, int, int] | None = None
        self.setMinimumHeight(170)

    def set_crop(self, frame: tuple[int, int], crop: tuple[int, int, int, int] | None) -> None:
        self.frame, self.crop = frame, crop
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.frame
        if w <= 0 or h <= 0:
            text(p, QRectF(self.rect()), "Not calibrated", t.TEXT_3, 9, align=Qt.AlignmentFlag.AlignCenter)
            return
        avail = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        scale = min(avail.width()/w, avail.height()/h)
        frame = QRectF(avail.left() + (avail.width() - w*scale)/2, avail.top() + (avail.height() - h*scale)/2, w*scale, h*scale)
        rounded(p, frame, 8, QColor(t.BG), QColor(t.BORDER_STRONG))
        p.setPen(QPen(t.color(t.BORDER_STRONG, 120), 1, Qt.PenStyle.DotLine))
        for i in (1, 2):
            p.drawLine(QPointF(frame.left() + frame.width()*i/3, frame.top()), QPointF(frame.left() + frame.width()*i/3, frame.bottom()))
            p.drawLine(QPointF(frame.left(), frame.top() + frame.height()*i/3), QPointF(frame.right(), frame.top() + frame.height()*i/3))
        text(p, frame.adjusted(10, 8, -10, -8), f"{w} × {h}", t.TEXT_3, 8, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, mono=True)
        if self.crop:
            x, y, cw, ch = self.crop
            box = QRectF(frame.left() + x*scale, frame.top() + y*scale, cw*scale, ch*scale)
            rounded(p, box, 3, t.color(t.ACCENT, 40), QColor(t.ACCENT))
            text(p, QRectF(box.left() - 140, box.bottom() + 4, box.width() + 140, 16), f"{cw}×{ch} @ {x},{y}", t.ACCENT, 8,
                 align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, mono=True)


class _ThumbLoader(QObject):
    loaded = Signal(str, bytes)


class StreamCard(QFrame):
    """YouTube source preview: thumbnail, title, channel and stream facts."""
    THUMB = QSize(208, 117)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.url = ""
        self.loader = _ThumbLoader(self)
        self.loader.loaded.connect(self._set_thumb)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 16, 18, 16)
        row.setSpacing(18)
        self.thumb = QLabel()
        self.thumb.setFixedSize(self.THUMB)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(f"background:{t.BG}; border:1px solid {t.BORDER}; border-radius:10px; color:{t.TEXT_3};")
        row.addWidget(self.thumb)
        column = QVBoxLayout()
        column.setSpacing(6)
        self.eyebrow = QLabel("YOUTUBE SOURCE", objectName="eyebrow")
        column.addWidget(self.eyebrow)
        self.title = QLabel(objectName="h2")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.title)
        self.channel = QLabel(objectName="muted")
        column.addWidget(self.channel)
        self.facts = QLabel(objectName="faint")
        self.facts.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.facts)
        self.link = QLabel(objectName="mono")
        self.link.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.link)
        column.addStretch(1)
        row.addLayout(column, 1)

    def resolving(self, url: str) -> None:
        self.url = url
        self.thumb.setPixmap(QPixmap())
        self.thumb.setText("…")
        self.eyebrow.setText("YOUTUBE SOURCE")
        self.title.setText("Checking stream URL…")
        self.channel.setText("")
        self.facts.setText(f"<span style='color:{t.TEXT_3}'>Asking YouTube for title, format and duration</span>")
        self.link.setText(url)

    def failed(self, message: str) -> None:
        self.thumb.setText("✕")
        self.title.setText("Stream could not be opened")
        self.facts.setText(f"<span style='color:{t.RED}'>{html.escape(message[:220])}</span>")

    def set_info(self, info: dict) -> None:
        self.title.setText(info.get("title") or "Untitled stream")
        self.channel.setText(info.get("channel") or info.get("uploader") or "")
        if info.get("is_live"):
            badge = f"<span style='color:{t.RED}'>● LIVE NOW</span>"
        elif info.get("was_live"):
            badge = f"<span style='color:{t.AMBER}'>● Past live stream</span>"
        else:
            badge = f"<span style='color:{t.GREEN}'>● Video</span>"
        parts = [badge]
        if info.get("duration") and not info.get("is_live"):
            parts.append(fmt_time(info["duration"]))
        if info.get("height"):
            parts.append(f"{info.get('width') or '?'}×{info['height']}" + (f" @ {info['fps']:g} fps" if info.get("fps") else ""))
        if info.get("view_count"):
            parts.append(f"{fmt_count(info['view_count'])} views")
        date = info.get("upload_date") or ""
        if len(date) == 8:
            parts.append(f"{date[:4]}-{date[4:6]}-{date[6:]}")
        self.facts.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts))
        self.link.setText(info.get("webpage_url") or self.url)
        thumb = info.get("thumbnail")
        if thumb:
            threading.Thread(target=self._fetch, args=(thumb, self.url), daemon=True).start()
        else:
            self.thumb.setText("no thumbnail")

    def _fetch(self, url: str, owner: str) -> None:
        import urllib.request
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                self.loader.loaded.emit(owner, response.read(5_000_000))
        except Exception:
            self.loader.loaded.emit(owner, b"")

    def _set_thumb(self, owner: str, data: bytes) -> None:
        if owner != self.url:
            return  # a newer run replaced this card
        image = QPixmap()
        if not data or not image.loadFromData(data):
            self.thumb.setText("no thumbnail")
            return
        size = self.THUMB
        scaled = image.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        result = QPixmap(size)
        result.fill(Qt.GlobalColor.transparent)
        p = QPainter(result)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, size.width(), size.height()), 10, 10)
        p.setClipPath(clip)
        p.drawPixmap(-(scaled.width() - size.width())//2, -(scaled.height() - size.height())//2, scaled)
        p.end()
        self.thumb.setText("")
        self.thumb.setPixmap(result)


class BufferView(QWidget):
    """Processed segments held on disk for clips that cross a segment boundary."""
    ROW = 22
    STRIP = 46

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[dict] = []
        self.awaiting: list[dict] = []
        self.next_number: int | None = None
        self.finished = False
        self._fit()

    def set_buffer(self, segments: list[dict], awaiting: list[dict], next_number: int | None) -> None:
        self.segments, self.awaiting, self.next_number = list(segments), list(awaiting), next_number
        self.finished = False
        self._fit()
        self.update()

    def clear(self, finished: bool = False) -> None:
        self.segments, self.awaiting, self.next_number, self.finished = [], [], None, finished
        self._fit()
        self.update()

    def _fit(self) -> None:
        rows = max(1, len(self.segments))
        self.setFixedHeight((self.STRIP if self.segments else 0) + rows*self.ROW + 22)

    @staticmethod
    def _mb(size: int) -> str:
        return f"{size/1_048_576:.1f} MB"

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if not self.segments:
            message = "Emptied — run finished" if self.finished else "Empty · no processed segment is being kept"
            text(p, QRectF(r.left(), r.top(), r.width(), self.ROW), message, t.TEXT_3, 8.5)
            return
        # Mini timeline: held segments, then the next segment (dashed) a waiting clip will cut into.
        first = self.segments[0]["start"]
        span_end = self.segments[-1]["end"]
        length = max(1.0, self.segments[-1]["end"] - self.segments[-1]["start"])
        show_next = self.next_number is not None
        total_end = span_end + (length if show_next else 0)
        clip_end = max((clip["end"] for clip in self.awaiting), default=span_end)
        total_end = max(total_end, min(clip_end, span_end + length*1.5))
        track = QRectF(r.left(), r.top() + 6, r.width(), 22)
        def x_of(stamp: float) -> float:
            return track.left() + track.width()*(stamp - first)/max(.001, total_end - first)
        for segment in self.segments:
            box = QRectF(x_of(segment["start"]) + 1, track.top(), x_of(segment["end"]) - x_of(segment["start"]) - 2, track.height())
            edge = segment["reason"] == "edge"
            rounded(p, box, 5, t.color(t.AMBER if edge else t.TEXT_3, 34), t.color(t.AMBER if edge else t.BORDER_STRONG, 160))
            text(p, box, str(segment["number"]), t.TEXT_2, 8, align=Qt.AlignmentFlag.AlignCenter, mono=True)
        if show_next:
            box = QRectF(x_of(span_end) + 1, track.top(), x_of(min(total_end, span_end + length)) - x_of(span_end) - 2, track.height())
            pen = QPen(QColor(t.BORDER_STRONG), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box, 5, 5)
            text(p, box, f"seg {self.next_number}", t.TEXT_3, 7.5, align=Qt.AlignmentFlag.AlignCenter, mono=True)
        for clip in self.awaiting:
            a, b = x_of(max(first, clip["start"])), x_of(min(total_end, clip["end"]))
            bar = QRectF(a, track.bottom() + 5, max(4, b - a), 5)
            color = t.AMBER if clip.get("confirmed") else t.VIOLET
            rounded(p, bar, 2.5, QColor(color))
        boundary = x_of(span_end)
        pen = QPen(t.color(t.TEXT, 120), 1, Qt.PenStyle.DotLine)
        p.setPen(pen)
        p.drawLine(QPointF(boundary, track.top() - 4), QPointF(boundary, track.bottom() + 12))
        # One row per held segment.
        y = r.top() + self.STRIP
        for segment in self.segments:
            row = QRectF(r.left(), y, r.width(), self.ROW)
            edge = segment["reason"] == "edge"
            color = t.AMBER if edge else t.TEXT_3
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(row.left() + 4, row.center().y()), 3, 3)
            text(p, QRectF(row.left() + 14, row.top(), 52, row.height()), f"seg {segment['number']}", t.TEXT, 8.5, mono=True)
            text(p, QRectF(row.left() + 66, row.top(), 92, row.height()),
                 f"{fmt_time(segment['start'])}–{fmt_time(segment['end'])}", t.TEXT_3, 8, mono=True)
            clip = segment.get("clip")
            if edge and clip:
                reason = (f"edge clip {fmt_time(clip['start'])}–{fmt_time(clip['end'])} → seg {segment['number']+1}"
                          if clip.get("confirmed") else "possible kill at the edge, confirming")
            else:
                reason = f"lead-in for seg {segment['number']+1}"
            size = self._mb(segment["size"])
            p.setFont(t.font(8))
            width = int(row.width() - 160 - 60)
            text(p, QRectF(row.left() + 160, row.top(), width, row.height()),
                 p.fontMetrics().elidedText(reason, Qt.TextElideMode.ElideRight, width), color if edge else t.TEXT_2, 8)
            text(p, QRectF(row.right() - 58, row.top(), 58, row.height()), size, t.TEXT_3, 8,
                 align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, mono=True)
            y += self.ROW
        total = sum(s["size"] for s in self.segments)
        edges = sum(1 for s in self.segments if s["reason"] == "edge")
        summary = f"{len(self.segments)} held · {self._mb(total)} on disk" + (f" · {edges} for edge clips" if edges else "")
        text(p, QRectF(r.left(), y + 2, r.width(), 18), summary, t.TEXT_3, 8)
