"""Broadcast controller: priority arbitration, live paging and history recording.

Rules enforced here (see project specification, section 7):

* emergency alarms have the highest priority and may interrupt any regular
  broadcast when the user holds the *emergency override* permission;
* regular broadcasts can never interrupt an emergency alarm;
* only one broadcast runs at a time - a lower/equal priority request is
  rejected (the scheduler retries later), a higher one needs confirmation;
* every attempt that reaches the audio layer is written to broadcast history.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from database.database import now_str
from database.models import User
from database.repositories import Repositories
from services.audio_engine import AudioEngine
from services.audio_input import MicrophoneSource, gain_db_from_slider
from services.audio_output import OutputTarget
from services.audio_sources import AudioSource, ProgramSource, SequenceSource, SilenceSource
from services.device_detection import SystemDevice

from .activity_logger import ActivityLogger
from .authentication import AuthManager
from .errors import BroadcastRejected, PermissionDenied
from .events import EventBus
from .permissions import P
from .settings_manager import SettingsManager
from .zone_manager import ZoneManager

log = logging.getLogger(__name__)


class Priority(IntEnum):
    TEST = 5
    BACKGROUND = 10
    ANNOUNCEMENT = 30
    LIVE = 50
    EMERGENCY = 100


KIND_LABELS = {
    "live": "Live Paging", "announcement": "Announcement", "alarm": "Emergency Alarm",
    "scheduled": "Scheduled Broadcast", "audio": "Audio Playback", "test": "Zone Test",
}

KIND_PERMISSIONS = {
    "live": P.PAGING_LIVE, "alarm": P.ALARMS_TRIGGER, "announcement": P.ANNOUNCEMENTS_BROADCAST,
    "audio": P.ANNOUNCEMENTS_BROADCAST, "test": P.ZONES_MANAGE,
}


@dataclass
class BroadcastRequest:
    kind: str
    title: str
    zone_ids: list[int]
    source: AudioSource
    priority: int
    category: str = ""
    user: User | None = None
    origin: str = "manual"
    reference_type: str = ""
    reference_id: int | None = None
    max_seconds: float = 0.0
    interrupt: bool = False
    color: str = ""
    targets: list[OutputTarget] | None = None   # explicit endpoints (device tests) instead of zones


@dataclass
class Decision:
    allowed: bool
    needs_confirmation: bool = False
    message: str = ""
    code: str = ""


@dataclass
class ActiveBroadcast:
    request: BroadcastRequest
    history_id: int
    started_at: datetime
    started_mono: float
    zone_ids: list[int]
    zone_names: str
    targets: list[OutputTarget]
    warnings: list[str] = field(default_factory=list)
    status: str = "in_progress"
    status_detail: str = ""
    ended_by: str = ""
    ending: bool = False            # stop requested; the closing (outro) chime is playing
    ending_since: float = 0.0
    pending_end: tuple[str, str, str] | None = None   # (status, detail, ended_by) recorded once the outro ends

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_mono

    @property
    def is_emergency(self) -> bool:
        return self.request.priority >= Priority.EMERGENCY

    @property
    def kind(self) -> str:
        return self.request.kind

    @property
    def title(self) -> str:
        return self.request.title

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.request.kind, self.request.kind.title())

    @property
    def initiated_by(self) -> str:
        return self.request.user.full_name if self.request.user else "Scheduler"

    @property
    def simulated_only(self) -> bool:
        return bool(self.targets) and all(t.simulated for t in self.targets)


class BroadcastController(QObject):
    POLL_MS = 50
    OUTRO_TIMEOUT = 20.0    # never let a closing chime keep the line busy longer than this

    def __init__(self, engine: AudioEngine, zones: ZoneManager, repos: Repositories, auth: AuthManager,
                 activity: ActivityLogger, bus: EventBus, settings: SettingsManager):
        super().__init__()
        self.engine = engine
        self.zones = zones
        self._repos = repos
        self._auth = auth
        self._activity = activity
        self._bus = bus
        self._settings = settings
        self.active: ActiveBroadcast | None = None
        self.mic: MicrophoneSource | None = None
        self.chime_provider: Callable[[str], AudioSource | None] | None = None
        self.test_source_provider: Callable[[str], AudioSource] | None = None
        self._reported_errors: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        engine.set_master_volume(settings.get_int("audio.master_volume"))

    # ---------------------------------------------------------- arbitration
    def evaluate(self, kind: str, priority: int, user: User | None, origin: str = "manual") -> Decision:
        if origin == "manual":
            permission = KIND_PERMISSIONS.get(kind)
            if user is None:
                return Decision(False, message="Please sign in first.", code="auth")
            if permission and not user.has(permission):
                what = {"live": "make live announcements", "alarm": "trigger emergency alarms",
                        "test": "test zones"}.get(kind, "broadcast announcements")
                return Decision(False, message=f"Your account is not authorized to {what}.", code="permission")
        active = self.active
        if active is None:
            return Decision(True)
        if active.is_emergency:
            if priority < Priority.EMERGENCY:
                return Decision(False, code="emergency_active",
                                message=f"An emergency alarm is active ({active.title}). Regular broadcasts "
                                        "cannot interrupt emergency alarms.")
            return Decision(True, True, f"'{active.title}' is currently active. Replace it with this alarm?",
                            "replace_alarm")
        if priority >= Priority.EMERGENCY:
            if origin == "manual" and not (user and user.has(P.EMERGENCY_OVERRIDE)):
                return Decision(False, code="override",
                                message=f"'{active.title}' is on air. Interrupting it requires the "
                                        "Emergency override permission. Stop it first or ask an administrator.")
            return Decision(True, origin == "manual",
                            f"'{active.title}' ({active.kind_label}) is on air. The emergency alarm will "
                            "interrupt it immediately.", "interrupt")
        if priority > active.request.priority:
            if origin != "manual":
                return Decision(False, code="busy", message=f"'{active.title}' is on air.")
            return Decision(True, True, f"'{active.title}' ({active.kind_label}) is currently on air. "
                                        "Interrupt it and start this broadcast?", "interrupt")
        return Decision(False, code="busy",
                        message=f"'{active.title}' ({active.kind_label}) is on air. Wait for it to finish or "
                                "stop it before starting another broadcast.")

    # ---------------------------------------------------------------- start
    def start(self, request: BroadcastRequest) -> ActiveBroadcast:
        decision = self.evaluate(request.kind, request.priority, request.user, request.origin)
        if not decision.allowed:
            if decision.code in ("permission", "override"):
                self._activity.log("Broadcast refused", "Broadcasting", f"{request.title}: {decision.message}",
                                   "warning", user=request.user)
            raise BroadcastRejected(decision.message, decision.code)
        if decision.needs_confirmation and not request.interrupt:
            raise BroadcastRejected(decision.message, "confirm")

        if request.targets is not None:
            expanded, targets, warnings = [], list(request.targets), []
            zone_names = ", ".join(t.device_name for t in targets) or "No outputs"
        else:
            expanded = self.zones.expand_zone_ids(request.zone_ids)
            if not expanded:
                raise BroadcastRejected("Select at least one enabled zone.", "no_zones")
            targets, warnings = self.zones.resolve_targets(expanded)
            zone_names = self.zones.describe_zones(request.zone_ids)
        actor = request.user.full_name if request.user else "Scheduler"

        if self.active is not None:
            self._finish("interrupted", f"Interrupted by {KIND_LABELS.get(request.kind, request.kind)}: "
                                        f"{request.title}", actor)
        if self.engine.active:
            self.engine.stop_now()

        history_id = self._repos.history.create(
            title=request.title, broadcast_type=request.kind, category=request.category,
            priority=int(request.priority), source=request.origin,
            initiated_by=request.user.id if request.user else None, initiated_by_name=actor,
            zone_names=zone_names, zone_ids=expanded, started_at=now_str(), status="in_progress",
            reference_type=request.reference_type, reference_id=request.reference_id)

        def fail(message: str, code: str) -> BroadcastRejected:
            self._repos.history.finish(history_id, ended_at=now_str(), duration=0, status="failed",
                                       detail=message[:500])
            self._activity.log("Broadcast failed", "Broadcasting", f"{request.title}: {message}", "warning",
                               user=request.user)
            self._bus.data_changed.emit("history")
            return BroadcastRejected(message, code)

        if not targets:
            detail = "; ".join(warnings) or "No output devices configured"
            raise fail(f"None of the selected zones has a working output device. {detail}.", "no_devices")
        try:
            report = self.engine.start(request.source, targets)
        except Exception as exc:
            raise fail(f"Could not start audio: {exc}", "audio_error") from exc
        warnings += [f"{t.device_name}: {err}" for t, err in report.failed]
        if not report.ok:
            raise fail("No output device could be opened. " + "; ".join(warnings), "audio_error")

        summary = f"{report.real_count} real device(s), {report.simulated_count} simulated"
        self._repos.history.update(history_id, devices_summary=summary)
        for target in report.started:
            if target.transport == "local":
                self.zones.mark_verified(target.device_id)
        if request.reference_type == "announcement" and request.reference_id:
            self._repos.announcements.mark_broadcast(request.reference_id)

        self.active = ActiveBroadcast(request=request, history_id=history_id, started_at=datetime.now(),
                                      started_mono=time.monotonic(), zone_ids=expanded, zone_names=zone_names,
                                      targets=report.started, warnings=warnings)
        self._reported_errors = set()
        level = "critical" if self.active.is_emergency else "info"
        action = "Emergency alarm activated" if self.active.is_emergency else "Broadcast started"
        self._activity.log(action, "Broadcasting", f"{request.title} -> {zone_names} ({summary})", level,
                           user=request.user, system=request.user is None)
        if warnings:
            self._bus.toast("warning", "Some zones are unavailable", "\n".join(warnings[:4]))
        self._bus.broadcast_started.emit(self.active)
        if self.active.is_emergency:
            self._bus.emergency_changed.emit(True)
        self._bus.broadcast_state_changed.emit()
        self._bus.data_changed.emit("history")
        return self.active

    # ----------------------------------------------------------------- stop
    def stop(self, user: User | None = None, detail: str = "Stopped by operator") -> None:
        """Stop the active broadcast.

        A live page with an outro chime ends gracefully: the microphone closes
        at once and the closing chime plays before the broadcast is recorded as
        finished.  Stopping again while the outro plays cuts it off.
        """
        active = self.active
        if active is None:
            if self.engine.active:
                self.engine.stop()
            return
        if active.is_emergency and user is not None and not user.has(P.ALARMS_TRIGGER):
            raise PermissionDenied("Only users authorized to trigger emergency alarms can stop them.")
        actor = user.full_name if user else "System"
        if not active.ending and self._begin_outro(active, "cancelled", detail, actor):
            return
        status, detail, actor = active.pending_end or ("cancelled", detail, actor)
        self.engine.stop()
        self._finish(status, detail, actor)

    def _begin_outro(self, active: ActiveBroadcast, status: str, detail: str, actor: str) -> bool:
        source = active.request.source
        if not (isinstance(source, ProgramSource) and source.has_outro and self.engine.active):
            return False
        source.finish()
        active.ending = True
        active.ending_since = time.monotonic()
        active.pending_end = (status, detail, actor)
        self.mic = None
        self._bus.broadcast_state_changed.emit()
        return True

    def stop_all(self, detail: str) -> None:
        if self.active is not None:
            self._finish("cancelled", detail, "System")
        self.engine.stop_now()

    def _finish(self, status: str, detail: str, ended_by: str) -> None:
        active = self.active
        if active is None:
            return
        self.active = None
        self.mic = None
        active.status, active.status_detail, active.ended_by = status, detail, ended_by
        self._repos.history.finish(active.history_id, ended_at=now_str(), duration=active.elapsed, status=status,
                                   detail=detail, ended_by=ended_by)
        if active.is_emergency:
            action = {"completed": "Emergency alarm completed", "cancelled": "Emergency alarm cancelled"}.get(
                status, "Emergency alarm interrupted")
            self._activity.log(action, "Broadcasting", f"{active.title}: {detail} ({ended_by})", "critical",
                               system=True)
        else:
            self._activity.log(f"Broadcast {status}", "Broadcasting", f"{active.title}: {detail}", system=True)
        self._bus.broadcast_ended.emit(active)
        if active.is_emergency:
            self._bus.emergency_changed.emit(False)
        self._bus.broadcast_state_changed.emit()
        self._bus.data_changed.emit("history")

    # ----------------------------------------------------------------- poll
    def _poll(self) -> None:
        status = self.engine.poll()
        active = self.active
        if active is None:
            return
        for error in status.errors:
            if error not in self._reported_errors:
                self._reported_errors.add(error)
                self._bus.toast("error", "Output problem", error)
                self._activity.log("Output device error", "Broadcasting", error, "warning", system=True)
        if not status.active:
            if status.errors:
                self._finish("interrupted", "; ".join(status.errors)[:400], "System")
            elif active.pending_end:
                self._finish(*active.pending_end)
            else:
                self._finish("completed", "Finished normally", "System")
            return
        if active.ending:
            if time.monotonic() - active.ending_since > self.OUTRO_TIMEOUT:
                self.engine.stop()
                self._finish(*active.pending_end)
            return
        if active.request.max_seconds and active.elapsed >= active.request.max_seconds:
            minutes = int(active.request.max_seconds // 60)
            detail = f"Automatically stopped after {minutes} min (safety limit)"
            if self._begin_outro(active, "cancelled", detail, "System"):
                return
            self.engine.stop()
            self._finish("completed" if active.kind == "alarm" else "cancelled", detail, "System")
            return
        mic = self.mic
        if active.kind == "live" and mic is not None and mic.running and time.monotonic() - mic.last_callback > 2.5:
            self.engine.stop()
            self._bus.toast("error", "Microphone disconnected", "Live paging was stopped because the microphone "
                                                                "stopped sending audio.")
            self._finish("interrupted", "Microphone stopped responding (disconnected?)", "System")

    # ---------------------------------------------------------- live paging
    def start_live(self, zone_ids: list[int], device: SystemDevice | None, user: User, push_to_talk: bool,
                   interrupt: bool = False) -> ActiveBroadcast:
        mic = MicrophoneSource(device, gain_db=gain_db_from_slider(self._settings.get_int("audio.mic_gain")),
                               high_pass=self._settings.get_bool("audio.high_pass"),
                               feedback_guard=self._settings.get_bool("audio.feedback_guard"),
                               gate_open=not push_to_talk,
                               name=f"Microphone: {device.name}" if device else "Microphone")
        intro = outro = None
        if self.chime_provider:
            if self._settings.get_bool("audio.chime_before"):
                chime = self.chime_provider(self._settings.get("audio.chime_sound") or "chime_pre")
                if chime is not None:
                    intro = SequenceSource([chime, SilenceSource(0.15)], name="Intro chime")
            if self._settings.get_bool("audio.chime_after"):
                chime = self.chime_provider(self._settings.get("audio.outro_chime_sound") or "chime_post")
                if chime is not None:
                    outro = SequenceSource([SilenceSource(0.3), chime], name="Outro chime")
        source = ProgramSource(mic, intro, outro, name="Live paging")
        request = BroadcastRequest(kind="live", title="Live Paging", zone_ids=zone_ids, source=source,
                                   priority=Priority.LIVE, category="Live Announcement", user=user,
                                   max_seconds=max(1, self._settings.get_int("paging.max_live_minutes")) * 60,
                                   interrupt=interrupt)
        self.mic = mic
        try:
            return self.start(request)
        except Exception:
            self.mic = None
            raise

    # ---------------------------------------------------------------- tests
    def test_zone(self, zone_id: int, user: User, interrupt: bool = False) -> ActiveBroadcast:
        zone = self.zones.get_zone(zone_id)
        source = self.test_source_provider(zone.name) if self.test_source_provider else None
        if source is None:
            raise BroadcastRejected("Test audio is unavailable.", "content")
        return self.start(BroadcastRequest(kind="test", title=f"Zone test: {zone.name}", zone_ids=[zone.id],
                                           source=source, priority=Priority.TEST, category="Zone Test", user=user,
                                           interrupt=interrupt))

    def test_device(self, device_id: int, user: User, interrupt: bool = False) -> ActiveBroadcast:
        device = self.zones.get_device(device_id)
        target = self.zones.device_target(device_id)
        source = self.test_source_provider(device.name) if self.test_source_provider else None
        if source is None:
            raise BroadcastRejected("Test audio is unavailable.", "content")
        return self.start(BroadcastRequest(kind="test", title=f"Device test: {device.name}", zone_ids=[],
                                           source=source, priority=Priority.TEST, category="Device Test", user=user,
                                           interrupt=interrupt, targets=[target]))

    def set_talking(self, talking: bool) -> None:
        if self.mic is not None:
            self.mic.set_gate(talking)

    def set_mic_gain(self, slider_value: int) -> None:
        if self.mic is not None:
            self.mic.set_gain_db(gain_db_from_slider(slider_value))

    def set_master_volume(self, percent: int) -> None:
        self.engine.set_master_volume(percent)

    # ---------------------------------------------------------------- state
    @property
    def is_active(self) -> bool:
        return self.active is not None

    @property
    def emergency_active(self) -> bool:
        return self.active is not None and self.active.is_emergency

    def active_zone_ids(self) -> set[int]:
        return set(self.active.zone_ids) if self.active else set()

    @property
    def mic_level(self) -> float:
        return self.mic.level if self.mic is not None else 0.0

    @property
    def output_level(self) -> float:
        return self.engine.peak_level()

    def shutdown(self) -> None:
        self._timer.stop()
        self.stop_all("Application closed")
