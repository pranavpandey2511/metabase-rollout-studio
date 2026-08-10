from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from app.config import settings
from app.jobs import JobConflictError, JobManager
from app.models import RunResult, TaskSpec


class JobManagerTests(unittest.TestCase):
    @staticmethod
    def _wait_for_status(manager: JobManager, job_id: str, status: str) -> None:
        deadline = time.monotonic() + 2
        while manager.get(job_id)["status"] != status and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_rejects_attempts_above_configured_limit(self):
        manager = JobManager()
        with self.assertRaisesRegex(
            ValueError, f"limited to {settings.max_attempts_per_problem}"
        ):
            manager.start(
                [TaskSpec(id="one", prompt="read only")],
                settings.max_attempts_per_problem + 1,
                1,
            )

    def test_rejects_models_without_computer_use_support(self):
        manager = JobManager()
        with self.assertRaisesRegex(ValueError, "Unsupported computer-use model"):
            manager.start(
                [TaskSpec(id="one", prompt="read only")],
                1,
                1,
                model_name="gemini-text-only",
            )

    def test_rejects_parallelism_above_configured_capacity(self):
        manager = JobManager()
        capacity = min(settings.max_parallel_rollouts, len(settings.metabase_urls))

        with self.assertRaisesRegex(ValueError, f"limited to {capacity}"):
            manager.start(
                [TaskSpec(id="one", prompt="read only")],
                1,
                capacity + 1,
            )

    @patch("app.jobs.JobManager._persist")
    @patch("app.jobs.run_single")
    def test_cancel_marks_job_cancelled(self, mock_run_single, _persist):
        def wait_for_cancel(
            task, attempt, environment_url, job_id, model_name, cancel_event
        ):
            while not cancel_event.is_set():
                time.sleep(0.01)
            raise RuntimeError("cancelled child")

        mock_run_single.side_effect = wait_for_cancel
        manager = JobManager()
        job = manager.start([TaskSpec(id="one", prompt="read only")], 1, 1)
        manager.cancel(job.id)
        self._wait_for_status(manager, job.id, "cancelled")

        self.assertEqual(manager.get(job.id)["status"], "cancelled")
        self.assertEqual(
            manager.get(job.id)["model_name"], settings.default_computer_use_model
        )

    @patch("app.jobs.JobManager._persist")
    @patch("app.jobs.run_single")
    def test_rejects_a_second_active_evaluation(self, mock_run_single, _persist):
        def wait_for_cancel(
            task, attempt, environment_url, job_id, model_name, cancel_event
        ):
            while not cancel_event.is_set():
                time.sleep(0.01)
            raise RuntimeError("cancelled child")

        mock_run_single.side_effect = wait_for_cancel
        manager = JobManager()
        job = manager.start([TaskSpec(id="one", prompt="read only")], 1, 1)

        with self.assertRaises(JobConflictError):
            manager.start([TaskSpec(id="two", prompt="read only")], 1, 1)

        manager.cancel(job.id)
        self._wait_for_status(manager, job.id, "cancelled")

    @patch("app.jobs.JobManager._persist")
    @patch("app.jobs.run_single")
    def test_rollout_infrastructure_errors_mark_the_job_invalid(
        self, mock_run_single, _persist
    ):
        mock_run_single.return_value = RunResult(
            run_id="run",
            task_id="one",
            attempt=1,
            environment_url=settings.metabase_urls[0],
            model_name=settings.default_computer_use_model,
            status="error",
            started_at="now",
            error="agent unavailable",
        )
        manager = JobManager()
        job = manager.start([TaskSpec(id="one", prompt="read only")], 1, 1)

        self._wait_for_status(manager, job.id, "error")

        self.assertIn("infrastructure errors", manager.get(job.id)["error"])

    @patch("app.jobs.JobManager._persist")
    @patch("app.jobs.run_single")
    def test_unexpected_worker_error_is_not_overwritten_by_later_success(
        self, mock_run_single, _persist
    ):
        release_second = Event()

        def run(task, attempt, environment_url, job_id, model_name, cancel_event):
            if attempt == 1:
                release_second.set()
                raise RuntimeError("worker crashed")
            release_second.wait(1)
            return RunResult(
                run_id="run-2",
                task_id="one",
                attempt=2,
                environment_url=environment_url,
                model_name=model_name,
                status="passed",
                started_at="now",
            )

        mock_run_single.side_effect = run
        test_settings = replace(
            settings,
            metabase_urls=("http://localhost:33000", "http://localhost:33001"),
            max_parallel_rollouts=2,
        )
        manager = JobManager()
        with patch("app.jobs.settings", test_settings):
            job = manager.start([TaskSpec(id="one", prompt="read only")], 2, 2)
            self._wait_for_status(manager, job.id, "error")

        self.assertEqual(manager.get(job.id)["status"], "error")
        self.assertIn("worker crashed", manager.get(job.id)["error"])

    def test_reconciles_interrupted_artifacts_only_when_explicitly_called(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            attempt_dir = runs_dir / "job" / "one" / "1"
            attempt_dir.mkdir(parents=True)
            job_path = runs_dir / "job" / "job.json"
            result_path = attempt_dir / "result.json"
            job_path.write_text(json.dumps({"id": "job", "status": "running"}))
            result_path.write_text(json.dumps({"status": "running"}))
            test_settings = replace(settings, runs_dir=runs_dir)
            manager = JobManager()

            self.assertEqual(json.loads(job_path.read_text())["status"], "running")
            with patch("app.jobs.settings", test_settings):
                manager.reconcile_interrupted_jobs()

            self.assertEqual(json.loads(job_path.read_text())["status"], "error")
            self.assertEqual(json.loads(result_path.read_text())["status"], "error")

    def test_reconciliation_recovers_a_fully_persisted_evaluation(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            job_dir = runs_dir / "job"
            job_dir.mkdir()
            job_path = job_dir / "job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "id": "job",
                        "status": "running",
                        "attempts": 2,
                        "problems": [{"id": "one", "prompt": "read"}],
                    }
                )
            )
            for attempt, status in ((1, "passed"), (2, "failed")):
                attempt_dir = job_dir / "one" / str(attempt)
                attempt_dir.mkdir(parents=True)
                (attempt_dir / "result.json").write_text(
                    json.dumps(
                        {"task_id": "one", "attempt": attempt, "status": status}
                    )
                )
            test_settings = replace(settings, runs_dir=runs_dir)

            with patch("app.jobs.settings", test_settings):
                JobManager().reconcile_interrupted_jobs()

            recovered = json.loads(job_path.read_text())

        self.assertEqual(recovered["status"], "complete")
        self.assertIsNone(recovered["error"])


if __name__ == "__main__":
    unittest.main()
