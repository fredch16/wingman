"""Tests for progress-aware background workflows."""

import time
import unittest
from threading import Event

from wingman.jobs import CancellationRegistry, JobManager


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

        def worker(update, _check_cancelled) -> None:
            update(1, 2, "First complete")
            update(2, 2, "Second complete")

        job = manager.start("Test workflow", 2, worker)
        result = self.wait_for_terminal_state(manager, job.job_id)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["total"], 2)

    def test_reports_worker_failure(self) -> None:
        manager = JobManager()

        def worker(_update, _check_cancelled) -> None:
            raise RuntimeError("API unavailable")

        job = manager.start("Broken workflow", 1, worker)
        result = self.wait_for_terminal_state(manager, job.job_id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "API unavailable")

    def test_reuses_active_job_instead_of_running_concurrently(self) -> None:
        manager = JobManager()
        release = Event()
        first = manager.start(
            "First", 1, lambda _update, _check_cancelled: release.wait(1)
        )

        second = manager.start(
            "Second", 1, lambda _update, _check_cancelled: None
        )

        self.assertEqual(second.job_id, first.job_id)
        release.set()
        self.wait_for_terminal_state(manager, first.job_id)

    def test_cancel_stops_worker_before_later_work(self) -> None:
        manager = JobManager()
        started = Event()
        release = Event()
        stopped = Event()
        api_calls: list[str] = []

        def worker(_update, check_cancelled) -> None:
            try:
                api_calls.append("first")
                started.set()
                release.wait(1)
                check_cancelled()
                api_calls.append("second")
            finally:
                stopped.set()

        job = manager.start("Generate replies", 2, worker)
        self.assertTrue(started.wait(1))
        cancelled = manager.cancel(job.job_id)
        release.set()
        self.assertTrue(stopped.wait(1))

        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(manager.get(job.job_id)["status"], "cancelled")
        self.assertEqual(api_calls, ["first"])


class CancellationRegistryTests(unittest.TestCase):
    def test_cancels_active_operation(self) -> None:
        registry = CancellationRegistry()
        cancellation = registry.begin("operation-1")

        self.assertEqual(registry.cancel(["operation-1"]), 1)
        self.assertTrue(cancellation.is_set())

        registry.finish("operation-1")

    def test_remembers_cancel_that_arrives_before_operation_begins(self) -> None:
        registry = CancellationRegistry()

        registry.cancel(["operation-early"])
        cancellation = registry.begin("operation-early")

        self.assertTrue(cancellation.is_set())
        registry.finish("operation-early")


if __name__ == "__main__":
    unittest.main()
