"""Job directories, life cycle, concurrency limit and TTL clean-up (PLAN.md §5.2).

Every job owns `<DATA_DIR>/jobs/<jobId>/` with `inputs/` (uploaded files, esmini's working directory), `work/`
(intermediate files) and `outputs/` (the only directory from which artefacts are served), plus `result.json`.

A job is admitted when it is submitted, then runs in a background task: `queued` until a semaphore slot is free,
`running` while it holds the slot, `finished` once `result.json` exists. The states of unfinished jobs live in
memory only; after a restart such a job is `interrupted`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.errors import ApiError
from app.models.simulation import JobState, SimulationResult

logger = logging.getLogger(__name__)

# secrets.token_urlsafe(16): 128 random bits, 22 URL-safe characters.
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
RESULT_FILE = "result.json"


@dataclass(frozen=True)
class Job:
    id: str
    dir: Path

    @property
    def inputs(self) -> Path:
        return self.dir / "inputs"

    @property
    def work(self) -> Path:
        return self.dir / "work"

    @property
    def outputs(self) -> Path:
        return self.dir / "outputs"

    @property
    def submitted_at(self) -> datetime | None:
        """When the inputs were stored at submission (the `inputs/` directory is not written to afterwards)."""
        try:
            return datetime.fromtimestamp(self.inputs.stat().st_mtime, timezone.utc)
        except FileNotFoundError:
            return None

    def write_result(self, result: SimulationResult) -> None:
        tmp = self.dir / (RESULT_FILE + ".tmp")
        tmp.write_text(result.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
        tmp.replace(self.dir / RESULT_FILE)

    def read_result(self) -> SimulationResult | None:
        path = self.dir / RESULT_FILE
        if not path.is_file():
            return None
        return SimulationResult.model_validate_json(path.read_text(encoding="utf-8"))

    def artifact_paths(self) -> list[Path]:
        if not self.outputs.is_dir():
            return []
        return sorted(p for p in self.outputs.iterdir() if p.is_file())

    def artifact(self, name: str) -> Path | None:
        """Look the name up among the existing files; user input is never joined into a path unchecked."""
        return next((p for p in self.artifact_paths() if p.name == name), None)


class JobStore:
    def __init__(self, root: Path, *, max_concurrent: int, max_queued: int, ttl_seconds: float) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.max_concurrent = max_concurrent
        self.max_queued = max_queued
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._states: dict[str, JobState] = {}  # unfinished jobs only
        self._tasks: set[asyncio.Task[None]] = set()

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def submit(self) -> Job:
        """Admit and create a job if fewer than `max_concurrent + max_queued` are unfinished, else fail with 429."""
        if len(self._states) >= self.max_concurrent + self.max_queued:
            raise ApiError(
                429,
                "too_many_requests",
                "The service is busy; retry later.",
                headers={"Retry-After": "5"},
            )
        self.ensure_root()
        job_id = secrets.token_urlsafe(16)
        job = Job(job_id, self.root / job_id)
        for d in (job.inputs, job.work, job.outputs):
            d.mkdir(parents=True)
        self._states[job_id] = JobState.QUEUED
        return job

    @contextlib.asynccontextmanager
    async def slot(self, job: Job) -> AsyncIterator[None]:
        """Wait for one of the `max_concurrent` slots; the job is `running` while it holds it."""
        async with self._semaphore:
            self._states[job.id] = JobState.RUNNING
            yield

    def finish(self, job: Job) -> None:
        """Release the admission of a job; call it once its result is written (or it was abandoned)."""
        self._states.pop(job.id, None)

    def state(self, job: Job) -> JobState:
        """`interrupted` for a job that is neither unfinished nor has a result, e.g. after a restart."""
        if job.id in self._states:
            return self._states[job.id]
        return JobState.FINISHED if (job.dir / RESULT_FILE).is_file() else JobState.INTERRUPTED

    def start(self, work: Coroutine[Any, Any, None]) -> None:
        """Run `work` in the background, keeping a reference so that the task is not garbage-collected."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def cancel_all(self) -> None:
        """Cancel the background jobs on shutdown; the runner kills their esmini processes."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def list_jobs(self) -> list[Job]:
        """All job directories, newest submission first (until the TTL clean-up deletes them)."""
        if not self.root.is_dir():
            return []
        jobs = [Job(p.name, p) for p in self.root.iterdir() if p.is_dir() and _JOB_ID.match(p.name)]
        jobs = [job for job in jobs if job.submitted_at is not None]  # skip one deleted meanwhile
        return sorted(jobs, key=lambda job: job.submitted_at, reverse=True)

    def get(self, job_id: str) -> Job | None:
        if not _JOB_ID.match(job_id):
            return None
        job = Job(job_id, self.root / job_id)
        return job if job.dir.is_dir() else None

    def cleanup_expired(self, now: float | None = None) -> int:
        """Delete finished jobs whose directory has not changed for `ttl_seconds`."""
        if not self.root.is_dir():
            return 0
        now = time.time() if now is None else now
        removed = 0
        for path in self.root.iterdir():
            if not path.is_dir() or path.name in self._states or not _JOB_ID.match(path.name):
                continue
            try:
                age = now - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > self.ttl_seconds:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        return removed

    async def run_cleanup_loop(self) -> None:
        interval = max(1.0, min(60.0, self.ttl_seconds / 2))
        while True:
            try:
                removed = await asyncio.to_thread(self.cleanup_expired)
                if removed:
                    logger.info("Deleted %d expired job(s)", removed)
            except Exception:  # keep the loop alive; clean-up is best effort
                logger.exception("Job clean-up failed")
            await asyncio.sleep(interval)
