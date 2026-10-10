from __future__ import annotations

import html
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ALLOWED_HOSTS = ("xiaohongshu.com", "xhslink.com", "rednote.com")
SHARE_URL = re.compile(
    r"(?:(?:https?://)?(?:[A-Za-z0-9-]+\.)*(?:xiaohongshu\.com|xhslink\.com|rednote\.com)/[^\s<>\"']+)", re.I
)
TIMESTAMP = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})"
)
TAG = re.compile(r"<[^>]+>")


class DownloadStopped(RuntimeError):
    pass


def extract_rednote_url(value: str) -> str:
    """Accept either a clean URL or the complete text produced by RedNote's Share action."""
    source = html.unescape(str(value or "").strip())
    candidates = SHARE_URL.findall(source)
    if not candidates:
        raise ValueError("Paste a RedNote/Xiaohongshu share link or the complete copied share message.")
    url = candidates[0].rstrip(".,;:!?)]}，。！？；：】》」'\"")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def validate_rednote_url(value: str) -> tuple[str, dict]:
    url = extract_rednote_url(value)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
        raise ValueError("Enter a valid RedNote or Xiaohongshu share link.")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
        raise ValueError("Only RedNote/Xiaohongshu links are accepted in this studio.")
    if host.endswith(("xiaohongshu.com", "rednote.com")):
        path = parsed.path.lower()
        allowed_path = any(part in path for part in ("/explore/", "/discovery/item/", "/share/"))
        if not allowed_path and not parse_qs(parsed.query).get("xsec_token"):
            raise ValueError("Use a RedNote share, discovery, or explore link.")
    pieces = [piece for piece in parsed.path.split("/") if piece]
    content_id = ""
    for piece in reversed(pieces):
        if re.fullmatch(r"[A-Za-z0-9_-]{6,80}", piece):
            content_id = piece
            break
    identity = {"host": host, "content_id": content_id or "shared-link"}
    return url, identity


def resolve_rednote_url(url: str) -> str:
    """Resolve xhslink/share redirects to the direct URL supported by yt-dlp."""
    parsed = urlparse(url)
    direct = bool(re.search(r"/(?:explore|discovery/item)/[0-9a-f]+", parsed.path, re.I))
    if parsed.hostname and parsed.hostname.lower().endswith("rednote.com") and direct:
        # yt-dlp's XiaoHongShu extractor currently recognizes the equivalent
        # Xiaohongshu host. The path, content id, and access query are identical.
        return parsed._replace(netloc="www.xiaohongshu.com").geturl()
    if parsed.hostname and parsed.hostname.lower().endswith("xiaohongshu.com") and direct:
        return url
    try:
        import requests

        response = requests.get(
            url, allow_redirects=True, timeout=20, stream=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"},
        )
        response.raise_for_status()
        resolved = response.url
        response.close()
    except Exception as exc:
        raise RuntimeError("The RedNote short link could not be opened. Copy a fresh share link and retry.") from exc
    parsed = urlparse(resolved)
    host = (parsed.hostname or "").lower()
    if host.endswith("rednote.com") and re.search(r"/(?:explore|discovery/item)/[0-9a-f]+", parsed.path, re.I):
        return parsed._replace(netloc="www.xiaohongshu.com").geturl()
    if not host.endswith("xiaohongshu.com") or not re.search(r"/(?:explore|discovery/item)/[0-9a-f]+", parsed.path, re.I):
        raise RuntimeError("The share link did not resolve to a single RedNote video. Open it once in your browser, then copy its full address.")
    return resolved


def _seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


