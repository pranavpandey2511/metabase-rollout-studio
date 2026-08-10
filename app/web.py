from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .agent import AgentConfigurationError, validate_agent_configuration
from .config import settings
from .grading import submitted_answer_text
from .jobs import JobConflictError, job_manager
from .models import TaskFileError, load_tasks
from .runtime import EnvironmentUnavailable, ensure_environment
from .ui import INDEX_HTML


@asynccontextmanager
async def lifespan(_: FastAPI):
    job_manager.reconcile_interrupted_jobs()
    yield
    job_manager.shutdown(settings.shutdown_grace_seconds)


app = FastAPI(title="Metabase Rollout Studio", lifespan=lifespan)
app.mount("/runs", StaticFiles(directory=settings.runs_dir), name="runs")


def _artifact_path(relative_path: str) -> Path:
    candidate = (settings.runs_dir / relative_path).resolve()
    root = settings.runs_dir.resolve()
    if candidate == root or root not in candidate.parents or not candidate.is_dir():
        raise HTTPException(404, "Run not found")
    return candidate


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    records: list[dict[str, object]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _run_summary(result_path: Path) -> dict[str, object] | None:
    result = _read_json(result_path)
    if result is None:
        return None
    task_id = result.get("task_id")
    attempt = result.get("attempt")
    status = result.get("status")
    if (
        not isinstance(task_id, str)
        or not task_id
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
        or status
        not in {"running", "passed", "failed", "needs_review", "error", "cancelled"}
    ):
        return None
    artifact_dir = str(result_path.parent.relative_to(settings.runs_dir))
    result["artifact_dir"] = artifact_dir
    result["screenshot_count"] = len(list(result_path.parent.glob("screenshots/*.png")))
    result["has_trace"] = (result_path.parent / "trace.jsonl").exists()
    return result


def _runs() -> list[dict[str, object]]:
    summaries = filter(None, (_run_summary(path) for path in settings.runs_dir.rglob("result.json")))
    return sorted(
        summaries,
        key=lambda run: str(run.get("finished_at") or run.get("started_at") or ""),
        reverse=True,
    )


def _human_title(value: str) -> str:
    words = value.removesuffix(".json").replace("_", " ").replace("-", " ").split()
    return " ".join(word.capitalize() for word in words) or "Untitled evaluation"


def _require_local_browser_origin(origin: str | None) -> None:
    if origin is None:
        return
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or port != 8000
    ):
        raise HTTPException(403, "Evaluation creation is limited to the local dashboard")


def _problem_outcome(
    *,
    completed: bool,
    evaluation_status: str,
    passed: int,
    errors: int,
    cancelled: int,
    needs_review: int,
    attempts: int,
) -> str:
    if errors or (not completed and evaluation_status == "error"):
        return "error"
    if cancelled or (not completed and evaluation_status == "cancelled"):
        return "cancelled"
    if needs_review:
        return "needs_review"
    if not completed:
        return "running"
    if passed == attempts:
        return "passed"
    if passed == 0:
        return "failed"
    return "partial"


def _evaluation_metadata(evaluation_id: str) -> dict[str, object]:
    job = _read_json(settings.runs_dir / evaluation_id / "job.json") or {}
    if job:
        return job
    return {"id": evaluation_id, "title": _human_title(evaluation_id), "problems": []}


