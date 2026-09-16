"""Tests for progress-aware background workflows."""

import time
import unittest
from threading import Event

from wingman.jobs import JobManager


class JobManagerTests(unittest.TestCase):
    def wait_for_terminal_state(
        self, manager: JobManager, job_id: str
    ) -> dict[str, object]:
        for _ in range(100):
            snapshot = manager.get(job_id)
            assert snapshot is not None
            if snapshot["status"] != "running":
                return snapshot
            time.sleep(0.01)
        self.fail("Background job did not finish")

    def test_reports_progress_and_completion(self) -> None:
        manager = JobManager()

        def worker(update) -> None:
            update(1, 2, "First complete")
            update(2, 2, "Second complete")

        job = manager.start("Test workflow", 2, worker)
        result = self.wait_for_terminal_state(manager, job.job_id)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["total"], 2)

    def test_reports_worker_failure(self) -> None:
        manager = JobManager()

        def worker(_update) -> None:
            raise RuntimeError("API unavailable")

        job = manager.start("Broken workflow", 1, worker)
        result = self.wait_for_terminal_state(manager, job.job_id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "API unavailable")

    def test_reuses_active_job_instead_of_running_concurrently(self) -> None:
        manager = JobManager()
        release = Event()
        first = manager.start("First", 1, lambda _update: release.wait(1))

        second = manager.start("Second", 1, lambda _update: None)

        self.assertEqual(second.job_id, first.job_id)
        release.set()
        self.wait_for_terminal_state(manager, first.job_id)


if __name__ == "__main__":
    unittest.main()
