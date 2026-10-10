from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

SAMPLE_RATE = 24_000
PROVIDER_VERSION = "edge-tts/7.2.8"
DEFAULT_VOICE = "edge:my-MM-NilarNeural"
ONLINE_VOICES = {
    "edge:my-MM-NilarNeural": {"id": "edge:my-MM-NilarNeural", "engine_id": "my-MM-NilarNeural", "name": "Nilar · Female / နီလာ · အမျိုးသမီး", "gender": "female"},
    "edge:my-MM-ThihaNeural": {"id": "edge:my-MM-ThihaNeural", "engine_id": "my-MM-ThihaNeural", "name": "Thiha · Male / သီဟ · အမျိုးသား", "gender": "male"},
    "edge:en-US-AvaMultilingualNeural": {"id": "edge:en-US-AvaMultilingualNeural", "engine_id": "en-US-AvaMultilingualNeural", "name": "Ava Multilingual · Female", "gender": "female"},
    "edge:en-US-AndrewMultilingualNeural": {"id": "edge:en-US-AndrewMultilingualNeural", "engine_id": "en-US-AndrewMultilingualNeural", "name": "Andrew Multilingual · Male", "gender": "male"},
    "edge:en-US-BrianMultilingualNeural": {"id": "edge:en-US-BrianMultilingualNeural", "engine_id": "en-US-BrianMultilingualNeural", "name": "Brian Multilingual · Male", "gender": "male"},
    "edge:en-US-EmmaMultilingualNeural": {"id": "edge:en-US-EmmaMultilingualNeural", "engine_id": "en-US-EmmaMultilingualNeural", "name": "Emma Multilingual · Female", "gender": "female"},
    "edge:de-DE-FlorianMultilingualNeural": {"id": "edge:de-DE-FlorianMultilingualNeural", "engine_id": "de-DE-FlorianMultilingualNeural", "name": "Florian Multilingual · Male", "gender": "male"},
}
STYLE_PROSODY = {
    "neutral": (0, 0, 0), "warm": (-5, -1, 1), "happy": (7, 3, 1),
    "sad": (-12, -3, -8), "angry": (5, 0, 5), "excited": (11, 5, 3),
    "serious": (-7, -2, 1), "inquisitive": (-1, 4, 0),
}


def online_voice(voice_id: str | None) -> dict:
    voice_id = voice_id or DEFAULT_VOICE
    if voice_id not in ONLINE_VOICES:
        raise ValueError("Choose one of the available online voices. / အွန်လိုင်းအသံတစ်ခုကို ရွေးပါ။")
    return ONLINE_VOICES[voice_id]


