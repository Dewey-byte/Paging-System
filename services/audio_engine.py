"""Broadcast audio engine: fans one source out to every configured endpoint."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace

from .audio_output import (GainControl, LocalOutputSession, OutputSession, OutputTarget,
                           SimulatedOutputSession)
from .audio_sources import AudioSource
from .device_detection import DeviceRegistry
from .network_audio import DRIVERS

log = logging.getLogger(__name__)

STALL_SECONDS = 2.5
STOP_TIMEOUT = 1.5


@dataclass
class StartReport:
    started: list[OutputTarget] = field(default_factory=list)
    failed: list[tuple[OutputTarget, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.started)

    @property
    def real_count(self) -> int:
        return sum(1 for t in self.started if not t.simulated)

    @property
    def simulated_count(self) -> int:
        return sum(1 for t in self.started if t.simulated)


@dataclass
class EngineStatus:
    active: bool
    just_finished: bool = False
    errors: list[str] = field(default_factory=list)


class AudioEngine:
    def __init__(self, registry: DeviceRegistry, latency: str = "balanced", force_simulated: bool = False):
        self.registry = registry
        self.latency = latency
        self.force_simulated = force_simulated
        self.master = GainControl(0.8)
        self.sessions: list[OutputSession] = []
        self.source: AudioSource | None = None
        self._stop_requested_at: float | None = None
        self._errors: list[str] = []

    # ------------------------------------------------------------- control
    @property
    def active(self) -> bool:
        return bool(self.sessions)

    def set_master_volume(self, percent: float) -> None:
        self.master.value = max(0.0, min(1.0, percent / 100.0))

    def _make_session(self, target: OutputTarget, source: AudioSource) -> OutputSession:
        if target.transport == "simulated" or (self.force_simulated and target.transport == "local"):
            return SimulatedOutputSession(target, source, self.master, self.latency)
        if target.transport == "local":
            return LocalOutputSession(target, source, self.master, self.latency)
        driver = DRIVERS.get(target.transport)
        if driver is None or not driver.streams_audio:
            raise RuntimeError(f"'{target.device_name}' uses an integration that cannot stream audio yet.")
        return driver.create_session(target, source, self.master)

    def start(self, source: AudioSource, targets: list[OutputTarget]) -> StartReport:
        """Start playing ``source`` on every target. Raises if the source itself cannot start."""
        if self.sessions:
            self.stop_now()
        report = StartReport()
        source.start()
        sessions = []
        for target in targets:
            if self.force_simulated and target.transport == "local":
                target = replace(target, transport="simulated", device_name=f"{target.device_name} (simulated)")
            try:
                session = self._make_session(target, source)
                session.start()
                sessions.append(session)
                report.started.append(target)
            except Exception as exc:
                log.warning("Output %s failed: %s", target.device_name, exc)
                report.failed.append((target, str(exc)))
        if not sessions:
            source.stop()
            return report
        self.source = source
        self.sessions = sessions
        self._stop_requested_at = None
        self._errors = []
        return report

    def stop(self) -> None:
        """Fade out and stop (completes asynchronously; see :meth:`poll`)."""
        if not self.sessions:
            return
        for session in self.sessions:
            session.request_stop()
        if self._stop_requested_at is None:
            self._stop_requested_at = time.monotonic()

    def stop_now(self) -> None:
        for session in self.sessions:
            session.request_stop()
            session.abort()
            session.close()
        self.sessions = []
        if self.source is not None:
            self.source.stop()
            self.source = None
        self._stop_requested_at = None

    def poll(self) -> EngineStatus:
        """Housekeeping; call periodically from the GUI thread."""
        if not self.sessions:
            return EngineStatus(active=False)
        now = time.monotonic()
        for session in self.sessions:
            if session.finished:
                continue
            if session.started and now - session.last_activity > STALL_SECONDS:
                session.error = session.error or f"'{session.target.device_name}' stopped responding (disconnected?)"
                session.abort()
        if self._stop_requested_at is not None and now - self._stop_requested_at > STOP_TIMEOUT:
            for session in self.sessions:
                if not session.finished:
                    session.abort()
        for session in self.sessions:
            if session.error and session.error not in self._errors:
                self._errors.append(session.error)
        if all(s.finished for s in self.sessions):
            errors = list(self._errors)
            for session in self.sessions:
                session.close()
            self.sessions = []
            if self.source is not None:
                self.source.stop()
                self.source = None
            self._stop_requested_at = None
            return EngineStatus(active=False, just_finished=True, errors=errors)
        return EngineStatus(active=True, errors=list(self._errors))

    # --------------------------------------------------------------- info
    def levels(self) -> dict[str, float]:
        return {s.target.key: s.level for s in self.sessions}

    def peak_level(self) -> float:
        return max((s.level for s in self.sessions), default=0.0)

    def active_targets(self) -> list[OutputTarget]:
        return [s.target for s in self.sessions if not s.finished]

    def position_seconds(self) -> float:
        return max((s.position_seconds for s in self.sessions), default=0.0)


class PreviewPlayer:
    """Plays audio on the operator's local monitor output (never on paging zones)."""

    def __init__(self, registry: DeviceRegistry, latency: str = "balanced", force_simulated: bool = False):
        self.registry = registry
        self.latency = latency
        self.force_simulated = force_simulated
        self.gain = GainControl(0.9)
        self.session: OutputSession | None = None
        self.duration: float | None = None
        self.token: object | None = None

    def play(self, source: AudioSource, device_name: str = "", volume: float = 0.9, token: object = None) -> str:
        """Start a preview; returns a short description of the output used."""
        self.stop()
        self.gain.value = max(0.0, min(1.0, volume))
        device = None if self.force_simulated else self.registry.find_output(device_name)
        if device is None and not self.force_simulated:
            raise RuntimeError("No local audio output is connected for previews.")
        target = OutputTarget(key="preview", device_id=0, device_name=device.name if device else "Simulated preview",
                              transport="local" if device else "simulated", system_device=device,
                              open_channels=2, gain=1.0)
        session = (LocalOutputSession if device else SimulatedOutputSession)(target, source, self.gain, self.latency)
        session.start()
        self.session = session
        self.duration = source.duration
        self.token = token
        return target.device_name

    @property
    def playing(self) -> bool:
        if self.session is None:
            return False
        if self.session.finished:
            self.session.close()
            self.session = None
            self.token = None
            return False
        return True

    @property
    def position(self) -> float:
        return self.session.position_seconds if self.session else 0.0

    @property
    def level(self) -> float:
        return self.session.level if self.session else 0.0

    def stop(self) -> None:
        if self.session is not None:
            self.session.request_stop()
            self.session.abort()
            self.session.close()
            self.session = None
        self.token = None
