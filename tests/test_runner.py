from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.config import settings
from app.models import TaskSpec
from app.runner import run_single


class RunnerTests(unittest.TestCase):
    def test_persists_running_state_and_counts_missing_final_as_model_failure(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            test_settings = replace(settings, runs_dir=runs_dir)

            def inspect_running_state(task, url, artifact_dir, model, cancel_event=None):
                result = json.loads((artifact_dir / "result.json").read_text())
                self.assertEqual(result["status"], "running")

            with (
                patch("app.runner.settings", test_settings),
                patch("app.runner.run_agent", side_effect=inspect_running_state),
            ):
                result = run_single(
                    TaskSpec(id="one", prompt="Read", expected_answer={"count": 1}),
                    1,
                    "http://localhost:33000",
                    "job",
                )

            persisted = json.loads(
                (runs_dir / "job" / "one" / "1" / "result.json").read_text()
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.grade.score, 0.0)
        self.assertIn("without submitting", result.grade.evidence)
        self.assertIsNone(result.error)
        self.assertEqual(persisted["status"], "failed")

    def test_timeout_counts_as_a_failed_attempt_in_k(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            test_settings = replace(settings, runs_dir=runs_dir, rollout_timeout_seconds=7)

            with (
                patch("app.runner.settings", test_settings),
                patch("app.runner.run_agent", side_effect=TimeoutError("stuck")),
            ):
                result = run_single(
                    TaskSpec(id="one", prompt="Read", expected_answer={"count": 1}),
                    1,
                    "http://localhost:33000",
                    "job",
                )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.grade.score, 0.0)
        self.assertIn("within 7 seconds", result.grade.evidence)

    def test_grades_a_submitted_final_answer(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            test_settings = replace(settings, runs_dir=runs_dir)

            def write_final(task, url, artifact_dir, model, cancel_event=None):
                (artifact_dir / "final_output.txt").write_text('{"count": 1}')

            with (
                patch("app.runner.settings", test_settings),
                patch("app.runner.run_agent", side_effect=write_final),
            ):
                result = run_single(
                    TaskSpec(id="one", prompt="Read", expected_answer={"count": 1}),
                    1,
                    "http://localhost:33000",
                    "job",
                )

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.grade.score, 1.0)


if __name__ == "__main__":
    unittest.main()