def split_online_text(text: str, limit: int = 4_400) -> list[str]:
    import re
    pieces = [p for p in re.split(r"(?<=[။၊.!?])|\n+", text.strip()) if p.strip()]
    chunks, current = [], ""
    for piece in pieces:
        piece = piece.strip()
        while len(piece) > limit:
            cut = piece.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            head, piece = piece[:cut].strip(), piece[cut:].strip()
            if current:
                chunks.append(current)
                current = ""
            if head:
                chunks.append(head)
        candidate = f"{current} {piece}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class ModelManager:
    """Online Edge speech provider retaining the former engine interface."""

    def __init__(self, cache_dir: Path | None = None):
        self.state = "checking"
        self.device = "Online voice · GPU not used"
        self.message = "Checking online speech / အွန်လိုင်းအသံ စစ်ဆေးနေသည်"
        self.error = ""
        self.cache_dir = cache_dir or Path.cwd() / "data" / "provider_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def status(self) -> dict:
        return {"state": self.state, "device": self.device, "message": self.message,
                "error": self.error, "provider": "Microsoft Edge online speech",
                "provider_version": PROVIDER_VERSION, "online_required": True}

    def ensure_loaded(self):
        with self._load_lock:
            try:
                import edge_tts  # noqa: F401
                self.state = "ready"
                self.message = "Online voices ready / အွန်လိုင်းအသံ အသင့်ဖြစ်ပါပြီ"
                self.error = ""
                return self
            except ModuleNotFoundError as exc:
                self.state = "error"
                self.error = "Online speech runtime is not installed. Run start.cmd to finish setup."
                raise RuntimeError(self.error) from exc

    def unload(self) -> None:
        return None

    @staticmethod
    def _ffmpeg() -> str:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def _prosody(speed: float, style: str, intensity: float) -> tuple[str, str, str]:
        base_rate, pitch, volume = STYLE_PROSODY.get(style, STYLE_PROSODY["neutral"])
        scale = max(0.0, min(1.0, intensity))
        return (f"{round((speed - 1.0) * 100 + base_rate * scale):+d}%",
                f"{round(pitch * scale):+d}Hz", f"{round(volume * scale):+d}%")

    def _cache_key(self, text: str, voice_id: str, rate: str, pitch: str, volume: str) -> str:
        raw = json.dumps([PROVIDER_VERSION, text, voice_id, rate, pitch, volume], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def generate_chunk(self, text: str, output_path: Path, ref_audio: str | None = None,
                       ref_text: str | None = None, speed: float = 1.0,
                       preset: str = "balanced", seed: int | None = None,
                       *, voice_id: str | None = None, style: str = "neutral",
                       intensity: float = 0.35) -> float:
        del ref_audio, ref_text, preset, seed
        self.ensure_loaded()
        voice = online_voice(voice_id)
        rate, pitch, volume = self._prosody(speed, style, intensity)
        cached = self.cache_dir / f"{self._cache_key(text, voice['id'], rate, pitch, volume)}.wav"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if cached.exists():
            shutil.copy2(cached, output_path)
            with sf.SoundFile(output_path) as audio:
                return len(audio) / audio.samplerate
        temporary_mp3 = output_path.with_suffix(".edge.mp3")
        temporary_wav = output_path.with_suffix(".edge.wav")
        last_error = None
        with self._inference_lock:
            if cached.exists():
                shutil.copy2(cached, output_path)
                with sf.SoundFile(output_path) as audio:
                    return len(audio) / audio.samplerate
            for attempt in range(3):
                try:
                    import edge_tts
                    asyncio.run(edge_tts.Communicate(text, voice["engine_id"], rate=rate,
                                                     pitch=pitch, volume=volume).save(str(temporary_mp3)))
                    subprocess.run([self._ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                                    "-i", str(temporary_mp3), "-ac", "1", "-ar", str(SAMPLE_RATE),
                                    "-c:a", "pcm_s16le", str(temporary_wav)], check=True, capture_output=True)
                    os.replace(temporary_wav, output_path)
                    shutil.copy2(output_path, cached)
                    temporary_mp3.unlink(missing_ok=True)
                    self.state, self.error = "ready", ""
                    with sf.SoundFile(output_path) as audio:
                        return len(audio) / audio.samplerate
                except Exception as exc:
                    last_error = exc
                    temporary_mp3.unlink(missing_ok=True)
                    temporary_wav.unlink(missing_ok=True)
                    if attempt < 2:
                        time.sleep((0.8, 2.0)[attempt])
        self.state, self.error = "offline", str(last_error or "Online speech failed")
        raise RuntimeError("Online Burmese speech is unavailable. Check the internet and retry; completed chunks are saved. / အင်တာနက်စစ်ပြီး ပြန်ကြိုးစားပါ။ ပြီးသည့်အပိုင်းများကို သိမ်းထားပါသည်။") from last_error

    def generate(self, chunks: list[str], output_path: Path, ref_audio: str | None,
                 ref_text: str | None, speed: float, preset: str,
                 progress: Callable[[int, int], None]) -> float:
        pieces = []
        for index, chunk in enumerate(chunks, 1):
            part = output_path.with_name(f".{output_path.stem}-{index}.wav")
            self.generate_chunk(chunk, part, speed=speed, preset=preset)
            audio, _ = sf.read(part, dtype="float32")
            part.unlink(missing_ok=True)
            pieces.append(np.asarray(audio).reshape(-1))
            if index < len(chunks):
                pieces.append(np.zeros(round(SAMPLE_RATE * 0.16), dtype=np.float32))
            progress(index, len(chunks))
        combined = np.concatenate(pieces)
        temp = output_path.with_suffix(".tmp.wav")
        sf.write(temp, combined, SAMPLE_RATE, subtype="PCM_16")
        os.replace(temp, output_path)
        return len(combined) / SAMPLE_RATE
