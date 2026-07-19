"""Deterministically materialize ``mobilyze.review-subject.v1``."""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from agent.review.diff import (
    changed_files,
    compute_diff_line_set,
    fetch_pr_diff,
    fetch_pr_metadata,
    materialize_review_diff,
    review_diff_range,
)
from agent.review.publish import fetch_pr_review_threads
from agent.utils.agents_md import fetch_agents_md, fetch_scoped_agents_md
from agent.utils.github_ci import fetch_pr
from agent.utils.repo_prep import prepare_review_repo

from .artifacts import ReviewSubjectMaterials, persist_review_subject
from .contracts import (
    AgentDefinition,
    ArtifactRef,
    LaneInputLimits,
    MaterializedReviewSubject,
    ReviewLaneInput,
    ReviewSubjectBlocked,
    ReviewSubjectBlockerCode,
    ReviewSubjectRequest,
    ValidationReference,
)
from .trusted_sources import git_value, root_instruction_path, trusted_skill_records

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_AGENT_DEFINITIONS = 16
_MAX_ADMIN_SKILL_REFS = 32
_MAX_VALIDATIONS = 32


def _validate_request(request: ReviewSubjectRequest) -> None:
    if not _NAME_RE.fullmatch(request.owner) or not _NAME_RE.fullmatch(request.repo):
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.INVALID_INPUT, "repository owner and name are invalid"
        )
    if request.pr_number <= 0:
        raise ReviewSubjectBlocked(ReviewSubjectBlockerCode.INVALID_INPUT, "PR number is invalid")
    for label, sha in (
        ("base", request.base_sha),
        ("head", request.head_sha),
        ("last reviewed", request.last_reviewed_sha),
    ):
        if sha and not _FULL_SHA_RE.fullmatch(sha):
            raise ReviewSubjectBlocked(
                ReviewSubjectBlockerCode.INVALID_INPUT, f"{label} SHA must be a full Git SHA"
            )
    if not request.artifact_root or not request.review_policy_version:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.INVALID_INPUT,
            "artifact root and review policy version are required",
        )
    if len(request.agent_definitions) > _MAX_AGENT_DEFINITIONS:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.INVALID_INPUT, "too many Agent Definitions"
        )
    if len(request.administrator_skill_refs) > _MAX_ADMIN_SKILL_REFS:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.INVALID_INPUT, "too many administrator skill references"
        )
    if len(request.validations) > _MAX_VALIDATIONS:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.INVALID_INPUT, "too many validation references"
        )
    if any(ref.trust != "trusted" for ref in request.administrator_skill_refs):
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.INVALID_INPUT,
            "administrator skill references must be marked trusted",
        )
    if request.run_trace_digest is not None and request.run_trace_digest.trust != "untrusted":
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.INVALID_INPUT, "run-trace-digest must be marked untrusted"
        )


def _normalized_line_map(diff_text: str) -> dict[str, dict[str, list[int]]]:
    return {
        path: {side: sorted(lines) for side, lines in sorted(sides.items())}
        for path, sides in sorted(compute_diff_line_set(diff_text).items())
    }