def _evaluation_payloads() -> list[dict[str, object]]:
    runs = _runs()
    evaluation_ids = {str(run["artifact_dir"]).split("/", 1)[0] for run in runs}
    evaluation_ids.update(
        path.parent.name for path in settings.runs_dir.glob("*/job.json") if path.parent.name != "uploads"
    )
    evaluations: list[dict[str, object]] = []
    for evaluation_id in evaluation_ids:
        metadata = _evaluation_metadata(evaluation_id)
        evaluation_runs = [run for run in runs if str(run["artifact_dir"]).split("/", 1)[0] == evaluation_id]
        prompts = {
            str(problem.get("id")): str(problem.get("prompt") or "")
            for problem in metadata.get("problems", [])
            if isinstance(problem, dict) and problem.get("id") is not None
        }
        problem_ids = set(prompts) | {str(run.get("task_id")) for run in evaluation_runs}
        configured_attempts = metadata.get("attempts")
        expected_attempts = (
            configured_attempts
            if isinstance(configured_attempts, int)
            and not isinstance(configured_attempts, bool)
            and configured_attempts > 0
            else max((int(run["attempt"]) for run in evaluation_runs), default=1)
        )
        evaluation_status = str(metadata.get("status") or "complete")
        problems: list[dict[str, object]] = []
        for problem_id in sorted(problem_ids):
            problem_runs = sorted(
                (
                    run
                    for run in evaluation_runs
                    if str(run.get("task_id")) == problem_id
                ),
                key=lambda run: int(run.get("attempt") or 0),
            )
            passed = sum(run.get("status") == "passed" for run in problem_runs)
            failed = sum(run.get("status") == "failed" for run in problem_runs)
            errors = sum(run.get("status") == "error" for run in problem_runs)
            needs_review = sum(run.get("status") == "needs_review" for run in problem_runs)
            cancelled = sum(run.get("status") == "cancelled" for run in problem_runs)
            completed = (
                len(problem_runs) >= expected_attempts
                and all(run.get("status") != "running" for run in problem_runs)
            )
            pass_k = passed / expected_attempts if expected_attempts else 0
            pass_k_percent = round(pass_k * 100)
            outcome_status = _problem_outcome(
                completed=completed,
                evaluation_status=evaluation_status,
                passed=passed,
                errors=errors,
                cancelled=cancelled,
                needs_review=needs_review,
                attempts=expected_attempts,
            )
            problems.append(
                {
                    "id": problem_id,
                    "prompt": prompts.get(problem_id, "Prompt was not persisted for this legacy run."),
                    "run_count": len(problem_runs),
                    "passed": passed,
                    "failed": failed,
                    "errors": errors,
                    "needs_review": needs_review,
                    "cancelled": cancelled,
                    "expected_attempts": expected_attempts,
                    "k": expected_attempts,
                    "pass_k": round(pass_k, 4),
                    "pass_k_percent": pass_k_percent,
                    "pass_rate": pass_k_percent,
                    "outcome_status": outcome_status,
                    "runs": problem_runs,
                }
            )
        passed = sum(run.get("status") == "passed" for run in evaluation_runs)
        passed_problems = sum(problem["outcome_status"] == "passed" for problem in problems)
        failed_problems = sum(problem["outcome_status"] == "failed" for problem in problems)
        running_problems = sum(problem["outcome_status"] == "running" for problem in problems)
        partial_problems = sum(problem["outcome_status"] == "partial" for problem in problems)
        error_problems = sum(problem["outcome_status"] == "error" for problem in problems)
        review_problems = sum(
            problem["outcome_status"] == "needs_review" for problem in problems
        )
        cancelled_problems = sum(
            problem["outcome_status"] == "cancelled" for problem in problems
        )
        mean_pass_k_percent = (
            round(sum(problem["pass_k_percent"] for problem in problems) / len(problems))
            if problems
            else 0
        )
        latest = max(
            (str(run.get("finished_at") or run.get("started_at") or "") for run in evaluation_runs),
            default=str(metadata.get("created_at") or ""),
        )
        evaluations.append(
            {
                "id": evaluation_id,
                "title": metadata.get("title") or _human_title(evaluation_id),
                "source_filename": metadata.get("source_filename") or "tasks.json",
                "model_name": metadata.get("model_name")
                or next(
                    (run.get("model_name") for run in evaluation_runs if run.get("model_name")),
                    "Legacy / unknown",
                ),
                "status": evaluation_status,
                "controllable": metadata.get("controllable") is not False,
                "error": metadata.get("error"),
                "created_at": metadata.get("created_at"),
                "updated_at": latest,
                "problem_count": len(problems),
                "passed_problem_count": passed_problems,
                "failed_problem_count": failed_problems,
                "running_problem_count": running_problems,
                "partial_problem_count": partial_problems,
                "error_problem_count": error_problems,
                "needs_review_problem_count": review_problems,
                "cancelled_problem_count": cancelled_problems,
                "k": expected_attempts,
                "mean_pass_k_percent": mean_pass_k_percent,
                "run_count": len(evaluation_runs),
                "passed": passed,
                "failed": sum(run.get("status") == "failed" for run in evaluation_runs),
                "errors": sum(run.get("status") == "error" for run in evaluation_runs),
                "needs_review": sum(run.get("status") == "needs_review" for run in evaluation_runs),
                "cancelled": sum(run.get("status") == "cancelled" for run in evaluation_runs),
                "pass_rate": mean_pass_k_percent,
                "problems": problems,
            }
        )
    return sorted(evaluations, key=lambda evaluation: str(evaluation.get("updated_at") or ""), reverse=True)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


@app.get("/api/runs")
def get_runs() -> dict[str, object]:
    return {"runs": _runs()}


@app.get("/api/evaluations")
def get_evaluations() -> dict[str, object]:
    return {"evaluations": _evaluation_payloads()}


@app.get("/api/config")
def get_config() -> dict[str, object]:
    return {
        "computer_use_models": settings.computer_use_models,
        "default_computer_use_model": settings.default_computer_use_model,
        "max_attempts_per_problem": settings.max_attempts_per_problem,
        "max_rollouts_per_evaluation": settings.max_rollouts_per_evaluation,
        "max_parallel_rollouts": min(
            settings.max_parallel_rollouts, len(settings.metabase_urls)
        ),
        "auto_start_environment": settings.auto_start_environment,
    }


