"""Small in-process job runner for progress-aware local workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from threading import Event, Lock, Thread
from uuid import uuid4

ProgressCallback = Callable[[int, int, str], None]
CancellationCheck = Callable[[], None]
JobWorker = Callable[[ProgressCallback, CancellationCheck], None]


class JobCancelled(Exception):
    """Stop a cooperative background job without reporting a failure."""


class CancellationRegistry:
    """Track cancellable request-scoped operations by browser-generated ID."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active: dict[str, Event] = {}
        self._pending: set[str] = set()

    def begin(self, operation_id: str) -> Event:
        event = Event()
        with self._lock:
            if operation_id in self._pending:
                event.set()
                self._pending.discard(operation_id)
            self._active[operation_id] = event
        return event

    def cancel(self, operation_ids: list[str]) -> int:
        with self._lock:
            for operation_id in operation_ids:
                event = self._active.get(operation_id)
                if event is None:
                    self._pending.add(operation_id)
                else:
                    event.set()
        return len(operation_ids)

    def finish(self, operation_id: str) -> None:
        with self._lock:
            self._active.pop(operation_id, None)
            self._pending.discard(operation_id)


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

    def cancel(self, job_id: str) -> dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "running":
                job.status = "cancelled"
                job.current = "Cancelled"
                job.error = None
            return asdict(job)

    def _active_job(self) -> Job | None:
        if self._active_job_id is None:
            return None
        job = self._jobs.get(self._active_job_id)
        return job if job and job.status == "running" else None

    def _run(self, job_id: str, worker: JobWorker) -> None:
        def check_cancelled() -> None:
            with self._lock:
                if self._jobs[job_id].status == "cancelled":
                    raise JobCancelled()

        def update(completed: int, total: int, current: str) -> None:
            check_cancelled()
            with self._lock:
                job = self._jobs[job_id]
                job.completed = max(0, completed)
                job.total = max(total, 1)
                job.current = current

        try:
            worker(update, check_cancelled)
        except JobCancelled:
            pass
        except Exception as error:
            with self._lock:
                job = self._jobs[job_id]
                if job.status != "cancelled":
                    job.status = "failed"
                    job.error = str(error)
                    job.current = "Failed"
        else:
            with self._lock:
                job = self._jobs[job_id]
                if job.status != "cancelled":
                    job.completed = job.total
                    job.status = "complete"
                    job.current = "Complete"
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
