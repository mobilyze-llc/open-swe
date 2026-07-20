from __future__ import annotations

import grp
import http.client
import json
import pwd
import re
import shlex
import shutil
import socket
import ssl
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.mobilyze.orchard_baseline.constants import (
    ACCOUNT_GID,
    ACCOUNT_HOME,
    ACCOUNT_NAME,
    ACCOUNT_SHELL,
    ACCOUNT_UID,
    ADMIN_TOKEN,
    CONTROLLER_LABEL,
    CURRENT_ORCHARD_BINARY,
    CURRENT_TART_BINARY,
    DEFAULT_CPU,
    DEFAULT_DISK_GIB,
    DEFAULT_MEMORY_MIB,
    LISTENER_HOST,
    LISTENER_PORT,
    ORCHARD_SHA256,
    SECRETS_ROOT,
    SUPPORT_ROOT,
    TART_SHA256,
    VM_SLOTS,
    WORKER_LABEL,
    WORKER_SLOT_RESOURCE,
    WORKER_TOKEN,
)
from agent.mobilyze.orchard_baseline.host import command_error, run, sha256


@dataclass(frozen=True)
class Diagnostic:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _capture(name: str, probe: Callable[[], str], *, required: bool = True) -> Diagnostic:
    try:
        return Diagnostic(name, True, probe(), required)
    except Exception as exc:
        return Diagnostic(name, False, str(exc), required)


def _identity() -> str:
    user = pwd.getpwnam(ACCOUNT_NAME)
    group = grp.getgrnam(ACCOUNT_NAME)
    if user.pw_uid != ACCOUNT_UID or user.pw_gid != ACCOUNT_GID or group.gr_gid != ACCOUNT_GID:
        raise RuntimeError(f"expected UID/GID {ACCOUNT_UID}")
    if user.pw_dir != str(ACCOUNT_HOME) or user.pw_shell != ACCOUNT_SHELL:
        raise RuntimeError(f"expected home {ACCOUNT_HOME} and shell {ACCOUNT_SHELL}")
    return f"uid={user.pw_uid} gid={user.pw_gid} home={user.pw_dir} shell={user.pw_shell}"


def _protected_path(path: Path, expected_mode: int, *, directory: bool) -> str:
    metadata = path.lstat()
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"protected path is not a directory: {path}")
    elif not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"protected path is not a regular file: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode != expected_mode:
        raise RuntimeError(f"{path} mode is {mode:04o}, expected {expected_mode:04o}")
    owner = pwd.getpwuid(metadata.st_uid).pw_name
    group = grp.getgrgid(metadata.st_gid).gr_name
    if (
        metadata.st_uid != ACCOUNT_UID
        or metadata.st_gid != ACCOUNT_GID
        or owner != ACCOUNT_NAME
        or group != ACCOUNT_NAME
    ):
        raise RuntimeError(
            f"{path} owner/group is {owner}:{group} "
            f"({metadata.st_uid}:{metadata.st_gid}), expected {ACCOUNT_NAME}:{ACCOUNT_NAME}"
        )
    return f"path={path} mode={mode:04o} owner={owner} group={group}"


def _hash(path: Path, expected: str) -> str:
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"{path} SHA-256 is {actual}, expected {expected}")
    return actual


def _service(label: str) -> str:
    result = run(["/bin/launchctl", "print", f"system/{label}"], check=False)
    if result.returncode != 0:
        raise RuntimeError(command_error(result))
    if "state = running" not in result.stdout:
        raise RuntimeError(f"{label} is loaded but not running")
    return "running"


def _listener() -> str:
    with socket.create_connection((LISTENER_HOST, LISTENER_PORT), timeout=3):
        return f"{LISTENER_HOST}:{LISTENER_PORT} reachable"


