from __future__ import annotations

import threading
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .engine import ModelManager, SAMPLE_RATE, online_voice, split_online_text
import numpy as np
import soundfile as sf

from .emotion import EmotionAnalyzer, STYLES, prosody_for
from .storage import Library
from .text import MYANMAR_ANY, normalize_text, syllable_count


class JobQueue:
    def __init__(
        self,
        library: Library,
        model: ModelManager,
        executor: ThreadPoolExecutor | None = None,
        analyzer: EmotionAnalyzer | None = None,
    ):
        self.library = library
        self.model = model
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-worker")
        self.owns_executor = executor is None
        self.analyzer = analyzer

    def submit(
        self,
        text: str,
        voice_id: str | None,
        speed: float,
        preset: str,
        emotion_mode: str = "off",
        style_override: str | None = None,
        emotion_intensity: float = 0.35,
    ) -> dict:
        clean = normalize_text(text)
        if not clean or not MYANMAR_ANY.search(clean):
            raise ValueError("Please enter Burmese text. / မြန်မာစာ ထည့်သွင်းပါ။")
        if len(clean) > 50_000:
            raise ValueError(f"Text is too long ({len(clean)}/50000 characters). / စာသားရှည်လွန်းပါသည်။")
        syllables = syllable_count(clean)
        # Kept for older clients; online speech has no sampling-quality preset.
        preset = preset if preset in ("turbo", "fast", "balanced", "high") else "balanced"
        if not 0.8 <= speed <= 2.0:
            raise ValueError("Speed must be between 0.8 and 2.0.")
        voice = online_voice(voice_id)
        if emotion_mode not in ("off", "auto", "manual"):
            raise ValueError("Unknown emotion mode.")
        if style_override and style_override not in STYLES:
            raise ValueError("Unknown voice style.")
        job_id = uuid4().hex
        job = {
            "id": job_id,
            "state": "queued",
            "progress": 0,
            "total": 0,
            "syllables": syllables,
            "message": "Queued / စောင့်ဆိုင်းနေသည်",
            "error": "",
            "generation": None,
            "style": style_override or "neutral",
            "analysis": [],
        }
        with self.lock:
            self.jobs[job_id] = job
        self.executor.submit(
            self._run, job_id, clean, voice, speed, preset,
            emotion_mode, style_override, emotion_intensity,
        )
        return deepcopy(job)

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return deepcopy(job) if job else None

    def _update(self, job_id: str, **changes) -> None:
        with self.lock:
            self.jobs[job_id].update(changes)

    def _run(
        self,
        job_id: str,
        text: str,
        voice: dict | None,
        speed: float,
        preset: str,
        emotion_mode: str,
        style_override: str | None,
        emotion_intensity: float,
    ) -> None:
        output_path: Path | None = None
        try:
            if emotion_mode == "auto" and self.analyzer:
                analyzed = self.analyzer.analyze_text(text, "conversational")
            else:
                analyzed = [{"text": text, "effective_style": style_override or "neutral",
                             "confidence": 1.0, "intensity": emotion_intensity,
                             "explanation": "manual style" if style_override else "emotion off"}]
            parts: list[dict] = []
            for segment in analyzed:
                for part in split_online_text(segment["text"]):
                    parts.append({**segment, "text": part})
            self._update(
                job_id,
                state="loading" if self.model.state != "ready" else "generating",
                total=len(parts),
                message="Preparing model / မော်ဒယ် ပြင်ဆင်နေသည်",
                analysis=analyzed,
            )
            generation_id, output_path = self.library.create_generation(
                text, voice["id"], voice["name"], speed, preset
            )

            audio_parts: list[np.ndarray] = []
            styles_used: list[str] = []
            for index, part in enumerate(parts, 1):
                chosen_style = part["effective_style"]
                recipe = prosody_for(chosen_style, emotion_intensity, False)
                temporary = output_path.with_name(f".{output_path.stem}-{index}.wav")
                self.model.generate_chunk(
                    part["text"], temporary, speed=speed, preset=preset,
                    voice_id=voice["id"], style=chosen_style, intensity=emotion_intensity,
                )
                audio, rate = sf.read(temporary, dtype="float32", always_2d=False)
                temporary.unlink(missing_ok=True)
                if rate != SAMPLE_RATE:
                    raise RuntimeError("Generated audio has an unexpected sample rate.")
                audio_parts.append(np.asarray(audio).reshape(-1))
                if index < len(parts):
                    base_pause = 0.20 if part["text"].rstrip().endswith(("။", "!", "?")) else 0.11
                    audio_parts.append(np.zeros(int(SAMPLE_RATE * base_pause * recipe["pause_factor"]), dtype=np.float32))
                styles_used.append(chosen_style)
                self._update(job_id, state="generating", progress=index, total=len(parts), style=chosen_style,
                             message=f"Generating {index} of {len(parts)} / အသံဖန်တီးနေသည် {index}/{len(parts)}")
            combined = np.concatenate(audio_parts)
            temporary_output = output_path.with_suffix(".tmp.wav")
            sf.write(temporary_output, combined, SAMPLE_RATE, subtype="PCM_16")
            os.replace(temporary_output, output_path)
            duration = len(combined) / SAMPLE_RATE
            generation = self.library.save_generation(
                generation_id,
                text,
                voice["id"],
                voice["name"],
                output_path,
                duration,
                speed,
                preset,
            )
            self._update(
                job_id,
                state="succeeded",
                progress=len(parts),
                total=len(parts),
                style=styles_used[-1] if styles_used else "neutral",
                message="Ready to play / နားဆင်ရန် အသင့်ဖြစ်ပါပြီ",
                generation=generation,
            )
        except Exception as exc:
            if output_path:
                output_path.unlink(missing_ok=True)
            self._update(
                job_id,
                state="failed",
                message="Generation failed / အသံဖန်တီးမှု မအောင်မြင်ပါ",
                error=str(exc),
            )
