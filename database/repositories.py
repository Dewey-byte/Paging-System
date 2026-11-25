"""Repository classes: every SQL statement of the application lives here."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .database import Database, now_str
from .models import (ActivityLog, AlarmType, Announcement, AudioDevice, AudioFile, AudioZone, BroadcastRecord,
                     Role, ScheduledBroadcast, User)


def _json(value: Any) -> str:
    return json.dumps(value)


class _Repo:
    def __init__(self, db: Database):
        self.db = db

    def _set_links(self, table: str, owner_col: str, owner_id: int, zone_ids: Iterable[int]) -> None:
        self.db.execute(f"DELETE FROM {table} WHERE {owner_col} = ?", (owner_id,))
        unique = sorted({int(z) for z in zone_ids})
        if unique:
            self.db.executemany(f"INSERT INTO {table} ({owner_col}, zone_id) VALUES (?, ?)",
                                [(owner_id, z) for z in unique])

    def _links_for(self, table: str, owner_col: str, owner_ids: list[int]) -> dict[int, list[int]]:
        result: dict[int, list[int]] = defaultdict(list)
        if not owner_ids:
            return result
        marks = ",".join("?" for _ in owner_ids)
        for row in self.db.query(f"SELECT {owner_col}, zone_id FROM {table} WHERE {owner_col} IN ({marks})",
                                 owner_ids):
            result[row[0]].append(row[1])
        return result


# ------------------------------------------------------------------- roles/users
class RoleRepository(_Repo):
    _SELECT = ("SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.role_id = r.id) AS user_count "
               "FROM roles r")

    def all(self) -> list[Role]:
        return [Role.from_row(r) for r in self.db.query(self._SELECT + " ORDER BY r.id")]

    def get(self, role_id: int) -> Role | None:
        row = self.db.query_one(self._SELECT + " WHERE r.id = ?", (role_id,))
        return Role.from_row(row) if row else None

    def get_by_name(self, name: str) -> Role | None:
        row = self.db.query_one(self._SELECT + " WHERE r.name = ?", (name,))
        return Role.from_row(row) if row else None

    def create(self, name: str, description: str, permissions: list[str], is_system: bool = False) -> int:
        return self.db.insert("roles", {"name": name, "description": description,
                                        "permissions": _json(sorted(set(permissions))),
                                        "is_system": int(is_system)})

    def update(self, role_id: int, *, description: str | None = None, permissions: list[str] | None = None) -> None:
        data: dict[str, Any] = {"updated_at": now_str()}
        if description is not None:
            data["description"] = description
        if permissions is not None:
            data["permissions"] = _json(sorted(set(permissions)))
        self.db.update("roles", role_id, data)


class UserRepository(_Repo):
    _SELECT = ("SELECT u.*, r.name AS role_name, r.permissions AS role_permissions "
               "FROM users u JOIN roles r ON r.id = u.role_id")

    def all(self, include_inactive: bool = True) -> list[User]:
        where = "" if include_inactive else " WHERE u.is_active = 1"
        return [User.from_row(r) for r in self.db.query(self._SELECT + where + " ORDER BY u.full_name COLLATE NOCASE")]

    def get(self, user_id: int) -> User | None:
        row = self.db.query_one(self._SELECT + " WHERE u.id = ?", (user_id,))
        return User.from_row(row) if row else None

    def get_by_username(self, username: str) -> User | None:
        row = self.db.query_one(self._SELECT + " WHERE u.username = ? COLLATE NOCASE", (username.strip(),))
        return User.from_row(row) if row else None

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM users", default=0))

    def count_active_with_role(self, role_name: str) -> int:
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM users u JOIN roles r ON r.id = u.role_id WHERE u.is_active = 1 AND r.name = ?",
            (role_name,), default=0))

    def create(self, *, username: str, full_name: str, email: str, password_hash: str, role_id: int,
               extra_permissions: list[str] | None = None, is_active: bool = True,
               must_change_password: bool = False) -> int:
        return self.db.insert("users", {
            "username": username.strip(), "full_name": full_name.strip(), "email": email.strip(),
            "password_hash": password_hash, "role_id": role_id,
            "extra_permissions": _json(sorted(set(extra_permissions or []))),
            "is_active": int(is_active), "must_change_password": int(must_change_password),
        })

    def update(self, user_id: int, **fields: Any) -> None:
        if "extra_permissions" in fields:
            fields["extra_permissions"] = _json(sorted(set(fields["extra_permissions"])))
        for key in ("is_active", "must_change_password"):
            if key in fields:
                fields[key] = int(bool(fields[key]))
        fields["updated_at"] = now_str()
        self.db.update("users", user_id, fields)

    def delete(self, user_id: int) -> None:
        self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------------------------------------------------------------------- settings
class SettingsRepository(_Repo):
    def all(self) -> dict[str, Any]:
        result = {}
        for row in self.db.query("SELECT key, value FROM system_settings"):
            try:
                result[row["key"]] = json.loads(row["value"])
            except ValueError:
                result[row["key"]] = row["value"]
        return result

    def set(self, key: str, value: Any, user_id: int | None = None) -> None:
        self.db.execute(
            "INSERT INTO system_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
            "updated_by = excluded.updated_by",
            (key, _json(value), now_str(), user_id))


# -------------------------------------------------------------------- activity
class ActivityRepository(_Repo):
    def add(self, *, user_id: int | None, username: str, action: str, module: str, details: str,
            level: str) -> int:
        return self.db.insert("activity_logs", {"user_id": user_id, "username": username, "action": action,
                                                "module": module, "details": details, "level": level,
                                                "created_at": now_str()})

    def search(self, *, text: str = "", level: str = "", module: str = "", date_from: date | None = None,
               date_to: date | None = None, user_id: int | None = None, limit: int = 2000) -> list[ActivityLog]:
        sql, params = ["SELECT * FROM activity_logs WHERE 1=1"], []
        if text:
            sql.append("AND (action LIKE ? OR details LIKE ? OR username LIKE ? OR module LIKE ?)")
            params += [f"%{text}%"] * 4
        if level:
            sql.append("AND level = ?"); params.append(level)
        if module:
            sql.append("AND module = ?"); params.append(module)
        if user_id:
            sql.append("AND user_id = ?"); params.append(user_id)
        if date_from:
            sql.append("AND created_at >= ?"); params.append(f"{date_from.isoformat()} 00:00:00")
        if date_to:
            sql.append("AND created_at <= ?"); params.append(f"{date_to.isoformat()} 23:59:59")
        sql.append("ORDER BY id DESC LIMIT ?"); params.append(limit)
        return [ActivityLog.from_row(r) for r in self.db.query(" ".join(sql), params)]

    def modules(self) -> list[str]:
        return [r[0] for r in self.db.query("SELECT DISTINCT module FROM activity_logs WHERE module != '' ORDER BY 1")]


# ----------------------------------------------------------------------- devices
class DeviceRepository(_Repo):
    def all(self) -> list[AudioDevice]:
        return [AudioDevice.from_row(r) for r in self.db.query("SELECT * FROM audio_devices ORDER BY id")]

    def get(self, device_id: int) -> AudioDevice | None:
        row = self.db.query_one("SELECT * FROM audio_devices WHERE id = ?", (device_id,))
        return AudioDevice.from_row(row) if row else None

    def get_by_name(self, name: str) -> AudioDevice | None:
        row = self.db.query_one("SELECT * FROM audio_devices WHERE name = ? COLLATE NOCASE", (name,))
        return AudioDevice.from_row(row) if row else None

    def create(self, **data: Any) -> int:
        if "config" in data:
            data["config"] = _json(data["config"])
        if "is_enabled" in data:
            data["is_enabled"] = int(bool(data["is_enabled"]))
        return self.db.insert("audio_devices", data)

    def update(self, device_id: int, **data: Any) -> None:
        if "config" in data:
            data["config"] = _json(data["config"])
        if "is_enabled" in data:
            data["is_enabled"] = int(bool(data["is_enabled"]))
        data["updated_at"] = now_str()
        self.db.update("audio_devices", device_id, data)

    def set_status(self, device_id: int, status: str, detail: str, seen: bool, verified: bool = False) -> None:
        data: dict[str, Any] = {"status": status, "status_detail": detail}
        if seen:
            data["last_seen_at"] = now_str()
        if verified:
            data["last_verified_at"] = now_str()
        self.db.update("audio_devices", device_id, data)

    def delete(self, device_id: int) -> None:
        self.db.execute("DELETE FROM audio_devices WHERE id = ?", (device_id,))

    def zone_count(self, device_id: int) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM audio_zones WHERE device_id = ?", (device_id,), default=0))


# ------------------------------------------------------------------------- zones
class ZoneRepository(_Repo):
    def all(self, include_disabled: bool = True) -> list[AudioZone]:
        where = "" if include_disabled else " WHERE is_enabled = 1"
        return [AudioZone.from_row(r) for r in
                self.db.query(f"SELECT * FROM audio_zones{where} ORDER BY is_all_campus DESC, sort_order, name")]

    def get(self, zone_id: int) -> AudioZone | None:
        row = self.db.query_one("SELECT * FROM audio_zones WHERE id = ?", (zone_id,))
        return AudioZone.from_row(row) if row else None

    def get_by_name(self, name: str) -> AudioZone | None:
        row = self.db.query_one("SELECT * FROM audio_zones WHERE name = ? COLLATE NOCASE", (name,))
        return AudioZone.from_row(row) if row else None

    def create(self, **data: Any) -> int:
        for key in ("is_enabled", "is_all_campus"):
            if key in data:
                data[key] = int(bool(data[key]))
        if "sort_order" not in data:
            data["sort_order"] = int(self.db.scalar("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM audio_zones",
                                                    default=1))
        return self.db.insert("audio_zones", data)

    def update(self, zone_id: int, **data: Any) -> None:
        for key in ("is_enabled", "is_all_campus"):
            if key in data:
                data[key] = int(bool(data[key]))
        data["updated_at"] = now_str()
        self.db.update("audio_zones", zone_id, data)

    def delete(self, zone_id: int) -> None:
        self.db.execute("DELETE FROM audio_zones WHERE id = ?", (zone_id,))


# ------------------------------------------------------------------- audio files
class AudioFileRepository(_Repo):
    _SELECT = ("SELECT f.*, u.full_name AS uploaded_by_name FROM audio_files f "
               "LEFT JOIN users u ON u.id = f.uploaded_by")

    def all(self, category: str | None = None, search: str = "") -> list[AudioFile]:
        sql, params = [self._SELECT + " WHERE 1=1"], []
        if category:
            sql.append("AND f.category = ?"); params.append(category)
        if search:
            sql.append("AND (f.name LIKE ? OR f.original_filename LIKE ?)"); params += [f"%{search}%"] * 2
        sql.append("ORDER BY f.is_builtin ASC, f.name COLLATE NOCASE")
        return [AudioFile.from_row(r) for r in self.db.query(" ".join(sql), params)]

    def get(self, file_id: int | None) -> AudioFile | None:
        if not file_id:
            return None
        row = self.db.query_one(self._SELECT + " WHERE f.id = ?", (file_id,))
        return AudioFile.from_row(row) if row else None

    def get_by_builtin_key(self, key: str) -> AudioFile | None:
        row = self.db.query_one(self._SELECT + " WHERE f.builtin_key = ?", (key,))
        return AudioFile.from_row(row) if row else None

    def create(self, **data: Any) -> int:
        if "is_builtin" in data:
            data["is_builtin"] = int(bool(data["is_builtin"]))
        return self.db.insert("audio_files", data)

    def update(self, file_id: int, **data: Any) -> None:
        data["updated_at"] = now_str()
        self.db.update("audio_files", file_id, data)

    def delete(self, file_id: int) -> None:
        self.db.execute("DELETE FROM audio_files WHERE id = ?", (file_id,))

    def references(self, file_id: int) -> dict[str, list[str]]:
        refs = {
            "announcements": [r[0] for r in self.db.query("SELECT title FROM announcements WHERE audio_file_id = ?",
                                                          (file_id,))],
            "alarms": [r[0] for r in self.db.query(
                "SELECT name FROM alarm_types WHERE audio_file_id = ? AND sound_source = 'audio_file'", (file_id,))],
            "schedules": [r[0] for r in self.db.query(
                "SELECT name FROM scheduled_broadcasts WHERE audio_file_id = ? "
                "AND content_type IN ('audio_file', 'recording')",
                (file_id,))],
        }
        return {k: v for k, v in refs.items() if v}

    def category_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.query("SELECT category, COUNT(*) FROM audio_files GROUP BY category")}


# ----------------------------------------------------------------- announcements
class AnnouncementRepository(_Repo):
    _SELECT = ("SELECT a.*, u.full_name AS created_by_name, f.name AS audio_file_name FROM announcements a "
               "LEFT JOIN users u ON u.id = a.created_by LEFT JOIN audio_files f ON f.id = a.audio_file_id")

    def _attach(self, items: list[Announcement]) -> list[Announcement]:
        links = self._links_for("announcement_zones", "announcement_id", [a.id for a in items])
        for item in items:
            item.zone_ids = sorted(links.get(item.id, []))
        return items

    def all(self, search: str = "", category: str = "", approved_only: bool = False) -> list[Announcement]:
        sql, params = [self._SELECT + " WHERE 1=1"], []
        if search:
            sql.append("AND (a.title LIKE ? OR a.message LIKE ?)"); params += [f"%{search}%"] * 2
        if category:
            sql.append("AND a.category = ?"); params.append(category)
        if approved_only:
            sql.append("AND a.is_approved = 1")
        sql.append("ORDER BY a.updated_at DESC, a.id DESC")
        return self._attach([Announcement.from_row(r) for r in self.db.query(" ".join(sql), params)])

    def get(self, ann_id: int | None) -> Announcement | None:
        if not ann_id:
            return None
        row = self.db.query_one(self._SELECT + " WHERE a.id = ?", (ann_id,))
        return self._attach([Announcement.from_row(row)])[0] if row else None

    def create(self, data: dict[str, Any], zone_ids: Iterable[int]) -> int:
        with self.db.transaction():
            new_id = self.db.insert("announcements", data)
            self._set_links("announcement_zones", "announcement_id", new_id, zone_ids)
        return new_id

    def update(self, ann_id: int, data: dict[str, Any], zone_ids: Iterable[int] | None = None) -> None:
        with self.db.transaction():
            data["updated_at"] = now_str()
            self.db.update("announcements", ann_id, data)
            if zone_ids is not None:
                self._set_links("announcement_zones", "announcement_id", ann_id, zone_ids)

    def mark_broadcast(self, ann_id: int) -> None:
        self.db.execute("UPDATE announcements SET last_broadcast_at = ?, broadcast_count = broadcast_count + 1 "
                        "WHERE id = ?", (now_str(), ann_id))

    def delete(self, ann_id: int) -> None:
        self.db.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))

    def schedule_references(self, ann_id: int) -> list[str]:
        return [r[0] for r in self.db.query(
            "SELECT name FROM scheduled_broadcasts WHERE announcement_id = ? AND content_type = 'announcement'",
            (ann_id,))]


# ------------------------------------------------------------------------ alarms
class AlarmTypeRepository(_Repo):
    def all(self, include_disabled: bool = True) -> list[AlarmType]:
        where = "" if include_disabled else " WHERE is_enabled = 1"
        return [AlarmType.from_row(r) for r in
                self.db.query(f"SELECT * FROM alarm_types{where} ORDER BY sort_order, id")]

    def get(self, alarm_id: int | None) -> AlarmType | None:
        if not alarm_id:
            return None
        row = self.db.query_one("SELECT * FROM alarm_types WHERE id = ?", (alarm_id,))
        return AlarmType.from_row(row) if row else None

    def get_by_code(self, code: str) -> AlarmType | None:
        row = self.db.query_one("SELECT * FROM alarm_types WHERE code = ?", (code,))
        return AlarmType.from_row(row) if row else None

    def create(self, **data: Any) -> int:
        for key in ("is_system", "is_enabled"):
            if key in data:
                data[key] = int(bool(data[key]))
        return self.db.insert("alarm_types", data)

    def update(self, alarm_id: int, **data: Any) -> None:
        if "is_enabled" in data:
            data["is_enabled"] = int(bool(data["is_enabled"]))
        data["updated_at"] = now_str()
        self.db.update("alarm_types", alarm_id, data)

    def delete(self, alarm_id: int) -> None:
        self.db.execute("DELETE FROM alarm_types WHERE id = ?", (alarm_id,))


# --------------------------------------------------------------------- schedules
class ScheduleRepository(_Repo):
    _SELECT = (
        "SELECT s.*, CASE s.content_type "
        "  WHEN 'announcement' THEN (SELECT title FROM announcements WHERE id = s.announcement_id) "
        "  WHEN 'audio_file' THEN (SELECT name FROM audio_files WHERE id = s.audio_file_id) "
        "  WHEN 'recording' THEN (SELECT name FROM audio_files WHERE id = s.audio_file_id) "
        "  WHEN 'tts' THEN NULLIF(SUBSTR(TRIM(s.tts_message), 1, 90), '') "
        "  WHEN 'alarm' THEN (SELECT name FROM alarm_types WHERE id = s.alarm_type_id) END AS content_name "
        "FROM scheduled_broadcasts s")

    def _attach(self, items: list[ScheduledBroadcast]) -> list[ScheduledBroadcast]:
        links = self._links_for("schedule_zones", "schedule_id", [s.id for s in items])
        for item in items:
            item.zone_ids = sorted(links.get(item.id, []))
        return items

    def all(self) -> list[ScheduledBroadcast]:
        return self._attach([ScheduledBroadcast.from_row(r)
                             for r in self.db.query(self._SELECT + " ORDER BY s.time_of_day, s.name")])

    def get(self, schedule_id: int) -> ScheduledBroadcast | None:
        row = self.db.query_one(self._SELECT + " WHERE s.id = ?", (schedule_id,))
        return self._attach([ScheduledBroadcast.from_row(row)])[0] if row else None

    @staticmethod
    def _normalise(data: dict[str, Any]) -> dict[str, Any]:
        if "days_of_week" in data and not isinstance(data["days_of_week"], str):
            data["days_of_week"] = ",".join(str(d) for d in sorted(set(data["days_of_week"])))
        for key in ("play_chime", "play_outro_chime", "is_enabled"):
            if key in data:
                data[key] = int(bool(data[key]))
        return data

    def create(self, data: dict[str, Any], zone_ids: Iterable[int]) -> int:
        with self.db.transaction():
            new_id = self.db.insert("scheduled_broadcasts", self._normalise(data))
            self._set_links("schedule_zones", "schedule_id", new_id, zone_ids)
        return new_id

    def update(self, schedule_id: int, data: dict[str, Any], zone_ids: Iterable[int] | None = None) -> None:
        with self.db.transaction():
            data = self._normalise(data)
            data["updated_at"] = now_str()
            self.db.update("scheduled_broadcasts", schedule_id, data)
            if zone_ids is not None:
                self._set_links("schedule_zones", "schedule_id", schedule_id, zone_ids)

    def mark_run(self, schedule_id: int, occurrence: datetime, status: str) -> None:
        self.db.execute("UPDATE scheduled_broadcasts SET last_run_at = ?, last_status = ? WHERE id = ?",
                        (occurrence.strftime("%Y-%m-%d %H:%M:%S"), status, schedule_id))

    def set_status(self, schedule_id: int, status: str) -> None:
        self.db.execute("UPDATE scheduled_broadcasts SET last_status = ? WHERE id = ?", (status, schedule_id))

    def delete(self, schedule_id: int) -> None:
        self.db.execute("DELETE FROM scheduled_broadcasts WHERE id = ?", (schedule_id,))


# ----------------------------------------------------------------------- history
class HistoryRepository(_Repo):
    def create(self, **data: Any) -> int:
        if "zone_ids" in data and not isinstance(data["zone_ids"], str):
            data["zone_ids"] = _json(list(data["zone_ids"]))
        return self.db.insert("broadcast_history", data)

    def finish(self, record_id: int, *, ended_at: str, duration: float, status: str, detail: str = "",
               ended_by: str = "") -> None:
        self.db.update("broadcast_history", record_id, {
            "ended_at": ended_at, "duration_seconds": round(duration, 1), "status": status,
            "status_detail": detail, "ended_by_name": ended_by})

    def update(self, record_id: int, **data: Any) -> None:
        self.db.update("broadcast_history", record_id, data)

    def get(self, record_id: int) -> BroadcastRecord | None:
        row = self.db.query_one("SELECT * FROM broadcast_history WHERE id = ?", (record_id,))
        return BroadcastRecord.from_row(row) if row else None

    def search(self, *, text: str = "", date_from: date | None = None, date_to: date | None = None,
               broadcast_type: str = "", category: str = "", user_name: str = "", status: str = "",
               limit: int = 5000) -> list[BroadcastRecord]:
        sql, params = ["SELECT * FROM broadcast_history WHERE 1=1"], []
        if text:
            sql.append("AND (title LIKE ? OR zone_names LIKE ? OR status_detail LIKE ? OR initiated_by_name LIKE ?)")
            params += [f"%{text}%"] * 4
        if date_from:
            sql.append("AND started_at >= ?"); params.append(f"{date_from.isoformat()} 00:00:00")
        if date_to:
            sql.append("AND started_at <= ?"); params.append(f"{date_to.isoformat()} 23:59:59")
        if broadcast_type:
            sql.append("AND broadcast_type = ?"); params.append(broadcast_type)
        if category:
            sql.append("AND category = ?"); params.append(category)
        if user_name:
            sql.append("AND initiated_by_name = ?"); params.append(user_name)
        if status:
            sql.append("AND status = ?"); params.append(status)
        sql.append("ORDER BY started_at DESC, id DESC LIMIT ?"); params.append(limit)
        return [BroadcastRecord.from_row(r) for r in self.db.query(" ".join(sql), params)]

    def recent(self, limit: int = 10) -> list[BroadcastRecord]:
        return [BroadcastRecord.from_row(r) for r in
                self.db.query("SELECT * FROM broadcast_history ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))]

    def stats_between(self, start: datetime, end: datetime) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT COUNT(*) AS total, "
            "SUM(status = 'completed') AS completed, SUM(status = 'cancelled') AS cancelled, "
            "SUM(status = 'interrupted') AS interrupted, SUM(status = 'failed') AS failed, "
            "SUM(status = 'missed') AS missed, SUM(status = 'in_progress') AS in_progress, "
            "SUM(broadcast_type = 'alarm') AS alarms, COALESCE(SUM(duration_seconds), 0) AS airtime "
            "FROM broadcast_history WHERE started_at >= ? AND started_at < ?",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")))
        return {k: (row[k] or 0) for k in row.keys()} if row else {}

    def daily_counts(self, days: int) -> list[tuple[date, int]]:
        start = date.today() - timedelta(days=days - 1)
        rows = {r[0]: r[1] for r in self.db.query(
            "SELECT substr(started_at, 1, 10) AS d, COUNT(*) FROM broadcast_history "
            "WHERE started_at >= ? AND status != 'missed' GROUP BY d", (f"{start.isoformat()} 00:00:00",))}
        return [(start + timedelta(days=i), int(rows.get((start + timedelta(days=i)).isoformat(), 0)))
                for i in range(days)]

    def close_stale(self, detail: str) -> int:
        """Mark records left 'in_progress' (e.g. after a crash) as interrupted."""
        cur = self.db.execute(
            "UPDATE broadcast_history SET status = 'interrupted', status_detail = ?, "
            "ended_at = COALESCE(ended_at, started_at) WHERE status = 'in_progress'", (detail,))
        return cur.rowcount

    def distinct(self, column: str) -> list[str]:
        if column not in {"initiated_by_name", "category", "broadcast_type"}:
            raise ValueError(column)
        return [r[0] for r in self.db.query(
            f"SELECT DISTINCT {column} FROM broadcast_history WHERE {column} != '' ORDER BY 1")]


class Repositories:
    """Convenience container giving managers access to every repository."""

    def __init__(self, db: Database):
        self.db = db
        self.roles = RoleRepository(db)
        self.users = UserRepository(db)
        self.settings = SettingsRepository(db)
        self.activity = ActivityRepository(db)
        self.devices = DeviceRepository(db)
        self.zones = ZoneRepository(db)
        self.audio_files = AudioFileRepository(db)
        self.announcements = AnnouncementRepository(db)
        self.alarms = AlarmTypeRepository(db)
        self.schedules = ScheduleRepository(db)
        self.history = HistoryRepository(db)
