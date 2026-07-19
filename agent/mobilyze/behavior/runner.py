from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from agent.mobilyze.behavior.binding import AcceptedContract
from agent.mobilyze.behavior.cache import CacheKey, ClauseCache
from agent.mobilyze.behavior.codec import canonical_hash
from agent.mobilyze.behavior.executors import Observation, execute_clause
from agent.mobilyze.behavior.policy import require_sha256, require_text
from agent.mobilyze.behavior.report import (
    REPORT_SCHEMA,
    BehaviorReport,
    ClauseResult,
    ClauseStatus,
)

EXECUTOR_VERSION = "mobilyze.behavior-probes.v1"


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    target_artifact_hash: str
    profile_image_hash: str
    executor_version: str = EXECUTOR_VERSION

    def __post_init__(self) -> None:
        require_sha256(self.target_artifact_hash, "target/artifact hash")
        require_sha256(self.profile_image_hash, "profile/image hash")
        require_text(self.executor_version, "executor version")


def _blocked(clause_id: str, *, anti_cheat: bool) -> ClauseResult:
    return ClauseResult(
        clause_id=clause_id,
        status=ClauseStatus.BLOCKED,
        evidence=(),
        reproduction_reference=f"probe://{clause_id}/reproduce",
        blocker="required observation was not supplied",
        anti_cheat_passed=False if anti_cheat else None,
    )


def _out_of_scope(clause_id: str) -> ClauseResult:
    return ClauseResult(
        clause_id=clause_id,
        status=ClauseStatus.OUT_OF_SCOPE,
        evidence=(),
        reproduction_reference=None,
        blocker=None,
        anti_cheat_passed=None,
    )


def run_contract(
    binding: AcceptedContract,
    observations: Mapping[str, Observation],
    context: ExecutionContext,
    *,
    cache: ClauseCache | None = None,
    clause_ids: tuple[str, ...] | None = None,
) -> BehaviorReport:
    if binding.implementation_started_event is None:
        raise ValueError("contract must be accepted and bound before implementation starts")
    if canonical_hash(binding.contract) != binding.contract_hash:
        raise ValueError("bound contract hash does not match immutable contract content")
    clauses = {clause.id: clause for clause in binding.contract.clauses}
    selected = clause_ids or tuple(clauses)
    if not isinstance(selected, tuple):
        raise ValueError("selected clause ids must be immutable")
    unknown = set(selected).difference(clauses)
    if unknown:
        raise ValueError(f"unknown selected clauses: {', '.join(sorted(unknown))}")
    if len(set(selected)) != len(selected):
        raise ValueError("selected clause ids must be unique")
    unexpected_observations = set(observations).difference(clauses)
    if unexpected_observations:
        raise ValueError("observations may only name contract clauses")

    results: list[ClauseResult] = []
    for clause_id in selected:
        clause = clauses[clause_id]
        if clause.out_of_scope_reason is not None:
            results.append(_out_of_scope(clause_id))
            continue
        observed = observations.get(clause_id)
        if observed is None:
            results.append(_blocked(clause_id, anti_cheat=clause.anti_cheat))
            continue
        key = CacheKey(
            target_artifact_hash=context.target_artifact_hash,
            clause_hash=canonical_hash(clause),
            executor_version=context.executor_version,
            profile_image_hash=context.profile_image_hash,
        )
        cached = cache.get(key) if cache is not None else None
        if cached is not None:
            results.append(replace(cached, cache_hit=True))
            continue
        result = execute_clause(clause, observed)
        if cache is not None:
            cache.put(key, result)
        results.append(result)

    return BehaviorReport(
        schema=REPORT_SCHEMA,
        contract_hash=binding.contract_hash,
        contract_version=binding.contract.contract_version,
        owner_reference=binding.contract.owner.reference,
        target_artifact_hash=context.target_artifact_hash,
        profile_image_hash=context.profile_image_hash,
        executor_version=context.executor_version,
        selected_clause_ids=selected,
        results=tuple(results),
    )
