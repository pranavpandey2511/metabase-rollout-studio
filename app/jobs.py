from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from queue import Queue
from threading import Event, Lock, Thread
import time
from typing import Any
from uuid import uuid4

from .agent import PROCESS_MARKER, terminate_recorded_agent
from .artifacts import write_json
from .config import settings
from .models import RunResult, TaskSpec
from .runner import run_single


ACTIVE_JOB_STATUSES = {"queued", "running", "cancelling"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobConflictError(ValueError):
    pass


@dataclass
class Job:
    id: str
    title: str
    source_filename: str
    created_at: str
    tasks: list[TaskSpec]
    attempts: int
    parallelism: int
    model_name: str
    status: str = "queued"
    runs: list[RunResult] = field(default_factory=list)
    cancel_requested_at: str | None = None
    error: str | None = None
    cancel_event: Event = field(default_factory=Event, repr=False)

    def public(self) -> dict[str, Any]:
        ordered_runs = sorted(self.runs, key=lambda run: (run.task_id, run.attempt))
        return {
            "id": self.id,
            "title": self.title,
            "source_filename": self.source_filename,
            "created_at": self.created_at,
            "status": self.status,
            "attempts": self.attempts,
            "parallelism": self.parallelism,
            "model_name": self.model_name,
            "cancel_requested_at": self.cancel_requested_at,
            "error": self.error,
            "task_count": len(self.tasks),
            "problems": [{"id": task.id, "prompt": task.prompt} for task in self.tasks],
            "runs": [run.to_dict() for run in ordered_runs],
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()

    def _validate_request(
        self, attempts: int, parallelism: int, model_name: str | None
    ) -> tuple[str, int]:
        active = next(
            (job for job in self._jobs.values() if job.status in ACTIVE_JOB_STATUSES),
            None,
        )
        if active:
            raise JobConflictError(
                f"Evaluation {active.id} is already {active.status}; stop it or wait for it to finish"
            )
        if attempts < 1:
            raise ValueError("Attempts must be at least 1")
        if attempts > settings.max_attempts_per_problem:
            raise ValueError(
                f"Attempts are limited to {settings.max_attempts_per_problem} per problem for this environment"
            )
        if parallelism < 1:
            raise ValueError("Parallelism must be at least 1")
        selected_model = model_name or settings.default_computer_use_model
        if selected_model not in settings.computer_use_models:
            raise ValueError(f"Unsupported computer-use model: {selected_model}")
        capacity = min(settings.max_parallel_rollouts, len(settings.metabase_urls))
        if capacity < 1:
            raise ValueError("No Metabase environments are configured")
        if parallelism > capacity:
            raise ValueError(f"Parallelism is limited to {capacity} for this environment")
        return selected_model, parallelism

    def validate_request(
        self, attempts: int, parallelism: int, model_name: str | None
    ) -> int:
        with self._lock:
            _, capacity = self._validate_request(attempts, parallelism, model_name)
            return capacity

    def start(
        self,
        tasks: list[TaskSpec],
        attempts: int,
        parallelism: int,
        title: str = "Untitled evaluation",
        source_filename: str = "tasks.json",
        model_name: str | None = None,
    ) -> Job:
        with self._lock:
            selected_model, capacity = self._validate_request(
                attempts, parallelism, model_name
            )
            job = Job(
                id=uuid4().hex[:12],
                title=title,
                source_filename=source_filename,
                created_at=_now(),
                tasks=tasks,
                attempts=attempts,
                parallelism=capacity,
                model_name=selected_model,
                status="running",
            )
            self._jobs[job.id] = job
            self._persist(job)

        executor: ThreadPoolExecutor | None = None
        try:
            executor = ThreadPoolExecutor(
                max_workers=capacity, thread_name_prefix=f"job-{job.id}"
            )
            environments: Queue[str] = Queue()
            for url in settings.metabase_urls[:capacity]:
                environments.put(url)
            futures = [
                executor.submit(self._run_one, job.id, task, attempt, environments)
                for task in tasks
                for attempt in range(1, attempts + 1)
            ]
        except Exception as exc:
            with self._lock:
                job.cancel_event.set()
                job.status = "error"
                job.error = f"Could not schedule evaluation: {exc}"
                self._persist(job)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            raise
        Thread(
            target=self._finish_when_done,
            args=(job.id, executor, futures),
            daemon=True,
        ).start()
        return job

    def _run_one(
        self,
        job_id: str,
        task: TaskSpec,
        attempt: int,
        environments: Queue[str],
    ) -> None:
        environment_url = environments.get()
        try:
            with self._lock:
                job = self._jobs[job_id]
                if job.cancel_event.is_set():
                    return
                cancel_event = job.cancel_event
                model_name = job.model_name
            result = run_single(
                task,
                attempt,
                environment_url,
                job_id,
                model_name=model_name,
                cancel_event=cancel_event,
            )
            with self._lock:
                job = self._jobs[job_id]
                job.runs.append(result)
                self._persist(job)
        finally:
            environments.put(environment_url)

    def _finish_when_done(
        self,
        job_id: str,
        executor: ThreadPoolExecutor,
        futures: list[Future[None]],
    ) -> None:
        orchestrator_errors: list[str] = []
        for future in futures:
            try:
                future.result()
            except Exception as exc:  # Normal rollout failures are captured by run_single.
                orchestrator_errors.append(str(exc))
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_event.is_set():
                job.status = "cancelled"
            elif orchestrator_errors:
                job.status = "error"
                job.error = "Orchestrator failure: " + "; ".join(orchestrator_errors)
            else:
                error_count = sum(run.status == "error" for run in job.runs)
                if error_count:
                    job.status = "error"
                    job.error = (
                        f"{error_count} rollout(s) ended with infrastructure errors; "
                        "inspect and rerun them before using the score."
                    )
                else:
                    job.status = "complete"
            self._persist(job)
        executor.shutdown(wait=False)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public() if job else None

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status not in ACTIVE_JOB_STATUSES:
                return job.public()
            job.cancel_event.set()
            job.cancel_requested_at = job.cancel_requested_at or _now()
            job.status = "cancelling"
            self._persist(job)
            return job.public()

    def shutdown(self, timeout: float) -> None:
        with self._lock:
            active_ids = [
                job.id for job in self._jobs.values() if job.status in ACTIVE_JOB_STATUSES
            ]
        for job_id in active_ids:
            self.cancel(job_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not any(
                    job.status in ACTIVE_JOB_STATUSES for job in self._jobs.values()
                ):
                    return
            time.sleep(0.05)

    def reconcile_interrupted_jobs(self) -> None:
        interruption = "Interrupted because Rollout Studio stopped before the evaluation finished."
        for job_path in settings.runs_dir.glob("*/job.json"):
            try:
                payload = json.loads(job_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("status") not in ACTIVE_JOB_STATUSES:
                continue
            for marker_path in job_path.parent.rglob(PROCESS_MARKER):
                terminate_recorded_agent(marker_path.parent)

            result_paths = list(job_path.parent.rglob("result.json"))
            results: list[dict[str, Any]] = []
            for result_path in result_paths:
                try:
                    result = json.loads(result_path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(result, dict):
                    results.append(result)
                if isinstance(result, dict) and result.get("status") == "running":
                    result["status"] = "error"
                    result["error"] = interruption
                    result["finished_at"] = _now()
                    write_json(result_path, result)

            terminal_status = self._completed_persisted_status(payload, results)
            if terminal_status is not None:
                payload["status"], payload["error"] = terminal_status
            else:
                payload["status"] = "error"
                payload["error"] = interruption
            write_json(job_path, payload)

    @staticmethod
    def _completed_persisted_status(
        payload: dict[str, Any], results: list[dict[str, Any]]
    ) -> tuple[str, str | None] | None:
        attempts = payload.get("attempts")
        problems = payload.get("problems")
        if (
            not isinstance(attempts, int)
            or isinstance(attempts, bool)
            or attempts < 1
            or not isinstance(problems, list)
        ):
            return None
        task_ids = {
            problem.get("id")
            for problem in problems
            if isinstance(problem, dict)
            and isinstance(problem.get("id"), str)
            and problem.get("id")
        }
        if len(task_ids) != len(problems) or not task_ids:
            return None
        terminal = {"passed", "failed", "needs_review", "error", "cancelled"}
        observed = {
            (result.get("task_id"), result.get("attempt")): result.get("status")
            for result in results
            if isinstance(result.get("task_id"), str)
            and isinstance(result.get("attempt"), int)
            and not isinstance(result.get("attempt"), bool)
        }
        expected = {
            (task_id, attempt)
            for task_id in task_ids
            for attempt in range(1, attempts + 1)
        }
        if set(observed) != expected or any(observed[key] not in terminal for key in expected):
            return None
        statuses = set(observed.values())
        if "error" in statuses:
            count = sum(status == "error" for status in observed.values())
            return (
                "error",
                f"{count} rollout(s) ended with infrastructure errors; inspect and rerun them before using the score.",
            )
        if "cancelled" in statuses:
            return "cancelled", payload.get("error") if isinstance(payload.get("error"), str) else None
        return "complete", None

    @staticmethod
    def _persist(job: Job) -> None:
        write_json(settings.runs_dir / job.id / "job.json", job.public())


job_manager = JobManager()
