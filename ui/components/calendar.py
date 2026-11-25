"""Month calendar that shows how many broadcasts are scheduled on each day."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget

from ..theme import theme


class ScheduleCalendar(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.counts: dict[date, int] = {}
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setMinimumHeight(360)
        self._style_headers()
        theme.changed.connect(self._style_headers)

    def _style_headers(self) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(theme.c("text_muted"))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(8.5)
        fmt.setFont(font)
        for day in (Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday, Qt.DayOfWeek.Thursday,
                    Qt.DayOfWeek.Friday, Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.setWeekdayTextFormat(day, fmt)
        self.setHeaderTextFormat(fmt)
        self.update()

    def set_counts(self, counts: dict[date, int]) -> None:
        self.counts = counts
        self.updateCells()

    def paintCell(self, painter: QPainter, rect, qdate: QDate) -> None:  # noqa: N802 - Qt API
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        day = date(qdate.year(), qdate.month(), qdate.day())
        selected = qdate == self.selectedDate()
        in_month = qdate.month() == self.monthShown()
        today = qdate == QDate.currentDate()
        cell = QRectF(rect).adjusted(3, 3, -3, -3)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.c("accent"))
            painter.drawRoundedRect(cell, 10, 10)
        elif today:
            painter.setPen(QPen(theme.c("accent"), 1.5))
            painter.setBrush(theme.c("accent_soft"))
            painter.drawRoundedRect(cell, 10, 10)
        font = QFont(self.font())
        font.setBold(today or selected)
        painter.setFont(font)
        if selected:
            painter.setPen(QColor("#FFFFFF"))
        elif not in_month:
            painter.setPen(theme.c("text_faint"))
        elif qdate.dayOfWeek() >= 6:
            painter.setPen(theme.c("text_muted"))
        else:
            painter.setPen(theme.c("text"))
        painter.drawText(cell.adjusted(0, 4, 0, -cell.height() / 2.4), Qt.AlignmentFlag.AlignCenter, str(qdate.day()))
        count = self.counts.get(day, 0)
        if count and in_month:
            small = QFont(self.font())
            small.setPointSizeF(7.5)
            small.setBold(True)
            painter.setFont(small)
            text = str(count)
            pill = QRectF(cell.center().x() - 12, cell.bottom() - 18, 24, 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF") if selected else theme.c("purple_soft"))
            painter.drawRoundedRect(pill, 7, 7)
            painter.setPen(theme.c("accent") if selected else theme.c("purple_text"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
