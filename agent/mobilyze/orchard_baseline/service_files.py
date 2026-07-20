from __future__ import annotations

import plistlib
import shlex
from pathlib import Path

from agent.mobilyze.orchard_baseline.constants import (
    ACCOUNT_HOME,
    ACCOUNT_NAME,
    ADMIN_TOKEN,
    CONTROLLER_DATA,
    CONTROLLER_LABEL,
    CONTROLLER_LOG,
    CONTROLLER_PLIST,
    CONTROLLER_URL,
    CONTROLLER_WRAPPER,
    CURRENT_BIN,
    CURRENT_ORCHARD_BINARY,
    DEFAULT_CPU,
    DEFAULT_MEMORY_MIB,
    LISTENER_HOST,
    LISTENER_PORT,
    ORCHARD_HOME,
    TAILSCALE_BINARY,
    WORKER_LABEL,
    WORKER_LABEL_VALUE,
    WORKER_LOG,
    WORKER_NAME,
    WORKER_PLIST,
    WORKER_SLOT_RESOURCE,
    WORKER_TOKEN,
    WORKER_WRAPPER,
)


def _shell(value: str | Path) -> str:
    return shlex.quote(str(value))


def _environment() -> str:
    return f"HOME={_shell(ACCOUNT_HOME)}\nORCHARD_HOME={_shell(ORCHARD_HOME)}\nexport HOME ORCHARD_HOME"


def controller_wrapper() -> str:
    tailscale = _shell(TAILSCALE_BINARY)
    return f"""#!/bin/sh
set -eu
{_environment()}
{tailscale} up
test "$({tailscale} ip -4)" = {_shell(LISTENER_HOST)}
ORCHARD_BOOTSTRAP_ADMIN_TOKEN="$(/bin/cat {_shell(ADMIN_TOKEN)})"
export ORCHARD_BOOTSTRAP_ADMIN_TOKEN
exec {_shell(CURRENT_ORCHARD_BINARY)} controller run --data-dir {_shell(CONTROLLER_DATA)} --listen {_shell(f"{LISTENER_HOST}:{LISTENER_PORT}")}
"""


def worker_wrapper() -> str:
    return f"""#!/bin/sh
set -eu
{_environment()}
export PATH={_shell(f"{CURRENT_BIN}:/usr/bin:/bin:/usr/sbin:/sbin")}
exec {_shell(CURRENT_ORCHARD_BINARY)} worker run --bootstrap-token-stdin --no-pki --user {_shell(ACCOUNT_NAME)} --name {_shell(WORKER_NAME)} --labels {_shell(WORKER_LABEL_VALUE)} --resources {_shell(WORKER_SLOT_RESOURCE)} --default-cpu {DEFAULT_CPU} --default-memory {DEFAULT_MEMORY_MIB} {_shell(CONTROLLER_URL)} < {_shell(WORKER_TOKEN)}
"""


def _plist(label: str, program: str, log_path: str, *, user_name: str | None = None) -> bytes:
    payload: dict[str, object] = {
        "Label": label,
        "ProgramArguments": [program],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }
    if user_name is not None:
        payload["UserName"] = user_name
        payload["GroupName"] = user_name
    return plistlib.dumps(payload, sort_keys=True)


def controller_plist() -> bytes:
    return _plist(
        CONTROLLER_LABEL, str(CONTROLLER_WRAPPER), str(CONTROLLER_LOG), user_name=ACCOUNT_NAME
    )


def worker_plist() -> bytes:
    return _plist(WORKER_LABEL, str(WORKER_WRAPPER), str(WORKER_LOG))


SERVICE_PLISTS = {
    CONTROLLER_LABEL: CONTROLLER_PLIST,
    WORKER_LABEL: WORKER_PLIST,
}
