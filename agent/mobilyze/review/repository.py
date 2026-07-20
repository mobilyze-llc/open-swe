from __future__ import annotations

import asyncio
import hashlib
import posixpath
import shlex
from dataclasses import dataclass

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.mobilyze.review.contracts import BlockerCode
from agent.mobilyze.review.trusted_context import TrustedContext, materialize_trusted_context
from agent.review.diff import changed_files, compute_diff_line_set, review_diff_range
from agent.utils.repo_prep import prepare_review_repo


class ReviewPreparationError(RuntimeError):
    """Typed exact-repository preparation failure."""

    def __init__(self, code: BlockerCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PreparedReview:
    repo_dir: str
    merge_base_sha: str
    diff_base_sha: str
    diff_head_sha: str
    diff_uses_merge_base: bool
    diff_text: str
    changed_files: list[str]
    changed_lines: dict[str, dict[str, set[int]]]
    trusted_context: TrustedContext


def _output(result: object) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("stdout", "output", "text"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        return ""
    for key in ("stdout", "output", "text"):
        value = getattr(result, key, None)
        if isinstance(value, str):
            return value
    return ""


def _succeeded(result: object) -> bool:
    exit_code = (
        result.get("exit_code") if isinstance(result, dict) else getattr(result, "exit_code", None)
    )
    return exit_code in (0, None)


async def _git(sandbox_backend: SandboxBackendProtocol, repo_dir: str, command: str) -> object:
    return await asyncio.to_thread(
        sandbox_backend.execute,
        f"cd {shlex.quote(repo_dir)} && {command}",
    )


def _download_bytes(response: object) -> bytes | None:
    if isinstance(response, dict):
        content = response.get("content")
        error = response.get("error")
    else:
        content = getattr(response, "content", None)
        error = getattr(response, "error", None)
    if error or content is None:
        return None
    if isinstance(content, bytes):
        return content
    return content.encode("utf-8") if isinstance(content, str) else None


async def checkout_matches_head(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, head_sha: str
) -> bool:
    """Return whether the prepared checkout still points at the exact head."""
    result = await _git(sandbox_backend, repo_dir, "git rev-parse HEAD")
    return _succeeded(result) and _output(result).strip() == head_sha


async def prepare_exact_review(
    sandbox_backend: SandboxBackendProtocol,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    expected_merge_base_sha: str | None,
    last_reviewed_sha: str | None,
    re_review: bool,
    work_dir: str,
    max_trusted_context_bytes: int,
) -> PreparedReview:
    """Prepare and verify the exact checkout, diff, paths, and base context."""
    diff_base, diff_head, merge_base_diff = review_diff_range(
        base_sha=base_sha,
        head_sha=head_sha,
        last_reviewed_sha=last_reviewed_sha or "",
        re_review=re_review,
    )
    prepared = await prepare_review_repo(
        sandbox_backend,
        work_dir=work_dir,
        repo_owner=owner,
        repo_name=repo,
        head_sha=head_sha,
        pr_number=pr_number,
        base_sha=base_sha,
    )
    if not prepared:
        raise ReviewPreparationError(
            BlockerCode.REPOSITORY_PREP_FAILED, "exact review checkout failed"
        )
    repo_dir = posixpath.join(work_dir, repo)
    clean_result = await _git(sandbox_backend, repo_dir, "git clean -ffd")
    status_result = await _git(
        sandbox_backend, repo_dir, "git status --porcelain --untracked-files=all"
    )
    if (
        not _succeeded(clean_result)
        or not _succeeded(status_result)
        or _output(status_result).strip()
    ):
        raise ReviewPreparationError(
            BlockerCode.CHECKOUT_MISMATCH, "checkout contains content outside the exact head"
        )
    head_result = await _git(sandbox_backend, repo_dir, "git rev-parse HEAD")
    if not _succeeded(head_result) or _output(head_result).strip() != head_sha:
        raise ReviewPreparationError(
            BlockerCode.CHECKOUT_MISMATCH, "checkout is not at the exact PR head"
        )
    for sha in dict.fromkeys((base_sha, head_sha, diff_base)):
        result = await _git(
            sandbox_backend,
            repo_dir,
            f"git cat-file -e {shlex.quote(sha + '^{commit}')}",
        )
        if not _succeeded(result):
            raise ReviewPreparationError(
                BlockerCode.COMMIT_UNAVAILABLE, f"commit {sha} is unavailable"
            )
    merge_result = await _git(
        sandbox_backend,
        repo_dir,
        f"git merge-base {shlex.quote(base_sha)} {shlex.quote(head_sha)}",
    )
    merge_base = _output(merge_result).strip()
    if not _succeeded(merge_result) or not merge_base:
        raise ReviewPreparationError(BlockerCode.COMMIT_UNAVAILABLE, "merge base is unavailable")
    if expected_merge_base_sha and merge_base != expected_merge_base_sha:
        raise ReviewPreparationError(
            BlockerCode.IDENTITY_MISMATCH, "computed merge base does not match"
        )

    operator = "..." if merge_base_diff else ".."
    diff_range = f"{diff_base}{operator}{diff_head}"
    range_digest = hashlib.sha256(diff_range.encode()).hexdigest()[:16]
    materialization_dir = posixpath.join(repo_dir, ".git", "mobilyze-review-subject")
    diff_path = posixpath.join(materialization_dir, f"{range_digest}.patch")
    names_path = posixpath.join(materialization_dir, f"{range_digest}.names")
    diff_result = await _git(
        sandbox_backend,
        repo_dir,
        f"mkdir -p {shlex.quote(materialization_dir)} && "
        f"git -c core.quotePath=false diff --no-color {diff_range} -- "
        f"> {shlex.quote(diff_path)} && "
        f"git -c core.quotePath=false diff --name-only -z {diff_range} -- "
        f"> {shlex.quote(names_path)}",
    )
    if not _succeeded(diff_result):
        raise ReviewPreparationError(BlockerCode.DIFF_UNAVAILABLE, "fresh exact-SHA diff failed")
    downloaded = await sandbox_backend.adownload_files([diff_path, names_path])
    diff_bytes = _download_bytes(downloaded[0]) if len(downloaded) > 0 else None
    names_bytes = _download_bytes(downloaded[1]) if len(downloaded) > 1 else None
    if diff_bytes is None or names_bytes is None:
        raise ReviewPreparationError(BlockerCode.DIFF_UNAVAILABLE, "fresh diff download failed")
    try:
        diff_text = diff_bytes.decode("utf-8")
        exact_files = [path for path in names_bytes.decode("utf-8").split("\0") if path]
    except UnicodeDecodeError as exc:
        raise ReviewPreparationError(
            BlockerCode.DIFF_UNAVAILABLE, "fresh diff paths are not valid UTF-8"
        ) from exc
    parsed_files = changed_files(diff_text)
    if parsed_files != exact_files:
        raise ReviewPreparationError(
            BlockerCode.DIFF_PATH_MISMATCH,
            "diff parser paths do not match git name-only output",
        )
    trusted_context = await materialize_trusted_context(
        sandbox_backend,
        repo_dir=repo_dir,
        base_sha=base_sha,
        changed_file_paths=parsed_files,
        max_bytes=max_trusted_context_bytes,
    )
    return PreparedReview(
        repo_dir=repo_dir,
        merge_base_sha=merge_base,
        diff_base_sha=diff_base,
        diff_head_sha=diff_head,
        diff_uses_merge_base=merge_base_diff,
        diff_text=diff_text,
        changed_files=parsed_files,
        changed_lines=compute_diff_line_set(diff_text),
        trusted_context=trusted_context,
    )
