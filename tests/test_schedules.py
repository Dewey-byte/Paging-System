"""Recurrence rules, conflict detection and automatic execution."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from conftest import pump
from core.errors import ConflictError, ValidationError
from core.schedule_manager import describe, find_conflicts, next_occurrence, occurs_on
from database.models import ScheduledBroadcast


def make(recurrence="daily", start="2026-01-05", time_of_day="08:00", days=None, interval=1, end=None, duration=30,
         sid=1, enabled=True):
    return ScheduledBroadcast(sid, f"S{sid}", "audio_file", None, 1, None, start, time_of_day, recurrence, days or [],
                              interval, end, duration, False, enabled, None, "", "", None)


def test_recurrence_rules():
    monday = date(2026, 1, 5)
    assert occurs_on(make("once"), monday) and not occurs_on(make("once"), monday + timedelta(days=1))
    assert occurs_on(make("daily"), date(2026, 3, 1))
    weekdays = make("weekdays")
    assert occurs_on(weekdays, monday) and not occurs_on(weekdays, date(2026, 1, 10))
    weekly = make("weekly", days=[0, 2, 4])
    assert [occurs_on(weekly, monday + timedelta(days=i)) for i in range(7)] == [True, False, True, False, True,
                                                                                  False, False]
    biweekly = make("custom", days=[4], interval=2)
    assert occurs_on(biweekly, date(2026, 1, 9)) and not occurs_on(biweekly, date(2026, 1, 16))
    assert occurs_on(biweekly, date(2026, 1, 23))
    ended = make("daily", end="2026-01-10")
    assert occurs_on(ended, date(2026, 1, 10)) and not occurs_on(ended, date(2026, 1, 11))
    assert not occurs_on(make("daily"), date(2026, 1, 4))  # before start


def test_next_occurrence_and_description():
    s = make("weekdays", time_of_day="07:30")
    assert next_occurrence(s, datetime(2026, 1, 9, 8, 0)) == datetime(2026, 1, 12, 7, 30)  # Fri -> Mon
    assert next_occurrence(make("once"), datetime(2026, 2, 1)) is None
    assert describe(s) == "Weekdays at 7:30 AM"
    assert describe(make("weekly", days=[0, 4], time_of_day="13:05")) == "Every Mon, Fri at 1:05 PM"


def test_conflict_detection():
    base = make("weekdays", time_of_day="12:00", duration=60, sid=1)
    overlapping = make("daily", time_of_day="12:00", duration=20, sid=2)
    later = make("daily", time_of_day="12:02", duration=20, sid=3)
    weekend = make("weekly", days=[5, 6], time_of_day="12:00", sid=4)
    disabled = make("daily", time_of_day="12:00", sid=5, enabled=False)
    found = find_conflicts(overlapping, [base, later, weekend, disabled], today=date(2026, 1, 5))
    assert [c.id for c, _ in found] == [1, 4]
    assert find_conflicts(later, [base], today=date(2026, 1, 5)) == []
    assert find_conflicts(weekend, [base], today=date(2026, 1, 5)) == []


def test_save_blocks_conflicts(ctx):
    campus = [ctx.zones.campus_zone().id]
    bell = ctx.library.files("Bell Sounds")[0]
    with pytest.raises(ConflictError):
        ctx.schedules.save(dict(name="Clash", content_type="audio_file", content_id=bell.id,
                                start_date=date.today().isoformat(), time_of_day="08:00", recurrence="weekdays"), campus)
    saved = ctx.schedules.save(dict(name="Afternoon bell", content_type="audio_file", content_id=bell.id,
                                    start_date=date.today().isoformat(), time_of_day="14:15", recurrence="weekly",
                                    days_of_week=[0, 2]), campus)
    assert saved.duration_seconds >= 5 and saved.days_of_week == [0, 2]
    with pytest.raises(ValidationError):
        ctx.schedules.save(dict(name="No days", content_type="audio_file", content_id=bell.id,
                                start_date=date.today().isoformat(), time_of_day="15:00", recurrence="weekly"), campus)


def _soon_schedule(ctx, minutes_ahead=1, name="Soon"):
    for other in ctx.schedules.schedules():  # isolate from the seeded daily schedule
        if other.is_enabled:
            ctx.schedules.set_enabled(other.id, False)
    when = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=minutes_ahead)
    bell = ctx.library.files("Bell Sounds")[0]
    s = ctx.schedules.save(dict(name=name, content_type="audio_file", content_id=bell.id,
                                start_date=when.date().isoformat(), time_of_day=when.strftime("%H:%M"),
                                recurrence="once"), [ctx.zones.campus_zone().id])
    return s, when


def test_scheduler_executes_due_broadcast_once(ctx):
    s, when = _soon_schedule(ctx)
    ctx.scheduler.tick(when + timedelta(seconds=2))
    assert ctx.controller.active is not None and ctx.controller.active.title == s.name
    assert ctx.controller.active.request.origin == "scheduler"
    ctx.controller.stop(ctx.auth.current_user)
    ctx.scheduler.tick(when + timedelta(seconds=10))
    assert ctx.controller.active is None  # not repeated
    assert ctx.schedules.get(s.id).last_status == "played"


def test_scheduler_waits_when_busy_then_marks_missed(ctx):
    s, when = _soon_schedule(ctx)
    ctx.alarms.trigger(ctx.alarms.alarm_types()[0].id, None)
    ctx.scheduler.tick(when + timedelta(seconds=1))
    assert ctx.scheduler.waiting_count == 1 and ctx.controller.active.kind == "alarm"
    grace = ctx.settings.get_int("schedules.grace_minutes")
    ctx.scheduler.tick(when + timedelta(minutes=grace, seconds=5))
    assert ctx.scheduler.waiting_count == 0
    assert all(schedule_id != s.id for schedule_id, _ in ctx.scheduler.pending)
    missed = ctx.history.search(status="missed")
    assert missed and missed[0].title == s.name


def test_paused_scheduler_does_nothing(ctx):
    s, when = _soon_schedule(ctx)
    ctx.schedules.set_paused(True)
    ctx.scheduler.tick(when + timedelta(seconds=2))
    assert ctx.controller.active is None
    ctx.schedules.set_paused(False)


def test_upcoming_and_month_counts(ctx):
    upcoming = ctx.schedules.upcoming(50, 7)
    assert all(s.is_enabled for _, s in upcoming)
    assert [w for w, _ in upcoming] == sorted(w for w, _ in upcoming)
    counts = ctx.schedules.month_counts(date.today().year, date.today().month)
    assert all(v > 0 for v in counts.values())


# ------------------------------------------------- own text-to-speech / recordings
def _isolate(ctx):
    for other in ctx.schedules.schedules():
        if other.is_enabled:
            ctx.schedules.set_enabled(other.id, False)


def _fake_tts(ctx, monkeypatch):
    spoken = []

    def load(text, voice="", rate=0, volume=100):
        spoken.append((text, rate, volume))
        return np.zeros(4800, dtype=np.float32), 48000
    monkeypatch.setattr(ctx.tts, "load", load)
    return spoken


def test_tts_schedule_validation_and_listing(ctx):
    campus = [ctx.zones.campus_zone().id]
    base = dict(name="Spoken reminder", content_type="tts", start_date=date.today().isoformat(),
                time_of_day="15:10", recurrence="weekdays")
    with pytest.raises(ValidationError):
        ctx.schedules.save(dict(base, tts_message="   "), campus)
    with pytest.raises(ValidationError):
        ctx.schedules.save(dict(base, tts_message="x" * 2001), campus)
    saved = ctx.schedules.save(dict(base, tts_message="Please return your library books today.", tts_rate=3,
                                    tts_volume=80, play_chime=True), campus)
    assert saved.content_type == "tts" and saved.has_content and saved.content_id is None
    assert saved.content_name == "Please return your library books today."
    assert (saved.tts_rate, saved.tts_volume) == (3, 80)
    assert saved.play_chime and saved.play_outro_chime          # outro defaults to the intro choice
    assert saved.duration_seconds > ctx.tts.estimate_seconds(saved.tts_message, 3) + 6  # chimes counted


def test_scheduler_speaks_tts_schedule_with_chimes(ctx, monkeypatch):
    spoken = _fake_tts(ctx, monkeypatch)
    _isolate(ctx)
    when = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
    s = ctx.schedules.save(dict(name="Spoken bell", content_type="tts", tts_message="Time for recess.",
                                start_date=when.date().isoformat(), time_of_day=when.strftime("%H:%M"),
                                recurrence="once", play_chime=True, play_outro_chime=True),
                           [ctx.zones.campus_zone().id])
    ctx.scheduler.tick(when + timedelta(seconds=1))
    active = ctx.controller.active
    assert active is not None and active.title == "Spoken bell" and active.request.origin == "scheduler"
    assert active.request.category == "Scheduled Text-to-Speech"
    parts = active.request.source.parts
    assert parts[0] is ctx.library.builtin_source("chime_pre") and parts[-1] is ctx.library.builtin_source("chime_post")
    assert spoken and spoken[0][0] == "Time for recess."
    assert ctx.schedules.get(s.id).last_status == "played"


def test_recording_schedule_uses_library_audio(ctx):
    campus = [ctx.zones.campus_zone().id]
    base = dict(name="Principal's message", content_type="recording", start_date=date.today().isoformat(),
                time_of_day="15:20", recurrence="weekly", days_of_week=[4])
    with pytest.raises(ValidationError) as info:
        ctx.schedules.save(base, campus)
    assert "Record" in info.value.message
    preview = ctx.schedules.build(dict(base, pending_audio_seconds=12.0), campus)   # editor summary only
    assert preview.duration_seconds >= 12
    with pytest.raises(ValidationError):                                      # but never saved without audio
        ctx.schedules.save(dict(base, pending_audio_seconds=12.0), campus)
    take = (np.random.default_rng(1).uniform(-0.2, 0.2, 48000 * 2).astype(np.float32), 48000)
    item = ctx.library.save_recording(*take, name="Principal's message")
    saved = ctx.schedules.save(dict(base, content_id=item.id), campus)
    assert saved.audio_file_id == item.id and saved.has_content
    assert saved.content_name == "Principal's message" and not saved.play_outro_chime
    with pytest.raises(ValidationError):                                      # still in use by the schedule
        ctx.library.delete(item.id)
    active = ctx.schedules.run_now(saved.id)
    assert active.request.category == "Scheduled Voice Recording"
    assert abs(active.request.source.duration - 2.0) < 0.05                  # no chimes requested
