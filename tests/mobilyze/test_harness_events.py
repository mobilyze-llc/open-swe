from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent.mobilyze.harness import (
    AssistantMessageEvent,
    CommandCompletedEvent,
    CommandStartedEvent,
    CompletionEvent,
    FailureEvent,
    FileChangeEvent,
    FileChangeKind,
    HarnessEvent,
    SessionStartedEvent,
    TerminalStatus,
    ToolActivityEvent,
    ToolActivityPhase,
    UnknownEventKindError,
    UnknownEventVersionError,
    Usage,
    UsageEvent,
    WarningEvent,
    event_from_persisted_dict,
    event_from_persisted_json,
)

NOW = datetime(2026, 7, 19, 16, 0, tzinfo=UTC)


def _events() -> tuple[HarnessEvent, ...]:
    common = {"execution_id": "exec-123", "occurred_at": NOW}
    return (
        SessionStartedEvent(
            **common,
            sequence=0,
            provider_session_id="provider-session-secret",
        ),
        AssistantMessageEvent(**common, sequence=1, message="Working"),
        CommandStartedEvent(
            **common,
            sequence=2,
            command_id="command-1",
            display_name="pytest",
        ),
        CommandCompletedEvent(
            **common,
            sequence=3,
            command_id="command-1",
            exit_code=0,
        ),
        FileChangeEvent(
            **common,
            sequence=4,
            path="agent/mobilyze/harness/events.py",
            change=FileChangeKind.MODIFIED,
        ),
        ToolActivityEvent(
            **common,
            sequence=5,
            tool_name="read_file",
            phase=ToolActivityPhase.COMPLETED,
        ),
        UsageEvent(**common, sequence=6, usage=Usage(input_tokens=10, output_tokens=4)),
        WarningEvent(**common, sequence=7, code="rate_limit", message="Slowing down"),
        FailureEvent(
            **common,
            sequence=8,
            code="provider_failed",
            message="The provider exited",
            retryable=False,
        ),
        CompletionEvent(**common, sequence=9, status=TerminalStatus.SUCCEEDED),
    )


@pytest.mark.parametrize("event", _events())
def test_every_event_kind_round_trips(event: HarnessEvent) -> None:
    assert event_from_persisted_dict(event.to_persisted_dict()) == event
    assert event_from_persisted_json(event.to_persisted_json()) == event


def test_event_parser_fails_closed_on_unknown_version() -> None:
    payload = _events()[0].to_persisted_dict()
    payload["version"] = 2

    with pytest.raises(UnknownEventVersionError, match="unsupported harness event version"):
        event_from_persisted_dict(payload)


@pytest.mark.parametrize("version", [True, 1.0, "1", None])
def test_event_parser_fails_closed_on_malformed_version_types(version: object) -> None:
    payload = _events()[0].to_persisted_dict()
    payload["version"] = version

    with pytest.raises(UnknownEventVersionError, match="unsupported harness event version"):
        event_from_persisted_dict(payload)


def test_event_parser_fails_closed_on_unknown_kind() -> None:
    payload = _events()[0].to_persisted_dict()
    payload["kind"] = "provider_raw_log"

    with pytest.raises(
        UnknownEventKindError,
        match="unsupported harness event kind",
    ):
        event_from_persisted_dict(payload)


@pytest.mark.parametrize("kind", [[], {}, 1, True, None])
def test_event_parser_fails_closed_on_malformed_kind_types(kind: object) -> None:
    payload = _events()[0].to_persisted_dict()
    payload["kind"] = kind

    with pytest.raises(UnknownEventKindError, match="unsupported harness event kind"):
        event_from_persisted_dict(payload)


def test_event_payloads_are_bounded_and_reject_raw_log_fields() -> None:
    with pytest.raises(ValidationError, match="at most 16384 characters"):
        AssistantMessageEvent(
            execution_id="exec-123",
            sequence=1,
            occurred_at=NOW,
            message="x" * 16385,
        )

    payload = _events()[1].to_persisted_dict()
    payload["raw_log"] = "unbounded"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        event_from_persisted_dict(payload)


def test_provider_session_identity_is_redacted_from_event_logs() -> None:
    event = _events()[0]

    assert "provider-session-secret" not in repr(event)
    assert event.to_log_dict()["provider_session_id"] == "<redacted>"


@pytest.mark.parametrize("field", ["version", "kind"])
def test_discriminator_errors_do_not_reproduce_untrusted_values(field: str) -> None:
    secret = "secret-bearing-discriminator"
    payload = _events()[0].to_persisted_dict()
    payload[field] = secret * 10_000

    with pytest.raises((UnknownEventVersionError, UnknownEventKindError)) as raised:
        event_from_persisted_dict(payload)

    assert secret not in str(raised.value)
    assert len(str(raised.value)) < 256
