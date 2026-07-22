from __future__ import annotations

from typing import Any

import pytest

from agent.dashboard import team_settings as ts


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str):
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value


class _FakeClient:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(ts, "_client", lambda: _FakeClient(store))
    return store


@pytest.mark.asyncio
async def test_stage_profiles_survive_real_team_settings_scrub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stored() -> dict[str, Any]:
        return {"plan_profile": "careful-v2", "review_profile": "strict-v3"}

    monkeypatch.setattr(ts, "_get_stored_team_settings", stored)

    settings = await ts.get_team_settings()

    assert settings["plan_profile"] == "careful-v2"
    assert settings["review_profile"] == "strict-v3"
    assert await ts.get_team_stage_profile("plan") == "careful-v2"
    assert await ts.get_team_stage_profile("review") == "strict-v3"


@pytest.mark.asyncio
async def test_stage_profile_selection_round_trips_and_survives_unrelated_save(
    fake_store: _FakeStore,
) -> None:
    await ts.upsert_team_settings(
        ts.TeamSettingsUpdate(plan_profile="careful-v2", review_profile="strict-v3")
    )

    settings = await ts.get_team_settings()
    assert settings["plan_profile"] == "careful-v2"
    assert settings["review_profile"] == "strict-v3"

    await ts.upsert_team_settings(ts.TeamSettingsUpdate(org_guidelines="Flag unsafe changes."))
    settings = await ts.get_team_settings()
    assert settings["plan_profile"] == "careful-v2"
    assert settings["review_profile"] == "strict-v3"

    await ts.upsert_team_settings(ts.TeamSettingsUpdate(plan_profile=None))
    settings = await ts.get_team_settings()
    assert settings["plan_profile"] is None
    assert settings["review_profile"] == "strict-v3"


@pytest.mark.asyncio
async def test_legacy_settings_surface_default_profile_selections(fake_store: _FakeStore) -> None:
    settings = await ts.get_team_settings()

    assert settings["plan_profile"] is None
    assert settings["review_profile"] is None
