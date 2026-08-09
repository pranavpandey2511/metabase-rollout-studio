from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json


class TaskFileError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    id: str
    prompt: str
    initial_url: str | None = None
    expected_answer: object | None = None


@dataclass
class Grade:
    status: str
    score: float
    evidence: str
    method: str = ""
    expected: Any = None
    actual: Any = None
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    task_id: str
    attempt: int
    environment_url: str
    model_name: str
    status: str
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    grade: Grade | None = None
    error: str | None = None
    artifact_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        return result


def _prompt_from(raw: dict[str, Any]) -> str:
    for key in ("prompt", "description", "task", "instruction"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise TaskFileError("must include non-empty prompt, description, task, or instruction")


def _expected_answer_from(raw: Any, task_id: str) -> object | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TaskFileError(f"task {task_id}: answer must be valid JSON") from exc
    if isinstance(raw, (dict, list, str, int, float, bool)):
        return raw
    raise TaskFileError(f"task {task_id}: answer must be JSON-compatible")


def parse_tasks(payload: object) -> list[TaskSpec]:
    raw_tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise TaskFileError("expected a non-empty JSON list or an object with a tasks list")

    tasks: list[TaskSpec] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise TaskFileError(f"task at index {index} must be an object")
        task_id = str(raw.get("id", "")).strip()
        if not task_id:
            raise TaskFileError(f"task at index {index} is missing id")
        if task_id in ids:
            raise TaskFileError(f"duplicate task id {task_id!r}")
        ids.add(task_id)
        initial_url = raw.get("initial_url") or raw.get("start_url")
        if initial_url is not None and not isinstance(initial_url, str):
            raise TaskFileError(f"task {task_id}: initial_url must be a string")
        if raw.get("grader") is not None:
            raise TaskFileError(
                f"task {task_id}: custom graders are outside this read-only MVP; supply answer instead"
            )
        expected_answer = _expected_answer_from(raw.get("answer"), task_id)
        tasks.append(
            TaskSpec(
                id=task_id,
                prompt=_prompt_from(raw),
                initial_url=initial_url,
                expected_answer=expected_answer,
            )
        )
    return tasks


def load_tasks(path: Path) -> list[TaskSpec]:
    try:
        return parse_tasks(json.loads(path.read_text()))
    except json.JSONDecodeError as exc:
        raise TaskFileError(f"invalid JSON: {exc.msg}") from exc
