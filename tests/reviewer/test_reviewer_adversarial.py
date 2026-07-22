from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import RunnableConfig
from langgraph.runtime import Runtime

from agent.reviewer import REVIEWER_PROMPT_TEMPLATE
from agent.reviewer_adversarial import (
    RESERVED_SUBAGENT_TOOLS,
    PrepareAdversarialReviewerRunMiddleware,
    get_reviewer_adversarial_agent,
)
from agent.utils.agent_definitions import (
    build_subagents,
    list_agent_definitions,
    load_agent_definition,
)


def test_registered_in_langgraph_json_and_importable() -> None:
    config = json.loads(Path("langgraph.json").read_text(encoding="utf-8"))
    assert config["graphs"]["reviewer_adversarial"] == (
        "agent.graphs.reviewer_adversarial:traced_reviewer_adversarial"
    )

    module = importlib.import_module("agent.graphs.reviewer_adversarial")
    assert hasattr(module, "traced_reviewer_adversarial")
    assert hasattr(module, "get_reviewer_adversarial_agent")


def test_shipped_definition_shape() -> None:
    definition = load_agent_definition("reviewer-adversarial")
    assert definition.description
    assert definition.tools == (
        "fetch_review_diff",
        "add_finding",
        "update_finding",
        "list_findings",
        "publish_review",
        "resolve_finding_thread",
        "reply_to_finding_thread",
        "web_search",
        "fetch_url",
        "http_request",
    )
    assert tuple(subagent.name for subagent in definition.subagents) == (
        "adjudicator",
        "correctness",
        "security",
    )
    assert all(subagent.tools == () for subagent in definition.subagents)

    model = cast(BaseChatModel, MagicMock())
    specs = build_subagents(
        definition,
        model=model,
        reserved_tools=RESERVED_SUBAGENT_TOOLS,
    )
    assert all(spec.get("tools") == [] for spec in specs)


def test_discovery_finds_exactly_the_shipped_definition() -> None:
    assert list_agent_definitions() == ("reviewer-adversarial",)


@pytest.mark.asyncio
async def test_config_isolation() -> None:
    callback = object()
    configurable_value = object()
    callbacks = [callback]
    config = cast(
        RunnableConfig,
        {
            "configurable": {
                "thread_id": None,
                "custom_key": configurable_value,
            },
            "callbacks": callbacks,
            "recursion_limit": 25,
        },
    )
    fake = MagicMock()
    fake.with_config = MagicMock(return_value=fake)

    with patch("agent.reviewer_adversarial.create_deep_agent", return_value=fake):
        await get_reviewer_adversarial_agent(config)

        bound = cast(RunnableConfig, fake.with_config.call_args.args[0])
        bound_configurable = cast(dict[str, object], bound.get("configurable"))
        original_configurable = cast(dict[str, object], config.get("configurable"))
        assert bound is not config
        assert bound_configurable is not original_configurable
        assert bound_configurable["custom_key"] is configurable_value
        assert bound.get("callbacks") is callbacks
        assert config.get("recursion_limit") == 25

        default_config: RunnableConfig = {"configurable": {"thread_id": None}}
        await get_reviewer_adversarial_agent(default_config)
        default_bound = cast(RunnableConfig, fake.with_config.call_args.args[0])
        assert "recursion_limit" not in default_config
        assert "recursion_limit" in default_bound


async def _run_prepare(
    middleware: PrepareAdversarialReviewerRunMiddleware,
) -> dict[str, Any]:
    updates = await middleware.abefore_agent(
        cast(AgentState, {"messages": []}),
        cast(Runtime[None], MagicMock()),
    )
    assert updates is not None
    return updates


@pytest.mark.asyncio
async def test_prepare_renders_definition_prompt() -> None:
    diff = (
        "diff --git a/example.py b/example.py\n"
        "--- a/example.py\n"
        "+++ b/example.py\n"
        "@@ -1 +1 @@\n"
        "-old = 1\n"
        "+new = 2\n"
    )
    base_configurable: dict[str, object] = {
        "thread_id": "adversarial-thread",
        "repo": {"owner": "test-owner", "name": "test-repo"},
        "pr_number": 7,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "pr_url": "https://github.com/test-owner/test-repo/pull/7",
    }
    backend = MagicMock()

    async def prepare(extra: dict[str, object] | None = None) -> dict[str, Any]:
        configurable = {**base_configurable, **(extra or {})}
        config = cast(RunnableConfig, {"configurable": configurable})
        middleware = PrepareAdversarialReviewerRunMiddleware(
            thread_id="adversarial-thread",
            config=config,
            use_gateway=False,
            review_profile_name="default",
            review_profile_body=REVIEWER_PROMPT_TEMPLATE,
        )
        return await _run_prepare(middleware)

    with (
        patch(
            "agent.reviewer_adversarial._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(backend, "token"),
        ),
        patch(
            "agent.reviewer_adversarial.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.reviewer_adversarial.prepare_review_repo",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.reviewer_adversarial.fetch_pr_diff",
            new_callable=AsyncMock,
            return_value=diff,
        ),
        patch(
            "agent.reviewer_adversarial.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(diff_text=diff),
        ),
        patch(
            "agent.reviewer_adversarial.fetch_pr_metadata",
            new_callable=AsyncMock,
            return_value=("A title", "A body"),
        ),
    ):
        updates = await prepare()
        prompt = cast(str, updates["rendered_system_prompt"])
        assert "A finding is a claim about a concrete failure" in prompt
        assert "Independent finder pass" in prompt
        assert "test-owner/test-repo#7" in prompt
        assert "/workspace/test-repo" in prompt
        assert "This is a first review" in prompt
        assert updates["diff_text"] == diff
        assert updates["diff_line_set"] is not None

        eval_updates = await prepare({"reviewer_eval": True})
        eval_prompt = cast(str, eval_updates["rendered_system_prompt"])
        assert "Eval mode — calibration" in eval_prompt
        assert "Pre-existing PR review threads" not in eval_prompt

        rejected_configs: tuple[dict[str, object], ...] = (
            {"re_review": True},
            {"reviewer_event": "finding_reply"},
            {"last_reviewed_sha": "c" * 40},
        )
        for rejected in rejected_configs:
            with pytest.raises(RuntimeError, match="first reviews only"):
                await prepare(rejected)


