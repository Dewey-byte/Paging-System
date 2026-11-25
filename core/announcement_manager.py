"""Announcements: create/edit/approve, render to audio (TTS, file, recording) and broadcast."""
from __future__ import annotations

from database.models import Announcement, User
from database.repositories import Repositories
from services.audio_sources import AudioSource, SequenceSource, SilenceSource
from services.text_to_speech import TTSService

from .activity_logger import ActivityLogger
from .audio_manager import AudioLibraryManager
from .authentication import AuthManager
from .errors import BroadcastRejected, NotFoundError, ValidationError
from .events import EventBus
from .paging_manager import ActiveBroadcast, BroadcastController, BroadcastRequest, Priority
from .permissions import P
from .seed_data import ANNOUNCEMENT_CATEGORIES
from .settings_manager import SettingsManager

SOURCE_TYPES = {"tts": "Text-to-Speech", "audio_file": "Audio File", "recording": "Voice Recording"}


class AnnouncementManager:
    CATEGORIES = ANNOUNCEMENT_CATEGORIES

    def __init__(self, repos: Repositories, auth: AuthManager, activity: ActivityLogger, bus: EventBus,
                 library: AudioLibraryManager, tts: TTSService, controller: BroadcastController,
                 settings: SettingsManager):
        self._repos = repos
        self._auth = auth
        self._activity = activity
        self._bus = bus
        self.library = library
        self.tts = tts
        self.controller = controller
        self._settings = settings

    # ---------------------------------------------------------------- query
    def list(self, search: str = "", category: str = "") -> list[Announcement]:
        user = self._auth.current_user
        approved_only = not (user and (user.has(P.ANNOUNCEMENTS_MANAGE) or user.has(P.ANNOUNCEMENTS_APPROVE)))
        return self._repos.announcements.all(search, category, approved_only)

    def get(self, ann_id: int) -> Announcement:
        item = self._repos.announcements.get(ann_id)
        if item is None:
            raise NotFoundError("That announcement no longer exists.")
        return item

    # --------------------------------------------------------------- change
    def save(self, data: dict, zone_ids: list[int], ann_id: int | None = None) -> Announcement:
        user = self._auth.require(P.ANNOUNCEMENTS_MANAGE)
        title = (data.get("title") or "").strip()
        message = (data.get("message") or "").strip()
        category = data.get("category") or ""
        source_type = data.get("source_type") or "tts"
        if not title:
            raise ValidationError("Enter a title for the announcement.")
        if category not in self.CATEGORIES:
            raise ValidationError("Choose an announcement category.")
        if source_type not in SOURCE_TYPES:
            raise ValidationError("Choose how the announcement will be played.")
        audio_file_id = data.get("audio_file_id")
        if source_type == "tts":
            if not message:
                raise ValidationError("Enter the message text to be spoken.")
            audio_file_id = None
        else:
            if not audio_file_id:
                raise ValidationError("Record your voice or choose an audio file first." if source_type == "recording"
                                      else "Choose an audio file from the library or upload one.")
            self.library.get(int(audio_file_id))
        if not zone_ids:
            raise ValidationError("Select at least one target zone.")
        can_approve = user.has(P.ANNOUNCEMENTS_APPROVE)
        record = {
            "title": title[:150], "message": message, "category": category, "source_type": source_type,
            "audio_file_id": audio_file_id, "tts_voice": data.get("tts_voice") or "",
            "tts_rate": int(data.get("tts_rate") or 0), "tts_volume": int(data.get("tts_volume", 100)),
            "play_chime": int(bool(data.get("play_chime", True))),
            "play_outro_chime": int(bool(data.get("play_outro_chime", True))),
            "repeat_count": max(1, min(5, int(data.get("repeat_count") or 1))),
            "is_approved": int(bool(data.get("is_approved", True)) if can_approve else 0),
            "updated_by": user.id,
        }
        if ann_id is None:
            record["created_by"] = user.id
            ann_id = self._repos.announcements.create(record, zone_ids)
            self._activity.log("Created announcement", "Announcements", f"{title} ({category})")
        else:
            self.get(ann_id)
            self._repos.announcements.update(ann_id, record, zone_ids)
            self._activity.log("Updated announcement", "Announcements", title)
        self._bus.data_changed.emit("announcements")
        return self.get(ann_id)

    def set_approved(self, ann_id: int, approved: bool) -> None:
        self._auth.require(P.ANNOUNCEMENTS_APPROVE)
        item = self.get(ann_id)
        self._repos.announcements.update(ann_id, {"is_approved": int(approved)})
        self._activity.log("Approved announcement" if approved else "Withdrew approval", "Announcements", item.title)
        self._bus.data_changed.emit("announcements")

    def delete(self, ann_id: int) -> None:
        self._auth.require(P.ANNOUNCEMENTS_MANAGE)
        item = self.get(ann_id)
        refs = self._repos.announcements.schedule_references(ann_id)
        if refs:
            raise ValidationError(f"'{item.title}' is used by scheduled broadcasts: {', '.join(refs)}. "
                                  "Change or delete those schedules first.")
        self._repos.announcements.delete(ann_id)
        self._activity.log("Deleted announcement", "Announcements", item.title, "warning")
        self._bus.data_changed.emit("announcements")

    # ------------------------------------------------------------ rendering
    def tts_defaults(self) -> tuple[str, int, int]:
        voice = self._settings.get("tts.voice") or self.tts.default_voice_id()
        return voice, self._settings.get_int("tts.rate"), self._settings.get_int("tts.volume")

    def content_source(self, ann: Announcement) -> AudioSource:
        if ann.source_type == "tts":
            voice, rate, volume = self.tts_defaults()
            from services.audio_sources import BufferSource
            data, sr = self.tts.load(ann.message, ann.tts_voice or voice, ann.tts_rate, ann.tts_volume)
            return BufferSource(data, sr, ann.title)
        if not ann.audio_file_id:
            raise ValidationError(f"'{ann.title}' has no audio assigned.")
        return self.library.source_for(ann.audio_file_id)

    # ---------------------------------------------------------------- chimes
    INTRO_GAP = 0.3
    OUTRO_GAP = 0.4

    def intro_chime(self) -> AudioSource | None:
        """The configured intro chime, or None when intro chimes are switched off."""
        if not self._settings.get_bool("audio.chime_before"):
            return None
        return self.library.chime_source(self._settings.get("audio.chime_sound") or "chime_pre")

    def outro_chime(self) -> AudioSource | None:
        """The configured outro chime, or None when outro chimes are switched off."""
        if not self._settings.get_bool("audio.chime_after"):
            return None
        return self.library.chime_source(self._settings.get("audio.outro_chime_sound") or "chime_post")

    def with_chimes(self, body: list[AudioSource], name: str, intro: bool = True, outro: bool = True) -> AudioSource:
        """Frame ``body`` with the intro and outro chimes (each subject to the global switches)."""
        parts: list[AudioSource] = []
        pre = self.intro_chime() if intro else None
        if pre is not None:
            parts += [pre, SilenceSource(self.INTRO_GAP)]
        parts += body
        post = self.outro_chime() if outro else None
        if post is not None:
            parts += [SilenceSource(self.OUTRO_GAP), post]
        return SequenceSource(parts, name=name)

    def chime_seconds(self, intro: bool, outro: bool) -> float:
        """Airtime added by the chimes (used for schedule conflict checks and estimates)."""
        total = 0.0
        for wanted, getter, gap in ((intro, self.intro_chime, self.INTRO_GAP),
                                    (outro, self.outro_chime, self.OUTRO_GAP)):
            if not wanted:
                continue
            try:
                chime = getter()
            except Exception:  # noqa: BLE001 - estimate only
                chime = None
            if chime is not None:
                total += (chime.duration or 3.2) + gap
        return total

    def build_source(self, ann: Announcement, with_chimes: bool = True) -> AudioSource:
        content = self.content_source(ann)
        body: list[AudioSource] = []
        for index in range(max(1, ann.repeat_count)):
            if index:
                body.append(SilenceSource(1.5))
            body.append(content)
        return self.with_chimes(body, ann.title, intro=with_chimes and ann.play_chime,
                                outro=with_chimes and ann.play_outro_chime)

    def estimate_duration(self, ann: Announcement) -> float:
        if ann.source_type == "tts":
            body = self.tts.estimate_seconds(ann.message, ann.tts_rate)
        else:
            item = self._repos.audio_files.get(ann.audio_file_id)
            body = item.duration_seconds if item else 0
        total = body * max(1, ann.repeat_count) + 1.5 * (max(1, ann.repeat_count) - 1)
        return total + self.chime_seconds(ann.play_chime, ann.play_outro_chime)

    def prewarm(self, ann: Announcement) -> None:
        """Render TTS ahead of time (call from a worker thread)."""
        if ann.source_type == "tts" and self.tts.available:
            voice, _, _ = self.tts_defaults()
            self.tts.synthesize(ann.message, ann.tts_voice or voice, ann.tts_rate, ann.tts_volume)

    # ------------------------------------------------------------ broadcast
    def priority_for(self, ann: Announcement) -> int:
        return Priority.LIVE if ann.category == "Emergency Announcement" else Priority.ANNOUNCEMENT

    def broadcast(self, ann_id: int, zone_ids: list[int] | None = None, interrupt: bool = False,
                  origin: str = "manual", user: User | None = None, schedule_id: int | None = None,
                  play_chime: bool | None = None, play_outro_chime: bool | None = None) -> ActiveBroadcast:
        ann = self.get(ann_id)
        if origin == "manual":
            user = self._auth.require(P.ANNOUNCEMENTS_BROADCAST, user)
            if not ann.is_approved and not (user.has(P.ANNOUNCEMENTS_MANAGE) or user.has(P.ANNOUNCEMENTS_APPROVE)):
                raise BroadcastRejected(f"'{ann.title}' is awaiting approval and cannot be played yet.", "approval")
        decision = self.controller.evaluate("announcement", self.priority_for(ann), user, origin)
        if not decision.allowed:
            raise BroadcastRejected(decision.message, decision.code)
        if decision.needs_confirmation and not interrupt:
            raise BroadcastRejected(decision.message, "confirm")
        if play_chime is not None:
            ann.play_chime = play_chime
        if play_outro_chime is not None:
            ann.play_outro_chime = play_outro_chime
        try:
            source = self.build_source(ann)
        except (ValidationError, ValueError, RuntimeError) as exc:
            raise BroadcastRejected(f"Could not prepare '{ann.title}': {exc}", "content") from exc
        zones = zone_ids or ann.zone_ids
        if not zones:
            campus = self.controller.zones.campus_zone()
            zones = [campus.id] if campus else []
        request = BroadcastRequest(
            kind="scheduled" if origin == "scheduler" else "announcement", title=ann.title, zone_ids=zones,
            source=source, priority=self.priority_for(ann), category=ann.category, user=user, origin=origin,
            reference_type="announcement", reference_id=ann.id, interrupt=interrupt)
        return self.controller.start(request)
