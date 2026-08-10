#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
AGENT_DIR=${COMPUTER_USE_DIR:-$PROJECT_ROOT/work/computer-use-preview}

case "$AGENT_DIR" in
  /*) ;;
  *) AGENT_DIR=$PROJECT_ROOT/$AGENT_DIR ;;
esac

for required_file in agent.py main.py requirements.txt computers/playwright/playwright.py; do
  if [ ! -f "$AGENT_DIR/$required_file" ]; then
    echo "Missing vendored Computer Use source: $AGENT_DIR/$required_file" >&2
    exit 1
  fi
done

echo "Vendored Computer Use source is ready at $AGENT_DIR"
