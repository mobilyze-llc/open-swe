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


async def _object_type(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, base_sha: str, path: str
) -> str | None:
    spec = shlex.quote(f"{base_sha}:{path}")
    result = await _run(
        sandbox_backend,
        f"cd {shlex.quote(repo_dir)} && git cat-file -t {spec}",
    )
    return _output(result).strip() if _succeeded(result) else None


async def _read_blob(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, base_sha: str, path: str
) -> str:
    spec = shlex.quote(f"{base_sha}:{path}")
    result = await _run(
        sandbox_backend,
        f"cd {shlex.quote(repo_dir)} && git cat-file blob {spec}",
    )
    if not _succeeded(result):
        raise TrustedContextError(
            BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            f"failed to read trusted instruction blob {path} at {base_sha}",
        )
    return _output(result)


async def _regular_blob_paths(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, base_sha: str
) -> set[str]:
    result = await _run(
        sandbox_backend,
        "cd "
        f"{shlex.quote(repo_dir)} && git -c core.quotePath=false ls-tree -r -z "
        f"{shlex.quote(base_sha)}",
    )
    if not _succeeded(result):
        raise TrustedContextError(
            BlockerCode.TRUSTED_INSTRUCTIONS_UNAVAILABLE,
            f"failed to discover trusted instruction blobs at {base_sha}",
        )
    paths: set[str] = set()
    for entry in _output(result).split("\0"):
        if not entry or "\t" not in entry:
            continue
        metadata, path = entry.split("\t", 1)
        mode, object_type, *_ = metadata.split()
        if mode in {"100644", "100755"} and object_type == "blob":
            paths.add(path)
    return paths


def _selected_instruction_path(blob_paths: set[str], directory: str) -> str | None:
    for filename in ("AGENTS.md", "CLAUDE.md"):
        path = posixpath.join(directory, filename) if directory else filename
        if path in blob_paths:
            return path
    return None


async def materialize_trusted_context(
    sandbox_backend: SandboxBackendProtocol,
    *,
    repo_dir: str,
    base_sha: str,
    changed_file_paths: list[str],
    max_bytes: int,
) -> TrustedContext:
    """Materialize base-SHA instructions and trusted skill identities."""
    degraded: list[DegradedField] = []
    selected: list[str] = []
    blob_paths = await _regular_blob_paths(sandbox_backend, repo_dir, base_sha)
    root_path = _selected_instruction_path(blob_paths, "")
    if root_path is None:
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
        path = _selected_instruction_path(blob_paths, directory)
        if path is not None and path not in selected:
            selected.append(path)

    instructions: list[dict[str, object]] = []
    for path in selected:
        content = await _read_blob(sandbox_backend, repo_dir, base_sha, path)
        instructions.append(
            {
                "path": path,
                "sha256": sha256_bytes(content.encode("utf-8")),
                "content": content,
            }
        )

    discovered: list[str] = []
    skills: list[dict[str, str]] = []
    for skill_dir in DEFAULT_SKILL_DIRS:
        if await _object_type(sandbox_backend, repo_dir, base_sha, skill_dir) != "tree":
            continue
        discovered.append(skill_dir)
        spec = shlex.quote(f"{base_sha}:{skill_dir}")
        tree_result = await _run(
            sandbox_backend,
            f"cd {shlex.quote(repo_dir)} && git rev-parse {spec}",
        )
        listing_result = await _run(
            sandbox_backend,
            "cd "
            f"{shlex.quote(repo_dir)} && git -c core.quotePath=false ls-tree -r -z "
            f"{shlex.quote(base_sha)} -- {shlex.quote(skill_dir)}",
        )
        if not _succeeded(tree_result) or not _succeeded(listing_result):
            raise TrustedContextError(
                BlockerCode.TRUSTED_SKILLS_UNAVAILABLE,
                f"failed to identify trusted skill tree {skill_dir} at {base_sha}",
            )
        skills.append(
            {
                "path": skill_dir,
                "git_tree": _output(tree_result).strip(),
                "listing_sha256": sha256_bytes(_output(listing_result).encode("utf-8")),
            }
        )

    dest_root = posixpath.join(posixpath.dirname(repo_dir), TRUSTED_SKILLS_DIRNAME)
    clean_result = await _run(sandbox_backend, f"rm -rf {shlex.quote(dest_root)}")
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

    content = canonical_json_bytes(
        {"base_sha": base_sha, "instructions": instructions, "skills": skills}
    )
    if len(content) > max_bytes:
        raise TrustedContextError(
            BlockerCode.LANE_LIMIT_EXCEEDED,
            "trusted context exceeds the caller-declared lane limit",
        )
    return TrustedContext(content=content, degraded=tuple(degraded))
