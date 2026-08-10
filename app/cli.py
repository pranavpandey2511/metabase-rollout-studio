from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .agent import AgentConfigurationError, validate_agent_configuration
from .artifacts import write_json
from .config import settings
from .models import load_tasks
from .runtime import EnvironmentUnavailable, ensure_environment
from .runner import run_single


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Metabase computer-use rollout")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--tasks", required=True, type=Path)
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--attempt", default=1, type=int)
    run_parser.add_argument(
        "--environment-url",
        choices=settings.metabase_urls,
        default=settings.metabase_urls[0],
    )
    run_parser.add_argument(
        "--model",
        choices=settings.computer_use_models,
        default=settings.default_computer_use_model,
    )
    args = parser.parse_args()

    tasks = load_tasks(args.tasks)
    task = next((candidate for candidate in tasks if candidate.id == args.task_id), None)
    if task is None:
        parser.error(f"task id {args.task_id!r} was not found")
    if args.attempt < 1:
        parser.error("--attempt must be at least 1")
    try:
        validate_agent_configuration(args.model)
        ensure_environment(settings.metabase_urls.index(args.environment_url) + 1)
    except (AgentConfigurationError, EnvironmentUnavailable) as exc:
        parser.error(str(exc))

    evaluation_id = f"cli-{uuid4().hex[:12]}"
    job_path = settings.runs_dir / evaluation_id / "job.json"
    metadata = {
        "id": evaluation_id,
        "title": f"CLI rollout: {task.id}",
        "source_filename": args.tasks.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "attempts": 1,
        "parallelism": 1,
        "model_name": args.model,
        "controllable": False,
        "task_count": 1,
        "problems": [{"id": task.id, "prompt": task.prompt}],
    }
    write_json(job_path, metadata)
    result = run_single(
        task,
        args.attempt,
        args.environment_url,
        evaluation_id,
        model_name=args.model,
    )
    metadata["status"] = "error" if result.status == "error" else "complete"
    metadata["error"] = result.error if result.status == "error" else None
    write_json(job_path, metadata)
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
