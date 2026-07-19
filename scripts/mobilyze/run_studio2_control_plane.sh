#!/bin/sh
set -eu

ROLE="${1:?role is required}"
DEPLOYMENT_ROOT=/opt/mobilyze/open-swe-control-plane
STATE_ROOT=/var/db/mobilyze-open-swe-control-plane
CONFIG_ROOT='/Library/Application Support/MobilyzeOpenSWEControlPlane'
ENV_FILE="$CONFIG_ROOT/env"

umask 077
set -a
. "$ENV_FILE"
set +a

export HOME="$STATE_ROOT/home"
export XDG_CACHE_HOME="$STATE_ROOT/cache"
export UV_CACHE_DIR="$STATE_ROOT/cache/uv"
export GIT_CONFIG_GLOBAL="$STATE_ROOT/gitconfig"
export GIT_CONFIG_SYSTEM=/dev/null
cd "$STATE_ROOT"

case "$ROLE" in
  backend)
    exec "$DEPLOYMENT_ROOT/current/.venv/bin/langgraph" dev \
      --config "$DEPLOYMENT_ROOT/current/langgraph.json" \
      --host 127.0.0.1 --port 2029 --no-browser --no-reload
    ;;
  dashboard)
    cd "$DEPLOYMENT_ROOT/current/ui"
    exec /opt/homebrew/bin/node node_modules/vite/bin/vite.js preview \
      --host 127.0.0.1 --port 3029 --strictPort
    ;;
  *)
    echo "unknown role: $ROLE" >&2
    exit 64
    ;;
esac
