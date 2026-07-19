from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from agent.mobilyze.behavior.policy import (
    require_clause_id,
    require_name,
    require_safe_artifact_path,
    require_sha256,
    require_target_reference,
    require_text,
)

CONTRACT_SCHEMA = "mobilyze.behavior-contract.v1"
JsonScalar: TypeAlias = str | int | float | bool | None


class ContractValidationError(ValueError):
    pass


class OwnerType(StrEnum):
    ISSUE = "issue"
    APPROVED_PLAN = "approved_plan"


class TargetType(StrEnum):
    CLI = "cli"
    HTTP_API = "http_api"
    ARTIFACT = "artifact"
    PROCESS = "process"


class ProbeType(StrEnum):
    CLI = "cli"
    HTTP_API = "http_api"
    GENERATED_ARTIFACT = "generated_artifact"
    PROCESS = "process"


class EvidenceType(StrEnum):
    EXIT_CODE = "exit_code"
    PUBLIC_OUTPUT = "public_output"
    FILESYSTEM_EFFECT = "filesystem_effect"
    HTTP_RESPONSE = "http_response"
    PERSISTENCE = "persistence"
    ARTIFACT_HASH = "artifact_hash"
    ARTIFACT_CONTENT = "artifact_content"
    PROCESS_LIFECYCLE = "process_lifecycle"
    PUBLIC_LOG = "public_log"


class JsonKind(StrEnum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    NULL = "null"


class FileState(StrEnum):
    EXISTS = "exists"
    ABSENT = "absent"


def _translate_validation(error: ValueError) -> ContractValidationError:
    return ContractValidationError(str(error))


def _require_tuple(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"{field} must be an immutable tuple")


@dataclass(frozen=True, slots=True)
class ContractOwner:
    type: OwnerType
    reference: str

    def __post_init__(self) -> None:
        try:
            require_text(self.reference, "owner reference")
            if not isinstance(self.type, OwnerType):
                raise ValueError("owner type must be an approved owner type")
            if self.type is OwnerType.ISSUE and not self.reference.startswith("OSWE-"):
                raise ValueError("issue owner must be an OSWE issue identifier")
        except ValueError as error:
            raise _translate_validation(error) from error


@dataclass(frozen=True, slots=True)
class TargetRef:
    type: TargetType
    reference: str

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.type, TargetType):
                raise ValueError("target type must be an approved target type")
            require_target_reference(self.type.value, self.reference)
        except ValueError as error:
            raise _translate_validation(error) from error


@dataclass(frozen=True, slots=True)
class JsonFieldExpectation:
    path: tuple[str, ...]
    kind: JsonKind
    expected: JsonScalar = None
    compare_value: bool = True

    def __post_init__(self) -> None:
        _require_tuple(self.path, "JSON field path")
        if not self.path:
            raise ContractValidationError("JSON field path cannot be empty")
        try:
            if not isinstance(self.kind, JsonKind):
                raise ValueError("JSON field kind must be an approved kind")
            for part in self.path:
                require_name(part, "JSON field path component")
            if isinstance(self.expected, float) and (
                self.expected != self.expected or self.expected in {float("inf"), float("-inf")}
            ):
                raise ValueError("JSON expected value must be finite")
        except ValueError as error:
            raise _translate_validation(error) from error


@dataclass(frozen=True, slots=True)
class FileEffect:
    path: str
    state: FileState
    sha256: str | None = None

    def __post_init__(self) -> None:
        try:
            require_safe_artifact_path(self.path)
            if not isinstance(self.state, FileState):
                raise ValueError("filesystem state must be an approved state")
            if self.sha256 is not None:
                require_sha256(self.sha256, "filesystem effect hash")
            if self.state is FileState.ABSENT and self.sha256 is not None:
                raise ValueError("absent filesystem effects cannot declare a hash")
        except ValueError as error:
            raise _translate_validation(error) from error


@dataclass(frozen=True, slots=True)
class CliProbe:
    fixture: str
    expected_exit_code: int
    stdout_contains: tuple[str, ...] = ()
    stderr_contains: tuple[str, ...] = ()
    stdout_fields: tuple[JsonFieldExpectation, ...] = ()
    filesystem_effects: tuple[FileEffect, ...] = ()
    type: ProbeType = field(default=ProbeType.CLI, init=False)

    def __post_init__(self) -> None:
        _validate_probe_collections(self)
        if isinstance(self.expected_exit_code, bool) or not isinstance(
            self.expected_exit_code, int
        ):
            raise ContractValidationError("CLI expected exit code must be an integer")


@dataclass(frozen=True, slots=True)
class HttpProbe:
    fixture: str
    expected_status: int
    response_fields: tuple[JsonFieldExpectation, ...] = ()
    persistence_fields: tuple[JsonFieldExpectation, ...] = ()
    type: ProbeType = field(default=ProbeType.HTTP_API, init=False)

    def __post_init__(self) -> None:
        _validate_probe_collections(self)
        if not isinstance(self.expected_status, int) or not 100 <= self.expected_status <= 599:
            raise ContractValidationError("HTTP expected status must be between 100 and 599")


@dataclass(frozen=True, slots=True)
class ArtifactProbe:
    fixture: str
    path: str
    expected_sha256: str | None = None
    contains: tuple[str, ...] = ()
    fields: tuple[JsonFieldExpectation, ...] = ()
    type: ProbeType = field(default=ProbeType.GENERATED_ARTIFACT, init=False)

    def __post_init__(self) -> None:
        _validate_probe_collections(self)
        try:
            require_safe_artifact_path(self.path)
            if self.expected_sha256 is not None:
                require_sha256(self.expected_sha256, "artifact hash")
        except ValueError as error:
            raise _translate_validation(error) from error


