import io
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

os.environ["BURMESE_TTS_SKIP_MODEL_LOAD"] = "1"

import app as app_module
from studio.emotion import EmotionAnalyzer
from studio.batches import BatchQueue
from studio.jobs import JobQueue
from studio.projects import ProjectRenderQueue
from studio.storage import Library
from studio.text import LONG_FORM_MAXIMUM, validate_long_form


class BasicAnalyzer(EmotionAnalyzer):
    def __init__(self):
        super().__init__()
        self._attempted = True
        self.state = "basic"


class FakeModel:
    def generate_chunk(self, text, output_path, ref_audio, ref_text, speed, preset, seed):
        sf.write(output_path, np.full(2400, 0.05, dtype=np.float32), 24_000, subtype="PCM_16")
        return 0.1


class LongFormTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = (
            app_module.library,
            app_module.jobs,
            app_module.analyzer,
            app_module.project_queue,
            app_module.batch_queue,
        )
        self.executor = ThreadPoolExecutor(max_workers=1)
        app_module.library = Library(Path(self.temp.name))
        app_module.analyzer = BasicAnalyzer()
        app_module.jobs = JobQueue(
            app_module.library, app_module.model, executor=self.executor, analyzer=app_module.analyzer
        )
        app_module.project_queue = ProjectRenderQueue(
            app_module.library, app_module.model, app_module.analyzer, self.executor
        )
        app_module.batch_queue = BatchQueue(app_module.library, app_module.project_queue)
        self.client = TestClient(app_module.app)

    def tearDown(self):
        # Worker completion is the point at which SQLite/WAV handles are
        # guaranteed closed on Windows.
        self.executor.shutdown(wait=True, cancel_futures=True)
        (
            app_module.library,
            app_module.jobs,
            app_module.analyzer,
            app_module.project_queue,
            app_module.batch_queue,
        ) = self.previous
        self.temp.cleanup()

    @staticmethod
    def wav_bytes(seconds=1.0):
        buffer = io.BytesIO()
        sf.write(buffer, np.zeros(round(24_000 * seconds), dtype=np.float32), 24_000, format="WAV")
        return buffer.getvalue()

    def create_voice(self):
        response = self.client.post(
            "/api/voices",
            data={"name": "Narrator", "transcript": "မင်္ဂလာပါ။"},
            files={"audio": ("voice.wav", self.wav_bytes(), "audio/wav")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_voice_styles_migrate_and_round_trip(self):
        voice_id = self.create_voice()
        styles = self.client.get(f"/api/voices/{voice_id}/styles").json()
        self.assertEqual([item["style"] for item in styles], ["neutral"])
        response = self.client.post(
            f"/api/voices/{voice_id}/styles",
            data={"style": "happy", "transcript": "ကျွန်တော် ပျော်ရွှင်ပါတယ်။"},
            files={"audio": ("happy.wav", self.wav_bytes(), "audio/wav")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        styles = self.client.get(f"/api/voices/{voice_id}/styles").json()
        self.assertEqual({item["style"] for item in styles}, {"neutral", "happy"})
        self.assertEqual(
            self.client.delete(f"/api/voices/{voice_id}/styles/happy").status_code, 204
        )
        self.assertEqual(
            self.client.delete(f"/api/voices/{voice_id}/styles/neutral").status_code, 400
        )

    def test_project_accepts_over_500_and_keeps_manual_override(self):
        text = "က" * 501 + "။"
        response = self.client.post(
            "/api/projects",
            json={
                "title": "Long narration",
                "voice_id": None,
                "speed": 1,
                "preset": "fast",
                "delivery_mode": "consistent",
                "emotion_intensity": 0.35,
                "chapters": [{"title": "One", "text": text}],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        project = response.json()
        self.assertEqual(project["syllables"], 501)
        analyzed = self.client.post(f"/api/projects/{project['id']}/analyze")
        self.assertEqual(analyzed.status_code, 200, analyzed.text)
        segment = analyzed.json()["segments"][0]
        changed = self.client.patch(
            f"/api/projects/{project['id']}/segments/{segment['id']}",
            json={"style": "serious"},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(changed.json()["manual_override"])
        unchanged = self.client.put(
            f"/api/projects/{project['id']}",
            json={"title": "Renamed", "chapters": [{"title": "One", "text": text}]},
        )
        self.assertEqual(unchanged.status_code, 200)
        self.assertEqual(unchanged.json()["segments"][0]["effective_style"], "serious")

    def test_long_form_limit(self):
        text, count = validate_long_form("က" * LONG_FORM_MAXIMUM)
        self.assertEqual(count, LONG_FORM_MAXIMUM)
        self.assertTrue(text)
        response = self.client.post(
            "/api/projects",
            json={
                "title": "Too long",
                "chapters": [{"title": "One", "text": "က" * (LONG_FORM_MAXIMUM + 1)}],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_checkpointed_render_writes_combined_and_chapter_audio(self):
        library = app_module.library
        chapter = {"title": "One", "text": "မင်္ဂလာပါ။ ကျေးဇူးတင်ပါတယ်။", "syllables": 13}
        project = library.create_project(
            "Render test", None, 1.0, "fast", "consistent", 0.35, [chapter]
        )
        queue = ProjectRenderQueue(library, FakeModel(), app_module.analyzer, self.executor)
        queue.analyze(project["id"])
        render = queue.start(project["id"])
        deadline = time.time() + 5
        while time.time() < deadline:
            render = library.get_render(render["id"])
            if render["state"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(render["state"], "succeeded", render.get("error"))
        render_dir = library.render_path(render)
        self.assertTrue((render_dir / "complete.wav").exists())
        self.assertTrue((render_dir / "chapters" / "000.wav").exists())
        self.assertEqual(render["completed_chunks"], render["total_chunks"])

    def test_builtin_auto_emotion_and_turbo_speed_two(self):
        response = self.client.post(
            "/api/emotions/analyze",
            json={"text": "ကျွန်တော် အရမ်းပျော်ရွှင်ပါတယ်! နေကောင်းလား။", "delivery_mode": "conversational"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        segments = response.json()["segments"]
        self.assertGreaterEqual(len(segments), 2)
        self.assertTrue(all(item["performance"] == "approximate" for item in segments))
        project = self.client.post(
            "/api/projects",
            json={"title": "Turbo", "speed": 2.0, "preset": "turbo", "chapters": [{"text": "မင်္ဂလာပါ။"}]},
        )
        self.assertEqual(project.status_code, 201, project.text)

    def test_multi_speaker_chapters_are_frozen_in_render(self):
        first, second = self.create_voice(), self.create_voice()
        response = self.client.post(
            "/api/projects",
            json={
                "title": "Cast", "voice_id": first, "chapters": [
                    {"title": "One", "text": "မင်္ဂလာပါ။", "voice_id": first},
                    {"title": "Two", "text": "ကျေးဇူးတင်ပါတယ်။", "voice_id": second},
                ],
            },
        )
        project = response.json()
        queue = ProjectRenderQueue(app_module.library, FakeModel(), app_module.analyzer, self.executor)
        queue.analyze(project["id"])
        render = queue.start(project["id"])
        detail = app_module.library.get_render(render["id"], include_chunks=True)
        self.assertEqual({chunk["voice_id"] for chunk in detail["chunks"]}, {first, second})
        self.assertEqual(set(detail["config"]["voices"]) - {"__builtin__"}, {first, second})

    def test_two_video_batch_completes_sequentially(self):
        queue = ProjectRenderQueue(app_module.library, FakeModel(), app_module.analyzer, self.executor)
        app_module.project_queue = queue
        app_module.batch_queue = BatchQueue(app_module.library, queue)
        response = self.client.post(
            "/api/batches",
            json={"title": "Videos", "items": [
                {"video_name": "One", "script": "မင်္ဂလာပါ။", "preset": "turbo"},
                {"video_name": "Two", "script": "ကျေးဇူးတင်ပါတယ်။", "preset": "turbo"},
            ]},
        )
        self.assertEqual(response.status_code, 202, response.text)
        batch_id = response.json()["id"]
        deadline = time.time() + 8
        while time.time() < deadline:
            batch = self.client.get(f"/api/batches/{batch_id}").json()
            if batch["state"] in ("succeeded", "completed_with_errors"):
                break
            time.sleep(0.05)
        self.assertEqual(batch["state"], "succeeded", batch)
        self.assertTrue(all(item["state"] == "succeeded" for item in batch["items"]))


if __name__ == "__main__":
    unittest.main()
