from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
from threading import Event
import time
from urllib.parse import urlsplit

from .config import settings
from .grading import submitted_answer_text
from .models import TaskSpec


class AgentConfigurationError(RuntimeError):
    pass


class RolloutCancelled(RuntimeError):
    pass


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, port


def _redact_secrets(text: str) -> str:
    for secret in (settings.metabase_password, settings.gemini_api_key):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def run_agent(
    task: TaskSpec,
    environment_url: str,
    artifact_dir: Path,
    model_name: str,
    cancel_event: Event | None = None,
) -> str:
    main_py = settings.computer_use_dir / "main.py"
    if not main_py.exists():
        raise AgentConfigurationError(
            f"Computer-use checkout not found at {settings.computer_use_dir}. "
            "Clone the supplied repository there or set COMPUTER_USE_DIR."
        )
    if not settings.gemini_api_key:
        raise AgentConfigurationError("GEMINI_API_KEY is missing from .env")
    if not settings.metabase_password:
        raise AgentConfigurationError("METABASE_PASSWORD is missing from .env")
    if model_name not in settings.computer_use_models:
        raise AgentConfigurationError(f"Unsupported computer-use model: {model_name}")

    start_url = task.initial_url or environment_url
    if _origin(start_url) != _origin(environment_url):
        raise AgentConfigurationError("Task initial_url must stay on the configured Metabase origin")
    login_instruction = (
        f"Open the Metabase site at {environment_url}. Sign in with email "
        f"{settings.metabase_email} and password {settings.metabase_password}. "
    )
    query = (
        login_instruction
        + "You are operating a read-only evaluation gym. Use only the visible Metabase UI "
        "through screenshots, mouse actions, and keyboard actions. Do not navigate outside "
        "this Metabase site and do not attempt to use a shell, code execution, a database "
        "connection, or any host-computer capability. Do not create, edit, or delete data. "
        + task.prompt
        + " When the task is complete, submit only the requested final JSON value. "
        "Do not include analysis, explanation, or markdown in the final response."
    )
    python_executable = settings.computer_use_python
    if "/" in python_executable and not Path(python_executable).is_absolute():
        # Do not resolve: virtualenv's Python is a symlink and resolving it
        # bypasses the virtualenv's site-packages.
        python_executable = str(settings.root / python_executable)
    command = [
        python_executable,
        str(main_py),
        "--query", query,
        "--initial_url", start_url,
        "--model", model_name,
    ]
    environment = os.environ.copy()
    environment["GEMINI_API_KEY"] = settings.gemini_api_key
    environment.setdefault("PLAYWRIGHT_HEADLESS", "true")
    environment["ROLLOUT_ARTIFACT_DIR"] = str(artifact_dir)
    environment["ROLLOUT_REDACT_VALUES"] = settings.metabase_password
    environment["ROLLOUT_NONINTERACTIVE"] = "true"
    environment["ROLLOUT_AUTO_CONFIRM_DISCARD"] = "true"
    process = subprocess.Popen(
        command,
        cwd=settings.computer_use_dir,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    cancelled = False
    timed_out = False
    stdout: str | None = None
    stderr: str | None = None
    deadline = time.monotonic() + settings.rollout_timeout_seconds
    while True:
        if cancel_event and cancel_event.is_set():
            cancelled = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            break
        if time.monotonic() >= deadline:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            break
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            continue
    if stdout is None or stderr is None:
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
    transcript = _redact_secrets((stdout or "") + ("\n[stderr]\n" + stderr if stderr else ""))
    (artifact_dir / "agent.log").write_text(transcript)
    trace_path = artifact_dir / "trace.jsonl"
    if trace_path.exists():
        final_lines = [
            line for line in trace_path.read_text().splitlines()
            if '"type": "final"' in line
        ]
        if final_lines:
            import json
            output = json.loads(final_lines[-1])["output"] or ""
            submitted = submitted_answer_text(_redact_secrets(output))
            (artifact_dir / "final_output.txt").write_text(submitted)
    if cancelled:
        raise RolloutCancelled("Rollout cancelled from the UI.")
    if timed_out:
        raise TimeoutError(f"Agent exceeded {settings.rollout_timeout_seconds} seconds")
    if process.returncode:
        raise RuntimeError(f"Agent exited with status {process.returncode}; see agent.log")
    return transcript