async def materialize_review_subject(
    backend: SandboxBackendProtocol,
    *,
    github_token: str,
    work_dir: str,
    request: ReviewSubjectRequest,
) -> MaterializedReviewSubject:
    """Materialize a SHA-bound subject before any review model is invoked."""
    _validate_request(request)
    base_sha = request.base_sha.lower()
    head_sha = request.head_sha.lower()
    last_reviewed_sha = request.last_reviewed_sha.lower()

    pr = await fetch_pr(
        owner=request.owner, repo=request.repo, pr_number=request.pr_number, token=github_token
    )
    if pr is None:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.PR_METADATA_UNAVAILABLE, "live PR metadata is unavailable"
        )
    live_base = str((pr.get("base") or {}).get("sha") or "").lower()
    live_head = str((pr.get("head") or {}).get("sha") or "").lower()
    if live_base != base_sha:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.STALE_BASE_SHA,
            f"requested base {base_sha} does not match live PR base {live_base or '<missing>'}",
        )
    if live_head != head_sha:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.STALE_HEAD_SHA,
            f"requested head {head_sha} does not match live PR head {live_head or '<missing>'}",
        )

    repo_ready = await prepare_review_repo(
        backend,
        work_dir=work_dir,
        repo_owner=request.owner,
        repo_name=request.repo,
        head_sha=head_sha,
        pr_number=request.pr_number,
        base_sha=base_sha,
    )
    if not repo_ready:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.REPOSITORY_PREP_FAILED,
            "repository preparation did not produce the requested checkout",
        )
    repo_dir = posixpath.join(work_dir, request.repo)
    checkout_sha = await git_value(
        backend, repo_dir, "git rev-parse HEAD", ReviewSubjectBlockerCode.CHECKOUT_MISMATCH
    )
    if checkout_sha.lower() != head_sha:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.CHECKOUT_MISMATCH,
            f"checkout is at {checkout_sha}, expected {head_sha}",
        )
    for sha in dict.fromkeys((base_sha, head_sha, last_reviewed_sha)):
        if sha:
            await git_value(
                backend,
                repo_dir,
                f"git rev-parse {sha}^{{commit}}",
                ReviewSubjectBlockerCode.MISSING_COMMIT,
            )
    merge_base_sha = await git_value(
        backend,
        repo_dir,
        f"git merge-base {base_sha} {head_sha}",
        ReviewSubjectBlockerCode.MERGE_BASE_FAILED,
    )

    re_review = bool(last_reviewed_sha)
    diff_base, diff_head, use_merge_base = review_diff_range(
        base_sha=base_sha,
        head_sha=head_sha,
        last_reviewed_sha=last_reviewed_sha,
        re_review=re_review,
    )
    supplied_diff = None
    if not re_review:
        supplied_diff = await fetch_pr_diff(
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
            token=github_token,
        )
        if supplied_diff is None:
            raise ReviewSubjectBlocked(
                ReviewSubjectBlockerCode.DIFF_MATERIALIZATION_FAILED,
                "canonical PR diff is unavailable",
            )
    try:
        diff = await materialize_review_diff(
            backend,
            work_dir=repo_dir,
            base_ref=diff_base,
            head_ref=diff_head,
            merge_base=use_merge_base,
            diff_text=supplied_diff,
        )
    except (RuntimeError, ValueError) as exc:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.DIFF_MATERIALIZATION_FAILED, str(exc)
        ) from exc

    files = changed_files(diff.diff_text)
    line_map = _normalized_line_map(diff.diff_text)
    metadata = await fetch_pr_metadata(
        owner=request.owner,
        repo=request.repo,
        pr_number=request.pr_number,
        token=github_token,
    )
    if metadata is None:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.PR_METADATA_UNAVAILABLE, "PR title and body are unavailable"
        )
    threads = await fetch_pr_review_threads(
        owner=request.owner,
        repo=request.repo,
        pr_number=request.pr_number,
        token=github_token,
        max_threads=request.lane_input_limits.max_review_threads,
    )

    root_instructions = await fetch_agents_md(
        request.owner, request.repo, base_sha, token=github_token
    )
    degradations: list[str] = []
    if root_instructions is None:
        if not request.allow_missing_root_instructions:
            raise ReviewSubjectBlocked(
                ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
                "trusted root AGENTS.md/CLAUDE.md is unavailable at the base SHA",
            )
        degradations.append("missing_root_instructions_policy_approved")
    root_path = (
        await root_instruction_path(backend, repo_dir, base_sha)
        if root_instructions is not None
        else None
    )
    scoped_instructions = await fetch_scoped_agents_md(
        request.owner, request.repo, base_sha, files, token=github_token
    )
    skill_records = await trusted_skill_records(backend, repo_dir, base_sha)

    return await persist_review_subject(
        backend,
        request=request,
        materials=ReviewSubjectMaterials(
            repo_dir=repo_dir,
            base_sha=base_sha,
            head_sha=head_sha,
            merge_base_sha=merge_base_sha.lower(),
            diff_base_sha=diff_base,
            re_review=re_review,
            diff_text=diff.diff_text,
            changed_files=files,
            changed_lines=line_map,
            pr_title=metadata[0],
            pr_body=metadata[1],
            review_threads=threads,
            root_instructions=root_instructions,
            root_instruction_path=root_path,
            scoped_instructions=scoped_instructions,
            repository_skill_refs=skill_records,
            degradations=degradations,
        ),
    )


__all__ = [
    "AgentDefinition",
    "ArtifactRef",
    "LaneInputLimits",
    "MaterializedReviewSubject",
    "ReviewLaneInput",
    "ReviewSubjectBlocked",
    "ReviewSubjectBlockerCode",
    "ReviewSubjectRequest",
    "ValidationReference",
    "materialize_review_subject",
]
