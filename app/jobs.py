from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from queue import Queue
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from .config import settings
from .models import RunResult, TaskSpec
from .runner import run_single


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    cancel_event: Event = field(default_factory=Event, repr=False)

    def public(self) -> dict[str, Any]:
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
            "task_count": len(self.tasks),
            "problems": [{"id": task.id, "prompt": task.prompt} for task in self.tasks],
            "runs": [run.to_dict() for run in self.runs],
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()

    def start(
        self,
        tasks: list[TaskSpec],
        attempts: int,
        parallelism: int,
        title: str = "Untitled evaluation",
        source_filename: str = "tasks.json",
        model_name: str | None = None,
    ) -> Job:
        if attempts < 1:
            raise ValueError("Attempts must be at least 1")
        if attempts > settings.max_attempts_per_problem:
            raise ValueError(
                f"Attempts are limited to {settings.max_attempts_per_problem} per problem for this environment"
            )
        selected_model = model_name or settings.default_computer_use_model
        if selected_model not in settings.computer_use_models:
            raise ValueError(f"Unsupported computer-use model: {selected_model}")
        capacity = min(parallelism, settings.max_parallel_rollouts, len(settings.metabase_urls))
        if capacity < 1:
            raise ValueError("No Metabase environments are configured")
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
        with self._lock:
            self._jobs[job.id] = job
            self._persist(job)
        executor = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix=f"job-{job.id}")
        environments: Queue[str] = Queue()
        for url in settings.metabase_urls[:capacity]:
            environments.put(url)
        futures = [
            executor.submit(self._run_one, job.id, task, attempt, environments)
            for task in tasks
            for attempt in range(1, attempts + 1)
        ]
        Thread(target=self._finish_when_done, args=(job.id, executor, futures), daemon=True).start()
        return job

    def _run_one(self, job_id: str, task: TaskSpec, attempt: int, environments: Queue[str]) -> None:
        environment_url = environments.get()
        try:
            with self._lock:
                job = self._jobs[job_id]
                if job.cancel_event.is_set():
                    return
                cancel_event = job.cancel_event
            result = run_single(
                task,
                attempt,
                environment_url,
                job_id,
                model_name=job.model_name,
                cancel_event=cancel_event,
            )
            with self._lock:
                job = self._jobs[job_id]
                if not job.cancel_event.is_set():
                    job.status = "running"
                job.runs.append(result)
                self._persist(job)
        finally:
            environments.put(environment_url)

    def _finish_when_done(self, job_id: str, executor: ThreadPoolExecutor, futures: list[Future[None]]) -> None:
        for future in futures:
            try:
                future.result()
            except Exception:  # Defensive: all normal failures are captured in run_single.
                with self._lock:
                    self._jobs[job_id].status = "error"
                    self._persist(self._jobs[job_id])
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_event.is_set():
                job.status = "cancelled"
            elif job.status != "error":
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
            if job.status in {"complete", "error", "cancelled"}:
                return job.public()
            job.cancel_event.set()
            job.cancel_requested_at = job.cancel_requested_at or _now()
            job.status = "cancelling"
            self._persist(job)
            return job.public()

    def _persist(self, job: Job) -> None:
        job_dir = settings.runs_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.json").write_text(json.dumps(job.public(), indent=2))


job_manager = JobManager()
