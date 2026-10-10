from __future__ import annotations

import tempfile
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from studio.secrets import GeminiKeyStore
from studio.storage import Library
from studio.video_dubbing import (GeminiTranslator, VideoDubbingQueue, atomicize_transcript, ffmpeg_path,
    friendly_video_failure, parse_manual_translation_srt, probe_video, safe_blur_radius,
    shorten_timed_cues, split_caption_text, validate_manual_translation, visual_windows, write_source_artifacts)
from studio.engine import ModelManager
from studio.emotion import EmotionAnalyzer
from studio.video_storage import VideoLibrary


class _Models:
    def __init__(self, answer=None, error=None, prompts=None):
        self.answer, self.error, self.prompts = answer, error, prompts

    def generate_content(self, **kwargs):
        if self.prompts is not None:
            self.prompts.append(kwargs.get("contents", ""))
        if self.error:
            raise RuntimeError(self.error)
        return type("Response", (), {"text": self.answer})()


class _Client:
    def __init__(self, answer=None, error=None, prompts=None):
        self.models = _Models(answer, error, prompts)

    def close(self):
        pass


class _NoopExecutor:
    def submit(self, *_args, **_kwargs):
        return None


class VideoDubbingTests(unittest.TestCase):
    def test_blur_strength_is_limited_to_region_size(self):
        self.assertEqual(safe_blur_radius(35, 960, 720, .74, .09), 15)
        error = subprocess.CalledProcessError(22, ["ffmpeg"], stderr=b"Invalid luma_param radius value 35\nboxblur invalid argument")
        title, recovery = friendly_video_failure(error, "render")
        self.assertIn("Subtitle blur", title)
        self.assertIn("Retry / Resume", recovery)

    def test_key_store_round_trip_is_encrypted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GeminiKeyStore(Path(folder))
            store.save(["example-key-that-is-long-enough"])
            self.assertEqual(store.load(), ["example-key-that-is-long-enough"])
            self.assertNotIn(b"example-key", store.path.read_bytes())

    def test_transcript_atomicization_preserves_order_and_duration(self):
        payload = {"segments": [{"words": [
            {"start": 0, "end": 2, "word": "Hello"},
            {"start": 2, "end": 4.4, "word": "world."},
            {"start": 4.5, "end": 7, "word": "Next"},
        ]}]}
        units = atomicize_transcript(payload)
        self.assertEqual(" ".join(row["source_text"] for row in units), "Hello world. Next")
        self.assertTrue(all(row["source_end"] - row["source_start"] <= 3.8 for row in units))
        windows = visual_windows(units, 8)
        self.assertEqual(windows[0][0], 0)
        self.assertEqual(windows[-1][1], 8)
        self.assertAlmostEqual(windows[0][1], windows[1][0])

    def test_key_failover_uses_next_key_without_exposing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GeminiKeyStore(Path(folder))
            store.save(["first-key-that-will-always-fail", "second-key-that-will-work-now"])
            translator = GeminiTranslator(store, lambda key: _Client(error="quota") if key.startswith("first") else _Client('{"ok":true}'))
            result = translator.test()
            self.assertTrue(result["ok"])
            self.assertIn("Key 2", result["route"])
            self.assertNotIn("second-key", str(result))

    def test_region_failure_stops_failover_immediately(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GeminiKeyStore(Path(folder))
            store.save(["first-key-that-is-long-enough", "second-key-that-is-long-enough"])
            calls = []
            def factory(key):
                calls.append(key)
                return _Client(error="User location is not supported for the API use.")
            with self.assertRaisesRegex(RuntimeError, "network region"):
                GeminiTranslator(store, factory).test()
            self.assertEqual(len(calls), 1)

    def test_translation_prompt_requests_spoken_localization_and_story_adaptation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GeminiKeyStore(Path(folder))
            store.save(["working-key-that-is-long-enough"])
            prompts = []
            answer = '{"translations":[{"id":0,"burmese":"မင်္ဂလာပါ"}]}'
            translator = GeminiTranslator(store, lambda _key: _Client(answer, prompts=prompts))
            result, _ = translator.translate(
                [{"position": 0, "source_text": "Once upon a time, a traveler arrived."}],
                lambda *_: None,
            )
            self.assertEqual(result[0], "မင်္ဂလာပါ")
            self.assertIn("spoken localization, not a literal", prompts[0])
            self.assertIn("storytelling or documentary narration", prompts[0])
            self.assertIn("short on-screen subtitle", prompts[0])

    def test_long_burmese_caption_splits_without_changing_text_order(self):
        text = "ဒီနေ့မနက်မှာ ကျွန်တော်တို့အားလုံး အရေးကြီးတဲ့ သတင်းတစ်ခုကို အတူတူ နားထောင်ကြပါမယ်။ ပြီးရင် ဆက်လက်ရှင်းပြပါမယ်။"
        parts = split_caption_text(text, "short", duration=8)
        self.assertGreater(len(parts), 1)
        self.assertEqual(" ".join(parts).replace("  ", " "), text)
        self.assertTrue(all(part.strip() for part in parts))

    def test_platform_subtitles_are_split_before_translation(self):
        cues = [{"start": 0, "end": 8, "text": "one two three four five six seven eight nine ten eleven twelve"}]
        units = shorten_timed_cues(cues, maximum=3)
        self.assertEqual(" ".join(item["source_text"] for item in units), cues[0]["text"])
        self.assertGreaterEqual(len(units), 3)
        self.assertAlmostEqual(units[0]["source_start"], 0)
        self.assertAlmostEqual(units[-1]["source_end"], 8)

    def test_video_metadata_and_manual_review_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder))
            videos = VideoLibrary(library)
            job, _ = videos.create("sample.mp4", ".mp4", {"preset": "turbo"})
            segments = videos.replace_segments(job["id"], [{"source_start": 0, "source_end": 2,
                "source_text": "Hello", "translation": "မင်္ဂလာပါ", "style": "warm"}])
            changed = videos.update_segment(job["id"], segments[0]["id"], {"translation": "မင်္ဂလာပါခင်ဗျာ"})
            self.assertEqual(changed["manual_translation"], 1)
            self.assertEqual(videos.get(job["id"], include_segments=True)["segments"][0]["translation"], "မင်္ဂလာပါခင်ဗျာ")

    def test_added_caption_cue_and_style_override_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder)); videos = VideoLibrary(library)
            job, _ = videos.create("captions.mp4", ".mp4", {"export_mode": "subtitles_only"})
            videos.update(job["id"], duration=10)
            videos.replace_segments(job["id"], [
                {"source_start": 1, "source_end": 2, "source_text": "Hello", "translation": "မင်္ဂလာပါ"},
            ])
            cue = videos.create_segment(job["id"], start=3, end=4.5, translation="စာတန်းအသစ်", caption_only=True)
            changed = videos.update_segment(job["id"], cue["id"], {
                "caption_style_override": {"font": "Padauk", "size": 56, "color": "#FFE082",
                                           "position": "custom", "x": .42, "y": .24}
            })
            self.assertEqual(changed["caption_style_override"]["font"], "Padauk")
            self.assertEqual(changed["caption_style_override"]["size"], 56)
            self.assertEqual(changed["caption_style_override"]["position"], "custom")
            self.assertEqual(changed["caption_only"], 1)
            self.assertEqual(changed["is_user_added"], 1)
            with self.assertRaisesRegex(ValueError, "cannot overlap"):
                videos.create_segment(job["id"], start=1.5, end=2.5, translation="ထပ်နေသည်")

    def test_export_checkpoint_is_resumable(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder)); videos = VideoLibrary(library)
            job, _ = videos.create("long.mp4", ".mp4", {"export_mode": "subtitles_only"})
            export = videos.begin_export(job["id"], 1, "subtitles_only", "snapshot", "libx264")
            videos.save_export_chunk(export["id"], 0, 0, 300, "complete", "chunk-0000.mp4", "hash")
            resumed = videos.begin_export(job["id"], 1, "subtitles_only", "snapshot", "libx264")
            current = videos.current_export(job["id"])
            self.assertEqual(resumed["id"], export["id"])
            self.assertEqual(current["chunks"][0]["state"], "complete")

    def test_manual_srt_contract_accepts_bom_and_keeps_source_timing(self):
        content = "\ufeff1\n00:00:00,100 --> 00:00:01,900\nမင်္ဂလာပါ\n\n2\n00:00:02,000 --> 00:00:03,000\nနေကောင်းလား\n"
        cues = parse_manual_translation_srt(content)
        translations, warning = validate_manual_translation(cues, [
            {"source_start": 0, "source_end": 2}, {"source_start": 2, "source_end": 3}
        ])
        self.assertEqual(translations, ["မင်္ဂလာပါ", "နေကောင်းလား"])
        self.assertIn("1 imported timestamp", warning)

    def test_manual_srt_rejects_duplicate_reordered_and_blank_cues(self):
        invalid = [
            "1\n00:00:00,000 --> 00:00:01,000\nမင်္ဂလာပါ\n\n1\n00:00:01,000 --> 00:00:02,000\nကောင်းပြီ",
            "2\n00:00:00,000 --> 00:00:01,000\nမင်္ဂလာပါ\n\n1\n00:00:01,000 --> 00:00:02,000\nကောင်းပြီ",
            "1\n00:00:00,000 --> 00:00:01,000\n",
        ]
        for content in invalid:
            with self.subTest(content=content[:10]), self.assertRaises(ValueError):
                parse_manual_translation_srt(content)

    def test_manual_translation_replacement_is_atomic_and_source_artifacts_are_numbered(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder)); videos = VideoLibrary(library)
            job, _ = videos.create("manual.mp4", ".mp4", {"translation_mode": "manual"})
            rows = videos.replace_segments(job["id"], [
                {"source_start": 0, "source_end": 1, "source_text": "Hello"},
                {"source_start": 1, "source_end": 2, "source_text": "World"},
            ])
            write_source_artifacts(videos.folder(job["id"]), rows)
            self.assertTrue((videos.folder(job["id"]) / "source.srt").exists())
            self.assertTrue((videos.folder(job["id"]) / "source.txt").read_text(encoding="utf-8").startswith("1. Hello"))
            with self.assertRaises(ValueError):
                videos.replace_manual_translations(job["id"], ["မင်္ဂလာပါ"])
            self.assertTrue(all(not row["translation"] for row in videos.segments(job["id"])))
            videos.replace_manual_translations(job["id"], ["မင်္ဂလာပါ", "ကမ္ဘာကြီး"])
            saved = videos.get(job["id"], include_segments=True)
            self.assertTrue(all(row["manual_translation"] for row in saved["segments"]))
            self.assertTrue(all(row["audio_state"] == "dirty" for row in saved["segments"]))
            self.assertEqual(saved["translation_mode"], "manual")

    def test_manual_queue_waits_then_imports_without_gemini(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder)); videos = VideoLibrary(library)
            job, _ = videos.create("manual.mp4", ".mp4", {"translation_mode": "manual", "delivery_mode": "consistent"})
            videos.replace_segments(job["id"], [
                {"source_start": 0, "source_end": 1, "source_text": "Hello"},
                {"source_start": 1, "source_end": 2, "source_text": "World"},
            ])
            queue = VideoDubbingQueue(library, videos, ModelManager(), EmotionAnalyzer(),
                                      GeminiKeyStore(library.data), _NoopExecutor())
            waiting = queue.await_manual_translation(job["id"])
            self.assertEqual(waiting["state"], "awaiting_translation")
            imported = queue.import_manual_translation(job["id"],
                "1\n00:00:00,000 --> 00:00:01,000\nမင်္ဂလာပါ\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\nကမ္ဘာကြီး\n")
            self.assertEqual(imported["state"], "review_ready")
            self.assertTrue(all(row["manual_translation"] for row in imported["segments"]))
            self.assertTrue((videos.folder(job["id"]) / "burmese.txt").exists())

    def test_archived_cast_is_preserved_but_ignored_by_new_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder))
            videos = VideoLibrary(library)
            job, _ = videos.create("cast.mp4", ".mp4", {"voice_id": "edge:my-MM-NilarNeural"})
            videos.update(job["id"], duration=4)
            segments = videos.replace_segments(job["id"], [
                {"source_start": .5, "source_end": 1.2, "source_text": "Hi", "translation": "မင်္ဂလာပါ"},
                {"source_start": 2.5, "source_end": 3.1, "source_text": "Bye", "translation": "သွားတော့မယ်"},
            ])
            archived = videos.save_cast(job["id"], [
                {"name": "Girl", "gender": "female", "voice_id": "edge:en-US-EmmaMultilingualNeural"}
            ])
            self.assertEqual(videos.speakers(job["id"])[0]["id"], archived[0]["id"])
            public = videos.get(job["id"], include_segments=True)
            self.assertNotIn("speakers", public)
            changed = videos.update_segment(job["id"], segments[1]["id"], {"speaker_id": archived[0]["id"]})
            self.assertIsNone(changed["speaker_id"])
            videos.mark_audio_dirty(job["id"])
            self.assertTrue(all(item["audio_state"] == "dirty" for item in videos.segments(job["id"])))
            self.assertEqual(segments[0]["source_clips"][0]["start"], 0)
            self.assertAlmostEqual(segments[0]["source_clips"][0]["end"], 1.85)
            self.assertEqual(segments[1]["source_clips"][0]["end"], 4)

    def test_smart_sync_exports_narration_only_mp4(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder))
            videos = VideoLibrary(library)
            job, source = videos.create("sample.mp4", ".mp4", {"output_ratio": "original", "frame_fill": "fit"})
            subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", str(source)],
                           check=True, capture_output=True)
            videos.update(job["id"], **probe_video(source))
            videos.update(job["id"], subtitle_blur={"enabled": True, "x": .1, "y": .72,
                "width": .8, "height": .16, "strength": 5, "confidence": .8, "status": "detected"})
            rows = videos.replace_segments(job["id"], [{"source_start": 0, "source_end": 1,
                "source_text": "Hello", "translation": "မင်္ဂလာပါ", "style": "neutral"}])
            clips = videos.folder(job["id"]) / "audio-clips"
            clips.mkdir()
            sf.write(clips / "00000.wav", np.zeros(12_000, dtype=np.float32), 24_000, subtype="PCM_16")
            videos.update_segment(job["id"], rows[0]["id"], {"state": "succeeded", "audio_filename": "00000.wav", "audio_duration": .5})
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                queue = VideoDubbingQueue(library, videos, ModelManager(), EmotionAnalyzer(), GeminiKeyStore(library.data), executor)
                queue._smart_sync(job["id"])
                result = videos.get(job["id"])
                self.assertEqual(result["state"], "succeeded")
                self.assertTrue((videos.folder(job["id"]) / "burmese-dub.mp4").exists())
                self.assertTrue((videos.folder(job["id"]) / "burmese.srt").exists())
                rendered = probe_video(videos.folder(job["id"]) / "burmese-dub.mp4")
                self.assertAlmostEqual(rendered["duration"], 1.0, delta=.12)
                plan = (videos.folder(job["id"]) / "sync-plan.json").read_text(encoding="utf-8")
                self.assertIn('"native_duration": 1.0', plan)
            finally:
                executor.shutdown(wait=True)

    def test_subtitle_only_export_skips_narration_and_preserves_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder)); videos = VideoLibrary(library)
            settings = {"output_ratio": "original", "frame_fill": "fit", "export_mode": "subtitles_only",
                        "caption_style": {"font": "Noto Sans Myanmar", "size": 38, "color": "#FFFFFF",
                                          "position": "lower", "x": .5, "y": .9}}
            job, source = videos.create("subtitle.mp4", ".mp4", settings)
            subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
                            "-f", "lavfi", "-i", "sine=frequency=330:duration=1", "-c:v", "libx264", "-c:a", "aac", str(source)],
                           check=True, capture_output=True)
            videos.update(job["id"], **probe_video(source))
            videos.replace_segments(job["id"], [{"source_start": 0, "source_end": 1,
                "source_text": "Hello", "translation": "မင်္ဂလာပါ", "style": "neutral"}])
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                queue = VideoDubbingQueue(library, videos, ModelManager(), EmotionAnalyzer(), GeminiKeyStore(library.data), executor)
                queue._smart_sync(job["id"])
                result = videos.get(job["id"])
                self.assertEqual(result["state"], "succeeded")
                self.assertFalse((videos.folder(job["id"]) / "narration.wav").exists())
                self.assertAlmostEqual(probe_video(videos.folder(job["id"]) / "burmese-dub.mp4")["duration"], 1, delta=.12)
            finally:
                executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
