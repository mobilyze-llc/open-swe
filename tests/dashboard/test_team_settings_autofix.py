from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard.team_settings import (
    AUTO_MERGE_ALWAYS,
    AUTO_MERGE_NEVER,
    AUTO_MERGE_ON_PLAN_APPROVAL,
    TeamSettingsUpdate,
    get_team_auto_merge_mode,
    get_team_autofix_settings,
    get_team_require_plan_approval,
    upsert_team_settings,
)

# --- accessor: get_team_autofix_settings (async, patched settings) ---


@pytest.mark.asyncio
async def test_autofix_defaults_off_when_absent() -> None:
    # Legacy record with no autofix keys -> disabled, medium threshold.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={},
    ):
        assert await get_team_autofix_settings() == (False, "medium")


@pytest.mark.asyncio
async def test_autofix_enabled_with_threshold() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"autofix_enabled": True, "autofix_severity_threshold": "high"},
    ):
        assert await get_team_autofix_settings() == (True, "high")


@pytest.mark.asyncio
async def test_autofix_invalid_values_fail_closed() -> None:
    # Fail-closed: non-bool enabled reads as off, unknown threshold as medium.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"autofix_enabled": "true", "autofix_severity_threshold": "extreme"},
    ):
        assert await get_team_autofix_settings() == (False, "medium")


@pytest.mark.asyncio
async def test_autofix_settings_survive_team_settings_scrub() -> None:
    # Regression (caught live by canary 3): get_team_settings carries a
    # stale-field purge that previously deleted the autofix keys from every
    # response, so the accessor read the opt-in as permanently off. This test
    # goes through the REAL get_team_settings — mocking it hides the scrub.
    with patch(
        "agent.dashboard.team_settings._get_stored_team_settings",
        new_callable=AsyncMock,
        return_value={"autofix_enabled": True, "autofix_severity_threshold": "high"},
    ):
        assert await get_team_autofix_settings() == (True, "high")


# --- upsert: unrelated saves must not erase the opt-in ---


def _put_capture() -> tuple[MagicMock, AsyncMock]:
    client = MagicMock()
    put = AsyncMock()
    client.store.put_item = put
    return client, put


@pytest.mark.asyncio
async def test_upsert_without_autofix_fields_preserves_stored_opt_in() -> None:
    client, put = _put_capture()
    with (
        patch(
            "agent.dashboard.team_settings._get_stored_team_settings",
            new_callable=AsyncMock,
            return_value={"autofix_enabled": True, "autofix_severity_threshold": "high"},
        ),
        patch("agent.dashboard.team_settings._client", return_value=client),
    ):
        value = await upsert_team_settings(TeamSettingsUpdate())

    put.assert_awaited_once()
    assert value["autofix_enabled"] is True
    assert value["autofix_severity_threshold"] == "high"


@pytest.mark.asyncio
async def test_upsert_sets_autofix_fields_explicitly() -> None:
    client, put = _put_capture()
    with (
        patch(
            "agent.dashboard.team_settings._get_stored_team_settings",
            new_callable=AsyncMock,
            return_value={"autofix_enabled": True, "autofix_severity_threshold": "high"},
        ),
        patch("agent.dashboard.team_settings._client", return_value=client),
    ):
        value = await upsert_team_settings(
            TeamSettingsUpdate(autofix_enabled=False, autofix_severity_threshold="low")
        )

    put.assert_awaited_once()
    assert value["autofix_enabled"] is False
    assert value["autofix_severity_threshold"] == "low"


def test_update_rejects_unknown_threshold() -> None:
    with pytest.raises(ValueError):
        TeamSettingsUpdate(autofix_severity_threshold="extreme")  # type: ignore[arg-type]


# --- plan approval policy: same tri-state persistence contract as autofix ---


@pytest.mark.asyncio
async def test_plan_approval_defaults_off_when_absent() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={},
    ):
        assert await get_team_require_plan_approval() is False


