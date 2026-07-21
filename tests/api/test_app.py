from __future__ import annotations

import importlib
from typing import Any

import pytest

app_module = importlib.import_module("agent.api.app")
model_utils = importlib.import_module("agent.utils.model")
sandbox_utils = importlib.import_module("agent.utils.sandbox")
wakeup_tool = importlib.import_module("agent.tools.schedule_thread_wakeup")


class _FakeCrons:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def search(self, **_: Any) -> list[dict[str, Any]]:
        return [
            {
                "cron_id": "expired-wakeup",
                "metadata": {
                    "kind": "thread_wakeup",
                    "expires_at": "2020-01-01T00:00:00+00:00",
                },
            }
        ]

    async def delete(self, cron_id: str) -> None:
        self.deleted.append(cron_id)


class _FakeClient:
    def __init__(self) -> None:
        self.crons = _FakeCrons()


async def test_lifespan_purges_expired_wakeups_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()

    async def close_cached_models() -> None:
        return None

    monkeypatch.setattr(wakeup_tool, "get_client", lambda url: client)
    monkeypatch.setattr(sandbox_utils, "validate_sandbox_startup_config", lambda: None)
    monkeypatch.setattr(model_utils, "validate_local_dev_llm_config", lambda: None)
    monkeypatch.setattr(model_utils, "close_cached_models", close_cached_models)

    async with app_module.lifespan(app_module.app):
        assert client.crons.deleted == ["expired-wakeup"]
