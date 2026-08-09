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
        for url in os.environ.get("METABASE_URLS", "http://localhost:3000").split(",")
        if url.strip()
    )
    max_parallel_rollouts: int = int(os.environ.get("MAX_PARALLEL_ROLLOUTS", "1"))
    max_attempts_per_problem: int = int(os.environ.get("MAX_ATTEMPTS_PER_PROBLEM", "10"))
    rollout_timeout_seconds: int = int(os.environ.get("ROLLOUT_TIMEOUT_SECONDS", "600"))

    def __post_init__(self) -> None:
        if self.default_computer_use_model not in self.computer_use_models:
            raise ValueError("DEFAULT_COMPUTER_USE_MODEL must be listed in COMPUTER_USE_MODELS")
        if self.max_attempts_per_problem < 1:
            raise ValueError("MAX_ATTEMPTS_PER_PROBLEM must be at least 1")


settings = Settings()
settings.runs_dir.mkdir(parents=True, exist_ok=True)
