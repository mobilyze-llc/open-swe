from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal, cast

from pydantic import Field, TypeAdapter, model_validator

from agent.mobilyze.behavior.contract import ContractIdentity
from agent.mobilyze.behavior.core import BehaviorModel, Identifier, Revision, Sha256
from agent.mobilyze.behavior.probes import EvidenceKind, ProbeKind


class Presence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"


class FilesystemState(StrEnum):
    ABSENT = "absent"
    FILE = "file"
    DIRECTORY = "directory"
    OTHER = "other"


class ProcessState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    EXITED = "exited"


class RawEvidence(BehaviorModel):
    kind: EvidenceKind
    summary: str = Field(
        min_length=1,
        max_length=8192,
        repr=False,
        json_schema_extra={"secret": True},
    )


class ObservationBase(BehaviorModel):
    task_id: Identifier
    contract_identity: ContractIdentity
    target_revision: Revision
    clause_id: Identifier
    fixture_name: Identifier
    evidence: tuple[RawEvidence, ...] = Field(default=(), max_length=16)


class CliObservation(ObservationBase):
    probe_kind: Literal[ProbeKind.CLI] = ProbeKind.CLI
    exit_code: int = Field(ge=0, le=255)
    public_stdout: str = Field(
        default="", max_length=8192, repr=False, json_schema_extra={"secret": True}
    )
    public_stderr: str = Field(
        default="", max_length=8192, repr=False, json_schema_extra={"secret": True}
    )


class HttpObservation(ObservationBase):
    probe_kind: Literal[ProbeKind.HTTP] = ProbeKind.HTTP
    status_code: int = Field(ge=100, le=599)
    public_body: str = Field(
        default="", max_length=8192, repr=False, json_schema_extra={"secret": True}
    )


class GeneratedArtifactObservation(ObservationBase):
    probe_kind: Literal[ProbeKind.GENERATED_ARTIFACT] = ProbeKind.GENERATED_ARTIFACT
    presence: Presence
    sha256: Sha256 | None = None

    @model_validator(mode="after")
    def _validate_absence(self) -> GeneratedArtifactObservation:
        if self.presence is Presence.ABSENT and self.sha256 is not None:
            raise ValueError("an absent artifact cannot have a content hash")
        return self


class FilesystemObservation(ObservationBase):
    probe_kind: Literal[ProbeKind.FILESYSTEM] = ProbeKind.FILESYSTEM
    state: FilesystemState
    sha256: Sha256 | None = None

    @model_validator(mode="after")
    def _validate_hash(self) -> FilesystemObservation:
        if self.state is not FilesystemState.FILE and self.sha256 is not None:
            raise ValueError("only an observed file can have a content hash")
        return self


class ProcessObservation(ObservationBase):
    probe_kind: Literal[ProbeKind.PROCESS] = ProbeKind.PROCESS
    state: ProcessState
    exit_code: int | None = Field(default=None, ge=0, le=255)
    public_log: str = Field(
        default="", max_length=8192, repr=False, json_schema_extra={"secret": True}
    )

    @model_validator(mode="after")
    def _validate_exit_code(self) -> ProcessObservation:
        if (self.state is ProcessState.EXITED) != (self.exit_code is not None):
            raise ValueError("exactly exited process observations require an exit code")
        return self


Observation = Annotated[
    CliObservation
    | HttpObservation
    | GeneratedArtifactObservation
    | FilesystemObservation
    | ProcessObservation,
    Field(discriminator="probe_kind"),
]
ObservationType = (
    CliObservation
    | HttpObservation
    | GeneratedArtifactObservation
    | FilesystemObservation
    | ProcessObservation
)
_OBSERVATION_ADAPTER = TypeAdapter(Observation)


def observation_from_persisted_dict(value: dict[str, object]) -> ObservationType:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
    return cast(ObservationType, _OBSERVATION_ADAPTER.validate_json(encoded, strict=True))


def observation_from_persisted_json(value: str | bytes) -> ObservationType:
    return cast(ObservationType, _OBSERVATION_ADAPTER.validate_json(value, strict=True))
