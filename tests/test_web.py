from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.config import settings
from app.ui import INDEX_HTML
from app.web import _evaluation_payloads, get_config, get_job, get_run, index


class WebTests(unittest.TestCase):
    @patch("app.web.job_manager.get", return_value=None)
    def test_missing_job_returns_terminal_state_for_stale_pollers(self, _get):
        payload = get_job("old-job")

        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["expired"])

    def test_dashboard_html_is_not_cached(self):
        response = index()

        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_dashboard_offers_only_configured_computer_use_models(self):
        config = get_config()

        self.assertEqual(config["computer_use_models"], settings.computer_use_models)
        self.assertIn(config["default_computer_use_model"], config["computer_use_models"])
        self.assertIn('select name="model_name"', INDEX_HTML)
        self.assertIn("fetch('/api/config')", INDEX_HTML)

    def test_attempt_limit_label_and_input_use_runtime_config(self):
        self.assertIn('id="attempts-max"', INDEX_HTML)
        self.assertIn("$('attempts-max').textContent=config.max_attempts_per_problem", INDEX_HTML)
        self.assertIn("attempts.max=config.max_attempts_per_problem", INDEX_HTML)

    def test_attempt_replay_includes_problem_and_action_before_thinking(self):
        self.assertIn("Problem statement given to the agent", INDEX_HTML)
        replay = INDEX_HTML[INDEX_HTML.index("function renderReplay") :]
        self.assertLess(replay.index("Issued action"), replay.index("Agent thinking"))

    def test_k_cards_nest_pass_k_and_problem_pass_k_nests_k(self):
        self.assertIn("metric-subvalue", INDEX_HTML)
        self.assertIn("mini-subvalue", INDEX_HTML)
        self.assertIn("subvalue:`Mean pass^k ${total.passK}%`", INDEX_HTML)
        self.assertIn("pass^k ${item.mean_pass_k_percent}%", INDEX_HTML)
        self.assertIn("subvalue:`K = ${item.k}`", INDEX_HTML)

    def test_dashboard_rendering_has_no_post_definition_monkey_patches(self):
        self.assertNotIn("decorateHome", INDEX_HTML)
        self.assertNotIn("baseShowHome", INDEX_HTML)
        self.assertNotIn("richShowHome", INDEX_HTML)

    def test_dashboard_uses_visible_history_routes_for_nested_views(self):
        self.assertIn("#evaluation/", INDEX_HTML)
        self.assertIn("window.history.pushState", INDEX_HTML)
        self.assertIn("window.addEventListener('popstate'", INDEX_HTML)
        self.assertIn('id="history-back"', INDEX_HTML)
        self.assertIn('id="history-forward"', INDEX_HTML)

    def test_replay_pairs_post_action_screenshot_with_next_turn_thinking(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            folder = runs_dir / "job" / "problem" / "1"
            screenshot_dir = folder / "screenshots"
            screenshot_dir.mkdir(parents=True)
            (screenshot_dir / "0001.png").write_bytes(b"png")
            (screenshot_dir / "manifest.jsonl").write_text(
                json.dumps({"screenshot": "0001.png", "action_id": "turn-001-action-01"}) + "\n"
            )
            (folder / "result.json").write_text(json.dumps({"attempt": 1}))
            (folder / "final_output.txt").write_text(
                'Analysis {"answer": 1}. Submitted {"answer": 2}'
            )
            events = [
                {
                    "type": "model_turn",
                    "id": "turn-001",
                    "thinking": "thinking that chose the click",
                    "actions": [{"id": "turn-001-action-01", "name": "click", "args": {"x": 1}}],
                },
                {
                    "type": "model_turn",
                    "id": "turn-002",
                    "thinking": "observation of the resulting screenshot",
                    "actions": [],
                },
                {"type": "final", "output": '{"answer": 2}'},
            ]
            (folder / "trace.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events)
            )
            test_settings = replace(settings, runs_dir=runs_dir)

            with patch("app.web.settings", test_settings):
                payload = get_run("job/problem/1")

        self.assertEqual(payload["slides"][0]["thinking"], "observation of the resulting screenshot")
        self.assertEqual(payload["slides"][0]["action"]["name"], "click")
        self.assertEqual(payload["final"], '{\n  "answer": 2\n}')

    def test_evaluation_reports_k_and_pass_k_without_threshold(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            job_dir = runs_dir / "job"
            job_dir.mkdir()
            (job_dir / "job.json").write_text(json.dumps({
                "id": "job",
                "title": "Evaluation",
                "attempts": 2,
                "status": "complete",
                "problems": [{"id": "problem", "prompt": "Answer it"}],
            }))
            for attempt, status in ((1, "passed"), (2, "failed")):
                folder = job_dir / "problem" / str(attempt)
                folder.mkdir(parents=True)
                (folder / "result.json").write_text(json.dumps({
                    "task_id": "problem",
                    "attempt": attempt,
                    "status": status,
                    "artifact_dir": f"job/problem/{attempt}",
                }))
            test_settings = replace(settings, runs_dir=runs_dir)

            with patch("app.web.settings", test_settings):
                evaluation = _evaluation_payloads()[0]

        problem = evaluation["problems"][0]
        self.assertEqual(problem["k"], 2)
        self.assertEqual(problem["pass_k"], 0.5)
        self.assertEqual(problem["pass_k_percent"], 50)
        self.assertEqual(problem["outcome_status"], "partial")
        self.assertEqual(evaluation["mean_pass_k_percent"], 50)
        self.assertNotIn("threshold_percent", problem)


if __name__ == "__main__":
    unittest.main()
