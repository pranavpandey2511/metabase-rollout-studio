from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_COMPUTER_USE_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-computer-use-preview-10-2025",
)


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without adding a runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _csv_setting(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(value.strip() for value in os.environ.get(name, "").split(",") if value.strip())
    return values or default


def _bool_setting(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    runs_dir: Path = ROOT / "runs"
    computer_use_dir: Path = ROOT / os.environ.get("COMPUTER_USE_DIR", "work/computer-use-preview")
    computer_use_python: str = os.environ.get("COMPUTER_USE_PYTHON", "python3")
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    computer_use_models: tuple[str, ...] = _csv_setting(
        "COMPUTER_USE_MODELS", DEFAULT_COMPUTER_USE_MODELS
    )
    default_computer_use_model: str = os.environ.get(
        "DEFAULT_COMPUTER_USE_MODEL", DEFAULT_COMPUTER_USE_MODELS[0]
    ).strip()
    metabase_email: str = os.environ.get("METABASE_EMAIL", "daksh@deeptune.com")
    metabase_password: str = os.environ.get("METABASE_PASSWORD", "")
    metabase_urls: tuple[str, ...] = tuple(
        url.strip().rstrip("/")
        for url in os.environ.get("METABASE_URLS", "http://localhost:33000").split(",")
        if url.strip()
    )
    max_parallel_rollouts: int = int(os.environ.get("MAX_PARALLEL_ROLLOUTS", "1"))
    max_attempts_per_problem: int = int(os.environ.get("MAX_ATTEMPTS_PER_PROBLEM", "10"))
    max_rollouts_per_evaluation: int = int(
        os.environ.get("MAX_ROLLOUTS_PER_EVALUATION", "100")
    )
    max_task_file_bytes: int = int(os.environ.get("MAX_TASK_FILE_BYTES", "2000000"))
    rollout_timeout_seconds: int = int(os.environ.get("ROLLOUT_TIMEOUT_SECONDS", "600"))
    auto_start_environment: bool = _bool_setting("AUTO_START_ENVIRONMENT", True)
    environment_start_script: Path = ROOT / os.environ.get(
        "ENVIRONMENT_START_SCRIPT", "scripts/ensure_environment.sh"
    )
    environment_start_timeout_seconds: int = int(
        os.environ.get("ENVIRONMENT_START_TIMEOUT_SECONDS", "180")
    )
    environment_health_timeout_seconds: float = float(
        os.environ.get("ENVIRONMENT_HEALTH_TIMEOUT_SECONDS", "5")
    )
    shutdown_grace_seconds: float = float(os.environ.get("SHUTDOWN_GRACE_SECONDS", "15"))

    def __post_init__(self) -> None:
        if self.default_computer_use_model not in self.computer_use_models:
            raise ValueError("DEFAULT_COMPUTER_USE_MODEL must be listed in COMPUTER_USE_MODELS")
        if self.max_attempts_per_problem < 1:
            raise ValueError("MAX_ATTEMPTS_PER_PROBLEM must be at least 1")
        if self.max_rollouts_per_evaluation < 1:
            raise ValueError("MAX_ROLLOUTS_PER_EVALUATION must be at least 1")
        if self.max_parallel_rollouts < 1:
            raise ValueError("MAX_PARALLEL_ROLLOUTS must be at least 1")
        if self.max_task_file_bytes < 1:
            raise ValueError("MAX_TASK_FILE_BYTES must be at least 1")
        if not self.metabase_urls:
            raise ValueError("METABASE_URLS must include at least one URL")
        effective_capacity = min(self.max_parallel_rollouts, len(self.metabase_urls))
        if effective_capacity > 2:
            raise ValueError(
                "Local evaluation capacity is limited to two Metabase environments"
            )
        if self.rollout_timeout_seconds < 1:
            raise ValueError("ROLLOUT_TIMEOUT_SECONDS must be at least 1")
        if self.environment_start_timeout_seconds < 1:
            raise ValueError("ENVIRONMENT_START_TIMEOUT_SECONDS must be at least 1")
        if self.environment_health_timeout_seconds <= 0:
            raise ValueError("ENVIRONMENT_HEALTH_TIMEOUT_SECONDS must be positive")
        if self.shutdown_grace_seconds < 0:
            raise ValueError("SHUTDOWN_GRACE_SECONDS cannot be negative")


settings = Settings()
settings.runs_dir.mkdir(parents=True, exist_ok=True)
