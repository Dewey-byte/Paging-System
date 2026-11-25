"""Data table with column specs, painted badges, icon titles and row action widgets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QTableView, QWidget

from ..icons import pixmap
from ..theme import theme


@dataclass
class Column:
    title: str
    value: Callable[[Any], Any] = lambda row: ""
    width: int | None = None
    kind: str = "text"            # text | muted | bold | badge | title | actions
    align: str = "left"
    stretch: bool = False
    tone: Callable[[Any], str] | None = None
    subtitle: Callable[[Any], str] | None = None
    icon: Callable[[Any], tuple[str, str]] | None = None   # (icon name, tone or #hex)
    sort_key: Callable[[Any], Any] | None = None
    tooltip: Callable[[Any], str] | None = None


ROW_ROLE = Qt.ItemDataRole.UserRole + 1


class ObjectTableModel(QAbstractTableModel):
    def __init__(self, columns: list[Column]):
        super().__init__()
        self.columns = columns
        self.rows: list[Any] = []

    def set_rows(self, rows: list[Any]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        column = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if column.kind == "actions":
                return None
            value = column.value(row)
            return "" if value is None else str(value)
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = {"left": Qt.AlignmentFlag.AlignLeft, "right": Qt.AlignmentFlag.AlignRight,
                          "center": Qt.AlignmentFlag.AlignHCenter}[column.align]
            return int(horizontal | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column.kind == "muted":
            return theme.c("text_muted")
        if role == Qt.ItemDataRole.FontRole and column.kind == "bold":
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole and column.tooltip:
            return column.tooltip(row)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title.upper()
        return None

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder):
        spec = self.columns[column]
        if spec.kind == "actions":
            return
        key = spec.sort_key or (lambda r: str(spec.value(r) or "").lower())
        self.layoutAboutToBeChanged.emit()
        self.rows.sort(key=key, reverse=order == Qt.SortOrder.DescendingOrder)
        self.layoutChanged.emit()


class RichDelegate(QStyledItemDelegate):
    """Paints badge and title (icon + two-line text) columns."""

    def __init__(self, table: "DataTable"):
        super().__init__(table)
        self.table = table

    def paint(self, painter: QPainter, option, index):
        column = self.table.columns[index.column()]
        if column.kind not in ("badge", "title"):
            super().paint(painter, option, index)
            return
        row = index.data(ROW_ROLE)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, theme.c("selection"))
        painter.setPen(theme.c("border_soft"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        rect = QRectF(option.rect).adjusted(8, 0, -8, 0)
        if column.kind == "badge":
            text = str(column.value(row) or "")
            if text:
                bg, fg, solid = theme.tone(column.tone(row) if column.tone else "neutral")
                font = QFont(option.font)
                font.setPointSizeF(8.3)
                font.setBold(True)
                painter.setFont(font)
                width = QFontMetrics(font).horizontalAdvance(text) + 26
                pill = QRectF(rect.left(), rect.center().y() - 11, width, 22)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(pill, 11, 11)
                painter.setBrush(solid)
                painter.drawEllipse(QPointF(pill.left() + 10, pill.center().y()), 3, 3)
                painter.setPen(fg)
                painter.drawText(pill.adjusted(17, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
        else:
            x = rect.left()
            if column.icon:
                name, tone = column.icon(row)
                if tone.startswith("#"):
                    fg = QColor(tone)
                    bg = QColor(tone)
                    bg.setAlpha(36 if theme.is_dark else 26)
                else:
                    bg, fg, _ = theme.tone(tone)
                box = QRectF(x, rect.center().y() - 16, 32, 32)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(box, 9, 9)
                painter.drawPixmap(QPointF(box.left() + 7, box.top() + 7), pixmap(name, fg.name(), 18))
                x += 44
            title = str(column.value(row) or "")
            subtitle = column.subtitle(row) if column.subtitle else ""
            bold = QFont(option.font)
            bold.setBold(True)
            painter.setFont(bold)
            painter.setPen(theme.c("text"))
            avail = rect.right() - x
            fm = QFontMetrics(bold)
            if subtitle:
                painter.drawText(QRectF(x, rect.top() + 5, avail, rect.height() / 2 - 2),
                                 Qt.AlignmentFlag.AlignBottom, fm.elidedText(title, Qt.TextElideMode.ElideRight,
                                                                             int(avail)))
                small = QFont(option.font)
                small.setPointSizeF(8.5)
                painter.setFont(small)
                painter.setPen(theme.c("text_muted"))
                painter.drawText(QRectF(x, rect.center().y() + 1, avail, rect.height() / 2 - 4),
                                 Qt.AlignmentFlag.AlignTop,
                                 QFontMetrics(small).elidedText(subtitle, Qt.TextElideMode.ElideRight, int(avail)))
            else:
                painter.drawText(QRectF(x, rect.top(), avail, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                                 fm.elidedText(title, Qt.TextElideMode.ElideRight, int(avail)))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(120, self.table.row_height)


class DataTable(QTableView):
    row_activated = Signal(object)
    selection_changed = Signal(object)

    def __init__(self, columns: list[Column], parent: QWidget | None = None, row_height: int = 46,
                 sortable: bool = True):
        super().__init__(parent)
        self.columns = columns
        self.row_height = row_height
        self.model_ = ObjectTableModel(columns)
        self.setModel(self.model_)
        self._actions_factory: Callable[[Any], QWidget] | None = None
        self.setItemDelegate(RichDelegate(self))
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(60)
        for i, column in enumerate(columns):
            if column.stretch:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                if column.width:
                    self.setColumnWidth(i, column.width)
        if sortable:
            self.setSortingEnabled(True)
            header.setSortIndicatorShown(False)
        self.doubleClicked.connect(lambda index: self.row_activated.emit(self.model_.rows[index.row()]))
        self.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit(self.selected()))
        theme.changed.connect(self.viewport().update)

    def set_actions(self, factory: Callable[[Any], QWidget]) -> None:
        self._actions_factory = factory

    def set_rows(self, rows: list[Any]) -> None:
        self.model_.set_rows(rows)
        self._install_actions()

    def sortByColumn(self, column, order):  # noqa: N802 - keep action widgets after sorting
        super().sortByColumn(column, order)
        self._install_actions()

    def _install_actions(self) -> None:
        if not self._actions_factory:
            return
        action_columns = [i for i, c in enumerate(self.columns) if c.kind == "actions"]
        if not action_columns:
            return
        col = action_columns[-1]
        for r, row in enumerate(self.model_.rows):
            widget = self._actions_factory(row)
            self.setIndexWidget(self.model_.index(r, col), widget)

    def selected(self):
        indexes = self.selectionModel().selectedRows()
        if not indexes:
            return None
        return self.model_.rows[indexes[0].row()]

    @property
    def rows(self) -> list[Any]:
        return self.model_.rows
