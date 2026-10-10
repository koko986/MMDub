from __future__ import annotations

import io
import importlib.util
import json
import os
import subprocess
import sys
import threading
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

# The Hub's Xet transfer backend does not reliably resume interrupted downloads
# on some Windows installations. Standard HTTP keeps the partial model reusable.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from studio.engine import DEFAULT_VOICE, ONLINE_VOICES, ModelManager, online_voice
from studio.emotion import DELIVERY_MODES, STYLES, EmotionAnalyzer
from studio.batches import BatchQueue
from studio.jobs import JobQueue
from studio.projects import ProjectRenderQueue
from studio.storage import Library
from studio.subtitles import SubtitleQueue
from studio.text import LONG_FORM_MAXIMUM, normalize_text, validate_burmese, validate_long_form
from studio.secrets import GeminiKeyStore
from studio.video_storage import VideoLibrary
from studio.rednote import validate_rednote_url
from studio.video_dubbing import VideoDubbingQueue, detect_subtitle_region, probe_video, write_source_artifacts


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MAX_UPLOAD = 20 * 1024 * 1024
library = Library(ROOT)
model = ModelManager(library.data / "provider_cache")
analyzer = EmotionAnalyzer()
tts_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-worker")
jobs = JobQueue(library, model, executor=tts_executor, analyzer=analyzer)
subtitle_queue = SubtitleQueue(library, model, analyzer, tts_executor)
project_queue = ProjectRenderQueue(library, model, analyzer, tts_executor)
batch_queue = BatchQueue(library, project_queue)
key_store = GeminiKeyStore(library.data)
video_library = VideoLibrary(library)
video_queue = VideoDubbingQueue(library, video_library, model, analyzer, key_store, tts_executor)


class RuntimeInstaller:
    def __init__(self):
        self.lock = threading.RLock()
        self.state = "ready" if self._installed() else "required"
        self.message = "Runtime ready." if self.state == "ready" else "First-time AI setup is required."
        self.process: subprocess.Popen | None = None
        self.log_path = ROOT / "data" / "setup.log"

    @staticmethod
    def _installed() -> bool:
        required = ("torch", "edge_tts", "transformers", "sentencepiece", "faster_whisper", "av", "imageio_ffmpeg", "google.genai", "yt_dlp", "cv2")
        return all(importlib.util.find_spec(package) is not None for package in required)

    def status(self) -> dict:
        with self.lock:
            state, message = self.state, self.message
        progress = ""
        if self.log_path.exists():
            try:
                lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                progress = next((line.strip() for line in reversed(lines) if line.strip()), "")[-240:]
            except OSError:
                pass
        return {"state": state, "message": message, "progress": progress}

    def start(self) -> dict:
        with self.lock:
            if self.state == "running":
                return self.status()
            if self._installed():
                self.state = "ready"
                self.message = "Runtime ready."
                return {"state": self.state, "message": self.message, "progress": ""}
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log = self.log_path.open("w", encoding="utf-8")
            self.process = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "setup.ps1"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.state = "running"
            self.message = "Installing the local AI runtime. Keep this page open."
            threading.Thread(target=self._monitor, args=(log,), daemon=True, name="runtime-installer").start()
        return {"state": "running", "message": self.message, "progress": ""}

    def _monitor(self, log) -> None:
        assert self.process is not None
        code = self.process.wait()
        log.close()
        with self.lock:
            if code == 0 and self._installed():
                self.state = "ready"
                self.message = "Runtime installed. Online Burmese voices are ready."
                threading.Thread(target=_preload_model, daemon=True, name="model-loader-after-setup").start()
            else:
                self.state = "failed"
                self.message = "Setup did not finish. Check the last setup message and try again."


runtime = RuntimeInstaller()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.environ.get("BURMESE_TTS_SKIP_MODEL_LOAD") != "1":
        threading.Thread(target=_preload_model, daemon=True, name="model-loader").start()
        analyzer.preload()
    yield
    tts_executor.shutdown(wait=False, cancel_futures=True)


def _preload_model() -> None:
    try:
        model.ensure_loaded()
    except Exception:
        pass


