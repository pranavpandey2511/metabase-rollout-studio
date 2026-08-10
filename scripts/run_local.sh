#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

if curl --fail --silent http://127.0.0.1:8000/api/config | grep -q '"computer_use_models"'; then
  echo "Rollout Studio is already running at http://127.0.0.1:8000"
  exit 0
fi

for pid in $(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); do
  command=$(ps -p "$pid" -o command= 2>/dev/null || true)
  case "$command" in
    *"uvicorn app.web:app"*)
      echo "Rollout Studio owns port 8000, but its health endpoint is unavailable." >&2
      echo "Run ./scripts/stop_local.sh, then retry startup." >&2
      exit 1
      ;;
    *)
      echo "Port 8000 is already used by another application: $command" >&2
      exit 1
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Run ./scripts/setup.sh after installing uv." >&2
  exit 1
fi

if [ ! -f uv.lock ]; then
  echo "Missing uv.lock. Run ./scripts/setup.sh." >&2
  exit 1
fi

if [ ! -s data/metabase_envdata.sql ]; then
  echo "Missing data/metabase_envdata.sql" >&2
  exit 1
fi

uv run --locked python -m app.runtime

echo "Rollout Studio is starting at http://127.0.0.1:8000"
echo "Press Ctrl-C to stop the dashboard. Run ./scripts/stop_local.sh to stop everything."

exec uv run --locked uvicorn app.web:app --host 127.0.0.1 --port 8000
