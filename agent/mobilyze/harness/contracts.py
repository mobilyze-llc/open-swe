from __future__ import annotations

import json
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue

_SECRET_MARKER = "secret"
_REDACTED = "<redacted>"


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
        return self.model_dump(mode="json")

    def to_persisted_json(self) -> str:
        """Return complete JSON, including secret-bearing values."""
        return self.model_dump_json()

    @classmethod
    def from_persisted_dict(cls, value: dict[str, Any]) -> Self:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
        return cls.model_validate_json(encoded)

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
        return output


def _to_log_value(value: object) -> object:
    if isinstance(value, PersistedContract):
        return value.to_log_dict()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_to_log_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_log_value(item) for key, item in value.items()}
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
