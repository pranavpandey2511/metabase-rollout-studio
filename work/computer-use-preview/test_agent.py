# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from google.genai import types
from agent import BrowserAgent
from computers import EnvState

class TestBrowserAgent(unittest.TestCase):
    def setUp(self):
        os.environ["GEMINI_API_KEY"] = "test_api_key"
        self.mock_browser_computer = MagicMock()
        self.mock_browser_computer.screen_size.return_value = (1000, 1000)
        self.agent = BrowserAgent(
            browser_computer=self.mock_browser_computer,
            query="test query",
            model_name="test_model"
        )
        # Mock the genai client
        self.agent._client = MagicMock()

    def test_handle_action_open_web_browser(self):
        action = types.FunctionCall(name="open_web_browser", args={})
        self.agent.handle_action(action, use_legacy_actions=True)
        self.mock_browser_computer.open_web_browser.assert_called_once()

    def test_handle_action_click_at(self):
        action = types.FunctionCall(name="click_at", args={"x": 100, "y": 200})
        self.agent.handle_action(action, use_legacy_actions=True)
        self.mock_browser_computer.click_at.assert_called_once_with(x=100, y=200)

    def test_handle_action_type_text_at(self):
        action = types.FunctionCall(name="type_text_at", args={"x": 100, "y": 200, "text": "hello"})
        self.agent.handle_action(action, use_legacy_actions=True)
        self.mock_browser_computer.type_text_at.assert_called_once_with(
            x=100, y=200, text="hello", press_enter=False, clear_before_typing=True
        )

    def test_handle_action_scroll_document(self):
        action = types.FunctionCall(name="scroll_document", args={"direction": "down"})
        self.agent.handle_action(action, use_legacy_actions=True)
        self.mock_browser_computer.scroll_document.assert_called_once_with("down")

    def test_handle_action_blocks_navigate(self):
        action = types.FunctionCall(name="navigate", args={"url": "https://example.com"})
        with self.assertRaises(PermissionError):
            self.agent.handle_action(action, use_legacy_actions=True)
        self.mock_browser_computer.navigate.assert_not_called()

    def test_handle_action_unknown_function(self):
        action = types.FunctionCall(name="unknown_function", args={})
        with self.assertRaises(PermissionError):
            self.agent.handle_action(action, use_legacy_actions=True)

    def test_agent_exposes_only_the_native_computer_use_tool(self):
        config = self.agent._generate_content_config.model_dump(exclude_none=True)

        self.assertEqual(len(config["tools"]), 1)
        self.assertEqual(set(config["tools"][0]), {"computer_use"})
        self.assertEqual(
            set(config["tools"][0]["computer_use"]["excluded_predefined_functions"]),
            {"navigate", "go_back", "go_forward"},
        )
        self.assertEqual(config["temperature"], 1)
        self.assertEqual(config["top_p"], 0.95)
        self.assertEqual(config["top_k"], 40)

    def test_legacy_agent_can_open_the_existing_browser_but_cannot_navigate(self):
        legacy_agent = BrowserAgent(
            browser_computer=self.mock_browser_computer,
            query="test query",
            model_name="gemini-2.5-computer-use-preview-10-2025",
        )
        config = legacy_agent._generate_content_config.model_dump(exclude_none=True)
        excluded = set(
            config["tools"][0]["computer_use"]["excluded_predefined_functions"]
        )

        self.assertNotIn("open_web_browser", excluded)
        self.assertEqual(excluded, {"navigate", "search", "go_back", "go_forward"})

    def test_denormalize_x(self):
        self.assertEqual(self.agent.denormalize_x(500), 500)

    def test_denormalize_y(self):
        self.assertEqual(self.agent.denormalize_y(500), 500)

    @patch('agent.BrowserAgent.get_model_response')
    def test_run_one_iteration_no_function_calls(self, mock_get_model_response):
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [
            types.Part(text="some reasoning", thought=True),
            types.Part(text='{"answer": 42}'),
        ]
        mock_response.candidates = [mock_candidate]
        mock_get_model_response.return_value = mock_response

        result = self.agent.run_one_iteration()

        self.assertEqual(result, "COMPLETE")
        self.assertEqual(len(self.agent._contents), 2)
        self.assertEqual(self.agent._contents[1], mock_candidate.content)
        self.assertEqual(self.agent.final_reasoning, '{"answer": 42}')

    @patch('agent.BrowserAgent.get_model_response')
    def test_run_one_iteration_propagates_model_failures(self, mock_get_model_response):
        mock_get_model_response.side_effect = RuntimeError("API unavailable")

        with self.assertRaisesRegex(RuntimeError, "API unavailable"):
            self.agent.run_one_iteration()

    def test_get_text_separates_thinking_from_final_output(self):
        candidate = MagicMock()
        candidate.content.parts = [
            types.Part(text="private reasoning", thought=True),
            types.Part(text='{"answer": 42}'),
        ]

        self.assertEqual(self.agent.get_text(candidate, thought=True), "private reasoning")
        self.assertEqual(self.agent.get_text(candidate, thought=False), '{"answer": 42}')

    def test_trace_redaction_uses_os_safe_json_payload(self):
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"ROLLOUT_REDACT_VALUES": json.dumps(["secret-value"])},
        ):
            trace_path = Path(directory) / "trace.jsonl"
            self.agent._trace_path = trace_path

            self.agent._write_trace({"type": "test", "value": "secret-value"})

            event = json.loads(trace_path.read_text())
            self.assertEqual(event["value"], "[REDACTED]")

    @patch('agent.BrowserAgent.get_model_response')
    @patch('agent.BrowserAgent.handle_action')
    def test_run_one_iteration_with_function_call(self, mock_handle_action, mock_get_model_response):
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        function_call = types.FunctionCall(name="navigate", args={"url": "https://example.com"})
        mock_candidate.content.parts = [types.Part(function_call=function_call)]
        mock_response.candidates = [mock_candidate]
        mock_get_model_response.return_value = mock_response

        mock_env_state = EnvState(screenshot=b"screenshot", url="https://example.com")
        mock_handle_action.return_value = mock_env_state

        result = self.agent.run_one_iteration()

        self.assertEqual(result, "CONTINUE")
        mock_handle_action.assert_called_once_with(function_call, False)
        self.assertEqual(len(self.agent._contents), 3)

    @patch('agent.BrowserAgent.get_model_response')
    def test_run_one_iteration_recovers_from_missing_action_coordinate(self, mock_get_model_response):
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        function_call = types.FunctionCall(name="click", args={})
        mock_candidate.content.parts = [types.Part(function_call=function_call)]
        mock_response.candidates = [mock_candidate]
        mock_get_model_response.return_value = mock_response
        self.mock_browser_computer.take_screenshot.return_value = EnvState(
            screenshot=b"screenshot", url="https://example.com"
        )

        result = self.agent.run_one_iteration()

        self.assertEqual(result, "CONTINUE")
        self.mock_browser_computer.take_screenshot.assert_called_once()
        self.assertEqual(len(self.agent._contents), 3)

    @patch('agent.BrowserAgent.get_model_response')
    @patch('agent.BrowserAgent.handle_action')
    def test_run_one_iteration_recovers_from_browser_action_failure(
        self, mock_handle_action, mock_get_model_response
    ):
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        function_call = types.FunctionCall(name="click", args={"x": 500, "y": 500})
        mock_candidate.content.parts = [types.Part(function_call=function_call)]
        mock_response.candidates = [mock_candidate]
        mock_get_model_response.return_value = mock_response
        mock_handle_action.side_effect = RuntimeError("net::ERR_CONNECTION_REFUSED")
        self.mock_browser_computer.take_screenshot.return_value = EnvState(
            screenshot=b"screenshot", url="http://localhost:33000/"
        )

        result = self.agent.run_one_iteration()

        self.assertEqual(result, "CONTINUE")
        self.mock_browser_computer.take_screenshot.assert_called_once()
        self.assertEqual(len(self.agent._contents), 3)


if __name__ == "__main__":
    unittest.main()