@pytest.mark.asyncio
async def test_plan_approval_invalid_value_fails_closed() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"require_plan_approval": "true"},
    ):
        assert await get_team_require_plan_approval() is False


@pytest.mark.asyncio
async def test_plan_approval_survives_team_settings_scrub() -> None:
    with patch(
        "agent.dashboard.team_settings._get_stored_team_settings",
        new_callable=AsyncMock,
        return_value={"require_plan_approval": True},
    ):
        assert await get_team_require_plan_approval() is True


@pytest.mark.asyncio
async def test_upsert_without_plan_approval_preserves_stored_policy() -> None:
    client, put = _put_capture()
    with (
        patch(
            "agent.dashboard.team_settings._get_stored_team_settings",
            new_callable=AsyncMock,
            return_value={"require_plan_approval": True},
        ),
        patch("agent.dashboard.team_settings._client", return_value=client),
    ):
        value = await upsert_team_settings(TeamSettingsUpdate())

    put.assert_awaited_once()
    assert value["require_plan_approval"] is True


@pytest.mark.asyncio
async def test_upsert_sets_plan_approval_policy_explicitly() -> None:
    client, put = _put_capture()
    with (
        patch(
            "agent.dashboard.team_settings._get_stored_team_settings",
            new_callable=AsyncMock,
            return_value={"require_plan_approval": True},
        ),
        patch("agent.dashboard.team_settings._client", return_value=client),
    ):
        value = await upsert_team_settings(TeamSettingsUpdate(require_plan_approval=False))

    put.assert_awaited_once()
    assert value["require_plan_approval"] is False


@pytest.mark.asyncio
async def test_plan_approval_lookup_failure_propagates_to_policy_cache() -> None:
    with patch(
        "agent.dashboard.team_settings._get_stored_team_settings",
        new_callable=AsyncMock,
        side_effect=RuntimeError("store unavailable"),
    ):
        with pytest.raises(RuntimeError, match="store unavailable"):
            await get_team_require_plan_approval()


@pytest.mark.asyncio
async def test_auto_merge_defaults_never_when_absent_or_invalid() -> None:
    for value in ({}, {"auto_merge_mode": "sometimes"}):
        with patch(
            "agent.dashboard.team_settings.get_team_settings",
            new_callable=AsyncMock,
            return_value=value,
        ):
            assert await get_team_auto_merge_mode() == AUTO_MERGE_NEVER


@pytest.mark.asyncio
async def test_auto_merge_mode_survives_settings_scrub() -> None:
    with patch(
        "agent.dashboard.team_settings._get_stored_team_settings",
        new_callable=AsyncMock,
        return_value={"auto_merge_mode": AUTO_MERGE_ALWAYS},
    ):
        assert await get_team_auto_merge_mode() == AUTO_MERGE_ALWAYS


@pytest.mark.asyncio
async def test_upsert_preserves_and_sets_auto_merge_mode() -> None:
    client, put = _put_capture()
    with (
        patch(
            "agent.dashboard.team_settings._get_stored_team_settings",
            new_callable=AsyncMock,
            return_value={"auto_merge_mode": AUTO_MERGE_ALWAYS},
        ),
        patch("agent.dashboard.team_settings._client", return_value=client),
    ):
        preserved = await upsert_team_settings(TeamSettingsUpdate())
        updated = await upsert_team_settings(
            TeamSettingsUpdate(auto_merge_mode=AUTO_MERGE_ON_PLAN_APPROVAL)
        )

    assert preserved["auto_merge_mode"] == AUTO_MERGE_ALWAYS
    assert updated["auto_merge_mode"] == AUTO_MERGE_ON_PLAN_APPROVAL
    assert put.await_count == 2


def test_update_rejects_unknown_auto_merge_mode() -> None:
    with pytest.raises(ValueError):
        TeamSettingsUpdate(auto_merge_mode="sometimes")  # type: ignore[arg-type]
