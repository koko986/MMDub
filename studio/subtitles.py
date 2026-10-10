from __future__ import annotations

import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import numpy as np
import soundfile as sf

from .emotion import EmotionAnalyzer
from .engine import ModelManager, SAMPLE_RATE, online_voice, split_online_text
from .storage import Library
from .text import MYANMAR_ANY, normalize_text


TIME = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})$")
RANGE = re.compile(r"\s*-->\s*")


def _seconds(value: str) -> float:
    match = TIME.match(value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_srt(content: str) -> list[dict]:
    content = (content or "").replace("\ufeff", "").replace("\r\n", "\n").strip()
    if not content:
        raise ValueError("Paste or import an SRT file. / SRT စာတန်းထည့်ပါ။")
    cues = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            continue
        pair = RANGE.split(lines[timing_index], maxsplit=1)
        if len(pair) != 2:
            raise ValueError("A subtitle timing line is invalid.")
        start, end = _seconds(pair[0]), _seconds(pair[1].split()[0])
        text = normalize_text(" ".join(lines[timing_index + 1:]))
        if not text or end <= start:
            raise ValueError("Every subtitle needs text and an end time after its start time.")
        cues.append({"index": len(cues) + 1, "start": start, "end": end, "text": text})
    if not cues:
        raise ValueError("No valid SRT subtitles were found. / SRT စာတန်းမတွေ့ပါ။")
    if cues[-1]["end"] > 3_000:
        raise ValueError("Subtitle audio is limited to 50 minutes.")
    if sum(len(cue["text"]) for cue in cues) > 50_000:
        raise ValueError("Subtitle text is limited to 50,000 characters.")
    if not any(MYANMAR_ANY.search(cue["text"]) for cue in cues):
        raise ValueError("Please provide Burmese subtitles. / မြန်မာစာတန်း ထည့်ပါ။")
    return cues


def _atempo(ratio: float) -> str:
    factors = []
    while ratio > 2:
        factors.append(2.0)
        ratio /= 2
    while ratio < .5:
        factors.append(.5)
        ratio /= .5
    factors.append(ratio)
    return ",".join(f"atempo={factor:.8f}" for factor in factors)


class SubtitleQueue:
    def __init__(self, library: Library, model: ModelManager, analyzer: EmotionAnalyzer,
                 executor: ThreadPoolExecutor):
        self.library, self.model, self.analyzer, self.executor = library, model, analyzer, executor
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.root = library.data / "subtitle_jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def submit(self, srt: str, voice_id: str | None, speed: float, emotion_mode: str,
               style: str | None, intensity: float) -> dict:
        cues = parse_srt(srt)
        voice = online_voice(voice_id)
        if not .8 <= speed <= 2:
            raise ValueError("Speed must be between 0.8 and 2.0.")
        job_id = uuid4().hex
        job = {"id": job_id, "state": "queued", "progress": 0, "total": len(cues),
               "message": "Queued / စောင့်ဆိုင်းနေသည်", "error": "", "warnings": [],
               "generation": None, "srt_url": None}
        with self.lock:
            self.jobs[job_id] = job
        self.executor.submit(self._run, job_id, srt, cues, voice, speed, emotion_mode, style, intensity)
        return deepcopy(job)

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return deepcopy(self.jobs.get(job_id)) if job_id in self.jobs else None

    def _update(self, job_id: str, **values) -> None:
        with self.lock:
            self.jobs[job_id].update(values)

    def _run(self, job_id: str, srt: str, cues: list[dict], voice: dict, speed: float,
             emotion_mode: str, style_override: str | None, intensity: float) -> None:
        generation_id = None
        try:
            generation_id, output = self.library.create_generation(
                "\n".join(cue["text"] for cue in cues), voice["id"], voice["name"], speed, "subtitle"
            )
            total_frames = round(cues[-1]["end"] * SAMPLE_RATE)
            sf.write(output, np.zeros(total_frames, dtype=np.int16), SAMPLE_RATE, subtype="PCM_16")
            warnings = []
            with sf.SoundFile(output, mode="r+") as timeline:
                for cue_index, cue in enumerate(cues, 1):
                    result = self.analyzer.analyze_sentence(cue["text"], "conversational") if emotion_mode == "auto" else None
                    style = style_override or (result.style if result else "neutral")
                    arrays = []
                    for part_index, part in enumerate(split_online_text(cue["text"])):
                        temp = self.root / f".{job_id}-{cue_index}-{part_index}.wav"
                        self.model.generate_chunk(part, temp, speed=speed, voice_id=voice["id"],
                                                  style=style, intensity=intensity)
                        arrays.append(sf.read(temp, dtype="float32")[0])
                        temp.unlink(missing_ok=True)
                    audio = np.concatenate(arrays) if arrays else np.zeros(1, dtype=np.float32)
                    slot_frames = max(1, round((cue["end"] - cue["start"]) * SAMPLE_RATE))
                    if len(audio) > slot_frames:
                        ratio = len(audio) / slot_frames
                        source = self.root / f".{job_id}-{cue_index}-source.wav"
                        fitted = self.root / f".{job_id}-{cue_index}-fit.wav"
                        sf.write(source, audio, SAMPLE_RATE, subtype="PCM_16")
                        import imageio_ffmpeg
                        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                                        "-i", str(source), "-af", _atempo(ratio), "-ar", str(SAMPLE_RATE),
                                        "-ac", "1", str(fitted)], check=True, capture_output=True)
                        audio = sf.read(fitted, dtype="float32")[0]
                        source.unlink(missing_ok=True); fitted.unlink(missing_ok=True)
                        warnings.append(f"Cue {cue_index} was sped up to fit {cue['end'] - cue['start']:.2f}s.")
                    audio = np.asarray(audio).reshape(-1)[:slot_frames]
                    start_frame = round(cue["start"] * SAMPLE_RATE)
                    timeline.seek(start_frame)
                    existing = timeline.read(len(audio), dtype="float32")
                    timeline.seek(start_frame)
                    timeline.write(np.clip(existing + audio, -.98, .98))
                    self._update(job_id, state="generating", progress=cue_index,
                                 message=f"Rendering subtitle {cue_index} of {len(cues)}")
            srt_path = self.root / f"{job_id}.srt"
            srt_path.write_text(srt, encoding="utf-8")
            generation = self.library.save_generation(
                generation_id, "\n".join(cue["text"] for cue in cues), voice["id"], voice["name"],
                output, cues[-1]["end"], speed, "subtitle"
            )
            self._update(job_id, state="succeeded", progress=len(cues), warnings=warnings,
                         message="Timed narration is ready / စာတန်းအသံ အသင့်ဖြစ်ပါပြီ",
                         generation=generation, srt_url=f"/api/subtitle-jobs/{job_id}/srt")
        except Exception as exc:
            self._update(job_id, state="failed", error=str(exc), message="Subtitle generation failed")

    def srt_path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.srt"
