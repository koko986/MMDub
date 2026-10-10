from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .emotion import EmotionAnalyzer, STYLES, prosody_for
from .engine import DEFAULT_VOICE, ModelManager, SAMPLE_RATE, online_voice, split_online_text
from .rednote import RedNoteDownloader, find_subtitle_file, parse_subtitles
from .secrets import GeminiKeyStore, RemoteSourceStore
from .storage import Library
from .text import split_passage, syllable_count
from .video_storage import VideoLibrary
from .whisper_worker import PersistentWhisper


MODEL_CHAIN = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite")
TERMINAL_STATES = {"review_ready", "succeeded", "failed", "paused", "cancelled", "interrupted"}
MYANMAR_TEXT = re.compile(r"[\u1000-\u109f\uaa60-\uaa7f]")
EXPORT_MODES = {"voiceover_captions", "voiceover_only", "subtitles_only"}
CAPTION_FONTS = {
    "Noto Sans Myanmar", "Padauk", "Pyidaungsu", "TharLon", "Myanmar Sans Pro",
}
CAPTION_SPLIT_LIMITS = {"short": 12, "balanced": 20, "full": 50_000}
FONT_DIRECTORY = Path(__file__).resolve().parent.parent / "static" / "fonts"


def friendly_video_failure(exc: Exception, task: str) -> tuple[str, str]:
    """Turn implementation errors into a useful recovery message while retaining diagnostics on disk."""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        lowered = stderr.lower()
        if "invalid luma_param radius" in lowered or ("boxblur" in lowered and "invalid argument" in lowered):
            return (
                "Subtitle blur was stronger than this video's selected region allows.",
                "The blur is now automatically limited to a safe value. Choose Retry / Resume; saved narration will be reused.",
            )
        if "no space left on device" in lowered:
            return ("The export drive does not have enough free space.",
                    "Free some disk space, then choose Retry / Resume. Existing narration and scene work are saved.")
        if "subtitles" in lowered and ("unable to open" in lowered or "error" in lowered):
            return ("Burmese captions could not be added to the video.",
                    "Check that the SRT is still available, then choose Retry / Resume. The video and narration are safe.")
        meaningful = next((line.strip() for line in reversed(stderr.splitlines())
                           if line.strip() and not line.startswith("frame=")), "FFmpeg returned an unknown error.")
        label = "Video export" if task == "render" else task.replace("_", " ").title()
        return (f"{label} could not finish.",
                f"Your completed work is saved. Choose Retry / Resume. Technical detail: {meaningful[:260]}")
    message = str(exc).strip() or type(exc).__name__
    return ("This step could not finish.", f"{message} Your saved work is safe; choose Retry / Resume.")


def safe_blur_radius(requested: int, frame_width: float, frame_height: float,
                     region_width: float, region_height: float) -> int:
    region_short_side = min(frame_width * region_width, frame_height * region_height)
    # yuv420 chroma planes are half-sized, and boxblur applies the same radius
    # to luma and chroma, so one quarter of the full-resolution side is safe.
    maximum = max(1, int(region_short_side / 4) - 1)
    return min(40, max(2, int(requested)), maximum)


def _srt_seconds(value: str) -> float:
    match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*", value)
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_manual_translation_srt(content: str) -> list[dict]:
    """Strict parser that preserves the synchronization-contract cue numbers."""
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise ValueError("Paste or choose a Burmese SRT file first.")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.rstrip() for line in block.splitlines()]
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError("Each SRT cue must start with its original cue number.")
        timing = re.fullmatch(r"\s*(.*?)\s*-->\s*(.*?)\s*", lines[1])
        if not timing:
            raise ValueError(f"Cue {lines[0].strip()} is missing a valid timestamp line.")
        text = "\n".join(lines[2:]).strip()
        if not text:
            raise ValueError(f"Cue {lines[0].strip()} has blank translation text.")
        cues.append({"index": int(lines[0]), "start": _srt_seconds(timing.group(1)),
                     "end": _srt_seconds(timing.group(2)), "text": text})
    indices = [cue["index"] for cue in cues]
    if len(indices) != len(set(indices)):
        raise ValueError("The imported SRT contains duplicated cue numbers.")
    if indices != list(range(1, len(cues) + 1)):
        raise ValueError("Cue numbers are missing or reordered. Keep the source SRT order unchanged.")
    if not any(MYANMAR_TEXT.search(cue["text"]) for cue in cues):
        raise ValueError("The imported SRT does not appear to contain Burmese text.")
    return cues


def validate_manual_translation(cues: list[dict], segments: list[dict]) -> tuple[list[str], str]:
    if len(cues) != len(segments):
        raise ValueError(f"Expected {len(segments)} cues, but the imported SRT has {len(cues)}.")
    changed = sum(
        abs(cue["start"] - float(segment["source_start"])) > .05
        or abs(cue["end"] - float(segment["source_end"])) > .05
        for cue, segment in zip(cues, segments)
    )
    warning = ""
    if changed:
        warning = f"{changed} imported timestamp(s) differed; the original source timing was kept."
    return [cue["text"] for cue in cues], warning


def ffmpeg_path() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def atempo_chain(ratio: float) -> str:
    factors = []
    ratio = max(.05, ratio)
    while ratio > 2:
        factors.append(2.0)
        ratio /= 2
    while ratio < .5:
        factors.append(.5)
        ratio /= .5
    factors.append(ratio)
    return ",".join(f"atempo={factor:.8f}" for factor in factors)


def _stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _caption_style(settings: dict, override: dict | None = None) -> dict:
    raw = dict(settings.get("caption_style") or {})
    raw.update(override or {})
    font = raw.get("font") if raw.get("font") in CAPTION_FONTS else "Noto Sans Myanmar"
    try:
        size = min(120, max(20, int(raw.get("size", 44))))
        x = min(.95, max(.05, float(raw.get("x", .5))))
        y = min(.95, max(.05, float(raw.get("y", .9))))
    except (TypeError, ValueError):
        size, x, y = 44, .5, .9
    position = raw.get("position") if raw.get("position") in ("upper", "center", "lower", "custom") else "lower"
    if position != "custom":
        x, y = .5, {"upper": .12, "center": .5, "lower": .9}[position]
    color = str(raw.get("color") or "#FFFFFF").upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", color):
        color = "#FFFFFF"
    return {"font": font, "size": size, "color": color, "position": position, "x": x, "y": y}


def _ass_color(value: str) -> str:
    value = value.lstrip("#")
    return f"&H00{value[4:6]}{value[2:4]}{value[0:2]}"


