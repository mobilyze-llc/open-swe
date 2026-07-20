from __future__ import annotations

import argparse
import os
from pathlib import Path

from agent.mobilyze.orchard_baseline.constants import (
    CURRENT_ORCHARD,
    CURRENT_TART,
    PREVIOUS_ORCHARD,
    PREVIOUS_TART,
    RELEASES_ROOT,
    SERVICE_LABELS,
    TART_EXECUTABLE_RELATIVE,
)
from agent.mobilyze.orchard_baseline.host import (
    atomic_symlink,
    require_root,
    verify_sha256,
)
from agent.mobilyze.orchard_baseline.services import (
    loaded_labels,
    start_services,
    stop_services,
)


def _validated_target(link: Path, executable: Path) -> Path:
    if not link.is_symlink():
        raise RuntimeError(f"missing rollback pointer {link}")
    target = Path(os.readlink(link))
    if not target.is_absolute():
        raise RuntimeError(f"rollback target is not absolute: {target}")
    releases_root = RELEASES_ROOT.resolve(strict=True)
    resolved_target = target.resolve(strict=True)
    if releases_root not in resolved_target.parents:
        raise RuntimeError(f"rollback target is outside {RELEASES_ROOT}: {target}")
    marker = (resolved_target / ".sha256").resolve(strict=True)
    resolved_executable = (resolved_target / executable).resolve(strict=True)
    if releases_root not in marker.parents or releases_root not in resolved_executable.parents:
        raise RuntimeError(f"rollback release escapes {RELEASES_ROOT}: {target}")
    expected = marker.read_text().strip()
    if len(expected) != 64:
        raise ValueError(f"invalid checksum marker {marker}")
    verify_sha256(resolved_executable, expected)
    return resolved_target


def _restore_links(snapshot: dict[Path, Path]) -> None:
    for link, target in snapshot.items():
        atomic_symlink(target, link)


def rollback() -> None:
    require_root()
    orchard_target = _validated_target(PREVIOUS_ORCHARD, Path("orchard"))
    tart_target = _validated_target(PREVIOUS_TART, TART_EXECUTABLE_RELATIVE)
    current_orchard = _validated_target(CURRENT_ORCHARD, Path("orchard"))
    current_tart = _validated_target(CURRENT_TART, TART_EXECUTABLE_RELATIVE)
    snapshot = {
        CURRENT_ORCHARD: current_orchard,
        CURRENT_TART: current_tart,
        PREVIOUS_ORCHARD: orchard_target,
        PREVIOUS_TART: tart_target,
    }
    loaded = loaded_labels(SERVICE_LABELS)
    try:
        stop_services(loaded)
        atomic_symlink(orchard_target, CURRENT_ORCHARD)
        atomic_symlink(tart_target, CURRENT_TART)
        atomic_symlink(current_orchard, PREVIOUS_ORCHARD)
        atomic_symlink(current_tart, PREVIOUS_TART)
        start_services(loaded)
    except BaseException as primary:
        try:
            stop_services(loaded_labels(SERVICE_LABELS))
            _restore_links(snapshot)
            start_services(loaded)
        except BaseException as restore_error:
            primary.add_note(f"rollback restoration also failed: {restore_error}")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roll back to the recorded release pair")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm:
        parser.error("--confirm is required")
    rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
