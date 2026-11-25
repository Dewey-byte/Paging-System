"""Broadcast history: search, filters, statistics, details and CSV/PDF export."""
from __future__ import annotations

import os
from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QDateEdit, QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from core.history_manager import STATUS_LABELS, TYPE_LABELS, format_duration
from core.permissions import P

from .components.base import Badge, Card, EmptyState, IconBubble, InfoBanner, button, label
from .components.controls import SearchBox
from .components.dialogs import Dialog
from .components.page import BasePage
from .components.tables import Column, DataTable

STATUS_TONES = {"completed": "success", "cancelled": "warning", "interrupted": "warning", "failed": "danger",
                "missed": "danger", "in_progress": "accent"}
TYPE_TONES = {"live": "accent", "announcement": "info", "alarm": "danger", "scheduled": "purple", "audio": "success",
              "test": "neutral"}
TYPE_ICONS = {"live": "mic", "announcement": "megaphone", "alarm": "siren", "scheduled": "calendar_clock",
              "audio": "music", "test": "speaker"}
PRESETS = [("today", "Today"), ("7", "Last 7 days"), ("30", "Last 30 days"), ("month", "This month"),
           ("all", "All time"), ("custom", "Custom range")]


class DetailsDialog(Dialog):
    def __init__(self, parent, record):
        super().__init__(parent, record.title, f"{TYPE_LABELS.get(record.broadcast_type, record.broadcast_type)} · "
                         f"record #{record.id}", TYPE_ICONS.get(record.broadcast_type, "radio"),
                         TYPE_TONES.get(record.broadcast_type, "accent"), width=560)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(12)
        fields = [
            ("Status", None), ("Duration", format_duration(record.duration_seconds)),
            ("Started", record.started_at), ("Ended", record.ended_at or "—"),
            ("Initiated by", record.initiated_by_name or "—"), ("Ended by", record.ended_by_name or "—"),
            ("Category", record.category or "—"), ("Source", "Scheduler" if record.source == "scheduler" else "Manual"),
            ("Priority", str(record.priority)), ("Outputs", record.devices_summary or "—"),
        ]
        for i, (caption, value) in enumerate(fields):
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(label(caption.upper(), "overline"))
            if value is None:
                box.addWidget(Badge(STATUS_LABELS.get(record.status, record.status),
                                    STATUS_TONES.get(record.status, "neutral"), dot=True))
            else:
                text = label(value, wrap=True, selectable=True)
                text.setStyleSheet("font-weight: 600;")
                box.addWidget(text)
            grid.addLayout(box, i // 2, i % 2)
        self.body.addLayout(grid)
        self.body.addWidget(label("TARGET ZONES", "overline"))
        self.body.addWidget(label(record.zone_names or "—", wrap=True, selectable=True))
        if record.status_detail:
            self.body.addWidget(InfoBanner(record.status_detail, "warning" if record.status in
                                           ("failed", "missed", "interrupted") else "info"))
        self.add_button("Close", "primary", self.accept, default=True)


class HistoryPage(BasePage):
    title = "Broadcast History"
    subtitle = "Complete record of every live page, announcement, alarm and scheduled broadcast."
    scrollable = False

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        can_export = self.can(P.HISTORY_EXPORT)
        self.csv_button = self.header.add_action(button("Export CSV", "download", None))
        self.csv_button.clicked.connect(lambda: self.export("csv"))
        self.pdf_button = self.header.add_action(button("Export PDF", "file_text", "primary"))
        self.pdf_button.clicked.connect(lambda: self.export("pdf"))
        for b in (self.csv_button, self.pdf_button):
            b.setVisible(can_export)

        filters = Card(padding=14, spacing=10, shadow=False)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.search = SearchBox("Search title, zones, user or details...")
        self.search.textChanged.connect(lambda: self._debounce.start(250))
        row1.addWidget(self.search, 3)
        self.preset = QComboBox()
        for key, text in PRESETS:
            self.preset.addItem(text, key)
        self.preset.setCurrentIndex(1)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        row1.addWidget(self.preset, 1)
        self.date_from = QDateEdit()
        self.date_to = QDateEdit()
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("MMM d, yyyy")
            edit.dateChanged.connect(self._custom_dates)
        row1.addWidget(self.date_from)
        row1.addWidget(label("to", "muted"))
        row1.addWidget(self.date_to)
        filters.body.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.type = QComboBox()
        self.category = QComboBox()
        self.user_filter = QComboBox()
        self.status = QComboBox()
        for combo in (self.type, self.category, self.user_filter, self.status):
            combo.currentIndexChanged.connect(self.refresh)
            row2.addWidget(combo, 1)
        reset = button("Reset filters", "x", "ghost", "sm")
        reset.clicked.connect(self.reset_filters)
        row2.addWidget(reset)
        filters.body.addLayout(row2)
        self.body.addWidget(filters)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stats: dict[str, object] = {}
        for key, caption, icon_name, tone in (("total", "Broadcasts", "radio", "accent"),
                                              ("completed", "Completed", "check_circle", "success"),
                                              ("stopped", "Stopped / interrupted", "stop", "warning"),
                                              ("failed", "Failed / missed", "x_circle", "danger"),
                                              ("alarms", "Emergency alarms", "siren", "danger"),
                                              ("airtime", "Total airtime", "clock", "purple")):
            tile = Card(padding=12, spacing=2, shadow=False)
            top = QHBoxLayout()
            top.setSpacing(10)
            top.addWidget(IconBubble(icon_name, tone, 34, 17))
            texts = QVBoxLayout()
            texts.setSpacing(0)
            value = label("0", "h2")
            texts.addWidget(value)
            texts.addWidget(label(caption, "caption"))
            top.addLayout(texts, 1)
            tile.body.addLayout(top)
            stats_row.addWidget(tile, 1)
            self.stats[key] = value
        self.body.addLayout(stats_row)

        table_card = Card(padding=12, spacing=6, shadow=False)
        self.table = DataTable([
            Column("Date & time", lambda r: r.started_at[:16], width=140, kind="muted"),
            Column("Broadcast", lambda r: r.title, kind="title", width=260,
                   subtitle=lambda r: r.category or TYPE_LABELS.get(r.broadcast_type, ""),
                   icon=lambda r: (TYPE_ICONS.get(r.broadcast_type, "radio"), TYPE_TONES.get(r.broadcast_type,
                                                                                             "neutral"))),
            Column("Type", lambda r: TYPE_LABELS.get(r.broadcast_type, r.broadcast_type), kind="badge", width=150,
                   tone=lambda r: TYPE_TONES.get(r.broadcast_type, "neutral")),
            Column("Initiated by", lambda r: r.initiated_by_name, width=140),
            Column("Zones", lambda r: r.zone_names, stretch=True, tooltip=lambda r: r.zone_names),
            Column("Duration", lambda r: format_duration(r.duration_seconds), width=90,
                   sort_key=lambda r: r.duration_seconds or 0),
            Column("Status", lambda r: STATUS_LABELS.get(r.status, r.status), kind="badge", width=130,
                   tone=lambda r: STATUS_TONES.get(r.status, "neutral"), tooltip=lambda r: r.status_detail),
        ], row_height=52)
        self.table.row_activated.connect(lambda r: DetailsDialog(self, r).exec())
        table_card.body.addWidget(self.table, 1)
        self.empty = EmptyState("history", "No broadcasts found", "Try a wider date range or clear the filters.")
        table_card.body.addWidget(self.empty)
        self.footer = label("", "caption")
        table_card.body.addWidget(self.footer)
        self.body.addWidget(table_card, 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
        self._loading = False
        ctx.bus.data_changed.connect(lambda name: self.refresh() if name == "history" and self.isVisible() else None)
        self._preset_changed()

    # ------------------------------------------------------------------ data
    def on_show(self) -> None:
        self._fill_filters()
        self.refresh()

    def _fill_filters(self) -> None:
        options = self.ctx.history.filter_options()
        self._loading = True
        for combo, caption, values, labels in (
                (self.type, "All types", list(TYPE_LABELS), TYPE_LABELS),
                (self.category, "All categories", options["categories"], None),
                (self.user_filter, "All users", options["users"], None),
                (self.status, "All statuses", list(STATUS_LABELS), STATUS_LABELS)):
            current = combo.currentData()
            combo.clear()
            combo.addItem(caption, "")
            for value in values:
                combo.addItem(labels.get(value, value) if labels else value, value)
            combo.setCurrentIndex(max(0, combo.findData(current or "")))
        self._loading = False

    def _preset_changed(self) -> None:
        key = self.preset.currentData()
        today = date.today()
        start = {"today": today, "7": today - timedelta(days=6), "30": today - timedelta(days=29),
                 "month": today.replace(day=1), "all": date(2000, 1, 1)}.get(key)
        custom = key == "custom"
        self.date_from.setEnabled(custom)
        self.date_to.setEnabled(custom)
        if start is not None:
            self._loading = True
            self.date_from.setDate(QDate(start.year, start.month, start.day))
            self.date_to.setDate(QDate(today.year, today.month, today.day))
            self._loading = False
        self.refresh()

    def _custom_dates(self) -> None:
        if not self._loading and self.preset.currentData() == "custom":
            self.refresh()

    @staticmethod
    def _d(edit) -> date:
        q = edit.date()
        return date(q.year(), q.month(), q.day())

    def filters(self) -> dict:
        return dict(text=self.search.text().strip(), date_from=self._d(self.date_from), date_to=self._d(self.date_to),
                    broadcast_type=self.type.currentData() or "", category=self.category.currentData() or "",
                    user_name=self.user_filter.currentData() or "", status=self.status.currentData() or "")

    def describe_filters(self) -> str:
        parts = [f"{self._d(self.date_from):%b %d, %Y} - {self._d(self.date_to):%b %d, %Y}"
                 if self.preset.currentData() != "all" else "All dates"]
        for combo in (self.type, self.category, self.user_filter, self.status):
            if combo.currentData():
                parts.append(combo.currentText())
        if self.search.text().strip():
            parts.append(f'"{self.search.text().strip()}"')
        return ", ".join(parts)

    def refresh(self) -> None:
        if self._loading:
            return
        records = self.ctx.history.search(**self.filters())
        self.records = records
        self.table.set_rows(records)
        self.table.setVisible(bool(records))
        self.empty.setVisible(not records)
        summary = self.ctx.history.summarize(records)
        for key, widget in self.stats.items():
            widget.setText(format_duration(summary[key]) if key == "airtime" else str(summary[key]))
        self.footer.setText(f"{len(records)} record(s) · {self.describe_filters()} · double-click a row for details")

    def reset_filters(self) -> None:
        self._loading = True
        self.search.clear()
        for combo in (self.type, self.category, self.user_filter, self.status):
            combo.setCurrentIndex(0)
        self.preset.setCurrentIndex(1)
        self._loading = False
        self._preset_changed()

    # --------------------------------------------------------------- export
    def export(self, kind: str) -> None:
        records = getattr(self, "records", [])
        if not records:
            self.toast("warning", "Nothing to export", "No records match the current filters.")
            return
        default = f"broadcast_history_{date.today().isoformat()}.{kind}"
        path, _ = QFileDialog.getSaveFileName(self, f"Export {kind.upper()}", default,
                                              "PDF documents (*.pdf)" if kind == "pdf" else "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(f".{kind}"):
            path += f".{kind}"
        try:
            self.ctx.history.export(records, path, self.describe_filters())
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc, dialog=True)
            return
        self.toast("success", f"{kind.upper()} exported", f"{len(records)} records saved to {os.path.basename(path)}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
