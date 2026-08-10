from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.cli import main
from app.config import settings
from app.models import RunResult


class CliTests(unittest.TestCase):
    def test_single_rollout_uses_preflight_and_a_unique_evaluation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = root / "tasks.json"
            tasks.write_text('[{"id":"one","prompt":"Read"}]')
            test_settings = replace(settings, runs_dir=root / "runs")
            result = RunResult(
                run_id="run",
                task_id="one",
                attempt=1,
                environment_url=test_settings.metabase_urls[0],
                model_name=test_settings.default_computer_use_model,
                status="failed",
                started_at="now",
            )
            arguments = [
                "app.cli",
                "run",
                "--tasks",
                str(tasks),
                "--task-id",
                "one",
            ]
            with (
                patch("sys.argv", arguments),
                patch("app.cli.settings", test_settings),
                patch("app.cli.validate_agent_configuration") as preflight,
                patch("app.cli.ensure_environment") as environment,
                patch("app.cli.run_single", return_value=result) as run,
                patch("builtins.print"),
            ):
                main()

            job_paths = list(test_settings.runs_dir.glob("cli-*/job.json"))
            metadata = json.loads(job_paths[0].read_text())

        self.assertEqual(len(job_paths), 1)
        self.assertEqual(metadata["status"], "complete")
        self.assertFalse(metadata["controllable"])
        preflight.assert_called_once_with(test_settings.default_computer_use_model)
        environment.assert_called_once_with(1)
        self.assertTrue(run.call_args.args[3].startswith("cli-"))


if __name__ == "__main__":
    unittest.main()
