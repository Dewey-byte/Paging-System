"""Announcement management: create (TTS / audio file / recording), preview, approve and broadcast."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from core.announcement_manager import SOURCE_TYPES
from core.errors import AppError
from core.history_manager import format_duration
from core.permissions import P
from services.audio_decoder import FILE_DIALOG_FILTER
from services.audio_sources import BufferSource

from .components.base import (Badge, Card, ClampLabel, ElidedLabel, EmptyState, IconBubble, InfoBanner, button,
                              clear_layout, label)
from .components.controls import SearchBox, SegmentedControl, Spinner
from .components.dialogs import Dialog, confirm
from .components.flow_layout import ResponsiveGrid
from .components.page import BasePage
from .components.speech import TTSEditor, VoiceRecorderPanel
from .components.zone_selector import ZoneSelector
from .workers import run_async

CATEGORY_TONES = {
    "General Announcement": "accent", "Academic Announcement": "info", "Faculty Announcement": "purple",
    "Student Announcement": "success", "Event Announcement": "warning", "Class Suspension": "warning",
    "Administrative Announcement": "neutral", "Emergency Announcement": "danger",
}
SOURCE_ICONS = {"tts": "type", "audio_file": "music", "recording": "mic"}


class AnnouncementDialog(Dialog):
    def __init__(self, page, announcement=None):
        super().__init__(page, "Edit announcement" if announcement else "New announcement",
                         "Compose the message, choose how it plays and where it is heard.", "megaphone", width=760,
                         scroll=True)
        self.page = page
        self.ctx = page.ctx
        self.ann = announcement
        self.broadcast_after = False
        self.setMinimumHeight(720)

        self.title_edit = QLineEdit(announcement.title if announcement else "")
        self.title_edit.setPlaceholderText("e.g. Assembly reminder")
        self.category = QComboBox()
        for name in self.ctx.announcements.CATEGORIES:
            self.category.addItem(name, name)
        if announcement:
            self.category.setCurrentIndex(max(0, self.category.findData(announcement.category)))
        self.body.addLayout(self.row(self.field("Title", self.title_edit), self.field("Category", self.category),
                                     stretch=[3, 2]))

        self.source = SegmentedControl([("tts", "Text-to-speech", "type"), ("audio_file", "Audio file", "music"),
                                        ("recording", "Record voice", "mic")])
        self.body.addWidget(self.field("How will it play?", self.source))
        self.stack = QStackedWidget()
        self.body.addWidget(self.stack)

        # --- TTS
        self.tts_editor = TTSEditor(self.ctx, announcement.message if announcement else "",
                                    announcement.tts_voice if announcement else "",
                                    announcement.tts_rate if announcement else None,
                                    announcement.tts_volume if announcement else None,
                                    placeholder="Type the announcement exactly as it should be spoken...")
        self.stack.addWidget(self.tts_editor)

        # --- audio file
        audio = QWidget()
        audio.setObjectName("Transparent")
        al = QVBoxLayout(audio)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(12)
        self.file_combo = QComboBox()
        self._fill_files(announcement.audio_file_id if announcement and announcement.source_type == "audio_file"
                         else None)
        upload = button("Upload file...", "upload", None)
        upload.clicked.connect(self._upload)
        upload.setEnabled(self.ctx.auth.has(P.LIBRARY_MANAGE))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.file_combo, 1)
        row.addWidget(upload)
        holder = QWidget()
        holder.setObjectName("Transparent")
        holder.setLayout(row)
        al.addWidget(self.field("Audio from the library", holder, "MP3, WAV, FLAC, OGG and AIFF are supported."))
        self.stack.addWidget(audio)

        # --- recording
        existing = announcement.audio_file_name if announcement and announcement.source_type == "recording" else ""
        self.recorder_panel = VoiceRecorderPanel(self.ctx, existing)
        self.recorder_panel.error.connect(self.show_error)
        self.recorder_panel.changed.connect(self.clear_error)
        self.stack.addWidget(self.recorder_panel)

        self.source.changed.connect(lambda key: self.stack.setCurrentIndex(["tts", "audio_file", "recording"].index(key)))
        if announcement:
            self.source.set_value(announcement.source_type)
            self.stack.setCurrentIndex(["tts", "audio_file", "recording"].index(announcement.source_type))

        # --- zones & options
        self.zones = ZoneSelector(self.ctx, min_tile_width=170)
        self.zones.set_selected(announcement.zone_ids if announcement and announcement.zone_ids else
                                [self.ctx.zones.campus_zone().id] if self.ctx.zones.campus_zone() else [])
        self.body.addWidget(self.field("Target zones", self.zones))
        options = QHBoxLayout()
        options.setSpacing(18)
        self.chime = QCheckBox("Intro chime")
        self.chime.setToolTip("Play the intro chime before the message")
        self.chime.setChecked(announcement.play_chime if announcement else True)
        options.addWidget(self.chime)
        self.outro_chime = QCheckBox("Outro chime")
        self.outro_chime.setToolTip("Play the outro chime after the message")
        self.outro_chime.setChecked(announcement.play_outro_chime if announcement else True)
        options.addWidget(self.outro_chime)
        options.addWidget(label("Repeat"))
        self.repeat = QSpinBox()
        self.repeat.setRange(1, 5)
        self.repeat.setSuffix(" time(s)")
        self.repeat.setValue(announcement.repeat_count if announcement else 1)
        options.addWidget(self.repeat)
        self.approved = QCheckBox("Approved for operators")
        self.approved.setChecked(announcement.is_approved if announcement else True)
        self.approved.setVisible(self.ctx.auth.has(P.ANNOUNCEMENTS_APPROVE))
        options.addWidget(self.approved)
        options.addStretch(1)
        self.body.addLayout(options)
        if not self.ctx.auth.has(P.ANNOUNCEMENTS_APPROVE):
            self.body.addWidget(InfoBanner("Announcements you create will be available to operators after an "
                                           "administrator approves them. You can still broadcast them yourself.",
                                           "info"))

        self.preview_spinner = Spinner(20)
        self.footer_left.addWidget(self.preview_spinner)
        self.preview_button = button("Preview", "headphones", None)
        self.preview_button.clicked.connect(self._preview)
        self.footer_left.addWidget(self.preview_button)
        self.add_cancel()
        self.add_button("Save", None, lambda: self._save(False), "save")
        broadcast = self.add_button("Save && broadcast now", "primary", lambda: self._save(True), "radio",
                                    default=True)
        broadcast.setEnabled(self.ctx.auth.has(P.ANNOUNCEMENTS_BROADCAST))

    # ---------------------------------------------------------------- helpers
    def _fill_files(self, selected: int | None) -> None:
        self.file_combo.clear()
        files = self.ctx.library.files()
        files.sort(key=lambda f: (f.category != "School Announcements", f.is_builtin, f.name.lower()))
        if not files:
            self.file_combo.addItem("The audio library is empty - upload a file", None)
        for f in files:
            self.file_combo.addItem(f"{f.name}  ·  {f.category}  ·  {format_duration(f.duration_seconds)}", f.id)
        if selected:
            self.file_combo.setCurrentIndex(max(0, self.file_combo.findData(selected)))

    def _upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an audio file", "", FILE_DIALOG_FILTER)
        if not path:
            return
        try:
            item = self.ctx.library.import_file(path, category="School Announcements")
        except AppError as exc:
            self.show_error(exc)
            return
        self._fill_files(item.id)
        self.page.toast("success", "Audio uploaded", item.name)

    def _draft(self) -> dict:
        tts = self.tts_editor
        return dict(title=self.title_edit.text(), message=tts.text(),
                    category=self.category.currentData(), source_type=self.source.value(),
                    audio_file_id=self.file_combo.currentData() if self.source.value() == "audio_file" else
                    (self.ann.audio_file_id if self.ann and self.ann.source_type == "recording" else None),
                    tts_voice=tts.voice_id(), tts_rate=tts.rate_value(), tts_volume=tts.volume_value(),
                    play_chime=self.chime.isChecked(), play_outro_chime=self.outro_chime.isChecked(),
                    repeat_count=self.repeat.value(), is_approved=self.approved.isChecked())

    # ---------------------------------------------------------------- preview
    def _preview(self) -> None:
        player = self.ctx.preview
        if player.playing and player.token == self:
            player.stop()
            self.preview_button.setText("Preview")
            return
        mode = self.source.value()
        self.clear_error()
        try:
            if mode == "recording":
                recording = self.recorder_panel.recording
                if recording is None:
                    if self.ann and self.ann.source_type == "recording" and self.ann.audio_file_id:
                        self._play(self.ctx.library.source_for(self.ann.audio_file_id))
                    else:
                        self.show_error("Record something first.")
                    return
                self._play(BufferSource(*recording))
            elif mode == "audio_file":
                if not self.file_combo.currentData():
                    self.show_error("Choose an audio file first.")
                    return
                self._play(self.ctx.library.source_for(self.file_combo.currentData()))
            else:
                if not self.tts_editor.text().strip():
                    self.show_error("Type a message to preview.")
                    return
                self.preview_spinner.start()
                self.preview_button.setEnabled(False)

                def done(result):
                    self.preview_spinner.stop()
                    self.preview_button.setEnabled(True)
                    self._play(BufferSource(*result))

                def failed(exc):
                    self.preview_spinner.stop()
                    self.preview_button.setEnabled(True)
                    self.show_error(f"Speech synthesis failed: {exc}")

                self.tts_editor.render_async(done, failed)
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)

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

    # ------------------------------------------------------------------ save
    def _save(self, broadcast: bool) -> None:
        self.clear_error()
        data = self._draft()
        try:
            if data["source_type"] == "recording" and self.recorder_panel.recording is not None:
                name = (data["title"] or "Voice recording").strip()
                item = self.ctx.library.save_recording(*self.recorder_panel.recording, name=name)
                data["audio_file_id"] = item.id
                self.recorder_panel.recording = None
            saved =self.ctx.announcements.save(data, self.zones.selected_ids(), self.ann.id if self.ann else None)
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        self.saved = saved
        self.broadcast_after = broadcast
        if saved.source_type == "tts":
            run_async(lambda: self.ctx.announcements.prewarm(saved))
        self.accept()

    def done(self, result):  # noqa: D401 - QDialog API
        self.recorder_panel.shutdown()
        if self.ctx.preview.token == self:
            self.ctx.preview.stop()
        super().done(result)


class AnnouncementCard(Card):
    def __init__(self, page, ann):
        super().__init__(padding=18, spacing=10)
        self.page = page
        ctx = page.ctx
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(Badge(ann.category.replace(" Announcement", ""), CATEGORY_TONES.get(ann.category, "neutral"),
                            dot=True, small=True))
        if not ann.is_approved:
            top.addWidget(Badge("Pending approval", "warning", small=True))
        top.addStretch(1)
        top.addWidget(IconBubble(SOURCE_ICONS.get(ann.source_type, "megaphone"), "neutral", 28, 14))
        self.body.addLayout(top)
        title = ElidedLabel(ann.title, "h3")
        self.body.addWidget(title)
        if ann.source_type == "tts":
            preview = ann.message
        else:
            preview = f"{SOURCE_TYPES[ann.source_type]}: {ann.audio_file_name or 'missing audio'}" + \
                      (f" — {ann.message}" if ann.message else "")
        self.body.addWidget(ClampLabel(preview, "muted", lines=2))
        zones = ctx.zones.describe_zones(ann.zone_ids) if ann.zone_ids else "Entire Campus"
        meta = label(f"{zones}  ·  ~{format_duration(ctx.announcements.estimate_duration(ann))}"
                     f"  ·  played {ann.broadcast_count}x", "caption", wrap=True)
        self.body.addWidget(meta)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        can_broadcast = page.can(P.ANNOUNCEMENTS_BROADCAST) and (ann.is_approved or page.can(P.ANNOUNCEMENTS_MANAGE))
        broadcast = button("Broadcast", "radio", "primary", "sm")
        broadcast.setEnabled(can_broadcast)
        broadcast.clicked.connect(lambda: page.broadcast(ann))
        actions.addWidget(broadcast)
        self.preview_btn = button("Preview", "headphones", None, "sm")
        self.preview_btn.clicked.connect(lambda: page.preview(ann, self.preview_btn))
        actions.addWidget(self.preview_btn)
        actions.addStretch(1)
        if page.can(P.ANNOUNCEMENTS_APPROVE) and not ann.is_approved:
            approve = button("", "check_circle", "ghost", tooltip="Approve for operators", icon_color="success")
            approve.clicked.connect(lambda: page.guarded(ctx.announcements.set_approved, ann.id, True,
                                                         success="Announcement approved"))
            actions.addWidget(approve)
        if page.can(P.ANNOUNCEMENTS_MANAGE):
            edit = button("", "edit", "ghost", tooltip="Edit")
            edit.clicked.connect(lambda: page.edit(ann))
            delete = button("", "trash", "ghost", tooltip="Delete", icon_color="danger")
            delete.clicked.connect(lambda: page.delete(ann))
            actions.addWidget(edit)
            actions.addWidget(delete)
        self.body.addLayout(actions)


class AnnouncementsPage(BasePage):
    title = "Announcements"
    subtitle = "Create, preview and broadcast school announcements."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        self.new_button = self.header.add_action(button("New announcement", "plus", "primary"))
        self.new_button.clicked.connect(self.new_announcement)
        self.new_button.setVisible(self.can(P.ANNOUNCEMENTS_MANAGE))

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = SearchBox("Search announcements...")
        self.search.textChanged.connect(lambda: self._debounce.start(200))
        bar.addWidget(self.search, 2)
        self.category = QComboBox()
        self.category.addItem("All categories", "")
        for name in ctx.announcements.CATEGORIES:
            self.category.addItem(name, name)
        self.category.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.category, 1)
        bar.addStretch(1)
        self.count_label = label("", "muted")
        bar.addWidget(self.count_label)
        self.body.addLayout(bar)

        grid_host = QWidget()
        grid_host.setObjectName("Transparent")
        self.grid = ResponsiveGrid(grid_host, min_width=320, spacing=14, max_columns=4)
        self.body.addWidget(grid_host)
        self.empty = EmptyState("megaphone", "No announcements yet",
                                "Create a text-to-speech message, upload audio or record your voice.",
                                "New announcement" if self.can(P.ANNOUNCEMENTS_MANAGE) else "")
        self.empty.action_clicked.connect(self.new_announcement)
        self.body.addWidget(self.empty)
        self.body.addStretch(1)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
        ctx.bus.data_changed.connect(lambda name: self.refresh() if name in ("announcements", "library", "zones")
                                     and self.isVisible() else None)

    def on_show(self) -> None:
        self.refresh()

    def on_hide(self) -> None:
        if self.ctx.preview.token and isinstance(self.ctx.preview.token, tuple) and self.ctx.preview.token[0] == "ann":
            self.ctx.preview.stop()

    def refresh(self) -> None:
        items = self.ctx.announcements.list(self.search.text().strip(), self.category.currentData() or "")
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for ann in items:
            self.grid.addWidget(AnnouncementCard(self, ann))
        self.empty.setVisible(not items)
        self.count_label.setText(f"{len(items)} announcement{'s' if len(items) != 1 else ''}")

    # ---------------------------------------------------------------- actions
    def new_announcement(self) -> None:
        if not self.can(P.ANNOUNCEMENTS_MANAGE):
            return
        self._open_editor(None)

    def edit(self, ann) -> None:
        self._open_editor(ann)

    def _open_editor(self, ann) -> None:
        dialog = AnnouncementDialog(self, ann)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Announcement saved", dialog.saved.title)
            self.refresh()
            if dialog.broadcast_after:
                self.broadcast(dialog.saved, ask=False)

    def delete(self, ann) -> None:
        if confirm(self, "Delete announcement?", f"<b>{ann.title}</b> will be permanently deleted.", "Delete",
                   danger=True, icon_name="trash"):
            self.guarded(self.ctx.announcements.delete, ann.id, success="Announcement deleted")

    def broadcast(self, ann, ask: bool = True) -> None:
        zones = self.ctx.zones.describe_zones(ann.zone_ids) if ann.zone_ids else "Entire Campus"
        if ask and not confirm(self, "Broadcast now?", f"<b>{ann.title}</b> will play in <b>{zones}</b> now.",
                               "Broadcast", icon_name="radio"):
            return
        if self.start_broadcast(lambda interrupt: self.ctx.announcements.broadcast(ann.id, None, interrupt)):
            self.toast("success", "Broadcasting", f"{ann.title} → {zones}")

    def preview(self, ann, btn) -> None:
        player = self.ctx.preview
        if player.playing and player.token == ("ann", ann.id):
            player.stop()
            btn.setText("Preview")
            return
        btn.setEnabled(False)
        btn.setText("Preparing...")

        def ready(source):
            btn.setEnabled(True)
            try:
                device = player.play(source, self.ctx.settings.get("audio.preview_output") or "", token=("ann", ann.id))
            except Exception as exc:  # noqa: BLE001
                btn.setText("Preview")
                self.toast("error", "Preview failed", str(exc))
                return
            btn.setText("Stop")
            self.toast("info", "Previewing locally", f"Playing on {device} only - not on paging zones.")
            self._watch(btn, ("ann", ann.id))

        def failed(exc):
            btn.setEnabled(True)
            btn.setText("Preview")
            self.handle_error(exc)

        run_async(lambda: self.ctx.announcements.build_source(ann, with_chimes=False), ready, failed)

    def _watch(self, btn, token) -> None:
        def check():
            try:
                if self.ctx.preview.playing and self.ctx.preview.token == token:
                    QTimer.singleShot(300, check)
                else:
                    btn.setText("Preview")
            except RuntimeError:
                pass  # card was rebuilt
        QTimer.singleShot(300, check)
