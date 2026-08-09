from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from threading import Event
from uuid import uuid4
from html import escape

from .agent import RolloutCancelled, run_agent
from .config import settings
from .grading import grade_task
from .models import RunResult, TaskSpec


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_result(result: RunResult, artifact_dir: Path) -> None:
    (artifact_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))


def _write_screenshot_gallery(artifact_dir: Path) -> None:
    screenshot_dir = artifact_dir / "screenshots"
    images = sorted(screenshot_dir.glob("*.png")) if screenshot_dir.exists() else []
    if not images:
        return
    thumbnails = "\n".join(
        f'<figure><figcaption>{escape(image.stem)}</figcaption><img src="{escape(image.name)}"></figure>'
        for image in images
    )
    screenshot_dir.joinpath("index.html").write_text(
        "<!doctype html><title>Rollout screenshots</title>"
        "<style>body{font-family:system-ui;margin:24px}figure{margin:0 0 24px}img{max-width:100%;border:1px solid #ddd}</style>"
        + thumbnails
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
    safe_task_id = re.sub(r"[^A-Za-z0-9._-]+", "_", task.id).strip("._") or "task"
    artifact_dir = settings.runs_dir / job_id / safe_task_id / str(attempt)
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
        final_answer = final_output_path.read_text() if final_output_path.exists() else ""
        result.grade = grade_task(task, final_answer)
        result.status = result.grade.status
    except RolloutCancelled as exc:
        result.status = "cancelled"
        result.error = str(exc)
    except Exception as exc:  # A failed rollout is an expected job result.
        result.status = "error"
        result.error = str(exc)
    finally:
        _write_screenshot_gallery(artifact_dir)
        result.duration_seconds = round(time.monotonic() - started, 2)
        result.finished_at = _now()
        _save_result(result, artifact_dir)

    return result
