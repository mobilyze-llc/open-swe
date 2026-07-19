#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root" >&2
  exit 77
fi

COMMAND="${1:?command is required}"
shift
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
MANIFEST="$PROJECT_ROOT/config/mobilyze/studio2-control-plane.json"
IDENTITY=_openswectl
IDENTITY_ID=451
DEPLOYMENT_ROOT=/opt/mobilyze/open-swe-control-plane
STATE_ROOT=/var/db/mobilyze-open-swe-control-plane
LOG_ROOT=/var/log/mobilyze-open-swe-control-plane
CONFIG_ROOT='/Library/Application Support/MobilyzeOpenSWEControlPlane'
ENV_FILE="$CONFIG_ROOT/env"
BACKEND_LABEL=com.mobilyze.open-swe-control-plane.backend
DASHBOARD_LABEL=com.mobilyze.open-swe-control-plane.dashboard

manifest_value() {
  /usr/bin/python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); print(value[sys.argv[2]][sys.argv[3]])' \
    "$MANIFEST" "$1" "$2"
}

service_plist() {
  echo "/Library/LaunchDaemons/$1.plist"
}

bootstrap_identity() {
  if dscl . -read "/Users/$IDENTITY" >/dev/null 2>&1; then
    [ "$(dscl . -read "/Users/$IDENTITY" UniqueID | awk '{print $2}')" = "$IDENTITY_ID" ] || {
      echo "$IDENTITY exists with an unexpected UID" >&2
      exit 78
    }
  else
    if dscl . -search /Users UniqueID "$IDENTITY_ID" | grep -q .; then
      echo "UID $IDENTITY_ID is already allocated" >&2
      exit 78
    fi
    if ! dscl . -read "/Groups/$IDENTITY" >/dev/null 2>&1; then
      dscl . -create "/Groups/$IDENTITY"
      dscl . -create "/Groups/$IDENTITY" PrimaryGroupID "$IDENTITY_ID"
      dscl . -create "/Groups/$IDENTITY" RealName "Mobilyze Open SWE Control Plane"
      dscl . -create "/Groups/$IDENTITY" Password '*'
    fi
    dscl . -create "/Users/$IDENTITY"
    dscl . -create "/Users/$IDENTITY" UniqueID "$IDENTITY_ID"
    dscl . -create "/Users/$IDENTITY" PrimaryGroupID "$IDENTITY_ID"
    dscl . -create "/Users/$IDENTITY" RealName "Mobilyze Open SWE Control Plane"
    dscl . -create "/Users/$IDENTITY" NFSHomeDirectory /var/empty
    dscl . -create "/Users/$IDENTITY" UserShell /usr/bin/false
    dscl . -create "/Users/$IDENTITY" IsHidden 1
    dscl . -create "/Users/$IDENTITY" AuthenticationAuthority ';DisabledUser;'
    dscl . -create "/Users/$IDENTITY" Password '*'
  fi

  /usr/bin/install -d -o root -g wheel -m 0755 "$DEPLOYMENT_ROOT" "$DEPLOYMENT_ROOT/releases"
  /usr/bin/install -d -o "$IDENTITY" -g "$IDENTITY" -m 0700 \
    "$STATE_ROOT" "$STATE_ROOT/home" "$STATE_ROOT/cache" "$STATE_ROOT/sandboxes"
  /usr/bin/install -d -o "$IDENTITY" -g "$IDENTITY" -m 0750 "$LOG_ROOT"
  /usr/bin/install -d -o root -g "$IDENTITY" -m 0750 "$CONFIG_ROOT"
}

