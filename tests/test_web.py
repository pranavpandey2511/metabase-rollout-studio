from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, UploadFile
from app.config import settings
from app.runtime import EnvironmentUnavailable
from app.ui import INDEX_HTML
from app.web import (
    _evaluation_payloads,
    _problem_outcome,
    _require_local_browser_origin,
    create_job,
    get_config,
    get_job,
    get_run,
    index,
)


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
        self.assertEqual(INDEX_HTML.count("<script>"), 1)
        self.assertNotIn("rollout:navigate", INDEX_HTML)

    def test_dashboard_uses_visible_history_routes_for_nested_views(self):
        self.assertIn("#evaluation/", INDEX_HTML)
        self.assertIn("window.history.pushState", INDEX_HTML)
        self.assertIn("window.addEventListener('popstate'", INDEX_HTML)
        self.assertIn('id="history-back"', INDEX_HTML)
        self.assertIn('id="history-forward"', INDEX_HTML)

    def test_active_jobs_refresh_visible_artifacts_while_polling(self):
        self.assertIn("function scheduleRefresh()", INDEX_HTML)
        self.assertIn("['queued','running','cancelling'].includes(item.status)", INDEX_HTML)
        self.assertIn("setTimeout(()=>refresh()", INDEX_HTML)
        self.assertNotIn("const poll=", INDEX_HTML)

    def test_evaluation_creation_rejects_nonlocal_browser_origins(self):
        _require_local_browser_origin("http://127.0.0.1:8000")

        with self.assertRaises(HTTPException) as raised:
            _require_local_browser_origin("https://example.com")

        self.assertEqual(raised.exception.status_code, 403)

    def test_problem_outcome_prioritizes_invalid_and_ungraded_runs(self):
        self.assertEqual(
            _problem_outcome(
                completed=True,
                evaluation_status="complete",
                passed=1,
                errors=1,
                cancelled=0,
                needs_review=0,
                attempts=2,
            ),
            "error",
        )
        self.assertEqual(
            _problem_outcome(
                completed=True,
                evaluation_status="complete",
                passed=0,
                errors=0,
                cancelled=0,
                needs_review=2,
                attempts=2,
            ),
            "needs_review",
        )
        self.assertEqual(
            _problem_outcome(
                completed=True,
                evaluation_status="cancelled",
                passed=0,
                errors=0,
                cancelled=2,
                needs_review=0,
                attempts=2,
            ),
            "cancelled",
        )
        self.assertEqual(
            _problem_outcome(
                completed=True,
                evaluation_status="error",
                passed=2,
                errors=0,
                cancelled=0,
                needs_review=0,
                attempts=2,
            ),
            "passed",
        )

    def test_job_creation_preflights_before_starting_the_environment(self):
        with TemporaryDirectory() as directory:
            test_settings = replace(settings, runs_dir=Path(directory))
            events: list[str] = []
            job = MagicMock()
            job.public.return_value = {"id": "job", "title": "Evaluation"}
            upload = UploadFile(
                filename="tasks.json",
                file=BytesIO(b'[{"id":"one","prompt":"Read","answer":{"count":1}}]'),
            )
            with (
                patch("app.web.settings", test_settings),
                patch(
                    "app.web.job_manager.validate_request",
                    side_effect=lambda *args: events.append("validate") or 1,
                ),
                patch(
                    "app.web.validate_agent_configuration",
                    side_effect=lambda *args: events.append("preflight"),
                ),
                patch(
                    "app.web.ensure_environment",
                    side_effect=lambda *args: events.append("environment"),
                ),
                patch(
                    "app.web.job_manager.start",
                    side_effect=lambda *args, **kwargs: events.append("start") or job,
                ),
            ):
                payload = create_job(
                    tasks_file=upload,
                    attempts=1,
                    parallelism=1,
                    model_name=settings.default_computer_use_model,
                    title=None,
                )

        self.assertEqual(payload["id"], "job")
        self.assertEqual(events, ["validate", "preflight", "environment", "start"])

    def test_environment_start_failure_does_not_create_a_job(self):
        with TemporaryDirectory() as directory:
            test_settings = replace(settings, runs_dir=Path(directory))
            upload = UploadFile(
                filename="tasks.json",
                file=BytesIO(b'[{"id":"one","prompt":"Read"}]'),
            )
            with (
                patch("app.web.settings", test_settings),
                patch("app.web.job_manager.validate_request", return_value=1),
                patch("app.web.validate_agent_configuration"),
                patch(
                    "app.web.ensure_environment",
                    side_effect=EnvironmentUnavailable("Colima unavailable"),
                ),
                patch("app.web.job_manager.start") as start,
            ):
                with self.assertRaises(HTTPException) as raised:
                    create_job(
                        tasks_file=upload,
                        attempts=1,
                        parallelism=1,
                        model_name=settings.default_computer_use_model,
                        title=None,
                    )

        self.assertEqual(raised.exception.status_code, 503)
        start.assert_not_called()

    def test_task_upload_limit_is_enforced_before_evaluation_start(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            test_settings = replace(
                settings, runs_dir=runs_dir, max_task_file_bytes=8
            )
            upload = UploadFile(filename="tasks.json", file=BytesIO(b"0123456789"))
            with (
                patch("app.web.settings", test_settings),
                patch("app.web.job_manager.validate_request") as validate,
            ):
                with self.assertRaises(HTTPException) as raised:
                    create_job(
                        tasks_file=upload,
                        attempts=1,
                        parallelism=1,
                        model_name=settings.default_computer_use_model,
                        title=None,
                    )

            staged_files = list((runs_dir / "uploads").glob("*"))

        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(staged_files, [])
        validate.assert_not_called()

    def test_total_rollout_limit_is_enforced_before_preflight(self):
        with TemporaryDirectory() as directory:
            test_settings = replace(
                settings,
                runs_dir=Path(directory),
                max_rollouts_per_evaluation=1,
            )
            upload = UploadFile(
                filename="tasks.json",
                file=BytesIO(
                    b'[{"id":"one","prompt":"Read"},{"id":"two","prompt":"Read"}]'
                ),
            )
            with (
                patch("app.web.settings", test_settings),
                patch("app.web.validate_agent_configuration") as preflight,
            ):
                with self.assertRaises(HTTPException) as raised:
                    create_job(
                        tasks_file=upload,
                        attempts=1,
                        parallelism=1,
                        model_name=settings.default_computer_use_model,
                        title=None,
                    )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("requests 2 rollouts", raised.exception.detail)
        preflight.assert_not_called()

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

    def test_started_k_attempt_is_reported_running_until_it_finishes(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            job_dir = runs_dir / "job"
            attempt_dir = job_dir / "problem" / "1"
            attempt_dir.mkdir(parents=True)
            (job_dir / "job.json").write_text(
                json.dumps(
                    {
                        "id": "job",
                        "attempts": 1,
                        "status": "running",
                        "problems": [{"id": "problem", "prompt": "Answer it"}],
                    }
                )
            )
            (attempt_dir / "result.json").write_text(
                json.dumps(
                    {"task_id": "problem", "attempt": 1, "status": "running"}
                )
            )
            test_settings = replace(settings, runs_dir=runs_dir)

            with patch("app.web.settings", test_settings):
                problem = _evaluation_payloads()[0]["problems"][0]

        self.assertEqual(problem["outcome_status"], "running")
        self.assertEqual(problem["failed"], 0)

    def test_malformed_result_artifacts_are_ignored(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            folder = runs_dir / "job" / "problem" / "1"
            folder.mkdir(parents=True)
            (folder / "result.json").write_text(
                json.dumps({"task_id": "problem", "attempt": True, "status": "passed"})
            )
            test_settings = replace(settings, runs_dir=runs_dir)

            with patch("app.web.settings", test_settings):
                evaluations = _evaluation_payloads()

        self.assertEqual(evaluations, [])

    def test_malformed_persisted_attempt_count_falls_back_to_results(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            job_dir = runs_dir / "job"
            attempt_dir = job_dir / "problem" / "1"
            attempt_dir.mkdir(parents=True)
            (job_dir / "job.json").write_text(
                json.dumps(
                    {
                        "id": "job",
                        "status": "complete",
                        "attempts": "not-a-number",
                        "problems": [{"id": "problem", "prompt": "Read"}],
                    }
                )
            )
            (attempt_dir / "result.json").write_text(
                json.dumps({"task_id": "problem", "attempt": 1, "status": "passed"})
            )
            test_settings = replace(settings, runs_dir=runs_dir)

            with patch("app.web.settings", test_settings):
                evaluation = _evaluation_payloads()[0]

        self.assertEqual(evaluation["k"], 1)
        self.assertEqual(evaluation["mean_pass_k_percent"], 100)

    def test_replay_ignores_malformed_action_collections(self):
        with TemporaryDirectory() as directory:
            runs_dir = Path(directory)
            folder = runs_dir / "job" / "problem" / "1"
            folder.mkdir(parents=True)
            (folder / "result.json").write_text(json.dumps({"attempt": 1}))
            (folder / "trace.jsonl").write_text(
                json.dumps(
                    {
                        "type": "model_turn",
                        "thinking": "observation",
                        "actions": {"unexpected": "object"},
                    }
                )
                + "\n"
            )
            test_settings = replace(settings, runs_dir=runs_dir)

            with patch("app.web.settings", test_settings):
                payload = get_run("job/problem/1")

        self.assertEqual(payload["slides"], [])


if __name__ == "__main__":
    unittest.main()
