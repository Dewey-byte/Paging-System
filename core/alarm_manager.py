"""Emergency alarms: alarm types, sound programs, activation and cancellation."""
from __future__ import annotations

import re

from database.models import AlarmType, BroadcastRecord, User
from database.repositories import Repositories
from services.audio_sources import AudioSource, BufferSource, RepeatSource, SequenceSource, SilenceSource
from services.sound_generator import ALARM_PATTERNS
from services.text_to_speech import TTSService

from .activity_logger import ActivityLogger
from .audio_manager import AudioLibraryManager
from .authentication import AuthManager
from .errors import BroadcastRejected, NotFoundError, ValidationError
from .events import EventBus
from .paging_manager import ActiveBroadcast, BroadcastController, BroadcastRequest, Priority
from .permissions import P
from .settings_manager import SettingsManager

ALARM_ICONS = ["flame", "activity", "door", "cloud_lightning", "shield_alert", "crosshair", "lock", "alert_octagon",
               "siren", "bell"]
MAX_ALARM_SECONDS = 3600


class AlarmManager:
    def __init__(self, repos: Repositories, auth: AuthManager, activity: ActivityLogger, bus: EventBus,
                 library: AudioLibraryManager, tts: TTSService, controller: BroadcastController,
                 settings: SettingsManager):
        self._repos = repos
        self._auth = auth
        self._activity = activity
        self._bus = bus
        self.library = library
        self.tts = tts
        self.controller = controller
        self._settings = settings

    # ---------------------------------------------------------------- query
    def alarm_types(self, include_disabled: bool = True) -> list[AlarmType]:
        return self._repos.alarms.all(include_disabled)

    def get(self, alarm_id: int) -> AlarmType:
        alarm = self._repos.alarms.get(alarm_id)
        if alarm is None:
            raise NotFoundError("That alarm type no longer exists.")
        return alarm

    def alarm_log(self, limit: int = 200) -> list[BroadcastRecord]:
        return self._repos.history.search(broadcast_type="alarm", limit=limit)

    # --------------------------------------------------------------- change
    def save(self, data: dict, alarm_id: int | None = None) -> AlarmType:
        self._auth.require(P.ALARMS_MANAGE)
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Enter a name for the alarm.")
        duration = int(data.get("duration_seconds") or 0)
        repeat = int(data.get("repeat_count") or 0)
        if not 0 <= duration <= MAX_ALARM_SECONDS:
            raise ValidationError(f"Duration must be between 0 (until stopped) and {MAX_ALARM_SECONDS} seconds.")
        if not 0 <= repeat <= 100:
            raise ValidationError("Repetitions must be between 0 (continuous) and 100.")
        source = data.get("sound_source") or "builtin"
        pattern = data.get("builtin_pattern") or "alarm_general"
        audio_file_id = data.get("audio_file_id")
        if source == "builtin":
            if pattern not in ALARM_PATTERNS:
                raise ValidationError("Choose one of the built-in alarm sounds.")
            audio_file_id = None
        elif source == "audio_file":
            if not audio_file_id:
                raise ValidationError("Choose or upload a custom alarm sound.")
            self.library.get(int(audio_file_id))
        else:
            raise ValidationError("Choose the alarm sound source.")
        color = data.get("color") or "#DC2626"
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValidationError("Choose a valid color.")
        record = dict(name=name[:80], description=(data.get("description") or "").strip()[:300],
                      sound_source=source, builtin_pattern=pattern, audio_file_id=audio_file_id,
                      voice_message=(data.get("voice_message") or "").strip()[:600], duration_seconds=duration,
                      repeat_count=repeat, color=color, icon=data.get("icon") or "siren",
                      is_enabled=bool(data.get("is_enabled", True)))
        if alarm_id is None:
            code = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:24] or "CUSTOM"
            base, n = code, 2
            while self._repos.alarms.get_by_code(code):
                code, n = f"{base}_{n}", n + 1
            alarm_id = self._repos.alarms.create(code=code, is_system=False, sort_order=100, **record)
            self._activity.log("Created alarm type", "Alarm Management", name)
        else:
            self.get(alarm_id)
            self._repos.alarms.update(alarm_id, **record)
            self._activity.log("Updated alarm type", "Alarm Management",
                               f"{name}: {duration or 'until stopped'}s, repeat {repeat or 'continuous'}")
        self._bus.data_changed.emit("alarms")
        return self.get(alarm_id)

    def delete(self, alarm_id: int) -> None:
        self._auth.require(P.ALARMS_MANAGE)
        alarm = self.get(alarm_id)
        if alarm.is_system:
            raise ValidationError("Predefined alarm types cannot be deleted. You can disable them instead.")
        self._repos.alarms.delete(alarm_id)
        self._activity.log("Deleted alarm type", "Alarm Management", alarm.name, "warning")
        self._bus.data_changed.emit("alarms")

    # ------------------------------------------------------------ rendering
    def siren_source(self, alarm: AlarmType) -> AudioSource:
        if alarm.sound_source == "audio_file" and alarm.audio_file_id:
            return self.library.source_for(alarm.audio_file_id)
        return self.library.builtin_source(alarm.builtin_pattern or "alarm_general")

    def build_source(self, alarm: AlarmType, include_voice: bool = True) -> AudioSource:
        siren = self.siren_source(alarm)
        parts: list[AudioSource] = [siren]
        if include_voice and alarm.voice_message and self.tts.available:
            try:
                voice = self._settings.get("tts.voice") or self.tts.default_voice_id()
                data, rate = self.tts.load(alarm.voice_message, voice, -1, 100)
                parts += [SilenceSource(0.4), BufferSource(data, rate, "Voice message"), SilenceSource(0.8)]
            except Exception:
                pass  # the siren alone still plays; never block an alarm on speech synthesis
        cycle = SequenceSource(parts, name=alarm.name) if len(parts) > 1 else siren
        return RepeatSource(cycle, count=alarm.repeat_count, max_seconds=alarm.duration_seconds, name=alarm.name)

    def describe_timing(self, alarm: AlarmType) -> str:
        duration = "until stopped" if not alarm.duration_seconds else _fmt_seconds(alarm.duration_seconds)
        repeat = "continuous" if not alarm.repeat_count else f"{alarm.repeat_count}x"
        return f"{duration} - {repeat}"

    # ------------------------------------------------------------- trigger
    def trigger(self, alarm_id: int, zone_ids: list[int] | None, user: User | None = None, interrupt: bool = False,
                origin: str = "manual", schedule_title: str = "") -> ActiveBroadcast:
        alarm = self.get(alarm_id)
        if origin == "manual":
            user = self._auth.require(P.ALARMS_TRIGGER, user)
        if not alarm.is_enabled:
            raise BroadcastRejected(f"'{alarm.name}' is disabled. Enable it in alarm settings first.", "disabled")
        decision = self.controller.evaluate("alarm", Priority.EMERGENCY, user, origin)
        if not decision.allowed:
            raise BroadcastRejected(decision.message, decision.code)
        if decision.needs_confirmation and not interrupt:
            raise BroadcastRejected(decision.message, "confirm")
        source = self.build_source(alarm)
        zones = zone_ids
        if not zones:
            campus = self.controller.zones.campus_zone()
            zones = [campus.id] if campus else []
        request = BroadcastRequest(kind="alarm", title=schedule_title or alarm.name, zone_ids=zones, source=source,
                                   priority=Priority.EMERGENCY, category="Emergency Alarm", user=user, origin=origin,
                                   reference_type="alarm", reference_id=alarm.id, interrupt=True, color=alarm.color)
        return self.controller.start(request)

    def stop_active(self, user: User | None = None) -> None:
        user = user or self._auth.current_user
        name = user.full_name if user else "System"
        self.controller.stop(user, detail=f"Alarm cancelled by {name}")


def _fmt_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min" + (f" {rest}s" if rest else "")
