#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -s data/metabase_envdata.sql ]; then
  echo "Missing data/metabase_envdata.sql" >&2
  exit 1
fi

if colima status >/dev/null 2>&1; then
  docker context use colima >/dev/null 2>&1 || true
  if ! docker info >/dev/null 2>&1; then
    echo "Colima's host Docker socket is stale; restarting Colima..."
    colima stop
    colima start --cpu 4 --memory 4 --disk 60
  fi
else
  colima start --cpu 4 --memory 4 --disk 60
fi
docker context use colima >/dev/null

attempt=0
until docker info >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "Colima started, but its Docker daemon did not become ready." >&2
    exit 1
  fi
  sleep 1
done

# This laptop is configured for one stable environment. Ensure an old pool2
# instance cannot consume memory after a previous experimental launch.
docker compose --profile pool2 stop metabase-2 db-2 >/dev/null 2>&1 || true
docker compose up -d --build

# Colima's automatic port forward has been unreliable on this machine. Run a
# separate supervised tunnel on 33000 so rollouts do not lose Metabase midway.
launchctl remove com.openai.codex.colima-metabase-tunnel >/dev/null 2>&1 || true
launchctl submit -l com.openai.codex.colima-metabase-tunnel -- \
  /usr/bin/ssh -F "$HOME/.colima/ssh_config" \
  -o ControlMaster=no -o ControlPath=none \
  -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
  -o TCPKeepAlive=yes -o ExitOnForwardFailure=yes \
  -N -L 127.0.0.1:33000:127.0.0.1:3000 colima

attempt=0
until curl --fail --silent http://127.0.0.1:33000/api/health | grep -q '"status":"ok"'; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "Metabase did not become healthy. Inspect with: docker compose logs metabase-1" >&2
    exit 1
  fi
  sleep 2
done

for pid in $(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); do
  command=$(ps -p "$pid" -o command= 2>/dev/null || true)
  case "$command" in
    *"uvicorn app.web:app"*)
      echo "Stopping stale rollout dashboard process $pid..."
      kill -TERM "$pid" 2>/dev/null || true
      ;;
    *)
      echo "Port 8000 is already used by another application: $command" >&2
      exit 1
      ;;
  esac
done

echo "Metabase is healthy at http://127.0.0.1:33000"
echo "Rollout Studio is starting at http://127.0.0.1:8000"
echo "Press Ctrl-C to stop the dashboard. Run ./scripts/stop_local.sh to stop everything."

.venv/bin/uvicorn app.web:app --host 127.0.0.1 --port 8000
