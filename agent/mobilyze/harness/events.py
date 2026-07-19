from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field, TypeAdapter, field_validator

from agent.mobilyze.harness.contracts import PersistedContract, TerminalStatus, Usage

MAX_EVENT_MESSAGE_LENGTH = 16_384


class FileChangeKind(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class ToolActivityPhase(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class HarnessEventBase(PersistedContract):
    """Common bounded metadata for events persisted on an Open SWE thread."""

    version: Literal[1] = 1
    execution_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=0)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value


class SessionStartedEvent(HarnessEventBase):
    kind: Literal["session_started"] = "session_started"
    provider_session_id: str = Field(
        min_length=1,
        max_length=4096,
        repr=False,
        json_schema_extra={"secret": True},
    )


class AssistantMessageEvent(HarnessEventBase):
    kind: Literal["assistant_message"] = "assistant_message"
    message: str = Field(max_length=MAX_EVENT_MESSAGE_LENGTH)


class CommandStartedEvent(HarnessEventBase):
    kind: Literal["command_started"] = "command_started"
    command_id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=1024)


class CommandCompletedEvent(HarnessEventBase):
    kind: Literal["command_completed"] = "command_completed"
    command_id: str = Field(min_length=1, max_length=256)
    exit_code: int


class FileChangeEvent(HarnessEventBase):
    kind: Literal["file_change"] = "file_change"
    path: str = Field(min_length=1, max_length=4096)
    change: FileChangeKind
    previous_path: str | None = Field(default=None, min_length=1, max_length=4096)


class ToolActivityEvent(HarnessEventBase):
    kind: Literal["tool_activity"] = "tool_activity"
    tool_name: str = Field(min_length=1, max_length=512)
    phase: ToolActivityPhase
    mcp_server: str | None = Field(default=None, min_length=1, max_length=512)


class UsageEvent(HarnessEventBase):
    kind: Literal["usage"] = "usage"
    usage: Usage


class WarningEvent(HarnessEventBase):
    kind: Literal["warning"] = "warning"
    code: str = Field(min_length=1, max_length=256)
    message: str = Field(max_length=MAX_EVENT_MESSAGE_LENGTH)


class FailureEvent(HarnessEventBase):
    kind: Literal["failure"] = "failure"
    code: str = Field(min_length=1, max_length=256)
    message: str = Field(max_length=MAX_EVENT_MESSAGE_LENGTH)
    retryable: bool


class CompletionEvent(HarnessEventBase):
    kind: Literal["completion"] = "completion"
    status: TerminalStatus


HarnessEvent: TypeAlias = Annotated[
    SessionStartedEvent
    | AssistantMessageEvent
    | CommandStartedEvent
    | CommandCompletedEvent
    | FileChangeEvent
    | ToolActivityEvent
    | UsageEvent
    | WarningEvent
    | FailureEvent
    | CompletionEvent,
    Field(discriminator="kind"),
]


_EVENT_ADAPTER: TypeAdapter[HarnessEvent] = TypeAdapter(HarnessEvent)
_EVENT_KINDS = frozenset(
    {
        "session_started",
        "assistant_message",
        "command_started",
        "command_completed",
        "file_change",
        "tool_activity",
        "usage",
        "warning",
        "failure",
        "completion",
    }
)


class HarnessEventValidationError(ValueError):
    pass


class UnknownEventVersionError(HarnessEventValidationError):
    pass


class UnknownEventKindError(HarnessEventValidationError):
    pass


def event_from_persisted_dict(value: dict[str, Any]) -> HarnessEvent:
    version = value.get("version")
    if type(version) is not int or version != 1:
        raise UnknownEventVersionError(f"unsupported harness event version: {version!r}")

    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _EVENT_KINDS:
        raise UnknownEventKindError(f"unsupported harness event kind: {kind!r}")

    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HarnessEventValidationError(f"invalid persisted harness event: {exc}") from exc
    return _EVENT_ADAPTER.validate_json(encoded)


def event_from_persisted_json(value: str | bytes) -> HarnessEvent:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HarnessEventValidationError(f"invalid harness event JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise HarnessEventValidationError("persisted harness event must be a JSON object")
    return event_from_persisted_dict(decoded)
