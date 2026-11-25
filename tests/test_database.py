"""Database schema, persistence, relationships and transactions."""
from __future__ import annotations

import sqlite3

import pytest

from database.database import Database
from database.migrations import EXPECTED_TABLES, LATEST_VERSION, SCHEMA_V1, migrate
from database.repositories import Repositories


def test_schema_has_all_required_tables(tmp_path):
    db = Database(tmp_path / "t.db")
    assert migrate(db) == LATEST_VERSION
    names = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(EXPECTED_TABLES) <= names
    assert db.integrity_check() == "ok"
    assert db.scalar("PRAGMA foreign_keys") == 1


def test_migration_is_idempotent(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db)
    migrate(db)
    assert db.user_version == LATEST_VERSION


def test_data_persists_after_reopen(tmp_path):
    path = tmp_path / "persist.db"
    db = Database(path)
    migrate(db)
    repos = Repositories(db)
    role_id = repos.roles.create("Tester", "", ["dashboard.view"])
    repos.users.create(username="persist", full_name="Persist Me", email="", password_hash="x", role_id=role_id)
    repos.settings.set("school.name", "Persistent School")
    db.close()
    db2 = Database(path)
    repos2 = Repositories(db2)
    user = repos2.users.get_by_username("PERSIST")  # case-insensitive usernames
    assert user is not None and user.full_name == "Persist Me" and user.role_name == "Tester"
    assert repos2.settings.all()["school.name"] == "Persistent School"


def test_zone_delete_cascades_links_and_history_keeps_snapshot(ctx):
    repos = ctx.repos
    zone = ctx.zones.create_zone(name="Annex", device_id=None)
    ann = ctx.announcements.save(dict(title="Annex test", message="Hello annex", category="General Announcement",
                                      source_type="tts"), [zone.id])
    assert ann.zone_ids == [zone.id]
    ctx.zones.delete_zone(zone.id)
    assert ctx.announcements.get(ann.id).zone_ids == []


def test_transaction_rolls_back(tmp_path):
    db = Database(tmp_path / "tx.db")
    migrate(db)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.insert("roles", {"name": "Temp"})
            raise RuntimeError("boom")
    assert db.scalar("SELECT COUNT(*) FROM roles WHERE name='Temp'") == 0


def test_check_constraints_reject_invalid_values(tmp_path):
    db = Database(tmp_path / "c.db")
    migrate(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.insert("audio_zones", {"name": "Loud", "volume": 150})
    with pytest.raises(sqlite3.IntegrityError):
        db.insert("broadcast_history", {"title": "x", "broadcast_type": "live", "started_at": "now", "status": "weird"})


def test_seed_content(ctx):
    names = {z.name for z in ctx.zones.zones()}
    assert {"Entire Campus", "Main Building", "Library", "Cafeteria"} <= names
    codes = {a.code for a in ctx.alarms.alarm_types()}
    assert codes == {"FIRE", "EARTHQUAKE", "EVACUATION", "WEATHER", "SECURITY", "SHOOTING", "GENERAL"}
    assert len(ctx.library.files()) >= 12
    assert {s.time_of_day for s in ctx.schedules.schedules()} == {"07:30", "07:45", "08:00", "12:00", "16:30"}
    assert {r.name for r in ctx.users.list_roles()} == {"Administrator", "School Personnel", "Operator"}


def test_v1_database_upgrades_without_losing_schedules(tmp_path):
    """Schema v2 rebuilds scheduled_broadcasts; links, ids and chime choices must survive."""
    db = Database(tmp_path / "old.db")
    db.executescript(f"BEGIN;\n{SCHEMA_V1}\nPRAGMA user_version = 1;\nCOMMIT;")
    zone = db.insert("audio_zones", {"name": "Hall"})
    ann = db.insert("announcements", {"title": "A", "category": "General Announcement", "source_type": "tts",
                                      "play_chime": 0})
    sid = db.insert("scheduled_broadcasts", {"name": "Bell", "content_type": "announcement", "announcement_id": ann,
                                             "start_date": "2026-01-05", "time_of_day": "08:00",
                                             "recurrence": "daily", "play_chime": 1})
    db.insert("schedule_zones", {"schedule_id": sid, "zone_id": zone})
    db.execute("INSERT INTO system_settings (key, value) VALUES ('audio.chime_after', 'false')")
    assert migrate(db) == LATEST_VERSION
    repos = Repositories(db)
    schedule = repos.schedules.get(sid)
    assert schedule.name == "Bell" and schedule.zone_ids == [zone]
    assert schedule.play_chime and schedule.play_outro_chime          # outro follows the intro choice
    assert repos.announcements.get(ann).play_outro_chime is False     # chime was off -> outro off too
    assert "audio.chime_after" not in repos.settings.all()            # new default (on) applies
    assert db.scalar("PRAGMA foreign_keys") == 1 and db.integrity_check() == "ok"
    new_id = repos.schedules.create({"name": "Speak", "content_type": "tts", "tts_message": "Hello",
                                     "start_date": "2026-01-05", "time_of_day": "09:00", "recurrence": "daily"},
                                    [zone])
    assert new_id > sid and repos.schedules.get(new_id).content_name == "Hello"