@dataclass(frozen=True, slots=True)
class ProcessProbe:
    fixture: str
    expected_launched: bool
    expected_terminated: bool
    expected_exit_code: int | None = None
    public_log_contains: tuple[str, ...] = ()
    type: ProbeType = field(default=ProbeType.PROCESS, init=False)

    def __post_init__(self) -> None:
        _validate_probe_collections(self)
        if not isinstance(self.expected_launched, bool) or not isinstance(
            self.expected_terminated, bool
        ):
            raise ContractValidationError("process lifecycle expectations must be booleans")
        if self.expected_exit_code is not None and (
            isinstance(self.expected_exit_code, bool)
            or not isinstance(self.expected_exit_code, int)
        ):
            raise ContractValidationError("process expected exit code must be an integer")


Probe: TypeAlias = CliProbe | HttpProbe | ArtifactProbe | ProcessProbe


def _validate_probe_collections(probe: Probe) -> None:
    try:
        require_name(probe.fixture, "probe fixture")
        tuple_fields = {
            "stdout_contains",
            "stderr_contains",
            "stdout_fields",
            "filesystem_effects",
            "response_fields",
            "persistence_fields",
            "contains",
            "fields",
            "public_log_contains",
        }
        for field_name in tuple_fields.intersection(probe.__dataclass_fields__):
            value = getattr(probe, field_name)
            _require_tuple(value, field_name)
            if field_name.endswith("contains"):
                for marker in value:
                    require_text(marker, f"{field_name} marker")
    except ValueError as error:
        raise _translate_validation(error) from error


@dataclass(frozen=True, slots=True)
class ContractClause:
    id: str
    task: str
    expected_behavior: str
    failure_behavior: str
    evidence_types: tuple[EvidenceType, ...]
    probe: Probe | None = None
    anti_cheat: bool = False
    out_of_scope_reason: str | None = None
    adjacent_clause_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_tuple(self.evidence_types, "evidence types")
        _require_tuple(self.adjacent_clause_ids, "adjacent clause ids")
        try:
            require_clause_id(self.id)
            require_text(self.task, "clause task")
            require_text(self.expected_behavior, "expected behavior")
            require_text(self.failure_behavior, "failure behavior")
            for adjacent in self.adjacent_clause_ids:
                require_clause_id(adjacent)
            if self.id in self.adjacent_clause_ids:
                raise ValueError("a clause cannot be adjacent to itself")
            if len(set(self.adjacent_clause_ids)) != len(self.adjacent_clause_ids):
                raise ValueError("adjacent clause ids must be unique")
            if not all(isinstance(item, EvidenceType) for item in self.evidence_types):
                raise ValueError("evidence types must be approved evidence types")
            if self.probe is not None and not isinstance(
                self.probe, CliProbe | HttpProbe | ArtifactProbe | ProcessProbe
            ):
                raise ValueError("probe must be a named approved probe type")
            if self.out_of_scope_reason is None and self.probe is None:
                raise ValueError("in-scope clauses require an approved probe")
            if self.out_of_scope_reason is not None:
                require_text(self.out_of_scope_reason, "out-of-scope reason")
                if self.probe is not None or self.evidence_types or self.anti_cheat:
                    raise ValueError("out-of-scope clauses cannot declare probes or evidence")
        except ValueError as error:
            raise _translate_validation(error) from error


@dataclass(frozen=True, slots=True)
class BehaviorContract:
    schema: str
    contract_version: int
    owner: ContractOwner
    user_visible_goal: str
    target: TargetRef
    approved_fixtures: tuple[str, ...]
    credential_references: tuple[str, ...]
    clauses: tuple[ContractClause, ...]

    def __post_init__(self) -> None:
        _require_tuple(self.approved_fixtures, "approved fixtures")
        _require_tuple(self.credential_references, "credential references")
        _require_tuple(self.clauses, "contract clauses")
        try:
            if self.schema != CONTRACT_SCHEMA:
                raise ValueError(f"schema must be {CONTRACT_SCHEMA}")
            if isinstance(self.contract_version, bool) or self.contract_version < 1:
                raise ValueError("contract version must be a positive integer")
            require_text(self.user_visible_goal, "user-visible goal")
            for fixture in self.approved_fixtures:
                require_name(fixture, "approved fixture")
            for reference in self.credential_references:
                require_name(reference, "credential reference")
            if len(set(self.approved_fixtures)) != len(self.approved_fixtures):
                raise ValueError("approved fixtures must be unique")
            if len(set(self.credential_references)) != len(self.credential_references):
                raise ValueError("credential references must be unique")
            if not self.clauses:
                raise ValueError("contract must contain at least one explicit clause")
            clause_ids = tuple(clause.id for clause in self.clauses)
            if len(set(clause_ids)) != len(clause_ids):
                raise ValueError("contract clause ids must be unique")
            known_ids = set(clause_ids)
            for clause in self.clauses:
                unknown = set(clause.adjacent_clause_ids).difference(known_ids)
                if unknown:
                    raise ValueError(f"clause {clause.id} names unknown adjacent clauses")
                if clause.probe and clause.probe.fixture not in self.approved_fixtures:
                    raise ValueError(f"clause {clause.id} uses an unapproved fixture")
        except ValueError as error:
            raise _translate_validation(error) from error
