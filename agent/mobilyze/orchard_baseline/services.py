from __future__ import annotations

from collections.abc import Iterable

from agent.mobilyze.orchard_baseline.host import run
from agent.mobilyze.orchard_baseline.service_files import SERVICE_PLISTS


def is_loaded(label: str) -> bool:
    return run(["/bin/launchctl", "print", f"system/{label}"], check=False).returncode == 0


def loaded_labels(labels: Iterable[str]) -> tuple[str, ...]:
    return tuple(label for label in labels if is_loaded(label))


def stop_services(labels: Iterable[str]) -> None:
    for label in reversed(tuple(labels)):
        run(["/bin/launchctl", "bootout", f"system/{label}"])


def start_services(labels: Iterable[str]) -> None:
    for label in labels:
        if not is_loaded(label):
            run(["/bin/launchctl", "bootstrap", "system", str(SERVICE_PLISTS[label])])
