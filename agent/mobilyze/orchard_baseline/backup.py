from __future__ import annotations

import argparse
import os
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from agent.mobilyze.orchard_baseline.constants import DATA_ROOT, SERVICE_LABELS
from agent.mobilyze.orchard_baseline.host import require_root
from agent.mobilyze.orchard_baseline.services import (
    loaded_labels,
    start_services,
    stop_services,
)


def _raise(error: BaseException) -> NoReturn:
    raise error


def backup_with_restore(
    stop: Callable[[], None], archive: Callable[[], None], restore: Callable[[], None]
) -> None:
    primary: BaseException | None = None
    try:
        stop()
        archive()
    except BaseException as exc:
        primary = exc
    try:
        restore()
    except BaseException as restore_error:
        if primary is None:
            raise
        primary.add_note(f"service restoration also failed: {restore_error}")
    if primary is not None:
        _raise(primary)


def create_archive(destination: Path) -> None:
    if not DATA_ROOT.is_dir():
        raise FileNotFoundError(DATA_ROOT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, "w") as archive:
            archive.add(DATA_ROOT, arcname=str(DATA_ROOT.relative_to("/")), recursive=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def backup(destination: Path) -> None:
    require_root()
    loaded = loaded_labels(SERVICE_LABELS)
    backup_with_restore(
        lambda: stop_services(loaded),
        lambda: create_archive(destination),
        lambda: start_services(loaded),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up Orchard state")
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    backup(args.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
