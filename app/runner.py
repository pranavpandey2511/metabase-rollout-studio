from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from threading import Event
from uuid import uuid4

from .agent import RolloutCancelled, run_agent
from .artifacts import write_json
from .config import settings
from .grading import grade_task
from .models import Grade, RunResult, TaskSpec


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_result(result: RunResult, artifact_dir: Path) -> None:
    write_json(artifact_dir / "result.json", result.to_dict())


def _completion_failure(reason: str) -> Grade:
    return Grade(
        status="failed",
        score=0.0,
        evidence=reason,
        method="rollout_completion",
        actual=None,
        checks=[
            {
                "name": "Agent submitted a final answer within the rollout limit",
                "passed": False,
                "detail": reason,
            }
        ],
    )


def run_single(
    task: TaskSpec,
    attempt: int,
    environment_url: str,
    job_id: str,
    model_name: str | None = None,
    cancel_event: Event | None = None,
) -> RunResult:
    selected_model = model_name or settings.default_computer_use_model
    run_id = f"{task.id}-{attempt}-{uuid4().hex[:8]}"
    artifact_dir = settings.runs_dir / job_id / task.id / str(attempt)
    artifact_dir.mkdir(parents=True, exist_ok=False)
    result = RunResult(
        run_id=run_id,
        task_id=task.id,
        attempt=attempt,
        environment_url=environment_url,
        model_name=selected_model,
        status="running",
        started_at=_now(),
        artifact_dir=str(artifact_dir.relative_to(settings.runs_dir)),
    )
    _save_result(result, artifact_dir)
    started = time.monotonic()
    try:
        run_agent(
            task,
            environment_url,
            artifact_dir,
            selected_model,
            cancel_event=cancel_event,
        )
        final_output_path = artifact_dir / "final_output.txt"
        if not final_output_path.exists() or not final_output_path.read_text().strip():
            result.grade = _completion_failure(
                "The agent completed without submitting a final JSON answer."
            )
        else:
            result.grade = grade_task(task, final_output_path.read_text())
        result.status = result.grade.status
    except RolloutCancelled as exc:
        result.status = "cancelled"
        result.error = str(exc)
    except TimeoutError:
        result.grade = _completion_failure(
            f"The agent did not finish within {settings.rollout_timeout_seconds} seconds."
        )
        result.status = result.grade.status
    except Exception as exc:  # A failed rollout is an expected job result.
        result.status = "error"
        result.error = str(exc)
    finally:
        result.duration_seconds = round(time.monotonic() - started, 2)
        result.finished_at = _now()
        _save_result(result, artifact_dir)

    return result
