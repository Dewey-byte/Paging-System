"""Typed access to the ``system_settings`` table with sensible defaults."""
from __future__ import annotations

from typing import Any

from database.repositories import SettingsRepository

from .events import EventBus

DEFAULTS: dict[str, Any] = {
    # School profile
    "school.name": "Your School Name",
    "school.tagline": "Campus Paging & Emergency Communication",
    "school.logo_path": "",
    # Audio
    "audio.host_api": "auto",
    "audio.default_input": "",          # input device name ("" = system default)
    "audio.preview_output": "",         # local monitor/preview output ("" = system default)
    "audio.master_volume": 80,
    "audio.mic_gain": 50,               # 0..100 -> -24..+24 dB, 50 = 0 dB
    "audio.chime_before": True,         # intro chime (live paging and announcements)
    "audio.chime_sound": "chime_pre",
    "audio.chime_after": True,          # outro chime (live paging and announcements)
    "audio.outro_chime_sound": "chime_post",
    "audio.feedback_guard": True,
    "audio.high_pass": True,
    "audio.latency": "low",
    # Live paging
    "paging.push_to_talk": True,
    "paging.max_live_minutes": 15,
    # Text-to-speech
    "tts.voice": "",
    "tts.rate": 0,
    "tts.volume": 100,
    # Emergency alarms
    "alarm.hold_seconds": 1.5,
    "alarm.require_password": False,
    # Scheduler
    "schedules.paused": False,
    "schedules.grace_minutes": 5,
    # Application
    "app.theme": "light",
    "app.start_maximized": True,
    "app.start_minimized": False,
    "app.start_with_windows": False,
    "app.close_to_tray": True,
    "app.auto_logout_minutes": 0,
    "app.auto_backup": True,
    "app.last_auto_backup": "",
    # Security
    "security.lockout_attempts": 5,
    "security.lockout_minutes": 5,
    "security.min_password_length": 8,
}


class SettingsManager:
    def __init__(self, repo: SettingsRepository, bus: EventBus):
        self._repo = repo
        self._bus = bus
        self._cache: dict[str, Any] = repo.all()

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            return self._cache[key]
        if default is not None:
            return default
        return DEFAULTS.get(key)

    def get_int(self, key: str) -> int:
        try:
            return int(self.get(key))
        except (TypeError, ValueError):
            return int(DEFAULTS.get(key, 0) or 0)

    def get_float(self, key: str) -> float:
        try:
            return float(self.get(key))
        except (TypeError, ValueError):
            return float(DEFAULTS.get(key, 0) or 0)

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def set(self, key: str, value: Any, user_id: int | None = None, *, quiet: bool = False) -> None:
        if self._cache.get(key, DEFAULTS.get(key)) == value and key in self._cache:
            return
        self._repo.set(key, value, user_id)
        self._cache[key] = value
        if not quiet:
            self._bus.settings_changed.emit(key)

    def set_many(self, values: dict[str, Any], user_id: int | None = None) -> None:
        for key, value in values.items():
            self.set(key, value, user_id)

    def reload(self) -> None:
        self._cache = self._repo.all()

    @property
    def school_name(self) -> str:
        return str(self.get("school.name") or DEFAULTS["school.name"])
