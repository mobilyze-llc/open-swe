from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

from agent.mobilyze.behavior.models import (
    ArtifactProbe,
    CliProbe,
    HttpProbe,
    Probe,
    ProcessProbe,
)
from agent.mobilyze.behavior.policy import (
    require_name,
    require_safe_artifact_path,
    require_sha256,
)


@dataclass(frozen=True, slots=True)
class FileObservation:
    path: str
    exists: bool
    sha256: str | None = None

    def __post_init__(self) -> None:
        require_safe_artifact_path(self.path)
        if not isinstance(self.exists, bool):
            raise ValueError("observed file existence must be a boolean")
        if self.sha256 is not None:
            require_sha256(self.sha256, "observed file hash")
        if not self.exists and self.sha256 is not None:
            raise ValueError("an absent observed file cannot carry a hash")


@dataclass(frozen=True, slots=True)
class CliObservation:
    fixture: str
    exit_code: int
    stdout: str
    stderr: str
    filesystem: tuple[FileObservation, ...] = ()

    def __post_init__(self) -> None:
        require_name(self.fixture, "observed CLI fixture")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("observed CLI exit code must be an integer")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise ValueError("observed CLI output must be strings")
        if not isinstance(self.filesystem, tuple) or not all(
            isinstance(item, FileObservation) for item in self.filesystem
        ):
            raise ValueError("observed filesystem must contain immutable file observations")


@dataclass(frozen=True, slots=True)
class HttpObservation:
    fixture: str
    status_code: int
    response: Mapping[str, object]
    persisted_state: Mapping[str, object]

    def __post_init__(self) -> None:
        require_name(self.fixture, "observed HTTP fixture")
        if not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise ValueError("observed HTTP status must be between 100 and 599")
        if not isinstance(self.response, Mapping) or not isinstance(self.persisted_state, Mapping):
            raise ValueError("observed HTTP response and persistence state must be mappings")


@dataclass(frozen=True, slots=True)
class ArtifactObservation:
    fixture: str
    path: str
    content: str
    exists: bool = True

    def __post_init__(self) -> None:
        require_name(self.fixture, "observed artifact fixture")
        require_safe_artifact_path(self.path)
        if not isinstance(self.content, str):
            raise ValueError("observed artifact content must be a string")
        if not isinstance(self.exists, bool):
            raise ValueError("observed artifact existence must be a boolean")
        if not self.exists and self.content:
            raise ValueError("an absent observed artifact cannot carry content")


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    fixture: str
    launched: bool
    terminated: bool
    exit_code: int | None
    public_log: str

    def __post_init__(self) -> None:
        require_name(self.fixture, "observed process fixture")
        if not isinstance(self.launched, bool) or not isinstance(self.terminated, bool):
            raise ValueError("observed process lifecycle states must be booleans")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise ValueError("observed process exit code must be an integer")
        if not isinstance(self.public_log, str):
            raise ValueError("observed public process log must be a string")


Observation: TypeAlias = CliObservation | HttpObservation | ArtifactObservation | ProcessObservation


def observation_wiring_blocker(probe: Probe, observed: Observation) -> str | None:
    type_matches = (
        (isinstance(probe, CliProbe) and isinstance(observed, CliObservation))
        or (isinstance(probe, HttpProbe) and isinstance(observed, HttpObservation))
        or (isinstance(probe, ArtifactProbe) and isinstance(observed, ArtifactObservation))
        or (isinstance(probe, ProcessProbe) and isinstance(observed, ProcessObservation))
    )
    if not type_matches:
        return "observation type does not match the approved probe"
    if observed.fixture != probe.fixture:
        return "observation fixture does not match the approved probe"
    if isinstance(probe, ArtifactProbe) and isinstance(observed, ArtifactObservation):
        if observed.path != probe.path:
            return "observed artifact path does not match the approved probe"
    return None
