"""Scheduled broadcasts: recurrence rules, conflict detection and execution."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from database.database import now_str
from database.models import ScheduledBroadcast, User
from database.repositories import Repositories
from services.audio_sources import AudioSource, BufferSource

from .activity_logger import ActivityLogger
from .alarm_manager import AlarmManager
from .announcement_manager import AnnouncementManager
from .audio_manager import AudioLibraryManager
from .authentication import AuthManager
from .errors import BroadcastRejected, ConflictError, NotFoundError, ValidationError
from .events import EventBus
from .paging_manager import ActiveBroadcast, BroadcastController, BroadcastRequest, Priority
from .permissions import P
from .settings_manager import SettingsManager
from .zone_manager import ZoneManager

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RECURRENCE_LABELS = {
    "once": "One time", "daily": "Every day", "weekdays": "Weekdays (Mon-Fri)", "weekly": "Weekly on selected days",
    "custom": "Every N weeks on selected days",
}
CONTENT_LABELS = {"announcement": "Announcement", "tts": "Text-to-speech", "recording": "Voice recording",
                  "audio_file": "Audio file", "alarm": "Alarm"}
SCHEDULE_CATEGORIES = {"tts": "Scheduled Text-to-Speech", "recording": "Scheduled Voice Recording",
                       "audio_file": "Scheduled Audio"}
MAX_TTS_CHARS = 2000
CONFLICT_MARGIN_SECONDS = 5


# ----------------------------------------------------------- recurrence rules
def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def parse_time(value: str) -> time:
    hours, minutes = str(value).split(":")[:2]
    return time(int(hours), int(minutes))


def occurs_on(s: ScheduledBroadcast, day: date) -> bool:
    start = parse_date(s.start_date)
    end = parse_date(s.end_date)
    if start and day < start:
        return False
    if end and day > end:
        return False
    if s.recurrence == "once":
        return day == start
    if s.recurrence == "daily":
        return True
    if s.recurrence == "weekdays":
        return day.weekday() < 5
    if s.recurrence == "weekly":
        return day.weekday() in s.days_of_week
    if s.recurrence == "custom":
        if day.weekday() not in s.days_of_week or not start:
            return False
        week0 = start - timedelta(days=start.weekday())
        weeks = (day - week0).days // 7
        return weeks % max(1, s.interval_weeks) == 0
    return False


def occurrence_at(s: ScheduledBroadcast, day: date) -> datetime:
    return datetime.combine(day, parse_time(s.time_of_day))


def occurrences_between(s: ScheduledBroadcast, start: datetime, end: datetime) -> list[datetime]:
    result, day = [], start.date()
    while day <= end.date():
        if occurs_on(s, day):
            when = occurrence_at(s, day)
            if start <= when <= end:
                result.append(when)
        day += timedelta(days=1)
    return result


def next_occurrence(s: ScheduledBroadcast, after: datetime, horizon_days: int = 400) -> datetime | None:
    if s.recurrence == "once":
        start = parse_date(s.start_date)
        when = occurrence_at(s, start) if start else None
        return when if when and when > after else None
    day = after.date()
    end = parse_date(s.end_date)
    for _ in range(horizon_days):
        if end and day > end:
            return None
        if occurs_on(s, day):
            when = occurrence_at(s, day)
            if when > after:
                return when
        day += timedelta(days=1)
    return None


def describe(s: ScheduledBroadcast) -> str:
    clock = parse_time(s.time_of_day).strftime("%I:%M %p").lstrip("0")
    days = ", ".join(WEEKDAYS[d] for d in sorted(s.days_of_week)) or "no days"
    if s.recurrence == "once":
        start = parse_date(s.start_date)
        return f"Once on {start.strftime('%b %d, %Y') if start else '?'} at {clock}"
    if s.recurrence == "daily":
        text = f"Every day at {clock}"
    elif s.recurrence == "weekdays":
        text = f"Weekdays at {clock}"
    elif s.recurrence == "weekly":
        text = f"Every {days} at {clock}"
    else:
        text = f"Every {s.interval_weeks} week{'s' if s.interval_weeks != 1 else ''} on {days} at {clock}"
    end = parse_date(s.end_date)
    return text + (f" until {end.strftime('%b %d, %Y')}" if end else "")


def _seconds(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def find_conflicts(candidate: ScheduledBroadcast, others: list[ScheduledBroadcast], horizon_days: int = 366,
                   today: date | None = None) -> list[tuple[ScheduledBroadcast, datetime]]:
    """Enabled schedules whose airtime overlaps ``candidate`` on at least one future day."""
    today = today or date.today()
    a_start = _seconds(parse_time(candidate.time_of_day))
    a_end = a_start + max(1, candidate.duration_seconds) + CONFLICT_MARGIN_SECONDS
    conflicts = []
    for other in others:
        if other.id == candidate.id or not other.is_enabled:
            continue
        b_start = _seconds(parse_time(other.time_of_day))
        b_end = b_start + max(1, other.duration_seconds) + CONFLICT_MARGIN_SECONDS
        if not (a_start < b_end and b_start < a_end):
            continue
        first = max(d for d in (today, parse_date(candidate.start_date), parse_date(other.start_date)) if d)
        ends = [d for d in (parse_date(candidate.end_date), parse_date(other.end_date)) if d]
        last = min([today + timedelta(days=horizon_days)] + ends)
        day = first
        while day <= last:
            if occurs_on(candidate, day) and occurs_on(other, day):
                conflicts.append((other, occurrence_at(other, day)))
                break
            day += timedelta(days=1)
    return conflicts


# ------------------------------------------------------------------- manager
class ScheduleManager:
    def __init__(self, repos: Repositories, auth: AuthManager, activity: ActivityLogger, bus: EventBus,
                 zones: ZoneManager, announcements: AnnouncementManager, alarms: AlarmManager,
                 library: AudioLibraryManager, controller: BroadcastController, settings: SettingsManager):
        self._repos = repos
        self._auth = auth
        self._activity = activity
        self._bus = bus
        self.zones = zones
        self.announcements = announcements
        self.alarms = alarms
        self.library = library
        self.controller = controller
        self._settings = settings

    # ---------------------------------------------------------------- query
    def schedules(self) -> list[ScheduledBroadcast]:
        return self._repos.schedules.all()

    def get(self, schedule_id: int) -> ScheduledBroadcast:
        item = self._repos.schedules.get(schedule_id)
        if item is None:
            raise NotFoundError("That schedule no longer exists.")
        return item

    @property
    def paused(self) -> bool:
        return self._settings.get_bool("schedules.paused")

    def set_paused(self, paused: bool) -> None:
        self._auth.require(P.SCHEDULES_MANAGE)
        self._settings.set("schedules.paused", bool(paused))
        self._activity.log("Paused all schedules" if paused else "Resumed schedules", "Scheduled Broadcasts",
                           level="warning" if paused else "info")
        self._bus.data_changed.emit("schedules")

    def upcoming(self, limit: int = 10, days: int = 7, start: datetime | None = None
                 ) -> list[tuple[datetime, ScheduledBroadcast]]:
        start = start or datetime.now()
        end = start + timedelta(days=days)
        items = []
        for s in self.schedules():
            if s.is_enabled:
                last = s.last_run_at or ""
                items.extend((when, s) for when in occurrences_between(s, start, end)
                             if when.strftime("%Y-%m-%d %H:%M:%S") > last)
        items.sort(key=lambda pair: pair[0])
        return items[:limit]

    def occurrences_on(self, day: date, include_disabled: bool = True) -> list[tuple[datetime, ScheduledBroadcast]]:
        items = [(occurrence_at(s, day), s) for s in self.schedules()
                 if (include_disabled or s.is_enabled) and occurs_on(s, day)]
        return sorted(items, key=lambda pair: pair[0])

    def month_counts(self, year: int, month: int) -> dict[date, int]:
        day = date(year, month, 1)
        counts: dict[date, int] = {}
        enabled = [s for s in self.schedules() if s.is_enabled]
        while day.month == month:
            n = sum(1 for s in enabled if occurs_on(s, day))
            if n:
                counts[day] = n
            day += timedelta(days=1)
        return counts

    def next_run(self, s: ScheduledBroadcast) -> datetime | None:
        return next_occurrence(s, datetime.now()) if s.is_enabled else None

    def content_duration(self, content_type: str, content_id: int | None, play_chime: bool,
                         play_outro_chime: bool = False, tts_message: str = "", tts_rate: int = 0,
                         pending_seconds: float = 0.0) -> int:
        seconds = 0.0
        if content_type == "announcement" and content_id:
            ann = self._repos.announcements.get(content_id)
            if ann:
                ann.play_chime = ann.play_chime or play_chime
                ann.play_outro_chime = ann.play_outro_chime or play_outro_chime
                seconds = self.announcements.estimate_duration(ann)
        elif content_type in ("audio_file", "recording") and (content_id or pending_seconds):
            item = self._repos.audio_files.get(content_id) if content_id else None
            body = pending_seconds or (item.duration_seconds if item else 0)
            seconds = body + self.announcements.chime_seconds(play_chime, play_outro_chime)
        elif content_type == "tts" and tts_message.strip():
            seconds = self.announcements.tts.estimate_seconds(tts_message, tts_rate) + \
                self.announcements.chime_seconds(play_chime, play_outro_chime)
        elif content_type == "alarm" and content_id:
            alarm = self._repos.alarms.get(content_id)
            seconds = alarm.duration_seconds if alarm else 0
        return max(5, int(round(seconds + 0.5)))

    # --------------------------------------------------------------- change
    def build(self, data: dict, zone_ids: list[int], schedule_id: int | None = None) -> ScheduledBroadcast:
        """Validate form data and return an (unsaved) schedule record."""
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Enter a name for the scheduled broadcast.")
        content_type = data.get("content_type")
        if content_type not in CONTENT_LABELS:
            raise ValidationError("Choose what should be broadcast.")
        content_id = data.get("content_id")
        enabled = bool(data.get("is_enabled", True))
        tts_message = (data.get("tts_message") or "").strip() if content_type == "tts" else ""
        # Editor preview only: a recording that is not saved to the library yet (save() never accepts it).
        pending_seconds = float(data.get("pending_audio_seconds") or 0) if content_type == "recording" else 0.0
        if content_type == "tts":
            content_id = None
            if len(tts_message) > MAX_TTS_CHARS:
                raise ValidationError(f"The spoken message is too long (maximum {MAX_TTS_CHARS} characters).")
            if not tts_message and enabled:
                raise ValidationError("Type the message to be spoken (or disable the schedule).")
        elif content_id:
            content_id = int(content_id)
            if content_type == "announcement":
                self.announcements.get(content_id)
            elif content_type in ("audio_file", "recording"):
                self.library.get(content_id)
            else:
                alarm = self.alarms.get(content_id)
                if not self._auth.has(P.ALARMS_TRIGGER):
                    raise ValidationError("Scheduling an emergency alarm requires permission to trigger alarms.")
                if not alarm.duration_seconds:
                    raise ValidationError(f"'{alarm.name}' plays until stopped. Give it a fixed duration in alarm "
                                          "settings before scheduling it.")
        elif enabled and not pending_seconds:
            if content_type == "recording":
                raise ValidationError("Record the voice message first (or disable the schedule).")
            raise ValidationError(f"Choose the {CONTENT_LABELS[content_type].lower()} to broadcast "
                                  "(or disable the schedule).")
        start = parse_date(data.get("start_date"))
        if start is None:
            raise ValidationError("Choose a start date.")
        try:
            parse_time(data.get("time_of_day") or "")
        except ValueError as exc:
            raise ValidationError("Enter a valid time.") from exc
        recurrence = data.get("recurrence")
        if recurrence not in RECURRENCE_LABELS:
            raise ValidationError("Choose how often the broadcast repeats.")
        days = sorted({int(d) for d in data.get("days_of_week") or [] if 0 <= int(d) <= 6})
        if recurrence in ("weekly", "custom") and not days:
            raise ValidationError("Select at least one day of the week.")
        end = parse_date(data.get("end_date"))
        if end and end < start:
            raise ValidationError("The end date must be on or after the start date.")
        if recurrence == "once" and enabled and occurrence_at_dt(start, data["time_of_day"]) < datetime.now() \
                and schedule_id is None:
            raise ValidationError("That date and time are already in the past.")
        if not zone_ids:
            raise ValidationError("Select at least one target zone.")
        chimes = content_type != "alarm"
        play_chime = chimes and bool(data.get("play_chime", False))
        play_outro_chime = chimes and bool(data.get("play_outro_chime", play_chime))
        if content_type == "tts":
            tts_voice = str(data.get("tts_voice") or "")
            tts_rate = max(-10, min(10, int(data.get("tts_rate") or 0)))
            tts_volume = max(0, min(100, int(data.get("tts_volume", 100))))
        else:
            tts_voice, tts_rate, tts_volume = "", 0, 100
        duration = self.content_duration(content_type, content_id, play_chime, play_outro_chime, tts_message, tts_rate,
                                         pending_seconds)
        return ScheduledBroadcast(
            id=schedule_id or 0, name=name[:120], content_type=content_type,
            announcement_id=content_id if content_type == "announcement" else None,
            audio_file_id=content_id if content_type in ("audio_file", "recording") else None,
            alarm_type_id=content_id if content_type == "alarm" else None,
            start_date=start.isoformat(), time_of_day=parse_time(data["time_of_day"]).strftime("%H:%M"),
            recurrence=recurrence, days_of_week=days, interval_weeks=max(1, int(data.get("interval_weeks") or 1)),
            end_date=end.isoformat() if end else None, duration_seconds=duration, play_chime=play_chime,
            is_enabled=enabled, last_run_at=None, last_status="", notes=(data.get("notes") or "").strip()[:500],
            created_by=None, zone_ids=sorted(set(zone_ids)), play_outro_chime=play_outro_chime,
            tts_message=tts_message, tts_voice=tts_voice, tts_rate=tts_rate, tts_volume=tts_volume)

    def check_conflicts(self, candidate: ScheduledBroadcast) -> list[tuple[ScheduledBroadcast, datetime]]:
        if not candidate.is_enabled:
            return []
        return find_conflicts(candidate, self.schedules())

    def save(self, data: dict, zone_ids: list[int], schedule_id: int | None = None) -> ScheduledBroadcast:
        user = self._auth.require(P.SCHEDULES_MANAGE)
        if schedule_id is not None:
            self.get(schedule_id)
        data = {key: value for key, value in data.items() if key != "pending_audio_seconds"}
        candidate = self.build(data, zone_ids, schedule_id)
        conflicts = self.check_conflicts(candidate)
        if conflicts:
            lines = [f"'{c.name}' at {when.strftime('%a %b %d, %I:%M %p').replace(' 0', ' ')}" for c, when in conflicts]
            raise ConflictError("This schedule overlaps with: " + "; ".join(lines) +
                                ". Choose a different time so broadcasts do not collide.", conflicts)
        record = {
            "name": candidate.name, "content_type": candidate.content_type,
            "announcement_id": candidate.announcement_id, "audio_file_id": candidate.audio_file_id,
            "alarm_type_id": candidate.alarm_type_id, "start_date": candidate.start_date,
            "time_of_day": candidate.time_of_day, "recurrence": candidate.recurrence,
            "days_of_week": candidate.days_of_week, "interval_weeks": candidate.interval_weeks,
            "end_date": candidate.end_date, "duration_seconds": candidate.duration_seconds,
            "play_chime": candidate.play_chime, "play_outro_chime": candidate.play_outro_chime,
            "tts_message": candidate.tts_message, "tts_voice": candidate.tts_voice, "tts_rate": candidate.tts_rate,
            "tts_volume": candidate.tts_volume, "is_enabled": candidate.is_enabled, "notes": candidate.notes,
        }
        if schedule_id is None:
            record["created_by"] = user.id
            schedule_id = self._repos.schedules.create(record, candidate.zone_ids)
            self._activity.log("Created schedule", "Scheduled Broadcasts", f"{candidate.name}: {describe(candidate)}")
        else:
            self._repos.schedules.update(schedule_id, record, candidate.zone_ids)
            self._activity.log("Updated schedule", "Scheduled Broadcasts", f"{candidate.name}: {describe(candidate)}")
        self._bus.data_changed.emit("schedules")
        return self.get(schedule_id)

    def set_enabled(self, schedule_id: int, enabled: bool) -> None:
        self._auth.require(P.SCHEDULES_MANAGE)
        item = self.get(schedule_id)
        if enabled:
            if not item.has_content:
                raise ValidationError(f"Assign content to '{item.name}' before enabling it.")
            item.is_enabled = True
            conflicts = self.check_conflicts(item)
            if conflicts:
                other, when = conflicts[0]
                raise ConflictError(f"'{item.name}' would overlap with '{other.name}' "
                                    f"({when.strftime('%a %b %d, %I:%M %p')}).", conflicts)
        self._repos.schedules.update(schedule_id, {"is_enabled": enabled})
        self._activity.log("Enabled schedule" if enabled else "Disabled schedule", "Scheduled Broadcasts", item.name)
        self._bus.data_changed.emit("schedules")

    def delete(self, schedule_id: int) -> None:
        self._auth.require(P.SCHEDULES_MANAGE)
        item = self.get(schedule_id)
        self._repos.schedules.delete(schedule_id)
        self._activity.log("Deleted schedule", "Scheduled Broadcasts", item.name, "warning")
        self._bus.data_changed.emit("schedules")

    # ------------------------------------------------------------ execution
    def due(self, now: datetime, grace_minutes: int) -> list[tuple[ScheduledBroadcast, datetime]]:
        window_start = now - timedelta(minutes=max(0, grace_minutes))
        result = []
        for s in self.schedules():
            if not s.is_enabled or not s.has_content:
                continue
            last = datetime.strptime(s.last_run_at, "%Y-%m-%d %H:%M:%S") if s.last_run_at else None
            for when in occurrences_between(s, window_start, now):
                if last is None or when > last:
                    result.append((s, when))
        result.sort(key=lambda pair: pair[1])
        return result

    def priority_for(self, s: ScheduledBroadcast) -> int:
        if s.content_type == "alarm":
            return Priority.EMERGENCY
        if s.content_type == "announcement":
            ann = self._repos.announcements.get(s.announcement_id)
            if ann:
                return self.announcements.priority_for(ann)
        return Priority.ANNOUNCEMENT

    def kind_for(self, s: ScheduledBroadcast) -> str:
        return "alarm" if s.content_type == "alarm" else "scheduled"

    def execute(self, s: ScheduledBroadcast, occurrence: datetime, user: User | None = None,
                origin: str = "scheduler", interrupt: bool = False) -> ActiveBroadcast:
        zones = s.zone_ids or ([self.zones.campus_zone().id] if self.zones.campus_zone() else [])
        if s.content_type == "announcement":
            active = self.announcements.broadcast(s.announcement_id, zones, interrupt=interrupt, origin=origin,
                                                  user=user, schedule_id=s.id,
                                                  play_chime=True if s.play_chime else None,
                                                  play_outro_chime=True if s.play_outro_chime else None)
        elif s.content_type == "alarm":
            active = self.alarms.trigger(s.alarm_type_id, zones, user=user, interrupt=True, origin=origin,
                                         schedule_title=s.name)
        else:
            if origin == "manual":
                user = self._auth.require(P.ANNOUNCEMENTS_BROADCAST, user)
            try:
                body = self.content_source(s)
            except (ValidationError, ValueError, RuntimeError) as exc:
                raise BroadcastRejected(f"Could not prepare '{s.name}': {exc}", "content") from exc
            source = self.announcements.with_chimes([body], s.name, intro=s.play_chime, outro=s.play_outro_chime)
            active = self.controller.start(BroadcastRequest(
                kind="scheduled" if origin == "scheduler" else "audio", title=s.name, zone_ids=zones, source=source,
                priority=Priority.ANNOUNCEMENT, category=SCHEDULE_CATEGORIES.get(s.content_type, "Scheduled Audio"),
                user=user, origin=origin, reference_type="schedule", reference_id=s.id, interrupt=interrupt))
        if origin == "scheduler":
            self._repos.history.update(active.history_id, title=s.name, reference_type="schedule",
                                       reference_id=s.id)
            self._repos.schedules.mark_run(s.id, occurrence, "played")
            self._activity.log("Scheduled broadcast started", "Scheduled Broadcasts",
                               f"{s.name} ({occurrence.strftime('%H:%M')})", system=True)
            self._bus.data_changed.emit("schedules")
        return active

    def content_source(self, s: ScheduledBroadcast) -> AudioSource:
        """The schedule's own audio (without chimes) for audio-file, recording and text-to-speech schedules."""
        if s.content_type == "tts":
            if not s.tts_message.strip():
                raise ValidationError(f"'{s.name}' has no message to speak.")
            voice = s.tts_voice or self._settings.get("tts.voice") or self.announcements.tts.default_voice_id()
            data, rate = self.announcements.tts.load(s.tts_message, voice, s.tts_rate, s.tts_volume)
            return BufferSource(data, rate, s.name)
        if s.content_type in ("audio_file", "recording"):
            if not s.audio_file_id:
                raise ValidationError(f"'{s.name}' has no audio assigned.")
            return self.library.source_for(s.audio_file_id)
        raise ValidationError(f"'{s.name}' does not have its own audio.")

    def prewarm(self, s: ScheduledBroadcast) -> None:
        """Render a text-to-speech schedule ahead of time (call from a worker thread)."""
        tts = self.announcements.tts
        if s.content_type == "tts" and s.tts_message.strip() and tts.available:
            voice = s.tts_voice or self._settings.get("tts.voice") or tts.default_voice_id()
            tts.synthesize(s.tts_message, voice, s.tts_rate, s.tts_volume)

    def run_now(self, schedule_id: int, interrupt: bool = False) -> ActiveBroadcast:
        user = self._auth.require(P.SCHEDULES_MANAGE)
        s = self.get(schedule_id)
        if not s.has_content:
            raise BroadcastRejected(f"'{s.name}' has no content assigned.", "content")
        return self.execute(s, datetime.now(), user=user, origin="manual", interrupt=interrupt)

    def mark_missed(self, s: ScheduledBroadcast, occurrence: datetime, reason: str) -> None:
        self._repos.history.create(
            title=s.name, broadcast_type="scheduled", category="Scheduled", priority=int(self.priority_for(s)),
            source="scheduler", initiated_by_name="Scheduler", zone_names=self.zones.describe_zones(s.zone_ids),
            zone_ids=self.zones.expand_zone_ids(s.zone_ids),
            started_at=occurrence.strftime("%Y-%m-%d %H:%M:%S"), ended_at=now_str(), status="missed",
            status_detail=reason[:400], reference_type="schedule", reference_id=s.id)
        self._repos.schedules.mark_run(s.id, occurrence, "missed")
        self._activity.log("Scheduled broadcast missed", "Scheduled Broadcasts", f"{s.name}: {reason}", "warning",
                           system=True)
        self._bus.data_changed.emit("history")
        self._bus.data_changed.emit("schedules")

    def mark_failed(self, s: ScheduledBroadcast, occurrence: datetime, reason: str) -> None:
        self._repos.schedules.mark_run(s.id, occurrence, "failed")
        self._activity.log("Scheduled broadcast failed", "Scheduled Broadcasts", f"{s.name}: {reason}", "warning",
                           system=True)
        self._bus.data_changed.emit("schedules")


def occurrence_at_dt(day: date, time_text: str) -> datetime:
    return datetime.combine(day, parse_time(time_text))
