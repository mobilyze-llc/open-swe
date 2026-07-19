from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TypeAlias

from agent.mobilyze.behavior.codec import content_sha256
from agent.mobilyze.behavior.models import (
    ArtifactProbe,
    CliProbe,
    ContractClause,
    FileState,
    HttpProbe,
    JsonFieldExpectation,
    JsonKind,
    ProcessProbe,
)
from agent.mobilyze.behavior.policy import require_safe_artifact_path, require_sha256
from agent.mobilyze.behavior.report import ClauseResult, ClauseStatus, Evidence


@dataclass(frozen=True, slots=True)
class FileObservation:
    path: str
    exists: bool
    sha256: str | None = None

    def __post_init__(self) -> None:
        require_safe_artifact_path(self.path)
        if self.sha256 is not None:
            require_sha256(self.sha256, "observed file hash")
        if not self.exists and self.sha256 is not None:
            raise ValueError("an absent observed file cannot carry a hash")


@dataclass(frozen=True, slots=True)
class CliObservation:
    exit_code: int
    stdout: str
    stderr: str
    filesystem: tuple[FileObservation, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("observed CLI exit code must be an integer")
        if not isinstance(self.filesystem, tuple):
            raise ValueError("observed filesystem must be immutable")


@dataclass(frozen=True, slots=True)
class HttpObservation:
    status_code: int
    response: Mapping[str, object]
    persisted_state: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise ValueError("observed HTTP status must be between 100 and 599")


@dataclass(frozen=True, slots=True)
class ArtifactObservation:
    path: str
    content: str
    exists: bool = True

    def __post_init__(self) -> None:
        require_safe_artifact_path(self.path)
        if not isinstance(self.exists, bool):
            raise ValueError("observed artifact existence must be a boolean")
        if not self.exists and self.content:
            raise ValueError("an absent observed artifact cannot carry content")


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    launched: bool
    terminated: bool
    exit_code: int | None
    public_log: str


Observation: TypeAlias = CliObservation | HttpObservation | ArtifactObservation | ProcessObservation


def _kind_matches(value: object, kind: JsonKind) -> bool:
    if kind is JsonKind.NULL:
        return value is None
    if kind is JsonKind.BOOLEAN:
        return isinstance(value, bool)
    if kind is JsonKind.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is JsonKind.NUMBER:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind is JsonKind.STRING:
        return isinstance(value, str)
    if kind is JsonKind.OBJECT:
        return isinstance(value, Mapping)
    return isinstance(value, list)


def _field_failures(value: object, expectations: tuple[JsonFieldExpectation, ...]) -> list[str]:
    failures: list[str] = []
    for expectation in expectations:
        current = value
        missing = False
        for part in expectation.path:
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
                current = current[int(part)]
            else:
                missing = True
                break
        label = ".".join(expectation.path)
        if missing:
            failures.append(f"missing JSON field {label}")
        elif not _kind_matches(current, expectation.kind):
            failures.append(f"JSON field {label} has the wrong type")
        elif expectation.compare_value:
            expected = expectation.expected
            number_match = (
                expectation.kind is JsonKind.NUMBER
                and isinstance(expected, int | float)
                and not isinstance(expected, bool)
                and current == expected
            )
            if not number_match and (type(current) is not type(expected) or current != expected):
                failures.append(f"JSON field {label} has the wrong value")
    return failures


def _json_failures(raw: str, expectations: tuple[JsonFieldExpectation, ...]) -> list[str]:
    if not expectations:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ["public output is not valid JSON"]
    return _field_failures(parsed, expectations)


def _evidence(clause: ContractClause, summary: str) -> tuple[Evidence, ...]:
    return tuple(
        Evidence(
            type=evidence_type,
            reference=f"probe://{clause.id}/{evidence_type.value}",
            summary=summary,
        )
        for evidence_type in clause.evidence_types
    )


def _completed(clause: ContractClause, failures: list[str], summary: str) -> ClauseResult:
    return ClauseResult(
        clause_id=clause.id,
        status=ClauseStatus.FAIL if failures else ClauseStatus.PASS,
        evidence=_evidence(clause, summary),
        reproduction_reference=f"probe://{clause.id}/reproduce",
        blocker=None,
        anti_cheat_passed=None,
    )


def _execute_cli(clause: ContractClause, probe: CliProbe, observed: CliObservation) -> ClauseResult:
    failures: list[str] = []
    if observed.exit_code != probe.expected_exit_code:
        failures.append("CLI exit code differs from the contract")
    failures.extend(
        "public stdout is missing a required marker"
        for marker in probe.stdout_contains
        if marker not in observed.stdout
    )
    failures.extend(
        "public stderr is missing a required marker"
        for marker in probe.stderr_contains
        if marker not in observed.stderr
    )
    failures.extend(_json_failures(observed.stdout, probe.stdout_fields))
    files = {item.path: item for item in observed.filesystem}
    for effect in probe.filesystem_effects:
        actual = files.get(effect.path)
        if actual is None and effect.state is FileState.EXISTS:
            failures.append("filesystem effect was not observed")
        elif actual is not None and effect.state is FileState.EXISTS and not actual.exists:
            failures.append("expected generated file is absent")
        elif actual is not None and effect.state is FileState.ABSENT and actual.exists:
            failures.append("expected absent file exists")
        elif actual is not None and effect.sha256 is not None and actual.sha256 != effect.sha256:
            failures.append("generated file hash differs from the contract")
    checks = (
        1
        + len(probe.stdout_contains)
        + len(probe.stderr_contains)
        + len(probe.stdout_fields)
        + len(probe.filesystem_effects)
    )
    summary = f"exit={observed.exit_code}; checks={checks}; failures={len(failures)}"
    return _completed(clause, failures, summary)


def _execute_http(
    clause: ContractClause, probe: HttpProbe, observed: HttpObservation
) -> ClauseResult:
    failures: list[str] = []
    if observed.status_code != probe.expected_status:
        failures.append("HTTP response status differs from the contract")
    failures.extend(_field_failures(observed.response, probe.response_fields))
    failures.extend(_field_failures(observed.persisted_state, probe.persistence_fields))
    summary = f"status={observed.status_code}; assertions={len(probe.response_fields) + len(probe.persistence_fields)}; failures={len(failures)}"
    return _completed(clause, failures, summary)


def _execute_artifact(
    clause: ContractClause, probe: ArtifactProbe, observed: ArtifactObservation
) -> ClauseResult:
    failures: list[str] = []
    if observed.path != probe.path:
        failures.append("generated artifact path differs from the contract")
    if observed.exists is not probe.expected_exists:
        failures.append("generated artifact existence differs from the contract")
    digest = content_sha256(observed.content) if observed.exists else None
    if observed.exists:
        if probe.expected_sha256 is not None and digest != probe.expected_sha256:
            failures.append("generated artifact hash differs from the contract")
        failures.extend(
            "generated artifact is missing required public content"
            for marker in probe.contains
            if marker not in observed.content
        )
        failures.extend(_json_failures(observed.content, probe.fields))
    summary = (
        f"artifact_exists={observed.exists}; artifact_sha256={digest}; failures={len(failures)}"
    )
    return _completed(clause, failures, summary)


def _execute_process(
    clause: ContractClause, probe: ProcessProbe, observed: ProcessObservation
) -> ClauseResult:
    failures: list[str] = []
    if observed.launched is not probe.expected_launched:
        failures.append("process launch state differs from the contract")
    if observed.terminated is not probe.expected_terminated:
        failures.append("process termination state differs from the contract")
    if probe.expected_exit_code is not None and observed.exit_code != probe.expected_exit_code:
        failures.append("process exit code differs from the contract")
    failures.extend(
        "public process log is missing a required marker"
        for marker in probe.public_log_contains
        if marker not in observed.public_log
    )
    summary = (
        f"launched={observed.launched}; terminated={observed.terminated}; failures={len(failures)}"
    )
    return _completed(clause, failures, summary)


def _execute_with_probe(
    clause: ContractClause,
    probe: CliProbe | HttpProbe | ArtifactProbe | ProcessProbe,
    observed: Observation,
) -> ClauseResult:
    if isinstance(probe, CliProbe) and isinstance(observed, CliObservation):
        return _execute_cli(clause, probe, observed)
    if isinstance(probe, HttpProbe) and isinstance(observed, HttpObservation):
        return _execute_http(clause, probe, observed)
    if isinstance(probe, ArtifactProbe) and isinstance(observed, ArtifactObservation):
        return _execute_artifact(clause, probe, observed)
    if isinstance(probe, ProcessProbe) and isinstance(observed, ProcessObservation):
        return _execute_process(clause, probe, observed)
    return ClauseResult(
        clause_id=clause.id,
        status=ClauseStatus.BLOCKED,
        evidence=(),
        reproduction_reference=f"probe://{clause.id}/reproduce",
        blocker="observation type does not match the approved probe",
        anti_cheat_passed=None,
    )


def execute_clause(
    clause: ContractClause,
    observed: Observation,
    anti_cheat_observed: Observation | None = None,
) -> ClauseResult:
    probe = clause.probe
    if probe is None:
        raise ValueError("in-scope clauses require an approved probe")
    primary = _execute_with_probe(clause, probe, observed)
    if primary.status is ClauseStatus.BLOCKED or clause.anti_cheat_probe is None:
        return primary
    if anti_cheat_observed is None:
        return replace(
            primary,
            status=ClauseStatus.BLOCKED,
            evidence=(),
            blocker="required anti-cheat observation was not supplied",
            anti_cheat_passed=None,
        )
    anti_cheat = _execute_with_probe(clause, clause.anti_cheat_probe, anti_cheat_observed)
    if anti_cheat.status is ClauseStatus.BLOCKED:
        return replace(
            anti_cheat,
            blocker="anti-cheat observation type does not match the approved probe",
            anti_cheat_passed=None,
        )
    anti_cheat_passed = anti_cheat.status is ClauseStatus.PASS
    evidence = tuple(
        replace(
            item,
            summary=f"{item.summary}; anti_cheat={'pass' if anti_cheat_passed else 'fail'}",
        )
        for item in primary.evidence
    )
    return replace(
        primary,
        status=(
            ClauseStatus.PASS
            if primary.status is ClauseStatus.PASS and anti_cheat_passed
            else ClauseStatus.FAIL
        ),
        evidence=evidence,
        anti_cheat_passed=anti_cheat_passed,
    )