@app.get("/api/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str) -> dict[str, object]:
    evaluation = next((item for item in _evaluation_payloads() if item["id"] == evaluation_id), None)
    if evaluation is None:
        raise HTTPException(404, "Evaluation not found")
    return evaluation


@app.get("/api/runs/{artifact_dir:path}")
def get_run(artifact_dir: str) -> dict[str, object]:
    folder = _artifact_path(artifact_dir)
    result = _read_json(folder / "result.json")
    if result is None:
        raise HTTPException(404, "Run result not found")

    trace_events = _read_json_lines(folder / "trace.jsonl")
    model_turns = [event for event in trace_events if event.get("type") == "model_turn"]
    final_event = next(
        (event for event in reversed(trace_events) if event.get("type") == "final"),
        {},
    )
    action_context: dict[str, dict[str, object]] = {}
    fallback_actions: list[dict[str, object]] = []
    for turn_index, turn in enumerate(model_turns):
        if turn_index + 1 < len(model_turns):
            observed_thinking = model_turns[turn_index + 1].get("thinking")
        else:
            observed_thinking = final_event.get("thinking") or final_event.get("output")
        thinking = str(
            observed_thinking
            or "The agent did not record a separate observation after this screenshot."
        )
        actions = turn.get("actions")
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            context = {"thinking": thinking, "action": action}
            action_id = action.get("id")
            if isinstance(action_id, str):
                action_context[action_id] = context
            fallback_actions.append(context)

    manifest = {
        item.get("screenshot"): item
        for item in _read_json_lines(folder / "screenshots" / "manifest.jsonl")
        if isinstance(item.get("screenshot"), str)
    }
    image_paths = sorted((folder / "screenshots").glob("*.png"))
    slides: list[dict[str, object]] = []
    for index, image in enumerate(image_paths):
        item = manifest.get(image.name, {})
        exact = action_context.get(item.get("action_id")) if isinstance(item, dict) else None
        fallback = fallback_actions[min(index, len(fallback_actions) - 1)] if fallback_actions else {}
        context = exact or fallback
        relative_image = image.relative_to(settings.runs_dir.resolve()).as_posix()
        slides.append(
            {
                "number": index + 1,
                "screenshot": "/runs/" + quote(relative_image, safe="/"),
                "url": item.get("url") if isinstance(item, dict) else None,
                "thinking": context.get("thinking", "No model reasoning was recorded for this step."),
                "action": context.get("action"),
                "mapping": "exact" if exact else "estimated",
            }
        )
    result["artifact_dir"] = artifact_dir
    result["slides"] = slides
    final_output_path = folder / "final_output.txt"
    raw_final = (
        final_output_path.read_text()
        if final_output_path.exists()
        else str(final_event.get("output") or "")
    )
    result["final"] = submitted_answer_text(raw_final) or None
    return result


@app.post("/api/jobs")
def create_job(
    tasks_file: UploadFile = File(...),
    attempts: int = Form(...),
    parallelism: int = Form(...),
    model_name: str = Form(...),
    title: str | None = Form(None),
    origin: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    _require_local_browser_origin(origin)
    if not tasks_file.filename or not tasks_file.filename.lower().endswith(".json"):
        raise HTTPException(400, "Upload a .json task file")
    staging_dir = settings.runs_dir / "uploads"
    staging_dir.mkdir(exist_ok=True)
    staged = staging_dir / f"upload-{uuid4().hex}.json"
    try:
        total = 0
        with staged.open("wb") as output:
            while chunk := tasks_file.file.read(64 * 1024):
                total += len(chunk)
                if total > settings.max_task_file_bytes:
                    raise HTTPException(
                        413,
                        f"Task file exceeds the {settings.max_task_file_bytes}-byte limit",
                    )
                output.write(chunk)
        try:
            tasks = load_tasks(staged)
        except TaskFileError as exc:
            raise HTTPException(400, str(exc)) from exc
        payload = _read_json(staged) or {}
    finally:
        staged.unlink(missing_ok=True)
    rollout_count = len(tasks) * attempts
    if attempts > 0 and rollout_count > settings.max_rollouts_per_evaluation:
        raise HTTPException(
            400,
            f"Evaluation requests {rollout_count} rollouts; the configured limit is "
            f"{settings.max_rollouts_per_evaluation}",
        )
    requested_title = title or (payload.get("title") if isinstance(payload.get("title"), str) else None)
    try:
        capacity = job_manager.validate_request(attempts, parallelism, model_name)
    except JobConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        validate_agent_configuration(model_name)
    except AgentConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        ensure_environment(capacity)
    except EnvironmentUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        job = job_manager.start(
            tasks,
            attempts,
            parallelism,
            title=requested_title.strip() if requested_title and requested_title.strip() else _human_title(tasks_file.filename),
            source_filename=tasks_file.filename,
            model_name=model_name,
        )
    except JobConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return job.public()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = job_manager.get(job_id)
    if job is None:
        persisted = _read_json(settings.runs_dir / job_id / "job.json")
        if persisted is not None:
            persisted["expired"] = True
            return persisted
        # A browser tab can outlive the in-memory job manager after a server
        # restart. Return a terminal state so both old and new UI pollers stop.
        return {
            "id": job_id,
            "status": "error",
            "expired": True,
            "error": "This job is no longer active. The server was restarted or its artifacts were removed.",
        }
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, object]:
    job = job_manager.cancel(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job
