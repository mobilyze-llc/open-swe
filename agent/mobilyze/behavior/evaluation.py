from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field

from agent.mobilyze.behavior.assertions import assert_probe
from agent.mobilyze.behavior.binding import TaskBinding
from agent.mobilyze.behavior.contract import (
    BehaviorContract,
    ContractIdentity,
    ObservableClause,
    OutOfScopeClause,
)
from agent.mobilyze.behavior.core import BehaviorModel, Identifier, Revision
from agent.mobilyze.behavior.evidence import (
    EvidenceIssue,
    EvidenceIssueCode,
    EvidenceSummary,
    sensitive_text_issue,
    summarize_evidence,
)
from agent.mobilyze.behavior.observations import (
    CliObservation,
    FilesystemObservation,
    GeneratedArtifactObservation,
    HttpObservation,
    ObservationType,
    ProcessObservation,
)
from agent.mobilyze.behavior.probes import (
    CliProbe,
    EvidenceKind,
    FilesystemAssertion,
    FilesystemProbe,
    Fixture,
    GeneratedArtifactAssertion,
    GeneratedArtifactProbe,
    HttpProbe,
    ProcessProbe,
    supported_evidence,
)


class ClauseStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    OUT_OF_SCOPE = "out_of_scope"


class EvaluationIssueCode(StrEnum):
    MISSING_OBSERVATION = "missing_observation"
    OBSERVATION_BINDING = "observation_binding"
    DUPLICATE_OBSERVATION = "duplicate_observation"
    ASSERTION_FAILED = "assertion_failed"
    UNEXPECTED_OBSERVATION = "unexpected_observation"


class EvaluationIssue(BehaviorModel):
    code: EvaluationIssueCode
    message: str = Field(min_length=1, max_length=256)


class ClauseResult(BehaviorModel):
    clause_id: Identifier
    fixture_name: Identifier | None
    status: ClauseStatus
    summary: str = Field(min_length=1, max_length=512)
    evidence: tuple[EvidenceSummary, ...] = Field(default=(), max_length=16)
    issues: tuple[EvaluationIssue, ...] = Field(default=(), max_length=32)
    evidence_issues: tuple[EvidenceIssue, ...] = Field(default=(), max_length=64)


class EvaluationReport(BehaviorModel):
    task_id: Identifier
    contract_identity: ContractIdentity
    target_revision: Revision
    results: tuple[ClauseResult, ...] = Field(min_length=1, max_length=256)
    issues: tuple[EvaluationIssue, ...] = Field(default=(), max_length=256)

    @property
    def passed(self) -> bool:
        required = (
            result for result in self.results if result.status is not ClauseStatus.OUT_OF_SCOPE
        )
        return not self.issues and all(result.status is ClauseStatus.PASS for result in required)


def evaluate_contract(
    contract: BehaviorContract,
    binding: TaskBinding,
    observations: Sequence[ObservationType],
) -> EvaluationReport:
    if len(observations) > 256:
        raise ValueError("at most 256 observations may be evaluated")
    if binding.contract_identity != contract.identity:
        raise ValueError("task binding does not match the supplied contract identity")
    fixtures = {fixture.name: fixture for fixture in contract.fixtures}
    results = tuple(
        _evaluate_clause(clause, fixtures[clause.fixture_name], binding, observations)
        for clause in contract.clauses
    ) + tuple(_out_of_scope_result(clause) for clause in contract.out_of_scope_clauses)
    known_clause_ids = {clause.clause_id for clause in contract.clauses}
    report_issues = tuple(
        EvaluationIssue(
            code=EvaluationIssueCode.UNEXPECTED_OBSERVATION,
            message=f"observation references undeclared clause '{observation.clause_id}'",
        )
        for observation in observations
        if observation.clause_id not in known_clause_ids
    )
    return EvaluationReport(
        task_id=binding.task_id,
        contract_identity=contract.identity,
        target_revision=binding.target_revision,
        results=results,
        issues=report_issues,
    )


def _evaluate_clause(
    clause: ObservableClause,
    fixture: Fixture,
    binding: TaskBinding,
    observations: Sequence[ObservationType],
) -> ClauseResult:
    related = [
        observation for observation in observations if observation.clause_id == clause.clause_id
    ]
    valid = [
        observation for observation in related if _is_wired(observation, clause, fixture, binding)
    ]
    issues: list[EvaluationIssue] = []
    if len(related) > 1:
        issues.append(
            EvaluationIssue(
                code=EvaluationIssueCode.DUPLICATE_OBSERVATION,
                message="duplicate or contradictory observations fail closed",
            )
        )
    if any(observation not in valid for observation in related):
        issues.append(
            EvaluationIssue(
                code=EvaluationIssueCode.OBSERVATION_BINDING,
                message="an observation does not match the task, contract, target, fixture, or probe",
            )
        )
    if not valid:
        if not related:
            issues.append(
                EvaluationIssue(
                    code=EvaluationIssueCode.MISSING_OBSERVATION,
                    message="no explicit observation was supplied",
                )
            )
        return ClauseResult(
            clause_id=clause.clause_id,
            fixture_name=fixture.name,
            status=ClauseStatus.BLOCKED,
            summary="clause could not be evaluated from one correctly bound observation",
            issues=tuple(issues),
        )
    outcomes = [assert_probe(fixture, observation) for observation in valid]
    failed = any(not outcome for outcome in outcomes)
    if len(valid) > 1:
        if failed:
            issues.append(
                EvaluationIssue(
                    code=EvaluationIssueCode.ASSERTION_FAILED,
                    message="a contradictory observation failed the declared probe assertion",
                )
            )
            return ClauseResult(
                clause_id=clause.clause_id,
                fixture_name=fixture.name,
                status=ClauseStatus.FAIL,
                summary=clause.failure_behavior,
                issues=tuple(issues),
            )
        return ClauseResult(
            clause_id=clause.clause_id,
            fixture_name=fixture.name,
            status=ClauseStatus.BLOCKED,
            summary="duplicate observations fail closed",
            issues=tuple(issues),
        )
    evidence, evidence_issues = _evaluate_evidence(clause, fixture, valid[0])
    evidence_issues += _observation_content_issues(valid[0])
    if failed:
        issues.append(
            EvaluationIssue(
                code=EvaluationIssueCode.ASSERTION_FAILED,
                message="the observed value did not satisfy the declared probe assertion",
            )
        )
        return ClauseResult(
            clause_id=clause.clause_id,
            fixture_name=fixture.name,
            status=ClauseStatus.FAIL,
            summary=clause.failure_behavior,
            evidence=evidence,
            issues=tuple(issues),
            evidence_issues=evidence_issues,
        )
    if issues or evidence_issues:
        return ClauseResult(
            clause_id=clause.clause_id,
            fixture_name=fixture.name,
            status=ClauseStatus.BLOCKED,
            summary="assertion passed but observation or evidence validation failed closed",
            evidence=evidence,
            issues=tuple(issues),
            evidence_issues=evidence_issues,
        )
    return ClauseResult(
        clause_id=clause.clause_id,
        fixture_name=fixture.name,
        status=ClauseStatus.PASS,
        summary=clause.success_behavior,
        evidence=evidence,
    )


