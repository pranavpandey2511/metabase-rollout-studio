from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import settings
from .models import load_tasks
from .runner import run_single


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Metabase computer-use rollout")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--tasks", required=True, type=Path)
    run_parser.add_argument("--task-id", required=True)
    run_parser.add_argument("--attempt", default=1, type=int)
    run_parser.add_argument("--environment-url", default=settings.metabase_urls[0])
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
    result = run_single(task, args.attempt, args.environment_url, "cli", model_name=args.model)
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
