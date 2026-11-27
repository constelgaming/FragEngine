"""Shared dark theme for every FragEngine window."""
from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtGui import QColor, QFont, QFontDatabase

BG = "#0b0d10"
SURFACE = "#111419"
SURFACE_2 = "#161a21"
SURFACE_3 = "#1c212a"
BORDER = "#1f242d"
BORDER_STRONG = "#2b323d"
TEXT = "#e8ebef"
TEXT_2 = "#9ba4b0"
TEXT_3 = "#646d79"
ACCENT = "#7aa2ff"
ACCENT_HOVER = "#93b4ff"
GREEN = "#5fd39a"
AMBER = "#f2b866"
RED = "#f27a7a"
VIOLET = "#b392ff"
CYAN = "#5cc8e0"
CHEVRON = (Path(__file__).with_name("chevron.svg")).as_posix()


def color(value: str, alpha: int | None = None) -> QColor:
    result = QColor(value)
    if alpha is not None:
        result.setAlpha(alpha)
    return result


def _pick(candidates: tuple[str, ...], fallback: str) -> str:
    families = set(QFontDatabase.families())
    return next((name for name in candidates if name in families), fallback)


def ui_family() -> str:
    return _pick(("Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Cantarell", "Noto Sans"), "Sans Serif")


def mono_family() -> str:
    return _pick(("Cascadia Mono", "Cascadia Code", "JetBrains Mono", "Consolas", "SF Mono", "DejaVu Sans Mono"), "Monospace")


def font(size: float = 10, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False) -> QFont:
    result = QFont(mono_family() if mono else ui_family())
    result.setPointSizeF(size)
    result.setWeight(weight)
    result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return result


def stylesheet() -> str:
    family = ui_family()
    mono = mono_family()
    return f"""
* {{ outline: 0; }}
QWidget {{ background: transparent; color: {TEXT}; font-family: "{family}"; font-size: 10pt; }}
QMainWindow, QDialog, QMessageBox, #root {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 5px 8px; border-radius: 6px; }}

QFrame#card, QFrame#panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QGraphicsView#canvas {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QFrame#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: 0; }}

QLabel {{ background: transparent; }}
QLabel#muted, QLabel#hint {{ color: {TEXT_2}; }}
QLabel#faint {{ color: {TEXT_3}; }}
QLabel#h1 {{ font-size: 17pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11pt; font-weight: 600; }}
QLabel#eyebrow {{ color: {TEXT_3}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#mono {{ font-family: "{mono}"; font-size: 9pt; color: {TEXT_2}; }}

QPushButton {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: #232934; border-color: #38404d; }}
QPushButton:pressed {{ background: #1a1f27; }}
QPushButton:disabled {{ color: {TEXT_3}; background: {SURFACE_2}; border-color: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; color: #0a1020; border: 0; font-weight: 600; padding: 9px 20px; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: #26314a; color: #6c7a96; }}
QPushButton#danger {{ background: transparent; color: {RED}; border: 1px solid #4a2a2e; }}
QPushButton#danger:hover {{ background: #2a1719; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {TEXT_2}; padding: 5px 10px; }}
QPushButton#ghost:hover {{ background: {SURFACE_3}; color: {TEXT}; }}
QPushButton#chip {{ background: transparent; border: 1px solid {BORDER_STRONG}; border-radius: 12px; padding: 3px 11px; color: {TEXT_2}; font-size: 9pt; }}
QPushButton#chip:checked {{ background: #1d2740; border-color: #34497a; color: {ACCENT_HOVER}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {BG}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 7px 10px;
    selection-background-color: #2c3f66; selection-color: {TEXT};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: #3a4350; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_3}; border-color: {BORDER}; }}
QLineEdit[readOnly="true"] {{ color: {TEXT_2}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: 0; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{ image: url("{CHEVRON}"); width: 10px; height: 6px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 4px; selection-background-color: {SURFACE_3}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER_STRONG}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QPlainTextEdit#log {{ font-family: "{mono}"; font-size: 9pt; background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 10px; color: {TEXT_2}; }}

QScrollArea {{ border: 0; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #262c36; border-radius: 3px; min-height: 30px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: #343b47; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #262c36; border-radius: 3px; min-width: 30px; margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: 0; width: 0; height: 0; }}

QTableWidget {{ background: transparent; border: 0; gridline-color: transparent; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; }}
QTableWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {BORDER}; }}
QHeaderView::section {{ background: transparent; color: {TEXT_3}; border: 0; border-bottom: 1px solid {BORDER}; padding: 6px 8px; font-size: 8pt; font-weight: 600; }}
QTableCornerButton::section {{ background: transparent; border: 0; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QMessageBox QLabel {{ color: {TEXT}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
"""


def pad_spinboxes(root) -> None:
    """Fusion ignores QSS padding for a spin box's inner editor; set it directly."""
    from PySide6.QtWidgets import QAbstractSpinBox
    for box in root.findChildren(QAbstractSpinBox):
        box.lineEdit().setTextMargins(8, 0, 4, 0)


def apply(app) -> None:
    """Install the dark palette and stylesheet on a QApplication."""
    from PySide6.QtGui import QPalette

    app.setStyle("Fusion")
    palette = QPalette()
    for role, value in (
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, BG), (QPalette.ColorRole.AlternateBase, SURFACE),
        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, SURFACE_3),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#0a1020"), (QPalette.ColorRole.ToolTipBase, SURFACE_3),
        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, TEXT_3),
        (QPalette.ColorRole.Link, ACCENT),
    ):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    app.setFont(font(10))
    app.setStyleSheet(stylesheet())
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FragEngine.Desktop")
        except Exception:
            pass


def dark_title_bar(widget) -> None:
    """Ask Windows 10/11 to draw a dark native title bar for this window."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        caption = ctypes.c_int(0x00100D0B)  # COLORREF for BG (#0b0d10) as 0x00BBGGRR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:
        pass
