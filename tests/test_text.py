import unittest

from studio.text import normalize_text, split_passage, syllable_count, validate_burmese


class TextTests(unittest.TestCase):
    def test_normalizes_spaces_and_newlines(self):
        self.assertEqual(normalize_text("  မင်္ဂလာပါ။   နေကောင်းလား။  "), "မင်္ဂလာပါ။ နေကောင်းလား။")

    def test_rejects_blank_and_non_burmese(self):
        with self.assertRaises(ValueError):
            validate_burmese("   ")
        with self.assertRaises(ValueError):
            validate_burmese("hello world")

    def test_enforces_passage_limit(self):
        text = "က" * 501
        with self.assertRaises(ValueError):
            validate_burmese(text)

    def test_splits_at_safe_syllable_size(self):
        text = "က" * 47 + "။ " + "မ" * 44 + "။"
        chunks = split_passage(text, limit=45)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertTrue(all(syllable_count(chunk) <= 45 for chunk in chunks))

    def test_short_passage_stays_together(self):
        text = "မင်္ဂလာပါ။ ဒီနေ့ နေကောင်းလား။"
        self.assertEqual(split_passage(text), [text])


if __name__ == "__main__":
    unittest.main()

