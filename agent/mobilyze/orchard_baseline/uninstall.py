from __future__ import annotations

import argparse
import grp
import pwd
import shutil

from agent.mobilyze.orchard_baseline.constants import (
    ACCOUNT_GID,
    ACCOUNT_NAME,
    ACCOUNT_UID,
    CONTROLLER_PLIST,
    CONTROLLER_WRAPPER,
    DATA_ROOT,
    INSTALL_ROOT,
    LOG_ROOT,
    SERVICE_LABELS,
    SUPPORT_ROOT,
    WORKER_PLIST,
    WORKER_WRAPPER,
)
from agent.mobilyze.orchard_baseline.host import require_root, run
from agent.mobilyze.orchard_baseline.services import loaded_labels, stop_services

MANAGED_PATHS = (
    CONTROLLER_PLIST,
    WORKER_PLIST,
    CONTROLLER_WRAPPER,
    WORKER_WRAPPER,
    SUPPORT_ROOT,
    DATA_ROOT,
    LOG_ROOT,
    INSTALL_ROOT,
)


def preview() -> str:
    lines = [
        *(str(path) for path in MANAGED_PATHS),
        f"account={ACCOUNT_NAME}:{ACCOUNT_UID}:{ACCOUNT_GID}",
    ]
    return "\n".join(lines)


def _validate_identity() -> None:
    try:
        user = pwd.getpwnam(ACCOUNT_NAME)
    except KeyError:
        user = None
    try:
        group = grp.getgrnam(ACCOUNT_NAME)
    except KeyError:
        group = None
    if user is not None and (user.pw_uid != ACCOUNT_UID or user.pw_gid != ACCOUNT_GID):
        raise RuntimeError(f"refusing to remove unexpected user {ACCOUNT_NAME}")
    if group is not None and group.gr_gid != ACCOUNT_GID:
        raise RuntimeError(f"refusing to remove unexpected group {ACCOUNT_NAME}")


def uninstall() -> None:
    require_root()
    _validate_identity()
    stop_services(loaded_labels(SERVICE_LABELS))
    for path in MANAGED_PATHS:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    try:
        pwd.getpwnam(ACCOUNT_NAME)
    except KeyError:
        pass
    else:
        run(["/usr/bin/dscl", ".", "-delete", f"/Users/{ACCOUNT_NAME}"])
    try:
        grp.getgrnam(ACCOUNT_NAME)
    except KeyError:
        pass
    else:
        run(["/usr/bin/dscl", ".", "-delete", f"/Groups/{ACCOUNT_NAME}"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Uninstall only the dedicated Orchard baseline")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm:
        print(preview())
        return 2
    uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
