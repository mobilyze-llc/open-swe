from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from agent.mobilyze.harness.contracts import PersistedContract

SHA256_PATTERN = r"^[0-9a-f]{64}$"
GIT_SHA_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"


class ArtifactTrust(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class BlockerCode(StrEnum):
    PR_UNAVAILABLE = "pr_unavailable"
    IDENTITY_MISMATCH = "identity_mismatch"
    REPOSITORY_PREP_FAILED = "repository_prep_failed"
    COMMIT_UNAVAILABLE = "commit_unavailable"
    CHECKOUT_MISMATCH = "checkout_mismatch"
    DIFF_UNAVAILABLE = "diff_unavailable"
    DIFF_PATH_MISMATCH = "diff_path_mismatch"
    TRUSTED_INSTRUCTIONS_UNAVAILABLE = "trusted_instructions_unavailable"
    TRUSTED_SKILLS_UNAVAILABLE = "trusted_skills_unavailable"
    LANE_LIMIT_EXCEEDED = "lane_limit_exceeded"
    ARTIFACT_WRITE_FAILED = "artifact_write_failed"
    SUBJECT_CHANGED = "subject_changed"


class DegradedCode(StrEnum):
    ROOT_INSTRUCTIONS_ABSENT = "root_instructions_absent"
    REVIEW_THREADS_UNAVAILABLE = "review_threads_unavailable"


class ReviewArtifact(PersistedContract):
    role: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    byte_length: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    trust: ArtifactTrust


class RepositoryIdentity(PersistedContract):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_number: int = Field(gt=0)
    head_repository: str | None = Field(default=None, min_length=1)


class ReviewRange(PersistedContract):
    base_sha: str = Field(pattern=GIT_SHA_PATTERN)
    head_sha: str = Field(pattern=GIT_SHA_PATTERN)
    merge_base_sha: str = Field(pattern=GIT_SHA_PATTERN)
    diff_base_sha: str = Field(pattern=GIT_SHA_PATTERN)
    diff_head_sha: str = Field(pattern=GIT_SHA_PATTERN)
    is_re_review: bool
    diff_uses_merge_base: bool


class LaneInputLimits(PersistedContract):
    max_diff_bytes: int = Field(gt=0)
    max_pr_metadata_bytes: int = Field(gt=0)
    max_review_threads_bytes: int = Field(gt=0)
    max_trusted_context_bytes: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    max_manifest_bytes: int = Field(gt=0)


class AgentDefinitionReference(PersistedContract):
    id: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)


class ReviewPolicy(PersistedContract):
    version: str = Field(min_length=1)
    agent_definitions: tuple[AgentDefinitionReference, ...] = Field(min_length=1)
    lane_limits: LaneInputLimits


class ValidationReference(PersistedContract):
    command: str = Field(min_length=1)
    result: ReviewArtifact

    @model_validator(mode="after")
    def validate_result_trust(self) -> ValidationReference:
        if self.result.trust is not ArtifactTrust.TRUSTED:
            raise ValueError("validation results must be trusted")
        return self


class ReviewSubject(PersistedContract):
    schema_version: Literal["mobilyze.review-subject.v1"] = "mobilyze.review-subject.v1"
    repository: RepositoryIdentity
    review_range: ReviewRange
    diff: ReviewArtifact
    changed_files: ReviewArtifact
    changed_lines: ReviewArtifact
    pr_metadata: ReviewArtifact
    review_threads: ReviewArtifact
    trusted_context: ReviewArtifact
    policy: ReviewPolicy
    validations: tuple[ValidationReference, ...] = ()
    behavior_contract: ReviewArtifact | None = None
    behavior_report: ReviewArtifact | None = None
    run_trace_digest: ReviewArtifact | None = None
    degraded: tuple[DegradedField, ...] = ()
    subject_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_artifact_trust(self) -> ReviewSubject:
        trusted = (
            self.diff,
            self.changed_files,
            self.changed_lines,
            self.trusted_context,
            self.behavior_contract,
            self.behavior_report,
        )
        if any(
            artifact is not None and artifact.trust is not ArtifactTrust.TRUSTED
            for artifact in trusted
        ):
            raise ValueError("review evidence and trusted context must be trusted")
        untrusted = (self.pr_metadata, self.review_threads, self.run_trace_digest)
        if any(
            artifact is not None and artifact.trust is not ArtifactTrust.UNTRUSTED
            for artifact in untrusted
        ):
            raise ValueError("PR context and run trace must be untrusted")
        return self


class ReviewSubjectBlocker(PersistedContract):
    code: BlockerCode
    detail: str = Field(min_length=1)


class DegradedField(PersistedContract):
    code: DegradedCode
    field: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class ReviewSubjectMaterialization(PersistedContract):
    subject: ReviewSubject | None = None
    manifest_artifact: ReviewArtifact | None = None
    blockers: tuple[ReviewSubjectBlocker, ...] = ()

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> ReviewSubjectMaterialization:
        succeeded = self.subject is not None and self.manifest_artifact is not None
        if succeeded == bool(self.blockers):
            raise ValueError("materialization must contain either a subject or blockers")
        if (self.subject is None) != (self.manifest_artifact is None):
            raise ValueError("subject and manifest artifact must be present together")
        return self


class FinderAdapterInput(PersistedContract):
    checkout_path: str = Field(min_length=1)
    checkout_head_sha: str = Field(pattern=GIT_SHA_PATTERN)
    base_sha: str = Field(pattern=GIT_SHA_PATTERN)
    diff_base_sha: str = Field(pattern=GIT_SHA_PATTERN)
    diff_head_sha: str = Field(pattern=GIT_SHA_PATTERN)
    diff_uses_merge_base: bool
    diff_path: str = Field(min_length=1)
    diff_sha256: str = Field(pattern=SHA256_PATTERN)
    trusted_context_path: str = Field(min_length=1)
    trusted_context_sha256: str = Field(pattern=SHA256_PATTERN)
    output_path: str = Field(min_length=1)
    subject_hash: str = Field(pattern=SHA256_PATTERN)
    run_trace_digest: ReviewArtifact | None = None
