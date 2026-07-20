"""Resolve immutable repository identities and trusted skill sources."""

from __future__ import annotations

import asyncio
import posixpath
import shlex
from collections.abc import Iterable
from typing import TYPE_CHECKING

from agent.utils.agents_md import applicable_agents_md_paths
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


_POLICY_STATUS_MARKER = "__mobilyze_instruction_status__="
_POLICY_BLOB_MODES = {"100644", "100755"}


async def _policy_blobs_at_ref(
    backend: SandboxBackendProtocol,
    repo_dir: str,
    base_sha: str,
    candidates: Iterable[str],
) -> set[str]:
    paths = list(dict.fromkeys(candidates))
    if not paths:
        return set()
    path_args = " ".join(shlex.quote(path) for path in paths)
    command = (
        f"git ls-tree -z {shlex.quote(base_sha)} -- {path_args}; "
        f"status=$?; printf '{_POLICY_STATUS_MARKER}%s\\0' \"$status\""
    )
    try:
        result = await asyncio.to_thread(
            backend.execute, f"cd {shlex.quote(repo_dir)} && {command}"
        )
    except Exception as exc:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            "failed to inspect trusted instruction sources at the base SHA",
        ) from exc
    records = [record for record in _result_output(result).split("\0") if record]
    status_records = [record for record in records if record.startswith(_POLICY_STATUS_MARKER)]
    if _result_exit_code(result) not in (0, None) or len(status_records) != 1:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            "failed to inspect trusted instruction sources at the base SHA",
        )
    try:
        status = int(status_records[0].removeprefix(_POLICY_STATUS_MARKER))
    except ValueError as exc:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            "failed to inspect trusted instruction sources at the base SHA",
        ) from exc
    if status != 0:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            "failed to inspect trusted instruction sources at the base SHA",
        )
    entries: dict[str, tuple[str, str]] = {}
    for record in records:
        if record.startswith(_POLICY_STATUS_MARKER):
            continue
        metadata, separator, entry_path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or entry_path not in paths or entry_path in entries:
            raise ReviewSubjectBlocked(
                ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
                "trusted instruction inspection returned an invalid entry",
            )
        mode, object_type, _ = fields
        entries[entry_path] = (mode, object_type)
    return {
        entry_path
        for entry_path, (mode, object_type) in entries.items()
        if object_type == "blob" and mode in _POLICY_BLOB_MODES
    }


async def root_instruction_path(
    backend: SandboxBackendProtocol, repo_dir: str, base_sha: str
) -> str:
    candidates = ("AGENTS.md", "CLAUDE.md")
    readable = await _policy_blobs_at_ref(backend, repo_dir, base_sha, candidates)
    for path in candidates:
        if path in readable:
            return path
    raise ReviewSubjectBlocked(
        ReviewSubjectBlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
        "trusted root instruction source is absent at the base SHA",
    )


async def expected_scoped_instruction_paths(
    backend: SandboxBackendProtocol,
    repo_dir: str,
    base_sha: str,
    changed_files: Iterable[str],
) -> set[str]:
    """Return applicable scoped instruction files that exist at the base SHA."""
    agents_paths = applicable_agents_md_paths(changed_files)
    candidates: list[str] = []
    for agents_path in agents_paths:
        directory = posixpath.dirname(agents_path)
        candidates.extend((agents_path, posixpath.join(directory, "CLAUDE.md")))
    readable = await _policy_blobs_at_ref(backend, repo_dir, base_sha, candidates)
    expected: set[str] = set()
    for agents_path in agents_paths:
        claude_path = posixpath.join(posixpath.dirname(agents_path), "CLAUDE.md")
        if agents_path in readable:
            expected.add(agents_path)
        elif claude_path in readable:
            expected.add(claude_path)
    return expected


async def _trusted_skill_dirs_at_ref(
    backend: SandboxBackendProtocol, repo_dir: str, base_sha: str
) -> set[str]:
    paths = " ".join(shlex.quote(path) for path in DEFAULT_SKILL_DIRS)
    marker = "__mobilyze_git_status__="
    command = (
        f"git ls-tree -d --name-only {shlex.quote(base_sha)} -- {paths}; "
        f"status=$?; echo {marker}$status"
    )
    try:
        result = await asyncio.to_thread(
            backend.execute, f"cd {shlex.quote(repo_dir)} && {command}"
        )
    except Exception as exc:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "failed to inspect trusted skill sources at the base SHA",
        ) from exc
    lines = _result_output(result).splitlines()
    status_lines = [line for line in lines if line.startswith(marker)]
    if _result_exit_code(result) not in (0, None) or len(status_lines) != 1:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "failed to inspect trusted skill sources at the base SHA",
        )
    try:
        status = int(status_lines[0].removeprefix(marker))
    except ValueError as exc:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "failed to inspect trusted skill sources at the base SHA",
        ) from exc
    if status != 0:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "failed to inspect trusted skill sources at the base SHA",
        )
    present = {line for line in lines if not line.startswith(marker)}
    if not present.issubset(DEFAULT_SKILL_DIRS):
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "trusted skill source inspection returned an unexpected path",
        )
    return present


async def trusted_skill_records(
    backend: SandboxBackendProtocol,
    repo_dir: str,
    base_sha: str,
) -> list[dict[str, str]]:
    expected = await _trusted_skill_dirs_at_ref(backend, repo_dir, base_sha)
    sources = await materialize_trusted_skills(backend, repo_dir=repo_dir, trusted_ref=base_sha)
    materialized: dict[str, str] = {}
    for source in sources:
        normalized_source = source.rstrip("/")
        source_dir = next(
            (item for item in DEFAULT_SKILL_DIRS if normalized_source.endswith(f"/{item}")), None
        )
        if source_dir is None or source_dir in materialized or source_dir not in expected:
            raise ReviewSubjectBlocked(
                ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
                f"unexpected trusted skill source: {source}",
            )
        materialized[source_dir] = source
    missing = sorted(expected.difference(materialized))
    if missing:
        raise ReviewSubjectBlocked(
            ReviewSubjectBlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            f"failed to materialize trusted skill sources: {', '.join(missing)}",
        )

    records: list[dict[str, str]] = []
    for source_dir, source in materialized.items():
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
