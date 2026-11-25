"""System settings: school profile, audio, zones, TTS, alarms, application, security, backup and database."""
from __future__ import annotations

import os
import platform
import sys
from datetime import datetime
from pathlib import Path

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from core import paths, windows_startup
from core.backup_manager import BACKUP_EXT, read_manifest
from core.permissions import P
from services.audio_input import gain_db_from_slider
from services.audio_sources import BufferSource
from services.device_detection import sd
from services.sound_generator import CHIME_SOUNDS

from .branding import school_logo
from .components.base import Card, InfoBanner, SectionHeader, button, divider, label
from .components.controls import SegmentedControl, SliderField, ToggleSwitch
from .components.dialogs import alert, confirm
from .components.page import BasePage
from .icons import icon
from .theme import theme
from .workers import run_async

SECTIONS = [
    ("school", "School profile", "school"), ("audio", "Audio devices", "speaker"), ("zones", "Zone volumes", "sliders"),
    ("tts", "Text-to-speech", "type"), ("alarms", "Emergency alarms", "siren"), ("app", "Application", "settings"),
    ("security", "Security", "lock"), ("backup", "Backup & restore", "archive"), ("database", "Database", "database"),
    ("about", "About", "info"),
]


class SettingRow(QWidget):
    """Label + description on the left, control on the right."""

    def __init__(self, title: str, description: str, control: QWidget):
        super().__init__()
        self.setObjectName("Transparent")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(label(title, "h3"))
        if description:
            texts.addWidget(label(description, "caption", wrap=True))
        layout.addLayout(texts, 1)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)


