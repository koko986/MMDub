from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.rednote import classify_download_error, parse_subtitles, resolve_rednote_url, validate_rednote_url


class RedNoteTests(unittest.TestCase):
    def test_only_rednote_hosts_are_accepted_and_identity_is_sanitized(self):
        url, identity = validate_rednote_url(
            "https://www.xiaohongshu.com/explore/abc123456?xsec_token=secret-token"
        )
        self.assertIn("secret-token", url)
        self.assertEqual(identity, {"host": "www.xiaohongshu.com", "content_id": "abc123456"})
        self.assertNotIn("secret", str(identity))
        validate_rednote_url("https://xhslink.com/aBcD1234")
        pasted, pasted_identity = validate_rednote_url(
            "复制打开小红书看看这个视频 88 xhslink.com/a/AbCdEfGh 真的很好看！"
        )
        self.assertEqual(pasted, "https://xhslink.com/a/AbCdEfGh")
        self.assertEqual(pasted_identity["host"], "xhslink.com")
        with self.assertRaises(ValueError):
            validate_rednote_url("https://example.com/explore/abc123456")
        with self.assertRaises(ValueError):
            validate_rednote_url("file:///C:/video.mp4")

    def test_short_link_is_resolved_before_downloader(self):
        response = type("Response", (), {
            "url": "https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9?xsec_token=abc",
            "raise_for_status": lambda self: None,
            "close": lambda self: None,
        })()
        with patch("requests.get", return_value=response) as request:
            resolved = resolve_rednote_url("https://xhslink.com/a/AbCdEfGh")
        self.assertIn("/explore/6411cf99000000001300b6d9", resolved)
        request.assert_called_once()

    def test_rednote_domain_is_normalized_for_ytdlp(self):
        copied = (
            "23 [A video | rednote] share-code "
            "https://www.rednote.com/discovery/item/6a421427000000000f006380"
            "?source=webshare&xsec_token=private-token&xsec_source=pc_share"
        )
        url, identity = validate_rednote_url(copied)
        self.assertEqual(identity["host"], "www.rednote.com")
        normalized = resolve_rednote_url(url)
        self.assertTrue(normalized.startswith(
            "https://www.xiaohongshu.com/discovery/item/6a421427000000000f006380?"
        ))
        self.assertIn("xsec_token=private-token", normalized)

    def test_platform_subtitles_keep_timestamps_and_merge_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.zh.srt"
            path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHello <b>world</b>\n\n"
                "2\n00:00:02,100 --> 00:00:03,000\nHello world\n\n"
                "3\n00:00:04,000 --> 00:00:05,250\nNext line\n",
                encoding="utf-8",
            )
            cues = parse_subtitles(path)
            self.assertEqual(len(cues), 2)
            self.assertEqual(cues[0], {"start": 1.0, "end": 3.0, "text": "Hello world"})
            self.assertEqual(cues[1]["text"], "Next line")

    def test_download_failures_are_actionable(self):
        self.assertIn("CAPTCHA", classify_download_error(RuntimeError("captcha required")))
        self.assertIn("logged-in", classify_download_error(RuntimeError("login required")))
        self.assertIn("disk space", classify_download_error(OSError("No space left on device")))


if __name__ == "__main__":
    unittest.main()
