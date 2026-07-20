from __future__ import annotations

import argparse
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from agent.mobilyze.orchard_baseline.backup import backup_with_restore
from agent.mobilyze.orchard_baseline.constants import DATA_ROOT, SERVICE_LABELS
from agent.mobilyze.orchard_baseline.host import require_root
from agent.mobilyze.orchard_baseline.services import (
    loaded_labels,
    start_services,
    stop_services,
)


def _validate_members(archive: tarfile.TarFile) -> None:
    allowed = Path(DATA_ROOT.relative_to("/"))
    for member in archive.getmembers():
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive member {member.name}")
        if path != allowed and allowed not in path.parents:
            raise ValueError(f"unexpected archive member {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise ValueError(f"unsupported archive member {member.name}")


def restore_archive(source: Path) -> None:
    temporary_parent = DATA_ROOT.parent
    with tempfile.TemporaryDirectory(dir=temporary_parent, prefix=".orchard-restore-") as name:
        staging = Path(name)
        with tarfile.open(source, "r") as archive:
            _validate_members(archive)
            archive.extractall(staging)
        restored = staging / DATA_ROOT.relative_to("/")
        if not restored.is_dir():
            raise ValueError("archive does not contain the Orchard data root")
        previous = DATA_ROOT.with_name(f".{DATA_ROOT.name}.restore-{os.getpid()}")
        if previous.exists():
            shutil.rmtree(previous)
        if DATA_ROOT.exists():
            os.replace(DATA_ROOT, previous)
        try:
            os.replace(restored, DATA_ROOT)
        except BaseException:
            if previous.exists():
                os.replace(previous, DATA_ROOT)
            raise
        if previous.exists():
            shutil.rmtree(previous)


def restore(source: Path) -> None:
    require_root()
    loaded = loaded_labels(SERVICE_LABELS)
    backup_with_restore(
        lambda: stop_services(loaded),
        lambda: restore_archive(source),
        lambda: start_services(loaded),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore Orchard state from a trusted state-only backup"
    )
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    restore(args.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
