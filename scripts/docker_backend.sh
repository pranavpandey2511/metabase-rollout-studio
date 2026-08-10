#!/bin/sh
# Shared local-Docker selection for the lifecycle scripts. Source this file;
# do not execute it directly.

docker_backend_error() {
  echo "$*" >&2
  return 1
}

docker_colima_ready() {
  docker --context colima info >/dev/null 2>&1
}

active_docker_is_local() {
  context_name=${1:-$(docker context show 2>/dev/null || true)}
  endpoint=$(docker context inspect --format '{{ .Endpoints.docker.Host }}' "$context_name" 2>/dev/null || true)
  case "$endpoint" in
    unix://*|npipe://*) return 0 ;;
    *) return 1 ;;
  esac
}

start_colima() {
  if ! command -v colima >/dev/null 2>&1; then
    docker_backend_error \
      "No local Docker engine is available. Start Docker Desktop or install Colima."
    return 1
  fi

  if colima status >/dev/null 2>&1; then
    echo "Repairing Colima because its Docker socket is unavailable..."
    colima stop
  else
    echo "Starting Colima..."
  fi
  colima start --cpu "$COLIMA_CPU" --memory "$COLIMA_MEMORY_GB" --disk "$COLIMA_DISK_GB"

  attempt=0
  until docker_colima_ready; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
      docker_backend_error "Colima started, but Docker did not become ready."
      return 1
    fi
    sleep 1
  done
}

select_docker_backend() {
  start_missing=${1:-start}
  requested=${DOCKER_BACKEND:-auto}

  if ! command -v docker >/dev/null 2>&1; then
    docker_backend_error "Docker CLI is required. Install Docker Desktop or the Docker CLI with Colima."
    return 1
  fi

  case "$requested" in
    docker)
      if ! docker info >/dev/null 2>&1; then
        docker_backend_error "DOCKER_BACKEND=docker requires a running local Docker engine. Start Docker Desktop."
        return 1
      fi
      if ! active_docker_is_local; then
        docker_backend_error "DOCKER_BACKEND=docker requires a local Unix-socket Docker context."
        return 1
      fi
      DOCKER_BACKEND_SELECTED=docker
      ;;
    colima)
      if ! docker_colima_ready; then
        if [ "$start_missing" = no-start ]; then
          return 1
        fi
        start_colima || return 1
      fi
      DOCKER_BACKEND_SELECTED=colima
      ;;
    auto)
      if docker info >/dev/null 2>&1; then
        active_context=$(docker context show 2>/dev/null || true)
        if [ "$active_context" = colima ]; then
          DOCKER_BACKEND_SELECTED=colima
        elif active_docker_is_local "$active_context"; then
          DOCKER_BACKEND_SELECTED=docker
        else
          docker_backend_error "The active Docker context is not local. Select Docker Desktop or set DOCKER_BACKEND=colima."
          return 1
        fi
      elif docker_colima_ready; then
        DOCKER_BACKEND_SELECTED=colima
      elif [ "$start_missing" = no-start ]; then
        return 1
      else
        start_colima || return 1
        DOCKER_BACKEND_SELECTED=colima
      fi
      ;;
    *)
      docker_backend_error "DOCKER_BACKEND must be auto, docker, or colima."
      return 1
      ;;
  esac
  export DOCKER_BACKEND_SELECTED
  echo "Using Docker backend: $DOCKER_BACKEND_SELECTED"
}

docker_engine() {
  if [ "${DOCKER_BACKEND_SELECTED:-}" = colima ]; then
    docker --context colima "$@"
  else
    docker "$@"
  fi
}

docker_compose() {
  docker_engine compose "$@"
}
