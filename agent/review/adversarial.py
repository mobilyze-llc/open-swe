"""Schemas and deterministic policies for adversarial review orchestration."""

from __future__ import annotations

import operator
import shlex
from collections import Counter
from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypedDict

from langgraph.types import Overwrite
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .findings import SEVERITY_ORDER

NON_PRODUCTION_PREFIXES = frozenset({"docs", "tests", "evals", ".github"})
ROOT_DOC_SUFFIXES = (".md", ".rst", ".txt")
ROOT_DOC_NAMES = frozenset({"LICENSE", "NOTICE"})


class FinderRun(TypedDict):
    finder: str
    candidates: list[dict[str, Any]]
    error: str | None


class AdversarialState(TypedDict, total=False):
    work_dir: str
    working_dir: str
    rendered_system_prompt: str
    stage_context: str
    diff_text: str
    diff_line_set: dict[str, Any] | None
    diff_path: str
    pr_title: str
    finders_expected: list[str]
    finder_name: str
    finder_results: Annotated[list[FinderRun], operator.add]
    candidates: list[dict[str, Any]]
    verdicts: list[dict[str, Any]]
    kept_candidates: list[dict[str, Any]]
    gate_triggers: list[str]
    gate_candidates: list[dict[str, Any]]
    gate_verdicts: list[dict[str, Any]]
    publication: dict[str, Any]
    error: str


class CandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quoted_line: str = Field(min_length=1)
    failure_mode: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    category: str = Field(default="correctness", min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> CandidateDraft:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self


class Candidate(CandidateDraft):
    candidate_id: str


class FinderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateDraft] = Field(default_factory=list)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    verdict: Literal["keep-confirmed", "keep-plausible", "kill"]
    evidence: str


class VerdictBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[Verdict]


class IndependenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str]
    independent: bool
    keep_candidate_ids: list[str] = Field(default_factory=list)
    rationale: str


class GateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateDraft] = Field(default_factory=list)
    independence: list[IndependenceDecision] = Field(default_factory=list)


def reset_run_state(prepared: Mapping[str, Any], finder_names: list[str]) -> dict[str, Any]:
    return {
        **prepared,
        "finders_expected": finder_names,
        "finder_results": Overwrite([]),
        "candidates": [],
        "verdicts": [],
        "kept_candidates": [],
        "gate_triggers": [],
        "gate_candidates": [],
        "gate_verdicts": [],
        "publication": {},
        "error": "",
    }


def configured_model_pair(
    configurable: Mapping[str, Any], is_eval: bool, namespaced: str, fallback: str
) -> tuple[str, str | None] | None:
    model = configurable.get(f"{namespaced}_model_id")
    effort_key = f"{namespaced}_reasoning_effort"
    if not (isinstance(model, str) and model) and is_eval:
        model = configurable.get(f"{fallback}_model_id")
        effort_key = f"{fallback}_reasoning_effort"
    if not isinstance(model, str) or not model:
        return None
    effort = configurable.get(effort_key)
    return model, effort if isinstance(effort, str) else None


def apply_independence(
    kept: list[dict[str, Any]],
    collisions: list[list[str]],
    decisions: list[IndependenceDecision],
) -> list[dict[str, Any]]:
    collision_keys = {tuple(sorted(collision)) for collision in collisions}
    by_ids = {tuple(sorted(item.candidate_ids)): item for item in decisions}
    if set(by_ids) != collision_keys:
        raise RuntimeError("same-file gate must cover exactly every collision")
    allowed: set[str] = set()
    collided = {candidate_id for collision in collisions for candidate_id in collision}
    for collision in collisions:
        collision_ids = set(collision)
        decision = by_ids[tuple(sorted(collision))]
        kept_ids = set(decision.keep_candidate_ids)
        if not kept_ids <= collision_ids:
            raise RuntimeError("same-file gate returned an unknown candidate ID")
        if not decision.independent and len(kept_ids) > 1:
            raise RuntimeError("non-independent findings may keep at most one candidate")
        allowed.update(collision_ids if decision.independent else kept_ids)
    return [
        item
        for item in kept
        if item["candidate_id"] not in collided or item["candidate_id"] in allowed
    ]


