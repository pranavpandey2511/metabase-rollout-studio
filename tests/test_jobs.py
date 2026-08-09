import time
import unittest
from unittest.mock import patch

from app.config import settings
from app.jobs import JobManager
from app.models import TaskSpec


class JobManagerTests(unittest.TestCase):
    def test_rejects_attempts_above_configured_limit(self):
        manager = JobManager()
        with self.assertRaisesRegex(ValueError, "limited to 2"):
            manager.start([TaskSpec(id="one", prompt="read only")], 3, 1)

    def test_rejects_models_without_computer_use_support(self):
        manager = JobManager()
        with self.assertRaisesRegex(ValueError, "Unsupported computer-use model"):
            manager.start(
                [TaskSpec(id="one", prompt="read only")],
                1,
                1,
                model_name="gemini-text-only",
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
        deadline = time.monotonic() + 2
        while manager.get(job.id)["status"] != "cancelled" and time.monotonic() < deadline:
            time.sleep(0.02)

        self.assertEqual(manager.get(job.id)["status"], "cancelled")
        self.assertEqual(
            manager.get(job.id)["model_name"], settings.default_computer_use_model
        )


if __name__ == "__main__":
    unittest.main()
