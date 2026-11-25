"""Audio library: upload, preview, rename, categorize, assign and delete audio files."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QMenu,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from core.errors import AppError
from core.history_manager import format_duration
from core.permissions import P
from services.audio_decoder import FILE_DIALOG_FILTER, SUPPORTED_EXTENSIONS

from .components.base import Card, EmptyState, IconBubble, InfoBanner, button, label
from .components.controls import SearchBox
from .components.dialogs import Dialog, ask_text, confirm
from .components.page import BasePage
from .components.tables import Column, DataTable
from .icons import icon
from .theme import theme
from .workers import run_async

CATEGORY_TONES = {"School Announcements": "accent", "Emergency Alarms": "danger", "School Hymns": "purple",
                  "National Anthem": "info", "Bell Sounds": "warning", "General Audio": "neutral"}
CATEGORY_ICONS = {"School Announcements": "megaphone", "Emergency Alarms": "siren", "School Hymns": "music",
                  "National Anthem": "music", "Bell Sounds": "bell", "General Audio": "waveform"}


def _size(nbytes: int) -> str:
    if nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.0f} KB"
    return f"{nbytes / (1024 * 1024):.1f} MB"


class UploadDialog(Dialog):
    def __init__(self, page, paths: list[str]):
        super().__init__(page, "Upload audio", f"{len(paths)} file(s) selected", "upload", width=520)
        self.ctx = page.ctx
        self.paths = paths
        self.category = QComboBox()
        for name in self.ctx.library.CATEGORIES:
            self.category.addItem(name, name)
        self.category.setCurrentIndex(self.category.findData("General Audio"))
        self.name = QLineEdit(Path(paths[0]).stem if len(paths) == 1 else "")
        if len(paths) == 1:
            self.body.addWidget(self.field("Display name", self.name))
        else:
            listing = "<br>".join(Path(p).name for p in paths[:8]) + ("<br>..." if len(paths) > 8 else "")
            self.body.addWidget(InfoBanner(listing, "info", "music"))
        self.body.addWidget(self.field("Category", self.category))
        self.add_cancel()
        self.add_button("Upload", "primary", self.accept, "upload", default=True)


class AudioLibraryPage(BasePage):
    title = "Audio Library"
    subtitle = "Central library of announcements, bells, hymns, anthems and alarm sounds."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        self.manage = self.can(P.LIBRARY_MANAGE)
        self.upload_button = self.header.add_action(button("Upload audio", "upload", "primary"))
        self.upload_button.clicked.connect(self.upload)
        self.upload_button.setVisible(self.manage)
        self.setAcceptDrops(self.manage)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        self.chips: dict[str, QPushButton] = {}
        for key in [""] + list(ctx.library.CATEGORIES):
            chip = QPushButton(key or "All files")
            chip.setCheckable(True)
            chip.setProperty("chip", True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(self.refresh)
            self.chip_group.addButton(chip)
            chips.addWidget(chip)
            self.chips[key] = chip
        self.chips[""].setChecked(True)
        chips.addStretch(1)
        self.body.addLayout(chips)

        card = Card(padding=16, spacing=12)
        bar = QHBoxLayout()
        self.search = SearchBox("Search by name or file name...")
        self.search.textChanged.connect(lambda: self._debounce.start(200))
        bar.addWidget(self.search, 1)
        bar.addStretch(1)
        self.summary = label("", "muted")
        bar.addWidget(self.summary)
        card.body.addLayout(bar)

        self.player_bar = QWidget()
        self.player_bar.setObjectName("PlayerBar")
        pl = QHBoxLayout(self.player_bar)
        pl.setContentsMargins(12, 8, 12, 8)
        pl.setSpacing(12)
        pl.addWidget(IconBubble("headphones", "accent", 32, 16))
        self.now_playing = label("", "h3")
        pl.addWidget(self.now_playing)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        pl.addWidget(self.progress, 1)
        self.position = label("", "caption")
        pl.addWidget(self.position)
        stop = button("Stop", "stop", None, "sm")
        stop.clicked.connect(self.stop_preview)
        pl.addWidget(stop)
        self.player_bar.hide()
        card.body.addWidget(self.player_bar)

        self.table = DataTable([
            Column("Name", lambda f: f.name, kind="title", stretch=True,
                   subtitle=lambda f: ("Built-in sound" if f.is_builtin else f.original_filename),
                   icon=lambda f: (CATEGORY_ICONS.get(f.category, "music"), CATEGORY_TONES.get(f.category, "neutral"))),
            Column("Category", lambda f: f.category, kind="badge", width=190,
                   tone=lambda f: CATEGORY_TONES.get(f.category, "neutral")),
            Column("Duration", lambda f: format_duration(f.duration_seconds), width=90,
                   sort_key=lambda f: f.duration_seconds),
            Column("Format", lambda f: f"{f.format} · {f.sample_rate // 1000 if f.sample_rate else '?'} kHz", width=110,
                   kind="muted"),
            Column("Size", lambda f: _size(f.size_bytes), width=80, kind="muted", sort_key=lambda f: f.size_bytes),
            Column("Added", lambda f: f.created_at[:10] + (f" · {f.uploaded_by_name}" if f.uploaded_by_name else ""),
                   width=170, kind="muted"),
            Column("", width=170, kind="actions"),
        ], row_height=54)
        from PySide6.QtWidgets import QHeaderView
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 170)
        self.table.set_actions(self._row_actions)
        self.table.row_activated.connect(self.preview)
        self.table.setMinimumHeight(460)
        card.body.addWidget(self.table)
        self.empty = EmptyState("music", "No audio in this category",
                                "Upload MP3 or WAV files, or drag and drop them onto this page.",
                                "Upload audio" if self.manage else "", "upload")
        self.empty.action_clicked.connect(self.upload)
        card.body.addWidget(self.empty)
        self.body.addWidget(card, 1)
        hint = "Tip: drag and drop audio files anywhere on this page to upload them." if self.manage else \
            "Your role can preview library audio. Uploading requires the Manage audio library permission."
        self.body.addWidget(label(hint, "caption"))

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._playing_id = None
        ctx.bus.data_changed.connect(lambda name: self.refresh() if name == "library" and self.isVisible() else None)
        theme.changed.connect(self._style_player)
        self._style_player()

    def _style_player(self) -> None:
        self.player_bar.setStyleSheet(f"QWidget#PlayerBar {{ background: {theme.hex('accent_soft')}; "
                                      f"border-radius: 10px; }}")

    def on_show(self) -> None:
        self.refresh()

    def on_hide(self) -> None:
        self.stop_preview()

    def refresh(self) -> None:
        category = next((k for k, chip in self.chips.items() if chip.isChecked()), "")
        files = self.ctx.library.files(category or None, self.search.text().strip())
        self.table.set_rows(files)
        self.table.setVisible(bool(files))
        self.empty.setVisible(not files)
        counts = self.ctx.library.category_counts()
        total = sum(counts.values())
        self.chips[""].setText(f"All files  {total}")
        for key in self.ctx.library.CATEGORIES:
            self.chips[key].setText(f"{key}  {counts.get(key, 0)}")
        self.summary.setText(f"{len(files)} file(s) · {format_duration(sum(f.duration_seconds for f in files))} total")

    # ---------------------------------------------------------------- rows
    def _row_actions(self, item) -> QWidget:
        holder = QWidget()
        holder.setObjectName("Transparent")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        play = button("", "stop" if self._playing_id == item.id else "play", "subtle", tooltip="Preview locally")
        play.clicked.connect(lambda: self.preview(item))
        layout.addWidget(play)
        more = button("", "more", "ghost", tooltip="More actions")
        more.clicked.connect(lambda: self._menu(item, more))
        layout.addWidget(more)
        layout.addStretch(1)
        return holder

    def _menu(self, item, anchor) -> None:
        menu = QMenu(self)
        color = theme.hex("text_secondary")
        rename = menu.addAction(icon("edit", color, 16), "Rename...", lambda: self.rename(item))
        cat_menu = menu.addMenu(icon("tag", color, 16), "Change category")
        for name in self.ctx.library.CATEGORIES:
            action = cat_menu.addAction(name, lambda n=name: self.guarded(self.ctx.library.set_category, item.id, n,
                                                                          success="Category updated"))
            action.setCheckable(True)
            action.setChecked(name == item.category)
        menu.addSeparator()
        use = menu.addAction(icon("megaphone", color, 16), "Use in a new announcement", lambda: self.use_in_announcement(item))
        use.setEnabled(self.can(P.ANNOUNCEMENTS_MANAGE))
        alarm_menu = menu.addMenu(icon("siren", color, 16), "Set as alarm sound")
        alarm_menu.setEnabled(self.can(P.ALARMS_MANAGE))
        for alarm in self.ctx.alarms.alarm_types():
            alarm_menu.addAction(alarm.name, lambda a=alarm: self.assign_alarm(item, a))
        menu.addSeparator()
        delete = menu.addAction(icon("trash", theme.hex("danger"), 16), "Delete", lambda: self.delete(item))
        for action in (rename, delete):
            action.setEnabled(self.manage and not (item.is_builtin and action is delete))
        cat_menu.setEnabled(self.manage)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ------------------------------------------------------------- actions
    def upload(self, paths: list[str] | None = None) -> None:
        if not self.manage:
            return
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "Upload audio files", "", FILE_DIALOG_FILTER)
        if not paths:
            return
        dialog = UploadDialog(self, paths)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        category = dialog.category.currentData()
        name = dialog.name.text().strip() if len(paths) == 1 else None
        ok, errors = 0, []
        for path in paths:
            try:
                self.ctx.library.import_file(path, name=name, category=category)
                ok += 1
            except AppError as exc:
                errors.append(exc.message)
        if ok:
            self.toast("success", f"Uploaded {ok} file(s)", category)
        for message in errors[:3]:
            self.toast("error", "Upload failed", message)
        self.refresh()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(Path(u.toLocalFile()).suffix.lower() in SUPPORTED_EXTENSIONS for u in urls):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if Path(u.toLocalFile()).suffix.lower() in SUPPORTED_EXTENSIONS]
        if paths:
            self.upload(paths)

    def rename(self, item) -> None:
        name = ask_text(self, "Rename audio", "Display name", item.name)
        if name is not None:
            self.guarded(self.ctx.library.rename, item.id, name, success="Audio renamed")

    def delete(self, item) -> None:
        refs = self.ctx.library.references(item.id)
        if refs:
            used = "; ".join(f"{k}: {', '.join(v)}" for k, v in refs.items())
            self.toast("warning", "Audio is in use", f"Reassign it first ({used}).")
            return
        if confirm(self, "Delete audio file?", f"<b>{item.name}</b> will be removed from the library and deleted "
                                               "from disk.", "Delete", danger=True, icon_name="trash"):
            if self._playing_id == item.id:
                self.stop_preview()
            self.guarded(self.ctx.library.delete, item.id, success="Audio deleted")

    def use_in_announcement(self, item) -> None:
        page = self.window_.page("announcements")
        if page is None:
            return
        from .announcements import AnnouncementDialog
        dialog = AnnouncementDialog(page)
        dialog.title_edit.setText(item.name)
        dialog.source.set_value("audio_file")
        dialog.stack.setCurrentIndex(1)
        dialog._fill_files(item.id)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            page.toast("success", "Announcement saved", dialog.saved.title)
            page.refresh()
            if dialog.broadcast_after:
                page.broadcast(dialog.saved, ask=False)

    def assign_alarm(self, item, alarm) -> None:
        data = dict(name=alarm.name, description=alarm.description, sound_source="audio_file",
                    builtin_pattern=alarm.builtin_pattern, audio_file_id=item.id, voice_message=alarm.voice_message,
                    duration_seconds=alarm.duration_seconds, repeat_count=alarm.repeat_count, color=alarm.color,
                    icon=alarm.icon, is_enabled=alarm.is_enabled)
        if self.guarded(self.ctx.alarms.save, data, alarm.id):
            self.toast("success", "Alarm sound updated", f"{alarm.name} now uses '{item.name}'.")

    # ------------------------------------------------------------- preview
    def preview(self, item) -> None:
        player = self.ctx.preview
        if self._playing_id == item.id and player.playing:
            self.stop_preview()
            return

        def ready(source):
            try:
                device = player.play(source, self.ctx.settings.get("audio.preview_output") or "",
                                     volume=0.5 if item.category == "Emergency Alarms" else 0.9, token=("lib", item.id))
            except Exception as exc:  # noqa: BLE001
                self.toast("error", "Preview failed", str(exc))
                return
            self._playing_id = item.id
            self.now_playing.setText(f"{item.name}  ·  on {device}")
            self.player_bar.show()
            self._tick_timer.start(100)
            self.refresh()

        run_async(lambda: self.ctx.library.source_for(item.id), ready, self.handle_error)

    def stop_preview(self) -> None:
        if self._playing_id is not None:
            self.ctx.preview.stop()
        self._playing_id = None
        self._tick_timer.stop()
        self.player_bar.hide()
        if self.isVisible():
            self.refresh()

    def _tick(self) -> None:
        player = self.ctx.preview
        if not player.playing or player.token != ("lib", self._playing_id):
            self.stop_preview()
            return
        duration = player.duration or 1
        self.progress.setValue(int(1000 * min(1.0, player.position / duration)))
        self.position.setText(f"{format_duration(player.position)} / {format_duration(duration)}")
