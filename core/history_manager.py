"""Broadcast history queries, statistics and exports."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from database.models import BroadcastRecord
from database.repositories import Repositories
from services import reporting

from .activity_logger import ActivityLogger
from .authentication import AuthManager
from .permissions import P
from .settings_manager import SettingsManager

TYPE_LABELS = {
    "live": "Live Paging", "announcement": "Announcement", "alarm": "Emergency Alarm",
    "scheduled": "Scheduled", "audio": "Audio Playback", "test": "Zone Test",
}
STATUS_LABELS = {
    "in_progress": "On Air", "completed": "Completed", "cancelled": "Cancelled", "interrupted": "Interrupted",
    "failed": "Failed", "missed": "Missed",
}


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class HistoryManager:
    def __init__(self, repos: Repositories, auth: AuthManager, activity: ActivityLogger, settings: SettingsManager):
        self._repos = repos
        self._auth = auth
        self._activity = activity
        self._settings = settings

    def search(self, **filters) -> list[BroadcastRecord]:
        return self._repos.history.search(**filters)

    def get(self, record_id: int) -> BroadcastRecord | None:
        return self._repos.history.get(record_id)

    def recent(self, limit: int = 8) -> list[BroadcastRecord]:
        return self._repos.history.recent(limit)

    def stats_for_day(self, day: date | None = None) -> dict:
        day = day or date.today()
        start = datetime.combine(day, datetime.min.time())
        return self._repos.history.stats_between(start, start + timedelta(days=1))

    def stats_between(self, start: date, end: date) -> dict:
        return self._repos.history.stats_between(datetime.combine(start, datetime.min.time()),
                                                 datetime.combine(end + timedelta(days=1), datetime.min.time()))

    def daily_counts(self, days: int = 7) -> list[tuple[date, int]]:
        return self._repos.history.daily_counts(days)

    def filter_options(self) -> dict[str, list[str]]:
        return {"users": self._repos.history.distinct("initiated_by_name"),
                "categories": self._repos.history.distinct("category"),
                "types": self._repos.history.distinct("broadcast_type")}

    @staticmethod
    def summarize(records: list[BroadcastRecord]) -> dict:
        return {
            "total": len(records),
            "completed": sum(1 for r in records if r.status == "completed"),
            "stopped": sum(1 for r in records if r.status in ("cancelled", "interrupted")),
            "failed": sum(1 for r in records if r.status in ("failed", "missed")),
            "alarms": sum(1 for r in records if r.broadcast_type == "alarm"),
            "airtime": sum(r.duration_seconds or 0 for r in records),
        }

    def export(self, records: list[BroadcastRecord], path: str | Path, filters_text: str = "") -> Path:
        self._auth.require(P.HISTORY_EXPORT)
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            reporting.export_history_pdf(records, path, school=self._settings.school_name, filters_text=filters_text,
                                         summary=self.summarize(records), type_labels=TYPE_LABELS,
                                         status_labels=STATUS_LABELS)
        else:
            reporting.export_history_csv(records, path, type_labels=TYPE_LABELS, status_labels=STATUS_LABELS)
        self._activity.log("Exported broadcast history", "Broadcast History",
                           f"{len(records)} records to {path.name}")
        return path
