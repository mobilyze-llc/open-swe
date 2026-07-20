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
    environment = (
        "unset GIT_CONFIG GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_EXTERNAL_DIFF "
        "GIT_DIFF_OPTS GIT_ATTR_SOURCE; "
        "export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null "
        "GIT_ATTR_NOSYSTEM=1 GIT_NO_REPLACE_OBJECTS=1"
    )
    return await asyncio.to_thread(
        sandbox_backend.execute,
        f"cd {shlex.quote(repo_dir)} && {environment} && {command}",
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
    """Return whether the checkout is a clean materialization of the exact head."""
    try:
        head_result = await _git(sandbox_backend, repo_dir, "git rev-parse HEAD")
        status_result = await _git(
            sandbox_backend, repo_dir, "git status --porcelain --untracked-files=all"
        )
        ignored_result = await _git(sandbox_backend, repo_dir, "git clean -ndx")
    except Exception:
        return False
    return (
        _succeeded(head_result)
        and _output(head_result).strip() == head_sha
        and _succeeded(status_result)
        and not _output(status_result).strip()
        and _succeeded(ignored_result)
        and not _output(ignored_result).strip()
    )


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
    allow_missing_root_instructions: bool,
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
    try:
        clean_result = await _git(sandbox_backend, repo_dir, "git clean -ffdx")
    except Exception as exc:
        raise ReviewPreparationError(
            BlockerCode.CHECKOUT_MISMATCH, "failed to clean the exact review checkout"
        ) from exc
    if not _succeeded(clean_result) or not await checkout_matches_head(
        sandbox_backend, repo_dir, head_sha
    ):
        raise ReviewPreparationError(
            BlockerCode.CHECKOUT_MISMATCH, "checkout contains content outside the exact head"
        )
    for sha in dict.fromkeys((base_sha, head_sha, diff_base)):
        try:
            result = await _git(
                sandbox_backend,
                repo_dir,
                f"git cat-file -e {shlex.quote(sha + '^{commit}')}",
            )
        except Exception as exc:
            raise ReviewPreparationError(
                BlockerCode.COMMIT_UNAVAILABLE, f"failed to verify commit {sha}"
            ) from exc
        if not _succeeded(result):
            raise ReviewPreparationError(
                BlockerCode.COMMIT_UNAVAILABLE, f"commit {sha} is unavailable"
            )
    try:
        merge_result = await _git(
            sandbox_backend,
            repo_dir,
            f"git merge-base {shlex.quote(base_sha)} {shlex.quote(head_sha)}",
        )
    except Exception as exc:
        raise ReviewPreparationError(
            BlockerCode.COMMIT_UNAVAILABLE, "failed to compute the merge base"
        ) from exc
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
    diff_options = (
        "--no-color --no-ext-diff --no-textconv --no-renames --no-indent-heuristic "
        "--full-index --unified=3 --src-prefix=a/ --dst-prefix=b/ --submodule=short"
    )
    sanitize_command = (
        "rm -f .git/info/attributes .git/info/grafts && "
        "git config --local --name-only --get-regexp '^include' | "
        'while IFS= read -r key; do git config --local --unset-all "$key" || true; done && '
        "git config --local core.quotePath false && "
        "git config --local core.attributesFile /dev/null && "
        "git config --local core.useReplaceRefs false && "
        "git config --local --name-only --get-regexp '^diff[.]' | "
        'while IFS= read -r key; do git config --local --unset-all "$key" || true; done'
    )
    try:
        sanitize_result = await _git(sandbox_backend, repo_dir, sanitize_command)
        if not _succeeded(sanitize_result):
            raise RuntimeError("failed to sanitize exact-SHA Git metadata")
        diff_result = await _git(
            sandbox_backend,
            repo_dir,
            f"mkdir -p {shlex.quote(materialization_dir)} && "
            f"git -c core.quotePath=false -c diff.algorithm=myers diff {diff_options} "
            f"{diff_range} -- > {shlex.quote(diff_path)} && "
            f"git -c core.quotePath=false -c diff.algorithm=myers diff {diff_options} "
            f"--name-only -z {diff_range} -- > {shlex.quote(names_path)}",
        )
        if not _succeeded(diff_result):
            raise RuntimeError("fresh exact-SHA diff failed")
        downloaded = await sandbox_backend.adownload_files([diff_path, names_path])
    except Exception as exc:
        raise ReviewPreparationError(
            BlockerCode.DIFF_UNAVAILABLE, "fresh exact-SHA diff failed"
        ) from exc
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
        allow_missing_root_instructions=allow_missing_root_instructions,
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
