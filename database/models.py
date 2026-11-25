"""Typed records returned by the repositories."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from sqlite3 import Row
from typing import Any

from core.paths import resolve_stored_path


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value or "[]")
        return list(data) if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _int_list(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(part) for part in str(text).split(",") if part.strip().lstrip("-").isdigit()]


def _get(row: Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


@dataclass
class Role:
    id: int
    name: str
    description: str
    permissions: list[str]
    is_system: bool
    created_at: str = ""
    updated_at: str = ""
    user_count: int = 0

    @classmethod
    def from_row(cls, r: Row) -> "Role":
        return cls(r["id"], r["name"], r["description"], _list(r["permissions"]), bool(r["is_system"]),
                   r["created_at"], r["updated_at"], int(_get(r, "user_count", 0) or 0))


@dataclass
class User:
    id: int
    username: str
    full_name: str
    email: str
    role_id: int
    role_name: str
    role_permissions: list[str]
    extra_permissions: list[str]
    is_active: bool
    must_change_password: bool
    failed_attempts: int
    locked_until: str | None
    last_login_at: str | None
    created_at: str
    updated_at: str
    password_hash: str = field(default="", repr=False)

    @classmethod
    def from_row(cls, r: Row) -> "User":
        return cls(
            id=r["id"], username=r["username"], full_name=r["full_name"], email=r["email"],
            role_id=r["role_id"], role_name=_get(r, "role_name", ""),
            role_permissions=_list(_get(r, "role_permissions", "[]")),
            extra_permissions=_list(r["extra_permissions"]), is_active=bool(r["is_active"]),
            must_change_password=bool(r["must_change_password"]), failed_attempts=r["failed_attempts"],
            locked_until=r["locked_until"], last_login_at=r["last_login_at"], created_at=r["created_at"],
            updated_at=r["updated_at"], password_hash=r["password_hash"],
        )

    @property
    def permissions(self) -> set[str]:
        return set(self.role_permissions) | set(self.extra_permissions)

    def has(self, permission: str) -> bool:
        return self.is_active and permission in self.permissions

    @property
    def initials(self) -> str:
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return self.username[:2].upper()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    @property
    def display_name(self) -> str:
        return self.full_name or self.username


@dataclass
class AudioDevice:
    id: int
    name: str
    device_type: str
    transport: str
    system_device_name: str
    host_api: str
    channels: int
    address: str
    port: int
    config: dict
    is_enabled: bool
    status: str
    status_detail: str
    last_seen_at: str | None
    last_verified_at: str | None
    notes: str
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, r: Row) -> "AudioDevice":
        return cls(r["id"], r["name"], r["device_type"], r["transport"], r["system_device_name"], r["host_api"],
                   r["channels"], r["address"], r["port"], _dict(r["config"]), bool(r["is_enabled"]),
                   r["status"], r["status_detail"], r["last_seen_at"], r["last_verified_at"], r["notes"],
                   r["created_at"], r["updated_at"])

    @property
    def is_simulated(self) -> bool:
        return self.transport == "simulated"


@dataclass
class AudioZone:
    id: int
    name: str
    description: str
    device_id: int | None
    output_channel: int
    volume: int
    is_enabled: bool
    is_all_campus: bool
    color: str
    map_x: float
    map_y: float
    map_w: float
    map_h: float
    sort_order: int
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, r: Row) -> "AudioZone":
        return cls(r["id"], r["name"], r["description"], r["device_id"], r["output_channel"], r["volume"],
                   bool(r["is_enabled"]), bool(r["is_all_campus"]), r["color"], r["map_x"], r["map_y"],
                   r["map_w"], r["map_h"], r["sort_order"], r["created_at"], r["updated_at"])

    @property
    def has_map_position(self) -> bool:
        return self.map_x >= 0 and self.map_y >= 0 and self.map_w > 0 and self.map_h > 0


@dataclass
class AudioFile:
    id: int
    name: str
    category: str
    file_path: str
    original_filename: str
    format: str
    duration_seconds: float
    sample_rate: int
    channels: int
    size_bytes: int
    is_builtin: bool
    builtin_key: str | None
    uploaded_by: int | None
    created_at: str = ""
    updated_at: str = ""
    uploaded_by_name: str = ""

    @classmethod
    def from_row(cls, r: Row) -> "AudioFile":
        return cls(r["id"], r["name"], r["category"], r["file_path"], r["original_filename"], r["format"],
                   r["duration_seconds"], r["sample_rate"], r["channels"], r["size_bytes"], bool(r["is_builtin"]),
                   r["builtin_key"], r["uploaded_by"], r["created_at"], r["updated_at"],
                   _get(r, "uploaded_by_name", "") or "")

    @property
    def abs_path(self):
        return resolve_stored_path(self.file_path)

    @property
    def exists(self) -> bool:
        return self.abs_path.exists()


@dataclass
class Announcement:
    id: int
    title: str
    message: str
    category: str
    source_type: str
    audio_file_id: int | None
    tts_voice: str
    tts_rate: int
    tts_volume: int
    play_chime: bool
    repeat_count: int
    is_approved: bool
    created_by: int | None
    updated_by: int | None
    last_broadcast_at: str | None
    broadcast_count: int
    created_at: str = ""
    updated_at: str = ""
    created_by_name: str = ""
    audio_file_name: str = ""
    zone_ids: list[int] = field(default_factory=list)
    play_outro_chime: bool = True

    @classmethod
    def from_row(cls, r: Row) -> "Announcement":
        return cls(r["id"], r["title"], r["message"], r["category"], r["source_type"], r["audio_file_id"],
                   r["tts_voice"], r["tts_rate"], r["tts_volume"], bool(r["play_chime"]), r["repeat_count"],
                   bool(r["is_approved"]), r["created_by"], r["updated_by"], r["last_broadcast_at"],
                   r["broadcast_count"], r["created_at"], r["updated_at"],
                   _get(r, "created_by_name", "") or "", _get(r, "audio_file_name", "") or "",
                   play_outro_chime=bool(_get(r, "play_outro_chime", 1)))


@dataclass
class AlarmType:
    id: int
    code: str
    name: str
    description: str
    sound_source: str
    builtin_pattern: str
    audio_file_id: int | None
    voice_message: str
    duration_seconds: int
    repeat_count: int
    color: str
    icon: str
    is_system: bool
    is_enabled: bool
    sort_order: int
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, r: Row) -> "AlarmType":
        return cls(r["id"], r["code"], r["name"], r["description"], r["sound_source"], r["builtin_pattern"],
                   r["audio_file_id"], r["voice_message"], r["duration_seconds"], r["repeat_count"], r["color"],
                   r["icon"], bool(r["is_system"]), bool(r["is_enabled"]), r["sort_order"], r["created_at"],
                   r["updated_at"])


@dataclass
class ScheduledBroadcast:
    id: int
    name: str
    content_type: str
    announcement_id: int | None
    audio_file_id: int | None
    alarm_type_id: int | None
    start_date: str
    time_of_day: str
    recurrence: str
    days_of_week: list[int]
    interval_weeks: int
    end_date: str | None
    duration_seconds: int
    play_chime: bool
    is_enabled: bool
    last_run_at: str | None
    last_status: str
    notes: str
    created_by: int | None
    created_at: str = ""
    updated_at: str = ""
    content_name: str = ""
    zone_ids: list[int] = field(default_factory=list)
    play_outro_chime: bool = False
    tts_message: str = ""           # content_type 'tts': the text spoken by the schedule itself
    tts_voice: str = ""
    tts_rate: int = 0
    tts_volume: int = 100

    @classmethod
    def from_row(cls, r: Row) -> "ScheduledBroadcast":
        return cls(r["id"], r["name"], r["content_type"], r["announcement_id"], r["audio_file_id"],
                   r["alarm_type_id"], r["start_date"], r["time_of_day"], r["recurrence"],
                   _int_list(r["days_of_week"]), r["interval_weeks"], r["end_date"], r["duration_seconds"],
                   bool(r["play_chime"]), bool(r["is_enabled"]), r["last_run_at"], r["last_status"], r["notes"],
                   r["created_by"], r["created_at"], r["updated_at"], _get(r, "content_name", "") or "",
                   play_outro_chime=bool(_get(r, "play_outro_chime", 0)),
                   tts_message=_get(r, "tts_message", "") or "", tts_voice=_get(r, "tts_voice", "") or "",
                   tts_rate=int(_get(r, "tts_rate", 0) or 0), tts_volume=int(_get(r, "tts_volume", 100) or 0))

    @property
    def content_id(self) -> int | None:
        """Linked record id (the audio file for recordings); None for text-to-speech schedules."""
        return {"announcement": self.announcement_id, "audio_file": self.audio_file_id,
                "recording": self.audio_file_id, "alarm": self.alarm_type_id}.get(self.content_type)

    @property
    def has_content(self) -> bool:
        if self.content_type == "tts":
            return bool(self.tts_message.strip())
        return bool(self.content_id)


@dataclass
class BroadcastRecord:
    id: int
    title: str
    broadcast_type: str
    category: str
    priority: int
    source: str
    initiated_by: int | None
    initiated_by_name: str
    zone_names: str
    zone_ids: list[int]
    started_at: str
    ended_at: str | None
    duration_seconds: float
    status: str
    status_detail: str
    ended_by_name: str
    reference_type: str
    reference_id: int | None
    devices_summary: str
    created_at: str = ""

    @classmethod
    def from_row(cls, r: Row) -> "BroadcastRecord":
        return cls(r["id"], r["title"], r["broadcast_type"], r["category"], r["priority"], r["source"],
                   r["initiated_by"], r["initiated_by_name"], r["zone_names"], _list(r["zone_ids"]),
                   r["started_at"], r["ended_at"], r["duration_seconds"], r["status"], r["status_detail"],
                   r["ended_by_name"], r["reference_type"], r["reference_id"], r["devices_summary"],
                   r["created_at"])


@dataclass
class ActivityLog:
    id: int
    user_id: int | None
    username: str
    action: str
    module: str
    details: str
    level: str
    created_at: str

    @classmethod
    def from_row(cls, r: Row) -> "ActivityLog":
        return cls(r["id"], r["user_id"], r["username"], r["action"], r["module"], r["details"], r["level"],
                   r["created_at"])
