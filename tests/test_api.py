import io
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

os.environ["BURMESE_TTS_SKIP_MODEL_LOAD"] = "1"

import app as app_module
from studio.jobs import JobQueue
from studio.storage import Library


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_library = app_module.library
        self.previous_jobs = app_module.jobs
        app_module.library = Library(Path(self.temp.name))
        app_module.jobs = JobQueue(app_module.library, app_module.model)
        self.client = TestClient(app_module.app)

    def tearDown(self):
        app_module.jobs.executor.shutdown(wait=False, cancel_futures=True)
        app_module.library = self.previous_library
        app_module.jobs = self.previous_jobs
        self.temp.cleanup()

    @staticmethod
    def wav_bytes(seconds=1.0):
        buffer = io.BytesIO()
        sf.write(buffer, np.zeros(round(24_000 * seconds), dtype=np.float32), 24_000, format="WAV")
        return buffer.getvalue()

    def test_status_and_default_voice(self):
        status = self.client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["local_only"])
        voices = self.client.get("/api/voices").json()
        self.assertTrue(voices[0]["built_in"])

    def test_voice_upload_persists_and_deletes(self):
        response = self.client.post(
            "/api/voices",
            data={"name": "My voice", "transcript": "မင်္ဂလာပါ။"},
            files={"audio": ("voice.wav", self.wav_bytes(), "audio/wav")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        voice_id = response.json()["id"]
        self.assertEqual(len(self.client.get("/api/voices").json()), 7)
        self.assertIsNotNone(app_module.library.get_voice(voice_id))
        self.assertEqual(self.client.delete(f"/api/voices/{voice_id}").status_code, 204)
        self.assertIsNone(app_module.library.get_voice(voice_id))

    def test_rejects_non_wav_and_non_burmese(self):
        bad_audio = self.client.post(
            "/api/voices",
            data={"name": "Test", "transcript": "မင်္ဂလာပါ။"},
            files={"audio": ("voice.txt", b"not audio", "text/plain")},
        )
        self.assertEqual(bad_audio.status_code, 400)
        bad_text = self.client.post(
            "/api/voices",
            data={"name": "Test", "transcript": "hello"},
            files={"audio": ("voice.wav", self.wav_bytes(), "audio/wav")},
        )
        self.assertEqual(bad_text.status_code, 400)

    def test_job_input_validation(self):
        missing_consent = self.client.post(
            "/api/jobs", json={"text": "မင်္ဂလာပါ။", "speed": 1.0, "preset": "balanced"}
        )
        self.assertEqual(missing_consent.status_code, 400)
        self.assertIn("Confirm", missing_consent.json()["detail"])
        response = self.client.post(
            "/api/jobs", json={"text": "hello", "online_speech_consent": True, "speed": 1.0, "preset": "balanced"}
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/jobs", json={"text": "မင်္ဂလာပါ။", "online_speech_consent": True, "speed": 2.01, "preset": "balanced"}
        )
        self.assertEqual(response.status_code, 400)

    def test_policy_pages_are_available(self):
        for path in ("/privacy", "/terms", "/cookies"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("MMDub", response.text)


if __name__ == "__main__":
    unittest.main()
