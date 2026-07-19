from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent.mobilyze.behavior.codec import canonical_json
from agent.mobilyze.behavior.models import EvidenceType
from agent.mobilyze.behavior.policy import redact_public_text, require_clause_id, require_sha256

REPORT_SCHEMA = "mobilyze.behavior-report.v1"


class ClauseStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True, slots=True)
class Evidence:
    type: EvidenceType
    reference: str
    summary: str

    def __post_init__(self) -> None:
        if not self.reference.startswith("probe://"):
            raise ValueError("evidence references must use the bounded probe scheme")
        if redact_public_text(self.summary) != self.summary:
            raise ValueError("evidence summary contains sensitive content")
        if len(self.summary) > 240:
            raise ValueError("evidence summary must remain compact")


@dataclass(frozen=True, slots=True)
class ClauseResult:
    clause_id: str
    status: ClauseStatus
    evidence: tuple[Evidence, ...]
    reproduction_reference: str | None
    blocker: str | None
    anti_cheat_passed: bool | None
    cache_hit: bool = False

    def __post_init__(self) -> None:
        require_clause_id(self.clause_id)
        if not isinstance(self.evidence, tuple):
            raise ValueError("clause evidence must be immutable")
        if self.status is ClauseStatus.BLOCKED and not self.blocker:
            raise ValueError("blocked clause results require a blocker")
        if self.status is not ClauseStatus.BLOCKED and self.blocker is not None:
            raise ValueError("only blocked clause results can carry a blocker")
        if self.status is ClauseStatus.OUT_OF_SCOPE and (
            self.evidence or self.reproduction_reference or self.anti_cheat_passed is not None
        ):
            raise ValueError("out-of-scope results cannot carry execution evidence")
        if self.blocker and redact_public_text(self.blocker) != self.blocker:
            raise ValueError("blocker contains sensitive content")


@dataclass(frozen=True, slots=True)
class BehaviorReport:
    schema: str
    contract_hash: str
    contract_version: int
    owner_reference: str
    target_artifact_hash: str
    profile_image_hash: str
    executor_version: str
    selected_clause_ids: tuple[str, ...]
    results: tuple[ClauseResult, ...]

    def __post_init__(self) -> None:
        if self.schema != REPORT_SCHEMA:
            raise ValueError(f"report schema must be {REPORT_SCHEMA}")
        require_sha256(self.contract_hash, "report contract hash")
        require_sha256(self.target_artifact_hash, "report target/artifact hash")
        require_sha256(self.profile_image_hash, "report profile/image hash")
        if not isinstance(self.selected_clause_ids, tuple) or not isinstance(self.results, tuple):
            raise ValueError("report collections must be immutable")
        result_ids = tuple(result.clause_id for result in self.results)
        if result_ids != self.selected_clause_ids:
            raise ValueError("every selected clause must have exactly one explicit result")
        if len(set(result_ids)) != len(result_ids):
            raise ValueError("report clause results must be unique")

    def to_json(self) -> str:
        return canonical_json(self)
