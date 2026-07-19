"""Serializable contracts for one immutable review subject."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

TrustLevel = Literal["trusted", "untrusted"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReviewSubjectBlockerCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    PR_METADATA_UNAVAILABLE = "pr_metadata_unavailable"
    STALE_BASE_SHA = "stale_base_sha"
    STALE_HEAD_SHA = "stale_head_sha"
    REPOSITORY_PREP_FAILED = "repository_prep_failed"
    MISSING_COMMIT = "missing_commit"
    CHECKOUT_MISMATCH = "checkout_mismatch"
    MERGE_BASE_FAILED = "merge_base_failed"
    DIFF_MATERIALIZATION_FAILED = "diff_materialization_failed"
    TRUSTED_INSTRUCTIONS_UNAVAILABLE = "trusted_instructions_unavailable"
    TRUSTED_SKILLS_UNAVAILABLE = "trusted_skills_unavailable"
    ARTIFACT_WRITE_FAILED = "artifact_write_failed"


class ReviewSubjectBlocked(RuntimeError):
    """Fail-closed materialization result with a stable machine code."""

    def __init__(self, code: ReviewSubjectBlockerCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str
    bytes: int
    trust: TrustLevel

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("artifact path is required")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("artifact sha256 must be 64 lowercase hexadecimal characters")
        if self.bytes < 0:
            raise ValueError("artifact byte count cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "trust": self.trust,
        }


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("agent definition id is required")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("agent definition sha256 is invalid")

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "sha256": self.sha256}


@dataclass(frozen=True)
class LaneInputLimits:
    max_diff_bytes: int
    max_changed_files: int
    max_instruction_bytes: int
    max_review_threads: int

    def __post_init__(self) -> None:
        values = (
            self.max_diff_bytes,
            self.max_changed_files,
            self.max_instruction_bytes,
            self.max_review_threads,
        )
        if any(value <= 0 for value in values):
            raise ValueError("lane input limits must be positive")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_changed_files": self.max_changed_files,
            "max_diff_bytes": self.max_diff_bytes,
            "max_instruction_bytes": self.max_instruction_bytes,
            "max_review_threads": self.max_review_threads,
        }


@dataclass(frozen=True)
class ValidationReference:
    command: str
    result: ArtifactRef

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("validation command is required")

    def to_dict(self) -> dict[str, object]:
        return {"command": self.command, "result": self.result.to_dict()}


@dataclass(frozen=True)
class ReviewSubjectRequest:
    owner: str
    repo: str
    pr_number: int
    base_sha: str
    head_sha: str
    artifact_root: str
    review_policy_version: str
    agent_definitions: tuple[AgentDefinition, ...]
    lane_input_limits: LaneInputLimits
    last_reviewed_sha: str = ""
    validations: tuple[ValidationReference, ...] = ()
    behavior_contract: ArtifactRef | None = None
    behavior_report: ArtifactRef | None = None
    run_trace_digest: ArtifactRef | None = None
    administrator_skill_refs: tuple[ArtifactRef, ...] = ()
    allow_missing_root_instructions: bool = False


@dataclass(frozen=True)
class ReviewLaneInput:
    checkout_path: str
    head_sha: str
    base_sha: str
    trusted_context_artifact: ArtifactRef
    output_location: str
    review_subject_hash: str
    run_trace_digest: ArtifactRef | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "base_sha": self.base_sha,
            "checkout_path": self.checkout_path,
            "head_sha": self.head_sha,
            "output_location": self.output_location,
            "review_subject_hash": self.review_subject_hash,
            "trusted_context_artifact": self.trusted_context_artifact.to_dict(),
        }
        if self.run_trace_digest is not None:
            payload["run_trace_digest"] = self.run_trace_digest.to_dict()
        return payload


@dataclass(frozen=True)
class MaterializedReviewSubject:
    manifest_path: str
    subject_hash: str
    manifest: dict[str, object]
    checkout_path: str
    base_sha: str
    head_sha: str
    trusted_context_artifact: ArtifactRef
    run_trace_digest: ArtifactRef | None

    def lane_input(
        self,
        *,
        output_location: str,
        lane: Literal["finder", "verifier"] = "finder",
        include_run_trace: bool = False,
    ) -> ReviewLaneInput:
        if not output_location:
            raise ValueError("output_location is required")
        if lane == "finder" and include_run_trace:
            raise ValueError("initial finder inputs cannot include run-trace-digest")
        trace = self.run_trace_digest if lane == "verifier" and include_run_trace else None
        return ReviewLaneInput(
            checkout_path=self.checkout_path,
            head_sha=self.head_sha,
            base_sha=self.base_sha,
            trusted_context_artifact=self.trusted_context_artifact,
            output_location=output_location,
            review_subject_hash=self.subject_hash,
            run_trace_digest=trace,
        )
