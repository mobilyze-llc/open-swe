from __future__ import annotations

import plistlib

from agent.mobilyze.orchard_baseline.constants import (
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
    WORKER_LABEL,
    WORKER_LABEL_VALUE,
    WORKER_LOG,
    WORKER_NAME,
    WORKER_PLIST,
    WORKER_SLOT_RESOURCE,
    WORKER_TOKEN,
    WORKER_WRAPPER,
)


def controller_wrapper() -> str:
    return f"""#!/bin/sh
set -eu
ORCHARD_BOOTSTRAP_ADMIN_TOKEN="$(/bin/cat {ADMIN_TOKEN})"
export ORCHARD_BOOTSTRAP_ADMIN_TOKEN
exec {CURRENT_ORCHARD_BINARY} controller run --data-dir {CONTROLLER_DATA} --listen {LISTENER_HOST}:{LISTENER_PORT}
"""


def worker_wrapper() -> str:
    return f"""#!/bin/sh
set -eu
export PATH={CURRENT_BIN}:/usr/bin:/bin:/usr/sbin:/sbin
exec {CURRENT_ORCHARD_BINARY} worker run --bootstrap-token-stdin --no-pki --user {ACCOUNT_NAME} --name {WORKER_NAME} --labels {WORKER_LABEL_VALUE} --resources {WORKER_SLOT_RESOURCE} --default-cpu {DEFAULT_CPU} --default-memory {DEFAULT_MEMORY_MIB} {CONTROLLER_URL} < {WORKER_TOKEN}
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
