from __future__ import annotations

import asyncio
import posixpath
import shlex
from dataclasses import dataclass

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.mobilyze.review.artifacts import canonical_json_bytes, sha256_bytes
from agent.mobilyze.review.contracts import BlockerCode, DegradedCode, DegradedField
from agent.utils.agents_md import applicable_agents_md_paths
from agent.utils.repo_prep import (
    DEFAULT_SKILL_DIRS,
    TRUSTED_SKILLS_DIRNAME,
    materialize_trusted_skills,
)


class TrustedContextError(RuntimeError):
    """Typed trusted-context materialization failure."""

    def __init__(self, code: BlockerCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TrustedContext:
    content: bytes
    degraded: tuple[DegradedField, ...]


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
    if isinstance(result, dict):
        exit_code = result.get("exit_code")
        truncated = result.get("truncated", False)
    else:
        exit_code = getattr(result, "exit_code", None)
        truncated = getattr(result, "truncated", False)
    return exit_code in (0, None) and truncated is not True


async def _run(sandbox_backend: SandboxBackendProtocol, command: str) -> object:
    return await asyncio.to_thread(sandbox_backend.execute, command)


async def _tree_oid(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, base_sha: str, path: str
) -> str | None:
    try:
        result = await _run(
            sandbox_backend,
            f"cd {shlex.quote(repo_dir)} && git ls-tree -z "
            f"{shlex.quote(base_sha)} -- {shlex.quote(path)}",
        )
    except Exception as exc:
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            f"failed to discover trusted skill tree {path} at {base_sha}",
        ) from exc
    if not _succeeded(result):
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            f"failed to discover trusted skill tree {path} at {base_sha}",
        )
    entries = [entry for entry in _output(result).split("\0") if entry]
    if not entries:
        return None
    try:
        metadata, actual_path = entries[0].split("\t", 1)
        mode, object_type, oid = metadata.split()
    except ValueError as exc:
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            f"invalid trusted skill tree metadata for {path} at {base_sha}",
        ) from exc
    if len(entries) != 1 or actual_path != path:
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            f"ambiguous trusted skill tree metadata for {path} at {base_sha}",
        )
    return oid if mode == "040000" and object_type == "tree" else None


async def _read_blob(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, base_sha: str, path: str
) -> str:
    spec = shlex.quote(f"{base_sha}:{path}")
    try:
        result = await _run(
            sandbox_backend,
            f"cd {shlex.quote(repo_dir)} && git cat-file blob {spec}",
        )
    except Exception as exc:
        raise TrustedContextError(
            BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            f"failed to read trusted instruction blob {path} at {base_sha}",
        ) from exc
    if not _succeeded(result):
        raise TrustedContextError(
            BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            f"failed to read trusted instruction blob {path} at {base_sha}",
        )
    return _output(result)


async def _regular_blob_objects(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, base_sha: str
) -> dict[str, str]:
    try:
        result = await _run(
            sandbox_backend,
            "cd "
            f"{shlex.quote(repo_dir)} && git -c core.quotePath=false ls-tree -r -z "
            f"{shlex.quote(base_sha)}",
        )
    except Exception as exc:
        raise TrustedContextError(
            BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            f"failed to discover trusted instruction blobs at {base_sha}",
        ) from exc
    if not _succeeded(result):
        raise TrustedContextError(
            BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            f"failed to discover trusted instruction blobs at {base_sha}",
        )
    objects: dict[str, str] = {}
    try:
        for entry in _output(result).split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            mode, object_type, oid = metadata.split()
            if mode in {"100644", "100755"} and object_type == "blob":
                objects[path] = oid
    except ValueError as exc:
        raise TrustedContextError(
            BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            f"invalid trusted instruction metadata at {base_sha}",
        ) from exc
    return objects


def _selected_instruction_path(blob_objects: dict[str, str], directory: str) -> str | None:
    for filename in ("AGENTS.md", "CLAUDE.md"):
        path = posixpath.join(directory, filename) if directory else filename
        if path in blob_objects:
            return path
    return None


def _skill_blob_entries(listing: str, skill_dir: str, base_sha: str) -> list[tuple[str, str, str]]:
    prefix = f"{skill_dir.rstrip('/')}/"
    entries: list[tuple[str, str, str]] = []
    try:
        for entry in listing.split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            mode, object_type, oid = metadata.split()
            if object_type != "blob" or mode not in {"100644", "100755"}:
                raise ValueError
            if not path.startswith(prefix) or path == prefix:
                raise ValueError
            entries.append((path, mode, oid))
    except ValueError as exc:
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            f"unsupported trusted skill tree content in {skill_dir} at {base_sha}",
        ) from exc
    return entries


