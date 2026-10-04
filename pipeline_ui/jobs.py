"""Shared async job-tracking registry for pipeline_ui background jobs.

Each long-running HTTP action (FAL scene-anchor i2i, anchor-preview batch/assemble,
conversation-master regen) starts work on a background thread and returns a job_id
for polling instead of holding the HTTP connection open (FAL/ffmpeg work can take
minutes, and the dev-reload server does not survive that long). This module holds
the generic thread/lock/prune/dedup bookkeeping so each job type only supplies the
actual work function.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any


class JobRegistry:
    """Tracks background jobs by id, with optional dedup on a caller-supplied key."""

    def __init__(self, max_age_sec: float = 3600):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._by_key: dict[str, str] = {}
        self._lock = threading.Lock()
        self._max_age_sec = max_age_sec

    def prune(self) -> None:
        cutoff = time.time() - self._max_age_sec
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                started = float(job.get("started_at") or 0)
                if started >= cutoff:
                    continue
                key = job.get("key")
                if key and self._by_key.get(key) == job_id:
                    del self._by_key[key]
                del self._jobs[job_id]

    def start(
        self,
        target: Callable[..., None],
        *,
        args: tuple = (),
        key: str | None = None,
        public: dict[str, Any] | None = None,
        thread_name: str = "job",
    ) -> tuple[str, bool]:
        """Start ``target(job_id, *args)`` on a daemon thread.

        Returns ``(job_id, already_running)``. If ``key`` matches a job already
        running, no new thread is started and that job's id is reused.
        """
        self.prune()
        with self._lock:
            if key:
                existing_id = self._by_key.get(key)
                if existing_id:
                    existing = self._jobs.get(existing_id)
                    if existing and existing.get("status") == "running":
                        return existing_id, True
            job_id = uuid.uuid4().hex
            job: dict[str, Any] = {"status": "running", "started_at": time.time(), "key": key}
            if public:
                job["public"] = dict(public)
            self._jobs[job_id] = job
            if key:
                self._by_key[key] = job_id
        threading.Thread(
            target=target,
            args=(job_id, *args),
            daemon=True,
            name=f"{thread_name}-{job_id[:8]}",
        ).start()
        return job_id, False

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "done" if result.get("ok") else "failed"
            job["result"] = result
            job["finished_at"] = time.time()
            key = job.get("key")
            if key and self._by_key.get(key) == job_id:
                del self._by_key[key]

    def status(self, job_id: str) -> dict[str, Any]:
        self.prune()
        jid = (job_id or "").strip()
        if not jid:
            return {"ok": False, "error": "missing_job_id"}
        with self._lock:
            job = self._jobs.get(jid)
        if not job:
            return {"ok": False, "error": "unknown_job_id", "job_id": jid}
        status = str(job.get("status") or "running")
        if status == "running":
            out = {"ok": True, "status": "running", "job_id": jid}
            out.update(job.get("public") or {})
            return out
        result = job.get("result")
        if not isinstance(result, dict):
            return {"ok": False, "status": "failed", "job_id": jid, "error": "job_missing_result"}
        out = dict(result)
        out["job_id"] = jid
        out["status"] = status
        return out
