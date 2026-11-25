"""Interactive controls: toggle switch, hold-to-confirm, level meter, segmented control..."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Property,
                            Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QSlider, QWidget)

from ..icons import icon, pixmap
from ..theme import theme
from .base import label


class ToggleSwitch(QAbstractButton):
    """Animated on/off switch."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        theme.changed.connect(self.update)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt API
        super().setChecked(checked)
        self._offset = 1.0 if checked else 0.0
        self.update()

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, get_offset, set_offset)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = theme.c("border_strong"), theme.c("accent")
        if not self.isEnabled():
            on = theme.c("accent_disabled")
            off = theme.c("border")
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), self.height() / 2, self.height() / 2)
        d = self.height() - 6
        x = 3 + (self.width() - d - 6) * self._offset
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(x, 3, d, d))


class HoldButton(QPushButton):
    """Must be pressed and held for ``hold_seconds`` to fire ``confirmed`` (accident-proof)."""

    confirmed = Signal()

    def __init__(self, text: str, hold_seconds: float = 1.5, color: str = "#DC2626", parent=None):
        super().__init__(text, parent)
        self.hold_seconds = max(0.3, hold_seconds)
        self._color = QColor(color)
        self._progress = 0.0
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(58)
        font = QFont(self.font())
        font.setPointSizeF(12)
        font.setBold(True)
        self.setFont(font)
        self.setStyleSheet("QPushButton { border: none; background: transparent; color: white; }")

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._started = time.monotonic()
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._cancel()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.isAutoRepeat():
            self._started = time.monotonic()
            self._timer.start()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.isAutoRepeat():
            self._cancel()
            return
        super().keyReleaseEvent(event)

    def _cancel(self) -> None:
        self._timer.stop()
        self._progress = 0.0
        self.update()

    def _tick(self) -> None:
        self._progress = min(1.0, (time.monotonic() - self._started) / self.hold_seconds)
        self.update()
        if self._progress >= 1.0:
            self._timer.stop()
            self._progress = 0.0
            self.update()
            self.confirmed.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        base = QColor(self._color) if self.isEnabled() else theme.c("border_strong")
        dark = base.darker(135)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base if not self.underMouse() else base.darker(108))
        painter.drawRoundedRect(r, 14, 14)
        if self._progress > 0:
            path = QPainterPath()
            path.addRoundedRect(r, 14, 14)
            painter.setClipPath(path)
            painter.fillRect(QRectF(r.left(), r.top(), r.width() * self._progress, r.height()), dark)
            painter.setClipping(False)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(self.font())
        text = self.text() if self._progress == 0 else f"Keep holding... {int(self._progress * 100)}%"
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


class LevelMeter(QWidget):
    """Segmented audio level meter with peak hold (input value 0..1 linear amplitude)."""

    def __init__(self, segments: int = 28, vertical: bool = False, parent=None):
        super().__init__(parent)
        self.segments = segments
        self.vertical = vertical
        self._level = 0.0
        self._peak = 0.0
        self._peak_time = 0.0
        if vertical:
            self.setMinimumSize(18, 120)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setMinimumSize(120, 14)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    @staticmethod
    def to_meter(amplitude: float) -> float:
        if amplitude <= 1e-5:
            return 0.0
        db = 20 * math.log10(min(1.0, amplitude))
        return max(0.0, min(1.0, (db + 60) / 60))

    def set_level(self, amplitude: float) -> None:
        value = self.to_meter(amplitude)
        # fast attack, smooth release
        self._level = value if value > self._level else self._level * 0.82 + value * 0.18
        now = time.monotonic()
        if value >= self._peak or now - self._peak_time > 1.2:
            self._peak = value
            self._peak_time = now
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = self.segments
        gap = 2.0
        length = self.height() if self.vertical else self.width()
        thickness = self.width() if self.vertical else self.height()
        seg = (length - gap * (n - 1)) / n
        lit = int(round(self._level * n))
        peak_index = min(n - 1, int(self._peak * n))
        off = theme.c("track")
        for i in range(n):
            frac = i / max(1, n - 1)
            if frac > 0.86:
                color = theme.c("danger")
            elif frac > 0.7:
                color = theme.c("warning")
            else:
                color = theme.c("success")
            on = i < lit or (i == peak_index and self._peak > 0.02)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color if on else off)
            pos = i * (seg + gap)
            if self.vertical:
                rect = QRectF(0, length - pos - seg, thickness, seg)
            else:
                rect = QRectF(pos, 0, seg, thickness)
            painter.drawRoundedRect(rect, 2, 2)


