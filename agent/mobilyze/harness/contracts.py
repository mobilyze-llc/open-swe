from __future__ import annotations

import json
import math
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, ClassVar, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

_SECRET_MARKER = "secret"
_REDACTED = "<redacted>"
_TRUNCATED = "<truncated>"
_MAX_LOG_RECORD_BYTES = 16_384
_MAX_LOG_STRING_LENGTH = 1_024
_MAX_LOG_COLLECTION_ITEMS = 32
_MAX_LOG_DEPTH = 8


class PersistedContract(BaseModel):
    """Strict JSON contract with separate persistence and log serialization."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
    )

    def to_persisted_dict(self) -> dict[str, Any]:
        """Return complete JSON values; callers must protect fields marked secret."""
        current = self.model_dump(mode="python", round_trip=True)
        validated = type(self).model_validate(current)
        return validated.model_dump(mode="json")

    def to_persisted_json(self) -> str:
        """Return complete JSON, including secret-bearing values."""
        return _encode_persisted_dict(self.to_persisted_dict())

    @classmethod
    def from_persisted_dict(cls, value: dict[str, Any]) -> Self:
        return cls.model_validate_json(_encode_persisted_dict(value))

    @classmethod
    def from_persisted_json(cls, value: str | bytes) -> Self:
        return cls.model_validate_json(value)

    def to_log_dict(self) -> dict[str, object]:
        """Return bounded, JSON-compatible values with secret fields redacted."""
        output: dict[str, object] = {}
        for name, field in type(self).model_fields.items():
            value = getattr(self, name)
            metadata = field.json_schema_extra
            is_secret = isinstance(metadata, dict) and metadata.get(_SECRET_MARKER) is True
            output[name] = _REDACTED if is_secret and value is not None else _to_log_value(value)
        if len(_encode_log_dict(output).encode()) > _MAX_LOG_RECORD_BYTES:
            return {"contract": type(self).__name__, "summary": _TRUNCATED}
        return output


def _encode_persisted_dict(value: dict[str, Any]) -> str:
    if type(value) is not dict:
        raise ValueError("persisted contract must be a JSON object")
    _validate_json_shape(value)
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def _validate_json_shape(value: object, *, depth: int = 0, seen: set[int] | None = None) -> None:
    if depth > 100:
        raise ValueError("persisted JSON exceeds the maximum nesting depth")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("persisted JSON numbers must be finite")
        return
    if type(value) not in {dict, list}:
        raise ValueError(f"persisted JSON contains a non-JSON value of type {type(value).__name__}")

    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        raise ValueError("persisted JSON contains a circular reference")
    seen.add(identity)
    try:
        if type(value) is dict:
            for key, item in cast(dict[object, object], value).items():
                if type(key) is not str:
                    raise ValueError("persisted JSON object keys must be strings")
                _validate_json_shape(item, depth=depth + 1, seen=seen)
        else:
            for item in cast(list[object], value):
                _validate_json_shape(item, depth=depth + 1, seen=seen)
    finally:
        seen.remove(identity)


def _encode_log_dict(value: dict[str, object]) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def _to_log_value(value: object, *, depth: int = 0) -> object:
    if depth >= _MAX_LOG_DEPTH:
        return _TRUNCATED
    if isinstance(value, PersistedContract):
        return value.to_log_dict()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        if len(value) <= _MAX_LOG_STRING_LENGTH:
            return value
        return f"{value[:_MAX_LOG_STRING_LENGTH]}{_TRUNCATED}"
    if isinstance(value, tuple | list):
        items = [_to_log_value(item, depth=depth + 1) for item in value[:_MAX_LOG_COLLECTION_ITEMS]]
        if len(value) > _MAX_LOG_COLLECTION_ITEMS:
            items.append(_TRUNCATED)
        return items
    if isinstance(value, dict):
        items = list(value.items())
        output = {
            str(key): _to_log_value(item, depth=depth + 1)
            for key, item in items[:_MAX_LOG_COLLECTION_ITEMS]
        }
        if len(items) > _MAX_LOG_COLLECTION_ITEMS:
            output["__truncated__"] = _TRUNCATED
        return output
    return value


class PromptSourceKind(StrEnum):
    TEXT = "text"
    FILE = "file"
    ARTIFACT = "artifact"


class PermissionProfile(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    UNRESTRICTED = "unrestricted"


class TerminalStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class SideEffectClassification(StrEnum):
    NOT_STARTED = "not_started"
    STARTED_NO_KNOWN_WRITES = "started_no_known_writes"
    WRITES_POSSIBLE = "writes_possible"
    COMPLETED = "completed"


class PromptSource(PersistedContract):
    """Prompt input. ``value`` may contain secrets and must not be logged."""

    kind: PromptSourceKind
    value: str = Field(
        min_length=1, max_length=1_048_576, repr=False, json_schema_extra={_SECRET_MARKER: True}
    )


class ProviderCapabilities(PersistedContract):
    resume: bool = False
    streaming: bool = False
    schema_output: bool = False
    sandbox_flags: bool = False
    skills: bool = False
    mcp: bool = False


class HarnessSpec(PersistedContract):
    """Persisted execution request.

    ``prompt_source.value`` and ``persisted_session_id`` may contain secrets. Persistence
    stores their complete values so the backing store must provide equivalent protection.
    """

    provider: str = Field(min_length=1, max_length=128)
    executable: str = Field(min_length=1, max_length=4096)
    model: str | None = Field(default=None, max_length=256)
    working_directory: str = Field(min_length=1, max_length=4096)
    environment_allowlist: tuple[str, ...] = Field(default=(), max_length=128)
    prompt_source: PromptSource
    timeout_seconds: int = Field(gt=0, le=86_400)
    permissions_profile: PermissionProfile
    expected_result_schema: dict[str, JsonValue] | None = None
    persisted_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        repr=False,
        json_schema_extra={_SECRET_MARKER: True},
    )


class ExecutionHandle(PersistedContract):
    """Persisted execution identity; it intentionally contains no live process object."""

    execution_id: str = Field(min_length=1, max_length=256)
    provider: str = Field(min_length=1, max_length=128)
    provider_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        repr=False,
        json_schema_extra={_SECRET_MARKER: True},
    )


class ArtifactReference(PersistedContract):
    """Reference to output stored outside bounded thread event metadata."""

    uri: str = Field(min_length=1, max_length=4096)
    media_type: str | None = Field(default=None, max_length=255)


class Usage(PersistedContract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)


class HarnessResult(PersistedContract):
    """Bounded terminal result. Raw output must be referenced through ``artifacts``."""

    MAX_FINAL_MESSAGE_LENGTH: ClassVar[int] = 16_384

    execution_id: str = Field(min_length=1, max_length=256)
    status: TerminalStatus
    provider_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        repr=False,
        json_schema_extra={_SECRET_MARKER: True},
    )
    final_message: str | None = Field(default=None, max_length=MAX_FINAL_MESSAGE_LENGTH)
    artifacts: tuple[ArtifactReference, ...] = Field(default=(), max_length=128)
    usage: Usage | None = None
    side_effects: SideEffectClassification