def _authentication_boundary() -> str:
    context = ssl._create_unverified_context()
    connection = http.client.HTTPSConnection(
        LISTENER_HOST, LISTENER_PORT, timeout=3, context=context
    )
    try:
        connection.request("GET", "/v1/controller/info")
        response = connection.getresponse()
        response.read()
        status = response.status
    finally:
        connection.close()
    if status != 401:
        raise RuntimeError(f"unauthenticated /v1/controller/info returned {status}")
    return "unauthenticated /v1/controller/info rejected with 401"


def _worker_limits() -> str:
    job = run(["/bin/launchctl", "print", f"system/{WORKER_LABEL}"], check=False)
    if job.returncode != 0:
        raise RuntimeError(command_error(job))
    match = re.search(r"^\s*pid = (\d+)$", job.stdout, re.MULTILINE)
    if match is None:
        raise RuntimeError("worker launchd PID not found")
    result = run(["/bin/ps", "-p", match.group(1), "-o", "command="], check=False)
    if result.returncode != 0:
        raise RuntimeError(command_error(result))
    command = result.stdout.strip()
    arguments = shlex.split(command)
    if "worker" not in arguments or "run" not in arguments:
        raise RuntimeError("managed worker process command not found")

    def values(option: str) -> list[str]:
        found: list[str] = []
        for index, argument in enumerate(arguments):
            if argument == option and index + 1 < len(arguments):
                found.append(arguments[index + 1])
            elif argument.startswith(f"{option}="):
                found.append(argument.partition("=")[2])
        return found

    expected = {
        "--resources": WORKER_SLOT_RESOURCE,
        "--default-cpu": str(DEFAULT_CPU),
        "--default-memory": str(DEFAULT_MEMORY_MIB),
    }
    missing = [
        f"{option} {value}" for option, value in expected.items() if values(option) != [value]
    ]
    if missing:
        raise RuntimeError(f"worker command is missing {', '.join(missing)}")
    return f"slots={VM_SLOTS} cpu={DEFAULT_CPU} memory_mib={DEFAULT_MEMORY_MIB}"


def _disk_report() -> str:
    usage = shutil.disk_usage("/")
    gib = 1024**3
    return f"capacity_gib={usage.total // gib} available_gib={usage.free // gib} threshold=none"


def collect_diagnostics() -> list[Diagnostic]:
    return [
        _capture("identity", _identity),
        _capture("support_root", lambda: _protected_path(SUPPORT_ROOT, 0o700, directory=True)),
        _capture("secrets_root", lambda: _protected_path(SECRETS_ROOT, 0o700, directory=True)),
        _capture(
            "bootstrap_admin_secret",
            lambda: _protected_path(ADMIN_TOKEN, 0o400, directory=False),
        ),
        _capture(
            "worker_bootstrap_secret",
            lambda: _protected_path(WORKER_TOKEN, 0o400, directory=False),
        ),
        _capture("orchard_sha256", lambda: _hash(CURRENT_ORCHARD_BINARY, ORCHARD_SHA256)),
        _capture("tart_sha256", lambda: _hash(CURRENT_TART_BINARY, TART_SHA256)),
        _capture("controller_service", lambda: _service(CONTROLLER_LABEL)),
        _capture("worker_service", lambda: _service(WORKER_LABEL)),
        _capture("listener", _listener),
        _capture("authentication", _authentication_boundary),
        _capture("worker_limits", _worker_limits),
        Diagnostic(
            "vm_caller_default",
            True,
            f"cpu={DEFAULT_CPU} memory_mib={DEFAULT_MEMORY_MIB} disk_gib={DEFAULT_DISK_GIB}",
            required=False,
        ),
        _capture("disk_report", _disk_report, required=False),
    ]


def render_status(diagnostics: list[Diagnostic]) -> tuple[str, int]:
    healthy = all(item.ok for item in diagnostics if item.required)
    payload = {
        "status": "healthy" if healthy else "degraded",
        "diagnostics": [asdict(item) for item in diagnostics],
    }
    return json.dumps(payload, indent=2, sort_keys=True), 0 if healthy else 1


def main() -> int:
    output, exit_code = render_status(collect_diagnostics())
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
