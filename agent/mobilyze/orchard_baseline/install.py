from __future__ import annotations

import argparse
import grp
import os
import pwd
import stat
from pathlib import Path

from agent.mobilyze.orchard_baseline.constants import (
    ACCOUNT_GID,
    ACCOUNT_HOME,
    ACCOUNT_NAME,
    ACCOUNT_SHELL,
    ACCOUNT_UID,
    ADMIN_TOKEN,
    CONTROLLER_PLIST,
    CONTROLLER_WRAPPER,
    CURRENT_BIN,
    CURRENT_ORCHARD,
    CURRENT_ORCHARD_BINARY,
    CURRENT_TART,
    CURRENT_TART_APP,
    CURRENT_TART_BINARY,
    DATA_ROOT,
    INSTALL_ROOT,
    LOG_ROOT,
    ORCHARD_BINARY,
    ORCHARD_RELEASE,
    ORCHARD_SHA256,
    PREVIOUS_ORCHARD,
    PREVIOUS_TART,
    RELEASES_ROOT,
    SECRETS_ROOT,
    SERVICE_LABELS,
    SUPPORT_ROOT,
    TART_APP,
    TART_APP_EXECUTABLE_RELATIVE,
    TART_EXECUTABLE_RELATIVE,
    TART_RELEASE,
    TART_SHA256,
    WORKER_PLIST,
    WORKER_TOKEN,
    WORKER_WRAPPER,
)
from agent.mobilyze.orchard_baseline.host import (
    atomic_symlink,
    atomic_write,
    chown,
    install_pinned_file,
    install_pinned_tree,
    require_root,
    run,
    same_path,
)
from agent.mobilyze.orchard_baseline.service_files import (
    controller_plist,
    controller_wrapper,
    worker_plist,
    worker_wrapper,
)
from agent.mobilyze.orchard_baseline.services import (
    loaded_labels,
    start_services,
    stop_services,
)


def _ensure_identity() -> None:
    try:
        group = grp.getgrnam(ACCOUNT_NAME)
    except KeyError:
        try:
            collision_group = grp.getgrgid(ACCOUNT_GID)
        except KeyError:
            collision_group = None
        if collision_group is not None:
            raise RuntimeError(f"GID {ACCOUNT_GID} belongs to {collision_group.gr_name}") from None
        run(["/usr/bin/dscl", ".", "-create", f"/Groups/{ACCOUNT_NAME}"])
        run(
            [
                "/usr/bin/dscl",
                ".",
                "-create",
                f"/Groups/{ACCOUNT_NAME}",
                "PrimaryGroupID",
                str(ACCOUNT_GID),
            ]
        )
        group = grp.getgrnam(ACCOUNT_NAME)
    if group.gr_gid != ACCOUNT_GID:
        raise RuntimeError(f"{ACCOUNT_NAME} must use GID {ACCOUNT_GID}")

    try:
        user = pwd.getpwnam(ACCOUNT_NAME)
    except KeyError:
        try:
            collision = pwd.getpwuid(ACCOUNT_UID)
        except KeyError:
            collision = None
        if collision is not None:
            raise RuntimeError(f"UID {ACCOUNT_UID} belongs to {collision.pw_name}") from None
        run(["/usr/bin/dscl", ".", "-create", f"/Users/{ACCOUNT_NAME}"])
        for key, value in (
            ("UniqueID", str(ACCOUNT_UID)),
            ("PrimaryGroupID", str(ACCOUNT_GID)),
            ("NFSHomeDirectory", str(ACCOUNT_HOME)),
            ("UserShell", ACCOUNT_SHELL),
        ):
            run(["/usr/bin/dscl", ".", "-create", f"/Users/{ACCOUNT_NAME}", key, value])
        user = pwd.getpwnam(ACCOUNT_NAME)
    if user.pw_uid != ACCOUNT_UID or user.pw_gid != ACCOUNT_GID:
        raise RuntimeError(f"{ACCOUNT_NAME} must use UID/GID {ACCOUNT_UID}")
    if user.pw_dir != str(ACCOUNT_HOME) or user.pw_shell != ACCOUNT_SHELL:
        raise RuntimeError(f"{ACCOUNT_NAME} must use home {ACCOUNT_HOME} and shell {ACCOUNT_SHELL}")


