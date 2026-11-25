"""Intro/outro chimes (live paging and announcements) and the predefined Shooting Incident alarm."""
from __future__ import annotations

import time

import numpy as np
import pytest

from conftest import wait_until
from core.paging_manager import BroadcastRequest, Priority
from core.seed_data import ensure_system_alarms
from services.audio_input import MicrophoneSource
from services.audio_sources import AudioSource, BufferSource, LiveReader, ProgramSource, SequenceSource


class _FakeMic(AudioSource):
    is_live = True
    name = "fake mic"

    def open_reader(self, sample_rate):
        return LiveReader(sample_rate)


def _campus(ctx):
    return [ctx.zones.campus_zone().id]


def _live_request(ctx, outro_seconds: float | None = 0.3):
    outro = BufferSource(np.zeros(int(48000 * outro_seconds), dtype=np.float32), 48000, "outro") \
        if outro_seconds else None
    return BroadcastRequest(kind="live", title="Live Paging", zone_ids=_campus(ctx),
                            source=ProgramSource(_FakeMic(), None, outro), priority=Priority.LIVE,
                            user=ctx.auth.current_user)


@pytest.fixture
def fake_microphone(monkeypatch):
    """Let start_live run without audio hardware."""
    def start(self):
        self._running = True
        self.last_callback = time.monotonic() + 60
    monkeypatch.setattr(MicrophoneSource, "start", start)
    monkeypatch.setattr(MicrophoneSource, "stop", lambda self: setattr(self, "_running", False))


# ------------------------------------------------------------------ live paging
def test_stopping_live_page_plays_outro_before_finishing(ctx):
    ctx.controller.start(_live_request(ctx))
    ctx.controller.stop(ctx.auth.current_user, "Live page ended by operator")
    active = ctx.controller.active
    assert active is not None and active.ending          # still on air: the outro is playing
    assert ctx.controller.mic is None
    assert ctx.controller.evaluate("announcement", Priority.ANNOUNCEMENT, ctx.auth.current_user).code == "busy"
    assert wait_until(lambda: not ctx.controller.is_active, 3)
    record = ctx.history.recent(1)[0]
    assert record.status == "cancelled" and record.status_detail == "Live page ended by operator"
    assert record.ended_by_name == "Ada Admin"


def test_second_stop_cuts_the_outro(ctx):
    ctx.controller.start(_live_request(ctx, outro_seconds=10))
    ctx.controller.stop(ctx.auth.current_user, "Live page ended by operator")
    assert ctx.controller.active.ending
    ctx.controller.stop(ctx.auth.current_user)
    assert not ctx.controller.is_active
    assert ctx.history.recent(1)[0].status_detail == "Live page ended by operator"


def test_live_page_without_outro_stops_immediately(ctx):
    ctx.controller.start(_live_request(ctx, outro_seconds=None))
    ctx.controller.stop(ctx.auth.current_user)
    assert not ctx.controller.is_active


def test_emergency_interrupts_live_page_without_outro(ctx):
    ctx.controller.start(_live_request(ctx))
    ctx.alarms.trigger(ctx.alarms.alarm_types()[0].id, None, interrupt=True)
    assert ctx.controller.emergency_active
    assert ctx.history.search(broadcast_type="live")[0].status == "interrupted"


def test_start_live_uses_configured_intro_and_outro(ctx, fake_microphone):
    ctx.settings.set("audio.outro_chime_sound", "chime_ding_dong")
    active = ctx.controller.start_live(_campus(ctx), None, ctx.auth.current_user, push_to_talk=True)
    source = active.request.source
    assert isinstance(source, ProgramSource) and source.intro is not None and source.has_outro
    assert source.outro.parts[-1] is ctx.library.builtin_source("chime_ding_dong")
    ctx.controller.stop(ctx.auth.current_user)
    assert ctx.controller.active.ending
    assert wait_until(lambda: not ctx.controller.is_active, 8)

    ctx.settings.set("audio.chime_after", False)
    active = ctx.controller.start_live(_campus(ctx), None, ctx.auth.current_user, push_to_talk=True)
    assert not active.request.source.has_outro
    ctx.controller.stop(ctx.auth.current_user)
    assert not ctx.controller.is_active


# ---------------------------------------------------------------- announcements
def test_announcements_get_outro_chime_by_default(ctx):
    assert ctx.settings.get_bool("audio.chime_after")
    ann = ctx.announcements.list()[0]
    assert ann.play_chime and ann.play_outro_chime
    source = ctx.announcements.build_source(ann)
    assert isinstance(source, SequenceSource)
    assert source.parts[0] is ctx.library.builtin_source("chime_pre")
    assert source.parts[-1] is ctx.library.builtin_source("chime_post")
    without = ctx.announcements.build_source(ann, with_chimes=False)
    assert ctx.announcements.estimate_duration(ann) > without.duration + 6


def test_outro_follows_announcement_flag_global_switch_and_sound(ctx):
    ann = ctx.announcements.list()[0]
    saved = ctx.announcements.save(dict(title=ann.title, message=ann.message, category=ann.category,
                                        source_type="tts", play_chime=True, play_outro_chime=False), ann.zone_ids,
                                   ann.id)
    assert not saved.play_outro_chime
    assert ctx.announcements.build_source(saved).parts[-1] is not ctx.library.builtin_source("chime_post")
    ann.play_outro_chime = True
    ctx.settings.set("audio.outro_chime_sound", "bell_dismissal")
    assert ctx.announcements.build_source(ann).parts[-1] is ctx.library.builtin_source("bell_dismissal")
    ctx.settings.set("audio.chime_after", False)
    assert ctx.announcements.build_source(ann).parts[-1] is not ctx.library.builtin_source("bell_dismissal")


# ------------------------------------------------------------------- alarms
def test_shooting_incident_alarm_is_predefined_and_triggers(ctx):
    alarm = next(a for a in ctx.alarms.alarm_types() if a.code == "SHOOTING")
    assert alarm.name == "Shooting Incident" and alarm.is_system and alarm.is_enabled
    assert alarm.builtin_pattern == "alarm_lockdown" and "lock" in alarm.voice_message.lower()
    codes = [a.code for a in ctx.alarms.alarm_types()]
    assert codes.index("SHOOTING") == codes.index("SECURITY") + 1
    active = ctx.alarms.trigger(alarm.id, None)
    assert active.is_emergency and active.title == "Shooting Incident"
    ctx.alarms.stop_active()
    record = ctx.alarms.alarm_log(1)[0]
    assert record.title == "Shooting Incident" and record.status == "cancelled"


def test_new_predefined_alarms_are_added_to_existing_databases(ctx):
    alarm = next(a for a in ctx.alarms.alarm_types() if a.code == "SHOOTING")
    ctx.repos.alarms.delete(alarm.id)             # simulate a database created before this alarm existed
    assert ensure_system_alarms(ctx.repos) == 1
    assert ensure_system_alarms(ctx.repos) == 0
    restored = ctx.repos.alarms.get_by_code("SHOOTING")
    assert restored.is_system and restored.builtin_pattern == "alarm_lockdown"


def test_predefined_alarm_customisations_are_kept(ctx):
    fire = ctx.repos.alarms.get_by_code("FIRE")
    ctx.alarms.save(dict(name="Fire Alarm", duration_seconds=45, builtin_pattern="alarm_fire", color=fire.color,
                         voice_message="Custom"), fire.id)
    ensure_system_alarms(ctx.repos)
    fire = ctx.repos.alarms.get_by_code("FIRE")
    assert fire.duration_seconds == 45 and fire.voice_message == "Custom"
