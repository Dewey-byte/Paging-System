"""Alarm management: predefined/custom alarms, safeguarded activation, active emergency state and log."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from core.alarm_manager import ALARM_ICONS
from core.errors import AppError, BroadcastRejected
from core.history_manager import STATUS_LABELS, format_duration
from core.permissions import P
from services.audio_decoder import FILE_DIALOG_FILTER
from services.audio_sources import RepeatSource
from services.sound_generator import ALARM_PATTERNS

from .components.base import (Badge, Card, ElidedLabel, EmptyState, IconBubble, InfoBanner, SectionHeader,
                              StatusDot, button, label)
from .components.controls import ColorSwatches, HoldButton, SegmentedControl, ToggleSwitch
from .components.dialogs import Dialog, confirm
from .components.flow_layout import ResponsiveGrid
from .components.page import BasePage
from .components.tables import Column, DataTable
from .components.zone_selector import ZoneSelector
from .icons import icon
from .theme import theme
from .workers import run_async

STATUS_TONES = {"completed": "success", "cancelled": "warning", "interrupted": "warning", "failed": "danger",
                "in_progress": "danger"}


class ActivateDialog(Dialog):
    """Safeguarded activation: zone choice, impact summary and press-and-hold confirmation."""

    def __init__(self, page, alarm):
        super().__init__(page, f"Activate {alarm.name}?", "This alarm overrides regular announcements and plays "
                         "immediately on the selected zones.", alarm.icon or "siren", "danger", width=700, scroll=True)
        self.ctx = page.ctx
        self.alarm = alarm
        self.activated = None
        self.setMinimumHeight(640)
        active = self.ctx.controller.active
        if active is not None:
            if active.is_emergency:
                text = f"<b>{active.title}</b> is already active. Activating will replace it."
            else:
                allowed = self.ctx.auth.has(P.EMERGENCY_OVERRIDE)
                text = (f"<b>{active.title}</b> ({active.kind_label}) is on air and will be interrupted immediately."
                        if allowed else f"<b>{active.title}</b> is on air. Interrupting it requires the Emergency "
                                        "override permission - stop it first or ask an administrator.")
            self.body.addWidget(InfoBanner(text, "danger"))
        timing = page.ctx.alarms.describe_timing(alarm)
        self.body.addWidget(InfoBanner(f"Plays <b>{timing}</b>" + (" with a spoken evacuation message"
                                                                    if alarm.voice_message else "") +
                                       ". Use only for real emergencies or announced drills. Every activation is "
                                       "logged with your name.", "warning"))
        self.zones = ZoneSelector(self.ctx, min_tile_width=170)
        campus = self.ctx.zones.campus_zone()
        self.zones.set_selected([campus.id] if campus else [])
        self.body.addWidget(self.field("Zones to alert", self.zones))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Re-enter your password")
        if self.ctx.settings.get_bool("alarm.require_password"):
            self.body.addWidget(self.field("Confirm your identity", self.password))
        hold = self.ctx.settings.get_float("alarm.hold_seconds") or 1.5
        self.hold = HoldButton(f"PRESS AND HOLD TO ACTIVATE  ({hold:.1f}s)", hold, alarm.color or "#DC2626")
        self.hold.confirmed.connect(self._activate)
        self.body.addWidget(self.hold)
        self.add_cancel()

    def _activate(self) -> None:
        if self.ctx.settings.get_bool("alarm.require_password") and not self.ctx.auth.verify_current_password(
                self.password.text()):
            self.show_error("Password incorrect. The alarm was not activated.")
            return
        zones = self.zones.selected_ids()
        if not zones:
            self.show_error("Select at least one zone.")
            return
        try:
            self.activated = self.ctx.alarms.trigger(self.alarm.id, zones, interrupt=True)
        except AppError as exc:
            self.show_error(exc)
            return
        self.accept()


class AlarmConfigDialog(Dialog):
    def __init__(self, page, alarm=None):
        super().__init__(page, f"Configure {alarm.name}" if alarm else "New alarm type",
                         "Sound, voice message, duration and repetition.", "sliders", width=640, scroll=True)
        self.ctx = page.ctx
        self.page = page
        self.alarm = alarm
        self.setMinimumHeight(640)
        self.name = QLineEdit(alarm.name if alarm else "")
        self.icon = QComboBox()
        for name in ALARM_ICONS:
            self.icon.addItem(icon(name, theme.hex("text_secondary"), 16), name.replace("_", " ").title(), name)
        self.icon.setCurrentIndex(max(0, self.icon.findData(alarm.icon if alarm else "siren")))
        self.body.addLayout(self.row(self.field("Alarm name", self.name), self.field("Icon", self.icon), stretch=[3, 2]))
        self.description = QLineEdit(alarm.description if alarm else "")
        self.body.addWidget(self.field("Description", self.description))
        self.color = ColorSwatches(alarm.color if alarm else "#DC2626")
        self.body.addWidget(self.field("Color", self.color))
        self.source = SegmentedControl([("builtin", "Built-in sound", "siren"), ("audio_file", "Custom sound", "upload")])
        self.body.addWidget(self.field("Alarm sound", self.source))
        self.stack = QStackedWidget()
        builtin = QWidget()
        builtin.setObjectName("Transparent")
        bl = QHBoxLayout(builtin)
        bl.setContentsMargins(0, 0, 0, 0)
        self.pattern = QComboBox()
        for key, name in ALARM_PATTERNS.items():
            self.pattern.addItem(name, key)
        self.pattern.setCurrentIndex(max(0, self.pattern.findData(alarm.builtin_pattern if alarm else "alarm_general")))
        bl.addWidget(self.pattern, 1)
        self.stack.addWidget(builtin)
        custom = QWidget()
        custom.setObjectName("Transparent")
        cl = QHBoxLayout(custom)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        self.file = QComboBox()
        self._fill_files(alarm.audio_file_id if alarm else None)
        cl.addWidget(self.file, 1)
        upload = button("Upload...", "upload", None)
        upload.clicked.connect(self._upload)
        upload.setEnabled(self.ctx.auth.has(P.LIBRARY_MANAGE))
        cl.addWidget(upload)
        self.stack.addWidget(custom)
        self.body.addWidget(self.stack)
        self.source.changed.connect(lambda k: self.stack.setCurrentIndex(0 if k == "builtin" else 1))
        if alarm:
            self.source.set_value(alarm.sound_source)
            self.stack.setCurrentIndex(0 if alarm.sound_source == "builtin" else 1)
        test = button("Test sound locally", "headphones", None, "sm")
        test.clicked.connect(self._test)
        self.body.addWidget(test, alignment=Qt.AlignmentFlag.AlignLeft)
        self.voice = QPlainTextEdit(alarm.voice_message if alarm else "")
        self.voice.setPlaceholderText("Optional spoken instructions played between siren cycles")
        self.voice.setFixedHeight(80)
        self.body.addWidget(self.field("Voice message (text-to-speech)", self.voice))
        self.duration = QSpinBox()
        self.duration.setRange(0, 3600)
        self.duration.setSingleStep(15)
        self.duration.setSuffix(" s")
        self.duration.setSpecialValueText("Until stopped")
        self.duration.setValue(alarm.duration_seconds if alarm else 120)
        self.repeat = QSpinBox()
        self.repeat.setRange(0, 100)
        self.repeat.setSpecialValueText("Continuous")
        self.repeat.setSuffix(" cycle(s)")
        self.repeat.setValue(alarm.repeat_count if alarm else 0)
        self.body.addLayout(self.row(self.field("Duration", self.duration, "Maximum playing time. 'Until stopped' "
                                                "plays until someone presses STOP."),
                                     self.field("Repetition", self.repeat, "Number of siren(+message) cycles. "
                                                "'Continuous' repeats for the whole duration.")))
        en = QHBoxLayout()
        en.addWidget(label("Alarm enabled"), 1)
        self.enabled = ToggleSwitch(alarm.is_enabled if alarm else True)
        en.addWidget(self.enabled)
        self.body.addLayout(en)
        self.add_cancel()
        self.add_button("Save alarm", "primary", self._save, "check", default=True)

    def _fill_files(self, selected) -> None:
        self.file.clear()
        files = self.ctx.library.files()
        files.sort(key=lambda f: (f.category != "Emergency Alarms", f.name.lower()))
        for f in files:
            self.file.addItem(f"{f.name}  ·  {f.category}  ·  {format_duration(f.duration_seconds)}", f.id)
        if selected:
            self.file.setCurrentIndex(max(0, self.file.findData(selected)))

    def _upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an alarm sound", "", FILE_DIALOG_FILTER)
        if path:
            try:
                item = self.ctx.library.import_file(path, category="Emergency Alarms")
            except AppError as exc:
                self.show_error(exc)
                return
            self._fill_files(item.id)

    def _test(self) -> None:
        try:
            if self.source.value() == "builtin":
                source = self.ctx.library.builtin_source(self.pattern.currentData())
            else:
                if not self.file.currentData():
                    self.show_error("Choose a sound first.")
                    return
                source = self.ctx.library.source_for(self.file.currentData())
            self.ctx.preview.play(RepeatSource(source, count=1, max_seconds=6), self.ctx.settings.get(
                "audio.preview_output") or "", volume=0.35, token=("alarmtest", 0))
            self.page.toast("info", "Testing alarm sound locally", "Plays 6 seconds at reduced volume on this "
                                                                   "computer only.")
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)

    def _save(self) -> None:
        data = dict(name=self.name.text(), description=self.description.text(), sound_source=self.source.value(),
                    builtin_pattern=self.pattern.currentData(), audio_file_id=self.file.currentData(),
                    voice_message=self.voice.toPlainText(), duration_seconds=self.duration.value(),
                    repeat_count=self.repeat.value(), color=self.color.value(), icon=self.icon.currentData(),
                    is_enabled=self.enabled.isChecked())
        try:
            self.saved = self.ctx.alarms.save(data, self.alarm.id if self.alarm else None)
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        self.accept()

    def done(self, result):
        if self.ctx.preview.token == ("alarmtest", 0):
            self.ctx.preview.stop()
        super().done(result)


class AlarmCard(Card):
    def __init__(self, page, alarm):
        super().__init__(padding=18, spacing=10)
        self.set_accent(alarm.color)
        ctx = page.ctx
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(IconBubble(alarm.icon or "siren", "danger", 48, 24, color=alarm.color))
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(ElidedLabel(alarm.name, "h2"))
        texts.addWidget(label(alarm.description or "Custom alarm", "caption", wrap=True))
        top.addLayout(texts, 1)
        self.body.addLayout(top)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        meta.addWidget(Badge(ctx.alarms.describe_timing(alarm), "neutral", small=True))
        if alarm.voice_message:
            meta.addWidget(Badge("Voice message", "info", small=True))
        if alarm.sound_source == "audio_file":
            meta.addWidget(Badge("Custom sound", "purple", small=True))
        if not alarm.is_enabled:
            meta.addWidget(Badge("Disabled", "warning", small=True))
        meta.addStretch(1)
        self.body.addLayout(meta)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        activate = button("ACTIVATE", "siren", "danger")
        activate.setEnabled(page.can(P.ALARMS_TRIGGER) and alarm.is_enabled)
        if not page.can(P.ALARMS_TRIGGER):
            activate.setToolTip("Your account is not authorized to trigger alarms.")
        activate.clicked.connect(lambda: page.activate(alarm))
        actions.addWidget(activate, 1)
        test = button("", "headphones", "ghost", tooltip="Test the sound locally (this computer only)")
        test.clicked.connect(lambda: page.test_sound(alarm))
        actions.addWidget(test)
        if page.can(P.ALARMS_MANAGE):
            cfg = button("", "sliders", "ghost", tooltip="Configure alarm")
            cfg.clicked.connect(lambda: page.configure(alarm))
            actions.addWidget(cfg)
            if not alarm.is_system:
                delete = button("", "trash", "ghost", tooltip="Delete custom alarm", icon_color="danger")
                delete.clicked.connect(lambda: page.delete(alarm))
                actions.addWidget(delete)
        self.body.addLayout(actions)


class AlarmPage(BasePage):
    title = "Alarm Management"
    subtitle = "Emergency alarms override every regular broadcast. Activation requires press-and-hold confirmation."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        self.new_button = self.header.add_action(button("New alarm type", "plus", None))
        self.new_button.clicked.connect(lambda: self.configure(None))
        self.new_button.setVisible(self.can(P.ALARMS_MANAGE))

        # active emergency panel
        self.active_card = Card(padding=22, spacing=12)
        self.active_card.set_fill("danger_soft", "danger")
        head = QHBoxLayout()
        head.setSpacing(14)
        self.active_dot = StatusDot("danger", 16, pulse=True)
        head.addWidget(self.active_dot)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.active_title = label("", "h1")
        self.active_detail = label("", "muted", wrap=True)
        texts.addWidget(self.active_title)
        texts.addWidget(self.active_detail)
        head.addLayout(texts, 1)
        self.active_elapsed = label("")
        self.active_elapsed.setStyleSheet("font-size: 26pt; font-weight: 700;")
        head.addWidget(self.active_elapsed)
        self.stop_button = button("STOP ALARM", "stop", "danger", "xl")
        self.stop_button.clicked.connect(self.stop_alarm)
        head.addWidget(self.stop_button)
        self.active_card.body.addLayout(head)
        self.active_card.hide()
        self.body.addWidget(self.active_card)

        if not self.can(P.ALARMS_TRIGGER):
            self.body.addWidget(InfoBanner("Your account can view alarms but is not authorized to trigger them. "
                                           "An administrator can grant the <b>Trigger emergency alarms</b> "
                                           "permission.", "warning"))
        grid_host = QWidget()
        grid_host.setObjectName("Transparent")
        self.grid = ResponsiveGrid(grid_host, min_width=300, spacing=14, max_columns=3)
        self.body.addWidget(grid_host)

        log_card = Card(padding=18, spacing=10)
        log_card.body.addWidget(SectionHeader("Alarm log", "Every activation and cancellation, newest first", "history"))
        self.log = DataTable([
            Column("Started", lambda r: r.started_at[:16], width=140, kind="muted"),
            Column("Alarm", lambda r: r.title, kind="bold", width=200),
            Column("Zones", lambda r: r.zone_names, stretch=True),
            Column("Activated by", lambda r: r.initiated_by_name, width=150),
            Column("Duration", lambda r: format_duration(r.duration_seconds), width=90),
            Column("Status", lambda r: STATUS_LABELS.get(r.status, r.status), kind="badge", width=120,
                   tone=lambda r: STATUS_TONES.get(r.status, "neutral")),
            Column("Ended", lambda r: r.ended_by_name or "—", width=150, kind="muted",
                   tooltip=lambda r: r.status_detail),
        ], row_height=42)
        self.log.setMinimumHeight(260)
        log_card.body.addWidget(self.log)
        self.log_empty = EmptyState("shield", "No alarms recorded", "Activations and cancellations will be logged "
                                                                    "here automatically.")
        log_card.body.addWidget(self.log_empty)
        self.body.addWidget(log_card)
        self.body.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        ctx.bus.broadcast_state_changed.connect(lambda: self.refresh() if self.isVisible() else None)
        ctx.bus.data_changed.connect(lambda name: self.refresh() if name in ("alarms", "history") and self.isVisible()
                                     else None)

    def on_show(self) -> None:
        self.refresh()
        self._timer.start(500)

    def on_hide(self) -> None:
        self._timer.stop()
        if isinstance(self.ctx.preview.token, tuple) and self.ctx.preview.token[0] == "alarmtest":
            self.ctx.preview.stop()

    def refresh(self) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for alarm in self.ctx.alarms.alarm_types():
            self.grid.addWidget(AlarmCard(self, alarm))
        records = self.ctx.alarms.alarm_log(200)
        self.log.set_rows(records)
        self.log.setVisible(bool(records))
        self.log_empty.setVisible(not records)
        self._tick()

    def _tick(self) -> None:
        active = self.ctx.controller.active
        if active is not None and active.is_emergency:
            self.active_title.setText(f"{active.title.upper()} ACTIVE")
            self.active_detail.setText(f"Sounding in {active.zone_names} · activated by {active.initiated_by} at "
                                       f"{active.started_at.strftime('%I:%M:%S %p').lstrip('0')}")
            self.active_elapsed.setText(format_duration(active.elapsed))
            self.stop_button.setEnabled(self.can(P.ALARMS_TRIGGER))
            self.active_card.show()
        else:
            self.active_card.hide()

    # ---------------------------------------------------------------- actions
    def activate(self, alarm) -> None:
        dialog = ActivateDialog(self, alarm)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.activated is not None:
            self.toast("error", f"{alarm.name} activated", f"Sounding in {dialog.activated.zone_names}.")
            self.refresh()

    def stop_alarm(self) -> None:
        self.window_.stop_broadcast()
        self.refresh()

    def test_sound(self, alarm) -> None:
        player = self.ctx.preview
        if player.playing and player.token == ("alarmtest", alarm.id):
            player.stop()
            return
        try:
            source = RepeatSource(self.ctx.alarms.siren_source(alarm), count=1, max_seconds=6)
            device = player.play(source, self.ctx.settings.get("audio.preview_output") or "", volume=0.35,
                                 token=("alarmtest", alarm.id))
            self.toast("info", "Testing alarm sound locally", f"6 seconds at reduced volume on {device} only - zones "
                                                              "are not affected. Click again to stop.")
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc)

    def configure(self, alarm) -> None:
        dialog = AlarmConfigDialog(self, alarm)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Alarm saved", dialog.saved.name)
            self.refresh()

    def delete(self, alarm) -> None:
        if confirm(self, "Delete alarm type?", f"<b>{alarm.name}</b> will be removed. Its log entries are kept.",
                   "Delete", danger=True, icon_name="trash"):
            self.guarded(self.ctx.alarms.delete, alarm.id, success="Alarm type deleted")