def _prepare_directory(path: Path, mode: int, *, owned: bool) -> None:
    if path.is_symlink():
        raise RuntimeError(f"managed directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError(f"managed directory is not a directory: {path}")
    os.chmod(path, mode)
    if owned:
        chown(path, ACCOUNT_UID, ACCOUNT_GID)


def _prepare_directories() -> None:
    for path, mode, owned in (
        (INSTALL_ROOT, 0o755, False),
        (RELEASES_ROOT, 0o755, False),
        (CURRENT_BIN, 0o755, False),
        (DATA_ROOT, 0o700, True),
        (SUPPORT_ROOT, 0o700, True),
        (SECRETS_ROOT, 0o700, True),
        (LOG_ROOT, 0o700, True),
    ):
        _prepare_directory(path, mode, owned=owned)


def _install_secret(source: Path | None, destination: Path) -> None:
    if destination.is_symlink():
        raise RuntimeError(f"managed secret must not be a symlink: {destination}")
    if destination.exists() and not destination.is_file():
        raise RuntimeError(f"managed secret is not a file: {destination}")
    if source is None:
        if not destination.is_file():
            raise FileNotFoundError(f"missing required secret {destination}")
    elif not same_path(source, destination):
        atomic_write(destination, source.read_bytes(), 0o400)
    os.chmod(destination, 0o400)
    chown(destination, ACCOUNT_UID, ACCOUNT_GID)


def _link_target(link: Path) -> Path | None:
    if link.is_symlink():
        return Path(os.readlink(link))
    if link.exists():
        raise RuntimeError(f"managed release pointer is not a symlink: {link}")
    return None


def _restore_links(snapshot: dict[Path, Path | None]) -> None:
    for link, target in snapshot.items():
        if target is None:
            link.unlink(missing_ok=True)
        else:
            atomic_symlink(target, link)


def _file_snapshot(path: Path) -> tuple[bytes, int, int, int] | None:
    if path.is_symlink():
        raise RuntimeError(f"managed file must not be a symlink: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError(f"managed path is not a file: {path}")
    metadata = path.stat()
    return path.read_bytes(), stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid


def _restore_files(snapshot: dict[Path, tuple[bytes, int, int, int] | None]) -> None:
    for path, state in snapshot.items():
        if state is None:
            path.unlink(missing_ok=True)
            continue
        content, mode, uid, gid = state
        atomic_write(path, content, mode)
        chown(path, uid, gid)


def _switch_release(current: Path, previous: Path, target: Path) -> None:
    old_target = _link_target(current)
    if old_target is not None and old_target != target:
        atomic_symlink(old_target, previous)
    atomic_symlink(target, current)


def _link_current_artifacts() -> None:
    atomic_symlink(CURRENT_ORCHARD / "orchard", CURRENT_ORCHARD_BINARY)
    atomic_symlink(CURRENT_TART / TART_EXECUTABLE_RELATIVE, CURRENT_TART_BINARY)
    atomic_symlink(CURRENT_TART / TART_APP.name, CURRENT_TART_APP)


def install(
    orchard_source: Path,
    tart_app_source: Path,
    admin_token_source: Path | None,
    worker_token_source: Path | None,
) -> None:
    require_root()
    _ensure_identity()
    _prepare_directories()
    install_pinned_file(orchard_source, ORCHARD_BINARY, ORCHARD_SHA256)
    install_pinned_tree(tart_app_source, TART_APP, TART_APP_EXECUTABLE_RELATIVE, TART_SHA256)
    atomic_write(ORCHARD_RELEASE / ".sha256", f"{ORCHARD_SHA256}\n", 0o644)
    atomic_write(TART_RELEASE / ".sha256", f"{TART_SHA256}\n", 0o644)
    managed_files = (
        ADMIN_TOKEN,
        WORKER_TOKEN,
        CONTROLLER_WRAPPER,
        WORKER_WRAPPER,
        CONTROLLER_PLIST,
        WORKER_PLIST,
    )
    file_snapshot = {path: _file_snapshot(path) for path in managed_files}
    links = (
        CURRENT_ORCHARD,
        PREVIOUS_ORCHARD,
        CURRENT_TART,
        PREVIOUS_TART,
        CURRENT_ORCHARD_BINARY,
        CURRENT_TART_BINARY,
        CURRENT_TART_APP,
    )
    link_snapshot = {link: _link_target(link) for link in links}
    loaded = loaded_labels(SERVICE_LABELS)
    activation_started = False
    try:
        _install_secret(admin_token_source, ADMIN_TOKEN)
        _install_secret(worker_token_source, WORKER_TOKEN)
        atomic_write(CONTROLLER_WRAPPER, controller_wrapper(), 0o755)
        atomic_write(WORKER_WRAPPER, worker_wrapper(), 0o755)
        atomic_write(CONTROLLER_PLIST, controller_plist(), 0o600)
        atomic_write(WORKER_PLIST, worker_plist(), 0o600)
        activation_started = True
        stop_services(loaded)
        _switch_release(CURRENT_ORCHARD, PREVIOUS_ORCHARD, ORCHARD_RELEASE)
        _switch_release(CURRENT_TART, PREVIOUS_TART, TART_RELEASE)
        _link_current_artifacts()
        start_services(SERVICE_LABELS)
    except BaseException as primary:
        try:
            if activation_started:
                stop_services(loaded_labels(SERVICE_LABELS))
            _restore_links(link_snapshot)
            _restore_files(file_snapshot)
            if activation_started:
                start_services(loaded)
        except BaseException as restore_error:
            primary.add_note(f"installation restoration also failed: {restore_error}")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or update the pinned Orchard baseline")
    parser.add_argument("--orchard-source", type=Path, required=True)
    parser.add_argument("--tart-app-source", type=Path, required=True)
    parser.add_argument("--admin-token-source", type=Path)
    parser.add_argument("--worker-token-source", type=Path)
    args = parser.parse_args(argv)
    install(
        args.orchard_source,
        args.tart_app_source,
        args.admin_token_source,
        args.worker_token_source,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