def _is_wired(
    observation: ObservationType,
    clause: ObservableClause,
    fixture: Fixture,
    binding: TaskBinding,
) -> bool:
    return (
        observation.task_id == binding.task_id
        and observation.contract_identity == binding.contract_identity
        and observation.target_revision == binding.target_revision
        and observation.fixture_name == clause.fixture_name
        and observation.probe_kind == fixture.probe.kind
        and _observation_matches_probe_type(observation, fixture)
        and _observation_fields_match_probe(observation, fixture)
    )


def _observation_matches_probe_type(observation: ObservationType, fixture: Fixture) -> bool:
    pairs = (
        (CliProbe, CliObservation),
        (HttpProbe, HttpObservation),
        (GeneratedArtifactProbe, GeneratedArtifactObservation),
        (FilesystemProbe, FilesystemObservation),
        (ProcessProbe, ProcessObservation),
    )
    return any(
        isinstance(fixture.probe, probe_type) and isinstance(observation, observation_type)
        for probe_type, observation_type in pairs
    )


def _observation_fields_match_probe(observation: ObservationType, fixture: Fixture) -> bool:
    probe = fixture.probe
    if isinstance(probe, GeneratedArtifactProbe) and isinstance(
        observation, GeneratedArtifactObservation
    ):
        return probe.assertion is GeneratedArtifactAssertion.SHA256 or observation.sha256 is None
    if isinstance(probe, FilesystemProbe) and isinstance(observation, FilesystemObservation):
        return probe.assertion is FilesystemAssertion.SHA256 or observation.sha256 is None
    return True


def _evaluate_evidence(
    clause: ObservableClause,
    fixture: Fixture,
    observation: ObservationType,
) -> tuple[tuple[EvidenceSummary, ...], tuple[EvidenceIssue, ...]]:
    summaries: list[EvidenceSummary] = []
    issues: list[EvidenceIssue] = []
    seen: set[object] = set()
    capabilities = supported_evidence(fixture.probe)
    for raw in observation.evidence:
        if raw.kind in seen:
            issues.append(
                EvidenceIssue(
                    code=EvidenceIssueCode.DUPLICATE,
                    kind=raw.kind,
                    message="duplicate evidence kind",
                )
            )
        seen.add(raw.kind)
        if raw.kind not in capabilities:
            issues.append(
                EvidenceIssue(
                    code=EvidenceIssueCode.UNSUPPORTED,
                    kind=raw.kind,
                    message="evidence kind is not supported by the declared probe",
                )
            )
        summary, issue = summarize_evidence(raw)
        summaries.append(summary)
        if issue is not None:
            issues.append(issue)
    for kind in clause.required_evidence:
        if kind not in seen:
            issues.append(
                EvidenceIssue(
                    code=EvidenceIssueCode.MISSING,
                    kind=kind,
                    message="required evidence kind is missing",
                )
            )
    return tuple(summaries), tuple(issues)


def _observation_content_issues(observation: ObservationType) -> tuple[EvidenceIssue, ...]:
    values: tuple[tuple[EvidenceKind, str], ...]
    if isinstance(observation, CliObservation):
        values = (
            (EvidenceKind.CLI_PUBLIC_OUTPUT, observation.public_stdout),
            (EvidenceKind.CLI_PUBLIC_OUTPUT, observation.public_stderr),
        )
    elif isinstance(observation, HttpObservation):
        values = ((EvidenceKind.HTTP_PUBLIC_BODY, observation.public_body),)
    elif isinstance(observation, ProcessObservation):
        values = ((EvidenceKind.PROCESS_PUBLIC_LOG, observation.public_log),)
    else:
        values = ()
    return tuple(
        issue for kind, value in values if (issue := sensitive_text_issue(kind, value)) is not None
    )


def _out_of_scope_result(clause: OutOfScopeClause) -> ClauseResult:
    return ClauseResult(
        clause_id=clause.clause_id,
        fixture_name=None,
        status=ClauseStatus.OUT_OF_SCOPE,
        summary=clause.reason,
    )