class SegmentedControl(QWidget):
    """Exclusive set of options rendered as a pill-style segmented button group."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]] | list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for option in options:
            key, text = option[0], option[1]
            icon_name = option[2] if len(option) > 2 else None
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setObjectName("SegmentButton")
            if icon_name:
                btn.setProperty("seg_icon", icon_name)
            self.group.addButton(btn)
            layout.addWidget(btn)
            self.buttons[key] = btn
            btn.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        if self.buttons:
            next(iter(self.buttons.values())).setChecked(True)
        theme.changed.connect(self._restyle)
        self.group.buttonToggled.connect(lambda *_: self._restyle())
        self._restyle()

    def value(self) -> str:
        for key, btn in self.buttons.items():
            if btn.isChecked():
                return key
        return ""

    def set_value(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def _restyle(self) -> None:
        self.setStyleSheet(
            f"QWidget#Segmented {{ background: {theme.hex('surface_alt')}; border: 1px solid {theme.hex('border')};"
            f" border-radius: 10px; }}"
            f"QPushButton#SegmentButton {{ border: none; background: transparent; padding: 7px 14px;"
            f" border-radius: 8px; color: {theme.hex('text_muted')}; font-weight: 600; }}"
            f"QPushButton#SegmentButton:hover {{ color: {theme.hex('text')}; }}"
            f"QPushButton#SegmentButton:checked {{ background: {theme.hex('surface')}; color: {theme.hex('text')};"
            f" border: 1px solid {theme.hex('border')}; }}")
        for btn in self.buttons.values():
            name = btn.property("seg_icon")
            if name:
                btn.setIcon(icon(name, theme.hex("accent") if btn.isChecked() else theme.hex("text_muted"), 15))


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "Search...", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(220)
        self._action = self.addAction(icon("search", theme.hex("text_faint"), 16),
                                      QLineEdit.ActionPosition.LeadingPosition)
        theme.changed.connect(lambda: self._action.setIcon(icon("search", theme.hex("text_faint"), 16)))


class Spinner(QWidget):
    """Indeterminate progress indicator."""

    def __init__(self, size: int = 22, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self) -> None:
        self.show()
        self._timer.start(16)

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(theme.c("track"), 3)
        painter.setPen(pen)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.drawEllipse(r)
        pen.setColor(theme.c("accent"))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(r, -self._angle * 16, 100 * 16)


class SliderField(QWidget):
    """Slider with an icon and a live value label (e.g. volume, gain)."""

    valueChanged = Signal(int)

    def __init__(self, minimum: int = 0, maximum: int = 100, value: int = 50, suffix: str = "%",
                 icon_name: str | None = None, formatter=None, parent=None):
        super().__init__(parent)
        self._formatter = formatter or (lambda v: f"{v}{suffix}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._icon_name = icon_name
        if icon_name:
            self.icon = QLabel()
            self.icon.setFixedSize(18, 18)
            layout.addWidget(self.icon)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.slider, 1)
        self.value_label = label(self._formatter(value), "h3")
        self.value_label.setMinimumWidth(52)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.value_label)
        self.slider.valueChanged.connect(self._changed)
        theme.changed.connect(self._restyle)
        self._restyle()

    def _restyle(self):
        if self._icon_name:
            self.icon.setPixmap(pixmap(self._icon_name, theme.hex("text_muted"), 18))

    def _changed(self, value: int) -> None:
        self.value_label.setText(self._formatter(value))
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, value: int) -> None:  # noqa: N802
        self.slider.setValue(int(value))


SWATCHES = ["#2563EB", "#0EA5E9", "#14B8A6", "#16A34A", "#84CC16", "#F59E0B", "#EA580C", "#EF4444", "#DC2626",
            "#EC4899", "#8B5CF6", "#7C3AED", "#64748B", "#172554"]


class ColorSwatches(QWidget):
    changed = Signal(str)

    def __init__(self, value: str = "#2563EB", colors: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._colors = colors or SWATCHES
        self._value = value
        self.setFixedHeight(30)
        self.setMinimumWidth(len(self._colors) * 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def value(self) -> str:
        return self._value

    def set_value(self, color: str) -> None:
        self._value = color
        self.update()

    def mousePressEvent(self, event):
        index = int(event.position().x() // 30)
        if 0 <= index < len(self._colors):
            self._value = self._colors[index]
            self.changed.emit(self._value)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, color in enumerate(self._colors):
            center = QPointF(i * 30 + 14, 15)
            if color.lower() == self._value.lower():
                painter.setPen(QPen(QColor(color), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(center, 13, 13)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(center, 9.5, 9.5)
