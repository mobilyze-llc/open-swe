"""Resolve immutable repository identities and trusted skill sources."""

from __future__ import annotations

import asyncio
import shlex
from typing import TYPE_CHECKING

from agent.utils.repo_prep import DEFAULT_SKILL_DIRS, materialize_trusted_skills

from .contracts import ReviewSubjectBlocked, ReviewSubjectBlockerCode

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol


def _result_output(result: object) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        value = result.get("output") or result.get("stdout")
    else:
        value = getattr(result, "output", None) or getattr(result, "stdout", None)
    return value if isinstance(value, str) else ""


def _result_exit_code(result: object) -> int | None:
    if isinstance(result, dict):
        value = result.get("exit_code")
    else:
        value = getattr(result, "exit_code", None)
    return value if isinstance(value, int) else None


async def git_value(
    backend: SandboxBackendProtocol,
    repo_dir: str,
    command: str,
    blocker: ReviewSubjectBlockerCode,
) -> str:
    result = await asyncio.to_thread(backend.execute, f"cd {shlex.quote(repo_dir)} && {command}")
    if _result_exit_code(result) not in (0, None):
        raise ReviewSubjectBlocked(blocker, f"Git identity command failed: {command}")
    value = _result_output(result).strip().splitlines()
    if not value or not value[0].strip():
        raise ReviewSubjectBlocked(blocker, f"Git identity command returned no value: {command}")
    return value[0].strip()


async def root_instruction_path(
    backend: SandboxBackendProtocol, repo_dir: str, base_sha: str
) -> str:
    for path in ("AGENTS.md", "CLAUDE.md"):
        command = f"git cat-file -e {shlex.quote(f'{base_sha}:{path}')}"
        result = await asyncio.to_thread(
            backend.execute, f"cd {shlex.quote(repo_dir)} && {command}"
        )
        if _result_exit_code(result) in (0, None):
            return path
    raise ReviewSubjectBlocked(
        ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
        "trusted root instruction source is absent at the base SHA",
    )


async def trusted_skill_records(
    backend: SandboxBackendProtocol,
    repo_dir: str,
    base_sha: str,
) -> list[dict[str, str]]:
    sources = await materialize_trusted_skills(backend, repo_dir=repo_dir, trusted_ref=base_sha)
    records: list[dict[str, str]] = []
    for source in sources:
        source_dir = next((item for item in DEFAULT_SKILL_DIRS if item in source), None)
        if source_dir is None:
            raise ReviewSubjectBlocked(
                ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
                f"unexpected trusted skill source: {source}",
            )
        git_oid = await git_value(
            backend,
            repo_dir,
            f"git rev-parse {shlex.quote(f'{base_sha}:{source_dir}')}",
            ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
        )
        records.append(
            {"git_oid": git_oid, "materialized_path": source, "path": source_dir, "ref": base_sha}
        )
    return sorted(records, key=lambda item: item["path"])
