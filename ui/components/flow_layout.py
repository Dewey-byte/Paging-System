"""Responsive grid: arranges equal-width tiles in as many columns as fit."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidgetItem


class ResponsiveGrid(QLayout):
    """Lays out child widgets in columns of at least ``min_width`` pixels, stretching to fill the row."""

    def __init__(self, parent=None, min_width: int = 220, spacing: int = 12, max_columns: int = 6):
        super().__init__(parent)
        self._items: list = []
        self.min_width = min_width
        self.max_columns = max_columns
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        width = self.min_width
        height = max((item.minimumSize().height() for item in self._items), default=0)
        m = self.contentsMargins()
        return QSize(width + m.left() + m.right(), height + m.top() + m.bottom())

    def columns_for(self, width: int) -> int:
        spacing = self.spacing()
        cols = max(1, (width + spacing) // (self.min_width + spacing))
        return int(min(cols, self.max_columns, max(1, len(self._visible()))))

    def _visible(self):
        return [i for i in self._items if not (i.widget() and i.widget().isHidden())]

    def _layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        items = self._visible()
        if not items:
            return m.top() + m.bottom()
        spacing = self.spacing()
        cols = self.columns_for(area.width())
        col_w = (area.width() - spacing * (cols - 1)) / cols
        y = area.y()
        for start in range(0, len(items), cols):
            row = items[start:start + cols]
            row_h = max(item.widget().heightForWidth(int(col_w)) if item.widget() and
                        item.widget().hasHeightForWidth() else item.sizeHint().height() for item in row)
            for i, item in enumerate(row):
                if apply:
                    x = area.x() + int(i * (col_w + spacing))
                    item.setGeometry(QRect(QPoint(x, int(y)), QSize(int(col_w), int(row_h))))
            y += row_h + spacing
        return int(y - spacing - rect.y() + m.bottom())
