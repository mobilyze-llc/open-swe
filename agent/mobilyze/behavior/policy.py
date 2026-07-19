from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")
_CLAUSE_ID = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:gh[pousr]_|sk-|lin_api_)[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_FORBIDDEN_INPUT_ROOTS = frozenset(
    {".git", "diff", "diffs", "history", "source", "sources", "test", "tests", "trace", "traces"}
)
_GENERATED_SOURCE_ROOT = ("agent", "generated")


def require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{field} must be a single line")
    if any(pattern.search(value) for pattern in _SENSITIVE):
        raise ValueError(f"{field} must not contain credential values")


def require_name(value: str, field: str) -> None:
    require_text(value, field)
    if not _NAME.fullmatch(value):
        raise ValueError(f"{field} must be a named repository-approved reference")


def require_clause_id(value: str) -> None:
    require_text(value, "clause id")
    if not _CLAUSE_ID.fullmatch(value):
        raise ValueError("clause id must contain only lowercase identifier characters")


def require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def require_safe_artifact_path(value: str) -> None:
    require_text(value, "artifact path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or ".." in path.parts or "." in path.parts:
        raise ValueError("artifact path must be a safe relative artifact path")
    normalized = tuple(part.lower() for part in path.parts)
    if normalized[0] in _FORBIDDEN_INPUT_ROOTS or (
        normalized[0] == "agent" and normalized[:2] != _GENERATED_SOURCE_ROOT
    ):
        raise ValueError("artifact path cannot name source, test, diff, history, or trace inputs")


def require_target_reference(target_type: str, reference: str) -> None:
    require_text(reference, "target reference")
    if target_type == "http_api":
        parsed = urlsplit(reference)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("HTTP target must be a credential-free URL without query or fragment")
    elif target_type == "artifact":
        require_safe_artifact_path(reference)
    elif not _NAME.fullmatch(reference):
        raise ValueError("CLI and process targets must be named approved targets")


def redact_public_text(value: str) -> str:
    redacted = value
    for pattern in _SENSITIVE:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)\b(?:token|password|secret|api[_-]?key)\s*[=:]\s*\S+", "[REDACTED]", redacted
    )
    return redacted[:240]
