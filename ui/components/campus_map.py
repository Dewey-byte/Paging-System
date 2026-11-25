"""Visual campus overview: zones drawn as buildings colored by live state."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolTip, QWidget

from core.zone_manager import (ZONE_STATE_LABELS, ZS_BROADCASTING, ZS_DISABLED, ZS_OFFLINE, ZS_READY,
                               ZS_SIMULATED, ZS_UNVERIFIED, ZoneStatus)

from ..theme import theme
from .base import StatusDot

CANVAS_W, CANVAS_H = 100.0, 60.0
STATE_TONES = {ZS_BROADCASTING: "accent", ZS_READY: "success", ZS_SIMULATED: "purple", ZS_UNVERIFIED: "warning",
               ZS_OFFLINE: "danger", ZS_DISABLED: "neutral"}


class CampusMap(QWidget):
    zone_clicked = Signal(int)
    layout_changed = Signal(dict)

    def __init__(self, parent=None, editable: bool = False):
        super().__init__(parent)
        self.statuses: list[ZoneStatus] = []
        self.editable = editable
        self.selected: set[int] = set()
        self._rects: dict[int, list[float]] = {}
        self._hover: int | None = None
        self._drag: tuple[int, str, QPointF, list[float]] | None = None
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setMouseTracking(True)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        theme.changed.connect(self.update)

    # ---------------------------------------------------------------- data
    def set_statuses(self, statuses: list[ZoneStatus]) -> None:
        self.statuses = statuses
        if self._drag is None:
            self._rects = {}
            unplaced = []
            for status in statuses:
                z = status.zone
                if z.has_map_position:
                    self._rects[z.id] = [z.map_x, z.map_y, z.map_w, z.map_h]
                else:
                    unplaced.append(z.id)
            self._auto_place(unplaced)
        if any(s.state == ZS_BROADCASTING for s in statuses):
            if not self._timer.isActive():
                self._timer.start(40)
        else:
            self._timer.stop()
        self.update()

    def _auto_place(self, zone_ids: list[int]) -> None:
        """Place zones without saved coordinates in free grid cells."""
        w, h, gap = 22.0, 11.0, 2.5
        candidates = [(x, y) for y in [3 + (h + gap) * r for r in range(8)] for x in [3 + (w + gap) * c for c in range(4)]]
        for zone_id in zone_ids:
            for x, y in candidates:
                box = QRectF(x, y, w, h)
                if all(not box.adjusted(-0.5, -0.5, 0.5, 0.5).intersects(QRectF(*r)) for r in self._rects.values()):
                    self._rects[zone_id] = [x, y, w, h]
                    break
            else:
                self._rects[zone_id] = [3, 3, w, h]

    def canvas_height(self) -> float:
        bottom = max((r[1] + r[3] for r in self._rects.values()), default=CANVAS_H)
        return max(CANVAS_H, bottom + 3)

    # ------------------------------------------------------------ geometry
    def _transform(self) -> tuple[float, float, float, float]:
        """Independent x/y scales so the schematic map always fills the available area."""
        pad = 4
        sx = (self.width() - pad * 2) / CANVAS_W
        sy = (self.height() - pad * 2) / self.canvas_height()
        return sx, sy, pad, pad

    def _screen_rect(self, rect: list[float]) -> QRectF:
        sx, sy, ox, oy = self._transform()
        return QRectF(ox + rect[0] * sx, oy + rect[1] * sy, rect[2] * sx, rect[3] * sy)

    def _zone_at(self, pos: QPointF) -> int | None:
        for status in reversed(self.statuses):
            rect = self._rects.get(status.zone.id)
            if rect and self._screen_rect(rect).contains(pos):
                return status.zone.id
        return None

    # --------------------------------------------------------------- mouse
    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._drag is not None:
            zone_id, mode, start, original = self._drag
            sx, sy, _, _ = self._transform()
            dx, dy = (pos.x() - start.x()) / sx, (pos.y() - start.y()) / sy
            rect = list(original)
            if mode == "move":
                rect[0] = round(max(0, min(CANVAS_W - rect[2], original[0] + dx)))
                rect[1] = round(max(0, min(self.canvas_height() - rect[3], original[1] + dy)))
            else:
                rect[2] = round(max(10, min(CANVAS_W - rect[0], original[2] + dx)))
                rect[3] = round(max(7, original[3] + dy))
            self._rects[zone_id] = rect
            self.update()
            return
        hover = self._zone_at(pos)
        if hover != self._hover:
            self._hover = hover
            self.update()
        if hover is not None:
            status = next(s for s in self.statuses if s.zone.id == hover)
            if self.editable:
                r = self._screen_rect(self._rects[hover])
                near_corner = (r.bottomRight() - pos).manhattanLength() < 18
                self.setCursor(Qt.CursorShape.SizeFDiagCursor if near_corner else Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"<b>{status.zone.name}</b><br>{ZONE_STATE_LABELS[status.state]}<br>{status.detail}", self)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        zone_id = self._zone_at(event.position())
        if zone_id is None:
            return
        if self.editable:
            r = self._screen_rect(self._rects[zone_id])
            mode = "resize" if (r.bottomRight() - event.position()).manhattanLength() < 18 else "move"
            self._drag = (zone_id, mode, event.position(), list(self._rects[zone_id]))
        else:
            self.zone_clicked.emit(zone_id)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            zone_id, _, _, original = self._drag
            self._drag = None
            if self._rects[zone_id] != original:
                self.layout_changed.emit({zone_id: tuple(self._rects[zone_id])})

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.025) % 1.0
        self.update()

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sx, sy, ox, oy = self._transform()
        scale = min(sx, sy * 1.6)
        canvas = QRectF(ox, oy, CANVAS_W * sx, self.canvas_height() * sy)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.c("map_bg"))
        painter.drawRoundedRect(canvas, 14, 14)
        # subtle dot grid
        painter.setPen(QPen(theme.c("map_grid"), 1.6))
        step = 18.0
        y = canvas.top() + step
        while y < canvas.bottom() - 2:
            x = canvas.left() + step
            while x < canvas.right() - 2:
                painter.drawPoint(QPointF(x, y))
                x += step
            y += step
        # decorative walkways
        painter.setPen(QPen(theme.c("map_path"), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for frac in (0.36, 0.69):
            yy = canvas.top() + canvas.height() * frac
            painter.drawLine(QPointF(canvas.left() + 12, yy), QPointF(canvas.right() - 12, yy))
        for frac in (0.32, 0.68):
            xx = canvas.left() + canvas.width() * frac
            painter.drawLine(QPointF(xx, canvas.top() + 12), QPointF(xx, canvas.bottom() - 12))

        name_font = QFont(self.font())
        name_font.setBold(True)
        name_font.setPointSizeF(max(8.0, min(10.5, scale * 0.9)))
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, min(9.0, scale * 0.75)))

        for status in self.statuses:
            rect = self._rects.get(status.zone.id)
            if not rect:
                continue
            r = self._screen_rect(rect).adjusted(2, 2, -2, -2)
            zone_color = QColor(status.zone.color or "#2563EB")
            tone = STATE_TONES.get(status.state, "neutral")
            _, tone_fg, tone_solid = theme.tone(tone)
            hovered = status.zone.id == self._hover
            disabled = status.state == ZS_DISABLED
            painter.setOpacity(0.55 if disabled else 1.0)
            if status.state == ZS_BROADCASTING:
                for k in range(2):
                    phase = (self._phase + k * 0.5) % 1.0
                    halo = QColor(tone_solid)
                    halo.setAlphaF(0.35 * (1 - phase))
                    painter.setPen(QPen(halo, 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    grow = phase * 12
                    painter.drawRoundedRect(r.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)
            fill = QColor(theme.c("surface"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(r, 10, 10)
            tint = QColor(tone_solid if status.state == ZS_BROADCASTING else zone_color)
            tint.setAlphaF(0.22 if status.state == ZS_BROADCASTING else (0.14 if theme.is_dark else 0.09))
            painter.setBrush(tint)
            painter.drawRoundedRect(r, 10, 10)
            border = QColor(tone_solid) if status.state in (ZS_BROADCASTING, ZS_OFFLINE) else theme.c("border_strong")
            if hovered or status.zone.id in self.selected:
                border = theme.c("accent")
            pen = QPen(border, 2 if (hovered or status.state == ZS_BROADCASTING) else 1.2)
            if status.state == ZS_OFFLINE:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(r, 10, 10)
            # color strip
            strip = QPainterPath()
            strip.addRoundedRect(QRectF(r.left() + 6, r.top() + 8, 4, max(8.0, r.height() - 16)), 2, 2)
            painter.fillPath(strip, zone_color)
            # text
            text_left = r.left() + 16
            avail = r.width() - 24
            painter.setFont(name_font)
            painter.setPen(theme.c("text"))
            name = QFontMetrics(name_font).elidedText(status.zone.name, Qt.TextElideMode.ElideRight, int(avail))
            name_h = QFontMetrics(name_font).height()
            small_h = QFontMetrics(small).height()
            block = name_h + small_h + 2
            top = r.center().y() - block / 2
            painter.drawText(QRectF(text_left, top, avail, name_h), Qt.AlignmentFlag.AlignLeft, name)
            painter.setFont(small)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tone_solid)
            dot_y = top + name_h + 2 + small_h / 2
            painter.drawEllipse(QPointF(text_left + 4, dot_y), 3.5, 3.5)
            painter.setPen(tone_fg)
            state_text = ZONE_STATE_LABELS[status.state]
            if status.state == ZS_BROADCASTING:
                state_text = "ON AIR"
            painter.drawText(QRectF(text_left + 12, top + name_h + 2, avail - 12, small_h),
                             Qt.AlignmentFlag.AlignLeft, state_text)
            painter.setOpacity(1.0)
            if self.editable and hovered:
                painter.setPen(QPen(theme.c("accent"), 2))
                corner = r.bottomRight()
                painter.drawLine(corner + QPointF(-10, -3), corner + QPointF(-3, -10))
                painter.drawLine(corner + QPointF(-6, -3), corner + QPointF(-3, -6))
        if not self.statuses:
            painter.setPen(theme.c("text_muted"))
            painter.drawText(canvas, Qt.AlignmentFlag.AlignCenter, "No audio zones configured yet")


def legend_row(states: list[str] | None = None) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(14)
    for state in states or [ZS_BROADCASTING, ZS_READY, ZS_SIMULATED, ZS_UNVERIFIED, ZS_OFFLINE, ZS_DISABLED]:
        item = QHBoxLayout()
        item.setSpacing(4)
        item.addWidget(StatusDot(STATE_TONES[state], 9))
        text = QLabel("On air" if state == ZS_BROADCASTING else ZONE_STATE_LABELS[state])
        text.setProperty("role", "caption")
        item.addWidget(text)
        layout.addLayout(item)
    layout.addStretch(1)
    return widget