app = FastAPI(title="MMDub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class JobRequest(BaseModel):
    text: str = Field(min_length=1)
    online_speech_consent: bool = False
    voice_id: str | None = None
    speed: float = 1.0
    preset: str = "balanced"
    emotion_mode: str = "off"
    style_override: str | None = None
    emotion_intensity: float = 0.35


class SubtitleJobRequest(BaseModel):
    srt: str = Field(min_length=1)
    online_speech_consent: bool = False
    voice_id: str | None = None
    speed: float = Field(default=1.0, ge=0.8, le=2.0)
    emotion_mode: str = "auto"
    style_override: str | None = None
    emotion_intensity: float = Field(default=0.35, ge=0, le=1)


class ChapterInput(BaseModel):
    title: str = "Chapter"
    text: str = Field(min_length=1)
    voice_id: str | None = None


class ProjectRequest(BaseModel):
    title: str = Field(default="Untitled narration", min_length=1, max_length=80)
    voice_id: str | None = None
    speed: float = 1.0
    preset: str = "balanced"
    delivery_mode: str = "consistent"
    emotion_intensity: float = 0.35
    chapters: list[ChapterInput]


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    voice_id: str | None = None
    speed: float | None = None
    preset: str | None = None
    delivery_mode: str | None = None
    emotion_intensity: float | None = None
    chapters: list[ChapterInput] | None = None


class SegmentStyleRequest(BaseModel):
    style: str | None = None
    voice_id: str | None = None


class EmotionAnalyzeRequest(BaseModel):
    text: str = Field(min_length=1)
    delivery_mode: str = "conversational"
    voice_id: str | None = None


class BatchItemRequest(BaseModel):
    video_name: str = Field(min_length=1, max_length=120)
    script: str = Field(min_length=1)
    voice_id: str | None = None
    speed: float = 1.0
    preset: str = "turbo"
    delivery_mode: str = "consistent"


class BatchRequest(BaseModel):
    title: str = Field(default="Video voiceovers", min_length=1, max_length=80)
    items: list[BatchItemRequest] = Field(min_length=1, max_length=100)
    online_speech_consent: bool = False


class GeminiKeysRequest(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=20)


class VideoJobUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    voice_id: str | None = None
    speed: float | None = Field(default=None, ge=0.8, le=2.0)
    preset: str | None = None
    delivery_mode: str | None = None
    emotion_intensity: float | None = Field(default=None, ge=0, le=1)
    output_ratio: str | None = None
    frame_fill: str | None = None
    original_audio_volume: float | None = Field(default=None, ge=0, le=1)
    ducking: bool | None = None
    caption_burn: bool | None = None
    caption_size: str | None = None
    caption_position: str | None = None
    export_mode: str | None = None
    caption_style: dict | None = None
    caption_split: str | None = None
    mirror_horizontal: bool | None = None
    subtitle_blur: dict | None = None
    translation_mode: str | None = None


class VideoSegmentUpdate(BaseModel):
    translation: str | None = Field(default=None, max_length=5000)
    style: str | None = None
    source_clips: list[dict] | None = None
    deleted: bool | None = None
    pause_after_ms: int | None = Field(default=None, ge=0, le=5000)
    scene_speed: float | None = Field(default=None, ge=0.8, le=2.0)
    source_start: float | None = Field(default=None, ge=0)
    source_end: float | None = Field(default=None, gt=0)
    caption_only: bool | None = None
    caption_style_override: dict | None = None


class VideoSegmentCreate(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    translation: str = Field(min_length=1, max_length=5000)
    caption_only: bool = False
    style: str = "neutral"


class VideoExportRequest(BaseModel):
    mode: str | None = None
    speech_consent: bool = False


class SpeechConsentRequest(BaseModel):
    online_speech_consent: bool = False


class VideoEditPlan(BaseModel):
    scenes: list[dict]
    settings: dict = {}


class RedNoteJobRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    permission_confirmed: bool = False
    allow_browser_cookies: bool = False
    voice_id: str | None = None
    speed: float = Field(default=1.0, ge=.8, le=2.0)
    delivery_mode: str = "consistent"
    emotion_intensity: float = Field(default=.35, ge=0, le=1)
    best_quality: bool = True
    output_ratio: str = "original"
    frame_fill: str = "fit"
    original_audio_volume: float = Field(default=.12, ge=0, le=1)
    ducking: bool = True
    caption_burn: bool = False
    caption_size: str = "medium"
    caption_position: str = "bottom"
    caption_split: str = "short"
    mirror_horizontal: bool = True
    export_mode: str = "voiceover_captions"
    translation_mode: str = "manual"
    translation_consent: bool = False


class ManualSrtRequest(BaseModel):
    srt: str = Field(min_length=1, max_length=10_000_000)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/privacy", include_in_schema=False)
def privacy_page():
    return FileResponse(STATIC / "privacy.html")


@app.get("/terms", include_in_schema=False)
def terms_page():
    return FileResponse(STATIC / "terms.html")


@app.get("/cookies", include_in_schema=False)
def cookie_policy_page():
    return FileResponse(STATIC / "cookies.html")


@app.get("/api/status")
def status():
    return {
        **model.status(),
        "runtime": runtime.status(),
        "emotion_analyzer": analyzer.status(),
        "local_only": True,
        "source_media_local": True,
        "speech_online": True,
        "transcription_device": "CUDA when available, CPU fallback",
        "port": 7860,
    }


@app.post("/api/emotions/analyze")
def analyze_emotion(request: EmotionAnalyzeRequest):
    if request.delivery_mode not in DELIVERY_MODES:
        raise HTTPException(400, "Unknown delivery mode.")
    try:
        clean, _ = validate_long_form(request.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    recorded = {"neutral"}
    if request.voice_id:
        try:
            online_voice(request.voice_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    segments = analyzer.analyze_text(clean, request.delivery_mode)
    for item in segments:
        item["performance"] = "approximate"
        item["delivery_label"] = "Expressive delivery"
    return {"segments": segments, "analyzer": analyzer.status()}


@app.get("/api/setup")
def setup_status():
    return runtime.status()


@app.post("/api/setup", status_code=202)
def start_setup():
    return runtime.start()


@app.get("/api/voices")
def voices():
    available = model.state != "offline"
    return [{**voice, "built_in": True, "provider": "edge-online", "available": available,
             "expressive_delivery": True, "styles": list(STYLES)}
            for voice in ONLINE_VOICES.values()]


@app.post("/api/voices", status_code=201)
async def create_voice(
    name: str = Form(...), transcript: str = Form(...), audio: UploadFile = File(...)
):
    name = normalize_text(name)
    try:
        transcript, _ = validate_burmese(transcript, maximum=120)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not 1 <= len(name) <= 40:
        raise HTTPException(400, "Voice name must be 1–40 characters.")
    raw = await audio.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Reference WAV must be 20 MB or smaller.")
    try:
        with sf.SoundFile(io.BytesIO(raw)) as source:
            if source.format != "WAV":
                raise ValueError("not WAV")
            samples = source.read(dtype="float32", always_2d=True)
            sample_rate = source.samplerate
    except Exception as exc:
        raise HTTPException(400, "Reference audio must be a valid WAV file.") from exc
    duration = len(samples) / sample_rate
    if not 0.5 <= duration <= 15:
        raise HTTPException(400, "Reference audio must be between 0.5 and 15 seconds.")
    mono = samples.mean(axis=1)
    if sample_rate != 24_000:
        old_positions = np.linspace(0, 1, len(mono), endpoint=False)
        new_length = max(1, round(len(mono) * 24_000 / sample_rate))
        new_positions = np.linspace(0, 1, new_length, endpoint=False)
        mono = np.interp(new_positions, old_positions, mono).astype(np.float32)
    record, destination = library.create_voice(name, transcript, len(mono) / 24_000)
    try:
        sf.write(destination, mono, 24_000, subtype="PCM_16")
    except Exception:
        library.delete_voice(record["id"])
        raise
    return {**record, "built_in": False}


async def _normalized_wav(audio: UploadFile) -> tuple[np.ndarray, float]:
    raw = await audio.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Reference WAV must be 20 MB or smaller.")
    try:
        with sf.SoundFile(io.BytesIO(raw)) as source:
            if source.format != "WAV":
                raise ValueError("not WAV")
            samples = source.read(dtype="float32", always_2d=True)
            sample_rate = source.samplerate
    except Exception as exc:
        raise HTTPException(400, "Reference audio must be a valid WAV file.") from exc
    duration = len(samples) / sample_rate
    if not 0.5 <= duration <= 15:
        raise HTTPException(400, "Reference audio must be between 0.5 and 15 seconds.")
    mono = samples.mean(axis=1)
    if sample_rate != 24_000:
        old_positions = np.linspace(0, 1, len(mono), endpoint=False)
        new_length = max(1, round(len(mono) * 24_000 / sample_rate))
        new_positions = np.linspace(0, 1, new_length, endpoint=False)
        mono = np.interp(new_positions, old_positions, mono).astype(np.float32)
    return mono, len(mono) / 24_000


@app.get("/api/voices/{voice_id}/styles")
def voice_styles(voice_id: str):
    if not library.get_voice(voice_id):
        raise HTTPException(404, "Voice profile not found.")
    return library.list_voice_styles(voice_id)


@app.post("/api/voices/{voice_id}/styles", status_code=201)
async def save_voice_style(
    voice_id: str,
    style: str = Form(...),
    transcript: str = Form(...),
    audio: UploadFile = File(...),
):
    if not library.get_voice(voice_id):
        raise HTTPException(404, "Voice profile not found.")
    style = style.lower()
    if style not in STYLES:
        raise HTTPException(400, "Unknown voice style.")
    try:
        transcript, _ = validate_burmese(transcript, maximum=120)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    mono, duration = await _normalized_wav(audio)
    record, destination, old_filename = library.upsert_voice_style(
        voice_id, style, transcript, duration
    )
    try:
        sf.write(destination, mono, 24_000, subtype="PCM_16")
        if old_filename and old_filename != destination.name:
            (library.voices_dir / old_filename).unlink(missing_ok=True)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    record.pop("filename", None)
    record["audio_url"] = f"/api/voices/{voice_id}/styles/{style}/audio"
    return record


@app.get("/api/voices/{voice_id}/styles/{style}/audio")
def voice_style_audio(voice_id: str, style: str):
    record = library.get_voice_style(voice_id, style)
    if not record:
        raise HTTPException(404, "Voice style not found.")
    return FileResponse(library.voices_dir / record["filename"], media_type="audio/wav")


@app.delete("/api/voices/{voice_id}/styles/{style}", status_code=204)
def delete_voice_style(voice_id: str, style: str):
    try:
        deleted = library.delete_voice_style(voice_id, style)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Voice style not found.")


@app.delete("/api/voices/{voice_id}", status_code=204)
def delete_voice(voice_id: str):
    if not library.delete_voice(voice_id):
        raise HTTPException(404, "Voice profile not found.")


@app.post("/api/jobs", status_code=202)
def create_job(request: JobRequest):
    if not request.online_speech_consent:
        raise HTTPException(400, "Confirm that your text and voice settings may be sent to the online speech provider.")
    try:
        return jobs.submit(
            request.text,
            request.voice_id,
            request.speed,
            request.preset,
            request.emotion_mode,
            request.style_override,
            request.emotion_intensity,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


@app.post("/api/subtitle-jobs", status_code=202)
def create_subtitle_job(request: SubtitleJobRequest):
    if not request.online_speech_consent:
        raise HTTPException(400, "Confirm that your subtitle text and voice settings may be sent to the online speech provider.")
    if request.emotion_mode not in ("auto", "off", "manual"):
        raise HTTPException(400, "Unknown emotion mode.")
    if request.style_override and request.style_override not in STYLES:
        raise HTTPException(400, "Unknown expressive delivery style.")
    try:
        return subtitle_queue.submit(request.srt, request.voice_id, request.speed,
                                     request.emotion_mode, request.style_override,
                                     request.emotion_intensity)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/subtitle-jobs/{job_id}")
def get_subtitle_job(job_id: str):
    job = subtitle_queue.get(job_id)
    if not job:
        raise HTTPException(404, "Subtitle job not found.")
    return job


@app.get("/api/subtitle-jobs/{job_id}/srt")
def subtitle_job_srt(job_id: str):
    if not subtitle_queue.get(job_id):
        raise HTTPException(404, "Subtitle job not found.")
    path = subtitle_queue.srt_path(job_id)
    if not path.exists():
        raise HTTPException(404, "SRT is not ready.")
    return FileResponse(path, media_type="application/x-subrip", filename="subtitles.srt")


@app.get("/api/generations")
def generations():
    return library.list_generations()


@app.get("/api/generations/{generation_id}/audio")
def generation_audio(generation_id: str):
    generation = library.get_generation(generation_id)
    if not generation:
        raise HTTPException(404, "Generation not found.")
    path = library.generations_dir / generation["filename"]
    if not path.exists():
        raise HTTPException(404, "Audio file not found.")
    return FileResponse(path, media_type="audio/wav", filename=f"burmese-{generation_id[:8]}.wav")


@app.delete("/api/generations/{generation_id}", status_code=204)
def delete_generation(generation_id: str):
    if not library.delete_generation(generation_id):
        raise HTTPException(404, "Generation not found.")


def _validated_chapters(chapters: list[ChapterInput]) -> list[dict]:
    if not chapters:
        raise HTTPException(400, "At least one chapter is required.")
    result: list[dict] = []
    total = 0
    for index, chapter in enumerate(chapters):
        if chapter.voice_id and not library.get_voice(chapter.voice_id):
            raise HTTPException(400, f"Chapter {index + 1}: saved voice was not found.")
        try:
            text, count = validate_long_form(chapter.text)
        except ValueError as exc:
            raise HTTPException(400, f"Chapter {index + 1}: {exc}") from exc
        total += count
        result.append(
            {
                "title": normalize_text(chapter.title)[:80] or f"Chapter {index + 1}",
                "text": text,
                "syllables": count,
                "voice_id": chapter.voice_id,
            }
        )
    if total > LONG_FORM_MAXIMUM:
        raise HTTPException(400, f"Project is too long ({total}/{LONG_FORM_MAXIMUM} syllables).")
    return result


def _validate_project_options(
    voice_id: str | None, speed: float, preset: str, delivery_mode: str, emotion_intensity: float
) -> None:
    if voice_id and not library.get_voice(voice_id):
        raise HTTPException(400, "Saved voice was not found.")
    if not 0.8 <= speed <= 2.0:
        raise HTTPException(400, "Speed must be between 0.8 and 2.0.")
    if preset not in ("turbo", "fast", "balanced", "high"):
        raise HTTPException(400, "Unknown quality preset.")
    if delivery_mode not in DELIVERY_MODES:
        raise HTTPException(400, "Unknown delivery mode.")
    if not 0 <= emotion_intensity <= 1:
        raise HTTPException(400, "Emotion intensity must be between 0 and 1.")


@app.get("/api/projects")
def projects():
    return library.list_projects()


@app.post("/api/projects", status_code=201)
def create_project(request: ProjectRequest):
    _validate_project_options(
        request.voice_id, request.speed, request.preset, request.delivery_mode, request.emotion_intensity
    )
    return library.create_project(
        normalize_text(request.title),
        request.voice_id,
        request.speed,
        request.preset,
        request.delivery_mode,
        request.emotion_intensity,
        _validated_chapters(request.chapters),
    )


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    project = library.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found.")
    return project


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, request: ProjectUpdate):
    current = library.get_project(project_id)
    if not current:
        raise HTTPException(404, "Project not found.")
    values = request.model_dump(exclude_none=True, exclude={"chapters"})
    if "voice_id" in request.model_fields_set:
        values["voice_id"] = request.voice_id
    voice_id = request.voice_id if "voice_id" in request.model_fields_set else current["voice_id"]
    speed = request.speed if request.speed is not None else current["speed"]
    preset = request.preset or current["preset"]
    delivery = request.delivery_mode or current["delivery_mode"]
    intensity = request.emotion_intensity if request.emotion_intensity is not None else current["emotion_intensity"]
    _validate_project_options(voice_id, speed, preset, delivery, intensity)
    chapters = _validated_chapters(request.chapters) if request.chapters is not None else None
    return library.update_project(project_id, values, chapters)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    project = library.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found.")
    if any(
        item["state"] in ("queued", "rendering", "pausing", "finalizing", "cancelling")
        for item in project["renders"]
    ):
        raise HTTPException(409, "Cancel the active render before deleting this project.")
    library.delete_project(project_id)


@app.post("/api/projects/{project_id}/import", status_code=200)
async def import_project_text(project_id: str, file: UploadFile = File(...)):
    if not library.get_project(project_id):
        raise HTTPException(404, "Project not found.")
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "TXT file must be 2 MB or smaller.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "TXT file must use UTF-8 encoding.") from exc
    chapter = ChapterInput(title=Path(file.filename or "Imported script").stem[:80], text=text)
    return library.update_project(project_id, {}, _validated_chapters([chapter]))


@app.post("/api/projects/{project_id}/analyze")
def analyze_project(project_id: str):
    try:
        segments = project_queue.analyze(project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"segments": segments, "analyzer": analyzer.status()}


@app.patch("/api/projects/{project_id}/segments/{segment_id}")
def update_segment(project_id: str, segment_id: str, request: SegmentStyleRequest):
    if request.style is not None and request.style not in STYLES:
        raise HTTPException(400, "Unknown voice style.")
    if "voice_id" in request.model_fields_set and request.voice_id and not library.get_voice(request.voice_id):
        raise HTTPException(400, "Saved voice was not found.")
    segment = library.update_segment(
        project_id, segment_id,
        style_marker="style" in request.model_fields_set, style=request.style,
        voice_marker="voice_id" in request.model_fields_set, voice_id=request.voice_id,
    )
    if not segment:
        raise HTTPException(404, "Performance segment not found.")
    return segment


@app.post("/api/projects/{project_id}/renders", status_code=202)
def start_project_render(project_id: str, request: SpeechConsentRequest):
    if not request.online_speech_consent:
        raise HTTPException(400, "Confirm that your script and voice settings may be sent to the online speech provider.")
    try:
        return project_queue.start(project_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/renders/{render_id}")
def get_render(render_id: str):
    render = library.get_render(render_id)
    if not render:
        raise HTTPException(404, "Render not found.")
    return render


@app.post("/api/renders/{render_id}/{action}", status_code=202)
def control_render(render_id: str, action: str):
    actions = {
        "pause": project_queue.pause,
        "resume": project_queue.resume,
        "cancel": project_queue.cancel,
        "retry": project_queue.retry,
    }
    if action not in actions:
        raise HTTPException(404, "Unknown render action.")
    try:
        return actions[action](render_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/renders/{render_id}/audio")
def render_audio(render_id: str):
    render = library.get_render(render_id)
    if not render or render["state"] != "succeeded":
        raise HTTPException(404, "Completed render not found.")
    path = library.render_path(render) / "complete.wav"
    return FileResponse(path, media_type="audio/wav", filename=f"narration-{render_id[:8]}.wav")


@app.get("/api/renders/{render_id}/chapters/{chapter_position}/audio")
def render_chapter_audio(render_id: str, chapter_position: int):
    render = library.get_render(render_id)
    if not render or render["state"] != "succeeded":
        raise HTTPException(404, "Completed render not found.")
    path = library.render_path(render) / "chapters" / f"{chapter_position:03d}.wav"
    if not path.exists():
        raise HTTPException(404, "Chapter audio not found.")
    return FileResponse(path, media_type="audio/wav", filename=f"chapter-{chapter_position + 1}.wav")


@app.get("/api/batches")
def list_batches():
    return library.list_batches()


@app.post("/api/batches", status_code=202)
def create_batch(request: BatchRequest):
    if not request.online_speech_consent:
        raise HTTPException(400, "Confirm that your scripts and voice settings may be sent to the online speech provider.")
    items: list[dict] = []
    for index, item in enumerate(request.items):
        _validate_project_options(item.voice_id, item.speed, item.preset, item.delivery_mode, 0.45)
        try:
            script, _ = validate_long_form(item.script)
        except ValueError as exc:
            raise HTTPException(400, f"Video {index + 1}: {exc}") from exc
        items.append({
            "video_name": normalize_text(item.video_name), "script": script,
            "voice_id": item.voice_id, "speed": item.speed, "preset": item.preset,
            "delivery_mode": item.delivery_mode,
        })
    batch = library.create_batch(normalize_text(request.title), items)
    return batch_queue.start(batch["id"])


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    batch = library.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found.")
    for item in batch["items"]:
        if item.get("render_id"):
            render = library.get_render(item["render_id"])
            item["render"] = render
            if render and render["state"] == "succeeded":
                item["audio_url"] = f"/api/batches/{batch_id}/items/{item['id']}/audio"
    return batch


@app.post("/api/batches/{batch_id}/{action}", status_code=202)
def control_batch(batch_id: str, action: str):
    actions = {"pause": batch_queue.pause, "resume": batch_queue.resume,
               "cancel": batch_queue.cancel, "retry": batch_queue.retry}
    if action not in actions:
        raise HTTPException(404, "Unknown batch action.")
    try:
        return actions[action](batch_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/batches/{batch_id}", status_code=204)
def delete_batch(batch_id: str):
    batch = library.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found.")
    if batch["state"] in ("queued", "running", "pausing", "cancelling"):
        raise HTTPException(409, "Pause or cancel this batch before deleting it.")
    library.delete_batch(batch_id)


@app.get("/api/batches/{batch_id}/items/{item_id}/audio")
def batch_item_audio(batch_id: str, item_id: str):
    batch = library.get_batch(batch_id)
    item = next((row for row in batch["items"] if row["id"] == item_id), None) if batch else None
    render = library.get_render(item["render_id"]) if item and item.get("render_id") else None
    if not render or render["state"] != "succeeded":
        raise HTTPException(404, "Completed video voiceover not found.")
    path = library.render_path(render) / "complete.wav"
    safe = re.sub(r"[^\w\-. ]+", "", item["video_name"], flags=re.UNICODE).strip() or item_id[:8]
    return FileResponse(path, media_type="audio/wav", filename=f"{safe}.wav")


@app.get("/api/batches/{batch_id}/download")
def batch_download(batch_id: str):
    batch = library.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found.")
    completed = [item for item in batch["items"] if item["state"] == "succeeded" and item.get("render_id")]
    if not completed:
        raise HTTPException(404, "No completed voiceovers to download.")
    folder = library.data / "batches"
    folder.mkdir(exist_ok=True)
    path = folder / f"{batch_id}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for number, item in enumerate(completed, 1):
            render = library.get_render(item["render_id"])
            if render:
                source = library.render_path(render) / "complete.wav"
                if source.exists():
                    safe = re.sub(r"[^\w\-. ]+", "", item["video_name"], flags=re.UNICODE).strip() or f"video-{number}"
                    archive.write(source, f"{number:02d}-{safe}.wav")
    return FileResponse(path, media_type="application/zip", filename=f"{batch['title']}.zip")


# --- English video → Burmese voice-over ------------------------------------

@app.get("/api/settings/gemini")
def gemini_status():
    try:
        return key_store.status()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc


@app.put("/api/settings/gemini")
def save_gemini_keys(request: GeminiKeysRequest):
    try:
        count = key_store.save(request.keys)
        return {**key_store.status(), "message": f"{count} encrypted key(s) saved for this Windows user."}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/settings/gemini/test")
def test_gemini_keys():
    try:
        return video_queue.translator.test()
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.delete("/api/settings/gemini", status_code=204)
def delete_gemini_keys():
    key_store.clear()


@app.get("/api/video-jobs")
def list_video_jobs():
    return video_library.list()


@app.post("/api/video-jobs", status_code=202)
async def create_video_jobs(files: list[UploadFile] = File(...), settings: str = Form("{}")):
    if not files or len(files) > 30:
        raise HTTPException(400, "Choose between 1 and 30 videos.")
    try:
        options = json.loads(settings)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Video settings are invalid.") from exc
    if options.get("translation_mode") == "gemini" and not options.get("translation_consent"):
        raise HTTPException(400, "Confirm that the source transcript may be sent to Google Gemini.")
    voice_id = options.get("voice_id") or DEFAULT_VOICE
    try:
        online_voice(voice_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    options = {
        "voice_id": voice_id,
        "speed": min(2.0, max(0.8, float(options.get("speed", 1.0)))),
        "preset": options.get("preset") if options.get("preset") in ("turbo", "fast", "balanced", "high") else "turbo",
        "delivery_mode": options.get("delivery_mode") if options.get("delivery_mode") in DELIVERY_MODES else "consistent",
        "emotion_intensity": min(1.0, max(0.0, float(options.get("emotion_intensity", .35)))),
        "output_ratio": options.get("output_ratio") if options.get("output_ratio") in ("original", "16:9", "9:16", "1:1") else "original",
        "frame_fill": options.get("frame_fill") if options.get("frame_fill") in ("fit", "fill") else "fit",
        "transcription_device": "cuda",
        "original_audio_volume": min(1.0, max(0.0, float(options.get("original_audio_volume", .12)))),
        "ducking": bool(options.get("ducking", True)),
        "caption_burn": bool(options.get("caption_burn", False)),
        "caption_size": options.get("caption_size") if options.get("caption_size") in ("small", "medium", "large") else "medium",
        "caption_position": options.get("caption_position") if options.get("caption_position") in ("bottom", "upper") else "bottom",
        "export_mode": options.get("export_mode") if options.get("export_mode") in ("voiceover_captions", "voiceover_only", "subtitles_only") else "voiceover_captions",
        "caption_style": options.get("caption_style") if isinstance(options.get("caption_style"), dict) else {
            "font": "Noto Sans Myanmar", "size": 44, "color": "#FFFFFF", "position": "lower", "x": .5, "y": .9,
        },
        "caption_split": options.get("caption_split") if options.get("caption_split") in ("short", "balanced", "full") else "short",
        "mirror_horizontal": bool(options.get("mirror_horizontal", True)),
        "edit_revision": 0,
        "translation_mode": options.get("translation_mode") if options.get("translation_mode") in ("manual", "gemini") else "manual",
    }
    created = []
    allowed = {".mp4", ".mov", ".mkv", ".webm"}
    for upload in files:
        suffix = Path(upload.filename or "video.mp4").suffix.lower()
        if suffix not in allowed:
            raise HTTPException(400, f"{upload.filename}: choose MP4, MOV, MKV, or WebM.")
        job, destination = video_library.create(upload.filename or "video.mp4", suffix, options)
        total = 0
        try:
            with destination.open("wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > 10 * 1024 * 1024 * 1024:
                        raise HTTPException(413, "Each video must be 10 GB or smaller.")
                    stream.write(chunk)
            info = probe_video(destination)
            job = video_library.update(job["id"], **info)
            created.append(video_queue.schedule(job["id"], "prepare"))
        except Exception:
            video_library.delete(job["id"])
            raise
    return {"jobs": created}


@app.post("/api/video-jobs/from-url", status_code=202)
def create_rednote_video_job(request: RedNoteJobRequest):
    if not request.permission_confirmed:
        raise HTTPException(400, "Confirm that you have permission to download and repurpose this video.")
    try:
        url, identity = validate_rednote_url(request.url)
        voice_id = request.voice_id or DEFAULT_VOICE
        online_voice(voice_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if request.delivery_mode not in DELIVERY_MODES:
        raise HTTPException(400, "Unknown delivery mode.")
    if request.output_ratio not in ("original", "16:9", "9:16", "1:1") or request.frame_fill not in ("fit", "fill"):
        raise HTTPException(400, "Unknown video framing option.")
    if request.caption_size not in ("small", "medium", "large") or request.caption_position not in ("bottom", "upper"):
        raise HTTPException(400, "Unknown caption option.")
    options = {
        "voice_id": voice_id, "speed": request.speed, "preset": "balanced",
        "delivery_mode": request.delivery_mode, "emotion_intensity": request.emotion_intensity,
        "best_quality": request.best_quality, "output_ratio": request.output_ratio,
        "frame_fill": request.frame_fill, "transcription_device": "cuda",
        "original_audio_volume": request.original_audio_volume, "ducking": request.ducking,
        "caption_burn": request.caption_burn, "caption_size": request.caption_size,
        "caption_position": request.caption_position, "edit_revision": 0,
        "export_mode": request.export_mode if request.export_mode in ("voiceover_captions", "voiceover_only", "subtitles_only") else "voiceover_captions",
        "caption_style": {"font": "Noto Sans Myanmar", "size": 44, "color": "#FFFFFF", "position": "lower", "x": .5, "y": .9},
        "caption_split": request.caption_split if request.caption_split in ("short", "balanced", "full") else "short",
        "mirror_horizontal": request.mirror_horizontal,
        "translation_mode": request.translation_mode,
        "allow_browser_cookies": request.allow_browser_cookies,
    }
    if request.translation_mode not in ("manual", "gemini"):
        raise HTTPException(400, "Unknown translation mode.")
    if request.translation_mode == "gemini" and not request.translation_consent:
        raise HTTPException(400, "Confirm that the source transcript may be sent to Google Gemini.")
    job, _ = video_library.create(
        f"RedNote-{identity['content_id']}.mp4", ".mp4", options,
        source_kind="rednote", source_identity=json.dumps(identity, ensure_ascii=False),
    )
    try:
        video_queue.remote_sources.save(job["id"], url)
        return video_queue.schedule(job["id"], "download")
    except Exception:
        video_queue.remote_sources.clear(job["id"])
        video_library.delete(job["id"])
        raise


@app.get("/api/video-jobs/{job_id}")
def get_video_job(job_id: str):
    job = video_library.get(job_id, include_segments=True)
    if not job:
        raise HTTPException(404, "Video job not found.")
    return job


@app.patch("/api/video-jobs/{job_id}")
def update_video_job(job_id: str, request: VideoJobUpdate):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    values = request.model_dump(exclude_none=True)
    title = values.pop("title", None)
    subtitle_blur = values.pop("subtitle_blur", None)
    settings = dict(job["settings"])
    voice_changed = False
    if "voice_id" in request.model_fields_set:
        try:
            online_voice(request.voice_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        selected = request.voice_id or DEFAULT_VOICE
        voice_changed = selected != settings.get("voice_id")
        settings["voice_id"] = selected
    for field in ("speed", "preset", "delivery_mode", "emotion_intensity", "output_ratio", "frame_fill",
                  "original_audio_volume", "ducking", "caption_burn", "caption_size", "caption_position",
                  "mirror_horizontal"):
        if field in values:
            settings[field] = values[field]
    settings["preset"] = "balanced"
    if settings.get("delivery_mode") not in DELIVERY_MODES:
        raise HTTPException(400, "Unknown delivery mode.")
    if settings.get("output_ratio") not in ("original", "16:9", "9:16", "1:1"):
        raise HTTPException(400, "Unknown output ratio.")
    if voice_changed:
        video_library.mark_audio_dirty(job_id)
    updates = {"settings": settings, **({"title": title} if title else {})}
    if subtitle_blur is not None:
        updates["subtitle_blur"] = subtitle_blur
    return video_library.update(job_id, **updates)


@app.post("/api/video-jobs/{job_id}/translation/manual")
def switch_video_to_manual_translation(job_id: str):
    try:
        return video_queue.await_manual_translation(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.put("/api/video-jobs/{job_id}/translation/srt")
def import_video_manual_translation(job_id: str, request: ManualSrtRequest):
    try:
        return video_queue.import_manual_translation(job_id, request.srt)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/video-jobs/{job_id}", status_code=204)
def delete_video_job(job_id: str):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    if job["state"] in ("queued", "downloading", "transcribing", "translating", "synthesizing", "syncing", "rendering"):
        raise HTTPException(409, "Pause or cancel this job before deleting it.")
    video_queue.remote_sources.clear(job_id)
    video_library.delete(job_id)


@app.post("/api/video-jobs/{job_id}/prepare", status_code=202)
def prepare_video_job(job_id: str):
    try:
        return video_queue.schedule(job_id, "prepare")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/video-jobs/{job_id}/render", status_code=202)
def render_video_job(job_id: str, request: SpeechConsentRequest | None = None):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    export_mode = (job.get("settings") or {}).get("export_mode", "voiceover_captions")
    if export_mode != "subtitles_only" and (request is None or not request.online_speech_consent):
        raise HTTPException(400, "Confirm online speech processing before exporting with voice-over.")
    try:
        return video_queue.schedule(job_id, "render")
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/video-jobs/{job_id}/export", status_code=202)
def export_video_job(job_id: str, request: VideoExportRequest | None = None):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    settings = dict(job["settings"])
    mode = (request.mode if request else None) or settings.get("export_mode", "voiceover_captions")
    if mode not in ("voiceover_captions", "voiceover_only", "subtitles_only"):
        raise HTTPException(400, "Unknown export mode.")
    if mode != "subtitles_only" and (request is None or not request.speech_consent):
        raise HTTPException(400, "Confirm online speech processing before exporting with voice-over.")
    settings["export_mode"] = mode
    settings["caption_burn"] = mode != "voiceover_only"
    video_library.update(job_id, settings=settings)
    return render_video_job(job_id, SpeechConsentRequest(online_speech_consent=bool(request and request.speech_consent)))


@app.get("/api/video-jobs/{job_id}/exports/current")
def current_video_export(job_id: str):
    if not video_library.get(job_id):
        raise HTTPException(404, "Video job not found.")
    return video_library.current_export(job_id) or {
        "state": "not_started", "stage": "review", "progress": 0, "total": 0,
        "message": "Choose an export mode when your edits are ready.", "chunks": [],
    }


@app.put("/api/video-jobs/{job_id}/edit-plan")
def save_video_edit_plan(job_id: str, request: VideoEditPlan):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    settings = dict(job["settings"])
    previous_voice = settings.get("voice_id")
    blur = request.settings.get("subtitle_blur")
    settings.update({key: value for key, value in request.settings.items() if key in {
        "voice_id", "speed", "original_audio_volume", "ducking", "caption_burn", "caption_size", "caption_position",
        "export_mode", "caption_style", "caption_split", "mirror_horizontal"
    }})
    settings["edit_revision"] = int(settings.get("edit_revision", 0)) + 1
    try:
        online_voice(settings.get("voice_id"))
        result = video_library.save_edit_plan(job_id, request.scenes, settings)
        if previous_voice != settings.get("voice_id"):
            video_library.mark_audio_dirty(job_id)
        if blur is not None:
            result = video_library.update(job_id, subtitle_blur=blur)
        return video_library.get(job_id, include_segments=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/video-jobs/{job_id}/detect-subtitle-region")
def redetect_video_subtitle_region(job_id: str):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    source = video_library.folder(job_id) / job["source_filename"]
    if not source.exists():
        raise HTTPException(409, "The source video is not ready yet.")
    proposal = detect_subtitle_region(source)
    video_library.update(job_id, subtitle_blur=proposal)
    return proposal


@app.post("/api/video-jobs/{job_id}/edit")
def edit_completed_video(job_id: str):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    if not video_library.segments(job_id):
        raise HTTPException(400, "This video has no editable scenes.")
    return video_library.update(job_id, state="review_ready", stage="review",
                                message="Editing reopened — the current export stays available until replacement succeeds.")


@app.post("/api/video-jobs/{job_id}/segments/{segment_id}/preview")
def preview_video_segment(job_id: str, segment_id: str, request: SpeechConsentRequest):
    if not request.online_speech_consent:
        raise HTTPException(400, "Confirm online speech processing before generating a scene preview.")
    try:
        return video_queue.synthesize_segment(job_id, segment_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/video-jobs/{job_id}/segments/{segment_id}/audio")
def video_segment_audio(job_id: str, segment_id: str):
    segment = next((s for s in video_library.segments(job_id) if s["id"] == segment_id), None)
    if not segment or not segment.get("audio_filename"):
        raise HTTPException(404, "Scene audio is not ready.")
    path = video_library.folder(job_id) / "audio-clips" / segment["audio_filename"]
    if not path.exists():
        raise HTTPException(404, "Scene audio is not ready.")
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/video-jobs/{job_id}/segments/{segment_id}/duplicate", status_code=201)
def duplicate_video_segment(job_id: str, segment_id: str):
    segment = video_library.duplicate_segment(job_id, segment_id)
    if not segment:
        raise HTTPException(404, "Scene not found.")
    return segment


@app.post("/api/video-jobs/{job_id}/segments", status_code=201)
def create_video_segment(job_id: str, request: VideoSegmentCreate):
    if request.style not in STYLES:
        raise HTTPException(400, "Unknown voice style.")
    try:
        return video_library.create_segment(job_id, start=request.start, end=request.end,
                                            translation=request.translation, caption_only=request.caption_only,
                                            style=request.style)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/video-jobs/{job_id}/preview", status_code=202)
def preview_video_job(job_id: str, request: SpeechConsentRequest):
    if not request.online_speech_consent:
        raise HTTPException(400, "Confirm online speech processing before generating video narration.")
    try:
        return video_queue.schedule(job_id, "render")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/video-jobs/{job_id}/{action}", status_code=202)
def control_video_job(job_id: str, action: str):
    try:
        return video_queue.action(job_id, action)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/video-jobs/{job_id}/segments/{segment_id}")
def update_video_segment(job_id: str, segment_id: str, request: VideoSegmentUpdate):
    values = request.model_dump(exclude_none=True)
    if "translation" in values and not values["translation"].strip():
        raise HTTPException(400, "Burmese translation cannot be blank.")
    if "style" in values and values["style"] not in STYLES:
        raise HTTPException(400, "Unknown voice style.")
    existing = next((item for item in video_library.segments(job_id) if item["id"] == segment_id), None)
    if not existing:
        raise HTTPException(404, "Video segment not found.")
    if any(key in values for key in ("source_start", "source_end")):
        if not existing.get("is_user_added"):
            raise HTTPException(400, "Only added subtitle cues can change authoritative cue timing.")
        start = float(values.get("source_start", existing["source_start"]))
        end = float(values.get("source_end", existing["source_end"]))
        duration = float((video_library.get(job_id) or {}).get("duration") or 0)
        if end <= start or (duration and end > duration + .001):
            raise HTTPException(400, "Choose a valid cue range inside the source video.")
    else:
        start, end = float(existing["source_start"]), float(existing["source_end"])
    caption_only = bool(values.get("caption_only", existing.get("caption_only")))
    if existing.get("is_user_added") and not caption_only:
        for other in video_library.segments(job_id):
            if other["id"] == segment_id or other.get("deleted") or other.get("caption_only"):
                continue
            if max(start, float(other["source_start"])) < min(end, float(other["source_end"])) - .001:
                raise HTTPException(400, "Narrated cues cannot overlap. Adjust the timing or make this caption-only.")
    segment = video_library.update_segment(job_id, segment_id, values)
    if not segment:
        raise HTTPException(404, "Video segment not found.")
    return segment


@app.get("/api/video-jobs/{job_id}/source")
def video_source(job_id: str):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    path = video_library.folder(job_id) / job["source_filename"]
    return FileResponse(path, filename=job["original_filename"])


@app.get("/api/video-jobs/{job_id}/artifacts/{kind}")
def video_artifact(job_id: str, kind: str):
    job = video_library.get(job_id)
    if not job:
        raise HTTPException(404, "Video job not found.")
    names = {"video": "burmese-dub.mp4", "audio": "narration.wav", "english": "english.txt",
             "source-srt": "source.srt",
             "burmese": "burmese.txt", "srt": "burmese.srt", "plan": "sync-plan.json"}
    if kind not in names:
        raise HTTPException(404, "Unknown artifact.")
    path = video_library.folder(job_id) / names[kind]
    if kind == "source-srt" and not path.exists():
        segments = video_library.segments(job_id)
        if segments:
            write_source_artifacts(video_library.folder(job_id), segments)
    if not path.exists():
        raise HTTPException(404, "Artifact is not ready.")
    media = {"video": "video/mp4", "audio": "audio/wav", "srt": "application/x-subrip", "source-srt": "application/x-subrip",
             "plan": "application/json"}.get(kind, "text/plain")
    return FileResponse(path, media_type=media, filename=f"{job['title']}-{names[kind]}")