def _ass_text(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _ass_time(value: float) -> str:
    centiseconds = max(0, int(round(value * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def split_caption_text(text: str, mode: str = "short", duration: float | None = None) -> list[str]:
    """Split Burmese display captions without changing the narration script."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    limit = CAPTION_SPLIT_LIMITS.get(mode, CAPTION_SPLIT_LIMITS["short"])
    if not clean or mode == "full":
        return [clean] if clean else []
    try:
        parts = split_passage(clean, limit=limit)
    except ValueError:
        words = clean.split()
        parts, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > limit * 4:
                parts.append(current)
                current = word
            else:
                current = candidate
        if current:
            parts.append(current)
    if duration is not None:
        maximum_parts = max(1, int(max(.1, duration) / .85))
        while len(parts) > maximum_parts:
            pair = min(range(len(parts) - 1), key=lambda index: len(parts[index]) + len(parts[index + 1]))
            parts[pair:pair + 2] = [f"{parts[pair]} {parts[pair + 1]}".strip()]
    return parts or [clean]


def _caption_events(cues: list[dict], settings: dict) -> list[dict]:
    events: list[dict] = []
    mode = settings.get("caption_split") if settings.get("caption_split") in CAPTION_SPLIT_LIMITS else "short"
    for cue in cues:
        start = float(cue["narration_start"])
        duration = max(.1, float(cue["cue_duration"]))
        parts = split_caption_text(cue["translation"], mode, duration)
        weights = [max(1, syllable_count(part), len(re.sub(r"\s+", "", part)) // 3) for part in parts]
        total = sum(weights)
        elapsed = 0.0
        for index, (part, weight) in enumerate(zip(parts, weights)):
            part_start = start + duration * elapsed / total
            elapsed += weight
            part_end = start + duration if index == len(parts) - 1 else start + duration * elapsed / total
            events.append({**cue, "translation": part, "caption_start": part_start, "caption_end": part_end,
                           "caption_part": index + 1, "caption_parts": len(parts)})
    return events


def _write_ass(path: Path, cues: list[dict], settings: dict, width: int, height: int,
               offset: float = 0, limit: float | None = None) -> None:
    styles: dict[str, str] = {}
    events: list[str] = []
    for index, cue in enumerate(_caption_events(cues, settings)):
        start = float(cue["caption_start"]) - offset
        end = float(cue["caption_end"]) - offset
        if limit is not None and (end <= 0 or start >= limit):
            continue
        start, end = max(0, start), min(limit, end) if limit is not None else end
        style = _caption_style(settings, cue.get("caption_style_override"))
        name = f"Cue{index}"
        styles[name] = (f"Style: {name},{style['font']},{style['size']},{_ass_color(style['color'])},&H000000FF,&H00000000,&H80000000,"
                        "-1,0,0,0,100,100,0,0,1,3,1,2,24,24,24,1")
        x, y = round(style["x"] * width), round(style["y"] * height)
        text = "{\\pos(" + str(x) + "," + str(y) + ")}"
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{name},,0,0,0,,{text}{_ass_text(cue['translation'])}")
    header = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
              f"PlayResX: {width}\nPlayResY: {height}\n\n[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
              "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
              "Alignment, MarginL, MarginR, MarginV, Encoding\n")
    event_header = ("\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    path.write_text(header + "\n".join(styles.values()) + event_header + "\n".join(events) + "\n", encoding="utf-8-sig")


def _has_audio_stream(path: Path) -> bool:
    import av
    with av.open(str(path)) as container:
        return any(stream.type == "audio" for stream in container.streams)


def probe_video(path: Path) -> dict:
    import av

    with av.open(str(path)) as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("The file does not contain a video stream.")
        duration = float(container.duration / av.time_base) if container.duration else 0.0
        if not duration and stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        fps = float(stream.average_rate) if stream.average_rate else 30.0
        return {"duration": duration, "width": stream.width, "height": stream.height, "fps": fps}


def ensure_browser_video(path: Path) -> Path:
    """Remux H.264 when possible; transcode only codecs browsers cannot preview reliably."""
    import av

    with av.open(str(path)) as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        codec = (stream.codec_context.name if stream else "").lower()
    if path.suffix.lower() == ".mp4" and codec in ("h264", "avc1"):
        return path
    target = path.with_name("source.mp4")
    temporary = path.with_name(".source-browser.mp4")
    command = [ffmpeg_path(), "-y", "-i", str(path)]
    if codec in ("h264", "avc1"):
        command += ["-c", "copy", "-movflags", "+faststart", str(temporary)]
        process = subprocess.run(command, capture_output=True)
        if process.returncode:
            command = [ffmpeg_path(), "-y", "-i", str(path), "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(temporary)]
            subprocess.run(command, check=True, capture_output=True)
    else:
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(temporary)]
        subprocess.run(command, check=True, capture_output=True)
    os.replace(temporary, target)
    if path.resolve() != target.resolve():
        path.unlink(missing_ok=True)
    return target


def detect_subtitle_region(path: Path) -> dict:
    """Propose a stable high-contrast subtitle band using sparse frame samples."""
    try:
        import cv2
    except ImportError:
        return {"enabled": False, "confidence": 0.0, "status": "detector_unavailable",
                "x": .08, "y": .72, "width": .84, "height": .18, "strength": 18}
    capture = cv2.VideoCapture(str(path))
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if frames < 2:
        capture.release()
        return {"enabled": False, "confidence": 0.0, "status": "not_detected",
                "x": .08, "y": .72, "width": .84, "height": .18, "strength": 18}
    bands: list[tuple[float, float, float, float]] = []
    for sample in np.linspace(.08, .92, 18):
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(sample * (frames - 1)))
        ok, frame = capture.read()
        if not ok:
            continue
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Bright glyphs and their dark outlines create dense horizontal edge runs.
        edges = cv2.Canny(gray, 110, 220)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, width // 45), 3))
        joined = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w >= width * .18 and height * .42 <= y <= height * .94 and 5 <= h <= height * .18:
                candidates.append((x / width, y / height, w / width, h / height))
        if candidates:
            left = min(item[0] for item in candidates)
            top = min(item[1] for item in candidates)
            right = max(item[0] + item[2] for item in candidates)
            bottom = max(item[1] + item[3] for item in candidates)
            bands.append((left, top, right, bottom))
    capture.release()
    if len(bands) < 3:
        return {"enabled": False, "confidence": round(len(bands) / 18, 2), "status": "not_detected",
                "x": .08, "y": .72, "width": .84, "height": .18, "strength": 18}
    tops = np.array([item[1] for item in bands])
    bottoms = np.array([item[3] for item in bands])
    stability = max(0.0, 1.0 - float(np.std(tops) + np.std(bottoms)) * 5)
    confidence = min(.99, len(bands) / 18 * .75 + stability * .25)
    left = max(0.0, float(np.percentile([item[0] for item in bands], 20)) - .025)
    top = max(0.0, float(np.percentile(tops, 20)) - .025)
    right = min(1.0, float(np.percentile([item[2] for item in bands], 80)) + .025)
    bottom = min(1.0, float(np.percentile(bottoms, 80)) + .035)
    return {"enabled": confidence >= .62, "confidence": round(confidence, 2),
            "status": "detected" if confidence >= .62 else "possible",
            "x": round(left, 4), "y": round(top, 4), "width": round(max(.08, right - left), 4),
            "height": round(max(.05, bottom - top), 4), "strength": 18}


def shorten_timed_cues(cues: list[dict], maximum: float = 3.8, maximum_chars: int = 48) -> list[dict]:
    """Split platform subtitle cues early so translation items stay concise."""
    result: list[dict] = []
    for cue in cues:
        start, end, text = float(cue["start"]), float(cue["end"]), str(cue.get("text") or "").strip()
        duration = max(.05, end - start)
        visible_chars = len(re.sub(r"\s+", "", text))
        count = max(1, math.ceil(duration / maximum), math.ceil(visible_chars / maximum_chars))
        tokens = text.split()
        if not tokens or (len(tokens) == 1 and count > 1):
            tokens = list(text)
        count = min(count, max(1, len(tokens)))
        for index in range(count):
            left = round(index * len(tokens) / count)
            right = round((index + 1) * len(tokens) / count)
            part = " ".join(tokens[left:right]).strip() if " " in text else "".join(tokens[left:right]).strip()
            part = re.sub(r"\s+([,.;:!?])", r"\1", part)
            if not part:
                continue
            result.append({"source_start": start + duration * left / len(tokens),
                           "source_end": start + duration * right / len(tokens), "source_text": part})
    return result


def atomicize_transcript(payload: dict, target: float = 2.8, maximum: float = 3.8) -> list[dict]:
    """Turn Whisper words into stable translation/sync units without dropping text."""
    words: list[dict] = []
    for segment in payload.get("segments", []):
        segment_words = segment.get("words") or []
        if segment_words:
            words.extend(segment_words)
        elif segment.get("text"):
            words.append({"start": segment["start"], "end": segment["end"], "word": segment["text"]})
    if not words:
        return []
    units: list[dict] = []
    current: list[dict] = []
    for word_index, word in enumerate(words):
        if current and float(word["end"]) - float(current[0]["start"]) > maximum:
            units.append(_unit(current))
            current = []
        current.append(word)
        duration = float(current[-1]["end"]) - float(current[0]["start"])
        boundary = bool(re.search(r"[.!?…][\"']?$", str(word["word"])))
        gap = 0.0
        next_index = word_index + 1
        if next_index < len(words):
            gap = float(words[next_index]["start"]) - float(word["end"])
        if duration >= target and (boundary or gap >= 0.45):
            units.append(_unit(current))
            current = []
    if current:
        units.append(_unit(current))
    return units


def _unit(words: list[dict]) -> dict:
    text = " ".join(str(item["word"]).strip() for item in words).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return {"source_start": float(words[0]["start"]), "source_end": float(words[-1]["end"]), "source_text": text}


def write_source_artifacts(folder: Path, segments: list[dict]) -> None:
    numbered = "\n\n".join(f"{index}. {segment['source_text']}" for index, segment in enumerate(segments, 1))
    plain = "\n".join(segment["source_text"] for segment in segments)
    source_srt = "\n\n".join(
        f"{index}\n{_srt_time(float(segment['source_start']))} --> {_srt_time(float(segment['source_end']))}\n"
        f"{segment['source_text']}"
        for index, segment in enumerate(segments, 1)
    ) + "\n"
    (folder / "source.txt").write_text(numbered, encoding="utf-8")
    # Historical name retained as a compatibility alias for existing integrations.
    (folder / "english.txt").write_text(plain, encoding="utf-8")
    (folder / "source.srt").write_text(source_srt, encoding="utf-8")


def visual_windows(segments: list[dict], duration: float) -> list[tuple[float, float]]:
    if not segments:
        return []
    windows = []
    for index, segment in enumerate(segments):
        start = 0.0 if index == 0 else (float(segments[index - 1]["source_end"]) + float(segment["source_start"])) / 2
        end = duration if index == len(segments) - 1 else (float(segment["source_end"]) + float(segments[index + 1]["source_start"])) / 2
        windows.append((max(0.0, start), min(duration, max(start + 0.04, end))))
    return windows


class GeminiTranslator:
    def __init__(self, keys: GeminiKeyStore, client_factory: Callable | None = None):
        self.keys = keys
        self.client_factory = client_factory
        self._preferred: tuple[int, str] | None = None
        self._route_lock = threading.Lock()

    def _client(self, key: str):
        if self.client_factory:
            return self.client_factory(key)
        from google import genai

        from google.genai import types

        return genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=20_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    @staticmethod
    def _failure_kind(exc: Exception) -> str:
        message = str(exc).lower()
        if "location is not supported" in message or "region is not supported" in message:
            return "region"
        if "not found" in message or "no longer available" in message or "unsupported model" in message:
            return "model"
        return "key"

    def _request(self, prompt: str) -> tuple[str, str]:
        keys = self.keys.load()
        if not keys:
            raise RuntimeError("Add a Gemini API key before translation. / Gemini API key ထည့်ပါ။")
        errors = []
        with self._route_lock:
            preferred = self._preferred
        candidates: list[tuple[int, str]] = []
        if preferred and preferred[0] < len(keys):
            candidates.append(preferred)
        candidates.extend(
            (key_index, model_name)
            for model_name in MODEL_CHAIN
            for key_index in range(len(keys))
            if (key_index, model_name) not in candidates
        )
        unavailable_models: set[str] = set()
        for key_index, model_name in candidates:
            if model_name in unavailable_models:
                continue
            key = keys[key_index]
            client = None
            try:
                client = self._client(key)
                response = client.models.generate_content(model=model_name, contents=prompt)
                text = (getattr(response, "text", None) or "").strip()
                if not text:
                    raise RuntimeError("empty response")
                with self._route_lock:
                    self._preferred = (key_index, model_name)
                return text, f"Key {key_index + 1} · {model_name}"
            except Exception as exc:
                kind = self._failure_kind(exc)
                errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
                if kind == "region":
                    raise RuntimeError(
                        "Gemini translation is unavailable from this network region. "
                        "The local transcript is safe; retry when Gemini API access is available. "
                        "/ လက်ရှိဒေသမှ Gemini ကို အသုံးမပြုနိုင်သေးပါ။"
                    ) from exc
                if kind == "model":
                    unavailable_models.add(model_name)
                time.sleep(0.05)
            finally:
                close = getattr(client, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        pass
        raise RuntimeError("Every configured Gemini key/model failed. " + (errors[-1] if errors else ""))

    @staticmethod
    def _json(text: str) -> dict:
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip()
        return json.loads(cleaned)

    def test(self) -> dict:
        text, route = self._request('Return exactly this JSON: {"ok":true}')
        return {"ok": bool(self._json(text).get("ok")), "route": route}

    def translate(self, segments: list[dict], progress: Callable[[int, int], None],
                  on_batch: Callable[[dict[int, str]], None] | None = None) -> tuple[dict[int, str], str]:
        route = ""
        if len(segments) <= 20:
            context = {
                "summary": "Infer context from this short transcript",
                "delivery_style": "auto: conversation, explainer, news, instruction, documentary, or storytelling",
                "register": "natural spoken Myanmar Burmese",
                "names": [],
            }
        else:
            outline_prompt = (
                "You are the Burmese adaptation editor for a video voice-over. Read the complete source-language transcript, "
                "identify whether its natural delivery is conversation, explainer, news, instruction, documentary, "
                "or storytelling, and identify the appropriate Burmese speaking register. Return JSON only: "
                "{\"summary\":\"...\",\"delivery_style\":\"...\",\"register\":\"...\","
                "\"names\":[\"...\"],\"term_map\":{\"source term\":\"consistent Burmese rendering\"}}. "
                "Preserve facts, names, relationships, emotional intent, and the order of events.\n\n" +
                "\n".join(item["source_text"] for item in segments)[:45000]
            )
            context_text, route = self._request(outline_prompt)
            try:
                context = self._json(context_text)
            except Exception:
                context = {"summary": context_text[:1200], "tone": "natural narration", "names": []}
        translations: dict[int, str] = {}
        batches = [segments[index:index + 80] for index in range(0, len(segments), 80)]

        def translate_batch(batch: list[dict]) -> tuple[dict[int, str], str]:
            items = [{"id": item["position"], "source": item["source_text"]} for item in batch]
            prompt = (
                "Adapt every connected source-language item into natural Myanmar Burmese that a real person would say aloud "
                "in a video voice-over. This is spoken localization, not a literal sentence-by-sentence translation. "
                "First follow the delivery_style and register in Story context; when they are marked auto, infer the "
                "best style from all Items before translating. For conversation, use natural Burmese "
                "word order, pronouns, particles, and level of politeness for the relationship. For storytelling or "
                "documentary narration, use a smooth storyteller's flow, connected transitions, and the emotional intent "
                "of the scene. For explainers, news, or instructions, be clear, direct, and easy to listen to. "
                "Remove awkward source-language syntax and unnecessary repeated subjects, but preserve every fact, name, number, "
                "warning, joke, question, and event. Do not summarize, censor, invent details, or add translator notes. "
                "Keep each id concise enough for a short on-screen subtitle, and never move meaning into a neighboring id. "
                "Keep each id and its meaning in the same order; use neighboring items as one continuous script so tone "
                "and terminology remain consistent. Return JSON only as "
                "{\"translations\":[{\"id\":0,\"burmese\":\"...\"}]}.\n"
                f"Story context: {json.dumps(context, ensure_ascii=False)}\nItems: {json.dumps(items, ensure_ascii=False)}"
            )
            raw, used_route = self._request(prompt)
            parsed = self._json(raw)
            result: dict[int, str] = {}
            for row in parsed.get("translations", []):
                position = int(row["id"])
                value = str(row["burmese"]).strip()
                if value:
                    result[position] = value
            missing = [item["position"] for item in batch if item["position"] not in result]
            if missing:
                raise RuntimeError(f"Translation response missed segment(s): {missing[:8]}")
            return result, used_route

        # Two requests in flight is noticeably faster for hour-long videos but
        # remains conservative enough for typical Gemini key quotas.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        completed = 0
        with ThreadPoolExecutor(max_workers=min(2, len(batches))) as pool:
            futures = [pool.submit(translate_batch, batch) for batch in batches]
            for future in as_completed(futures):
                result, route = future.result()
                translations.update(result)
                if on_batch:
                    on_batch(result)
                completed += 1
                progress(completed, len(batches))
        return translations, route


class VideoDubbingQueue:
    def __init__(self, library: Library, videos: VideoLibrary, model: ModelManager,
                 analyzer: EmotionAnalyzer, keys: GeminiKeyStore, executor: ThreadPoolExecutor):
        self.library, self.videos, self.model, self.analyzer = library, videos, model, analyzer
        self.translator = GeminiTranslator(keys)
        self.remote_sources = RemoteSourceStore(library.data)
        self.transcriber = PersistentWhisper("small")
        self.downloader = RedNoteDownloader(self._download_progress, self._download_stop)
        self.executor = executor
        # Keep independent pipeline stages moving: network downloads can overlap
        # the single persistent Whisper model, while GPU transcription and final
        # renders remain serialized to protect the RTX 2050.
        self.download_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-download")
        self.prepare_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-transcribe")
        self.translation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-translate")
        self.render_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-export")
        self.analysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="subtitle-detector")
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()

    def schedule(self, job_id: str, task: str) -> dict:
        with self._lock:
            marker = f"{job_id}:{task}"
            if marker in self._scheduled:
                return self._require(job_id)
            self._scheduled.add(marker)
        stage = {"download": "download", "prepare": "transcription", "translate": "translation",
                 "render": "export"}.get(task, task)
        queued = self.videos.update(job_id, state="queued", stage=stage, error="",
                                    message=f"Queued for {stage} / စောင့်ဆိုင်းနေသည်")
        pool = {"download": self.download_executor, "prepare": self.prepare_executor,
                "translate": self.translation_executor, "render": self.render_executor}.get(task, self.executor)
        try:
            pool.submit(self._run, job_id, task, marker)
        except Exception:
            with self._lock:
                self._scheduled.discard(marker)
            raise
        return queued

    def action(self, job_id: str, action: str) -> dict:
        job = self._require(job_id)
        if action == "cancel":
            return self.videos.update(job_id, state="cancelling", message="Cancelling after this step…")
        if action == "pause":
            return self.videos.update(job_id, state="pausing", message="Pausing after this step…")
        if action in ("resume", "retry"):
            segments = self.videos.segments(job_id)
            if segments and not all(s["translation"] for s in segments) and job["settings"].get("translation_mode") == "manual":
                return self.await_manual_translation(job_id)
            task = "download" if job.get("source_kind") == "rednote" and not (self.videos.folder(job_id) / job["source_filename"]).exists() else (
                "render" if segments and all(s["translation"] for s in segments) else "translate" if segments else "prepare")
            return self.schedule(job_id, task)
        raise ValueError("Unknown action.")

    def _require(self, job_id: str) -> dict:
        job = self.videos.get(job_id)
        if not job:
            raise ValueError("Video job not found.")
        return job

    def _run(self, job_id: str, task: str, marker: str) -> None:
        try:
            if task == "download":
                self._download(job_id)
            elif task == "prepare":
                self._prepare(job_id)
            elif task == "translate":
                self._translate(job_id)
            else:
                self._render(job_id)
        except Exception as exc:
            title, recovery = friendly_video_failure(exc, task)
            try:
                technical = f"{type(exc).__name__}: {exc}\n"
                if isinstance(exc, subprocess.CalledProcessError):
                    stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
                    technical += "\n" + stderr
                (self.videos.folder(job_id) / "last-error.log").write_text(technical, encoding="utf-8")
            except Exception:
                pass
            self.videos.update(job_id, state="failed", stage=f"{task}_failed", error=title,
                               message=recovery)
        finally:
            with self._lock:
                self._scheduled.discard(marker)

    def _stop_requested(self, job_id: str) -> bool:
        state = self._require(job_id)["state"]
        if state in ("pausing", "paused"):
            self.videos.update(job_id, state="paused", message="Paused / ခေတ္တရပ်ထားသည်")
            return True
        if state in ("cancelling", "cancelled"):
            self.videos.update(job_id, state="cancelled", message="Cancelled / ပယ်ဖျက်ပြီး")
            return True
        return False

    def _download_progress(self, job_id: str, progress: dict) -> None:
        total = int(progress.get("total_bytes") or 0)
        complete = int(progress.get("downloaded_bytes") or 0)
        self.videos.update(
            job_id, state="downloading", stage="download", progress=complete, total=max(total, 1),
            download=progress, message=f"Downloading RedNote video · {progress.get('percent', 0):.1f}%",
        )

    def _download_stop(self, job_id: str) -> str:
        state = self._require(job_id)["state"]
        if state in ("pausing", "paused"):
            self.videos.update(job_id, state="paused", message="Download paused — partial file kept for resume")
            return "Download paused — partial file kept for resume."
        if state in ("cancelling", "cancelled"):
            self.videos.update(job_id, state="cancelled", message="Download cancelled — partial file kept")
            return "Download cancelled."
        return ""

    def _download(self, job_id: str) -> None:
        job = self._require(job_id)
        settings = job["settings"]
        self.videos.update(job_id, state="downloading", stage="download", progress=0, total=1,
                           message="Connecting to RedNote…")
        result = self.downloader.download(
            job_id, self.remote_sources.load(job_id), self.videos.folder(job_id),
            bool(settings.get("best_quality", True)),
            bool(settings.get("allow_browser_cookies", False)),
        )
        source = ensure_browser_video(result["path"])
        self.videos.update(
            job_id, title=result.get("title") or job["title"], original_filename=source.name,
            source_filename=source.name, download={"state": "finished", "percent": 100,
            "browser_session": result.get("browser_session"), "uploader": result.get("uploader", "")},
        )
        self.schedule(job_id, "prepare")

    def _prepare(self, job_id: str) -> None:
        job = self._require(job_id)
        folder = self.videos.folder(job_id)
        source = folder / job["source_filename"]
        info = probe_video(source)
        self.videos.update(job_id, state="transcribing", stage="transcription", progress=0, total=4,
                           message="Finding source speech and subtitles…", **info)
        blur_future = self.analysis_executor.submit(detect_subtitle_region, source)
        transcript = folder / "transcript.json"
        subtitle = find_subtitle_file(folder) if job.get("source_kind") == "rednote" else None
        if subtitle:
            cues = parse_subtitles(subtitle)
            units = shorten_timed_cues(cues)
            payload = {"language": subtitle.stem.split(".")[-1], "language_probability": 1.0,
                       "segments": [{**cue, "words": []} for cue in cues], "device": "platform subtitles"}
            transcript_source = "platform_subtitles"
        else:
            audio = folder / "source-16k.wav"
            subprocess.run([ffmpeg_path(), "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
                           check=True, capture_output=True)
            payload = self.transcriber.transcribe(audio)
            units = atomicize_transcript(payload)
            transcript_source = "whisper"
        transcript.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not units:
            raise RuntimeError("No usable source speech or subtitle timestamps were detected in this video.")
        for position, unit in enumerate(units):
            unit["position"] = position
        self.videos.replace_segments(job_id, units)
        write_source_artifacts(folder, self.videos.segments(job_id))
        blur = blur_future.result()
        self.videos.update(job_id, source_language=payload.get("language") or "unknown",
                           transcript_source=transcript_source, subtitle_blur=blur,
                           message=(payload.get("warning") or "Source transcript is ready"))
        if self._stop_requested(job_id):
            return
        if job["settings"].get("translation_mode", "gemini") == "manual":
            self.await_manual_translation(job_id)
        else:
            self.schedule(job_id, "translate")

    def await_manual_translation(self, job_id: str) -> dict:
        job = self._require(job_id)
        segments = self.videos.segments(job_id)
        if not segments:
            raise ValueError("The source transcript is not ready yet.")
        settings = dict(job["settings"])
        settings["translation_mode"] = "manual"
        write_source_artifacts(self.videos.folder(job_id), segments)
        return self.videos.update(
            job_id, settings=settings, state="awaiting_translation", stage="manual_translation",
            progress=1, total=1, error="",
            message=f"Transcript ready · import {len(segments)} translated Burmese SRT cues to continue.",
        )

    def import_manual_translation(self, job_id: str, content: str) -> dict:
        job = self._require(job_id)
        segments = self.videos.segments(job_id)
        if not segments:
            raise ValueError("The source transcript is not ready yet.")
        cues = parse_manual_translation_srt(content)
        translations, timing_warning = validate_manual_translation(cues, segments)
        # Storage performs the replacement in one transaction after all validation succeeds.
        self.videos.replace_manual_translations(job_id, translations)
        settings = dict(job["settings"])
        settings["translation_mode"] = "manual"
        updated = self.videos.segments(job_id)
        for segment in updated:
            result = self.analyzer.analyze_sentence(
                segment["translation"], settings.get("delivery_mode", "consistent")
            )
            self.videos.update_segment(job_id, segment["id"], {"style": result.style})
            with closing(self.library.connect()) as db, db:
                db.execute(
                    "UPDATE video_segments SET manual_style=0,confidence=? WHERE id=?",
                    (result.confidence, segment["id"]),
                )
        folder = self.videos.folder(job_id)
        final_segments = self.videos.segments(job_id)
        (folder / "burmese.txt").write_text(
            "\n".join(segment["translation"] for segment in final_segments), encoding="utf-8"
        )
        (folder / "burmese-imported.srt").write_text(content.lstrip("\ufeff"), encoding="utf-8")
        message = "Burmese SRT imported — review scenes before export."
        if timing_warning:
            message += " " + timing_warning
        result = self.videos.update(
            job_id, settings=settings, state="review_ready", stage="review", progress=1, total=1,
            error="", message=message,
        )
        self.executor.submit(self._prefetch_previews, job_id)
        return self.videos.get(job_id, include_segments=True)

    def _translate(self, job_id: str) -> None:
        job = self._require(job_id)
        folder = self.videos.folder(job_id)
        segments = self.videos.segments(job_id)
        pending = [segment for segment in segments if not segment["translation"].strip()]
        if not pending:
            self.videos.update(job_id, state="review_ready", stage="review", progress=1, total=1,
                               message="Translation is already saved — review before rendering")
            return
        self.videos.update(job_id, state="translating", stage="translation", progress=1, total=4,
                           message=f"Translating {len(pending)} saved English segments…")
        translations, route = self.translator.translate(
            pending,
            lambda done, total: self.videos.update(
                job_id, progress=1 + done, total=1 + total,
                message=f"Translated {done} of {total} batches · completed text is saved"),
            lambda result: self.videos.merge_translations(job_id, result),
        )
        self.videos.merge_translations(job_id, translations)
        for segment in self.videos.segments(job_id):
            result = self.analyzer.analyze_sentence(segment["translation"], job["settings"].get("delivery_mode", "consistent"))
            self.videos.update_segment(job_id, segment["id"], {"style": result.style})
            # Automatic values should remain editable but not be tagged as manual.
            with closing(self.library.connect()) as db, db:
                db.execute("UPDATE video_segments SET manual_style=0,confidence=? WHERE id=?", (result.confidence, segment["id"]))
        write_source_artifacts(folder, self.videos.segments(job_id))
        (folder / "burmese.txt").write_text("\n".join(s["translation"] for s in self.videos.segments(job_id)), encoding="utf-8")
        self.videos.update(job_id, state="review_ready", stage="review", progress=4, total=4,
                           message=f"Review translation before rendering · {route}")
        self.executor.submit(self._prefetch_previews, job_id)

    def _prefetch_previews(self, job_id: str) -> None:
        for segment in self.videos.segments(job_id):
            job = self.videos.get(job_id)
            if not job or job["state"] != "review_ready":
                return
            current = next((item for item in self.videos.segments(job_id) if item["id"] == segment["id"]), None)
            if not current or current.get("deleted") or current.get("audio_state") == "ready":
                continue
            try:
                self.synthesize_segment(job_id, segment["id"])
            except Exception:
                # Preview prefetch is an optimization; export and manual preview remain retryable.
                return
    def _render(self, job_id: str) -> None:
        job = self._require(job_id)
        settings = job["settings"]
        segments = [item for item in self.videos.segments(job_id) if not item.get("deleted")]
        if not segments or any(not item["translation"].strip() for item in segments):
            raise RuntimeError("Translate and review the video before rendering.")
        mode = settings.get("export_mode", "voiceover_captions")
        if mode not in EXPORT_MODES:
            raise RuntimeError("Choose a valid export mode before rendering.")
        folder = self.videos.folder(job_id)
        clips = folder / "audio-clips"
        clips.mkdir(exist_ok=True)
        narrated = [segment for segment in segments if not segment.get("caption_only")]
        if mode != "subtitles_only":
            self.videos.update(job_id, state="synthesizing", stage="voice", progress=0, total=max(1, len(narrated)), message="Creating Burmese voice…")
            for index, segment in enumerate(narrated):
                if self._stop_requested(job_id):
                    return
                output = clips / f"{segment['id']}.wav"
                if segment.get("audio_state") != "ready" or not output.exists():
                    self.synthesize_segment(job_id, segment["id"])
                self.videos.update(job_id, progress=index + 1, message=f"Voice segment {index + 1} of {len(narrated)}")
        else:
            self.videos.update(job_id, state="syncing", stage="preflight", progress=0, total=1,
                               message="Subtitle-only export · online voice is skipped")
        self.videos.update(job_id, state="syncing", stage="smart_sync", progress=0, total=len(segments), message="Fixed timing: keeping video at 1× and fitting narration…")
        self._smart_sync(job_id)

    def synthesize_segment(self, job_id: str, segment_id: str) -> dict:
        job = self._require(job_id)
        segment = next((s for s in self.videos.segments(job_id) if s["id"] == segment_id), None)
        if not segment or segment.get("deleted"):
            raise ValueError("Scene not found.")
        settings = job["settings"]
        voice_id = settings.get("voice_id") or DEFAULT_VOICE
        online_voice(voice_id)
        style = segment.get("style") if segment.get("style") in STYLES else "neutral"
        clips = self.videos.folder(job_id) / "audio-clips"
        clips.mkdir(exist_ok=True)
        output = clips / f"{segment_id}.wav"
        raw_output = clips / f".{segment_id}-raw.wav"
        arrays = []
        for part_index, part in enumerate(split_online_text(segment["translation"])):
            part_file = clips / f".{segment_id}-{part_index}.wav"
            self.model.generate_chunk(part, part_file,
                speed=float(segment.get("scene_speed") or settings.get("speed", 1)),
                preset=settings.get("preset", "balanced"), voice_id=voice_id, style=style,
                intensity=float(settings.get("emotion_intensity", .35)))
            arrays.append(sf.read(part_file, dtype="float32")[0])
            part_file.unlink(missing_ok=True)
        joined = np.concatenate(arrays) if arrays else np.zeros(1, dtype=np.float32)
        sf.write(raw_output, joined, SAMPLE_RATE, subtype="PCM_16")
        cue_duration = max(.05, float(segment["source_end"]) - float(segment["source_start"]))
        raw_duration = len(joined) / SAMPLE_RATE
        fit_ratio = max(1.0, raw_duration / cue_duration)
        if fit_ratio > 1.0005:
            subprocess.run([ffmpeg_path(), "-y", "-i", str(raw_output), "-af", atempo_chain(fit_ratio),
                            "-ac", "1", "-ar", str(SAMPLE_RATE), str(output)], check=True, capture_output=True)
            fitted, _ = sf.read(output, dtype="float32")
            fitted = np.asarray(fitted).reshape(-1)[:round(cue_duration * SAMPLE_RATE)]
            sf.write(output, fitted, SAMPLE_RATE, subtype="PCM_16")
        else:
            os.replace(raw_output, output)
            fitted = joined
        raw_output.unlink(missing_ok=True)
        warning = ""
        if fit_ratio > 1.5:
            warning = f"Fast fit {fit_ratio:.2f}× — shorten this translation for a more natural delivery."
        return self.videos.update_segment(job_id, segment_id, {
            "state": "succeeded", "audio_state": "ready", "audio_filename": output.name,
            "audio_duration": len(fitted) / SAMPLE_RATE, "warning": warning,
        })

    def _smart_sync(self, job_id: str) -> None:
        job = self._require(job_id)
        folder = self.videos.folder(job_id)
        source = folder / job["source_filename"]
        if not source.exists():
            raise RuntimeError("The source video is missing. Restore it before exporting.")
        required = max(512 * 1024 * 1024, int(source.stat().st_size * 2.2))
        free = shutil.disk_usage(folder).free
        if free < required:
            raise RuntimeError(f"Not enough disk space. Free at least {(required - free) / 1024**3:.1f} GB, then retry.")
        segments = [item for item in self.videos.segments(job_id) if not item.get("deleted")]
        snapshot = {"settings": job["settings"], "blur": job.get("subtitle_blur") or {}, "segments": segments,
                    "source_size": source.stat().st_size, "source_mtime": source.stat().st_mtime_ns}
        snapshot_hash = _stable_hash(snapshot)
        encoder = self._preferred_encoder(folder)
        revision = int(job.get("export_revision") or 0) + 1
        export = self.videos.begin_export(job_id, revision, job["settings"].get("export_mode", "voiceover_captions"),
                                          snapshot_hash, encoder)
        if encoder == "libx264":
            self.videos.update_export(export["id"], stage="encoder_fallback",
                                      message="RTX NVENC is unavailable · using the reliable CPU encoder")
            self.videos.update(job_id, message="RTX encoding unavailable · exporting with CPU / CPU ဖြင့် ထုတ်နေသည်")
        started = time.monotonic()
        try:
            self._smart_sync_attempt(job_id, export, encoder, snapshot_hash, started)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            if encoder == "h264_nvenc" and any(marker in stderr.lower() for marker in ("nvenc", "cuda", "no capable devices", "encoder setup failed")):
                shutil.rmtree(folder / "export-chunks", ignore_errors=True)
                shutil.rmtree(folder / "video-clips", ignore_errors=True)
                (folder / "export-chunks").mkdir(exist_ok=True)
                self.videos.update_export(export["id"], encoder="libx264", stage="encoder_fallback", progress=0,
                                          message="RTX encoding unavailable · restarting safely with CPU")
                self.videos.update(job_id, progress=0,
                                   message="RTX encoding failed · restarting safely with CPU / CPU ဖြင့် ပြန်စနေသည်")
                self._smart_sync_attempt(job_id, export, "libx264", snapshot_hash, started)
            else:
                self.videos.update_export(export["id"], state="failed", stage="render_failed", error=str(exc),
                                          message="A saved export chunk could not be completed.")
                raise

    def _preferred_encoder(self, folder: Path) -> str:
        test = folder / ".nvenc-test.mp4"
        try:
            subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=.05",
                            "-frames:v", "1", "-c:v", "h264_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p", str(test)],
                           check=True, capture_output=True, timeout=20)
            return "h264_nvenc"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return "libx264"
        finally:
            test.unlink(missing_ok=True)

    @staticmethod
    def _encoder_args(encoder: str) -> list[str]:
        if encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23", "-b:v", "0", "-pix_fmt", "yuv420p"]
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]

    def _smart_sync_attempt(self, job_id: str, export: dict, encoder: str, snapshot_hash: str,
                            started: float) -> None:
        job = self._require(job_id)
        folder, settings = self.videos.folder(job_id), job["settings"]
        source = folder / job["source_filename"]
        cues = [item for item in self.videos.segments(job_id) if not item.get("deleted")]
        visual_segments = [item for item in cues if not item.get("is_user_added")]
        if not visual_segments:
            raise RuntimeError("At least one retained picture scene is required.")
        render_dir, chunk_dir = folder / "video-clips", folder / "export-chunks"
        render_dir.mkdir(exist_ok=True); chunk_dir.mkdir(exist_ok=True)
        ratio, fill = settings.get("output_ratio", "original"), settings.get("frame_fill", "fit")
        dimensions = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}.get(ratio)
        fps = max(1.0, min(60.0, float(job.get("fps") or 30)))
        visual_entries, source_map, output_cursor = [], [], 0.0
        for index, segment in enumerate(visual_segments):
            if self._stop_requested(job_id):
                self.videos.update_export(export["id"], state="paused", stage="paused", message="Paused after a completed scene")
                return
            ranges = segment.get("source_clips") or [{"start": segment["source_start"], "end": segment["source_end"]}]
            duration = max(.05, sum(float(part["end"]) - float(part["start"]) for part in ranges))
            scene_hash = _stable_hash({"ranges": ranges, "dimensions": dimensions, "fill": fill, "fps": fps, "encoder": encoder,
                                       "source": snapshot_hash[:16]})
            clip, sidecar = render_dir / f"{index:05d}.mp4", render_dir / f"{index:05d}.sha256"
            if not clip.exists() or not sidecar.exists() or sidecar.read_text(encoding="ascii") != scene_hash:
                subclips = []
                for part_index, part in enumerate(ranges):
                    part_duration = max(.04, float(part["end"]) - float(part["start"]))
                    vf = f"fps={fps:.6f}"
                    if dimensions:
                        width, height = dimensions
                        vf += (f",scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}" if fill == "fill"
                               else f",scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
                    vf += f",trim=duration={part_duration:.6f},setpts=PTS-STARTPTS"
                    subclip = render_dir / f".{index:05d}-{part_index}.mp4"
                    command = [ffmpeg_path(), "-y", "-ss", f"{float(part['start']):.6f}", "-to", f"{float(part['end']):.6f}",
                               "-i", str(source), "-an", "-vf", vf, *self._encoder_args(encoder), str(subclip)]
                    subprocess.run(command, check=True, capture_output=True)
                    subclips.append(subclip)
                if len(subclips) == 1:
                    os.replace(subclips[0], clip)
                else:
                    scene_list = render_dir / f".{index:05d}-concat.txt"
                    scene_list.write_text("\n".join(f"file '{part.as_posix()}'" for part in subclips), encoding="utf-8")
                    subprocess.run([ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", str(scene_list),
                                    "-c", "copy", str(clip)], check=True, capture_output=True)
                    for part in subclips: part.unlink(missing_ok=True)
                    scene_list.unlink(missing_ok=True)
                sidecar.write_text(scene_hash, encoding="ascii")
            entry = {"segment_id": segment["id"], "clip": clip, "ranges": ranges, "start": output_cursor,
                     "end": output_cursor + duration, "duration": duration, "native_duration": duration}
            visual_entries.append(entry)
            local = output_cursor
            for part in ranges:
                length = float(part["end"]) - float(part["start"])
                source_map.append({"segment_id": segment["id"], "source_start": float(part["start"]),
                                   "source_end": float(part["end"]), "output_start": local, "output_end": local + length})
                local += length
            output_cursor += duration
        plan = []
        for position, cue in enumerate(cues):
            candidates = [item for item in source_map if item["segment_id"] == cue["id"] and
                          item["source_start"] <= float(cue["source_start"]) <= item["source_end"]]
            if not candidates:
                candidates = [item for item in source_map if item["source_start"] <= float(cue["source_start"]) < item["source_end"]]
            if not candidates:
                if cue.get("is_user_added"):
                    raise RuntimeError("An added cue starts outside every retained video range.")
                candidates = [item for item in source_map if item["segment_id"] == cue["id"]]
            mapped = candidates[0]
            narration_start = mapped["output_start"] + max(0, float(cue["source_start"]) - mapped["source_start"])
            plan.append({"position": position, "segment_id": cue["id"], "source_clips": cue.get("source_clips") or [],
                         "native_duration": next((item["duration"] for item in visual_entries if item["segment_id"] == cue["id"]), 0),
                         "narration_start": narration_start,
                         "cue_duration": max(.05, float(cue["source_end"]) - float(cue["source_start"])),
                         "audio_duration": float(cue.get("audio_duration") or 0), "fit_ratio": _warning_ratio(cue.get("warning")),
                         "voice_id": settings.get("voice_id") or DEFAULT_VOICE, "translation": cue["translation"],
                         "caption_only": bool(cue.get("caption_only")),
                         "caption_style_override": cue.get("caption_style_override") or {}})
        _write_srt(folder / "burmese.srt", cues, plan, settings)
        (folder / "burmese.txt").write_text("\n".join(cue["translation"] for cue in cues), encoding="utf-8")
        groups, current, current_duration = [], [], 0.0
        for entry in visual_entries:
            if current and current_duration + entry["duration"] > 300:
                groups.append(current); current, current_duration = [], 0.0
            current.append(entry); current_duration += entry["duration"]
        if current: groups.append(current)
        existing = {int(row["chunk_index"]): row for row in (self.videos.current_export(job_id) or {}).get("chunks", [])}
        self.videos.update_export(export["id"], state="rendering", stage="chunks", encoder=encoder,
                                  total=len(groups), message=f"Rendering {len(groups)} resumable video chunks")
        for chunk_index, entries in enumerate(groups):
            if self._stop_requested(job_id):
                self.videos.update_export(export["id"], state="paused", stage="paused", progress=chunk_index,
                                          total=len(groups), message="Paused · completed chunks are saved")
                return
            start, end = entries[0]["start"], entries[-1]["end"]
            chunk_hash = _stable_hash({"snapshot": snapshot_hash, "encoder": encoder, "index": chunk_index,
                                       "entries": [(entry["segment_id"], entry["start"], entry["end"]) for entry in entries]})
            output = chunk_dir / f"chunk-{chunk_index:04d}.mp4"
            saved = existing.get(chunk_index)
            if not (saved and saved.get("state") == "complete" and saved.get("chunk_hash") == chunk_hash and output.exists()):
                self._render_export_chunk(job, export, chunk_index, entries, plan, encoder, output)
                self.videos.save_export_chunk(export["id"], chunk_index, start, end, "complete", output.name, chunk_hash)
            elapsed = time.monotonic() - started
            complete = chunk_index + 1
            eta = (elapsed / complete) * (len(groups) - complete) if complete else None
            self.videos.update_export(export["id"], progress=complete, total=len(groups), elapsed_seconds=elapsed,
                                      eta_seconds=eta, message=f"Rendered chunk {complete} of {len(groups)}")
            encoder_label = "RTX NVENC" if encoder == "h264_nvenc" else "CPU encoder"
            self.videos.update(job_id, progress=complete, total=len(groups),
                               message=f"Long-video export · chunk {complete} of {len(groups)} · {encoder_label}")
        concat_file = chunk_dir / "concat.txt"
        concat_file.write_text("\n".join(f"file '{(chunk_dir / f'chunk-{i:04d}.mp4').as_posix()}'" for i in range(len(groups))), encoding="utf-8")
        pending = folder / ".burmese-dub.next.mp4"
        self.videos.update_export(export["id"], state="finalizing", stage="finalizing", message="Joining and validating saved chunks…")
        subprocess.run([ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy",
                        "-movflags", "+faststart", str(pending)], check=True, capture_output=True)
        narration_artifact = folder / "narration.wav"
        if settings.get("export_mode", "voiceover_captions") != "subtitles_only":
            narration_list = chunk_dir / "narration-concat.txt"
            narration_list.write_text("\n".join(
                f"file '{(chunk_dir / f'.{index:04d}-narration.wav').as_posix()}'" for index in range(len(groups))
            ), encoding="utf-8")
            pending_narration = folder / ".narration.next.wav"
            subprocess.run([ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", str(narration_list),
                            "-c", "copy", str(pending_narration)], check=True, capture_output=True)
            os.replace(pending_narration, narration_artifact)
        else:
            narration_artifact.unlink(missing_ok=True)
        rendered = probe_video(pending)
        if abs(float(rendered["duration"]) - output_cursor) > max(.5, output_cursor * .005):
            raise RuntimeError("Final validation found a duration mismatch. Saved chunks remain available for retry.")
        if not _has_audio_stream(pending):
            raise RuntimeError("Final validation found no audio stream.")
        final = folder / "burmese-dub.mp4"
        os.replace(pending, final)
        (folder / "sync-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        elapsed = time.monotonic() - started
        self.videos.update_export(export["id"], state="succeeded", stage="complete", progress=len(groups), total=len(groups),
                                  elapsed_seconds=elapsed, eta_seconds=0, output_filename=final.name,
                                  message="Export validated and ready")
        self.videos.update(job_id, state="succeeded", stage="complete", output_filename=final.name,
                           export_revision=int(job.get("export_revision") or 0) + 1,
                           progress=len(groups), total=len(groups), message="Burmese video is ready / ဗီဒီယိုအသင့်ဖြစ်ပါပြီ")

    def _render_export_chunk(self, job: dict, export: dict, chunk_index: int, entries: list[dict],
                             plan: list[dict], encoder: str, output: Path) -> None:
        folder, settings = self.videos.folder(job["id"]), job["settings"]
        source, chunk_dir = folder / job["source_filename"], output.parent
        start, end, duration = entries[0]["start"], entries[-1]["end"], entries[-1]["end"] - entries[0]["start"]
        list_file, silent = chunk_dir / f".{chunk_index:04d}-visual.txt", chunk_dir / f".{chunk_index:04d}-silent.mp4"
        list_file.write_text("\n".join(f"file '{entry['clip'].as_posix()}'" for entry in entries), encoding="utf-8")
        subprocess.run([ffmpeg_path(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(silent)],
                       check=True, capture_output=True)
        ambience, narration = chunk_dir / f".{chunk_index:04d}-ambience.wav", chunk_dir / f".{chunk_index:04d}-narration.wav"
        with sf.SoundFile(ambience, "w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16") as destination:
            for entry_index, entry in enumerate(entries):
                for part_index, part in enumerate(entry["ranges"]):
                    length = max(.04, float(part["end"]) - float(part["start"])); frames = round(length * SAMPLE_RATE)
                    temp = chunk_dir / f".amb-{chunk_index}-{entry_index}-{part_index}.wav"
                    try:
                        subprocess.run([ffmpeg_path(), "-y", "-ss", f"{float(part['start']):.6f}", "-to", f"{float(part['end']):.6f}",
                                        "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), str(temp)],
                                       check=True, capture_output=True)
                        audio, _ = sf.read(temp, dtype="float32")
                    except subprocess.CalledProcessError:
                        audio = np.zeros(0, dtype=np.float32)
                    finally:
                        temp.unlink(missing_ok=True)
                    audio = np.asarray(audio).reshape(-1)[:frames]
                    destination.write(np.pad(audio, (0, max(0, frames - len(audio))))[:frames])
        mode = settings.get("export_mode", "voiceover_captions")
        if mode != "subtitles_only":
            _write_silence(narration, duration)
            with sf.SoundFile(narration, "r+") as destination:
                for cue in plan:
                    if cue.get("caption_only") or not (start <= float(cue["narration_start"]) < end):
                        continue
                    segment = next(item for item in self.videos.segments(job["id"]) if item["id"] == cue["segment_id"])
                    audio_path = folder / "audio-clips" / str(segment.get("audio_filename") or "")
                    if not audio_path.exists():
                        continue
                    audio, _ = sf.read(audio_path, dtype="float32")
                    offset = round((float(cue["narration_start"]) - start) * SAMPLE_RATE)
                    available = max(0, round(duration * SAMPLE_RATE) - offset)
                    destination.seek(offset); destination.write(np.asarray(audio).reshape(-1)[:available])
        info = probe_video(silent); width, height = int(info["width"]), int(info["height"])
        filters, video_label = [], "0:v:0"
        if settings.get("mirror_horizontal"):
            filters.append("[0:v]hflip[vflip]")
            video_label = "vflip"
        blur = job.get("subtitle_blur") or {}
        if blur.get("enabled"):
            x = min(.98, max(0.0, float(blur.get("x", .08)))); y = min(.98, max(0.0, float(blur.get("y", .72))))
            region_width = min(1 - x, max(.02, float(blur.get("width", .84)))); region_height = min(1 - y, max(.02, float(blur.get("height", .18))))
            if settings.get("mirror_horizontal"):
                x = 1 - x - region_width
            strength = safe_blur_radius(int(blur.get("strength", 18)), width, height, region_width, region_height)
            filters.append(f"[{video_label}]split[base][region];[region]crop=iw*{region_width:.6f}:ih*{region_height:.6f}:iw*{x:.6f}:ih*{y:.6f},boxblur={strength}:2[blurred];[base][blurred]overlay=W*{x:.6f}:H*{y:.6f}[vblur]")
            video_label = "vblur"
        if mode in ("voiceover_captions", "subtitles_only"):
            ass = chunk_dir / f".{chunk_index:04d}-captions.ass"
            _write_ass(ass, plan, settings, width, height, offset=start, limit=duration)
            escaped_ass = ass.as_posix().replace(":", "\\:").replace("'", "\\'")
            escaped_fonts = FONT_DIRECTORY.as_posix().replace(":", "\\:").replace("'", "\\'")
            filters.append(f"[{video_label}]ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}'[vcap]")
            video_label = "vcap"
        command = [ffmpeg_path(), "-y", "-i", str(silent), "-i", str(ambience)]
        if mode != "subtitles_only": command += ["-i", str(narration)]
        audio_label = "1:a:0"
        if mode == "subtitles_only":
            filters.append("[1:a]anull[aout]"); audio_label = "aout"
        else:
            volume = float(settings.get("original_audio_volume", 0))
            if volume > 0 and settings.get("ducking", True):
                filters.append(f"[1:a]volume={volume:.3f}[amb];[amb][2:a]sidechaincompress=threshold=.025:ratio=8[duck];[duck][2:a]amix=inputs=2:duration=longest:normalize=0[aout]")
            elif volume > 0:
                filters.append(f"[1:a]volume={volume:.3f}[amb];[amb][2:a]amix=inputs=2:duration=longest:normalize=0[aout]")
            else:
                filters.append("[2:a]anull[aout]")
            audio_label = "aout"
        command += ["-filter_complex", ";".join(filters), "-map", f"[{video_label}]" if video_label != "0:v:0" else "0:v:0",
                    "-map", f"[{audio_label}]", *self._encoder_args(encoder), "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.6f}",
                    "-movflags", "+faststart", str(output)]
        subprocess.run(command, check=True, capture_output=True)
        for temporary in (list_file, silent, ambience): temporary.unlink(missing_ok=True)
        if mode == "subtitles_only": narration.unlink(missing_ok=True)


def _write_srt(path: Path, segments: list[dict], plan: list[dict], settings: dict | None = None) -> None:
    lines = []
    cues = [{**entry, "translation": segment["translation"]} for segment, entry in zip(segments, plan)]
    for index, event in enumerate(_caption_events(cues, settings or {"caption_split": "short"}), 1):
        lines.extend([str(index), f"{_srt_time(float(event['caption_start']))} --> {_srt_time(float(event['caption_end']))}",
                      event["translation"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_silence(path: Path, duration: float) -> None:
    remaining = max(1, round(duration * SAMPLE_RATE))
    block = np.zeros(SAMPLE_RATE * 10, dtype=np.float32)
    with sf.SoundFile(path, "w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16") as destination:
        while remaining:
            count = min(remaining, len(block))
            destination.write(block[:count])
            remaining -= count


def _warning_ratio(warning: str | None) -> float:
    match = re.search(r"([0-9.]+)×", warning or "")
    return float(match.group(1)) if match else 1.0


def _srt_time(value: float) -> str:
    milliseconds = int(round(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
