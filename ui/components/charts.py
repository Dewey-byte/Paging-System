"""Lightweight custom-painted charts (bar chart, sparkline, donut)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..theme import theme


class BarChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, float, str]] = []  # label, value, tooltip
        self.highlight_index = -1
        self._hover = -1
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        theme.changed.connect(self.update)

    def set_data(self, items: list[tuple[str, float, str]], highlight_index: int = -1) -> None:
        self.items = items
        self.highlight_index = highlight_index if highlight_index >= 0 else len(items) - 1
        self.update()

    def _bars(self) -> list[QRectF]:
        if not self.items:
            return []
        left, right, top, bottom = 30, 6, 16, 26
        width = self.width() - left - right
        height = self.height() - top - bottom
        n = len(self.items)
        slot = width / n
        bar_w = min(38.0, slot * 0.56)
        peak = max(1.0, max(v for _, v, _ in self.items))
        rects = []
        for i, (_, value, _) in enumerate(self.items):
            h = height * (value / peak)
            x = left + slot * i + (slot - bar_w) / 2
            rects.append(QRectF(x, top + height - h, bar_w, max(h, 2)))
        return rects

    def mouseMoveEvent(self, event):
        hover = -1
        for i, rect in enumerate(self._bars()):
            if rect.left() - 6 <= event.position().x() <= rect.right() + 6:
                hover = i
        if hover != self._hover:
            self._hover = hover
            self.update()
            if hover >= 0:
                QToolTip.showText(event.globalPosition().toPoint(), self.items[hover][2], self)

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.items:
            return
        left, top, bottom = 30, 16, 26
        height = self.height() - top - bottom
        peak = max(1.0, max(v for _, v, _ in self.items))
        small = QFont(self.font())
        small.setPointSizeF(8)
        painter.setFont(small)
        for i in range(4):
            y = top + height * i / 3
            painter.setPen(QPen(theme.c("border_soft"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(left, y), QPointF(self.width() - 6, y))
            painter.setPen(theme.c("text_faint"))
            value = peak * (1 - i / 3)
            painter.drawText(QRectF(0, y - 8, left - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{value:.0f}")
        for i, rect in enumerate(self._bars()):
            highlighted = i == self.highlight_index or i == self._hover
            color = theme.c("accent") if highlighted else theme.c("accent", 90 if theme.is_dark else 70)
            path = QPainterPath()
            radius = min(6.0, rect.width() / 2)
            path.addRoundedRect(rect, radius, radius)
            painter.fillPath(path, color)
            painter.setPen(theme.c("text_muted") if not highlighted else theme.c("text"))
            painter.drawText(QRectF(rect.center().x() - 30, self.height() - bottom + 6, 60, 16),
                             Qt.AlignmentFlag.AlignHCenter, self.items[i][0])
            if self.items[i][1] and (highlighted or rect.height() > 18):
                painter.setPen(theme.c("text_secondary"))
                painter.drawText(QRectF(rect.center().x() - 30, rect.top() - 16, 60, 14),
                                 Qt.AlignmentFlag.AlignHCenter, f"{self.items[i][1]:.0f}")


class Sparkline(QWidget):
    def __init__(self, tone: str = "accent", parent=None):
        super().__init__(parent)
        self.values: list[float] = []
        self.tone = tone
        self.setFixedHeight(34)
        theme.changed.connect(self.update)

    def set_values(self, values: list[float]) -> None:
        self.values = list(values)
        self.update()

    def paintEvent(self, event):
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, _, solid = theme.tone(self.tone)
        peak = max(1.0, max(self.values))
        w, h = self.width() - 4, self.height() - 6
        points = [QPointF(2 + w * i / (len(self.values) - 1), 3 + h - h * v / peak) for i, v in enumerate(self.values)]
        path = QPainterPath(points[0])
        for a, b in zip(points, points[1:]):
            mid = (a.x() + b.x()) / 2
            path.cubicTo(QPointF(mid, a.y()), QPointF(mid, b.y()), b)
        fill = QPainterPath(path)
        fill.lineTo(points[-1].x(), self.height())
        fill.lineTo(points[0].x(), self.height())
        fill.closeSubpath()
        gradient = QLinearGradient(0, 0, 0, self.height())
        top = QColor(solid)
        top.setAlpha(60)
        bottom = QColor(solid)
        bottom.setAlpha(0)
        gradient.setColorAt(0, top)
        gradient.setColorAt(1, bottom)
        painter.fillPath(fill, gradient)
        painter.setPen(QPen(solid, 2))
        painter.drawPath(path)
        painter.setBrush(solid)
        painter.drawEllipse(points[-1], 3, 3)


class DonutChart(QWidget):
    def __init__(self, parent=None, thickness: int = 14):
        super().__init__(parent)
        self.segments: list[tuple[float, str]] = []  # value, color token or hex
        self.center_text = ""
        self.center_caption = ""
        self.thickness = thickness
        self.setMinimumSize(120, 120)
        theme.changed.connect(self.update)

    def set_data(self, segments: list[tuple[float, str]], text: str = "", caption: str = "") -> None:
        self.segments = segments
        self.center_text = text
        self.center_caption = caption
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - self.thickness - 4
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        painter.setPen(QPen(theme.c("track"), self.thickness))
        painter.drawArc(rect, 0, 360 * 16)
        total = sum(v for v, _ in self.segments) or 1
        start = 90 * 16
        for value, color in self.segments:
            if value <= 0:
                continue
            span = -int(360 * 16 * value / total)
            pen = QPen(QColor(color) if color.startswith("#") else theme.c(color), self.thickness)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(rect, start, span)
            start += span
        font = QFont(self.font())
        font.setPointSizeF(16)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(theme.c("text"))
        painter.drawText(rect.adjusted(0, -10, 0, -10), Qt.AlignmentFlag.AlignCenter, self.center_text)
        font.setPointSizeF(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(theme.c("text_muted"))
        painter.drawText(rect.adjusted(0, 26, 0, 0), Qt.AlignmentFlag.AlignCenter, self.center_caption)
