"""Audio library: built-in sounds, uploads, recordings, categories and decoding."""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

import numpy as np

from database.models import AudioFile
from database.repositories import Repositories
from services import sound_generator
from services.audio_decoder import SUPPORTED_EXTENSIONS, AudioCache, DecodeError, probe, write_wav
from services.audio_sources import BufferSource

from . import paths
from .activity_logger import ActivityLogger
from .authentication import AuthManager
from .errors import NotFoundError, ValidationError
from .events import EventBus
from .permissions import P
from .seed_data import AUDIO_CATEGORIES

MAX_UPLOAD_BYTES = 300 * 1024 * 1024


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip().strip(".")
    return cleaned[:80] or "audio"


class AudioLibraryManager:
    CATEGORIES = AUDIO_CATEGORIES

    def __init__(self, repos: Repositories, auth: AuthManager, activity: ActivityLogger, bus: EventBus,
                 cache: AudioCache):
        self._repos = repos
        self._auth = auth
        self._activity = activity
        self._bus = bus
        self.cache = cache
        self._builtin_sources: dict[str, BufferSource] = {}

    # ------------------------------------------------------------ built-in
    def ensure_builtin_sounds(self) -> int:
        """Generate missing built-in sounds and register them. Returns how many were created."""
        created = 0
        folder = paths.builtin_sounds_dir()
        for key, (name, category, _gen, _peak) in sound_generator.BUILTIN_SOUNDS.items():
            path = folder / f"{key}.wav"
            if not path.exists():
                sound_generator.write_builtin(key, folder)
                created += 1
            row = self._repos.audio_files.get_by_builtin_key(key)
            if row is None:
                info = probe(path)
                self._repos.audio_files.create(
                    name=name, category=category, file_path=paths.to_stored_path(path),
                    original_filename=path.name, format="WAV", duration_seconds=round(info.duration, 2),
                    sample_rate=info.sample_rate, channels=info.channels, size_bytes=path.stat().st_size,
                    is_builtin=True, builtin_key=key)
            elif not row.exists:
                sound_generator.write_builtin(key, folder)
        return created

    def builtin_source(self, key: str) -> BufferSource:
        if key not in self._builtin_sources:
            row = self._repos.audio_files.get_by_builtin_key(key)
            if row is not None and row.exists:
                data, rate = self.cache.load(row.abs_path)
            else:
                data, rate = sound_generator.generate(key)
            self._builtin_sources[key] = BufferSource(data, rate, sound_generator.BUILTIN_SOUNDS[key][0])
        return self._builtin_sources[key]

    def chime_source(self, key: str) -> BufferSource | None:
        if key in sound_generator.BUILTIN_SOUNDS:
            return self.builtin_source(key)
        if str(key).isdigit():
            try:
                return self.source_for(int(key))
            except (NotFoundError, ValidationError):
                return None
        return None

    # --------------------------------------------------------------- query
    def files(self, category: str | None = None, search: str = "") -> list[AudioFile]:
        return self._repos.audio_files.all(category, search)

    def get(self, file_id: int) -> AudioFile:
        item = self._repos.audio_files.get(file_id)
        if item is None:
            raise NotFoundError("That audio file no longer exists in the library.")
        return item

    def category_counts(self) -> dict[str, int]:
        return self._repos.audio_files.category_counts()

    def source_for(self, file_id: int) -> BufferSource:
        item = self.get(file_id)
        if not item.exists:
            raise ValidationError(f"The audio file for '{item.name}' is missing from disk.")
        try:
            data, rate = self.cache.load(item.abs_path)
        except DecodeError as exc:
            raise ValidationError(str(exc)) from exc
        return BufferSource(data, rate, item.name)

    # ------------------------------------------------------------- changes
    def import_file(self, source_path: str | Path, name: str | None = None, category: str = "General Audio",
                    require_permission: bool = True) -> AudioFile:
        if require_permission:
            self._auth.require(P.LIBRARY_MANAGE)
        source_path = Path(source_path)
        if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValidationError(f"'{source_path.name}' is not a supported audio format "
                                  f"({', '.join(e.lstrip('.').upper() for e in SUPPORTED_EXTENSIONS)}).")
        if not source_path.exists():
            raise ValidationError(f"'{source_path.name}' could not be found.")
        size = source_path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ValidationError(f"'{source_path.name}' is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        try:
            info = probe(source_path)
        except DecodeError as exc:
            raise ValidationError(f"'{source_path.name}' could not be read: {exc}") from exc
        if info.duration <= 0:
            raise ValidationError(f"'{source_path.name}' contains no audio.")
        if category not in self.CATEGORIES:
            category = "General Audio"
        uploads = paths.library_dir() / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / f"{uuid.uuid4().hex[:8]}_{_safe_filename(source_path.name)}"
        shutil.copy2(source_path, dest)
        user = self._auth.current_user
        display = (name or source_path.stem).strip()[:120] or source_path.stem
        file_id = self._repos.audio_files.create(
            name=display, category=category, file_path=paths.to_stored_path(dest),
            original_filename=source_path.name, format=source_path.suffix.lstrip(".").upper(),
            duration_seconds=round(info.duration, 2), sample_rate=info.sample_rate, channels=info.channels,
            size_bytes=size, is_builtin=False, uploaded_by=user.id if user else None)
        self._activity.log("Uploaded audio", "Audio Library", f"{display} ({category}, {info.duration:.1f}s)")
        self._bus.data_changed.emit("library")
        return self.get(file_id)

    def save_recording(self, data: np.ndarray, sample_rate: int, name: str,
                       category: str = "School Announcements") -> AudioFile:
        if data.size < sample_rate * 0.3:
            raise ValidationError("The recording is too short. Record at least one second of audio.")
        dest = paths.recordings_dir() / f"rec_{uuid.uuid4().hex[:10]}.wav"
        write_wav(dest, data, sample_rate)
        user = self._auth.current_user
        file_id = self._repos.audio_files.create(
            name=(name or "Voice recording").strip()[:120], category=category, file_path=paths.to_stored_path(dest),
            original_filename=dest.name, format="WAV", duration_seconds=round(data.size / sample_rate, 2),
            sample_rate=sample_rate, channels=1, size_bytes=dest.stat().st_size, is_builtin=False,
            uploaded_by=user.id if user else None)
        self._activity.log("Recorded voice announcement", "Audio Library", f"{name} ({data.size / sample_rate:.1f}s)")
        self._bus.data_changed.emit("library")
        return self.get(file_id)

    def rename(self, file_id: int, name: str) -> None:
        self._auth.require(P.LIBRARY_MANAGE)
        item = self.get(file_id)
        name = (name or "").strip()
        if not name:
            raise ValidationError("Enter a name for the audio file.")
        self._repos.audio_files.update(file_id, name=name[:120])
        self._activity.log("Renamed audio", "Audio Library", f"{item.name} -> {name}")
        self._bus.data_changed.emit("library")

    def set_category(self, file_id: int, category: str) -> None:
        self._auth.require(P.LIBRARY_MANAGE)
        if category not in self.CATEGORIES:
            raise ValidationError("Choose one of the library categories.")
        item = self.get(file_id)
        self._repos.audio_files.update(file_id, category=category)
        self._activity.log("Changed audio category", "Audio Library", f"{item.name}: {category}")
        self._bus.data_changed.emit("library")

    def references(self, file_id: int) -> dict[str, list[str]]:
        return self._repos.audio_files.references(file_id)

    def delete(self, file_id: int) -> None:
        self._auth.require(P.LIBRARY_MANAGE)
        item = self.get(file_id)
        if item.is_builtin:
            raise ValidationError("Built-in sounds cannot be deleted.")
        refs = self.references(file_id)
        if refs:
            used = "; ".join(f"{kind}: {', '.join(names)}" for kind, names in refs.items())
            raise ValidationError(f"'{item.name}' is still in use ({used}). Reassign those items first.")
        self._repos.audio_files.delete(file_id)
        try:
            if item.abs_path.is_relative_to(paths.library_dir()):
                item.abs_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._activity.log("Deleted audio", "Audio Library", item.name, "warning")
        self._bus.data_changed.emit("library")
