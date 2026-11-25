"""Composition root: creates and wires every manager and service."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from database.database import Database
from database.migrations import migrate
from database.repositories import Repositories
from services.audio_decoder import AudioCache
from services.audio_engine import AudioEngine, PreviewPlayer
from services.device_detection import STREAMS, DeviceRegistry
from services.scheduler import SchedulerService
from services.text_to_speech import TTSService

from . import paths
from .activity_logger import ActivityLogger
from .alarm_manager import AlarmManager
from .announcement_manager import AnnouncementManager
from .audio_manager import AudioLibraryManager
from .authentication import AuthManager
from .backup_manager import BackupManager, apply_pending_restore
from .events import EventBus
from .history_manager import HistoryManager
from .paging_manager import BroadcastController
from .schedule_manager import ScheduleManager
from .seed_data import SIM_DEVICE_NAME, ensure_roles, seed_base, seed_demo_content
from .settings_manager import SettingsManager
from .user_manager import UserManager
from .zone_manager import ZoneManager

log = logging.getLogger(__name__)
_logging_ready = False


def configure_logging() -> None:
    global _logging_ready
    if _logging_ready:
        return
    handler = RotatingFileHandler(paths.logs_dir() / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    _logging_ready = True


class AppContext(QObject):
    DEVICE_POLL_MS = 3000

    def __init__(self, data_dir: str | Path | None = None, force_simulated_audio: bool | None = None,
                 enable_logging: bool = True):
        super().__init__()
        if data_dir is not None:
            paths.set_data_dir(data_dir)
        if enable_logging:
            configure_logging()
        self.restored = apply_pending_restore()

        self.db = Database(paths.database_path())
        migrate(self.db)
        self.repos = Repositories(self.db)
        ensure_roles(self.repos)

        self.bus = EventBus()
        self.settings = SettingsManager(self.repos.settings, self.bus)
        self.auth = AuthManager(self.repos, self.settings, self.bus)
        self.activity = ActivityLogger(self.repos.activity, lambda: self.auth.current_user)
        self.auth.activity = self.activity
        self.users = UserManager(self.repos, self.auth, self.activity, self.bus)

        if force_simulated_audio is None:
            force_simulated_audio = os.environ.get("SPS_AUDIO", "").lower() == "simulated"
        self.simulated_audio_only = force_simulated_audio
        latency = self.settings.get("audio.latency") or "balanced"
        self.registry = DeviceRegistry(self.settings.get("audio.host_api") or "auto")
        self.engine = AudioEngine(self.registry, latency=latency, force_simulated=force_simulated_audio)
        self.preview = PreviewPlayer(self.registry, latency=latency, force_simulated=force_simulated_audio)
        self.cache = AudioCache()
        self.tts = TTSService(paths.tts_cache_dir())

        self.zones = ZoneManager(self.repos, self.registry, self.auth, self.activity, self.bus)
        self.library = AudioLibraryManager(self.repos, self.auth, self.activity, self.bus, self.cache)
        self.controller = BroadcastController(self.engine, self.zones, self.repos, self.auth, self.activity, self.bus,
                                              self.settings)
        self.controller.chime_provider = self.library.chime_source
        self.controller.test_source_provider = self._test_program
        self.announcements = AnnouncementManager(self.repos, self.auth, self.activity, self.bus, self.library,
                                                 self.tts, self.controller, self.settings)
        self.alarms = AlarmManager(self.repos, self.auth, self.activity, self.bus, self.library, self.tts,
                                   self.controller, self.settings)
        self.schedules = ScheduleManager(self.repos, self.auth, self.activity, self.bus, self.zones,
                                         self.announcements, self.alarms, self.library, self.controller,
                                         self.settings)
        self.scheduler = SchedulerService(self.schedules, self.controller, self.settings, self.bus)
        self.history = HistoryManager(self.repos, self.auth, self.activity, self.settings)
        self.backup = BackupManager(self.db, self.auth, self.activity, self.settings)

        self._device_timer = QTimer(self)
        self._device_timer.setInterval(self.DEVICE_POLL_MS)
        self._device_timer.timeout.connect(self._check_hotplug)
        self.bus.settings_changed.connect(self._on_setting_changed)
        self._started = False

    # ------------------------------------------------------------ lifecycle
    def startup(self) -> None:
        seed_base(self.repos, map_local_output=True)
        self.library.ensure_builtin_sounds()
        stale = self.repos.history.close_stale("Application closed before the broadcast finished")
        if stale:
            log.warning("Closed %s stale broadcast records", stale)
        self.zones.refresh_status(reinitialize=False)
        self.scheduler.start()
        self._device_timer.start()
        try:
            self.backup.auto_backup_if_due()
        except Exception:
            log.exception("Automatic backup failed")
        self.activity.log("Application started", "System", f"Version {paths.APP_VERSION}; data at {paths.data_dir()}",
                          system=True)
        self._started = True

    def complete_first_run(self, *, school_name: str, full_name: str, username: str, password: str,
                           email: str = "", demo_content: bool = True, use_local_output: bool = True):
        admin = self.users.create_initial_admin(full_name, username, password, email)
        self.settings.set("school.name", school_name.strip() or "Your School Name", admin.id)
        if not use_local_output:
            sim = self.repos.devices.get_by_name(SIM_DEVICE_NAME)
            main = next((z for z in self.repos.zones.all() if z.name == "Main Building"), None)
            if sim and main:
                self.repos.zones.update(main.id, device_id=sim.id, output_channel=8)
        if demo_content:
            seed_demo_content(self.repos, admin.id, include_schedules=True)
        self.zones.refresh_status(reinitialize=False)
        return admin

    def shutdown(self) -> None:
        self._device_timer.stop()
        self.scheduler.stop()
        self.preview.stop()
        self.controller.shutdown()
        if self._started:
            self.activity.log("Application closed", "System", system=True)
        self.db.close()

    def _test_program(self, label: str):
        from services.audio_sources import BufferSource, SequenceSource, SilenceSource
        parts = [self.library.builtin_source("test_tone")]
        if self.tts.available:
            try:
                voice = self.settings.get("tts.voice") or self.tts.default_voice_id()
                data, rate = self.tts.load(f"This is a test of {label}.", voice, 0, 100)
                parts += [SilenceSource(0.3), BufferSource(data, rate, "Test message")]
            except Exception:
                log.warning("Test message synthesis failed", exc_info=True)
        return SequenceSource(parts, name=f"Test: {label}")

    # ------------------------------------------------------------- devices
    def _check_hotplug(self) -> None:
        if self.registry.hotplug_suspected() and STREAMS.count == 0:
            before = {d.name for d in self.registry.outputs() + self.registry.inputs()}
            if self.zones.refresh_status(reinitialize=True, probe_network=False):
                after = {d.name for d in self.registry.outputs() + self.registry.inputs()}
                added, removed = after - before, before - after
                parts = ([f"Connected: {', '.join(sorted(added))}"] if added else []) + \
                        ([f"Disconnected: {', '.join(sorted(removed))}"] if removed else [])
                self.bus.toast("info", "Audio hardware changed", "\n".join(parts) or "Device list updated")

    def refresh_devices(self) -> bool:
        """Manual refresh (re-initialises PortAudio when no stream is open)."""
        return self.zones.refresh_status(reinitialize=STREAMS.count == 0)

    def _on_setting_changed(self, key: str) -> None:
        if key == "audio.master_volume":
            self.controller.set_master_volume(self.settings.get_int(key))
        elif key == "audio.host_api":
            self.registry.set_preferred_host_api(self.settings.get(key) or "auto")
            self.zones.refresh_status(reinitialize=False)
        elif key == "audio.latency":
            self.engine.latency = self.preview.latency = self.settings.get(key) or "balanced"