install_release() {
  archive="${1:?release archive is required}"
  sha="${2:?release SHA is required}"
  expected_sha=$(manifest_value application commit)
  [ "$sha" = "$expected_sha" ] || {
    echo "release SHA $sha does not match manifest $expected_sha" >&2
    exit 65
  }
  [ -f "$archive" ] || {
    echo "release archive not found: $archive" >&2
    exit 66
  }
  release="$DEPLOYMENT_ROOT/releases/$sha"
  [ ! -e "$release" ] || {
    echo "release already exists: $release" >&2
    exit 73
  }
  /usr/bin/install -d -o root -g wheel -m 0755 "$release"
  /usr/bin/tar -xzf "$archive" -C "$release"

  actual_uv=$(shasum -a 256 "$release/uv.lock" | awk '{print $1}')
  actual_ui=$(shasum -a 256 "$release/ui/pnpm-lock.yaml" | awk '{print $1}')
  expected_uv=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["application"]["dependency_locks"]["uv.lock"])' "$MANIFEST")
  expected_ui=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["application"]["dependency_locks"]["ui/pnpm-lock.yaml"])' "$MANIFEST")
  [ "$actual_uv" = "$expected_uv" ] && [ "$actual_ui" = "$expected_ui" ] || {
    echo "dependency lock hash mismatch" >&2
    exit 65
  }

  env HOME="$STATE_ROOT/home" UV_CACHE_DIR="$STATE_ROOT/cache/uv" \
    /opt/homebrew/bin/uv sync --directory "$release" --frozen --no-dev --python 3.12
  (
    cd "$release/ui"
    /opt/homebrew/bin/pnpm install --frozen-lockfile
    env VITE_DASHBOARD_API_BASE_URL= /opt/homebrew/bin/pnpm run build
  )
  chmod -R go-w "$release"
  /usr/bin/install -o root -g "$IDENTITY" -m 0755 \
    "$PROJECT_ROOT/scripts/mobilyze/run_studio2_control_plane.sh" "$CONFIG_ROOT/run"
  ln -sfn "$release" "$DEPLOYMENT_ROOT/current"
}

install_services() {
  temp_dir=$(mktemp -d /tmp/open-swe-control-plane.XXXXXX)
  trap 'rm -rf "$temp_dir"' EXIT INT TERM
  /usr/bin/python3 "$PROJECT_ROOT/scripts/mobilyze/studio2_control_plane.py" \
    --manifest "$MANIFEST" render-launchd --output-dir "$temp_dir"
  for label in "$BACKEND_LABEL" "$DASHBOARD_LABEL"; do
    /usr/bin/install -o root -g wheel -m 0644 "$temp_dir/$label.plist" "$(service_plist "$label")"
  done
  if [ ! -e "$ENV_FILE" ]; then
    /usr/bin/python3 "$PROJECT_ROOT/scripts/mobilyze/studio2_control_plane.py" \
      --manifest "$MANIFEST" render-env-template --output "$ENV_FILE"
    chown root:"$IDENTITY" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
  fi
}

start_services() {
  for label in "$BACKEND_LABEL" "$DASHBOARD_LABEL"; do
    plist=$(service_plist "$label")
    launchctl print "system/$label" >/dev/null 2>&1 || launchctl bootstrap system "$plist"
    launchctl enable "system/$label"
    launchctl kickstart -k "system/$label"
  done
}

stop_services() {
  for label in "$DASHBOARD_LABEL" "$BACKEND_LABEL"; do
    launchctl bootout "system/$label" >/dev/null 2>&1 || true
  done
}

case "$COMMAND" in
  bootstrap)
    bootstrap_identity
    install_services
    ;;
  install)
    bootstrap_identity
    install_release "$@"
    install_services
    ;;
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  restart)
    for label in "$BACKEND_LABEL" "$DASHBOARD_LABEL"; do
      launchctl kickstart -k "system/$label"
    done
    ;;
  status)
    for label in "$BACKEND_LABEL" "$DASHBOARD_LABEL"; do
      launchctl print "system/$label" | sed -n '1,45p'
    done
    ;;
  rollback)
    sha="${1:?rollback SHA is required}"
    release="$DEPLOYMENT_ROOT/releases/$sha"
    [ -d "$release" ] || {
      echo "rollback release not found: $release" >&2
      exit 66
    }
    stop_services
    ln -sfn "$release" "$DEPLOYMENT_ROOT/current"
    start_services
    ;;
  *)
    echo "unknown command: $COMMAND" >&2
    exit 64
    ;;
esac
