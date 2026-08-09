#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

launchctl remove com.openai.codex.colima-metabase-tunnel >/dev/null 2>&1 || true

for pid in $(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true); do
  command=$(ps -p "$pid" -o command= 2>/dev/null || true)
  case "$command" in
    *"uvicorn app.web:app"*) kill -TERM "$pid" 2>/dev/null || true ;;
  esac
done

if colima status >/dev/null 2>&1; then
  docker context use colima >/dev/null 2>&1 || true
  if docker info >/dev/null 2>&1; then
    docker compose --profile pool2 down --remove-orphans
  else
    echo "Host Docker socket is unavailable; removing project containers inside Colima..."
    colima ssh -- docker rm -f \
      rl-environment-hackathon-india-mots-today-metabase-1-1 \
      rl-environment-hackathon-india-mots-today-bootstrap-1-1 \
      rl-environment-hackathon-india-mots-today-db-1-1 \
      rl-environment-hackathon-india-mots-today-metabase-2-1 \
      rl-environment-hackathon-india-mots-today-bootstrap-2-1 \
      rl-environment-hackathon-india-mots-today-db-2-1 >/dev/null 2>&1 || true
    colima ssh -- docker network rm \
      rl-environment-hackathon-india-mots-today_default >/dev/null 2>&1 || true
  fi
  colima stop
fi

echo "Rollout Studio, project containers, and Colima are stopped. Docker volumes were preserved."