@pytest.mark.asyncio
async def test_prepare_materializes_diff_without_api_token() -> None:
    diff = (
        "diff --git a/example.py b/example.py\n"
        "--- a/example.py\n"
        "+++ b/example.py\n"
        "@@ -1 +1 @@\n"
        "-old = 1\n"
        "+new = 2\n"
    )
    config = cast(
        RunnableConfig,
        {
            "configurable": {
                "thread_id": "adversarial-thread",
                "repo": {"owner": "test-owner", "name": "test-repo"},
                "pr_number": 7,
                "base_sha": "a" * 40,
                "head_sha": "b" * 40,
                "reviewer_eval": True,
            }
        },
    )
    middleware = PrepareAdversarialReviewerRunMiddleware(
        thread_id="adversarial-thread",
        config=config,
        use_gateway=False,
        review_profile_name="default",
        review_profile_body=REVIEWER_PROMPT_TEMPLATE,
    )

    with (
        patch(
            "agent.reviewer_adversarial._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(MagicMock(), None),
        ),
        patch(
            "agent.reviewer_adversarial.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.reviewer_adversarial.prepare_review_repo",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.reviewer_adversarial.fetch_pr_diff",
            new_callable=AsyncMock,
        ) as fetch_diff,
        patch(
            "agent.reviewer_adversarial.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(diff_text=diff),
        ) as materialize,
        patch(
            "agent.reviewer_adversarial.fetch_pr_metadata",
            new_callable=AsyncMock,
        ) as fetch_metadata,
    ):
        updates = await _run_prepare(middleware)

    fetch_diff.assert_not_awaited()
    fetch_metadata.assert_not_awaited()
    assert materialize.await_args is not None
    assert materialize.await_args.kwargs["diff_text"] is None
    assert updates["diff_text"] == diff
    assert updates["diff_line_set"] is not None


@pytest.mark.asyncio
async def test_model_key_resolution() -> None:
    requested: list[str] = []
    fake = MagicMock()
    fake.with_config = MagicMock(return_value=fake)

    def make_model(model_id: str, **kwargs: object) -> MagicMock:
        del kwargs
        requested.append(model_id)
        return MagicMock()

    async def run(configurable: dict[str, object]) -> list[str]:
        requested.clear()
        config = cast(
            RunnableConfig,
            {
                "configurable": {
                    "thread_id": "adversarial-thread",
                    "__is_for_execution__": True,
                    **configurable,
                }
            },
        )
        await get_reviewer_adversarial_agent(config)
        return requested.copy()

    with (
        patch("agent.reviewer_adversarial.create_deep_agent", return_value=fake),
        patch("agent.reviewer_adversarial._make_model_or_defer", side_effect=make_model),
        patch(
            "agent.reviewer_adversarial._cached_reviewer_team_defaults",
            new_callable=AsyncMock,
            return_value=(("team-main", "low"), ("team-sub", "high")),
        ),
        patch(
            "agent.reviewer_adversarial._cached_review_profile_name",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.reviewer_adversarial._cached_gateway_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "agent.reviewer_adversarial.get_team_fable_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("agent.reviewer_adversarial.build_subagents", return_value=[]),
    ):
        assert await run({"reviewer_adversarial_model_id": "X"}) == ["X", "X"]
        assert await run(
            {
                "reviewer_adversarial_model_id": "X",
                "reviewer_adversarial_subagent_model_id": "Y",
            }
        ) == ["X", "Y"]
        assert await run({"reviewer_model_id": "should-be-ignored"}) == [
            "team-main",
            "team-sub",
        ]
        assert await run({"reviewer_eval": True, "reviewer_model_id": "E"}) == ["E", "E"]
        assert await run(
            {
                "eval": True,
                "reviewer_model_id": "E",
                "reviewer_subagent_model_id": "F",
            }
        ) == ["E", "F"]
        assert await run(
            {
                "reviewer_eval": True,
                "reviewer_adversarial_model_id": "X",
                "reviewer_model_id": "E",
            }
        ) == ["X", "X"]
