from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from .emotion import DELIVERY_MODES, STYLES, EmotionAnalyzer, prosody_for
from .engine import ModelManager, SAMPLE_RATE
from .storage import Library
from .text import split_passage, validate_long_form


class ProjectRenderQueue:
    def __init__(
        self,
        library: Library,
        model: ModelManager,
        analyzer: EmotionAnalyzer,
        executor: ThreadPoolExecutor,
    ) -> None:
        self.library = library
        self.model = model
        self.analyzer = analyzer
        self.executor = executor
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()

    def analyze(self, project_id: str) -> list[dict]:
        project = self.library.get_project(project_id)
        if not project:
            raise ValueError("Project not found.")
        grouped: list[tuple[str, list[dict]]] = []
        for chapter in project["chapters"]:
            analyzed = self.analyzer.analyze_text(chapter["text"], project["delivery_mode"])
            for segment in analyzed:
                segment["voice_id"] = chapter.get("voice_id")
                segment["manual_voice_override"] = False
            grouped.append((chapter["id"], analyzed))
        return self.library.replace_segments(project_id, grouped)

    def start(self, project_id: str) -> dict:
        project = self.library.get_project(project_id)
        if not project:
            raise ValueError("Project not found.")
        if not project["segments"]:
            self.analyze(project_id)
            project = self.library.get_project(project_id)
            assert project is not None
        active = [item for item in project["renders"] if item["state"] in ("queued", "rendering", "pausing")]
        if active:
            raise ValueError("This project already has an active render.")

        seed = secrets.randbelow(2_000_000_000)
        chunks: list[dict] = []
        chapter_positions = {item["id"]: item["position"] for item in project["chapters"]}
        chapters_by_id = {item["id"]: item for item in project["chapters"]}
        for segment in project["segments"]:
            chapter = chapters_by_id[segment["chapter_id"]]
            voice_id = segment.get("voice_id") if segment.get("manual_voice_override") else None
            if not voice_id:
                voice_id = chapter.get("voice_id") or project.get("voice_id")
            for part in split_passage(segment["text"], limit=45):
                position = len(chunks)
                chunks.append(
                    {
                        "chapter_position": chapter_positions[segment["chapter_id"]],
                        "text": part,
                        "style": segment["effective_style"],
                        "voice_id": voice_id,
                        "seed": (seed + position * 104729) % 2_147_483_647,
                    }
                )
        used_voice_ids = {item["voice_id"] for item in chunks if item.get("voice_id")}
        voices = {voice_id: self.library.get_voice(voice_id) for voice_id in used_voice_ids}
        missing = [voice_id for voice_id, voice in voices.items() if not voice]
        if missing:
            raise ValueError("A selected cast voice no longer exists. Choose another voice.")
        voice_names = [voice["name"] for voice in voices.values() if voice]
        display_voice = voice_names[0] if len(voice_names) == 1 and not any(not c.get("voice_id") for c in chunks) else "Multi-speaker cast"
        config = {
            "voice_id": project["voice_id"],
            "voice_name": display_voice if voices else "Built-in voice",
            "speed": project["speed"],
            "preset": project["preset"],
            "delivery_mode": project["delivery_mode"],
            "emotion_intensity": project["emotion_intensity"],
            "voices": {"__builtin__": {"name": "Built-in voice", "styles": {}}},
            "chapters": [{"position": c["position"], "title": c["title"], "voice_id": c.get("voice_id")} for c in project["chapters"]],
        }
        render = self.library.create_render(project_id, seed, config, chunks)
        render_dir = self.library.render_path(render)
        refs_dir = render_dir / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        for voice_id, voice in voices.items():
            assert voice is not None
            config["voices"][voice_id] = {"name": voice["name"], "styles": {}}
            voice_refs = refs_dir / voice_id
            voice_refs.mkdir(exist_ok=True)
            for style in self.library.list_voice_styles(voice_id, include_filename=True):
                source = self.library.voices_dir / style["filename"]
                destination = voice_refs / f"{style['style']}.wav"
                shutil.copy2(source, destination)
                config["voices"][voice_id]["styles"][style["style"]] = {
                    "path": str(destination), "transcript": style["transcript"],
                    "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                }
        self.library.update_render(render["id"], config_json=json.dumps(config, ensure_ascii=False))
        self._schedule(render["id"])
        return self.library.get_render(render["id"])

    def _schedule(self, render_id: str) -> None:
        with self._lock:
            if render_id in self._scheduled:
                return
            self._scheduled.add(render_id)
        self.executor.submit(self._run, render_id)

    def pause(self, render_id: str) -> dict:
        render = self._require(render_id)
        if render["state"] in ("queued", "interrupted"):
            return self.library.update_render(render_id, state="paused", message="Paused / ခေတ္တရပ်ထားသည်")
        if render["state"] == "rendering":
            return self.library.update_render(render_id, state="pausing", message="Pausing after this part…")
        return render

    def resume(self, render_id: str) -> dict:
        render = self._require(render_id)
        if render["state"] not in ("paused", "interrupted", "failed"):
            raise ValueError("This render cannot be resumed.")
        if render["state"] == "failed":
            for chunk in self.library.get_render(render_id, include_chunks=True)["chunks"]:
                if chunk["state"] == "failed":
                    self.library.update_render_chunk(chunk["id"], state="pending", error="")
        updated = self.library.update_render(render_id, state="queued", error="", message="Queued to resume")
        self._schedule(render_id)
        return updated

    def cancel(self, render_id: str) -> dict:
        render = self._require(render_id)
        state = "cancelled" if render["state"] in ("paused", "interrupted", "queued") else "cancelling"
        return self.library.update_render(render_id, state=state, message="Cancelling…")

    def retry(self, render_id: str) -> dict:
        return self.resume(render_id)

    def _require(self, render_id: str) -> dict:
        render = self.library.get_render(render_id)
        if not render:
            raise ValueError("Render not found.")
        return render

    def _run(self, render_id: str) -> None:
        try:
            self.library.update_render(render_id, state="rendering", message="Rendering / အသံဖန်တီးနေသည်")
            render = self.library.get_render(render_id, include_chunks=True)
            assert render is not None
            config = render["config"]
            render_dir = self.library.render_path(render)
            for chunk in render["chunks"]:
                latest = self.library.get_render(render_id)
                if not latest:
                    return
                if latest["state"] in ("pausing", "paused"):
                    self.library.update_render(render_id, state="paused", message="Paused / ခေတ္တရပ်ထားသည်")
                    return
                if latest["state"] in ("cancelling", "cancelled"):
                    self.library.update_render(render_id, state="cancelled", message="Cancelled / ပယ်ဖျက်ပြီး")
                    return
                if chunk["state"] == "succeeded" and (render_dir / chunk["filename"]).exists():
                    continue
                style = chunk["style"]
                voice_config = config.get("voices", {}).get(chunk.get("voice_id") or "__builtin__", {"styles": {}})
                styles = voice_config.get("styles", {})
                requested_style = styles.get(style)
                style_config = requested_style or styles.get("neutral")
                recipe = prosody_for(style, float(config.get("emotion_intensity", 0.35)), requested_style is not None)
                output = render_dir / chunk["filename"]
                output.parent.mkdir(parents=True, exist_ok=True)
                self.library.update_render(
                    render_id,
                    current_chapter=chunk["chapter_position"],
                    message=f"Rendering part {chunk['position'] + 1} of {render['total_chunks']}",
                )
                try:
                    duration = self.model.generate_chunk(
                        chunk["text"],
                        output,
                        style_config["path"] if style_config else None,
                        style_config["transcript"] if style_config else None,
                        min(2.0, max(0.8, float(config["speed"]) * recipe["speed_factor"])),
                        config["preset"],
                        int(chunk["seed"]),
                    )
                    if abs(recipe["gain"] - 1.0) > 0.001:
                        audio, rate = sf.read(output, dtype="float32", always_2d=False)
                        sf.write(output, np.clip(np.asarray(audio) * recipe["gain"], -0.98, 0.98), rate, subtype="PCM_16")
                    self.library.update_render_chunk(chunk["id"], state="succeeded", duration=duration, error="")
                    completed = sum(
                        1 for item in self.library.get_render(render_id, include_chunks=True)["chunks"]
                        if item["state"] == "succeeded"
                    )
                    self.library.update_render(render_id, completed_chunks=completed)
                except Exception as exc:
                    self.library.update_render_chunk(chunk["id"], state="failed", error=str(exc))
                    raise

            self.library.update_render(render_id, state="finalizing", message="Joining audio / အသံများ ပေါင်းနေသည်")
            render = self.library.get_render(render_id, include_chunks=True)
            assert render is not None
            final_path = render_dir / "complete.wav"
            duration = self._stitch(render, final_path)
            chapter_positions = sorted({item["chapter_position"] for item in render["chunks"]})
            chapters_dir = render_dir / "chapters"
            chapters_dir.mkdir(exist_ok=True)
            for chapter_position in chapter_positions:
                self._stitch(render, chapters_dir / f"{chapter_position:03d}.wav", chapter_position)

            project = self.library.get_project(render["project_id"])
            assert project is not None
            combined_text = "\n\n".join(item["text"] for item in project["chapters"])
            generation_id, generation_path = self.library.create_generation(
                combined_text, project["voice_id"], config["voice_name"], project["speed"], project["preset"]
            )
            shutil.copy2(final_path, generation_path)
            self.library.save_generation(
                generation_id, combined_text, project["voice_id"], config["voice_name"],
                generation_path, duration, project["speed"], project["preset"],
            )
            # Publish the terminal state last. API clients and tests use it as
            # the guarantee that no worker still owns a database/file handle.
            self.library.update_project(render["project_id"], {"state": "completed"})
            self.library.update_render(
                render_id,
                state="succeeded",
                completed_chunks=render["total_chunks"],
                message="Ready to play / နားဆင်ရန် အသင့်ဖြစ်ပါပြီ",
                output_filename="complete.wav",
                duration=duration,
                generation_id=generation_id,
            )
        except Exception as exc:
            self.library.update_render(
                render_id,
                state="failed",
                error=str(exc),
                message="Render failed — completed parts are safe / မအောင်မြင်သော်လည်း ပြီးသောအပိုင်းများ သိမ်းထားသည်",
            )
        finally:
            with self._lock:
                self._scheduled.discard(render_id)

    @staticmethod
    def _pause_seconds(text: str, style: str = "neutral", intensity: float = 0.35) -> float:
        stripped = text.rstrip()
        if stripped.endswith("။"):
            base = 0.24
        elif stripped.endswith("၊"):
            base = 0.13
        elif stripped.endswith(("!", "?")):
            base = 0.22
        else:
            base = 0.08
        return base * prosody_for(style, intensity, False)["pause_factor"]

    def _stitch(self, render: dict, output: Path, chapter_position: int | None = None) -> float:
        chunks = [
            item for item in render["chunks"]
            if item["state"] == "succeeded"
            and (chapter_position is None or item["chapter_position"] == chapter_position)
        ]
        temporary = output.with_suffix(".tmp.wav")
        frame_count = 0
        target_rms: float | None = None
        fade_samples = int(SAMPLE_RATE * 0.025)
        with sf.SoundFile(
            temporary, mode="w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16", format="WAV"
        ) as destination:
            for chunk in chunks:
                path = self.library.render_path(render) / chunk["filename"]
                audio, rate = sf.read(path, dtype="float32", always_2d=False)
                if rate != SAMPLE_RATE:
                    raise RuntimeError("A render chunk has an unexpected sample rate.")
                audio = np.asarray(audio, dtype=np.float32).reshape(-1)
                rms = float(np.sqrt(np.mean(np.square(audio)) + 1e-9))
                if target_rms is None and rms > 1e-5:
                    target_rms = rms
                elif target_rms and rms > 1e-5:
                    gain = np.clip(target_rms / rms, 10 ** (-3 / 20), 10 ** (3 / 20))
                    audio = audio * gain
                    target_rms = target_rms * 0.95 + float(rms * gain) * 0.05
                edge = min(fade_samples, len(audio) // 4)
                if edge:
                    audio[:edge] *= np.linspace(0.0, 1.0, edge, dtype=np.float32)
                    audio[-edge:] *= np.linspace(1.0, 0.0, edge, dtype=np.float32)
                destination.write(np.clip(audio, -0.98, 0.98))
                intensity = float(render.get("config", {}).get("emotion_intensity", 0.35))
                pause = np.zeros(int(SAMPLE_RATE * self._pause_seconds(chunk["text"], chunk.get("style", "neutral"), intensity)), dtype=np.float32)
                destination.write(pause)
                frame_count += len(audio) + len(pause)
        os.replace(temporary, output)
        return frame_count / SAMPLE_RATE
