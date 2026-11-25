"""Versioned schema migrations (tracked with ``PRAGMA user_version``)."""
from __future__ import annotations

import logging

from .database import Database

log = logging.getLogger(__name__)

_TS = "TEXT NOT NULL DEFAULT (datetime('now','localtime'))"

SCHEMA_V1 = f"""
CREATE TABLE IF NOT EXISTS roles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    description   TEXT    NOT NULL DEFAULT '',
    permissions   TEXT    NOT NULL DEFAULT '[]',
    is_system     INTEGER NOT NULL DEFAULT 0,
    created_at    {_TS},
    updated_at    {_TS}
);

CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    full_name             TEXT    NOT NULL,
    email                 TEXT    NOT NULL DEFAULT '',
    password_hash         TEXT    NOT NULL,
    role_id               INTEGER NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    extra_permissions     TEXT    NOT NULL DEFAULT '[]',
    is_active             INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    must_change_password  INTEGER NOT NULL DEFAULT 0,
    failed_attempts       INTEGER NOT NULL DEFAULT 0,
    locked_until          TEXT,
    last_login_at         TEXT,
    created_at            {_TS},
    updated_at            {_TS}
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role_id);

CREATE TABLE IF NOT EXISTS audio_devices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    device_type         TEXT    NOT NULL CHECK (device_type IN
                            ('local_output','pa_amplifier','network_adapter','ip_speaker','simulated')),
    transport           TEXT    NOT NULL CHECK (transport IN ('local','simulated','rtp','sip','http')),
    system_device_name  TEXT    NOT NULL DEFAULT '',
    host_api            TEXT    NOT NULL DEFAULT '',
    channels            INTEGER NOT NULL DEFAULT 2 CHECK (channels BETWEEN 1 AND 64),
    address             TEXT    NOT NULL DEFAULT '',
    port                INTEGER NOT NULL DEFAULT 0,
    config              TEXT    NOT NULL DEFAULT '{{}}',
    is_enabled          INTEGER NOT NULL DEFAULT 1,
    status              TEXT    NOT NULL DEFAULT 'unknown',
    status_detail       TEXT    NOT NULL DEFAULT '',
    last_seen_at        TEXT,
    last_verified_at    TEXT,
    notes               TEXT    NOT NULL DEFAULT '',
    created_at          {_TS},
    updated_at          {_TS}
);

CREATE TABLE IF NOT EXISTS audio_zones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    description     TEXT    NOT NULL DEFAULT '',
    device_id       INTEGER REFERENCES audio_devices(id) ON DELETE SET NULL,
    output_channel  INTEGER NOT NULL DEFAULT 0 CHECK (output_channel >= 0),
    volume          INTEGER NOT NULL DEFAULT 80 CHECK (volume BETWEEN 0 AND 100),
    is_enabled      INTEGER NOT NULL DEFAULT 1,
    is_all_campus   INTEGER NOT NULL DEFAULT 0,
    color           TEXT    NOT NULL DEFAULT '#2563EB',
    map_x           REAL    NOT NULL DEFAULT -1,
    map_y           REAL    NOT NULL DEFAULT -1,
    map_w           REAL    NOT NULL DEFAULT 0,
    map_h           REAL    NOT NULL DEFAULT 0,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      {_TS},
    updated_at      {_TS}
);
CREATE INDEX IF NOT EXISTS idx_zones_device ON audio_zones(device_id);

CREATE TABLE IF NOT EXISTS audio_files (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    category           TEXT    NOT NULL DEFAULT 'General Audio',
    file_path          TEXT    NOT NULL,
    original_filename  TEXT    NOT NULL DEFAULT '',
    format             TEXT    NOT NULL DEFAULT '',
    duration_seconds   REAL    NOT NULL DEFAULT 0,
    sample_rate        INTEGER NOT NULL DEFAULT 0,
    channels           INTEGER NOT NULL DEFAULT 0,
    size_bytes         INTEGER NOT NULL DEFAULT 0,
    is_builtin         INTEGER NOT NULL DEFAULT 0,
    builtin_key        TEXT    UNIQUE,
    uploaded_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at         {_TS},
    updated_at         {_TS}
);
CREATE INDEX IF NOT EXISTS idx_audio_files_category ON audio_files(category);

CREATE TABLE IF NOT EXISTS announcements (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT    NOT NULL,
    message            TEXT    NOT NULL DEFAULT '',
    category           TEXT    NOT NULL,
    source_type        TEXT    NOT NULL CHECK (source_type IN ('tts','audio_file','recording')),
    audio_file_id      INTEGER REFERENCES audio_files(id) ON DELETE SET NULL,
    tts_voice          TEXT    NOT NULL DEFAULT '',
    tts_rate           INTEGER NOT NULL DEFAULT 0 CHECK (tts_rate BETWEEN -10 AND 10),
    tts_volume         INTEGER NOT NULL DEFAULT 100 CHECK (tts_volume BETWEEN 0 AND 100),
    play_chime         INTEGER NOT NULL DEFAULT 1,
    repeat_count       INTEGER NOT NULL DEFAULT 1 CHECK (repeat_count BETWEEN 1 AND 5),
    is_approved        INTEGER NOT NULL DEFAULT 1,
    created_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    last_broadcast_at  TEXT,
    broadcast_count    INTEGER NOT NULL DEFAULT 0,
    created_at         {_TS},
    updated_at         {_TS}
);

CREATE TABLE IF NOT EXISTS announcement_zones (
    announcement_id  INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
    zone_id          INTEGER NOT NULL REFERENCES audio_zones(id) ON DELETE CASCADE,
    PRIMARY KEY (announcement_id, zone_id)
);

CREATE TABLE IF NOT EXISTS alarm_types (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              TEXT    NOT NULL UNIQUE,
    name              TEXT    NOT NULL,
    description       TEXT    NOT NULL DEFAULT '',
    sound_source      TEXT    NOT NULL DEFAULT 'builtin' CHECK (sound_source IN ('builtin','audio_file')),
    builtin_pattern   TEXT    NOT NULL DEFAULT 'alarm_general',
    audio_file_id     INTEGER REFERENCES audio_files(id) ON DELETE SET NULL,
    voice_message     TEXT    NOT NULL DEFAULT '',
    duration_seconds  INTEGER NOT NULL DEFAULT 60 CHECK (duration_seconds >= 0),
    repeat_count      INTEGER NOT NULL DEFAULT 0 CHECK (repeat_count >= 0),
    color             TEXT    NOT NULL DEFAULT '#DC2626',
    icon              TEXT    NOT NULL DEFAULT 'siren',
    is_system         INTEGER NOT NULL DEFAULT 0,
    is_enabled        INTEGER NOT NULL DEFAULT 1,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        {_TS},
    updated_at        {_TS}
);

CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    content_type      TEXT    NOT NULL CHECK (content_type IN ('announcement','audio_file','alarm')),
    announcement_id   INTEGER REFERENCES announcements(id) ON DELETE SET NULL,
    audio_file_id     INTEGER REFERENCES audio_files(id) ON DELETE SET NULL,
    alarm_type_id     INTEGER REFERENCES alarm_types(id) ON DELETE SET NULL,
    start_date        TEXT    NOT NULL,
    time_of_day       TEXT    NOT NULL,
    recurrence        TEXT    NOT NULL CHECK (recurrence IN ('once','daily','weekdays','weekly','custom')),
    days_of_week      TEXT    NOT NULL DEFAULT '',
    interval_weeks    INTEGER NOT NULL DEFAULT 1 CHECK (interval_weeks >= 1),
    end_date          TEXT,
    duration_seconds  INTEGER NOT NULL DEFAULT 30,
    play_chime        INTEGER NOT NULL DEFAULT 0,
    is_enabled        INTEGER NOT NULL DEFAULT 1,
    last_run_at       TEXT,
    last_status       TEXT    NOT NULL DEFAULT '',
    notes             TEXT    NOT NULL DEFAULT '',
    created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at        {_TS},
    updated_at        {_TS}
);

CREATE TABLE IF NOT EXISTS schedule_zones (
    schedule_id  INTEGER NOT NULL REFERENCES scheduled_broadcasts(id) ON DELETE CASCADE,
    zone_id      INTEGER NOT NULL REFERENCES audio_zones(id) ON DELETE CASCADE,
    PRIMARY KEY (schedule_id, zone_id)
);

CREATE TABLE IF NOT EXISTS broadcast_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT    NOT NULL,
    broadcast_type     TEXT    NOT NULL,
    category           TEXT    NOT NULL DEFAULT '',
    priority           INTEGER NOT NULL DEFAULT 0,
    source             TEXT    NOT NULL DEFAULT 'manual',
    initiated_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    initiated_by_name  TEXT    NOT NULL DEFAULT '',
    zone_names         TEXT    NOT NULL DEFAULT '',
    zone_ids           TEXT    NOT NULL DEFAULT '[]',
    started_at         TEXT    NOT NULL,
    ended_at           TEXT,
    duration_seconds   REAL    NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'in_progress' CHECK (status IN
                           ('in_progress','completed','cancelled','interrupted','failed','missed')),
    status_detail      TEXT    NOT NULL DEFAULT '',
    ended_by_name      TEXT    NOT NULL DEFAULT '',
    reference_type     TEXT    NOT NULL DEFAULT '',
    reference_id       INTEGER,
    devices_summary    TEXT    NOT NULL DEFAULT '',
    created_at         {_TS}
);
CREATE INDEX IF NOT EXISTS idx_history_started ON broadcast_history(started_at);
CREATE INDEX IF NOT EXISTS idx_history_type ON broadcast_history(broadcast_type);
CREATE INDEX IF NOT EXISTS idx_history_user ON broadcast_history(initiated_by);

CREATE TABLE IF NOT EXISTS system_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  {_TS},
    updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username    TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    module      TEXT NOT NULL DEFAULT '',
    details     TEXT NOT NULL DEFAULT '',
    level       TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info','warning','critical')),
    created_at  {_TS}
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_logs(user_id);
"""

