"""Small in-process job runner for progress-aware local workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from threading import Lock, Thread
from uuid import uuid4

ProgressCallback = Callable[[int, int, str], None]
JobWorker = Callable[[ProgressCallback], None]


@dataclass
class Job:
    job_id: str
    label: str
    completed: int
    total: int
    current: str
    status: str = "running"
    error: str | None = None


class JobManager:
    """Run one mutating background workflow at a time."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, Job] = {}
        self._active_job_id: str | None = None

    def start(self, label: str, total: int, worker: JobWorker) -> Job:
        with self._lock:
            active = self._active_job()
            if active is not None:
                return active
            job = Job(
                job_id=uuid4().hex,
                label=label,
                completed=0,
                total=max(total, 1),
                current="Starting…",
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id

        Thread(
            target=self._run,
            args=(job.job_id, worker),
            daemon=True,
            name=f"wingman-{job.job_id[:8]}",
        ).start()
        return job

    def get(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return asdict(job) if job else None

    def _active_job(self) -> Job | None:
        if self._active_job_id is None:
            return None
        job = self._jobs.get(self._active_job_id)
        return job if job and job.status == "running" else None

    def _run(self, job_id: str, worker: JobWorker) -> None:
        def update(completed: int, total: int, current: str) -> None:
            with self._lock:
                job = self._jobs[job_id]
                job.completed = max(0, completed)
                job.total = max(total, 1)
                job.current = current

        try:
            worker(update)
        except Exception as error:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = str(error)
                job.current = "Failed"
        else:
            with self._lock:
                job = self._jobs[job_id]
                job.completed = job.total
                job.status = "complete"
                job.current = "Complete"
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
