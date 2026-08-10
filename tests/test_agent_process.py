from dataclasses import replace
import json
from pathlib import Path
import signal
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from app.agent import AgentConfigurationError, run_agent, terminate_recorded_agent
from app.config import settings
from app.models import TaskSpec


class AgentProcessTests(unittest.TestCase):
    @patch("app.agent.subprocess.Popen")
    def test_task_initial_url_cannot_escape_the_gym(self, mock_popen):
        test_settings = replace(
            settings,
            gemini_api_key="test-key",
            metabase_password="test-password",
        )

        with TemporaryDirectory() as directory, patch("app.agent.settings", test_settings):
            with self.assertRaisesRegex(AgentConfigurationError, "configured Metabase origin"):
                run_agent(
                    TaskSpec(
                        id="one",
                        prompt="Read the metric",
                        initial_url="https://example.com/",
                    ),
                    "http://localhost:3000",
                    Path(directory),
                    test_settings.default_computer_use_model,
                )

        mock_popen.assert_not_called()

    @patch("app.agent.subprocess.Popen")
    def test_popen_uses_explicit_output_pipes(self, mock_popen):
        process = MagicMock()
        process.communicate.return_value = ("agent output", "")
        process.returncode = 0
        process.pid = 12345
        mock_popen.return_value = process
        test_settings = replace(
            settings,
            gemini_api_key="test-key",
            metabase_password="test-password",
        )

        with TemporaryDirectory() as directory, patch("app.agent.settings", test_settings):
            transcript = run_agent(
                TaskSpec(id="one", prompt="Read the metric"),
                "http://localhost:3000",
                Path(directory),
                test_settings.default_computer_use_model,
            )

        self.assertEqual(transcript, "agent output")
        kwargs = mock_popen.call_args.kwargs
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertNotIn("capture_output", kwargs)
        command = mock_popen.call_args.args[0]
        self.assertEqual(
            command[command.index("--model") + 1],
            test_settings.default_computer_use_model,
        )
        self.assertNotIn("--query", command)
        query = kwargs["env"]["ROLLOUT_QUERY"]
        self.assertIn("Use only the visible Metabase UI", query)
        self.assertIn("do not attempt to use a shell", query)
        self.assertNotIn("test-password", " ".join(command))

    def test_reconciliation_terminates_only_a_verified_recorded_agent(self):
        with TemporaryDirectory() as directory:
            artifact_dir = Path(directory)
            main_py = settings.computer_use_dir / "main.py"
            (artifact_dir / "agent-process.json").write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "process_group": 12345,
                        "main_py": str(main_py),
                    }
                )
            )
            ps_result = MagicMock(stdout=f"python {main_py} --model test")
            with (
                patch("app.agent.os.getpgid", return_value=12345),
                patch("app.agent.subprocess.run", return_value=ps_result),
                patch("app.agent._signal_process_group") as signal_group,
                patch("app.agent.os.kill", side_effect=ProcessLookupError),
            ):
                terminated = terminate_recorded_agent(artifact_dir)

            self.assertTrue(terminated)
            signal_group.assert_called_once_with(12345, signal.SIGTERM)
            self.assertFalse((artifact_dir / "agent-process.json").exists())

    def test_reconciliation_does_not_signal_an_unrelated_reused_pid(self):
        with TemporaryDirectory() as directory:
            artifact_dir = Path(directory)
            main_py = settings.computer_use_dir / "main.py"
            (artifact_dir / "agent-process.json").write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "process_group": 12345,
                        "main_py": str(main_py),
                    }
                )
            )
            with (
                patch("app.agent.os.getpgid", return_value=12345),
                patch(
                    "app.agent.subprocess.run",
                    return_value=MagicMock(stdout="python unrelated.py"),
                ),
                patch("app.agent._signal_process_group") as signal_group,
            ):
                terminated = terminate_recorded_agent(artifact_dir)

            self.assertFalse(terminated)
            signal_group.assert_not_called()

    @patch("app.agent.subprocess.Popen")
    def test_keyboard_interrupt_terminates_and_reaps_detached_agent(self, mock_popen):
        process = MagicMock()
        process.pid = 12345
        process.communicate.side_effect = [KeyboardInterrupt, ("", "")]
        process.poll.return_value = None
        mock_popen.return_value = process
        test_settings = replace(
            settings,
            gemini_api_key="test-key",
            metabase_password="test-password",
        )

        with (
            TemporaryDirectory() as directory,
            patch("app.agent.settings", test_settings),
            patch("app.agent._signal_process_group") as signal_group,
        ):
            artifact_dir = Path(directory)
            with self.assertRaises(KeyboardInterrupt):
                run_agent(
                    TaskSpec(id="one", prompt="Read the metric"),
                    "http://localhost:33000",
                    artifact_dir,
                    test_settings.default_computer_use_model,
                )

            self.assertFalse((artifact_dir / "agent-process.json").exists())

        signal_group.assert_called_once_with(12345, signal.SIGTERM)
        self.assertEqual(process.communicate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
