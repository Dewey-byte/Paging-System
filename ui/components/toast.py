"""Non-blocking toast notifications stacked in the top-right corner of the window."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..icons import icon, pixmap
from ..theme import theme

KIND_STYLE = {
    "success": ("success", "check_circle"),
    "info": ("accent", "info"),
    "warning": ("warning", "alert"),
    "error": ("danger", "x_circle"),
}


class Toast(QFrame):
    closed = Signal(object)
    WIDTH = 360

    def __init__(self, parent: QWidget, kind: str, title: str, message: str, duration_ms: int):
        super().__init__(parent)
        self.kind = kind if kind in KIND_STYLE else "info"
        tone, icon_name = KIND_STYLE[self.kind]
        self._tone = tone
        self.setFixedWidth(self.WIDTH)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 10, 14)
        layout.setSpacing(12)
        glyph = QLabel()
        glyph.setPixmap(pixmap(icon_name, theme.hex(tone), 20))
        glyph.setFixedSize(22, 22)
        layout.addWidget(glyph, alignment=Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        heading = QLabel(title)
        heading.setStyleSheet(f"font-weight: 700; color: {theme.hex('text')}; background: transparent;")
        heading.setWordWrap(True)
        text.addWidget(heading)
        if message:
            body = QLabel(message)
            body.setWordWrap(True)
            body.setStyleSheet(f"color: {theme.hex('text_muted')}; font-size: 9pt; background: transparent;")
            text.addWidget(body)
        layout.addLayout(text, 1)
        close = QPushButton()
        close.setIcon(icon("x", theme.hex("text_faint"), 14))
        close.setFixedSize(24, 24)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet("QPushButton { border: none; background: transparent; border-radius: 6px; }"
                            f"QPushButton:hover {{ background: {theme.hex('surface_hover')}; }}")
        close.clicked.connect(self.dismiss)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._move = QPropertyAnimation(self, b"pos", self)
        self._move.setDuration(220)
        self._move.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._duration = duration_ms
        self._closing = False
        self.adjustSize()

    def start(self) -> None:
        self.show()
        self.raise_()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(self._duration)

    def slide_to(self, point: QPoint, animate: bool = True) -> None:
        if not animate or not self.isVisible():
            self.move(point)
            return
        self._move.stop()
        self._move.setStartValue(self.pos())
        self._move.setEndValue(point)
        self._move.start()

    def enterEvent(self, event):
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._closing:
            self._timer.start(1800)
        super().leaveEvent(event)

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._finish)
        self._fade.start()

    def _finish(self) -> None:
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 2, -3, -5)
        for i in range(5, 0, -1):
            shadow = theme.c("shadow")
            shadow.setAlphaF(0.025 * (6 - i) * (2 if theme.is_dark else 1))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawRoundedRect(r.adjusted(-i * 0.6, -i * 0.3, i * 0.6, i * 0.9), 12 + i * 0.5, 12 + i * 0.5)
        painter.setBrush(theme.c("surface"))
        painter.setPen(QPen(theme.c("border"), 1))
        painter.drawRoundedRect(r, 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.c(self._tone))
        painter.drawRoundedRect(QRectF(r.left() + 6, r.top() + 12, 4, r.height() - 24), 2, 2)


class ToastManager(QObject):
    MAX_VISIBLE = 4

    def __init__(self, host: QWidget, top_offset: int = 76):
        super().__init__(host)
        self.host = host
        self.top_offset = top_offset
        self.toasts: list[Toast] = []
        host.installEventFilter(self)

    def show(self, kind: str, title: str, message: str = "") -> None:
        duration = 6500 if kind in ("error", "warning") else 4200
        toast = Toast(self.host, kind, title, message, duration)
        toast.closed.connect(self._removed)
        self.toasts.insert(0, toast)
        while len(self.toasts) > self.MAX_VISIBLE:
            self.toasts[-1].dismiss()
            self.toasts.pop()
        toast.adjustSize()
        x = self.host.width() - Toast.WIDTH - 22
        toast.move(x, self.top_offset)
        toast.start()
        self._layout(animate=True)

    def _removed(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
        self._layout(animate=True)

    def _layout(self, animate: bool = False) -> None:
        y = self.top_offset
        x = self.host.width() - Toast.WIDTH - 22
        for toast in self.toasts:
            toast.slide_to(QPoint(x, y), animate)
            toast.raise_()
            y += toast.height() + 8

    def eventFilter(self, obj, event):
        if obj is self.host and event.type() == QEvent.Type.Resize:
            self._layout(animate=False)
        return False
