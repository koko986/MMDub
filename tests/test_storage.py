import tempfile
import unittest
from pathlib import Path

from studio.storage import Library


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Library(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_voice_profile_persists_and_deletes(self):
        voice, path = self.library.create_voice("Test voice", "မင်္ဂလာပါ။", 3.2)
        path.write_bytes(b"wav")
        self.assertEqual(self.library.get_voice(voice["id"])["name"], "Test voice")
        self.assertTrue(self.library.delete_voice(voice["id"]))
        self.assertFalse(path.exists())

    def test_generation_is_listed_newest_first(self):
        first_id, first_path = self.library.create_generation("က", None, "Built-in", 1.0, "fast")
        first_path.write_bytes(b"one")
        self.library.save_generation(first_id, "က", None, "Built-in", first_path, 1.0, 1.0, "fast")
        second_id, second_path = self.library.create_generation("ခ", None, "Built-in", 1.0, "high")
        second_path.write_bytes(b"two")
        self.library.save_generation(second_id, "ခ", None, "Built-in", second_path, 2.0, 1.0, "high")
        records = self.library.list_generations()
        self.assertEqual(records[0]["id"], second_id)
        self.assertEqual(records[0]["audio_url"], f"/api/generations/{second_id}/audio")


if __name__ == "__main__":
    unittest.main()

