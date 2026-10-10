from __future__ import annotations

import sqlite3
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Library:
    def __init__(self, root: Path):
        self.root = root
        self.data = root / "data"
        self.voices_dir = self.data / "voices"
        self.generations_dir = self.data / "generations"
        self.projects_dir = self.data / "projects"
        self.db_path = self.data / "library.sqlite3"
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with closing(self.connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS voices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    filename TEXT NOT NULL UNIQUE,
                    duration REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    voice_id TEXT,
                    voice_name TEXT NOT NULL,
                    filename TEXT NOT NULL UNIQUE,
                    duration REAL NOT NULL,
                    speed REAL NOT NULL,
                    preset TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS voice_styles (
                    id TEXT PRIMARY KEY,
                    voice_id TEXT NOT NULL,
                    style TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    duration REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(voice_id, style),
                    FOREIGN KEY(voice_id) REFERENCES voices(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    voice_id TEXT,
                    speed REAL NOT NULL,
                    preset TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL,
                    emotion_intensity REAL NOT NULL,
                    syllables INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_chapters (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    syllables INTEGER NOT NULL,
                    UNIQUE(project_id, position),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS project_segments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    paragraph_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    syllables INTEGER NOT NULL,
                    detected_style TEXT NOT NULL,
                    effective_style TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    intensity REAL NOT NULL,
                    explanation TEXT NOT NULL,
                    manual_override INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(project_id, position),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(chapter_id) REFERENCES project_chapters(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS project_renders (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    completed_chunks INTEGER NOT NULL,
                    current_chapter INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    error TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    output_filename TEXT,
                    duration REAL,
                    generation_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS render_chunks (
                    id TEXT PRIMARY KEY,
                    render_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    chapter_position INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    style TEXT NOT NULL,
                    state TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    duration REAL,
                    error TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    UNIQUE(render_id, position),
                    FOREIGN KEY(render_id) REFERENCES project_renders(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    total_items INTEGER NOT NULL,
                    completed_items INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_items (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    video_name TEXT NOT NULL,
                    script TEXT NOT NULL,
                    voice_id TEXT,
                    speed REAL NOT NULL,
                    preset TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    project_id TEXT,
                    render_id TEXT,
                    error TEXT NOT NULL,
                    UNIQUE(batch_id, position),
                    FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_voices_created_at
                    ON voices(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_generations_created_at
                    ON generations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_projects_updated_at
                    ON projects(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_render_chunks
                    ON render_chunks(render_id, position);
                CREATE INDEX IF NOT EXISTS idx_batch_items
                    ON batch_items(batch_id, position);
                """
            )
            self._ensure_column(db, "projects", "batch_id", "TEXT")
            self._ensure_column(db, "project_chapters", "voice_id", "TEXT")
            self._ensure_column(db, "project_segments", "voice_id", "TEXT")
            self._ensure_column(db, "project_segments", "manual_voice_override", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "render_chunks", "voice_id", "TEXT")
            db.execute(
                """INSERT OR IGNORE INTO voice_styles
                (id,voice_id,style,transcript,filename,duration,created_at)
                SELECT 'neutral-' || id,id,'neutral',transcript,filename,duration,created_at FROM voices"""
            )
            db.execute(
                """UPDATE project_renders SET state='interrupted',
                message='Interrupted — ready to resume / ပြန်လည်စတင်နိုင်ပါသည်။',
                updated_at=? WHERE state IN ('queued','rendering','finalizing')""",
                (now_iso(),),
            )
            db.execute(
                """UPDATE batches SET state='interrupted',
                message='Interrupted — ready to resume / ပြန်လည်စတင်နိုင်ပါသည်။',updated_at=?
                WHERE state IN ('queued','running','pausing','cancelling')""",
                (now_iso(),),
            )
            db.execute("PRAGMA optimize")

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def list_voices(self) -> list[dict]:
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT id, name, transcript, duration, created_at FROM voices ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_voice(self, voice_id: str) -> dict | None:
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM voices WHERE id = ?", (voice_id,)).fetchone()
        return dict(row) if row else None

    def create_voice(self, name: str, transcript: str, duration: float) -> tuple[dict, Path]:
        voice_id = uuid4().hex
        filename = f"{voice_id}.wav"
        record = {
            "id": voice_id,
            "name": name,
            "transcript": transcript,
            "filename": filename,
            "duration": round(duration, 2),
            "created_at": now_iso(),
        }
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO voices(id,name,transcript,filename,duration,created_at) VALUES(?,?,?,?,?,?)",
                tuple(record.values()),
            )
            db.execute(
                """INSERT INTO voice_styles(id,voice_id,style,transcript,filename,duration,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (f"neutral-{voice_id}", voice_id, "neutral", transcript, filename, round(duration, 2), record["created_at"]),
            )
        return {key: value for key, value in record.items() if key != "filename"}, self.voices_dir / filename

    def delete_voice(self, voice_id: str) -> bool:
        voice = self.get_voice(voice_id)
        if not voice:
            return False
        styles = self.list_voice_styles(voice_id, include_filename=True)
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM voices WHERE id = ?", (voice_id,))
        for style in styles:
            (self.voices_dir / style["filename"]).unlink(missing_ok=True)
        return True

    def list_voice_styles(self, voice_id: str, include_filename: bool = False) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM voice_styles WHERE voice_id=? ORDER BY CASE style WHEN 'neutral' THEN 0 ELSE 1 END, style",
                (voice_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        if not include_filename:
            for item in result:
                item.pop("filename", None)
                item["audio_url"] = f"/api/voices/{voice_id}/styles/{item['style']}/audio"
        return result

    def get_voice_style(self, voice_id: str, style: str) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT * FROM voice_styles WHERE voice_id=? AND style=?", (voice_id, style)
            ).fetchone()
        return dict(row) if row else None

    def upsert_voice_style(
        self, voice_id: str, style: str, transcript: str, duration: float
    ) -> tuple[dict, Path, str | None]:
        existing = self.get_voice_style(voice_id, style)
        style_id = existing["id"] if existing else uuid4().hex
        filename = f"{voice_id}-{style}-{uuid4().hex[:8]}.wav"
        created_at = now_iso()
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO voice_styles(id,voice_id,style,transcript,filename,duration,created_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(voice_id,style) DO UPDATE SET
                transcript=excluded.transcript,filename=excluded.filename,
                duration=excluded.duration,created_at=excluded.created_at""",
                (style_id, voice_id, style, transcript, filename, round(duration, 2), created_at),
            )
        record = self.get_voice_style(voice_id, style)
        assert record is not None
        old_filename = existing["filename"] if existing else None
        return record, self.voices_dir / filename, old_filename

    def delete_voice_style(self, voice_id: str, style: str) -> bool:
        if style == "neutral":
            raise ValueError("Neutral is required and cannot be deleted.")
        record = self.get_voice_style(voice_id, style)
        if not record:
            return False
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM voice_styles WHERE voice_id=? AND style=?", (voice_id, style))
        (self.voices_dir / record["filename"]).unlink(missing_ok=True)
        return True

    def create_generation(
        self, text: str, voice_id: str | None, voice_name: str, speed: float, preset: str
    ) -> tuple[str, Path]:
        generation_id = uuid4().hex
        return generation_id, self.generations_dir / f"{generation_id}.wav"

    def save_generation(
        self,
        generation_id: str,
        text: str,
        voice_id: str | None,
        voice_name: str,
        path: Path,
        duration: float,
        speed: float,
        preset: str,
    ) -> dict:
        record = {
            "id": generation_id,
            "text": text,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "filename": path.name,
            "duration": round(duration, 2),
            "speed": speed,
            "preset": preset,
            "created_at": now_iso(),
        }
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO generations
                (id,text,voice_id,voice_name,filename,duration,speed,preset,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                tuple(record.values()),
            )
        return self._public_generation(record)

    @staticmethod
    def _public_generation(record: dict) -> dict:
        public = {key: value for key, value in record.items() if key != "filename"}
        public["audio_url"] = f"/api/generations/{record['id']}/audio"
        return public

    def list_generations(self, limit: int = 50) -> list[dict]:
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT * FROM generations ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._public_generation(dict(row)) for row in rows]

    def get_generation(self, generation_id: str) -> dict | None:
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM generations WHERE id = ?", (generation_id,)).fetchone()
        return dict(row) if row else None

    def delete_generation(self, generation_id: str) -> bool:
        record = self.get_generation(generation_id)
        if not record:
            return False
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM generations WHERE id = ?", (generation_id,))
        (self.generations_dir / record["filename"]).unlink(missing_ok=True)
        return True

    # Long-form projects -------------------------------------------------
    def create_project(
        self,
        title: str,
        voice_id: str | None,
        speed: float,
        preset: str,
        delivery_mode: str,
        emotion_intensity: float,
        chapters: list[dict],
        batch_id: str | None = None,
    ) -> dict:
        project_id = uuid4().hex
        timestamp = now_iso()
        total = sum(int(chapter["syllables"]) for chapter in chapters)
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO projects
                (id,title,voice_id,speed,preset,delivery_mode,emotion_intensity,syllables,state,created_at,updated_at,batch_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (project_id, title, voice_id, speed, preset, delivery_mode, emotion_intensity, total, "draft", timestamp, timestamp, batch_id),
            )
            for position, chapter in enumerate(chapters):
                db.execute(
                    """INSERT INTO project_chapters(id,project_id,position,title,text,syllables,voice_id)
                    VALUES(?,?,?,?,?,?,?)""",
                    (uuid4().hex, project_id, position, chapter["title"], chapter["text"], chapter["syllables"], chapter.get("voice_id")),
                )
        (self.projects_dir / project_id).mkdir(parents=True, exist_ok=True)
        return self.get_project(project_id)

    def list_projects(self) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM projects WHERE batch_id IS NULL ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                return None
            chapters = db.execute(
                "SELECT * FROM project_chapters WHERE project_id=? ORDER BY position", (project_id,)
            ).fetchall()
            segments = db.execute(
                "SELECT * FROM project_segments WHERE project_id=? ORDER BY position", (project_id,)
            ).fetchall()
            renders = db.execute(
                "SELECT * FROM project_renders WHERE project_id=? ORDER BY created_at DESC", (project_id,)
            ).fetchall()
        result = dict(row)
        result["chapters"] = [dict(item) for item in chapters]
        result["segments"] = [dict(item) for item in segments]
        result["renders"] = [self._public_render(dict(item)) for item in renders]
        return result

    def update_project(self, project_id: str, values: dict, chapters: list[dict] | None = None) -> dict | None:
        allowed = ("title", "voice_id", "speed", "preset", "delivery_mode", "emotion_intensity", "state")
        updates = {key: values[key] for key in allowed if key in values}
        updates["updated_at"] = now_iso()
        with closing(self.connect()) as db, db:
            if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                return None
            if chapters is not None:
                existing = db.execute(
                    "SELECT title,text,voice_id FROM project_chapters WHERE project_id=? ORDER BY position",
                    (project_id,),
                ).fetchall()
                changed = [(row["title"], row["text"], row["voice_id"]) for row in existing] != [
                    (chapter["title"], chapter["text"], chapter.get("voice_id")) for chapter in chapters
                ]
                if changed:
                    db.execute("DELETE FROM project_segments WHERE project_id=?", (project_id,))
                    db.execute("DELETE FROM project_chapters WHERE project_id=?", (project_id,))
                    total = 0
                    for position, chapter in enumerate(chapters):
                        total += int(chapter["syllables"])
                        db.execute(
                            """INSERT INTO project_chapters(id,project_id,position,title,text,syllables,voice_id)
                            VALUES(?,?,?,?,?,?,?)""",
                            (uuid4().hex, project_id, position, chapter["title"], chapter["text"], chapter["syllables"], chapter.get("voice_id")),
                        )
                    updates["syllables"] = total
                    updates["state"] = "draft"
            assignments = ",".join(f"{key}=?" for key in updates)
            db.execute(
                f"UPDATE projects SET {assignments} WHERE id=?",
                (*updates.values(), project_id),
            )
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        if not self.get_project(project_id):
            return False
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        import shutil
        shutil.rmtree(self.projects_dir / project_id, ignore_errors=True)
        return True

    def replace_segments(self, project_id: str, chapter_segments: list[tuple[str, list[dict]]]) -> list[dict]:
        position = 0
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM project_segments WHERE project_id=?", (project_id,))
            for chapter_id, segments in chapter_segments:
                for segment in segments:
                    db.execute(
                        """INSERT INTO project_segments
                        (id,project_id,chapter_id,position,paragraph_index,text,syllables,
                        detected_style,effective_style,confidence,intensity,explanation,manual_override,voice_id,manual_voice_override)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            uuid4().hex, project_id, chapter_id, position,
                            segment["paragraph_index"], segment["text"], segment["syllables"],
                            segment["detected_style"], segment["effective_style"], segment["confidence"],
                            segment["intensity"], segment["explanation"], int(segment["manual_override"]),
                            segment.get("voice_id"), int(segment.get("manual_voice_override", False)),
                        ),
                    )
                    position += 1
            db.execute(
                "UPDATE projects SET state='analyzed',updated_at=? WHERE id=?",
                (now_iso(), project_id),
            )
        project = self.get_project(project_id)
        return project["segments"] if project else []

    def update_segment_style(self, project_id: str, segment_id: str, style: str | None) -> dict | None:
        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT * FROM project_segments WHERE id=? AND project_id=?", (segment_id, project_id)
            ).fetchone()
            if not row:
                return None
            effective = style or row["detected_style"]
            db.execute(
                """UPDATE project_segments SET effective_style=?,manual_override=?
                WHERE id=? AND project_id=?""",
                (effective, int(style is not None), segment_id, project_id),
            )
        project = self.get_project(project_id)
        return next((item for item in project["segments"] if item["id"] == segment_id), None) if project else None

    def update_segment(self, project_id: str, segment_id: str, *, style_marker=False, style=None,
                       voice_marker=False, voice_id=None) -> dict | None:
        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT * FROM project_segments WHERE id=? AND project_id=?", (segment_id, project_id)
            ).fetchone()
            if not row:
                return None
            values: dict[str, object] = {}
            if style_marker:
                values["effective_style"] = style or row["detected_style"]
                values["manual_override"] = int(style is not None)
            if voice_marker:
                values["voice_id"] = voice_id
                values["manual_voice_override"] = int(voice_id is not None)
            if values:
                assignments = ",".join(f"{key}=?" for key in values)
                db.execute(f"UPDATE project_segments SET {assignments} WHERE id=?", (*values.values(), segment_id))
        project = self.get_project(project_id)
        return next((item for item in project["segments"] if item["id"] == segment_id), None) if project else None

    def create_render(self, project_id: str, seed: int, config: dict, chunks: list[dict]) -> dict:
        render_id = uuid4().hex
        timestamp = now_iso()
        render_dir = self.projects_dir / project_id / "renders" / render_id
        (render_dir / "chunks").mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO project_renders
                (id,project_id,state,total_chunks,completed_chunks,current_chapter,message,error,
                seed,config_json,output_filename,duration,generation_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (render_id, project_id, "queued", len(chunks), 0, 0, "Queued / စောင့်ဆိုင်းနေသည်", "",
                 seed, json.dumps(config, ensure_ascii=False), None, None, None, timestamp, timestamp),
            )
            for position, chunk in enumerate(chunks):
                filename = f"chunks/{position:06d}.wav"
                db.execute(
                    """INSERT INTO render_chunks
                    (id,render_id,position,chapter_position,text,style,state,filename,duration,error,seed,voice_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uuid4().hex, render_id, position, chunk["chapter_position"], chunk["text"],
                     chunk["style"], "pending", filename, None, "", chunk["seed"], chunk.get("voice_id")),
                )
            db.execute("UPDATE projects SET state='rendering',updated_at=? WHERE id=?", (timestamp, project_id))
        return self.get_render(render_id)

    @staticmethod
    def _public_render(record: dict) -> dict:
        record = dict(record)
        config = record.pop("config_json", "{}")
        try:
            record["config"] = json.loads(config)
        except json.JSONDecodeError:
            record["config"] = {}
        if record.get("output_filename"):
            record["audio_url"] = f"/api/renders/{record['id']}/audio"
        return record

    def get_render(self, render_id: str, include_chunks: bool = False) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM project_renders WHERE id=?", (render_id,)).fetchone()
            if not row:
                return None
            chunks = db.execute(
                "SELECT * FROM render_chunks WHERE render_id=? ORDER BY position", (render_id,)
            ).fetchall() if include_chunks else []
        result = self._public_render(dict(row))
        if include_chunks:
            result["chunks"] = [dict(item) for item in chunks]
        return result

    def update_render(self, render_id: str, **values) -> dict | None:
        if not values:
            return self.get_render(render_id)
        values["updated_at"] = now_iso()
        with closing(self.connect()) as db, db:
            assignments = ",".join(f"{key}=?" for key in values)
            db.execute(f"UPDATE project_renders SET {assignments} WHERE id=?", (*values.values(), render_id))
        return self.get_render(render_id)

    def update_render_chunk(self, chunk_id: str, **values) -> None:
        with closing(self.connect()) as db, db:
            assignments = ",".join(f"{key}=?" for key in values)
            db.execute(f"UPDATE render_chunks SET {assignments} WHERE id=?", (*values.values(), chunk_id))

    def render_path(self, render: dict) -> Path:
        return self.projects_dir / render["project_id"] / "renders" / render["id"]

    # Batch video queue --------------------------------------------------
    def create_batch(self, title: str, items: list[dict]) -> dict:
        batch_id, timestamp = uuid4().hex, now_iso()
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT INTO batches(id,title,state,total_items,completed_items,message,error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (batch_id, title, "queued", len(items), 0, "Queued / စောင့်ဆိုင်းနေသည်", "", timestamp, timestamp),
            )
            for position, item in enumerate(items):
                db.execute(
                    """INSERT INTO batch_items(id,batch_id,position,video_name,script,voice_id,speed,preset,
                    delivery_mode,state,project_id,render_id,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uuid4().hex, batch_id, position, item["video_name"], item["script"], item.get("voice_id"),
                     item["speed"], item["preset"], item["delivery_mode"], "pending", None, None, ""),
                )
        return self.get_batch(batch_id)

    def list_batches(self) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT * FROM batches ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_batch(self, batch_id: str) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if not row:
                return None
            items = db.execute("SELECT * FROM batch_items WHERE batch_id=? ORDER BY position", (batch_id,)).fetchall()
        result = dict(row)
        result["items"] = [dict(item) for item in items]
        return result

    def update_batch(self, batch_id: str, **values) -> dict | None:
        if not values:
            return self.get_batch(batch_id)
        values["updated_at"] = now_iso()
        with closing(self.connect()) as db, db:
            assignments = ",".join(f"{key}=?" for key in values)
            db.execute(f"UPDATE batches SET {assignments} WHERE id=?", (*values.values(), batch_id))
        return self.get_batch(batch_id)

    def update_batch_item(self, item_id: str, **values) -> None:
        with closing(self.connect()) as db, db:
            assignments = ",".join(f"{key}=?" for key in values)
            db.execute(f"UPDATE batch_items SET {assignments} WHERE id=?", (*values.values(), item_id))

    def delete_batch(self, batch_id: str) -> bool:
        batch = self.get_batch(batch_id)
        if not batch:
            return False
        with closing(self.connect()) as db, db:
            project_ids = [item["project_id"] for item in batch["items"] if item["project_id"]]
            db.execute("DELETE FROM batches WHERE id=?", (batch_id,))
            for project_id in project_ids:
                db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        import shutil
        for project_id in project_ids:
            shutil.rmtree(self.projects_dir / project_id, ignore_errors=True)
        return True
