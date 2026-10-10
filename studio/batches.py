from __future__ import annotations

import threading
import time

from .projects import ProjectRenderQueue
from .storage import Library
from .text import validate_long_form


class BatchQueue:
    """Durable sequential orchestration for a folder of video scripts."""

    def __init__(self, library: Library, project_queue: ProjectRenderQueue) -> None:
        self.library = library
        self.project_queue = project_queue
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def start(self, batch_id: str) -> dict:
        batch = self.library.get_batch(batch_id)
        if not batch:
            raise ValueError("Batch not found.")
        self.library.update_batch(batch_id, state="queued", error="", message="Queued / စောင့်ဆိုင်းနေသည်")
        with self._lock:
            if batch_id not in self._running:
                self._running.add(batch_id)
                threading.Thread(target=self._run, args=(batch_id,), daemon=True, name=f"batch-{batch_id[:6]}").start()
        return self.library.get_batch(batch_id)

    def pause(self, batch_id: str) -> dict:
        batch = self._require(batch_id)
        self.library.update_batch(batch_id, state="pausing", message="Pausing after the current part…")
        active = next((item for item in batch["items"] if item["state"] == "rendering" and item["render_id"]), None)
        if active:
            self.project_queue.pause(active["render_id"])
        else:
            self.library.update_batch(batch_id, state="paused", message="Paused / ခေတ္တရပ်ထားသည်")
        return self.library.get_batch(batch_id)

    def resume(self, batch_id: str) -> dict:
        batch = self._require(batch_id)
        active = next((item for item in batch["items"] if item["state"] == "rendering" and item["render_id"]), None)
        if active:
            render = self.library.get_render(active["render_id"])
            if render and render["state"] in ("paused", "interrupted", "failed"):
                self.project_queue.resume(render["id"])
        return self.start(batch_id)

    def cancel(self, batch_id: str) -> dict:
        batch = self._require(batch_id)
        self.library.update_batch(batch_id, state="cancelling", message="Cancelling…")
        active = next((item for item in batch["items"] if item["state"] == "rendering" and item["render_id"]), None)
        if active:
            self.project_queue.cancel(active["render_id"])
        return self.library.get_batch(batch_id)

    def retry(self, batch_id: str) -> dict:
        batch = self._require(batch_id)
        for item in batch["items"]:
            if item["state"] == "failed":
                self.library.update_batch_item(item["id"], state="pending", error="")
        return self.start(batch_id)

    def _require(self, batch_id: str) -> dict:
        batch = self.library.get_batch(batch_id)
        if not batch:
            raise ValueError("Batch not found.")
        return batch

    def _run(self, batch_id: str) -> None:
        try:
            self.library.update_batch(batch_id, state="running", message="Rendering video scripts in order…")
            while True:
                batch = self._require(batch_id)
                if batch["state"] in ("pausing", "paused"):
                    self.library.update_batch(batch_id, state="paused", message="Paused / ခေတ္တရပ်ထားသည်")
                    return
                if batch["state"] in ("cancelling", "cancelled"):
                    for item in batch["items"]:
                        if item["state"] == "pending":
                            self.library.update_batch_item(item["id"], state="cancelled")
                    self.library.update_batch(batch_id, state="cancelled", message="Cancelled / ပယ်ဖျက်ပြီး")
                    return
                item = next((row for row in batch["items"] if row["state"] in ("pending", "rendering")), None)
                if not item:
                    failures = [row for row in batch["items"] if row["state"] == "failed"]
                    state = "completed_with_errors" if failures else "succeeded"
                    message = "Finished with some errors." if failures else "All video voiceovers are ready."
                    self.library.update_batch(batch_id, state=state, completed_items=len(batch["items"]) - len(failures), message=message)
                    return
                try:
                    self._run_item(batch_id, item)
                except Exception as exc:
                    self.library.update_batch_item(item["id"], state="failed", error=str(exc))
        finally:
            with self._lock:
                self._running.discard(batch_id)

    def _run_item(self, batch_id: str, item: dict) -> None:
        project_id, render_id = item.get("project_id"), item.get("render_id")
        if not project_id:
            clean, count = validate_long_form(item["script"])
            project = self.library.create_project(
                item["video_name"], item.get("voice_id"), float(item["speed"]), item["preset"],
                item["delivery_mode"], 0.45,
                [{"title": item["video_name"], "text": clean, "syllables": count, "voice_id": item.get("voice_id")}],
                batch_id=batch_id,
            )
            project_id = project["id"]
            self.project_queue.analyze(project_id)
            render = self.project_queue.start(project_id)
            render_id = render["id"]
            self.library.update_batch_item(item["id"], state="rendering", project_id=project_id, render_id=render_id)
        elif not render_id:
            render = self.project_queue.start(project_id)
            render_id = render["id"]
            self.library.update_batch_item(item["id"], state="rendering", render_id=render_id)

        while True:
            batch = self._require(batch_id)
            render = self.library.get_render(render_id)
            if not render:
                raise RuntimeError("The narration render disappeared.")
            if render["state"] == "succeeded":
                self.library.update_batch_item(item["id"], state="succeeded", error="")
                completed = sum(1 for row in self._require(batch_id)["items"] if row["state"] == "succeeded")
                self.library.update_batch(batch_id, completed_items=completed, message=f"Completed {completed} of {batch['total_items']}")
                return
            if render["state"] in ("failed", "cancelled"):
                raise RuntimeError(render.get("error") or render.get("message") or "Render failed.")
            if batch["state"] == "pausing":
                if render["state"] not in ("paused", "pausing"):
                    self.project_queue.pause(render_id)
                if render["state"] == "paused":
                    return
            if batch["state"] == "cancelling":
                if render["state"] not in ("cancelled", "cancelling"):
                    self.project_queue.cancel(render_id)
            time.sleep(0.8)
