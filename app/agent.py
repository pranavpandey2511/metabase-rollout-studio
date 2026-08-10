from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from threading import Event
import time
from urllib.parse import urlsplit

from .artifacts import write_json
from .config import settings
from .grading import submitted_answer_text
from .models import TaskSpec


class AgentConfigurationError(RuntimeError):
    pass


class RolloutCancelled(RuntimeError):
    pass


PROCESS_MARKER = "agent-process.json"


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, port


def _redact_secrets(text: str) -> str:
    for secret in (settings.metabase_password, settings.gemini_api_key):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _python_executable() -> str:
    configured = settings.computer_use_python
    path = Path(configured)
    if path.is_absolute():
        executable = path
    elif path.parent != Path("."):
        # Do not resolve: virtualenv's Python is a symlink and resolving it
        # bypasses the virtualenv's site-packages.
        executable = settings.root / path
    else:
        located = shutil.which(configured)
        if not located:
            raise AgentConfigurationError(
                f"Configured computer-use Python executable was not found: {configured}"
            )
        return located
    if not executable.is_file():
        raise AgentConfigurationError(
            f"Configured computer-use Python executable was not found: {executable}"
        )
    return str(executable)


def validate_agent_configuration(model_name: str, *, check_dependencies: bool = True) -> str:
    """Fail once, before K rollouts, when the local Gemini adapter is not usable."""
    main_py = settings.computer_use_dir / "main.py"
    if not main_py.is_file():
        raise AgentConfigurationError(
            f"Computer-use checkout not found at {settings.computer_use_dir}. "
            "Run ./scripts/setup_agent.sh or set COMPUTER_USE_DIR."
        )
    if not settings.gemini_api_key:
        raise AgentConfigurationError("GEMINI_API_KEY is missing from .env")
    if not settings.metabase_password:
        raise AgentConfigurationError("METABASE_PASSWORD is missing from .env")
    if model_name not in settings.computer_use_models:
        raise AgentConfigurationError(f"Unsupported computer-use model: {model_name}")
    python_executable = _python_executable()
    if not check_dependencies:
        return python_executable

    try:
        check = subprocess.run(
            [
                python_executable,
                "-c",
                (
                    "from agent import ROLLOUT_ADAPTER_VERSION; "
                    "from pathlib import Path; "
                    "from playwright.sync_api import sync_playwright; "
                    "assert ROLLOUT_ADAPTER_VERSION == 1, "
                    "'Rollout adapter patch is missing'; "
                    "p=sync_playwright().start(); "
                    "assert Path(p.chromium.executable_path).is_file(), "
                    "'Playwright Chromium is not installed'; "
                    "p.stop()"
                ),
            ],
            cwd=settings.computer_use_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentConfigurationError(
            f"Computer-use dependency preflight could not finish: {exc}"
        ) from exc
    if check.returncode:
        output = (check.stderr or check.stdout or "").strip()
        detail = output.splitlines()[-1] if output else "dependency check failed"
        raise AgentConfigurationError(
            "Computer-use dependencies are not ready: " + detail
        )
    return python_executable


def _signal_process_group(process_group: int, signal_number: int) -> None:
    try:
        os.killpg(process_group, signal_number)
    except ProcessLookupError:
        pass


def terminate_recorded_agent(artifact_dir: Path, grace_seconds: float = 2.0) -> bool:
    """Terminate a verified child left behind by a hard dashboard shutdown."""
    marker_path = artifact_dir / PROCESS_MARKER
    try:
        marker = json.loads(marker_path.read_text())
        pid = marker["pid"]
        process_group = marker["process_group"]
        expected_main = marker["main_py"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        marker_path.unlink(missing_ok=True)
        return False
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid < 1
        or not isinstance(process_group, int)
        or isinstance(process_group, bool)
        or process_group != pid
        or expected_main != str(settings.computer_use_dir / "main.py")
    ):
        marker_path.unlink(missing_ok=True)
        return False
    try:
        if os.getpgid(pid) != process_group:
            marker_path.unlink(missing_ok=True)
            return False
        command = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        marker_path.unlink(missing_ok=True)
        return False
    if expected_main not in command:
        marker_path.unlink(missing_ok=True)
        return False

    _signal_process_group(process_group, signal.SIGTERM)
    deadline = time.monotonic() + max(grace_seconds, 0)
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            marker_path.unlink(missing_ok=True)
            return True
        time.sleep(0.05)
    _signal_process_group(process_group, signal.SIGKILL)
    marker_path.unlink(missing_ok=True)
    return True


def run_agent(
    task: TaskSpec,
    environment_url: str,
    artifact_dir: Path,
    model_name: str,
    cancel_event: Event | None = None,
) -> str:
    main_py = settings.computer_use_dir / "main.py"
    python_executable = validate_agent_configuration(
        model_name, check_dependencies=False
    )

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
    command = [
        python_executable,
        str(main_py),
        "--initial_url", start_url,
        "--model", model_name,
    ]
    environment = os.environ.copy()
    environment["GEMINI_API_KEY"] = settings.gemini_api_key
    environment.setdefault("PLAYWRIGHT_HEADLESS", "true")
    environment["ROLLOUT_ARTIFACT_DIR"] = str(artifact_dir)
    environment["ROLLOUT_REDACT_VALUES"] = settings.metabase_password
    environment["ROLLOUT_QUERY"] = query
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
    marker_path = artifact_dir / PROCESS_MARKER
    try:
        write_json(
            marker_path,
            {
                "pid": process.pid,
                "process_group": process.pid,
                "main_py": str(main_py),
            },
        )
    except Exception:
        _signal_process_group(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            _signal_process_group(process.pid, signal.SIGKILL)
            process.communicate()
        raise

    try:
        cancelled = False
        timed_out = False
        stdout: str | None = None
        stderr: str | None = None
        deadline = time.monotonic() + settings.rollout_timeout_seconds
        while True:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                _signal_process_group(process.pid, signal.SIGTERM)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _signal_process_group(process.pid, signal.SIGTERM)
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
                _signal_process_group(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
        transcript = _redact_secrets(
            (stdout or "") + ("\n[stderr]\n" + stderr if stderr else "")
        )
        (artifact_dir / "agent.log").write_text(transcript)
        trace_path = artifact_dir / "trace.jsonl"
        if trace_path.exists():
            final_event = None
            for line in reversed(trace_path.read_text().splitlines()):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "final":
                    final_event = event
                    break
            if final_event is not None:
                output = str(final_event.get("output") or "")
                submitted = submitted_answer_text(_redact_secrets(output))
                (artifact_dir / "final_output.txt").write_text(submitted)
        if cancelled:
            raise RolloutCancelled("Rollout cancelled from the UI.")
        if timed_out:
            raise TimeoutError(f"Agent exceeded {settings.rollout_timeout_seconds} seconds")
        if process.returncode:
            raise RuntimeError(f"Agent exited with status {process.returncode}; see agent.log")
        return transcript
    finally:
        if process.poll() is None:
            _signal_process_group(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_process_group(process.pid, signal.SIGKILL)
                process.communicate()
        marker_path.unlink(missing_ok=True)