def dedupe_candidates(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[CandidateDraft] = []
    for raw in drafts:
        draft = CandidateDraft.model_validate(raw)
        key = " ".join(draft.failure_mode.casefold().split())
        duplicate = next(
            (
                item
                for item in merged
                if item.file == draft.file
                and " ".join(item.failure_mode.casefold().split()) == key
                and item.start_line <= draft.end_line
                and draft.start_line <= item.end_line
            ),
            None,
        )
        if duplicate is None:
            merged.append(draft)
        else:
            duplicate.start_line = min(duplicate.start_line, draft.start_line)
            duplicate.end_line = max(duplicate.end_line, draft.end_line)
            if SEVERITY_ORDER[draft.severity] > SEVERITY_ORDER[duplicate.severity]:
                duplicate.severity = draft.severity
    merged.sort(key=lambda item: (item.file, item.start_line, item.end_line, item.failure_mode))
    return [
        Candidate(candidate_id=f"c{index + 1}", **item.model_dump()).model_dump()
        for index, item in enumerate(merged)
    ]


def validate_verdicts(
    candidates: list[dict[str, Any]], verdicts: list[dict[str, Any]]
) -> dict[str, Verdict]:
    expected = {Candidate.model_validate(item).candidate_id for item in candidates}
    parsed = [Verdict.model_validate(item) for item in verdicts]
    received = [item.candidate_id for item in parsed]
    if len(received) != len(set(received)) or set(received) != expected:
        raise RuntimeError("adjudication verdicts must cover every candidate ID exactly once")
    return {item.candidate_id: item for item in parsed}


def publication_blocker(state: Mapping[str, Any]) -> str | None:
    results = state.get("finder_results", [])
    expected = set(state.get("finders_expected", []))
    result_names = [item["finder"] for item in results]
    if (
        len(result_names) != len(set(result_names))
        or set(result_names) != expected
        or any(item["error"] for item in results)
    ):
        return "finder fanout incomplete or failed"
    try:
        validate_verdicts(state.get("candidates", []), state.get("verdicts", []))
    except RuntimeError as exc:
        return str(exc)
    gate_candidates = state.get("gate_candidates", [])
    if gate_candidates:
        try:
            validate_verdicts(gate_candidates, state.get("gate_verdicts", []))
        except RuntimeError as exc:
            return str(exc)
    return None


def changed_prefix_counts(diff_text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    current = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = []
            current = parts[3][2:] if len(parts) == 4 and parts[3].startswith("b/") else ""
        elif (
            current
            and (line.startswith("+") or line.startswith("-"))
            and not line.startswith(("+++", "---"))
        ):
            counts[current.split("/", 1)[0]] += 1
    return counts


def gate_triggers(diff_text: str, kept: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    counts = changed_prefix_counts(diff_text)
    production = any(
        prefix not in NON_PRODUCTION_PREFIXES
        and prefix not in ROOT_DOC_NAMES
        and not prefix.casefold().endswith(ROOT_DOC_SUFFIXES)
        for prefix in counts
    )
    triggers: list[str] = []
    if production and not kept:
        triggers.append("zero-findings")
    if counts:
        maximum = max(counts.values())
        major = {prefix for prefix, count in counts.items() if count == maximum}
        covered = {str(item["file"]).split("/", 1)[0] for item in kept}
        if major - covered:
            triggers.append("uncovered-major-prefix")
    same_file: dict[str, list[str]] = {}
    for item in kept:
        same_file.setdefault(str(item["file"]), []).append(str(item["candidate_id"]))
    collisions = [ids for ids in same_file.values() if len(ids) > 1]
    if collisions:
        triggers.append("same-file-independence")
    return triggers, collisions
