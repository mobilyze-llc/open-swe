from __future__ import annotations

from collections.abc import Iterable

from agent.mobilyze.behavior.codec import canonical_hash
from agent.mobilyze.behavior.models import BehaviorContract
from agent.mobilyze.behavior.report import BehaviorReport, ClauseStatus


def targeted_clause_ids(
    contract: BehaviorContract,
    *,
    affected_clause_ids: Iterable[str] = (),
    prior_report: BehaviorReport | None = None,
) -> tuple[str, ...]:
    known = {clause.id for clause in contract.clauses}
    selected = set(affected_clause_ids)
    unknown = selected.difference(known)
    if unknown:
        raise ValueError(f"targeted rerun names unknown clauses: {', '.join(sorted(unknown))}")
    if prior_report is not None:
        if prior_report.contract_hash != canonical_hash(contract):
            raise ValueError("prior report does not belong to this contract")
        selected.update(
            result.clause_id
            for result in prior_report.results
            if result.status in {ClauseStatus.FAIL, ClauseStatus.BLOCKED}
        )
    adjacent = {
        adjacent_id
        for clause in contract.clauses
        if clause.id in selected
        for adjacent_id in clause.adjacent_clause_ids
    }
    selected.update(adjacent)
    return tuple(
        clause.id
        for clause in contract.clauses
        if clause.id in selected and clause.probe is not None
    )
