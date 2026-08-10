#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"
. "$PROJECT_ROOT/scripts/docker_backend.sh"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install uv before running this script." >&2
  exit 1
fi
minimum_shutdown_wait=$(
  uv run --locked python -c \
    'import math; from app.config import settings; print(math.ceil(settings.shutdown_grace_seconds) + 5)'
)
DASHBOARD_SHUTDOWN_WAIT_SECONDS=${DASHBOARD_SHUTDOWN_WAIT_SECONDS:-$minimum_shutdown_wait}
case "$DASHBOARD_SHUTDOWN_WAIT_SECONDS" in
  ''|*[!0-9]*)
    echo "DASHBOARD_SHUTDOWN_WAIT_SECONDS must be a positive integer." >&2
    exit 1
    ;;
esac
if [ "$DASHBOARD_SHUTDOWN_WAIT_SECONDS" -lt "$minimum_shutdown_wait" ]; then
  DASHBOARD_SHUTDOWN_WAIT_SECONDS=$minimum_shutdown_wait
fi

for pid in $(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); do
  command=$(ps -p "$pid" -o command= 2>/dev/null || true)
  case "$command" in
    *"uvicorn app.web:app"*)
      kill -TERM "$pid" 2>/dev/null || true
      elapsed=0
      while kill -0 "$pid" 2>/dev/null; do
        [ "$elapsed" -ge "$DASHBOARD_SHUTDOWN_WAIT_SECONDS" ] && break
        sleep 1
        elapsed=$((elapsed + 1))
      done
      if kill -0 "$pid" 2>/dev/null; then
        echo "Rollout Studio did not stop within ${DASHBOARD_SHUTDOWN_WAIT_SECONDS}s; containers were left running." >&2
        exit 1
      fi
      ;;
  esac
done

uv run --locked python -c \
  'from app.jobs import job_manager; job_manager.reconcile_interrupted_jobs()'

launchctl remove com.openai.codex.colima-metabase-tunnel >/dev/null 2>&1 || true
launchctl remove com.openai.rollout-studio.metabase-1 >/dev/null 2>&1 || true
launchctl remove com.openai.rollout-studio.metabase-2 >/dev/null 2>&1 || true

if select_docker_backend no-start >/dev/null 2>&1; then
  docker_compose --profile pool2 down --remove-orphans
  if [ "$DOCKER_BACKEND_SELECTED" = colima ]; then
    colima stop
    echo "Rollout Studio, project containers, and Colima are stopped. Docker volumes were preserved."
    exit 0
  fi
fi

echo "Rollout Studio and project containers are stopped. Docker volumes were preserved."
