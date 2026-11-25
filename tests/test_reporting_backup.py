"""History export (CSV/PDF), library management and backup/restore round trip."""
from __future__ import annotations

import csv
import zipfile

import numpy as np
import pytest

from conftest import ADMIN_PASSWORD, wait_until
from core import paths
from core.backup_manager import apply_pending_restore, read_manifest
from core.errors import ValidationError


def _broadcast_some(ctx):
    for ann in ctx.announcements.list()[:2]:
        ctx.announcements.broadcast(ann.id, [ctx.zones.campus_zone().id])
        ctx.controller.stop(ctx.auth.current_user)


def test_csv_and_pdf_export(ctx, tmp_path):
    _broadcast_some(ctx)
    records = ctx.history.search()
    assert len(records) >= 2
    csv_path = ctx.history.export(records, tmp_path / "history.csv", "All")
    rows = list(csv.reader(csv_path.open(encoding="utf-8-sig")))
    assert rows[0][0] == "Date/Time" and len(rows) == len(records) + 1
    pdf_path = ctx.history.export(records, tmp_path / "history.pdf", "All")
    assert pdf_path.read_bytes()[:4] == b"%PDF"


def test_history_filters(ctx):
    _broadcast_some(ctx)
    assert ctx.history.search(broadcast_type="announcement")
    assert ctx.history.search(broadcast_type="alarm") == []
    assert ctx.history.search(user_name="Ada Admin")
    assert ctx.history.search(text="zzz-no-match") == []
    options = ctx.history.filter_options()
    assert "Ada Admin" in options["users"]


def test_library_upload_rename_categorize_delete(ctx, tmp_path):
    from services.audio_decoder import write_wav
    wav = tmp_path / "hymn.wav"
    write_wav(wav, np.zeros(22050, dtype=np.float32), 22050)
    item = ctx.library.import_file(wav, category="School Hymns")
    assert item.category == "School Hymns" and item.exists and item.duration_seconds == pytest.approx(1.0, abs=0.01)
    ctx.library.rename(item.id, "School Hymn")
    ctx.library.set_category(item.id, "General Audio")
    assert ctx.library.get(item.id).name == "School Hymn"
    builtin = next(f for f in ctx.library.files() if f.is_builtin)
    with pytest.raises(ValidationError):
        ctx.library.delete(builtin.id)
    path = ctx.library.get(item.id).abs_path
    ctx.library.delete(item.id)
    assert not path.exists()
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(ValidationError):
        ctx.library.import_file(bad)


def test_backup_and_restore_roundtrip(ctx, tmp_path):
    ctx.settings.set("school.name", "Before Backup")
    backup = ctx.backup.create_backup(tmp_path / "test.spsbackup")
    manifest = read_manifest(backup)
    assert manifest["school"] == "Before Backup" and manifest["audio_files"] >= 12
    with zipfile.ZipFile(backup) as archive:
        assert "database/paging_system.db" in archive.namelist()
    ctx.settings.set("school.name", "Changed Later")
    ctx.backup.stage_restore(backup)
    ctx.shutdown()
    assert apply_pending_restore()
    from core.app_context import AppContext
    restored = AppContext(data_dir=paths.data_dir(), force_simulated_audio=True, enable_logging=False)
    assert restored.settings.school_name == "Before Backup"
    assert list(paths.backups_dir().glob("pre_restore_*"))
    restored.auth.login("admin", ADMIN_PASSWORD)
    restored.shutdown()
    # make the fixture teardown harmless
    ctx.shutdown = lambda: None


def test_invalid_backup_rejected(ctx, tmp_path):
    fake = tmp_path / "fake.spsbackup"
    fake.write_bytes(b"not a zip")
    with pytest.raises(ValidationError):
        read_manifest(fake)
