from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field

from agent.mobilyze.behavior.core import BehaviorModel
from agent.mobilyze.behavior.observations import RawEvidence
from agent.mobilyze.behavior.probes import EvidenceKind

MAX_RAW_EVIDENCE_LENGTH = 2048
MAX_EVIDENCE_SUMMARY_LENGTH = 512

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization[\"']?\s*:\s*[\"']?\s*)?(?:basic|bearer)\s+\S+"),
    re.compile(
        r"(?i)\b(?:access[_-]?token|api[_-]?key|password|refresh[_-]?token|secret|session|token)[\"']?\s*[:=]\s*[\"']?\S+"
    ),
    re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:\s*\S+"),
    re.compile(r"\b(?:gh[opsu]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class EvidenceIssueCode(StrEnum):
    OVERLONG = "overlong_evidence"
    SENSITIVE = "sensitive_evidence"
    UNSUPPORTED = "unsupported_evidence"
    MISSING = "missing_evidence"
    DUPLICATE = "duplicate_evidence"
    EMPTY = "empty_evidence"


class EvidenceSummary(BehaviorModel):
    kind: EvidenceKind
    summary: str = Field(min_length=1, max_length=MAX_EVIDENCE_SUMMARY_LENGTH)
    redacted: bool = False


class EvidenceIssue(BehaviorModel):
    code: EvidenceIssueCode
    kind: EvidenceKind
    message: str = Field(min_length=1, max_length=256)


def summarize_evidence(raw: RawEvidence) -> tuple[EvidenceSummary, EvidenceIssue | None]:
    if len(raw.summary) > MAX_RAW_EVIDENCE_LENGTH:
        return (
            EvidenceSummary(kind=raw.kind, summary="<redacted:overlong>", redacted=True),
            EvidenceIssue(
                code=EvidenceIssueCode.OVERLONG,
                kind=raw.kind,
                message="raw evidence exceeds the permitted bound",
            ),
        )
    if any(pattern.search(raw.summary) for pattern in _SENSITIVE_PATTERNS):
        return (
            EvidenceSummary(kind=raw.kind, summary="<redacted:sensitive>", redacted=True),
            EvidenceIssue(
                code=EvidenceIssueCode.SENSITIVE,
                kind=raw.kind,
                message="raw evidence contains sensitive material",
            ),
        )
    normalized = " ".join(raw.summary.split())
    if not normalized:
        return (
            EvidenceSummary(kind=raw.kind, summary="<redacted:empty>", redacted=True),
            EvidenceIssue(
                code=EvidenceIssueCode.EMPTY,
                kind=raw.kind,
                message="raw evidence is empty",
            ),
        )
    if len(normalized) > MAX_EVIDENCE_SUMMARY_LENGTH:
        normalized = normalized[: MAX_EVIDENCE_SUMMARY_LENGTH - 1] + "…"
    return EvidenceSummary(kind=raw.kind, summary=normalized), None


def sensitive_text_issue(kind: EvidenceKind, value: str) -> EvidenceIssue | None:
    if value and any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS):
        return EvidenceIssue(
            code=EvidenceIssueCode.SENSITIVE,
            kind=kind,
            message="observation contains sensitive material",
        )
    return None
