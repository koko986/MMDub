from __future__ import annotations

import json
import os
import shutil
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from .storage import Library, now_iso


ACTIVE_STATES = ("queued", "downloading", "transcribing", "translating", "synthesizing", "syncing", "rendering", "pausing", "cancelling")


class VideoLibrary:
    def __init__(self, library: Library):
        self.library = library
        self.root = library.data / "video_jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with closing(self.library.connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS video_jobs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    error TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    duration REAL,
                    width INTEGER,
                    height INTEGER,
                    fps REAL,
                    output_filename TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS video_segments (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    source_start REAL NOT NULL,
                    source_end REAL NOT NULL,
                    source_text TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    style TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    voice_id TEXT,
                    manual_translation INTEGER NOT NULL DEFAULT 0,
                    manual_style INTEGER NOT NULL DEFAULT 0,
                    manual_voice INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    audio_filename TEXT,
                    audio_duration REAL,
                    warning TEXT NOT NULL,
                    UNIQUE(job_id, position),
                    FOREIGN KEY(job_id) REFERENCES video_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_video_jobs_updated ON video_jobs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_video_segments_position ON video_segments(job_id, position);
                CREATE TABLE IF NOT EXISTS video_speakers (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    gender TEXT NOT NULL,
                    voice_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, position),
                    FOREIGN KEY(job_id) REFERENCES video_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_video_speakers_job ON video_speakers(job_id, position);
                CREATE TABLE IF NOT EXISTS video_exports (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    encoder TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    elapsed_seconds REAL NOT NULL DEFAULT 0,
                    eta_seconds REAL,
                    output_filename TEXT,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, revision),
                    FOREIGN KEY(job_id) REFERENCES video_jobs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS video_export_chunks (
                    export_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL,
                    state TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    chunk_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(export_id, chunk_index),
                    FOREIGN KEY(export_id) REFERENCES video_exports(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_video_exports_job ON video_exports(job_id, revision DESC);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(video_segments)")}
            additions = {
                "scene_order": "INTEGER", "source_clips_json": "TEXT NOT NULL DEFAULT '[]'",
                "deleted": "INTEGER NOT NULL DEFAULT 0", "pause_after_ms": "INTEGER NOT NULL DEFAULT 180",
                "scene_speed": "REAL NOT NULL DEFAULT 1.0", "audio_hash": "TEXT NOT NULL DEFAULT ''",
                "audio_state": "TEXT NOT NULL DEFAULT 'dirty'",
                "speaker_id": "TEXT",
                "caption_only": "INTEGER NOT NULL DEFAULT 0",
                "is_user_added": "INTEGER NOT NULL DEFAULT 0",
                "caption_style_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE video_segments ADD COLUMN {name} {definition}")
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(video_jobs)")}
            job_additions = {
                "export_revision": "INTEGER NOT NULL DEFAULT 0",
                "source_kind": "TEXT NOT NULL DEFAULT 'upload'",
                "source_identity": "TEXT NOT NULL DEFAULT ''",
                "source_language": "TEXT NOT NULL DEFAULT ''",
                "transcript_source": "TEXT NOT NULL DEFAULT ''",
                "download_json": "TEXT NOT NULL DEFAULT '{}'",
                "blur_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, definition in job_additions.items():
                if name not in job_columns:
                    db.execute(f"ALTER TABLE video_jobs ADD COLUMN {name} {definition}")
            db.execute("UPDATE video_segments SET scene_order=position WHERE scene_order IS NULL")
            for row in db.execute("SELECT id,source_start,source_end,source_clips_json FROM video_segments"):
                if not row[3] or row[3] == "[]":
                    db.execute("UPDATE video_segments SET source_clips_json=? WHERE id=?",
                               (json.dumps([{"start": row[1], "end": row[2]}]), row[0]))
            db.execute("""UPDATE video_exports SET state='interrupted',stage='interrupted',
                       message='Export interrupted — ready to resume / ပြန်လည်စတင်နိုင်ပါသည်။',updated_at=?
                       WHERE state IN ('queued','rendering','finalizing')""", (now_iso(),))
            placeholders = ",".join("?" for _ in ACTIVE_STATES)
            db.execute(
                f"""UPDATE video_jobs SET state='interrupted',stage='interrupted',
                message='Interrupted — ready to resume / ပြန်လည်စတင်နိုင်ပါသည်။',updated_at=?
                WHERE state IN ({placeholders})""",
                (now_iso(), *ACTIVE_STATES),
            )

    def create(self, original_filename: str, suffix: str, settings: dict, *, source_kind: str = "upload",
               source_identity: str = "") -> tuple[dict, Path]:
        job_id = uuid4().hex
        timestamp = now_iso()
        safe_suffix = suffix.lower() if suffix.lower() in (".mp4", ".mov", ".mkv", ".webm") else ".mp4"
        source_filename = f"source{safe_suffix}"
        title = Path(original_filename).stem.strip()[:120] or "Untitled video"
        folder = self.root / job_id
        folder.mkdir(parents=True, exist_ok=True)
        with closing(self.library.connect()) as db, db:
            db.execute(
                """INSERT INTO video_jobs(id,title,original_filename,source_filename,state,stage,progress,total,
                message,error,settings_json,duration,width,height,fps,output_filename,created_at,updated_at,
                source_kind,source_identity)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, title, Path(original_filename).name, source_filename, "uploaded", "source", 0, 1,
                 "Ready to translate / ဘာသာပြန်ရန် အသင့်", "", json.dumps(settings, ensure_ascii=False),
                 None, None, None, None, None, timestamp, timestamp, source_kind, source_identity),
            )
        return self.get(job_id), folder / source_filename

    @staticmethod
    def _public(record: dict) -> dict:
        item = dict(record)
        raw = item.pop("settings_json", "{}")
        try:
            item["settings"] = json.loads(raw)
        except json.JSONDecodeError:
            item["settings"] = {}
        for source, target in (("download_json", "download"), ("blur_json", "subtitle_blur")):
            value = item.pop(source, "{}")
            try:
                item[target] = json.loads(value or "{}")
            except json.JSONDecodeError:
                item[target] = {}
        identity = item.get("source_identity") or ""
        if identity:
            try:
                item["source_identity"] = json.loads(identity)
            except json.JSONDecodeError:
                item["source_identity"] = {"host": identity, "content_id": ""}
        if item.get("output_filename"):
            item["video_url"] = f"/api/video-jobs/{item['id']}/artifacts/video"
        item["source_url"] = f"/api/video-jobs/{item['id']}/source"
        item["translation_mode"] = item["settings"].get("translation_mode", "gemini")
        item["source_srt_url"] = f"/api/video-jobs/{item['id']}/artifacts/source-srt"
        # Older builds persisted the full FFmpeg command. Keep APIs readable and actionable.
        if item.get("state") == "failed" and str(item.get("error", "")).startswith("Command '"):
            item["error"] = "Video export could not finish."
            item["message"] = "Your generated narration and edits are saved. Choose Retry / Resume to continue."
        return item

    def list(self) -> list[dict]:
        with closing(self.library.connect()) as db:
            rows = db.execute("SELECT * FROM video_jobs ORDER BY updated_at DESC").fetchall()
        return [self._public(dict(row)) for row in rows]

    def get(self, job_id: str, include_segments: bool = False) -> dict | None:
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return None
            segments = db.execute(
                "SELECT * FROM video_segments WHERE job_id=? ORDER BY scene_order,position", (job_id,)
            ).fetchall() if include_segments else []
        result = self._public(dict(row))
        if include_segments:
            result["segments"] = [self._segment_public(dict(segment)) for segment in segments]
            result["expected_cue_count"] = len(segments)
            result["source_transcript_ready"] = bool(segments)
        return result

    def update(self, job_id: str, **values) -> dict | None:
        if "settings" in values:
            values["settings_json"] = json.dumps(values.pop("settings"), ensure_ascii=False)
        if "download" in values:
            values["download_json"] = json.dumps(values.pop("download"), ensure_ascii=False)
        if "subtitle_blur" in values:
            values["blur_json"] = json.dumps(values.pop("subtitle_blur"), ensure_ascii=False)
        if not values:
            return self.get(job_id)
        values["updated_at"] = now_iso()
        with closing(self.library.connect()) as db, db:
            assignments = ",".join(f"{key}=?" for key in values)
            db.execute(f"UPDATE video_jobs SET {assignments} WHERE id=?", (*values.values(), job_id))
        return self.get(job_id)

    def replace_segments(self, job_id: str, segments: list[dict]) -> list[dict]:
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM video_segments WHERE job_id=?", (job_id,))
            for position, segment in enumerate(segments):
                db.execute(
                    """INSERT INTO video_segments(id,job_id,position,source_start,source_end,source_text,
                    translation,style,confidence,voice_id,manual_translation,manual_style,manual_voice,state,
                    audio_filename,audio_duration,warning,speaker_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uuid4().hex, job_id, position, segment["source_start"], segment["source_end"],
                     segment["source_text"], segment.get("translation", ""), segment.get("style", "neutral"),
                     segment.get("confidence", 0.5), None, 0, 0, 0, "pending", None, None, "", None),
                )
            rows = db.execute("SELECT id,source_start,source_end FROM video_segments WHERE job_id=? ORDER BY position", (job_id,)).fetchall()
            duration_row = db.execute("SELECT duration FROM video_jobs WHERE id=?", (job_id,)).fetchone()
            duration = float(duration_row[0] or 0) if duration_row else 0
            for index, row in enumerate(rows):
                start = 0.0 if index == 0 else (float(rows[index - 1][2]) + float(row[1])) / 2
                end = duration if index == len(rows) - 1 and duration else (
                    (float(row[2]) + float(rows[index + 1][1])) / 2 if index + 1 < len(rows) else float(row[2])
                )
                db.execute("UPDATE video_segments SET scene_order=?,source_clips_json=? WHERE id=?",
                           (index, json.dumps([{"start": max(0.0, start), "end": max(start + .04, end)}]), row[0]))
        return self.segments(job_id)

    @staticmethod
    def _segment_public(item: dict) -> dict:
        raw = item.pop("source_clips_json", "[]")
        try:
            item["source_clips"] = json.loads(raw)
        except json.JSONDecodeError:
            item["source_clips"] = [{"start": item["source_start"], "end": item["source_end"]}]
        raw_style = item.pop("caption_style_json", "{}")
        try:
            item["caption_style_override"] = json.loads(raw_style or "{}")
        except json.JSONDecodeError:
            item["caption_style_override"] = {}
        return item

    def segments(self, job_id: str) -> list[dict]:
        with closing(self.library.connect()) as db:
            rows = db.execute("SELECT * FROM video_segments WHERE job_id=? ORDER BY scene_order,position", (job_id,)).fetchall()
        return [self._segment_public(dict(row)) for row in rows]

    def update_segment(self, job_id: str, segment_id: str, values: dict) -> dict | None:
        allowed = {"translation", "style", "state", "audio_filename", "audio_duration", "warning",
                   "scene_order", "deleted", "pause_after_ms", "scene_speed", "audio_hash", "audio_state",
                   "caption_only", "source_start", "source_end"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if "source_clips" in values:
            updates["source_clips_json"] = json.dumps(values["source_clips"])
        if "caption_style_override" in values:
            updates["caption_style_json"] = json.dumps(values["caption_style_override"], ensure_ascii=False)
        if "translation" in values:
            updates["manual_translation"] = 1
        if "style" in values:
            updates["manual_style"] = 1
        if any(key in values for key in ("translation", "style", "scene_speed", "source_start", "source_end", "caption_only")):
            updates["audio_state"] = "dirty"
        if updates:
            with closing(self.library.connect()) as db, db:
                assignments = ",".join(f"{key}=?" for key in updates)
                db.execute(
                    f"UPDATE video_segments SET {assignments} WHERE id=? AND job_id=?",
                    (*updates.values(), segment_id, job_id),
                )
        return next((item for item in self.segments(job_id) if item["id"] == segment_id), None)

    def save_edit_plan(self, job_id: str, scenes: list[dict], settings: dict) -> dict:
        with closing(self.library.connect()) as db, db:
            known = {row[0] for row in db.execute("SELECT id FROM video_segments WHERE job_id=?", (job_id,))}
            supplied = {scene.get("id") for scene in scenes}
            if not supplied.issubset(known):
                raise ValueError("Edit plan contains an unknown scene.")
            for order, scene in enumerate(scenes):
                clips = scene.get("source_clips") or []
                if (not scene.get("is_user_added") and not clips) or any(float(c["start"]) < 0 or float(c["end"]) <= float(c["start"]) for c in clips):
                    raise ValueError("Every scene needs a valid picture range.")
                db.execute("""UPDATE video_segments SET scene_order=?,source_clips_json=?,deleted=?,pause_after_ms=?,
                           scene_speed=?,translation=?,style=?,caption_only=?,caption_style_json=?,audio_state=CASE WHEN
                           translation!=? OR style!=? OR scene_speed!=? OR caption_only!=?
                           THEN 'dirty' ELSE audio_state END WHERE id=? AND job_id=?""",
                           (order, json.dumps(clips), int(bool(scene.get("deleted"))), int(scene.get("pause_after_ms", 180)),
                            float(scene.get("scene_speed", 1.0)), scene.get("translation", ""), scene.get("style", "neutral"),
                            int(bool(scene.get("caption_only"))), json.dumps(scene.get("caption_style_override") or {}, ensure_ascii=False),
                            scene.get("translation", ""), scene.get("style", "neutral"),
                            float(scene.get("scene_speed", 1.0)), int(bool(scene.get("caption_only"))), scene["id"], job_id))
            db.execute("UPDATE video_jobs SET settings_json=?,updated_at=? WHERE id=?",
                       (json.dumps(settings, ensure_ascii=False), now_iso(), job_id))
        return self.get(job_id, include_segments=True)

    def create_segment(self, job_id: str, *, start: float, end: float, translation: str,
                       caption_only: bool = False, style: str = "neutral") -> dict:
        job = self.get(job_id)
        if not job:
            raise ValueError("Video job not found.")
        duration = float(job.get("duration") or 0)
        if start < 0 or end <= start or (duration and end > duration + .001):
            raise ValueError("Choose a valid cue range inside the source video.")
        if not translation.strip():
            raise ValueError("Burmese subtitle text cannot be blank.")
        rows = [row for row in self.segments(job_id) if not row.get("deleted")]
        if not caption_only and any(not row.get("caption_only") and
            max(start, float(row["source_start"])) < min(end, float(row["source_end"])) - .001 for row in rows):
            raise ValueError("Narrated cues cannot overlap. Adjust the start/end time or make this caption-only.")
        if not any(any(float(part["start"]) <= start < float(part["end"]) for part in (row.get("source_clips") or []))
                   for row in rows if not row.get("is_user_added")):
            raise ValueError("This cue must start inside a retained video range.")
        segment_id, stamp = uuid4().hex, now_iso()
        with closing(self.library.connect()) as db, db:
            position = int(db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM video_segments WHERE job_id=?", (job_id,)).fetchone()[0])
            order = sum(1 for row in rows if float(row["source_start"]) <= start)
            db.execute("UPDATE video_segments SET scene_order=scene_order+1 WHERE job_id=? AND scene_order>=?", (job_id, order))
            db.execute("""INSERT INTO video_segments(id,job_id,position,source_start,source_end,source_text,
                       translation,style,confidence,voice_id,manual_translation,manual_style,manual_voice,state,
                       audio_filename,audio_duration,warning,speaker_id,scene_order,source_clips_json,deleted,
                       pause_after_ms,scene_speed,audio_hash,audio_state,caption_only,is_user_added,caption_style_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (segment_id, job_id, position, start, end, "", translation.strip(), style, 1.0, None,
                        1, 1, 0, "pending", None, None, "", None, order, "[]", 0, 0, 1.0, "", "dirty",
                        int(caption_only), 1, "{}"))
            db.execute("UPDATE video_jobs SET updated_at=? WHERE id=?", (stamp, job_id))
        return next(row for row in self.segments(job_id) if row["id"] == segment_id)

    def begin_export(self, job_id: str, revision: int, mode: str, snapshot_hash: str, encoder: str) -> dict:
        timestamp, export_id = now_iso(), uuid4().hex
        with closing(self.library.connect()) as db, db:
            row = db.execute("SELECT * FROM video_exports WHERE job_id=? AND revision=?", (job_id, revision)).fetchone()
            if row and row["snapshot_hash"] == snapshot_hash:
                db.execute("UPDATE video_exports SET state='rendering',stage='preflight',error='',message=?,updated_at=? WHERE id=?",
                           ("Resuming saved export…", timestamp, row["id"]))
                return dict(db.execute("SELECT * FROM video_exports WHERE id=?", (row["id"],)).fetchone())
            if row:
                db.execute("DELETE FROM video_exports WHERE id=?", (row["id"],))
            db.execute("""INSERT INTO video_exports(id,job_id,revision,mode,snapshot_hash,state,stage,encoder,
                       progress,total,message,error,started_at,updated_at) VALUES(?,?,?,?,?,'rendering','preflight',?,0,0,?,'',?,?)""",
                       (export_id, job_id, revision, mode, snapshot_hash, encoder, "Preparing export…", timestamp, timestamp))
        return self.current_export(job_id)

    def update_export(self, export_id: str, **values) -> dict | None:
        values["updated_at"] = now_iso()
        with closing(self.library.connect()) as db, db:
            assignments = ",".join(f"{key}=?" for key in values)
            db.execute(f"UPDATE video_exports SET {assignments} WHERE id=?", (*values.values(), export_id))
            row = db.execute("SELECT * FROM video_exports WHERE id=?", (export_id,)).fetchone()
        return dict(row) if row else None

    def current_export(self, job_id: str) -> dict | None:
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM video_exports WHERE job_id=? ORDER BY revision DESC LIMIT 1", (job_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            chunks = db.execute("SELECT * FROM video_export_chunks WHERE export_id=? ORDER BY chunk_index", (item["id"],)).fetchall()
        item["chunks"] = [dict(chunk) for chunk in chunks]
        return item

    def save_export_chunk(self, export_id: str, index: int, start: float, end: float,
                          state: str, filename: str, chunk_hash: str) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute("""INSERT INTO video_export_chunks(export_id,chunk_index,start_time,end_time,state,filename,chunk_hash,updated_at)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(export_id,chunk_index) DO UPDATE SET
                       start_time=excluded.start_time,end_time=excluded.end_time,state=excluded.state,
                       filename=excluded.filename,chunk_hash=excluded.chunk_hash,updated_at=excluded.updated_at""",
                       (export_id, index, start, end, state, filename, chunk_hash, now_iso()))

    def duplicate_segment(self, job_id: str, segment_id: str) -> dict | None:
        with closing(self.library.connect()) as db, db:
            row = db.execute("SELECT * FROM video_segments WHERE id=? AND job_id=?", (segment_id, job_id)).fetchone()
            if not row:
                return None
            item = dict(row)
            new_id = uuid4().hex
            position = db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM video_segments WHERE job_id=?", (job_id,)).fetchone()[0]
            order = int(item.get("scene_order") or item["position"]) + 1
            db.execute("UPDATE video_segments SET scene_order=scene_order+1 WHERE job_id=? AND scene_order>=?", (job_id, order))
            columns = [key for key in item if key not in ("id", "position", "scene_order", "audio_filename", "audio_duration")]
            values = [item[key] for key in columns]
            db.execute(f"INSERT INTO video_segments(id,position,scene_order,{','.join(columns)}) VALUES(?,?,?,{','.join('?' for _ in columns)})",
                       (new_id, position, order, *values))
            db.execute("UPDATE video_segments SET state='pending',audio_state='dirty',audio_hash='' WHERE id=?", (new_id,))
            # A copied user-added narration would overlap its source cue exactly.
            # Keep the useful duplicate subtitle, but make it caption-only until the
            # user gives it a distinct time range.
            if item.get("is_user_added") and not item.get("caption_only"):
                db.execute("UPDATE video_segments SET caption_only=1 WHERE id=?", (new_id,))
        return next((row for row in self.segments(job_id) if row["id"] == new_id), None)

    def merge_translations(self, job_id: str, translations: dict[int, str]) -> None:
        with closing(self.library.connect()) as db, db:
            for position, translation in translations.items():
                db.execute(
                    """UPDATE video_segments SET translation=? WHERE job_id=? AND position=?
                    AND manual_translation=0""",
                    (translation, job_id, position),
                )

    def replace_manual_translations(self, job_id: str, translations: list[str]) -> None:
        """Replace an entire translated script atomically; never leave a partial import."""
        with closing(self.library.connect()) as db, db:
            rows = db.execute(
                "SELECT id FROM video_segments WHERE job_id=? ORDER BY position", (job_id,)
            ).fetchall()
            if len(rows) != len(translations):
                raise ValueError(f"Expected {len(rows)} cues, but the imported SRT has {len(translations)}.")
            for row, translation in zip(rows, translations):
                if not translation.strip():
                    raise ValueError("Every SRT cue needs Burmese translation text.")
                db.execute(
                    """UPDATE video_segments SET translation=?,manual_translation=1,state='pending',
                    audio_state='dirty',audio_hash='',warning='' WHERE id=? AND job_id=?""",
                    (translation.strip(), row[0], job_id),
                )

    def mark_audio_dirty(self, job_id: str) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute("UPDATE video_segments SET audio_state='dirty' WHERE job_id=?", (job_id,))

    def folder(self, job_id: str) -> Path:
        return self.root / job_id

    def speakers(self, job_id: str) -> list[dict]:
        with closing(self.library.connect()) as db:
            rows = db.execute("SELECT id,job_id,position,name,gender,voice_id,created_at,updated_at FROM video_speakers WHERE job_id=? ORDER BY position", (job_id,)).fetchall()
        return [dict(row) for row in rows]

    def save_cast(self, job_id: str, speakers: list[dict]) -> list[dict]:
        if not speakers:
            raise ValueError("Add at least one speaker.")
        with closing(self.library.connect()) as db, db:
            existing = {row[0] for row in db.execute("SELECT id FROM video_speakers WHERE job_id=?", (job_id,))}
            supplied: set[str] = set()
            stamp = now_iso()
            for position, item in enumerate(speakers):
                speaker_id = item.get("id") if item.get("id") in existing else uuid4().hex
                name = str(item.get("name") or "").strip()[:40]
                gender = str(item.get("gender") or "unknown").lower()
                if not name or gender not in ("female", "male", "unknown"):
                    raise ValueError("Every speaker needs a name and a valid gender category.")
                supplied.add(speaker_id)
                if speaker_id in existing:
                    db.execute("UPDATE video_speakers SET position=?,name=?,gender=?,voice_id=?,updated_at=? WHERE id=? AND job_id=?",
                               (position, name, gender, item["voice_id"], stamp, speaker_id, job_id))
                else:
                    db.execute("INSERT INTO video_speakers(id,job_id,position,name,gender,voice_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                               (speaker_id, job_id, position, name, gender, item["voice_id"], stamp, stamp))
            removed = existing - supplied
            if removed:
                marks = ",".join("?" for _ in removed)
                used = db.execute(f"SELECT COUNT(*) FROM video_segments WHERE job_id=? AND speaker_id IN ({marks})", (job_id, *removed)).fetchone()[0]
                if used:
                    raise ValueError("Reassign scenes before removing a speaker.")
                db.execute(f"DELETE FROM video_speakers WHERE job_id=? AND id IN ({marks})", (job_id, *removed))
        return self.speakers(job_id)

    def delete(self, job_id: str) -> bool:
        if not self.get(job_id):
            return False
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM video_jobs WHERE id=?", (job_id,))
        shutil.rmtree(self.folder(job_id), ignore_errors=True)
        return True