async def materialize_trusted_context(
    sandbox_backend: SandboxBackendProtocol,
    *,
    repo_dir: str,
    base_sha: str,
    changed_file_paths: list[str],
    max_bytes: int,
    allow_missing_root_instructions: bool,
) -> TrustedContext:
    """Materialize base-SHA instructions and trusted skill identities."""
    degraded: list[DegradedField] = []
    selected: list[str] = []
    blob_objects = await _regular_blob_objects(sandbox_backend, repo_dir, base_sha)
    root_path = _selected_instruction_path(blob_objects, "")
    if root_path is None:
        if not allow_missing_root_instructions:
            raise TrustedContextError(
                BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
                "trusted root instructions are absent and policy does not allow degradation",
            )
        degraded.append(
            DegradedField(
                code=DegradedCode.ROOT_INSTRUCTIONS_ABSENT,
                field="trusted_context.root_instruction",
                detail="neither root AGENTS.md nor CLAUDE.md is a regular blob at the base SHA",
            )
        )
    else:
        selected.append(root_path)

    scoped_candidates = applicable_agents_md_paths(changed_file_paths)
    for candidate in scoped_candidates:
        directory = posixpath.dirname(candidate)
        path = _selected_instruction_path(blob_objects, directory)
        if path is not None and path not in selected:
            selected.append(path)

    instructions: list[dict[str, object]] = []
    for path in selected:
        content = await _read_blob(sandbox_backend, repo_dir, base_sha, path)
        instructions.append(
            {
                "path": path,
                "git_blob": blob_objects[path],
                "sha256": sha256_bytes(content.encode("utf-8")),
                "content": content,
            }
        )

    discovered: list[str] = []
    expected_skill_files: list[tuple[str, str, str]] = []
    skills: list[dict[str, str]] = []
    for skill_dir in DEFAULT_SKILL_DIRS:
        tree_oid = await _tree_oid(sandbox_backend, repo_dir, base_sha, skill_dir)
        if tree_oid is None:
            continue
        discovered.append(skill_dir)
        try:
            listing_result = await _run(
                sandbox_backend,
                "cd "
                f"{shlex.quote(repo_dir)} && git -c core.quotePath=false ls-tree -r -z "
                f"{shlex.quote(base_sha)} -- {shlex.quote(skill_dir)}",
            )
        except Exception as exc:
            raise TrustedContextError(
                BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
                f"failed to identify trusted skill tree {skill_dir} at {base_sha}",
            ) from exc
        if not _succeeded(listing_result):
            raise TrustedContextError(
                BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
                f"failed to identify trusted skill tree {skill_dir} at {base_sha}",
            )
        listing = _output(listing_result)
        expected_skill_files.extend(_skill_blob_entries(listing, skill_dir, base_sha))
        skills.append(
            {
                "path": skill_dir,
                "git_tree": tree_oid,
                "listing_sha256": sha256_bytes(listing.encode("utf-8")),
            }
        )

    dest_root = posixpath.join(posixpath.dirname(repo_dir), TRUSTED_SKILLS_DIRNAME)
    try:
        clean_result = await _run(sandbox_backend, f"rm -rf {shlex.quote(dest_root)}")
    except Exception as exc:
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "failed to clear previously extracted trusted skills",
        ) from exc
    if not _succeeded(clean_result):
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "failed to clear previously extracted trusted skills",
        )
    extracted = await materialize_trusted_skills(
        sandbox_backend,
        repo_dir=repo_dir,
        trusted_ref=base_sha,
    )
    expected = [f"{posixpath.join(dest_root, path)}/" for path in discovered]
    if extracted != expected:
        raise TrustedContextError(
            BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
            "trusted skill discovery and extraction did not match one-to-one",
        )
    environment = (
        "unset GIT_CONFIG GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_EXTERNAL_DIFF "
        "GIT_DIFF_OPTS GIT_ATTR_SOURCE; "
        "export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null "
        "GIT_ATTR_NOSYSTEM=1 GIT_NO_REPLACE_OBJECTS=1"
    )
    for path, mode, oid in expected_skill_files:
        destination = posixpath.join(dest_root, path)
        executable_check = "test -x" if mode == "100755" else "test ! -x"
        try:
            result = await _run(
                sandbox_backend,
                f"cd {shlex.quote(repo_dir)} && {environment} && "
                f"test -f {shlex.quote(destination)} && "
                f"{executable_check} {shlex.quote(destination)} && "
                f"git hash-object --no-filters {shlex.quote(destination)}",
            )
        except Exception as exc:
            raise TrustedContextError(
                BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
                f"failed to verify extracted trusted skill file {path}",
            ) from exc
        if not _succeeded(result) or _output(result).strip() != oid:
            raise TrustedContextError(
                BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
                f"extracted trusted skill file does not match {base_sha}:{path}",
            )

    content = canonical_json_bytes(
        {"base_sha": base_sha, "instructions": instructions, "skills": skills}
    )
    if len(content) > max_bytes:
        raise TrustedContextError(
            BlockerCode.LANE_LIMIT_EXCEEDED,
            "trusted context exceeds the caller-declared lane limit",
        )
    return TrustedContext(content=content, degraded=tuple(degraded))
