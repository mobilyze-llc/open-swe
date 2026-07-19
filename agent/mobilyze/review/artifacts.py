"""Content-addressed artifact persistence for review subjects."""

from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .contracts import (
    ArtifactRef,
    MaterializedReviewSubject,
    ReviewSubjectBlocked,
    ReviewSubjectBlockerCode,
    ReviewSubjectRequest,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _artifact_identity(ref: ArtifactRef) -> dict[str, object]:
    return {"bytes": ref.bytes, "sha256": ref.sha256, "trust": ref.trust}


def _without_storage_paths(value: object) -> object:
    if isinstance(value, list):
        return [_without_storage_paths(item) for item in value]
    if not isinstance(value, dict):
        return value
    is_artifact_ref = {"bytes", "path", "sha256", "trust"}.issubset(value)
    return {
        key: _without_storage_paths(item)
        for key, item in value.items()
        if not (is_artifact_ref and key == "path")
    }


@dataclass(frozen=True)
class ReviewSubjectMaterials:
    repo_dir: str
    base_sha: str
    head_sha: str
    merge_base_sha: str
    diff_base_sha: str
    re_review: bool
    diff_text: str
    changed_files: list[str]
    changed_lines: dict[str, dict[str, list[int]]]
    pr_title: str
    pr_body: str
    review_threads: list[dict[str, object]]
    root_instructions: str | None
    root_instruction_path: str | None
    scoped_instructions: dict[str, str]
    repository_skill_refs: list[dict[str, str]]
    degradations: list[str]


def _artifact(
    root: str, kind: str, content: bytes, trust: str, suffix: str = "json"
) -> ArtifactRef:
    digest = hashlib.sha256(content).hexdigest()
    path = posixpath.join(root.rstrip("/"), "artifacts", f"{kind}-{digest[:20]}.{suffix}")
    if trust == "trusted":
        return ArtifactRef(path=path, sha256=digest, bytes=len(content), trust="trusted")
    return ArtifactRef(path=path, sha256=digest, bytes=len(content), trust="untrusted")


async def _upload(
    backend: SandboxBackendProtocol, artifacts: list[tuple[ArtifactRef, bytes]]
) -> None:
    responses = await backend.aupload_files([(ref.path, content) for ref, content in artifacts])
    for response in responses:
        error = (
            response.get("error")
            if isinstance(response, dict)
            else getattr(response, "error", None)
        )
        if error:
            raise ReviewSubjectBlocked(
                ReviewSubjectBlockerCode.ARTIFACT_WRITE_FAILED,
                f"failed to write review-subject artifact: {error}",
            )


async def persist_review_subject(
    backend: SandboxBackendProtocol,
    *,
    request: ReviewSubjectRequest,
    materials: ReviewSubjectMaterials,
) -> MaterializedReviewSubject:
    artifacts: list[tuple[ArtifactRef, bytes]] = []

    def add(kind: str, value: object, trust: str, suffix: str = "json") -> ArtifactRef:
        content = value.encode() if isinstance(value, str) else canonical_json(value)
        ref = _artifact(request.artifact_root, kind, content, trust, suffix)
        artifacts.append((ref, content))
        return ref

    diff_ref = add("diff", materials.diff_text, "untrusted", "patch")
    files_ref = add("changed-files", materials.changed_files, "untrusted")
    lines_ref = add("changed-lines", materials.changed_lines, "untrusted")
    pr_text_ref = add(
        "pr-text", {"body": materials.pr_body, "title": materials.pr_title}, "untrusted"
    )
    threads_ref = add("review-threads", materials.review_threads, "untrusted")
    instructions_ref = add(
        "trusted-instructions",
        {
            "base_sha": materials.base_sha,
            "root": materials.root_instructions,
            "root_path": materials.root_instruction_path,
            "scoped": dict(sorted(materials.scoped_instructions.items())),
            "trust": "trusted",
        },
        "trusted",
    )
    trusted_context_identity = {
        "administrator_skill_refs": sorted(
            (_artifact_identity(ref) for ref in request.administrator_skill_refs),
            key=canonical_json,
        ),
        "base_sha": materials.base_sha,
        "instructions": _artifact_identity(instructions_ref),
        "repository_skill_refs": [
            {key: value for key, value in ref.items() if key != "materialized_path"}
            for ref in materials.repository_skill_refs
        ],
    }
    trusted_context_identity_sha = hashlib.sha256(
        canonical_json(trusted_context_identity)
    ).hexdigest()
    trusted_context_ref = add(
        "trusted-context",
        {
            "administrator_skill_refs": [
                ref.to_dict()
                for ref in sorted(request.administrator_skill_refs, key=lambda item: item.path)
            ],
            "base_sha": materials.base_sha,
            "instructions": instructions_ref.to_dict(),
            "repository_skill_refs": materials.repository_skill_refs,
        },
        "trusted",
    )
    manifest: dict[str, object] = {
        "schema": "mobilyze.review-subject.v1",
        "repository": {
            "owner": request.owner,
            "name": request.repo,
            "pr_number": request.pr_number,
        },
        "revisions": {
            "base_sha": materials.base_sha,
            "head_sha": materials.head_sha,
            "merge_base_sha": materials.merge_base_sha,
            "diff_base_sha": materials.diff_base_sha,
            "diff_mode": ("re_review_range" if materials.re_review else "first_review_merge_base"),
        },
        "artifacts": {
            "changed_files": {"count": len(materials.changed_files), "ref": files_ref.to_dict()},
            "changed_lines": {
                "file_count": len(materials.changed_lines),
                "ref": lines_ref.to_dict(),
            },
            "diff": diff_ref.to_dict(),
            "pr_text": {"classification": "untrusted_data", "ref": pr_text_ref.to_dict()},
            "review_threads": {
                "classification": "untrusted_data",
                "count": len(materials.review_threads),
                "ref": threads_ref.to_dict(),
            },
            "trusted_context": {
                "identity_sha256": trusted_context_identity_sha,
                "ref": trusted_context_ref.to_dict(),
            },
        },
        "review_policy": {
            "agent_definitions": [
                item.to_dict()
                for item in sorted(request.agent_definitions, key=lambda item: item.id)
            ],
            "lane_input_limits": request.lane_input_limits.to_dict(),
            "version": request.review_policy_version,
        },
        "validations": [item.to_dict() for item in request.validations],
        "behavior": {
            "contract": request.behavior_contract.to_dict()
            if request.behavior_contract is not None
            else None,
            "report": request.behavior_report.to_dict()
            if request.behavior_report is not None
            else None,
        },
        "run_trace_digest": {
            "available_to_initial_finders": False,
            "classification": "untrusted_data",
            "ref": request.run_trace_digest.to_dict(),
        }
        if request.run_trace_digest is not None
        else None,
        "degradations": sorted(materials.degradations),
    }
    identity_manifest = cast(dict[str, object], _without_storage_paths(manifest))
    identity_artifacts = cast(dict[str, object], identity_manifest["artifacts"])
    identity_artifacts["trusted_context"] = {"identity_sha256": trusted_context_identity_sha}
    subject_hash = hashlib.sha256(canonical_json(identity_manifest)).hexdigest()
    manifest_path = posixpath.join(
        request.artifact_root.rstrip("/"), f"review-subject-{subject_hash}.json"
    )
    manifest_content = canonical_json({"manifest": manifest, "subject_hash": subject_hash})
    manifest_ref = ArtifactRef(
        path=manifest_path,
        sha256=hashlib.sha256(manifest_content).hexdigest(),
        bytes=len(manifest_content),
        trust="trusted",
    )
    artifacts.append((manifest_ref, manifest_content))
    await _upload(backend, artifacts)
    return MaterializedReviewSubject(
        manifest_path=manifest_path,
        subject_hash=subject_hash,
        manifest=manifest,
        checkout_path=materials.repo_dir,
        base_sha=materials.base_sha,
        head_sha=materials.head_sha,
        trusted_context_artifact=trusted_context_ref,
        run_trace_digest=request.run_trace_digest,
    )
