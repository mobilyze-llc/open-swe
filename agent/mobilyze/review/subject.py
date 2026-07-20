from __future__ import annotations

import posixpath
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import Field, model_validator

from agent.mobilyze.harness.contracts import PersistedContract
from agent.mobilyze.review.artifacts import (
    canonical_json_bytes,
    make_artifact,
    manifest_bytes,
    semantic_subject_hash,
    sha256_bytes,
    upload_artifacts,
)
from agent.mobilyze.review.contracts import (
    GIT_SHA_PATTERN,
    ArtifactTrust,
    BlockerCode,
    DegradedCode,
    DegradedField,
    RepositoryIdentity,
    ReviewArtifact,
    ReviewPolicy,
    ReviewRange,
    ReviewSubject,
    ReviewSubjectBlocker,
    ReviewSubjectMaterialization,
    ValidationReference,
)
from agent.mobilyze.review.repository import (
    PreparedReview,
    ReviewPreparationError,
    checkout_matches_head,
    prepare_exact_review,
)
from agent.mobilyze.review.trusted_context import TrustedContextError
from agent.review.diff import fetch_pr_metadata, materialize_review_diff
from agent.review.publish import fetch_pr_review_threads
from agent.utils.github_ci import fetch_pr


class ReviewSubjectRequest(PersistedContract):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_number: int = Field(gt=0)
    base_sha: str = Field(pattern=GIT_SHA_PATTERN)
    head_sha: str = Field(pattern=GIT_SHA_PATTERN)
    expected_merge_base_sha: str | None = Field(default=None, pattern=GIT_SHA_PATTERN)
    last_reviewed_sha: str | None = Field(default=None, pattern=GIT_SHA_PATTERN)
    re_review: bool = False
    work_dir: str = Field(min_length=1)
    artifact_root: str = Field(min_length=1)
    policy: ReviewPolicy
    validations: tuple[ValidationReference, ...] = ()
    behavior_contract: ReviewArtifact | None = None
    behavior_report: ReviewArtifact | None = None
    run_trace_digest: ReviewArtifact | None = None

    @model_validator(mode="after")
    def validate_re_review(self) -> ReviewSubjectRequest:
        if self.re_review and self.last_reviewed_sha is None:
            raise ValueError("re-review requires last_reviewed_sha")
        if not self.re_review and self.last_reviewed_sha is not None:
            raise ValueError("last_reviewed_sha is only valid for re-review")
        return self


def _block(code: BlockerCode, detail: str) -> ReviewSubjectMaterialization:
    return ReviewSubjectMaterialization(blockers=(ReviewSubjectBlocker(code=code, detail=detail),))


def _pr_identity(payload: dict[str, Any]) -> tuple[int | None, str, str, str | None]:
    base = payload.get("base")
    head = payload.get("head")
    head_repo = head.get("repo") if isinstance(head, dict) else None
    return (
        payload.get("number") if isinstance(payload.get("number"), int) else None,
        base.get("sha", "") if isinstance(base, dict) and isinstance(base.get("sha"), str) else "",
        head.get("sha", "") if isinstance(head, dict) and isinstance(head.get("sha"), str) else "",
        head_repo.get("full_name")
        if isinstance(head_repo, dict) and isinstance(head_repo.get("full_name"), str)
        else None,
    )


def _identity_matches(payload: dict[str, Any], request: ReviewSubjectRequest) -> bool:
    number, base_sha, head_sha, _ = _pr_identity(payload)
    base = payload.get("base")
    base_repo = base.get("repo") if isinstance(base, dict) else None
    full_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    return (
        number == request.pr_number
        and full_name == f"{request.owner}/{request.repo}"
        and base_sha == request.base_sha
        and head_sha == request.head_sha
    )


def _artifact_values(
    prepared: PreparedReview,
    pr_content: bytes,
    thread_content: bytes,
) -> list[tuple[ReviewArtifact, bytes]]:
    files_content = canonical_json_bytes(prepared.changed_files)
    lines_content = canonical_json_bytes(
        {
            path: {side: sorted(lines) for side, lines in sides.items()}
            for path, sides in prepared.changed_lines.items()
        }
    )
    values = (
        ("changed-files", files_content, ArtifactTrust.TRUSTED),
        ("changed-lines", lines_content, ArtifactTrust.TRUSTED),
        ("pr-metadata", pr_content, ArtifactTrust.UNTRUSTED),
        ("review-threads", thread_content, ArtifactTrust.UNTRUSTED),
        ("trusted-context", prepared.trusted_context.content, ArtifactTrust.TRUSTED),
    )
    return [
        (
            make_artifact(
                role=role,
                content=content,
                suffix=".json",
                media_type="application/json",
                trust=trust,
            ),
            content,
        )
        for role, content, trust in values
    ]


