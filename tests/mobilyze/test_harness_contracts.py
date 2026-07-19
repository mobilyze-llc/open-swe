from __future__ import annotations

from collections.abc import AsyncIterator
from math import inf, nan
from typing import cast

import pytest
from pydantic import ValidationError

from agent.mobilyze.harness import (
    ArtifactReference,
    ExecutionHandle,
    HarnessEvent,
    HarnessExecutor,
    HarnessResult,
    HarnessSpec,
    PermissionProfile,
    PromptSource,
    PromptSourceKind,
    ProviderCapabilities,
    SideEffectClassification,
    TerminalStatus,
    Usage,
)


def _spec() -> HarnessSpec:
    return HarnessSpec(
        provider="codex",
        executable="codex",
        model="gpt-5.6",
        working_directory="/workspace/repo",
        environment_allowlist=("PATH", "GH_TOKEN"),
        prompt_source=PromptSource(kind=PromptSourceKind.TEXT, value="sensitive prompt"),
        timeout_seconds=900,
        permissions_profile=PermissionProfile.READ_WRITE,
        expected_result_schema={"type": "object", "required": ["summary"]},
        persisted_session_id="provider-session-secret",
    )


def test_contracts_round_trip_through_persisted_json() -> None:
    spec = _spec()
    handle = ExecutionHandle(
        execution_id="exec-123",
        provider="codex",
        provider_session_id="provider-session-secret",
    )
    capabilities = ProviderCapabilities(
        resume=True,
        streaming=True,
        schema_output=True,
        sandbox_flags=True,
        skills=True,
        mcp=True,
    )
    result = HarnessResult(
        execution_id="exec-123",
        status=TerminalStatus.SUCCEEDED,
        provider_session_id="provider-session-secret",
        final_message="Finished",
        artifacts=(ArtifactReference(uri="artifact://exec-123/raw.log", media_type="text/plain"),),
        usage=Usage(input_tokens=20, output_tokens=10, cache_read_tokens=5),
        side_effects=SideEffectClassification.COMPLETED,
    )

    for value in (spec.prompt_source, spec, handle, capabilities, result):
        assert type(value).from_persisted_json(value.to_persisted_json()) == value


def test_secret_fields_are_redacted_from_repr_and_log_serialization() -> None:
    spec = _spec()
    handle = ExecutionHandle(
        execution_id="exec-123",
        provider="codex",
        provider_session_id="provider-session-secret",
    )
    result = HarnessResult(
        execution_id="exec-123",
        status=TerminalStatus.FAILED,
        provider_session_id="provider-session-secret",
        side_effects=SideEffectClassification.WRITES_POSSIBLE,
    )

    for value in (spec.prompt_source, spec, handle, result):
        rendered = repr(value)
        logged = value.to_log_dict()
        assert "sensitive prompt" not in rendered
        assert "provider-session-secret" not in rendered
        assert "sensitive prompt" not in str(logged)
        assert "provider-session-secret" not in str(logged)

    assert spec.prompt_source.to_log_dict()["value"] == "<redacted>"
    assert spec.to_log_dict()["persisted_session_id"] == "<redacted>"
    assert handle.to_log_dict()["provider_session_id"] == "<redacted>"
    assert result.to_log_dict()["provider_session_id"] == "<redacted>"


@pytest.mark.parametrize("side_effects", list(SideEffectClassification))
def test_result_exposes_every_retry_relevant_side_effect_state(
    side_effects: SideEffectClassification,
) -> None:
    result = HarnessResult(
        execution_id="exec-123",
        status=TerminalStatus.FAILED,
        side_effects=side_effects,
    )

    assert result.side_effects is side_effects


def test_models_reject_unknown_persisted_fields() -> None:
    payload = _spec().to_persisted_dict()
    payload["provider_argv"] = ["--dangerously-skip-permissions"]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HarnessSpec.from_persisted_dict(payload)


@pytest.mark.parametrize(
    ("contract", "payload"),
    [
        (ProviderCapabilities, '{"resume":"yes"}'),
        (Usage, '{"input_tokens":"12"}'),
    ],
)
def test_persisted_primitive_types_are_not_coerced(
    contract: type[ProviderCapabilities] | type[Usage], payload: str
) -> None:
    with pytest.raises(ValidationError):
        contract.from_persisted_json(payload)


@pytest.mark.parametrize("non_finite", [nan, inf, -inf])
def test_expected_result_schema_rejects_non_finite_json_numbers(non_finite: float) -> None:
    with pytest.raises(ValidationError, match="Input should be a finite number"):
        HarnessSpec(
            provider="codex",
            executable="codex",
            working_directory="/workspace/repo",
            prompt_source=PromptSource(kind=PromptSourceKind.TEXT, value="prompt"),
            timeout_seconds=900,
            permissions_profile=PermissionProfile.READ_WRITE,
            expected_result_schema={"const": non_finite},
        )

    payload = _spec().to_persisted_dict()
    payload["expected_result_schema"] = {"const": non_finite}

    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        HarnessSpec.from_persisted_dict(payload)


def test_validation_errors_hide_secret_inputs() -> None:
    secret = "secret-prompt-value"

    with pytest.raises(ValidationError) as raised:
        PromptSource(kind=PromptSourceKind.TEXT, value=secret * 100_000)

    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)


class _FakeExecutor:
    def __init__(self) -> None:
        self._result = HarnessResult(
            execution_id="exec-123",
            status=TerminalStatus.SUCCEEDED,
            side_effects=SideEffectClassification.COMPLETED,
        )

    async def start(self, spec: HarnessSpec) -> ExecutionHandle:
        return ExecutionHandle(execution_id="exec-123", provider=spec.provider)

    def events(self, handle: ExecutionHandle) -> AsyncIterator[HarnessEvent]:
        async def _empty() -> AsyncIterator[HarnessEvent]:
            if False:
                yield cast(HarnessEvent, handle)

        return _empty()

    async def resume(self, handle: ExecutionHandle, prompt_source: PromptSource) -> ExecutionHandle:
        return handle

    async def cancel(self, handle: ExecutionHandle) -> None:
        return None

    async def result(self, handle: ExecutionHandle) -> HarnessResult:
        return self._result


def test_executor_protocol_accepts_a_test_fake_without_inheritance() -> None:
    executor: HarnessExecutor = _FakeExecutor()

    assert isinstance(executor, HarnessExecutor)
