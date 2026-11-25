"""Filesystem locations used by the application.

All user data (database, uploaded audio, caches, backups, logs) lives in a
single *data directory* so it survives application updates and can be backed
up as a unit.  Resolution order:

1. ``set_data_dir()`` override (used by tests),
2. the ``SPS_DATA_DIR`` environment variable,
3. ``<install dir>/data`` when a ``portable.flag`` file sits next to the app,
4. ``%LOCALAPPDATA%/SchoolPagingSystem``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_ID = "SchoolPagingSystem"
APP_NAME = "School Paging System"
APP_VERSION = "1.0.0"
ORG_NAME = "SchoolPaging"

_data_dir_override: Path | None = None


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Folder that contains bundled read-only resources (assets/)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def install_dir() -> Path:
    """Folder containing the executable (or main.py when run from source)."""
    return Path(sys.executable).resolve().parent if is_frozen() else resource_root()


def assets_dir() -> Path:
    return resource_root() / "assets"


def asset(*parts: str) -> Path:
    return assets_dir().joinpath(*parts)


def set_data_dir(path: str | Path | None) -> None:
    """Override the data directory (tests, portable deployments)."""
    global _data_dir_override
    _data_dir_override = Path(path) if path else None


def data_dir() -> Path:
    if _data_dir_override is not None:
        path = _data_dir_override
    elif os.environ.get("SPS_DATA_DIR"):
        path = Path(os.environ["SPS_DATA_DIR"])
    elif (install_dir() / "portable.flag").exists():
        path = install_dir() / "data"
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sub(*parts: str) -> Path:
    path = data_dir().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_dir() -> Path:
    return _sub("library")


def builtin_sounds_dir() -> Path:
    return _sub("library", "builtin")


def recordings_dir() -> Path:
    return _sub("library", "recordings")


def tts_cache_dir() -> Path:
    return _sub("cache", "tts")


def ui_cache_dir() -> Path:
    return _sub("cache", "ui")


def backups_dir() -> Path:
    return _sub("backups")


def logs_dir() -> Path:
    return _sub("logs")


# --------------------------------------------------------------------------- config
CONFIG_FILE = "config.json"


def config_path() -> Path:
    return data_dir() / CONFIG_FILE


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def database_path() -> Path:
    """Database file location (configurable from System Settings)."""
    custom = load_config().get("database_path")
    return Path(custom) if custom else data_dir() / "paging_system.db"


# ------------------------------------------------------------------ stored paths
def to_stored_path(path: str | Path) -> str:
    """Store paths inside the data dir as relative paths so backups are portable."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(data_dir().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def resolve_stored_path(stored: str) -> Path:
    candidate = Path(stored)
    return candidate if candidate.is_absolute() else data_dir() / candidate
