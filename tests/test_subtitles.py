import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from studio.storage import Library
from studio.subtitles import SubtitleQueue, parse_srt


SRT = """1
00:00:00,500 --> 00:00:02,000
မင်္ဂလာပါ။

2
00:00:03,000 --> 00:00:05,000
နေကောင်းပါသလား။
"""


class FakeModel:
    def generate_chunk(self, text, output, **kwargs):
        sf.write(output, np.zeros(12_000, dtype=np.float32), 24_000, subtype="PCM_16")
        return .5


class FakeAnalyzer:
    def analyze_sentence(self, text, mode):
        return type("Result", (), {"style": "warm"})()


class SubtitleTests(unittest.TestCase):
    def test_parse_srt_preserves_timestamps(self):
        cues = parse_srt(SRT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start"], .5)
        self.assertEqual(cues[1]["end"], 5)

    def test_timed_wav_uses_srt_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder))
            executor = ThreadPoolExecutor(max_workers=1)
            queue = SubtitleQueue(library, FakeModel(), FakeAnalyzer(), executor)
            job = queue.submit(SRT, "edge:my-MM-NilarNeural", 1, "auto", None, .35)
            executor.shutdown(wait=True)
            result = queue.get(job["id"])
            self.assertEqual(result["state"], "succeeded", result.get("error"))
            audio = library.generations_dir / f"{result['generation']['id']}.wav"
            with sf.SoundFile(audio) as source:
                self.assertAlmostEqual(len(source) / source.samplerate, 5, places=2)


if __name__ == "__main__":
    unittest.main()
