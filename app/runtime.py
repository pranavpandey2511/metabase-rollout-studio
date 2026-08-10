from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
from threading import Lock
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from .config import settings


class EnvironmentUnavailable(RuntimeError):
    pass


_process_lock = Lock()


def _is_healthy(url: str) -> bool:
    try:
        with urlopen(
            f"{url.rstrip('/')}/api/health",
            timeout=settings.environment_health_timeout_seconds,
        ) as response:
            body = json.loads(response.read())
            return (
                response.status == 200
                and isinstance(body, dict)
                and body.get("status") == "ok"
            )
    except (OSError, URLError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def _required_urls(parallelism: int) -> tuple[str, ...]:
    configured_capacity = min(settings.max_parallel_rollouts, len(settings.metabase_urls))
    if configured_capacity > 2:
        raise EnvironmentUnavailable(
            "Local auto-start supports at most two Metabase environments"
        )
    if parallelism < 1 or configured_capacity < 1:
        raise EnvironmentUnavailable("No Metabase environment is configured")
    if parallelism > configured_capacity:
        raise EnvironmentUnavailable(
            f"Requested parallelism {parallelism} exceeds configured capacity "
            f"{configured_capacity}"
        )
    return settings.metabase_urls[:parallelism]


def _local_tunnel_ports(urls: tuple[str, ...]) -> tuple[int, ...]:
    ports: list[int] = []
    for url in urls:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise EnvironmentUnavailable(f"Invalid Metabase URL: {url}") from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise EnvironmentUnavailable(
                "Automatic local Docker startup requires plain local Metabase URLs such as "
                "http://localhost:33000"
            )
        ports.append(port)
    if len(set(ports)) != len(ports):
        raise EnvironmentUnavailable("Each Metabase environment must use a distinct local port")
    return tuple(ports)


def _should_reconcile_unused_slot(
    urls: tuple[str, ...], healthy: bool, requested: bool
) -> bool:
    if (
        not requested
        or not healthy
        or not settings.auto_start_environment
        or len(urls) != 1
    ):
        return False
    try:
        _local_tunnel_ports(urls)
    except EnvironmentUnavailable:
        return False
    return True


@contextmanager
def _startup_lock():
    lock_path = settings.root / ".runtime-start.lock"
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def ensure_environment(
    parallelism: int, *, reconcile_unused_slot: bool = False
) -> None:
    """Ensure the requested local Metabase slots are reachable before a job starts."""
    urls = _required_urls(parallelism)
    healthy = all(_is_healthy(url) for url in urls)
    needs_cleanup = _should_reconcile_unused_slot(
        urls, healthy, reconcile_unused_slot
    )
    if healthy and not needs_cleanup:
        return
    if not settings.auto_start_environment:
        raise EnvironmentUnavailable(
            "Metabase is not ready and AUTO_START_ENVIRONMENT is disabled"
        )

    with _process_lock, _startup_lock():
        healthy = all(_is_healthy(url) for url in urls)
        needs_cleanup = _should_reconcile_unused_slot(
            urls, healthy, reconcile_unused_slot
        )
        if healthy and not needs_cleanup:
            return
        _local_tunnel_ports(urls)
        script = settings.environment_start_script
        if not script.is_file():
            raise EnvironmentUnavailable(f"Environment start script was not found: {script}")
        environment = os.environ.copy()
        environment.pop("GEMINI_API_KEY", None)
        environment.pop("METABASE_PASSWORD", None)
        environment["REQUIRED_ENVIRONMENT_COUNT"] = str(len(urls))
        environment["METABASE_URLS"] = ",".join(urls)
        try:
            result = subprocess.run(
                [str(script)],
                cwd=settings.root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=settings.environment_start_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise EnvironmentUnavailable(
                f"Local environment startup exceeded {settings.environment_start_timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise EnvironmentUnavailable(f"Could not start the local environment: {exc}") from exc
        if result.returncode:
            output = (result.stdout or "").strip().splitlines()
            detail = output[-1] if output else f"exit status {result.returncode}"
            raise EnvironmentUnavailable(f"Local environment startup failed: {detail}")
        unavailable = [url for url in urls if not _is_healthy(url)]
        if unavailable:
            raise EnvironmentUnavailable(
                "Metabase did not become healthy at " + ", ".join(unavailable)
            )


def main() -> None:
    capacity = min(settings.max_parallel_rollouts, len(settings.metabase_urls))
    ensure_environment(capacity, reconcile_unused_slot=True)
    print("Configured Metabase environment is healthy.")


if __name__ == "__main__":
    main()