def parse_subtitles(path: Path) -> list[dict]:
    """Parse SRT/VTT into stable, de-duplicated speech cues."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[dict] = []
    index = 0
    while index < len(lines):
        match = TIMESTAMP.search(lines[index])
        if not match:
            index += 1
            continue
        start, end = _seconds(match.group("start")), _seconds(match.group("end"))
        index += 1
        content: list[str] = []
        while index < len(lines) and lines[index].strip():
            clean = TAG.sub("", lines[index]).strip()
            if clean and not clean.startswith(("NOTE", "Kind:")):
                content.append(clean)
            index += 1
        value = html.unescape(" ".join(content)).strip()
        value = re.sub(r"\s+", " ", value)
        if value and end > start:
            if cues and cues[-1]["text"] == value and start <= cues[-1]["end"] + .2:
                cues[-1]["end"] = max(cues[-1]["end"], end)
            else:
                cues.append({"start": start, "end": end, "text": value})
        index += 1
    return cues


def find_subtitle_file(folder: Path) -> Path | None:
    files = sorted((*folder.glob("*.srt"), *folder.glob("*.vtt")), key=lambda item: (item.suffix != ".srt", len(item.name)))
    for path in files:
        if parse_subtitles(path):
            return path
    return None


def classify_download_error(error: Exception) -> str:
    message = str(error).lower()
    if isinstance(error, DownloadStopped):
        return str(error)
    if any(token in message for token in ("captcha", "verify you are human", "risk control")):
        return "RedNote requires CAPTCHA verification. Open the link in Chrome or Edge, complete it, then retry."
    if any(token in message for token in ("private", "login", "sign in", "cookies")):
        return "This video requires a logged-in RedNote session. Sign in with Chrome or Edge, then retry."
    if any(token in message for token in ("expired", "invalid url", "not found", "404")):
        return "This share link has expired or is no longer available. Create a fresh RedNote share link."
    if any(token in message for token in ("no space", "disk full", "errno 28")):
        return "There is not enough disk space to download this video."
    if any(token in message for token in ("unsupported url", "playlist")):
        return "This RedNote link is not a supported single-video link."
    return "RedNote could not provide this video. Check the link or browser login and retry."


class RedNoteDownloader:
    def __init__(self, progress, should_stop):
        self.progress = progress
        self.should_stop = should_stop

    def _hook(self, job_id: str):
        def receive(status: dict) -> None:
            stop = self.should_stop(job_id)
            if stop:
                raise DownloadStopped(stop)
            state = status.get("status") or "downloading"
            downloaded = int(status.get("downloaded_bytes") or 0)
            total = int(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
            percent = round(downloaded * 100 / total, 1) if total else 0
            self.progress(job_id, {
                "state": state,
                "percent": percent,
                "downloaded_bytes": downloaded,
                "total_bytes": total,
                "speed_bps": int(status.get("speed") or 0),
                "eta_seconds": status.get("eta"),
            })
        return receive

    def download(self, job_id: str, url: str, folder: Path, best_quality: bool = True) -> dict:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("RedNote support is not installed yet. Close the studio and run start.cmd again.") from exc
        folder.mkdir(parents=True, exist_ok=True)
        url = resolve_rednote_url(url)
        output = str(folder / "rednote-source.%(ext)s")
        format_selector = (
            "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
            "best[vcodec^=avc1][ext=mp4]/bestvideo+bestaudio/best"
            if best_quality else "best[height<=1080]/best"
        )
        base = {
            "format": format_selector,
            "outtmpl": output,
            "noplaylist": True,
            "ignoreconfig": True,
            "continuedl": True,
            "nopart": False,
            "restrictfilenames": True,
            "merge_output_format": "mp4",
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": "srt/vtt/best",
            "subtitleslangs": ["all", "-live_chat"],
            "progress_hooks": [self._hook(job_id)],
            "quiet": True,
            "no_warnings": True,
            "overwrites": False,
        }
        last_error: Exception | None = None
        for browser in (None, "chrome", "edge"):
            options = dict(base)
            if browser:
                options["cookiesfrombrowser"] = (browser,)
            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    info = downloader.extract_info(url, download=True)
                    requested = info.get("requested_downloads") or []
                    candidates = [Path(item.get("filepath", "")) for item in requested if item.get("filepath")]
                    prepared = Path(downloader.prepare_filename(info))
                    candidates.extend([prepared, prepared.with_suffix(".mp4")])
                source = next((item for item in candidates if item.is_file()), None)
                if not source:
                    source = next((item for item in folder.glob("rednote-source.*") if item.is_file() and item.suffix not in (".part", ".srt", ".vtt", ".json")), None)
                if not source:
                    raise RuntimeError("The download finished without a playable video file.")
                target = folder / ("source" + source.suffix.lower())
                if source.resolve() != target.resolve():
                    target.unlink(missing_ok=True)
                    shutil.move(str(source), str(target))
                return {
                    "path": target,
                    "title": str(info.get("title") or "RedNote video")[:120],
                    "uploader": str(info.get("uploader") or "")[:120],
                    "duration": info.get("duration"),
                    "browser_session": browser or "anonymous",
                }
            except DownloadStopped:
                raise
            except Exception as exc:
                last_error = exc
        raise RuntimeError(classify_download_error(last_error or RuntimeError("Download failed"))) from last_error
