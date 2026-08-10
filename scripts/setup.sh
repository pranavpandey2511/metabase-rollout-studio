#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if [ ! -s data/metabase_envdata.sql ]; then
  echo "Missing data/metabase_envdata.sql" >&2
  exit 1
fi

./scripts/setup_agent.sh

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi

uv sync --locked
uv run --locked playwright install chromium

if grep -Eq '^(GEMINI_API_KEY|METABASE_PASSWORD)=$' .env; then
  echo "Setup complete. Add GEMINI_API_KEY and METABASE_PASSWORD to .env, then run ./scripts/run_local.sh"
else
  echo "Setup complete. Run ./scripts/run_local.sh"
fi
