from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from agent.mobilyze.behavior.core import BehaviorModel, Identifier, Sha256, ShortText
from agent.mobilyze.behavior.paths import ObservablePath


class ProbeKind(StrEnum):
    CLI = "cli"
    HTTP = "http"
    GENERATED_ARTIFACT = "generated_artifact"
    FILESYSTEM = "filesystem"
    PROCESS = "process"


class EvidenceKind(StrEnum):
    CLI_EXIT_STATUS = "cli_exit_status"
    CLI_PUBLIC_OUTPUT = "cli_public_output"
    HTTP_STATUS = "http_status"
    HTTP_PUBLIC_BODY = "http_public_body"
    ARTIFACT_EXISTENCE = "artifact_existence"
    ARTIFACT_CONTENT = "artifact_content"
    FILESYSTEM_STATE = "filesystem_state"
    FILE_CONTENT = "file_content"
    PROCESS_STATE = "process_state"
    PROCESS_EXIT_CODE = "process_exit_code"
    PROCESS_PUBLIC_LOG = "process_public_log"


class HttpMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"


class GeneratedArtifactAssertion(StrEnum):
    EXISTS = "exists"
    ABSENT = "absent"
    SHA256 = "sha256"


class FilesystemAssertion(StrEnum):
    EXISTS = "exists"
    ABSENT = "absent"
    FILE = "file"
    DIRECTORY = "directory"
    SHA256 = "sha256"


class ProcessAssertion(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    EXITED_ZERO = "exited_zero"
    EXITED_CODE = "exited_code"
    PUBLIC_LOG_CONTAINS = "public_log_contains"


class CliProbe(BehaviorModel):
    kind: Literal[ProbeKind.CLI] = ProbeKind.CLI
    executable_reference: Identifier
    expected_exit_code: int = Field(ge=0, le=255)
    stdout_contains: ShortText | None = None
    stderr_contains: ShortText | None = None


class HttpProbe(BehaviorModel):
    kind: Literal[ProbeKind.HTTP] = ProbeKind.HTTP
    endpoint_reference: Identifier
    method: HttpMethod
    path: str = Field(min_length=1, max_length=512, pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
    expected_status: int = Field(ge=100, le=599)
    body_contains: ShortText | None = None

    @model_validator(mode="after")
    def _validate_head_body(self) -> HttpProbe:
        if self.method is HttpMethod.HEAD and self.body_contains is not None:
            raise ValueError("HEAD probes cannot assert response body content")
        return self


class GeneratedArtifactProbe(BehaviorModel):
    kind: Literal[ProbeKind.GENERATED_ARTIFACT] = ProbeKind.GENERATED_ARTIFACT
    path: ObservablePath
    assertion: GeneratedArtifactAssertion
    expected_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def _validate_sha256(self) -> GeneratedArtifactProbe:
        if (self.assertion is GeneratedArtifactAssertion.SHA256) != (
            self.expected_sha256 is not None
        ):
            raise ValueError("sha256 artifact assertions require exactly one expected_sha256")
        return self


class FilesystemProbe(BehaviorModel):
    kind: Literal[ProbeKind.FILESYSTEM] = ProbeKind.FILESYSTEM
    path: ObservablePath
    assertion: FilesystemAssertion
    expected_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def _validate_sha256(self) -> FilesystemProbe:
        if (self.assertion is FilesystemAssertion.SHA256) != (self.expected_sha256 is not None):
            raise ValueError("sha256 filesystem assertions require exactly one expected_sha256")
        return self


class ProcessProbe(BehaviorModel):
    kind: Literal[ProbeKind.PROCESS] = ProbeKind.PROCESS
    process_reference: Identifier
    assertion: ProcessAssertion
    expected_exit_code: int | None = Field(default=None, ge=0, le=255)
    public_log_contains: ShortText | None = None

    @model_validator(mode="after")
    def _validate_expected_value(self) -> ProcessProbe:
        if (self.assertion is ProcessAssertion.EXITED_CODE) != (
            self.expected_exit_code is not None
        ):
            raise ValueError("exited_code assertions require exactly one expected_exit_code")
        if (self.assertion is ProcessAssertion.PUBLIC_LOG_CONTAINS) != (
            self.public_log_contains is not None
        ):
            raise ValueError("public_log_contains assertions require exactly one expected value")
        return self


Probe = Annotated[
    CliProbe | HttpProbe | GeneratedArtifactProbe | FilesystemProbe | ProcessProbe,
    Field(discriminator="kind"),
]


class Fixture(BehaviorModel):
    name: Identifier
    probe: Probe
    credential_references: tuple[Identifier, ...] = Field(default=(), max_length=16)


ProbeType = CliProbe | HttpProbe | GeneratedArtifactProbe | FilesystemProbe | ProcessProbe


def supported_evidence(probe: ProbeType) -> frozenset[EvidenceKind]:
    if isinstance(probe, CliProbe):
        kinds = {EvidenceKind.CLI_EXIT_STATUS}
        if probe.stdout_contains is not None or probe.stderr_contains is not None:
            kinds.add(EvidenceKind.CLI_PUBLIC_OUTPUT)
        return frozenset(kinds)
    if isinstance(probe, HttpProbe):
        kinds = {EvidenceKind.HTTP_STATUS}
        if probe.body_contains is not None:
            kinds.add(EvidenceKind.HTTP_PUBLIC_BODY)
        return frozenset(kinds)
    if isinstance(probe, GeneratedArtifactProbe):
        kinds = {EvidenceKind.ARTIFACT_EXISTENCE}
        if probe.assertion is GeneratedArtifactAssertion.SHA256:
            kinds.add(EvidenceKind.ARTIFACT_CONTENT)
        return frozenset(kinds)
    if isinstance(probe, FilesystemProbe):
        kinds = {EvidenceKind.FILESYSTEM_STATE}
        if probe.assertion is FilesystemAssertion.SHA256:
            kinds.add(EvidenceKind.FILE_CONTENT)
        return frozenset(kinds)
    kinds = {EvidenceKind.PROCESS_STATE}
    if probe.assertion in {ProcessAssertion.EXITED_ZERO, ProcessAssertion.EXITED_CODE}:
        kinds.add(EvidenceKind.PROCESS_EXIT_CODE)
    if probe.assertion is ProcessAssertion.PUBLIC_LOG_CONTAINS:
        kinds.add(EvidenceKind.PROCESS_PUBLIC_LOG)
    return frozenset(kinds)
