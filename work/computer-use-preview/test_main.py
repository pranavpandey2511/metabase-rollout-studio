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

import unittest
from unittest.mock import patch, MagicMock
import os
import main
from computers.playwright.playwright import PlaywrightComputer

class TestMain(unittest.TestCase):

    @patch.dict(os.environ, {"ROLLOUT_QUERY": "query from environment"})
    @patch('main.PlaywrightComputer')
    @patch('main.BrowserAgent')
    def test_main_accepts_query_from_environment(self, mock_browser_agent, mock_playwright_computer):
        with patch('main.argparse.ArgumentParser.parse_args') as parse_args:
            parse_args.return_value = MagicMock(
                env='playwright',
                initial_url='test_url',
                highlight_mouse=False,
                query=os.environ["ROLLOUT_QUERY"],
                model='test_model',
            )

            main.main()

        mock_browser_agent.assert_called_once()
        self.assertNotIn("ROLLOUT_QUERY", os.environ)

    @patch('main.argparse.ArgumentParser')
    @patch('main.PlaywrightComputer')
    @patch('main.BrowserAgent')
    def test_main_playwright(self, mock_browser_agent, mock_playwright_computer, mock_arg_parser):
        mock_args = MagicMock()
        mock_args.env = 'playwright'
        mock_args.initial_url = 'test_url'
        mock_args.highlight_mouse = True
        mock_args.query = 'test_query'
        mock_args.model = 'test_model'
        mock_args.api_server = None
        mock_args.api_server_key = None
        mock_arg_parser.return_value.parse_args.return_value = mock_args

        main.main()

        mock_playwright_computer.assert_called_once_with(
            screen_size=main.PLAYWRIGHT_SCREEN_SIZE,
            initial_url='test_url',
            highlight_mouse=True
        )
        mock_browser_agent.assert_called_once()
        mock_browser_agent.return_value.agent_loop.assert_called_once()

    @patch('main.argparse.ArgumentParser')
    @patch('main.BrowserbaseComputer')
    @patch('main.BrowserAgent')
    def test_main_browserbase(self, mock_browser_agent, mock_browserbase_computer, mock_arg_parser):
        mock_args = MagicMock()
        mock_args.env = 'browserbase'
        mock_args.query = 'test_query'
        mock_args.model = 'test_model'
        mock_args.api_server = None
        mock_args.api_server_key = None
        mock_args.initial_url = 'test_url'
        mock_args.highlight_mouse = False
        mock_arg_parser.return_value.parse_args.return_value = mock_args

        main.main()

        mock_browserbase_computer.assert_called_once_with(
            screen_size=main.PLAYWRIGHT_SCREEN_SIZE,
            initial_url='test_url'
        )
        mock_browser_agent.assert_called_once()
        mock_browser_agent.return_value.agent_loop.assert_called_once()


class PlaywrightContainmentTests(unittest.TestCase):
    def setUp(self):
        self.computer = PlaywrightComputer(
            screen_size=(1000, 700), initial_url="http://localhost:33000/"
        )
        self.computer._page = MagicMock()

    def test_same_origin_navigation_is_allowed(self):
        self.computer._page.url = "http://localhost:33000/dashboard/1"
        self.computer._page.screenshot.return_value = b"png"

        self.computer.navigate("http://localhost:33000/dashboard/1")

        self.computer._page.goto.assert_called_once_with(
            "http://localhost:33000/dashboard/1"
        )

    def test_cross_origin_navigation_is_blocked(self):
        with self.assertRaisesRegex(PermissionError, "outside"):
            self.computer.navigate("https://example.com/")

        self.computer._page.goto.assert_not_called()

    def test_cross_origin_page_requests_are_aborted(self):
        route = MagicMock()
        route.request.url = "https://example.com/"

        self.computer._guard_route(route)

        route.abort.assert_called_once_with("blockedbyclient")
        route.continue_.assert_not_called()

if __name__ == '__main__':
    unittest.main()