# v2: outro chimes for announcements and schedules; schedules can carry their own
# text-to-speech message or voice recording.  SQLite cannot alter a CHECK
# constraint, so scheduled_broadcasts is rebuilt (foreign keys are disabled by
# :func:`migrate` while this runs, which keeps schedule_zones intact).
SCHEMA_V2 = f"""
ALTER TABLE announcements ADD COLUMN play_outro_chime INTEGER NOT NULL DEFAULT 1;
UPDATE announcements SET play_outro_chime = play_chime;

CREATE TABLE scheduled_broadcasts_v2 (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    content_type      TEXT    NOT NULL CHECK (content_type IN
                          ('announcement','audio_file','alarm','tts','recording')),
    announcement_id   INTEGER REFERENCES announcements(id) ON DELETE SET NULL,
    audio_file_id     INTEGER REFERENCES audio_files(id) ON DELETE SET NULL,
    alarm_type_id     INTEGER REFERENCES alarm_types(id) ON DELETE SET NULL,
    tts_message       TEXT    NOT NULL DEFAULT '',
    tts_voice         TEXT    NOT NULL DEFAULT '',
    tts_rate          INTEGER NOT NULL DEFAULT 0 CHECK (tts_rate BETWEEN -10 AND 10),
    tts_volume        INTEGER NOT NULL DEFAULT 100 CHECK (tts_volume BETWEEN 0 AND 100),
    start_date        TEXT    NOT NULL,
    time_of_day       TEXT    NOT NULL,
    recurrence        TEXT    NOT NULL CHECK (recurrence IN ('once','daily','weekdays','weekly','custom')),
    days_of_week      TEXT    NOT NULL DEFAULT '',
    interval_weeks    INTEGER NOT NULL DEFAULT 1 CHECK (interval_weeks >= 1),
    end_date          TEXT,
    duration_seconds  INTEGER NOT NULL DEFAULT 30,
    play_chime        INTEGER NOT NULL DEFAULT 0,
    play_outro_chime  INTEGER NOT NULL DEFAULT 0,
    is_enabled        INTEGER NOT NULL DEFAULT 1,
    last_run_at       TEXT,
    last_status       TEXT    NOT NULL DEFAULT '',
    notes             TEXT    NOT NULL DEFAULT '',
    created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at        {_TS},
    updated_at        {_TS}
);
INSERT INTO scheduled_broadcasts_v2 (
    id, name, content_type, announcement_id, audio_file_id, alarm_type_id, start_date, time_of_day, recurrence,
    days_of_week, interval_weeks, end_date, duration_seconds, play_chime, play_outro_chime, is_enabled, last_run_at,
    last_status, notes, created_by, created_at, updated_at)
SELECT id, name, content_type, announcement_id, audio_file_id, alarm_type_id, start_date, time_of_day, recurrence,
    days_of_week, interval_weeks, end_date, duration_seconds, play_chime, play_chime, is_enabled, last_run_at,
    last_status, notes, created_by, created_at, updated_at
FROM scheduled_broadcasts;
DROP TABLE scheduled_broadcasts;
ALTER TABLE scheduled_broadcasts_v2 RENAME TO scheduled_broadcasts;

-- Outro chimes are now on by default (the old default was off).
DELETE FROM system_settings WHERE key = 'audio.chime_after';
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, SCHEMA_V1),
    (2, SCHEMA_V2),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def migrate(db: Database) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    current = db.user_version
    pending = [(version, script) for version, script in MIGRATIONS if version > current]
    if not pending:
        return current
    # Table rebuilds must not trigger ON DELETE actions; the pragma is a no-op inside a transaction.
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        for version, script in pending:
            try:
                db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
            except Exception:
                if db.conn.in_transaction:
                    db.conn.execute("ROLLBACK")
                raise
        problems = db.query("PRAGMA foreign_key_check")
        if problems:
            log.warning("Database has %s broken reference(s) after migration", len(problems))
    finally:
        db.execute("PRAGMA foreign_keys = ON")
    return db.user_version


EXPECTED_TABLES = (
    "users", "roles", "announcements", "alarm_types", "scheduled_broadcasts", "audio_zones",
    "audio_devices", "audio_files", "broadcast_history", "system_settings", "activity_logs",
    "announcement_zones", "schedule_zones",
)
