from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from agent.webhooks import common


@pytest.mark.asyncio
@pytest.mark.parametrize("re_review", [False, True])
@pytest.mark.parametrize("finding_reply", [False, True])
@pytest.mark.parametrize("explicit_request", [False, True])
async def test_empty_routing_resolves_stock_for_every_dispatch_kind(
    monkeypatch: pytest.MonkeyPatch,
    re_review: bool,
    finding_reply: bool,
    explicit_request: bool,
) -> None:
    monkeypatch.delenv("REVIEWER_ROUTING_DEFAULT", raising=False)
    monkeypatch.setattr(common, "get_team_settings", AsyncMock(return_value={}))

    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=re_review,
        finding_reply=finding_reply,
        explicit_request=explicit_request,
    )

    assert assistant_id == "reviewer"


@pytest.mark.asyncio
async def test_team_routing_precedes_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEWER_ROUTING_DEFAULT", "reviewer")
    monkeypatch.setattr(
        common,
        "get_team_settings",
        AsyncMock(return_value={"reviewer_routing": "reviewer_adversarial"}),
    )

    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=False,
        finding_reply=False,
        explicit_request=False,
    )

    assert assistant_id == "reviewer_adversarial"


@pytest.mark.asyncio
async def test_env_default_routes_fresh_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEWER_ROUTING_DEFAULT", "reviewer_adversarial")
    monkeypatch.setattr(common, "get_team_settings", AsyncMock(return_value={}))

    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=False,
        finding_reply=False,
        explicit_request=True,
    )

    assert assistant_id == "reviewer_adversarial"


@pytest.mark.asyncio
async def test_env_default_does_not_reuse_eval_assistant_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REVIEWER_ROUTING_DEFAULT", raising=False)
    monkeypatch.setenv("REVIEWER_ASSISTANT_ID", "reviewer_adversarial")
    monkeypatch.setattr(common, "get_team_settings", AsyncMock(return_value={}))

    assistant_id = await common.reviewer_assistant_for_dispatch(
        re_review=False,
        finding_reply=False,
        explicit_request=True,
    )

    assert assistant_id == "reviewer"


@pytest.mark.asyncio
async def test_invalid_routing_logs_and_falls_back_to_stock(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("REVIEWER_ROUTING_DEFAULT", "reviewer_adversarial")
    monkeypatch.setattr(
        common,
        "get_team_settings",
        AsyncMock(return_value={"reviewer_routing": "not-a-graph"}),
    )

    with caplog.at_level(logging.WARNING):
        assistant_id = await common.reviewer_assistant_for_dispatch(
            re_review=False,
            finding_reply=False,
            explicit_request=False,
        )

    assert assistant_id == "reviewer"
    assert "Invalid reviewer routing" in caplog.text
    assert "team setting" in caplog.text


@pytest.mark.asyncio
async def test_invalid_env_routing_logs_and_falls_back_to_stock(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("REVIEWER_ROUTING_DEFAULT", "not-a-graph")
    monkeypatch.setattr(common, "get_team_settings", AsyncMock(return_value={}))

    with caplog.at_level(logging.WARNING):
        assistant_id = await common.reviewer_assistant_for_dispatch(
            re_review=False,
            finding_reply=False,
            explicit_request=False,
        )

    assert assistant_id == "reviewer"
    assert "Invalid reviewer routing" in caplog.text
    assert "REVIEWER_ROUTING_DEFAULT" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("re_review", "finding_reply", "explicit_request", "dispatch_kind"),
    [
        (True, False, False, "re-review"),
        (False, True, False, "finding reply"),
        (True, False, True, "explicit re-request"),
    ],
)
async def test_interlock_forces_stock_for_unsupported_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    re_review: bool,
    finding_reply: bool,
    explicit_request: bool,
    dispatch_kind: str,
) -> None:
    monkeypatch.setattr(
        common,
        "get_team_settings",
        AsyncMock(return_value={"reviewer_routing": "reviewer_adversarial"}),
    )

    with caplog.at_level(logging.WARNING):
        assistant_id = await common.reviewer_assistant_for_dispatch(
            re_review=re_review,
            finding_reply=finding_reply,
            explicit_request=explicit_request,
        )

    assert assistant_id == "reviewer"
    assert dispatch_kind in caplog.text
