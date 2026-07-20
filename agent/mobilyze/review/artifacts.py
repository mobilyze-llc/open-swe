from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Iterable
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.mobilyze.review.contracts import ArtifactTrust, ReviewArtifact, ReviewSubject


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a value deterministically."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Hash artifact bytes."""
    return hashlib.sha256(value).hexdigest()


def make_artifact(
    *,
    role: str,
    content: bytes,
    suffix: str,
    media_type: str,
    trust: ArtifactTrust,
) -> ReviewArtifact:
    """Create a stable logical artifact reference."""
    digest = sha256_bytes(content)
    return ReviewArtifact(
        role=role,
        uri=f"{role}/{digest}{suffix}",
        sha256=digest,
        byte_length=len(content),
        media_type=media_type,
        trust=trust,
    )


def semantic_subject_hash(subject_values: dict[str, Any]) -> str:
    """Hash subject semantics without physical or logical artifact locations."""

    def strip_locations(value: object) -> object:
        if isinstance(value, dict):
            artifact = {str(key): item for key, item in value.items()}
            if {"role", "uri", "sha256", "byte_length", "media_type", "trust"} <= artifact.keys():
                artifact.pop("uri", None)
            return {key: strip_locations(item) for key, item in artifact.items()}
        if isinstance(value, list | tuple):
            return [strip_locations(item) for item in value]
        return value

    identity = dict(subject_values)
    identity.pop("subject_hash", None)
    return sha256_bytes(canonical_json_bytes(strip_locations(identity)))


def manifest_bytes(subject: ReviewSubject) -> bytes:
    """Serialize the persisted review subject."""
    return canonical_json_bytes(subject.to_persisted_dict())


async def upload_artifacts(
    sandbox_backend: SandboxBackendProtocol,
    *,
    artifact_root: str,
    values: Iterable[tuple[ReviewArtifact, bytes]],
) -> None:
    """Upload artifacts and fail on any provider-reported error."""
    uploads = [
        (posixpath.join(artifact_root.rstrip("/"), artifact.uri), content)
        for artifact, content in values
    ]
    responses = await sandbox_backend.aupload_files(uploads)
    if len(responses) != len(uploads):
        raise RuntimeError("artifact upload response count mismatch")
    for response in responses:
        error = (
            response.get("error")
            if isinstance(response, dict)
            else getattr(response, "error", None)
        )
        if error:
            raise RuntimeError(f"artifact upload failed: {error}")
