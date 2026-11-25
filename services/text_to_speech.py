"""Windows text-to-speech (SAPI 5 + OneCore voices) rendered to cached WAV files.

Speech is synthesised to a file and then played through the normal broadcast
engine, so TTS announcements reach exactly the configured zones/devices.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio_decoder import decode

log = logging.getLogger(__name__)

ONECORE_CATEGORY = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
SAFT22kHz16BitMono = 22
SVSF_IS_NOT_XML = 16
SSFM_CREATE_FOR_WRITE = 3


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str
    engine: str  # "SAPI" or "OneCore"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.engine})"


def _com():
    import pythoncom  # noqa: WPS433 - optional Windows dependency
    import win32com.client
    return pythoncom, win32com.client


class TTSService:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._voices: list[Voice] | None = None
        self._lock = threading.Lock()
        self.error = ""
        try:
            _com()
            self.available = True
        except Exception as exc:  # pragma: no cover - non-Windows
            self.available = False
            self.error = f"Text-to-speech is unavailable: {exc}"

    # ------------------------------------------------------------- voices
    def voices(self, refresh: bool = False) -> list[Voice]:
        if not self.available:
            return []
        if self._voices is not None and not refresh:
            return self._voices
        with self._lock:
            pythoncom, client = _com()
            pythoncom.CoInitialize()
            try:
                result: list[Voice] = []
                seen = set()
                speaker = client.Dispatch("SAPI.SpVoice")
                tokens = speaker.GetVoices()
                for i in range(tokens.Count):
                    token = tokens.Item(i)
                    result.append(self._voice_from_token(token, "SAPI"))
                    seen.add(self._short_name(token.GetDescription()))
                try:
                    category = client.Dispatch("SAPI.SpObjectTokenCategory")
                    category.SetId(ONECORE_CATEGORY, False)
                    onecore = category.EnumerateTokens()
                    for i in range(onecore.Count):
                        token = onecore.Item(i)
                        voice = self._voice_from_token(token, "OneCore")
                        if self._short_name(voice.name) not in seen:
                            result.append(voice)
                except Exception:
                    log.debug("OneCore voices unavailable", exc_info=True)
                self._voices = result
                self.error = "" if result else "No text-to-speech voices are installed."
            except Exception as exc:
                self.error = f"Could not list voices: {exc}"
                self._voices = []
            finally:
                pythoncom.CoUninitialize()
        return self._voices

    @staticmethod
    def _short_name(description: str) -> str:
        return re.sub(r"\s+(Desktop)?\s*-.*$", "", description).replace(" Desktop", "").strip().lower()

    @staticmethod
    def _voice_from_token(token, engine: str) -> Voice:
        description = token.GetDescription()
        name, _, language = description.partition(" - ")
        return Voice(id=token.Id, name=name.replace(" Desktop", "").strip(), language=language.strip(), engine=engine)

    def default_voice_id(self) -> str:
        voices = self.voices()
        preferred = next((v for v in voices if v.engine == "OneCore"), None)
        return (preferred or (voices[0] if voices else None)).id if voices else ""

    # ---------------------------------------------------------- synthesis
    def cache_path(self, text: str, voice_id: str, rate: int, volume: int) -> Path:
        key = hashlib.sha1(f"{voice_id}|{rate}|{volume}|{text}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.wav"

    def synthesize(self, text: str, voice_id: str = "", rate: int = 0, volume: int = 100) -> Path:
        """Render speech to a WAV file (cached). Safe to call from worker threads."""
        text = (text or "").strip()
        if not text:
            raise ValueError("There is no text to speak.")
        if not self.available:
            raise RuntimeError(self.error or "Text-to-speech is unavailable.")
        rate = max(-10, min(10, int(rate)))
        volume = max(0, min(100, int(volume)))
        path = self.cache_path(text, voice_id, rate, volume)
        if path.exists() and path.stat().st_size > 44:
            return path
        tmp = path.with_suffix(".tmp.wav")
        with self._lock:
            pythoncom, client = _com()
            pythoncom.CoInitialize()
            try:
                speaker = client.Dispatch("SAPI.SpVoice")
                if voice_id:
                    token = self._token(client, voice_id)
                    if token is not None:
                        speaker.Voice = token
                speaker.Rate = rate
                speaker.Volume = volume
                stream = client.Dispatch("SAPI.SpFileStream")
                try:
                    fmt = client.Dispatch("SAPI.SpAudioFormat")
                    fmt.Type = SAFT22kHz16BitMono
                    stream.Format = fmt
                except Exception:
                    log.debug("Could not set SAPI audio format", exc_info=True)
                stream.Open(str(tmp), SSFM_CREATE_FOR_WRITE, False)
                speaker.AudioOutputStream = stream
                try:
                    speaker.Speak(text, SVSF_IS_NOT_XML)
                finally:
                    stream.Close()
            finally:
                pythoncom.CoUninitialize()
        tmp.replace(path)
        return path

    @staticmethod
    def _token(client, voice_id: str):
        try:
            token = client.Dispatch("SAPI.SpObjectToken")
            token.SetId(voice_id, "", False)
            return token
        except Exception:
            log.warning("Voice %s not found; using the default voice", voice_id)
            return None

    def load(self, text: str, voice_id: str = "", rate: int = 0, volume: int = 100) -> tuple[np.ndarray, int]:
        return decode(self.synthesize(text, voice_id, rate, volume))

    @staticmethod
    def estimate_seconds(text: str, rate: int = 0) -> float:
        words = max(1, len((text or "").split()))
        wpm = 165 * (1.08 ** max(-10, min(10, rate)))
        return words / wpm * 60 + 0.5

    def clear_cache(self) -> int:
        removed = 0
        for path in self.cache_dir.glob("*.wav"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