class SettingsPage(BasePage):
    title = "System Settings"
    subtitle = "Configure the school profile, audio hardware, alarms, application behavior and data."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        self.settings = ctx.settings
        row = QHBoxLayout()
        row.setSpacing(18)
        self.nav = QListWidget()
        self.nav.setFixedWidth(230)
        self.nav.setObjectName("SettingsNav")
        self.nav.setSpacing(2)
        from PySide6.QtCore import QSize
        self.nav.setIconSize(QSize(18, 18))
        for key, text, icon_name in SECTIONS:
            item = QListWidgetItem(icon(icon_name, theme.hex("text_secondary"), 18), f"  {text}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
        self.nav.setFixedHeight(len(SECTIONS) * 42 + 10)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_card = Card(padding=8, spacing=0)
        nav_card.body.addWidget(self.nav)
        nav_card.setFixedWidth(260)
        row.addWidget(nav_card, 0, Qt.AlignmentFlag.AlignTop)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        self.body.addLayout(row, 1)
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.timeout.connect(lambda: self.toast("success", "Settings saved"))
        builders = {"school": self._school, "audio": self._audio, "zones": self._zones, "tts": self._tts,
                    "alarms": self._alarms, "app": self._app, "security": self._security, "backup": self._backup,
                    "database": self._database, "about": self._about}
        for key, text, icon_name in SECTIONS:
            card = Card(padding=24, spacing=10)
            card.body.addWidget(SectionHeader(text, "", icon_name))
            card.body.addWidget(divider())
            builders[key](card.body)
            card.body.addStretch(1)
            self.stack.addWidget(card)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        theme.changed.connect(self._style_nav)
        self._style_nav()

    def _style_nav(self) -> None:
        self.nav.setStyleSheet(
            f"QListWidget#SettingsNav {{ background: transparent; border: none; }}"
            f"QListWidget#SettingsNav::item {{ padding: 9px 8px; border-radius: 8px; color: {theme.hex('text_secondary')};"
            f" font-weight: 600; }}"
            f"QListWidget#SettingsNav::item:selected {{ background: {theme.hex('accent_soft')};"
            f" color: {theme.hex('accent_soft_text')}; }}"
            f"QListWidget#SettingsNav::item:hover:!selected {{ background: {theme.hex('surface_hover')}; }}")

    # ---------------------------------------------------------------- helpers
    def _set(self, key: str, value, toast: bool = True) -> None:
        self.settings.set(key, value, self.user.id if self.user else None)
        if toast:
            self._saved_timer.start(700)

    def _toggle(self, key: str) -> ToggleSwitch:
        toggle = ToggleSwitch(self.settings.get_bool(key))
        toggle.toggled.connect(lambda checked: self._set(key, checked))
        return toggle

    def _spin(self, key: str, minimum: int, maximum: int, suffix: str = "", special: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(self.settings.get_int(key))
        spin.setSuffix(suffix)
        spin.setMinimumWidth(130)
        if special:
            spin.setSpecialValueText(special)
        spin.valueChanged.connect(lambda v: self._set(key, v))
        return spin

    # ----------------------------------------------------------------- school
    def _school(self, body) -> None:
        self.school_name = QLineEdit(self.settings.school_name)
        self.school_tagline = QLineEdit(self.settings.get("school.tagline") or "")
        body.addWidget(label("School name", "field"))
        body.addWidget(self.school_name)
        body.addWidget(label("Tagline", "field"))
        body.addWidget(self.school_tagline)
        save = button("Save profile", "save", "primary")
        save.clicked.connect(self._save_profile)
        body.addWidget(save, alignment=Qt.AlignmentFlag.AlignLeft)
        body.addWidget(divider())
        body.addWidget(label("School logo", "field"))
        logo_row = QHBoxLayout()
        logo_row.setSpacing(14)
        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(72, 72)
        logo_row.addWidget(self.logo_preview)
        choose = button("Choose image...", "image", None)
        choose.clicked.connect(self._choose_logo)
        clear = button("Use default", None, "ghost")
        clear.clicked.connect(lambda: (self._set("school.logo_path", ""), self._refresh_logo()))
        logo_row.addWidget(choose)
        logo_row.addWidget(clear)
        logo_row.addStretch(1)
        body.addLayout(logo_row)
        body.addWidget(label("PNG, JPG or SVG. The image is copied into the data folder so backups include it.",
                             "caption"))
        self._refresh_logo()

    def _refresh_logo(self) -> None:
        self.logo_preview.setPixmap(school_logo(self.settings.get("school.logo_path") or "", 72))

    def _save_profile(self) -> None:
        name = self.school_name.text().strip()
        if not name:
            self.toast("error", "School name required")
            return
        self._set("school.name", name, toast=False)
        self._set("school.tagline", self.school_tagline.text().strip(), toast=False)
        self.ctx.activity.log("Updated school profile", "System Settings", name)
        self.toast("success", "School profile saved")

    def _choose_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose school logo", "", "Images (*.png *.jpg *.jpeg *.svg *.bmp)")
        if not path:
            return
        import shutil
        dest = paths.data_dir() / f"school_logo{Path(path).suffix.lower()}"
        shutil.copy2(path, dest)
        self._set("school.logo_path", str(dest))
        self._refresh_logo()

    # ------------------------------------------------------------------ audio
    def _audio(self, body) -> None:
        reg = self.ctx.registry
        self.api = QComboBox()
        self.api.addItem("Automatic (WASAPI preferred)", "auto")
        for name in reg.host_apis:
            if name in ("Windows WASAPI", "Windows DirectSound", "MME"):
                self.api.addItem(name, name)
        self.api.setCurrentIndex(max(0, self.api.findData(self.settings.get("audio.host_api") or "auto")))
        self.api.currentIndexChanged.connect(lambda: self._set("audio.host_api", self.api.currentData()))
        self.api.setMinimumWidth(260)
        body.addWidget(SettingRow("Audio interface", "Windows driver model used for every device. WASAPI gives the "
                                                     "lowest latency.", self.api))
        self.default_mic = QComboBox()
        self.default_mic.setMinimumWidth(320)
        self.preview_out = QComboBox()
        self.preview_out.setMinimumWidth(320)
        self._fill_devices()
        self.default_mic.currentIndexChanged.connect(lambda: self._set("audio.default_input",
                                                                       self.default_mic.currentData() or ""))
        self.preview_out.currentIndexChanged.connect(lambda: self._set("audio.preview_output",
                                                                       self.preview_out.currentData() or ""))
        body.addWidget(SettingRow("Default microphone", "Preselected on the live paging console.", self.default_mic))
        body.addWidget(SettingRow("Preview / monitor output", "Where previews play. Never sent to paging zones.",
                                  self.preview_out))
        refresh = button("Re-scan devices", "refresh", None, "sm")
        refresh.clicked.connect(lambda: (self.ctx.refresh_devices(), self._fill_devices(),
                                         self.toast("success", "Devices re-scanned")))
        body.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignRight)
        body.addWidget(divider())
        master = SliderField(0, 100, self.settings.get_int("audio.master_volume"), icon_name="volume")
        master.setMinimumWidth(320)
        master.slider.sliderReleased.connect(lambda: self._set("audio.master_volume", master.value()))
        body.addWidget(SettingRow("Master volume", "Applies to every zone and broadcast.", master))
        gain = SliderField(0, 100, self.settings.get_int("audio.mic_gain"), icon_name="mic",
                           formatter=lambda v: f"{gain_db_from_slider(v):+.0f} dB")
        gain.setMinimumWidth(320)
        gain.slider.sliderReleased.connect(lambda: self._set("audio.mic_gain", gain.value()))
        body.addWidget(SettingRow("Default microphone gain", "Starting gain for live paging.", gain))
        latency = SegmentedControl([("low", "Low"), ("balanced", "Balanced"), ("safe", "Safe")])
        latency.set_value(self.settings.get("audio.latency") or "balanced")
        latency.changed.connect(lambda v: self._set("audio.latency", v))
        body.addWidget(SettingRow("Output latency", "Use 'Safe' if you hear crackles on slower computers.", latency))
        body.addWidget(divider())
        self.chime = self._chime_picker("audio.chime_sound", "chime_pre", "Intro chime",
                                        "Sound played before live pages and announcements.", body)
        self.outro_chime = self._chime_picker("audio.outro_chime_sound", "chime_post", "Outro chime",
                                              "Sound played after live pages and announcements.", body)
        for key, title, desc in (("audio.chime_before", "Play intro chime", "Alert listeners before a live page or "
                                                                            "announcement starts."),
                                 ("audio.chime_after", "Play outro chime", "Signal the end of a live page or "
                                                                           "announcement."),
                                 ("audio.feedback_guard", "Feedback guard", "Lower microphone gain automatically when "
                                                                            "howling is detected."),
                                 ("audio.high_pass", "Low-cut filter", "Remove rumble and handling noise from the "
                                                                       "microphone.")):
            body.addWidget(SettingRow(title, desc, self._toggle(key)))

    def _fill_devices(self) -> None:
        reg = self.ctx.registry
        for combo, devices, key, default in ((self.default_mic, reg.inputs(), "audio.default_input", reg.default_input()),
                                             (self.preview_out, reg.outputs(), "audio.preview_output",
                                              reg.default_output())):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(f"System default ({default.name if default else 'none detected'})", "")
            for device in devices:
                combo.addItem(device.name, device.name)
            saved = self.settings.get(key) or ""
            if saved and combo.findData(saved) < 0:
                combo.addItem(f"{saved} (not detected)", saved)
            combo.setCurrentIndex(max(0, combo.findData(saved)))
            combo.blockSignals(False)

    def _chime_picker(self, key: str, default: str, title: str, description: str, body) -> QComboBox:
        combo = QComboBox()
        for sound_key, name in CHIME_SOUNDS.items():
            combo.addItem(name, sound_key)
        combo.setCurrentIndex(max(0, combo.findData(self.settings.get(key) or default)))
        combo.currentIndexChanged.connect(lambda: self._set(key, combo.currentData()))
        test = button("", "headphones", "ghost", tooltip=f"Preview the {title.lower()} locally")
        test.clicked.connect(lambda: self._preview_chime(combo.currentData()))
        box = QWidget()
        box.setObjectName("Transparent")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(combo)
        layout.addWidget(test)
        body.addWidget(SettingRow(title, description, box))
        return combo

    def _preview_chime(self, key: str) -> None:
        try:
            source = self.ctx.library.builtin_source(key)
            self.ctx.preview.play(source, self.settings.get("audio.preview_output") or "", token=("settings", 0))
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc)

    # ------------------------------------------------------------------ zones
    def _zones(self, body) -> None:
        body.addWidget(label("Individual volume for every zone. Changes apply to the next broadcast.", "muted"))
        manage = self.can(P.ZONES_MANAGE)
        for zone in self.ctx.zones.physical_zones():
            slider = SliderField(0, 100, zone.volume, icon_name="volume")
            slider.setMinimumWidth(320)
            slider.setEnabled(manage)
            slider.slider.sliderReleased.connect(lambda z=zone, s=slider: self.guarded(
                self.ctx.zones.set_zone_volume, z.id, s.value()))
            body.addWidget(SettingRow(zone.name, zone.description, slider))

    # -------------------------------------------------------------------- tts
    def _tts(self, body) -> None:
        tts = self.ctx.tts
        if not tts.available:
            body.addWidget(InfoBanner(tts.error or "Text-to-speech is unavailable on this computer.", "danger"))
            return
        self.voice = QComboBox()
        self.voice.setMinimumWidth(320)
        for v in tts.voices():
            self.voice.addItem(f"{v.label}  -  {v.language}", v.id)
        self.voice.setCurrentIndex(max(0, self.voice.findData(self.settings.get("tts.voice") or tts.default_voice_id())))
        self.voice.currentIndexChanged.connect(lambda: self._set("tts.voice", self.voice.currentData()))
        body.addWidget(SettingRow("Default voice", f"{len(tts.voices())} Windows voice(s) installed. Add more in "
                                                   "Windows Settings > Time & language > Speech.", self.voice))
        rate = SliderField(-10, 10, self.settings.get_int("tts.rate"),
                           formatter=lambda v: "Normal" if v == 0 else f"{v:+d}")
        rate.setMinimumWidth(320)
        rate.slider.sliderReleased.connect(lambda: self._set("tts.rate", rate.value()))
        body.addWidget(SettingRow("Speaking speed", "Default speed for new announcements.", rate))
        volume = SliderField(0, 100, self.settings.get_int("tts.volume"), icon_name="volume")
        volume.setMinimumWidth(320)
        volume.slider.sliderReleased.connect(lambda: self._set("tts.volume", volume.value()))
        body.addWidget(SettingRow("Speech volume", "Default loudness of synthesized speech.", volume))
        body.addWidget(divider())
        self.tts_text = QLineEdit("Good morning. This is a test of the school paging system.")
        speak = button("Speak test", "headphones", "subtle")
        speak.clicked.connect(lambda: self._speak(rate.value(), volume.value()))
        test_row = QHBoxLayout()
        test_row.addWidget(self.tts_text, 1)
        test_row.addWidget(speak)
        body.addWidget(label("Test phrase (plays on the preview output only)", "field"))
        body.addLayout(test_row)
        clear = button("Clear speech cache", "trash", "ghost", "sm")
        clear.clicked.connect(lambda: self.toast("success", "Speech cache cleared",
                                                 f"{self.ctx.tts.clear_cache()} file(s) removed"))
        body.addWidget(clear, alignment=Qt.AlignmentFlag.AlignLeft)

    def _speak(self, rate: int, volume: int) -> None:
        text = self.tts_text.text().strip()
        voice = self.voice.currentData()
        run_async(lambda: self.ctx.tts.load(text, voice, rate, volume),
                  lambda result: self.ctx.preview.play(BufferSource(*result), self.settings.get("audio.preview_output")
                                                       or "", token=("settings", 1)),
                  self.handle_error)

    # ----------------------------------------------------------------- alarms
    def _alarms(self, body) -> None:
        hold = QDoubleSpinBox()
        hold.setRange(0.5, 5.0)
        hold.setSingleStep(0.5)
        hold.setSuffix(" s")
        hold.setValue(self.settings.get_float("alarm.hold_seconds") or 1.5)
        hold.setMinimumWidth(130)
        hold.valueChanged.connect(lambda v: self._set("alarm.hold_seconds", float(v)))
        body.addWidget(SettingRow("Hold-to-activate time", "How long the activation button must be held. Prevents "
                                                           "accidental alarms.", hold))
        body.addWidget(SettingRow("Require password to activate", "Ask for the operator's password before any alarm "
                                                                  "sounds.", self._toggle("alarm.require_password")))
        body.addWidget(SettingRow("Live paging safety limit", "Automatically end a live page after this many minutes.",
                                  self._spin("paging.max_live_minutes", 1, 120, " min")))
        body.addWidget(SettingRow("Scheduler grace period", "How long a due broadcast waits for the line to become "
                                                            "free before it is recorded as missed.",
                                  self._spin("schedules.grace_minutes", 1, 60, " min")))
        body.addWidget(InfoBanner("Emergency alarms always have the highest priority. Regular announcements, "
                                  "scheduled bells and live pages can never interrupt an active alarm. Interrupting a "
                                  "broadcast with an alarm requires the <b>Emergency override</b> permission.", "info"))

    # -------------------------------------------------------------------- app
    def _app(self, body) -> None:
        theme_ctl = SegmentedControl([("light", "Light", "sun"), ("dark", "Dark", "moon")])
        theme_ctl.set_value(self.settings.get("app.theme") or "light")
        theme_ctl.changed.connect(lambda v: self._set("app.theme", v))
        body.addWidget(SettingRow("Theme", "Light is recommended for well-lit offices.", theme_ctl))
        startup = ToggleSwitch(windows_startup.is_enabled())
        startup.toggled.connect(self._toggle_startup)
        body.addWidget(SettingRow("Start with Windows", "Launch automatically when this Windows user signs in so "
                                                        "schedules run all day.", startup))
        body.addWidget(SettingRow("Start minimized to tray", "Open in the background after sign-in.",
                                  self._toggle("app.start_minimized")))
        body.addWidget(SettingRow("Start maximized", "Open the main window full screen.",
                                  self._toggle("app.start_maximized")))
        body.addWidget(SettingRow("Keep running when closed", "Closing the window keeps the app in the system tray so "
                                                              "scheduled broadcasts still play.",
                                  self._toggle("app.close_to_tray")))
        body.addWidget(SettingRow("Automatic sign-out", "Sign out after a period without activity (never during a "
                                                        "broadcast).",
                                  self._spin("app.auto_logout_minutes", 0, 240, " min", "Never")))

    def _toggle_startup(self, enabled: bool) -> None:
        try:
            windows_startup.set_enabled(enabled)
            self._set("app.start_with_windows", enabled)
        except OSError as exc:
            self.toast("error", "Could not change startup setting", str(exc))

    # --------------------------------------------------------------- security
    def _security(self, body) -> None:
        body.addWidget(SettingRow("Lock account after", "Failed sign-in attempts before an account is temporarily "
                                                        "locked.", self._spin("security.lockout_attempts", 1, 20,
                                                                              " attempts")))
        body.addWidget(SettingRow("Lockout duration", "", self._spin("security.lockout_minutes", 1, 120, " min")))
        body.addWidget(SettingRow("Minimum password length", "Passwords must also contain a letter and a number.",
                                  self._spin("security.min_password_length", 6, 64, " characters")))
        body.addWidget(InfoBanner("Passwords are stored as salted scrypt hashes - never in plain text. Every "
                                  "sign-in, broadcast and configuration change is recorded in the activity log.",
                                  "success", "shield"))

    # ----------------------------------------------------------------- backup
    def _backup(self, body) -> None:
        body.addWidget(label("A backup contains the database (users, zones, schedules, history, settings) and every "
                             "audio file in the library.", "muted", wrap=True))
        actions = QHBoxLayout()
        create = button("Create backup...", "download", "primary")
        create.clicked.connect(self._create_backup)
        restore = button("Restore from file...", "upload", None)
        restore.clicked.connect(self._restore_file)
        folder = button("Open backups folder", "folder", "ghost")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.backups_dir()))))
        actions.addWidget(create)
        actions.addWidget(restore)
        actions.addWidget(folder)
        actions.addStretch(1)
        body.addLayout(actions)
        body.addWidget(SettingRow("Automatic daily backup", "Keeps the last 7 automatic backups in the backups folder.",
                                  self._toggle("app.auto_backup")))
        body.addWidget(label("RECENT BACKUPS", "overline"))
        self.backup_list = QVBoxLayout()
        body.addLayout(self.backup_list)
        self._fill_backups()

    def _fill_backups(self) -> None:
        while self.backup_list.count():
            item = self.backup_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        backups = self.ctx.backup.list_backups()[:6]
        if not backups:
            self.backup_list.addWidget(label("No backups yet.", "caption"))
        for path in backups:
            row = QWidget()
            row.setObjectName("Transparent")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%b %d, %Y %I:%M %p")
            rl.addWidget(label(path.name, "h3"), 1)
            rl.addWidget(label(f"{stamp}  ·  {path.stat().st_size / (1024 * 1024):.1f} MB", "caption"))
            restore = button("Restore", "upload", "ghost", "sm")
            restore.clicked.connect(lambda _=False, p=path: self._restore(p))
            rl.addWidget(restore)
            self.backup_list.addWidget(row)

    def _create_backup(self) -> None:
        default = str(paths.backups_dir() / f"paging_backup_{datetime.now():%Y%m%d_%H%M}{BACKUP_EXT}")
        path, _ = QFileDialog.getSaveFileName(self, "Create backup", default, f"Paging backups (*{BACKUP_EXT})")
        if not path:
            return
        try:
            result = self.ctx.backup.create_backup(path)
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc, dialog=True)
            return
        self.toast("success", "Backup created", f"{Path(result).name} ({Path(result).stat().st_size / 1e6:.1f} MB)")
        self._fill_backups()

    def _restore_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Restore backup", str(paths.backups_dir()),
                                              f"Paging backups (*{BACKUP_EXT})")
        if path:
            self._restore(Path(path))

    def _restore(self, path: Path) -> None:
        try:
            manifest = read_manifest(path)
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc, dialog=True)
            return
        if self.ctx.controller.active is not None:
            self.toast("warning", "Broadcast in progress", "Stop the current broadcast before restoring.")
            return
        if not confirm(self, "Restore this backup?",
                       f"Backup from <b>{manifest.get('created_at', '?')}</b> ({manifest.get('school', '')}) will "
                       "replace all current data. A safety copy of the current database is kept in the backups "
                       "folder. The application restarts to finish.", "Restore & restart", danger=True,
                       icon_name="archive"):
            return
        try:
            self.ctx.backup.stage_restore(path)
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc, dialog=True)
            return
        self._restart()

    def _restart(self) -> None:
        args = [a for a in sys.argv[1:] if a != "--restarting"] + ["--restarting"]
        if not paths.is_frozen():
            args = [str(paths.resource_root() / "main.py")] + args
        QProcess.startDetached(sys.executable, args)
        self.window_.exit_requested.emit()

    # --------------------------------------------------------------- database
    def _database(self, body) -> None:
        db = self.ctx.db
        info = [("Location", str(paths.database_path())), ("Size", f"{db.size_bytes() / 1024:.0f} KB"),
                ("Schema version", str(db.user_version)), ("Data folder", str(paths.data_dir()))]
        for caption, value in info:
            row = QHBoxLayout()
            row.addWidget(label(caption, "h3"))
            row.addStretch(1)
            text = label(value, "mono", selectable=True)
            row.addWidget(text)
            body.addLayout(row)
        actions = QHBoxLayout()
        check = button("Run integrity check", "check_circle", None)
        check.clicked.connect(lambda: alert(self, "Integrity check", f"SQLite reports: <b>{db.integrity_check()}</b>",
                                            "success" if db.integrity_check() == "ok" else "danger"))
        move = button("Change location...", "folder", None)
        move.clicked.connect(self._move_db)
        open_folder = button("Open data folder", "external", "ghost")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir()))))
        actions.addWidget(check)
        actions.addWidget(move)
        actions.addWidget(open_folder)
        actions.addStretch(1)
        body.addLayout(actions)
        body.addWidget(InfoBanner("The database uses SQLite with write-ahead logging. Data is saved instantly and "
                                  "survives restarts. To share one database between computers, place it on a "
                                  "reliable local drive and use backups for transfer.", "info"))

    def _move_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "New database location", str(paths.database_path()),
                                              "SQLite database (*.db)")
        if not path:
            return
        try:
            new_path = self.ctx.backup.change_database_location(path)
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc, dialog=True)
            return
        if confirm(self, "Restart now?", f"The database was copied to <b>{new_path}</b>. Restart to start using it.",
                   "Restart"):
            self._restart()

    # ------------------------------------------------------------------ about
    def _about(self, body) -> None:
        rows = [("Application", f"{paths.APP_NAME} {paths.APP_VERSION}"),
                ("Python", platform.python_version()), ("Qt / PySide6", PYSIDE_VERSION),
                ("Audio engine", sd.get_portaudio_version()[1] if sd is not None else "unavailable"),
                ("Operating system", f"{platform.system()} {platform.release()} ({platform.version()})"),
                ("Data folder", str(paths.data_dir()))]
        for caption, value in rows:
            row = QHBoxLayout()
            row.addWidget(label(caption, "h3"))
            row.addStretch(1)
            row.addWidget(label(value, "mono", selectable=True))
            body.addLayout(row)
        body.addWidget(InfoBanner("IP speaker support: standard RTP (G.711 mu-law) streaming is included. SIP paging "
                                  "and vendor HTTP APIs require manufacturer-specific drivers and are reported as "
                                  "'Not supported' until added. Built-in alarm tones follow common evacuation-signal "
                                  "patterns but are not certified life-safety signals.", "warning"))
