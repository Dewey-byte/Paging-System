"""Zone picker with live device state (used by live paging, announcements, alarms, schedules)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from core.zone_manager import ZONE_STATE_LABELS, ZS_BROADCASTING, ZS_DISABLED, ZoneStatus

from ..icons import pixmap
from ..theme import theme
from .base import button, label
from .campus_map import STATE_TONES
from .flow_layout import ResponsiveGrid


class ZoneTile(QAbstractButton):
    def __init__(self, status: ZoneStatus | None, campus: bool = False, parent=None):
        super().__init__(parent)
        self.status = status
        self.campus = campus
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(62 if not campus else 56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.subtitle = ""
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(200, 62 if not self.campus else 56)

    def set_status(self, status: ZoneStatus) -> None:
        self.status = status
        self.setEnabled(status.state != ZS_DISABLED)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        checked = self.isChecked()
        hovered = self.underMouse() and self.isEnabled()
        fill = theme.c("accent_soft") if checked else theme.c("surface")
        painter.setPen(QPen(theme.c("accent") if checked else (theme.c("border_hover") if hovered else
                                                               theme.c("border")), 1.6 if checked else 1))
        painter.setBrush(fill)
        if not self.isEnabled():
            painter.setOpacity(0.5)
        painter.drawRoundedRect(r, 11, 11)
        x = r.left() + 14
        bold = QFont(self.font())
        bold.setBold(True)
        small = QFont(self.font())
        small.setPointSizeF(8.5)
        if self.campus:
            painter.drawPixmap(QPointF(x, r.center().y() - 11), pixmap("globe", theme.hex("accent"), 22))
            x += 34
            painter.setFont(bold)
            painter.setPen(theme.c("text"))
            painter.drawText(QRectF(x, r.top() + 9, r.width() - x - 40, 20), Qt.AlignmentFlag.AlignLeft, "Entire Campus")
            painter.setFont(small)
            painter.setPen(theme.c("text_muted"))
            painter.drawText(QRectF(x, r.top() + 28, r.width() - x - 40, 18), Qt.AlignmentFlag.AlignLeft,
                             self.subtitle or "Broadcast to every enabled zone")
        elif self.status is not None:
            zone = self.status.zone
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(zone.color or "#2563EB"))
            painter.drawRoundedRect(QRectF(r.left() + 8, r.top() + 12, 4, r.height() - 24), 2, 2)
            x = r.left() + 22
            painter.setFont(bold)
            painter.setPen(theme.c("text"))
            avail = r.width() - x - 38
            painter.drawText(QRectF(x, r.top() + 10, avail, 20), Qt.AlignmentFlag.AlignLeft,
                             QFontMetrics(bold).elidedText(zone.name, Qt.TextElideMode.ElideRight, int(avail)))
            _, fg, solid = theme.tone(STATE_TONES.get(self.status.state, "neutral"))
            painter.setBrush(solid)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(x + 4, r.top() + 39), 3.5, 3.5)
            painter.setFont(small)
            painter.setPen(fg)
            state = "On air" if self.status.state == ZS_BROADCASTING else ZONE_STATE_LABELS[self.status.state]
            painter.drawText(QRectF(x + 12, r.top() + 30, avail - 12, 18), Qt.AlignmentFlag.AlignLeft, state)
        # check indicator
        c = QPointF(r.right() - 20, r.top() + 20)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.c("accent"))
            painter.drawEllipse(c, 9, 9)
            painter.drawPixmap(QPointF(c.x() - 6, c.y() - 6), pixmap("check", "#FFFFFF", 12, 3.0))
        else:
            painter.setPen(QPen(theme.c("border_strong"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(c, 8.5, 8.5)


class ZoneSelector(QWidget):
    changed = Signal()

    def __init__(self, ctx, allow_campus: bool = True, min_tile_width: int = 190, show_toolbar: bool = True,
                 parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.allow_campus = allow_campus
        self._campus_id: int | None = None
        self.tiles: dict[int, ZoneTile] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        if show_toolbar:
            bar = QHBoxLayout()
            self.summary = label("", "caption")
            bar.addWidget(self.summary, 1)
            select_all = button("Select all", None, "ghost", "sm")
            select_all.clicked.connect(lambda: self.set_all(True))
            clear = button("Clear", None, "ghost", "sm")
            clear.clicked.connect(lambda: self.set_all(False))
            bar.addWidget(select_all)
            bar.addWidget(clear)
            layout.addLayout(bar)
        else:
            self.summary = label("", "caption")
        self.campus_tile = ZoneTile(None, campus=True)
        self.campus_tile.toggled.connect(self._campus_toggled)
        layout.addWidget(self.campus_tile)
        self.campus_tile.setVisible(allow_campus)
        grid_host = QWidget()
        grid_host.setObjectName("Transparent")
        self.grid = ResponsiveGrid(grid_host, min_width=min_tile_width, spacing=10)
        layout.addWidget(grid_host)
        self.refresh()

    def refresh(self) -> None:
        campus = self.ctx.zones.campus_zone()
        self._campus_id = campus.id if campus else None
        statuses = self.ctx.zones.zone_statuses(self.ctx.controller.active_zone_ids())
        selected = set(self.selected_ids(expand=True))
        seen = set()
        for status in statuses:
            tile = self.tiles.get(status.zone.id)
            if tile is None:
                tile = ZoneTile(status)
                tile.toggled.connect(self._tile_toggled)
                self.tiles[status.zone.id] = tile
                self.grid.addWidget(tile)
            tile.set_status(status)
            seen.add(status.zone.id)
        for zone_id in list(self.tiles):
            if zone_id not in seen:
                tile = self.tiles.pop(zone_id)
                self.grid.removeWidget(tile)
                tile.deleteLater()
        playable = sum(1 for s in statuses if s.playable)
        self.campus_tile.subtitle = f"All {playable} available zone{'s' if playable != 1 else ''}"
        self.campus_tile.update()
        self._update_summary()
        self.grid.invalidate()
        self.updateGeometry()

    def _campus_toggled(self, checked: bool) -> None:
        for tile in self.tiles.values():
            tile.blockSignals(True)
            tile.setChecked(checked and tile.isEnabled())
            tile.blockSignals(False)
            tile.update()
        self._update_summary()
        self.changed.emit()

    def _tile_toggled(self, checked: bool) -> None:
        if not checked and self.campus_tile.isChecked():
            self.campus_tile.blockSignals(True)
            self.campus_tile.setChecked(False)
            self.campus_tile.blockSignals(False)
        self._update_summary()
        self.changed.emit()

    def _update_summary(self) -> None:
        if self.campus_tile.isChecked():
            self.summary.setText("Entire campus selected")
        else:
            count = sum(1 for t in self.tiles.values() if t.isChecked())
            self.summary.setText(f"{count} zone{'s' if count != 1 else ''} selected" if count else "No zones selected")

    def set_all(self, checked: bool) -> None:
        if self.allow_campus:
            self.campus_tile.setChecked(checked)
            if not checked:
                self._campus_toggled(False)
        else:
            for tile in self.tiles.values():
                tile.setChecked(checked and tile.isEnabled())

    def selected_ids(self, expand: bool = False) -> list[int]:
        if self.allow_campus and self.campus_tile.isChecked() and self._campus_id and not expand:
            return [self._campus_id]
        return [zone_id for zone_id, tile in self.tiles.items() if tile.isChecked()]

    def set_selected(self, zone_ids: list[int]) -> None:
        ids = set(zone_ids or [])
        if self._campus_id in ids and self.allow_campus:
            self.campus_tile.setChecked(True)
            self._campus_toggled(True)
            return
        self.campus_tile.blockSignals(True)
        self.campus_tile.setChecked(False)
        self.campus_tile.blockSignals(False)
        for zone_id, tile in self.tiles.items():
            tile.blockSignals(True)
            tile.setChecked(zone_id in ids)
            tile.blockSignals(False)
            tile.update()
        self._update_summary()
