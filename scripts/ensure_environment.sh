#!/bin/sh
set -efu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT"

COLIMA_CPU=${COLIMA_CPU:-4}
COLIMA_MEMORY_GB=${COLIMA_MEMORY_GB:-4}
COLIMA_DISK_GB=${COLIMA_DISK_GB:-60}
REQUIRED_ENVIRONMENT_COUNT=${REQUIRED_ENVIRONMENT_COUNT:-1}
METABASE_URLS=${METABASE_URLS:-http://localhost:33000}
ENVIRONMENT_HEALTH_WAIT_SECONDS=${ENVIRONMENT_HEALTH_WAIT_SECONDS:-120}
TUNNEL_REPAIR_WAIT_SECONDS=${TUNNEL_REPAIR_WAIT_SECONDS:-5}
TUNNEL_LABEL_1=com.openai.rollout-studio.metabase-1
TUNNEL_LABEL_2=com.openai.rollout-studio.metabase-2

case "$REQUIRED_ENVIRONMENT_COUNT" in
  1|2) ;;
  *) echo "Local auto-start supports one or two Metabase environments." >&2; exit 1 ;;
esac
case "$ENVIRONMENT_HEALTH_WAIT_SECONDS" in
  ''|*[!0-9]*)
    echo "Environment health waits must be whole seconds." >&2
    exit 1
    ;;
esac
case "$TUNNEL_REPAIR_WAIT_SECONDS" in
  ''|*[!0-9]*)
    echo "Environment health waits must be whole seconds." >&2
    exit 1
    ;;
esac
if [ "$ENVIRONMENT_HEALTH_WAIT_SECONDS" -lt 1 ]; then
  echo "ENVIRONMENT_HEALTH_WAIT_SECONDS must be at least 1." >&2
  exit 1
fi

old_ifs=$IFS
IFS=,
set -- $METABASE_URLS
IFS=$old_ifs
if [ "$#" -lt "$REQUIRED_ENVIRONMENT_COUNT" ]; then
  echo "METABASE_URLS does not provide ${REQUIRED_ENVIRONMENT_COUNT} local URLs." >&2
  exit 1
fi

port_from_url() {
  url=${1%/}
  case "$url" in
    http://localhost:*|http://127.0.0.1:*) port=${url##*:} ;;
    *)
      echo "Automatic startup requires local URLs such as http://localhost:33000." >&2
      return 1
      ;;
  esac
  case "$port" in
    ''|*[!0-9]*)
      echo "Invalid local Metabase port in $1." >&2
      return 1
      ;;
  esac
  if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "Invalid local Metabase port in $1." >&2
    return 1
  fi
  printf '%s\n' "$port"
}

METABASE_PORT_1=$(port_from_url "$1")
METABASE_PORT_2=
if [ "$REQUIRED_ENVIRONMENT_COUNT" -eq 2 ]; then
  METABASE_PORT_2=$(port_from_url "$2")
  if [ "$METABASE_PORT_1" -eq "$METABASE_PORT_2" ]; then
    echo "Each Metabase environment must use a distinct local port." >&2
    exit 1
  fi
fi

health() {
  curl --fail --silent "http://127.0.0.1:$1/api/health" | grep -q '"status":"ok"'
}

all_healthy() {
  health "$METABASE_PORT_1" || return 1
  if [ "$REQUIRED_ENVIRONMENT_COUNT" -eq 2 ]; then
    health "$METABASE_PORT_2" || return 1
  fi
}

wait_for_health() {
  wait_seconds=$1
  elapsed=0
  while [ "$elapsed" -lt "$wait_seconds" ]; do
    all_healthy && return 0
    sleep 1
    elapsed=$((elapsed + 1))
  done
  all_healthy
}

submit_tunnels() {
  # Remove the previous development label once, then maintain one tunnel per slot.
  launchctl remove com.openai.codex.colima-metabase-tunnel >/dev/null 2>&1 || true
  launchctl remove "$TUNNEL_LABEL_1" >/dev/null 2>&1 || true
  launchctl submit -l "$TUNNEL_LABEL_1" -- \
    /usr/bin/ssh -F "$HOME/.colima/ssh_config" \
    -o ControlMaster=no -o ControlPath=none \
    -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
    -o TCPKeepAlive=yes -o ExitOnForwardFailure=yes \
    -N -L "127.0.0.1:${METABASE_PORT_1}:127.0.0.1:3000" colima

  launchctl remove "$TUNNEL_LABEL_2" >/dev/null 2>&1 || true
  if [ "$REQUIRED_ENVIRONMENT_COUNT" -eq 2 ]; then
    launchctl submit -l "$TUNNEL_LABEL_2" -- \
      /usr/bin/ssh -F "$HOME/.colima/ssh_config" \
      -o ControlMaster=no -o ControlPath=none \
      -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
      -o TCPKeepAlive=yes -o ExitOnForwardFailure=yes \
      -N -L "127.0.0.1:${METABASE_PORT_2}:127.0.0.1:3001" colima
  fi
}

cleanup_unused_slot() {
  [ "$REQUIRED_ENVIRONMENT_COUNT" -eq 1 ] || return 0
  launchctl remove "$TUNNEL_LABEL_2" >/dev/null 2>&1 || true
  if docker --context colima info >/dev/null 2>&1; then
    docker --context colima compose --profile pool2 stop metabase-2 db-2 \
      >/dev/null 2>&1 || true
  fi
}

cleanup_unused_slot
all_healthy && exit 0

if colima status >/dev/null 2>&1; then
  if ! docker --context colima info >/dev/null 2>&1; then
    echo "Repairing Colima because its Docker socket is unavailable..."
    colima stop
    colima start --cpu "$COLIMA_CPU" --memory "$COLIMA_MEMORY_GB" --disk "$COLIMA_DISK_GB"
  fi
else
  echo "Starting Colima..."
  colima start --cpu "$COLIMA_CPU" --memory "$COLIMA_MEMORY_GB" --disk "$COLIMA_DISK_GB"
fi

attempt=0
until docker --context colima info >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "Colima started, but Docker did not become ready." >&2
    exit 1
  fi
  sleep 1
done

cleanup_unused_slot

# A missing launchd tunnel is the common failure after a host restart. Repair it
# before touching Compose so a forwarding-only outage never invokes bootstrap.
submit_tunnels
if wait_for_health "$TUNNEL_REPAIR_WAIT_SECONDS"; then
  echo "Metabase tunnel repaired."
  exit 0
fi

# The bootstrap service is safe to invoke repeatedly: it only restores a fresh
# database and otherwise verifies the durable seed marker.
docker --context colima compose up -d db-1 bootstrap-1 metabase-1
if [ "$REQUIRED_ENVIRONMENT_COUNT" -eq 2 ]; then
  docker --context colima compose --profile pool2 up -d db-2 bootstrap-2 metabase-2
fi

submit_tunnels
if ! wait_for_health "$ENVIRONMENT_HEALTH_WAIT_SECONDS"; then
  echo "Metabase did not become healthy before the startup deadline." >&2
  exit 1
fi

echo "Metabase environment is ready."
