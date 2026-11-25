"""Scheduled broadcasts: calendar, agenda, recurring schedules and conflict-checked editor."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, Qt, QTime, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

from core.errors import ConflictError
from core.history_manager import format_duration
from core.permissions import P
from core.schedule_manager import (CONTENT_LABELS, RECURRENCE_LABELS, WEEKDAYS, describe, find_conflicts,
                                   next_occurrence)
from services.audio_sources import BufferSource

from .components.base import (Badge, Card, ElidedLabel, EmptyState, IconBubble, InfoBanner, SectionHeader, button,
                              clear_layout, divider, label)
from .components.calendar import ScheduleCalendar
from .components.controls import SegmentedControl, Spinner, ToggleSwitch
from .components.dialogs import Dialog, confirm
from .components.page import BasePage
from .components.speech import TTSEditor, VoiceRecorderPanel
from .components.tables import Column, DataTable
from .components.zone_selector import ZoneSelector
from .workers import run_async

CONTENT_ICONS = {"announcement": ("megaphone", "info"), "tts": ("type", "info"), "recording": ("mic", "purple"),
                 "audio_file": ("music", "success"), "alarm": ("siren", "danger")}
LAST_TONES = {"played": "success", "missed": "danger", "failed": "danger"}


def _clock(when: datetime) -> str:
    return when.strftime("%I:%M %p").lstrip("0")


class ScheduleDialog(Dialog):
    def __init__(self, page, schedule=None, day: date | None = None):
        super().__init__(page, "Edit schedule" if schedule else "New scheduled broadcast",
                         "Automatic broadcasts run while the application is open (it can stay in the system tray).",
                         "calendar_clock", width=780, scroll=True)
        self.ctx = page.ctx
        self.page = page
        self.schedule = schedule
        self._recording_file_id: int | None = None   # take saved to the library by an earlier Save attempt
        self._chimes_touched = schedule is not None
        self.setMinimumHeight(720)
        self.name = QLineEdit(schedule.name if schedule else "")
        self.name.setPlaceholderText("e.g. Morning bell")
        self.body.addWidget(self.field("Schedule name", self.name))

        self.content_type = SegmentedControl([("announcement", "Announcement", "megaphone"),
                                              ("tts", "Text-to-speech", "type"), ("recording", "Record voice", "mic"),
                                              ("audio_file", "Audio file", "music"), ("alarm", "Alarm drill", "siren")])
        self.body.addWidget(self.field("What should play?", self.content_type))
        self.content = QComboBox()
        self.content_field = self.field("Content", self.content)
        self.body.addWidget(self.content_field)
        own_tts = schedule is not None and schedule.content_type == "tts"
        self.tts_editor = TTSEditor(self.ctx, schedule.tts_message if own_tts else "",
                                    schedule.tts_voice if own_tts else "", schedule.tts_rate if own_tts else None,
                                    schedule.tts_volume if own_tts else None,
                                    placeholder="Type the message the schedule should speak...")
        self.body.addWidget(self.tts_editor)
        existing = schedule.content_name if schedule and schedule.content_type == "recording" else ""
        self.recorder_panel = VoiceRecorderPanel(self.ctx, existing)
        self.recorder_panel.error.connect(self.show_error)
        self.recorder_panel.changed.connect(self._recording_changed)
        self.body.addWidget(self.recorder_panel)

        chimes = QHBoxLayout()
        chimes.setSpacing(18)
        self.chime = QCheckBox("Intro chime")
        self.chime.setToolTip("Play the intro chime before the content")
        self.chime.setChecked(schedule.play_chime if schedule else False)
        self.outro_chime = QCheckBox("Outro chime")
        self.outro_chime.setToolTip("Play the outro chime after the content")
        self.outro_chime.setChecked(schedule.play_outro_chime if schedule else False)
        for box in (self.chime, self.outro_chime):
            box.clicked.connect(self._chime_clicked)
            chimes.addWidget(box)
        self.chime_hint = label("", "caption", wrap=True)
        chimes.addWidget(self.chime_hint, 1)
        self.chime_box = QWidget()
        self.chime_box.setObjectName("Transparent")
        self.chime_box.setLayout(chimes)
        self.body.addWidget(self.chime_box)

        self.content_type.changed.connect(self._fill_content)
        if schedule:
            self.content_type.set_value(schedule.content_type)
        self._fill_content()
        if schedule and schedule.content_id and schedule.content_type not in ("tts", "recording"):
            self.content.setCurrentIndex(max(0, self.content.findData(schedule.content_id)))

        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd, MMM d, yyyy")
        start = date.fromisoformat(schedule.start_date) if schedule else (day or date.today())
        self.date.setDate(QDate(start.year, start.month, start.day))
        self.time = QTimeEdit()
        self.time.setDisplayFormat("hh:mm AP")
        if schedule:
            h, m = map(int, schedule.time_of_day.split(":"))
            self.time.setTime(QTime(h, m))
        else:
            soon = (datetime.now() + timedelta(minutes=10)).replace(second=0)
            self.time.setTime(QTime(soon.hour, soon.minute))
        self.recurrence = QComboBox()
        for key, text in RECURRENCE_LABELS.items():
            self.recurrence.addItem(text, key)
        self.recurrence.setCurrentIndex(max(0, self.recurrence.findData(schedule.recurrence if schedule else "weekdays")))
        self.body.addLayout(self.row(self.field("Start date", self.date), self.field("Time", self.time),
                                     self.field("Repeat", self.recurrence), stretch=[3, 2, 3]))

        self.days_box = QWidget()
        self.days_box.setObjectName("Transparent")
        days_layout = QHBoxLayout(self.days_box)
        days_layout.setContentsMargins(0, 0, 0, 0)
        days_layout.setSpacing(6)
        self.day_buttons: list[QPushButton] = []
        selected_days = set(schedule.days_of_week) if schedule else {0, 1, 2, 3, 4}
        for index, name in enumerate(WEEKDAYS):
            chip = QPushButton(name)
            chip.setCheckable(True)
            chip.setChecked(index in selected_days)
            chip.setProperty("chip", True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.toggled.connect(self._update_summary)
            days_layout.addWidget(chip)
            self.day_buttons.append(chip)
        days_layout.addSpacing(12)
        self.interval_label = label("every")
        days_layout.addWidget(self.interval_label)
        self.interval = QSpinBox()
        self.interval.setRange(1, 12)
        self.interval.setSuffix(" week(s)")
        self.interval.setValue(schedule.interval_weeks if schedule else 2)
        days_layout.addWidget(self.interval)
        days_layout.addStretch(1)
        self.body.addWidget(self.field("On these days", self.days_box))

        end_row = QHBoxLayout()
        self.has_end = QCheckBox("Ends on")
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("ddd, MMM d, yyyy")
        if schedule and schedule.end_date:
            end = date.fromisoformat(schedule.end_date)
            self.has_end.setChecked(True)
            self.end_date.setDate(QDate(end.year, end.month, end.day))
        else:
            later = date.today() + timedelta(days=180)
            self.end_date.setDate(QDate(later.year, later.month, later.day))
        self.end_date.setEnabled(self.has_end.isChecked())
        self.has_end.toggled.connect(self.end_date.setEnabled)
        end_row.addWidget(self.has_end)
        end_row.addWidget(self.end_date)
        end_row.addStretch(1)
        self.end_holder = QWidget()
        self.end_holder.setObjectName("Transparent")
        self.end_holder.setLayout(end_row)
        self.body.addWidget(self.end_holder)

        self.zones = ZoneSelector(self.ctx, min_tile_width=170)
        campus = self.ctx.zones.campus_zone()
        self.zones.set_selected(schedule.zone_ids if schedule and schedule.zone_ids else ([campus.id] if campus else []))
        self.body.addWidget(self.field("Target zones", self.zones))

        bottom = QHBoxLayout()
        bottom.addWidget(label("Schedule enabled"), 1)
        self.enabled = ToggleSwitch(schedule.is_enabled if schedule else True)
        bottom.addWidget(self.enabled)
        self.body.addLayout(bottom)
        self.notes = QPlainTextEdit(schedule.notes if schedule else "")
        self.notes.setFixedHeight(60)
        self.notes.setPlaceholderText("Optional notes")
        self.body.addWidget(self.field("Notes", self.notes))

        self.summary = InfoBanner("", "info", "calendar_clock")
        self.body.addWidget(self.summary)
        self.conflict = InfoBanner("", "danger")
        self.conflict.hide()
        self.body.addWidget(self.conflict)

        for signal in (self.date.dateChanged, self.time.timeChanged, self.recurrence.currentIndexChanged,
                       self.interval.valueChanged, self.has_end.toggled, self.end_date.dateChanged,
                       self.content.currentIndexChanged, self.enabled.toggled, self.chime.toggled,
                       self.outro_chime.toggled, self.tts_editor.changed):
            signal.connect(self._update_summary)
        self.recurrence.currentIndexChanged.connect(self._recurrence_changed)
        self._recurrence_changed()
        self.preview_spinner = Spinner(20)
        self.footer_left.addWidget(self.preview_spinner)
        self.preview_button = button("Preview", "headphones", None)
        self.preview_button.setToolTip("Listen on this computer only (without chimes)")
        self.preview_button.clicked.connect(self._preview)
        self.footer_left.addWidget(self.preview_button)
        self.preview_button.setVisible(self.content_type.value() != "alarm")
        self.add_cancel()
        self.add_button("Save schedule", "primary", self._save, "check", default=True)

    def _fill_content(self, *args) -> None:
        kind = self.content_type.value()
        self.content.blockSignals(True)
        self.content.clear()
        items = []
        if kind == "announcement":
            items = [(a.id, f"{a.title}  ·  {a.category}") for a in self.ctx.announcements.list()]
        elif kind == "audio_file":
            files = self.ctx.library.files()
            files.sort(key=lambda f: (f.category not in ("Bell Sounds", "National Anthem", "School Hymns"),
                                      f.name.lower()))
            items = [(f.id, f"{f.name}  ·  {f.category}  ·  {format_duration(f.duration_seconds)}") for f in files]
        elif kind == "alarm":
            items = [(a.id, f"{a.name}  ·  {self.ctx.alarms.describe_timing(a)}") for a in self.ctx.alarms.alarm_types()]
        if not items:
            self.content.addItem("Nothing available - create it first", None)
        for item_id, text in items:
            self.content.addItem(text, item_id)
        self.content.blockSignals(False)
        self.content_field.setVisible(kind in ("announcement", "audio_file", "alarm"))
        self.tts_editor.setVisible(kind == "tts")
        self.recorder_panel.setVisible(kind == "recording")
        self.chime_box.setVisible(kind != "alarm")
        if not self._chimes_touched:
            spoken = kind in ("tts", "recording")
            self.chime.setChecked(spoken)
            self.outro_chime.setChecked(spoken)
        self.chime_hint.setText("The announcement's own chime settings also apply." if kind == "announcement" else
                                "Chime sounds and on/off switches are set in Settings > Audio.")
        if hasattr(self, "preview_button"):
            self.preview_button.setVisible(kind != "alarm")
        self._update_summary()

    def _chime_clicked(self, *args) -> None:
        self._chimes_touched = True

    def _recording_changed(self) -> None:
        self.clear_error()
        if self.recorder_panel.recording is not None:
            self._recording_file_id = None   # a new take replaces any earlier one
        self._update_summary()

    def _recording_id(self) -> int | None:
        if self._recording_file_id:
            return self._recording_file_id
        if self.schedule and self.schedule.content_type == "recording":
            return self.schedule.audio_file_id
        return None

    def _recurrence_changed(self, *args) -> None:
        key = self.recurrence.currentData()
        self.days_box.parentWidget().setVisible(key in ("weekly", "custom"))
        self.interval.setVisible(key == "custom")
        self.interval_label.setVisible(key == "custom")
        self.end_holder.setVisible(key != "once")
        self._update_summary()

    def _data(self) -> dict:
        qd, qt = self.date.date(), self.time.time()
        end = self.end_date.date()
        kind = self.content_type.value()
        if kind == "tts":
            content_id = None
        elif kind == "recording":
            content_id = self._recording_id()
        else:
            content_id = self.content.currentData()
        data = dict(name=self.name.text() or "Untitled", content_type=kind,
                    content_id=content_id, start_date=date(qd.year(), qd.month(), qd.day()).isoformat(),
                    time_of_day=f"{qt.hour():02d}:{qt.minute():02d}", recurrence=self.recurrence.currentData(),
                    days_of_week=[i for i, chip in enumerate(self.day_buttons) if chip.isChecked()],
                    interval_weeks=self.interval.value(),
                    end_date=date(end.year(), end.month(), end.day()).isoformat() if self.has_end.isChecked() and
                    self.recurrence.currentData() != "once" else None,
                    play_chime=self.chime.isChecked() and kind != "alarm",
                    play_outro_chime=self.outro_chime.isChecked() and kind != "alarm",
                    tts_message=self.tts_editor.text(), tts_voice=self.tts_editor.voice_id(),
                    tts_rate=self.tts_editor.rate_value(), tts_volume=self.tts_editor.volume_value(),
                    is_enabled=self.enabled.isChecked(), notes=self.notes.toPlainText())
        take = self.recorder_panel.recording
        if kind == "recording" and take is not None:
            data["pending_audio_seconds"] = take[0].size / take[1]   # unsaved take: used for the live summary
        return data

    def _update_summary(self, *args) -> None:
        if not hasattr(self, "summary"):
            return
        try:
            candidate = self.ctx.schedules.build(self._data(), self.zones.selected_ids() or [0],
                                                 self.schedule.id if self.schedule else None)
        except Exception as exc:  # noqa: BLE001 - live preview only
            self.summary.set_text(getattr(exc, "message", str(exc)), "warning")
            self.conflict.hide()
            return
        nxt = next_occurrence(candidate, datetime.now())
        when = f"next on {nxt.strftime('%a, %b %d at %I:%M %p').replace(' 0', ' ')}" if nxt else "no future dates"
        self.summary.set_text(f"<b>{describe(candidate)}</b> · about {format_duration(candidate.duration_seconds)} of "
                              f"airtime · {when}", "info")
        conflicts = self.ctx.schedules.check_conflicts(candidate)
        if conflicts:
            lines = "<br>".join(f"Overlaps <b>{c.name}</b> ({_clock(w)} on {w.strftime('%a %b %d')})" for c, w in
                                conflicts[:3])
            self.conflict.set_text(lines + "<br>Choose another time - overlapping broadcasts cannot run together.",
                                   "danger")
            self.conflict.show()
        else:
            self.conflict.hide()

    def _save(self) -> None:
        self.clear_error()
        try:
            take = self.recorder_panel.recording
            if self.content_type.value() == "recording" and take is not None:
                item = self.ctx.library.save_recording(*take, name=(self.name.text().strip() or "Scheduled recording"))
                self._recording_file_id = item.id
                self.recorder_panel.recording = None
            data = self._data()
            data.pop("pending_audio_seconds", None)
            self.saved = self.ctx.schedules.save(data, self.zones.selected_ids(),
                                                 self.schedule.id if self.schedule else None)
        except ConflictError as exc:
            self.show_error(exc)
            return
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        if self.saved.content_type == "tts":
            saved = self.saved
            run_async(lambda: self.ctx.schedules.prewarm(saved))
        self.accept()

    # ---------------------------------------------------------------- preview
    def _preview(self) -> None:
        player = self.ctx.preview
        if player.playing and player.token == self:
            player.stop()
            self.preview_button.setText("Preview")
            return
        self.clear_error()
        kind = self.content_type.value()
        try:
            if kind == "tts":
                if not self.tts_editor.text().strip():
                    self.show_error("Type a message to preview.")
                    return
                self._busy(True)

                def done(result):
                    self._busy(False)
                    self._play(BufferSource(*result))

                def failed(exc):
                    self._busy(False)
                    self.show_error(f"Speech synthesis failed: {exc}")

                self.tts_editor.render_async(done, failed)
            elif kind == "recording":
                take = self.recorder_panel.recording
                if take is not None:
                    self._play(BufferSource(*take))
                elif self._recording_id():
                    self._play(self.ctx.library.source_for(self._recording_id()))
                else:
                    self.show_error("Record something first.")
            elif kind == "announcement":
                if not self.content.currentData():
                    self.show_error("Choose an announcement first.")
                    return
                ann = self.ctx.announcements.get(self.content.currentData())
                self._busy(True)

                def ready(source):
                    self._busy(False)
                    self._play(source)

                def broken(exc):
                    self._busy(False)
                    self.show_error(exc)

                run_async(lambda: self.ctx.announcements.build_source(ann, with_chimes=False), ready, broken)
            elif kind == "audio_file":
                if not self.content.currentData():
                    self.show_error("Choose an audio file first.")
                    return
                self._play(self.ctx.library.source_for(self.content.currentData()))
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)

    def _busy(self, busy: bool) -> None:
        (self.preview_spinner.start if busy else self.preview_spinner.stop)()
        self.preview_button.setEnabled(not busy)

    def _play(self, source) -> None:
        try:
            device = self.ctx.preview.play(source, self.ctx.settings.get("audio.preview_output") or "", token=self)
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"Preview failed: {exc}")
            return
        self.preview_button.setText("Stop preview")
        self.page.toast("info", "Previewing locally", f"Playing on {device} only - not on paging zones.")
        QTimer.singleShot(250, self._watch_preview)

    def _watch_preview(self) -> None:
        if self.ctx.preview.playing and self.ctx.preview.token == self:
            QTimer.singleShot(250, self._watch_preview)
        else:
            self.preview_button.setText("Preview")

    def done(self, result):  # noqa: D401 - QDialog API
        self.recorder_panel.shutdown()
        if self.ctx.preview.token == self:
            self.ctx.preview.stop()
        super().done(result)


class AgendaRow(QWidget):
    def __init__(self, page, when: datetime, schedule):
        super().__init__()
        self.setObjectName("Transparent")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(12)
        time_box = QVBoxLayout()
        time_box.setSpacing(0)
        clock = label(when.strftime("%I:%M").lstrip("0"), "h3")
        clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ampm = label(when.strftime("%p"), "caption")
        ampm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_box.addWidget(clock)
        time_box.addWidget(ampm)
        holder = QWidget()
        holder.setObjectName("Transparent")
        holder.setFixedWidth(56)
        holder.setLayout(time_box)
        layout.addWidget(holder)
        name, tone = CONTENT_ICONS.get(schedule.content_type, ("radio", "neutral"))
        layout.addWidget(IconBubble(name, tone if schedule.is_enabled else "neutral", 36, 17))
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(ElidedLabel(schedule.name, "h3"))
        texts.addWidget(ElidedLabel(f"{CONTENT_LABELS[schedule.content_type]}: {schedule.content_name or 'not assigned'}"
                                    f"  ·  {page.ctx.zones.describe_zones(schedule.zone_ids)}", "caption"))
        layout.addLayout(texts, 1)
        status, tone = page.occurrence_status(when, schedule)
        layout.addWidget(Badge(status, tone, small=True))
        if page.can(P.SCHEDULES_MANAGE):
            edit = button("", "edit", "ghost", tooltip="Edit schedule")
            edit.clicked.connect(lambda: page.edit(schedule))
            layout.addWidget(edit)


class SchedulesPage(BasePage):
    title = "Scheduled Broadcasts"
    subtitle = "Bells, the national anthem and recurring announcements - played automatically on time."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        manage = self.can(P.SCHEDULES_MANAGE)
        pause_box = QHBoxLayout()
        pause_box.setSpacing(8)
        pause_box.addWidget(label("Pause all", "muted"))
        self.pause = ToggleSwitch(ctx.schedules.paused)
        self.pause.setEnabled(manage)
        self.pause.setToolTip("Temporarily stop every automatic broadcast (e.g. during class suspension)")
        self.pause.toggled.connect(self._toggle_pause)
        pause_box.addWidget(self.pause)
        holder = QWidget()
        holder.setObjectName("Transparent")
        holder.setLayout(pause_box)
        self.header.add_action(holder)
        self.new_button = self.header.add_action(button("New schedule", "plus", "primary"))
        self.new_button.clicked.connect(self.new_schedule)
        self.new_button.setVisible(manage)

        self.paused_banner = InfoBanner("<b>All scheduled broadcasts are paused.</b> Nothing will play automatically "
                                        "until schedules are resumed.", "warning")
        self.body.addWidget(self.paused_banner)
        self.status_strip = label("", "caption")
        self.body.addWidget(self.status_strip)

        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs, 1)

        # calendar tab
        cal_tab = QWidget()
        cal_tab.setObjectName("Transparent")
        cal_layout = QHBoxLayout(cal_tab)
        cal_layout.setContentsMargins(0, 8, 0, 0)
        cal_layout.setSpacing(16)
        cal_card = Card(padding=18, spacing=10)
        cal_card.body.addWidget(SectionHeader("Calendar", "Badges show how many broadcasts run each day", "calendar"))
        self.calendar = ScheduleCalendar()
        self.calendar.selectionChanged.connect(self._day_selected)
        self.calendar.currentPageChanged.connect(lambda *_: self._refresh_counts())
        cal_card.body.addWidget(self.calendar, 1)
        today_btn = button("Today", None, "ghost", "sm")
        today_btn.clicked.connect(lambda: self.calendar.setSelectedDate(QDate.currentDate()))
        cal_card.body.addWidget(today_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        cal_layout.addWidget(cal_card, 5)
        self.agenda_card = Card(padding=18, spacing=6)
        self.agenda_header = SectionHeader("Today", "", "clock")
        self.agenda_card.body.addWidget(self.agenda_header)
        self.agenda = QVBoxLayout()
        self.agenda.setSpacing(0)
        self.agenda_card.body.addLayout(self.agenda)
        self.agenda_card.body.addStretch(1)
        add_day = button("Add on this day", "plus", "subtle", "sm")
        add_day.clicked.connect(lambda: self.new_schedule(self._selected_day()))
        add_day.setVisible(manage)
        self.agenda_card.body.addWidget(add_day, alignment=Qt.AlignmentFlag.AlignLeft)
        cal_layout.addWidget(self.agenda_card, 6)
        self.tabs.addTab(cal_tab, "Calendar")

        # list tab
        list_tab = Card(padding=16, spacing=10)
        self.table = DataTable([
            Column("Schedule", lambda s: s.name, kind="title", width=260, subtitle=describe,
                   icon=lambda s: (CONTENT_ICONS.get(s.content_type, ("radio", "neutral"))[0],
                                   CONTENT_ICONS.get(s.content_type, ("radio", "neutral"))[1] if s.is_enabled
                                   else "neutral")),
            Column("Content", lambda s: f"{CONTENT_LABELS[s.content_type]}: {s.content_name or '— not assigned —'}",
                   stretch=True),
            Column("Zones", lambda s: self.ctx.zones.describe_zones(s.zone_ids), width=160),
            Column("Next run", self._next_text, width=150, sort_key=lambda s: self._next_sort(s)),
            Column("Last run", lambda s: (s.last_status.title() + " " + (s.last_run_at or "")[5:16]).strip()
                   if s.last_status else "Never", kind="badge", width=150,
                   tone=lambda s: LAST_TONES.get(s.last_status, "neutral")),
            Column("On", width=70, kind="actions"),
            Column("", width=150, kind="actions"),
        ], row_height=56)
        from PySide6.QtWidgets import QHeaderView
        for i in (5, 6):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(i, self.table.columns[i].width)
        self.table.set_actions(self._row_widgets)
        self.table.row_activated.connect(lambda s: self.edit(s) if manage else None)
        self.table.setMinimumHeight(420)
        list_tab.body.addWidget(self.table)
        self.list_empty = EmptyState("calendar_clock", "No schedules yet",
                                     "Add class bells, the national anthem or a recurring morning announcement.",
                                     "New schedule" if manage else "")
        self.list_empty.action_clicked.connect(self.new_schedule)
        list_tab.body.addWidget(self.list_empty)
        self.tabs.addTab(list_tab, "All schedules")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_status)
        ctx.bus.data_changed.connect(lambda name: self.refresh() if name in ("schedules", "announcements", "library",
                                                                             "alarms", "zones") and self.isVisible()
                                     else None)
        ctx.scheduler.queue_changed.connect(self._update_status)

    # ------------------------------------------------------------------ data
    def on_show(self) -> None:
        self.refresh()
        self._timer.start(5000)

    def on_hide(self) -> None:
        self._timer.stop()

    def refresh(self) -> None:
        schedules = self.ctx.schedules.schedules()
        self.table.set_rows(schedules)
        self.table.setVisible(bool(schedules))
        self.list_empty.setVisible(not schedules)
        self.tabs.setTabText(1, f"All schedules ({len(schedules)})")
        self.pause.blockSignals(True)
        self.pause.setChecked(self.ctx.schedules.paused)
        self.pause.blockSignals(False)
        self.paused_banner.setVisible(self.ctx.schedules.paused)
        self._refresh_counts()
        self._day_selected()
        self._update_status()

    def _refresh_counts(self) -> None:
        self.calendar.set_counts(self.ctx.schedules.month_counts(self.calendar.yearShown(), self.calendar.monthShown()))

    def _selected_day(self) -> date:
        q = self.calendar.selectedDate()
        return date(q.year(), q.month(), q.day())

    def _day_selected(self) -> None:
        day = self._selected_day()
        title = "Today" if day == date.today() else ("Tomorrow" if day == date.today() + timedelta(days=1)
                                                     else day.strftime("%A"))
        self.agenda_header.title.setText(f"{title}, {day.strftime('%B %d')}".replace(" 0", " "))
        clear_layout(self.agenda)
        items = self.ctx.schedules.occurrences_on(day)
        if not items:
            self.agenda.addWidget(EmptyState("calendar", "Nothing scheduled", "No broadcasts are planned for this day."))
            return
        for i, (when, schedule) in enumerate(items):
            if i:
                self.agenda.addWidget(divider())
            self.agenda.addWidget(AgendaRow(self, when, schedule))

    def occurrence_status(self, when: datetime, schedule) -> tuple[str, str]:
        if not schedule.is_enabled:
            return "Disabled", "neutral"
        if not schedule.has_content:
            return "No content", "warning"
        stamp = when.strftime("%Y-%m-%d %H:%M:%S")
        if schedule.last_run_at and stamp <= schedule.last_run_at:
            if stamp == schedule.last_run_at:
                return schedule.last_status.title() or "Played", LAST_TONES.get(schedule.last_status, "success")
            return "Past", "neutral"
        if when < datetime.now():
            return "Past", "neutral"
        if self.ctx.schedules.paused:
            return "Paused", "warning"
        delta = when - datetime.now()
        if delta < timedelta(hours=1):
            return f"in {int(delta.total_seconds() // 60)} min", "accent"
        return "Upcoming", "accent"

    def _next_sort(self, s):
        nxt = self.ctx.schedules.next_run(s)
        return nxt or datetime.max

    def _next_text(self, s) -> str:
        if not s.is_enabled:
            return "Disabled"
        nxt = self.ctx.schedules.next_run(s)
        if nxt is None:
            return "Finished" if s.recurrence == "once" else "—"
        if nxt.date() == date.today():
            return f"Today {_clock(nxt)}"
        if nxt.date() == date.today() + timedelta(days=1):
            return f"Tomorrow {_clock(nxt)}"
        return nxt.strftime("%a %b %d, ") + _clock(nxt)

    def _update_status(self) -> None:
        grace = self.ctx.settings.get_int("schedules.grace_minutes")
        waiting = self.ctx.scheduler.waiting_count
        running = "running" if self.ctx.scheduler.running else "stopped"
        nxt = self.ctx.schedules.upcoming(1, 7)
        nxt_text = f"Next: {nxt[0][1].name} at {_clock(nxt[0][0])}" if nxt else "No upcoming broadcasts"
        self.status_strip.setText(f"Scheduler {running} · checks every second · waits up to {grace} min when the line "
                                  f"is busy · {waiting} waiting · {nxt_text}")

    # --------------------------------------------------------------- widgets
    def _row_widgets(self, schedule) -> QWidget:
        manage = self.can(P.SCHEDULES_MANAGE)
        row = self.table.rows.index(schedule)
        toggle = ToggleSwitch(schedule.is_enabled)
        toggle.setEnabled(manage)
        toggle.toggled.connect(lambda checked: self._set_enabled(schedule, checked, toggle))
        holder = QWidget()
        holder.setObjectName("Transparent")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(8, 0, 0, 0)
        hl.addWidget(toggle)
        hl.addStretch(1)
        self.table.setIndexWidget(self.table.model_.index(row, 5), holder)
        actions = QWidget()
        actions.setObjectName("Transparent")
        al = QHBoxLayout(actions)
        al.setContentsMargins(4, 0, 4, 0)
        al.setSpacing(4)
        run = button("Run", "play", "subtle", "sm", tooltip="Play this schedule now (test)")
        run.setEnabled(manage and schedule.has_content)
        run.clicked.connect(lambda: self.run_now(schedule))
        al.addWidget(run)
        if manage:
            edit = button("", "edit", "ghost", tooltip="Edit")
            edit.clicked.connect(lambda: self.edit(schedule))
            delete = button("", "trash", "ghost", tooltip="Delete", icon_color="danger")
            delete.clicked.connect(lambda: self.delete(schedule))
            al.addWidget(edit)
            al.addWidget(delete)
        al.addStretch(1)
        return actions

    # ---------------------------------------------------------------- actions
    def new_schedule(self, day: date | None = None) -> None:
        if not self.can(P.SCHEDULES_MANAGE):
            return
        dialog = ScheduleDialog(self, None, day if isinstance(day, date) else None)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Schedule saved", f"{dialog.saved.name}: {describe(dialog.saved)}")
            self.refresh()

    def edit(self, schedule) -> None:
        dialog = ScheduleDialog(self, self.ctx.schedules.get(schedule.id))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Schedule updated", describe(dialog.saved))
            self.refresh()

    def delete(self, schedule) -> None:
        if confirm(self, "Delete schedule?", f"<b>{schedule.name}</b> ({describe(schedule)}) will be removed.",
                   "Delete", danger=True, icon_name="trash"):
            self.guarded(self.ctx.schedules.delete, schedule.id, success="Schedule deleted")

    def run_now(self, schedule) -> None:
        if self.start_broadcast(lambda interrupt: self.ctx.schedules.run_now(schedule.id, interrupt)):
            self.toast("success", "Playing now", schedule.name)

    def _set_enabled(self, schedule, enabled: bool, toggle) -> None:
        if not self.guarded(self.ctx.schedules.set_enabled, schedule.id, enabled):
            toggle.blockSignals(True)
            toggle.setChecked(not enabled)
            toggle.blockSignals(False)

    def _toggle_pause(self, paused: bool) -> None:
        if paused and not confirm(self, "Pause all schedules?", "No bell or scheduled announcement will play until "
                                                                "you resume.", "Pause schedules", danger=True,
                                  icon_name="pause"):
            self.pause.blockSignals(True)
            self.pause.setChecked(False)
            self.pause.blockSignals(False)
            return
        self.guarded(self.ctx.schedules.set_paused, paused,
                     success="Schedules paused" if paused else "Schedules resumed")
        self.refresh()
