from dataclasses import replace
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from app.agent import AgentConfigurationError, run_agent
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
        query = command[command.index("--query") + 1]
        self.assertIn("Use only the visible Metabase UI", query)
        self.assertIn("do not attempt to use a shell", query)


if __name__ == "__main__":
    unittest.main()
