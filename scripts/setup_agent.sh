#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
UPSTREAM_URL=https://github.com/google-gemini/computer-use-preview.git
UPSTREAM_REVISION=77c9797e943aad63bbc963b7fd092a9e51c07863
AGENT_DIR=${COMPUTER_USE_DIR:-$PROJECT_ROOT/work/computer-use-preview}
PATCH_FILE=$PROJECT_ROOT/patches/computer-use-preview.patch

case "$AGENT_DIR" in
  /*) ;;
  *) AGENT_DIR=$PROJECT_ROOT/$AGENT_DIR ;;
esac

if [ ! -d "$AGENT_DIR/.git" ]; then
  if [ -e "$AGENT_DIR" ]; then
    echo "Agent path exists but is not a Git checkout: $AGENT_DIR" >&2
    exit 1
  fi
  mkdir -p "$(dirname -- "$AGENT_DIR")"
  git clone --no-checkout "$UPSTREAM_URL" "$AGENT_DIR"
  git -C "$AGENT_DIR" checkout --detach "$UPSTREAM_REVISION"
fi

current_revision=$(git -C "$AGENT_DIR" rev-parse HEAD)
if [ "$current_revision" != "$UPSTREAM_REVISION" ]; then
  echo "Expected computer-use-preview revision $UPSTREAM_REVISION, found $current_revision" >&2
  exit 1
fi

if git -C "$AGENT_DIR" apply --reverse --check "$PATCH_FILE" >/dev/null 2>&1; then
  echo "Computer-use adapter is already configured."
  exit 0
fi

if ! git -C "$AGENT_DIR" diff --quiet || ! git -C "$AGENT_DIR" diff --cached --quiet; then
  echo "Computer-use checkout has unrelated changes; refusing to overwrite them." >&2
  exit 1
fi

git -C "$AGENT_DIR" apply --check "$PATCH_FILE"
git -C "$AGENT_DIR" apply "$PATCH_FILE"
echo "Computer-use adapter configured at $AGENT_DIR"
