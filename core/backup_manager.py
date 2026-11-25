"""Backup & restore of the database and audio library (zip based)."""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from database.database import Database
from database.migrations import LATEST_VERSION

from . import paths
from .activity_logger import ActivityLogger
from .authentication import AuthManager
from .errors import ValidationError
from .permissions import P
from .settings_manager import SettingsManager

BACKUP_EXT = ".spsbackup"
DB_ARCNAME = "database/paging_system.db"
KEEP_AUTO_BACKUPS = 7


def read_manifest(path: str | Path) -> dict:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "manifest.json" not in names or DB_ARCNAME not in names:
                raise ValidationError("This file is not a School Paging System backup.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise ValidationError("The backup file is damaged or not a valid backup.") from exc
    if int(manifest.get("schema_version", 0)) > LATEST_VERSION:
        raise ValidationError("This backup was created by a newer version of the application.")
    return manifest


def pending_restore_dir() -> Path:
    return paths.data_dir() / "restore_pending"


def apply_pending_restore() -> bool:
    """Swap in a staged restore. Must run before the database is opened."""
    staged = pending_restore_dir()
    marker = staged / "pending.json"
    if not marker.exists():
        return False
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safety = paths.backups_dir() / f"pre_restore_{stamp}"
    safety.mkdir(parents=True, exist_ok=True)
    db_path = paths.database_path()
    if db_path.exists():
        shutil.copy2(db_path, safety / db_path.name)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
    shutil.copy2(staged / DB_ARCNAME, db_path)
    staged_library = staged / "library"
    if staged_library.exists():
        current = paths.library_dir()
        shutil.move(str(current), str(safety / "library"))
        shutil.copytree(staged_library, current)
    shutil.rmtree(staged, ignore_errors=True)
    return True


class BackupManager:
    def __init__(self, db: Database, auth: AuthManager, activity: ActivityLogger, settings: SettingsManager):
        self._db = db
        self._auth = auth
        self._activity = activity
        self._settings = settings

    def create_backup(self, destination: str | Path | None = None, include_audio: bool = True,
                      auto: bool = False) -> Path:
        if not auto:
            self._auth.require(P.SETTINGS_MANAGE)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = Path(destination) if destination else \
            paths.backups_dir() / f"{'auto' if auto else 'backup'}_{stamp}{BACKUP_EXT}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "paging_system.db"
            self._db.backup_to(snapshot)
            audio_files = 0
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, DB_ARCNAME)
                if include_audio:
                    library = paths.library_dir()
                    for file in library.rglob("*"):
                        if file.is_file():
                            archive.write(file, f"library/{file.relative_to(library).as_posix()}")
                            audio_files += 1
                archive.writestr("manifest.json", json.dumps({
                    "app": paths.APP_NAME, "app_version": paths.APP_VERSION, "schema_version": self._db.user_version,
                    "created_at": datetime.now().isoformat(timespec="seconds"), "audio_files": audio_files,
                    "school": self._settings.school_name, "automatic": auto}, indent=2))
        self._activity.log("Created backup" if not auto else "Automatic backup", "System Settings",
                           f"{destination.name} ({audio_files} audio files)", system=auto)
        return destination

    def auto_backup_if_due(self) -> Path | None:
        if not self._settings.get_bool("app.auto_backup"):
            return None
        last = self._settings.get("app.last_auto_backup") or ""
        try:
            if last and datetime.fromisoformat(last) > datetime.now() - timedelta(hours=24):
                return None
        except ValueError:
            pass
        path = self.create_backup(auto=True)
        self._settings.set("app.last_auto_backup", datetime.now().isoformat(timespec="seconds"), quiet=True)
        autos = sorted(paths.backups_dir().glob(f"auto_*{BACKUP_EXT}"))
        for old in autos[:-KEEP_AUTO_BACKUPS]:
            old.unlink(missing_ok=True)
        return path

    def list_backups(self) -> list[Path]:
        return sorted(paths.backups_dir().glob(f"*{BACKUP_EXT}"), key=lambda p: p.stat().st_mtime, reverse=True)

    def stage_restore(self, backup_path: str | Path) -> dict:
        """Validate and stage a backup; it is applied on the next application start."""
        self._auth.require(P.SETTINGS_MANAGE)
        manifest = read_manifest(backup_path)
        staged = pending_restore_dir()
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True)
        with zipfile.ZipFile(backup_path) as archive:
            for member in archive.namelist():
                target = (staged / member).resolve()
                if not str(target).startswith(str(staged.resolve())):
                    raise ValidationError("The backup contains unsafe file paths and was rejected.")
            archive.extractall(staged)
        (staged / "pending.json").write_text(json.dumps({"source": str(backup_path), **manifest}), encoding="utf-8")
        self._activity.log("Staged restore", "System Settings",
                           f"{Path(backup_path).name} from {manifest.get('created_at', '?')}", "warning")
        return manifest

    def change_database_location(self, new_path: str | Path) -> Path:
        self._auth.require(P.SETTINGS_MANAGE)
        new_path = Path(new_path)
        if new_path.suffix.lower() != ".db":
            new_path = new_path.with_suffix(".db")
        if new_path.resolve() == paths.database_path().resolve():
            raise ValidationError("That is already the current database location.")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        self._db.backup_to(new_path)
        config = paths.load_config()
        config["database_path"] = str(new_path)
        paths.save_config(config)
        self._activity.log("Moved database", "System Settings", str(new_path), "warning")
        return new_path