async def materialize_review_subject(
    sandbox_backend: SandboxBackendProtocol,
    request: ReviewSubjectRequest,
    *,
    token: str,
) -> ReviewSubjectMaterialization:
    """Materialize one immutable, SHA-bound review subject."""
    try:
        initial_pr = await fetch_pr(
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
            token=token,
        )
    except Exception:
        initial_pr = None
    if initial_pr is None:
        return _block(BlockerCode.PR_UNAVAILABLE, "pull request metadata is unavailable")
    if not _identity_matches(initial_pr, request):
        return _block(BlockerCode.IDENTITY_MISMATCH, "pull request identity does not match request")
    try:
        prepared = await prepare_exact_review(
            sandbox_backend,
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
            base_sha=request.base_sha,
            head_sha=request.head_sha,
            expected_merge_base_sha=request.expected_merge_base_sha,
            last_reviewed_sha=request.last_reviewed_sha,
            re_review=request.re_review,
            work_dir=request.work_dir,
            max_trusted_context_bytes=request.policy.lane_limits.max_trusted_context_bytes,
            allow_missing_root_instructions=request.policy.allow_missing_root_instructions,
        )
    except (ReviewPreparationError, TrustedContextError) as exc:
        return _block(exc.code, exc.detail)
    except Exception as exc:
        return _block(BlockerCode.REPOSITORY_PREP_FAILED, f"review preparation failed: {exc}")

    try:
        metadata = await fetch_pr_metadata(
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
            token=token,
        )
    except Exception:
        metadata = None
    if metadata is None:
        return _block(BlockerCode.PR_UNAVAILABLE, "pull request title and body are unavailable")
    degraded = list(prepared.trusted_context.degraded)
    try:
        threads = await fetch_pr_review_threads(
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
            token=token,
        )
    except Exception:
        threads = []
        degraded.append(
            DegradedField(
                code=DegradedCode.REVIEW_THREADS_UNAVAILABLE,
                field="review_threads",
                detail="review threads could not be loaded",
            )
        )
    else:
        degraded.append(
            DegradedField(
                code=DegradedCode.REVIEW_THREADS_UNVERIFIED,
                field="review_threads",
                detail="review thread snapshot is best-effort and completeness is unverified",
            )
        )
    try:
        final_pr = await fetch_pr(
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
            token=token,
        )
    except Exception:
        final_pr = None
    if final_pr is None or not _identity_matches(final_pr, request):
        return _block(
            BlockerCode.SUBJECT_CHANGED, "pull request SHAs changed during materialization"
        )

    pr_content = canonical_json_bytes({"title": metadata[0], "body": metadata[1]})
    thread_content = canonical_json_bytes(threads)
    try:
        materialized_diff = await materialize_review_diff(
            sandbox_backend,
            work_dir=request.artifact_root,
            base_ref=prepared.diff_base_sha,
            head_ref=prepared.diff_head_sha,
            merge_base=prepared.diff_uses_merge_base,
            diff_text=prepared.diff_text,
        )
    except Exception as exc:
        return _block(BlockerCode.ARTIFACT_WRITE_FAILED, f"diff artifact failed: {exc}")
    diff_bytes = prepared.diff_text.encode("utf-8")
    diff_artifact = ReviewArtifact(
        role="unified-diff",
        uri=posixpath.relpath(materialized_diff.path, request.artifact_root),
        sha256=sha256_bytes(diff_bytes),
        byte_length=len(diff_bytes),
        media_type="text/x-diff",
        trust=ArtifactTrust.TRUSTED,
    )
    artifacts_and_content = _artifact_values(prepared, pr_content, thread_content)
    try:
        await upload_artifacts(
            sandbox_backend,
            artifact_root=request.artifact_root,
            values=artifacts_and_content,
        )
    except Exception as exc:
        return _block(BlockerCode.ARTIFACT_WRITE_FAILED, f"artifact upload failed: {exc}")

    if not await checkout_matches_head(sandbox_backend, prepared.repo_dir, request.head_sha):
        return _block(
            BlockerCode.CHECKOUT_MISMATCH,
            "checkout changed before review-subject persistence",
        )

    _, _, _, head_repository = _pr_identity(initial_pr)
    subject = ReviewSubject(
        repository=RepositoryIdentity(
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
            head_repository=head_repository,
        ),
        review_range=ReviewRange(
            base_sha=request.base_sha,
            head_sha=request.head_sha,
            merge_base_sha=prepared.merge_base_sha,
            diff_base_sha=prepared.diff_base_sha,
            diff_head_sha=prepared.diff_head_sha,
            is_re_review=request.re_review,
            diff_uses_merge_base=prepared.diff_uses_merge_base,
        ),
        diff=diff_artifact,
        changed_files=artifacts_and_content[0][0],
        changed_lines=artifacts_and_content[1][0],
        pr_metadata=artifacts_and_content[2][0],
        review_threads=artifacts_and_content[3][0],
        trusted_context=artifacts_and_content[4][0],
        policy=request.policy,
        validations=request.validations,
        behavior_contract=request.behavior_contract,
        behavior_report=request.behavior_report,
        run_trace_digest=request.run_trace_digest,
        degraded=tuple(degraded),
        subject_hash="0" * 64,
    )
    subject = subject.model_copy(
        update={"subject_hash": semantic_subject_hash(subject.to_persisted_dict())}
    )
    subject_content = manifest_bytes(subject)
    if len(subject_content) > request.policy.lane_limits.max_manifest_bytes:
        return _block(
            BlockerCode.LANE_LIMIT_EXCEEDED,
            "review-subject manifest exceeds the caller-declared limit",
        )
    manifest_artifact = make_artifact(
        role="review-subject",
        content=subject_content,
        suffix=".json",
        media_type="application/json",
        trust=ArtifactTrust.TRUSTED,
    )
    try:
        await upload_artifacts(
            sandbox_backend,
            artifact_root=request.artifact_root,
            values=((manifest_artifact, subject_content),),
        )
    except Exception as exc:
        return _block(BlockerCode.ARTIFACT_WRITE_FAILED, f"manifest upload failed: {exc}")
    if not await checkout_matches_head(sandbox_backend, prepared.repo_dir, request.head_sha):
        return _block(
            BlockerCode.CHECKOUT_MISMATCH,
            "checkout changed during review-subject persistence",
        )
    return ReviewSubjectMaterialization(
        subject=subject,
        manifest_artifact=manifest_artifact,
    )
