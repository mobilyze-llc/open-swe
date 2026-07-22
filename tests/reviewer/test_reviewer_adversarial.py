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

from agent.middleware import ExcludeToolsMiddleware
from agent.reviewer import (
    REVIEW_STAGE_TOOL_NAMES,
    REVIEWER_EVAL_PROMPT_SUFFIX,
    REVIEWER_PROMPT_TEMPLATE,
    _repo_checkout_note,
)
from agent.reviewer_adversarial import (
    RESERVED_SUBAGENT_TOOLS,
    PrepareAdversarialReviewerRunMiddleware,
    _render_parent_prompt,
    get_reviewer_adversarial_agent,
)
from agent.utils.agent_definitions import (
    build_subagents,
    list_agent_definitions,
    load_agent_definition,
)
from agent.utils.stage_profiles import StageProfile, load_stage_profile


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
async def test_prepare_default_profile_matches_definition_prompt() -> None:
    config = cast(
        RunnableConfig,
        {
            "configurable": {
                "thread_id": "adversarial-thread",
                "repo": {"owner": "test-owner", "name": "test-repo"},
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
    ):
        updates = await _run_prepare(middleware)

    checkout_note = _repo_checkout_note(
        repo_ready=True,
        working_dir="/workspace/test-repo",
        repo_owner="test-owner",
        repo_name="test-repo",
        pr_number="",
        head_sha="",
    )
    assert updates["rendered_system_prompt"] == _render_parent_prompt(
        working_dir="/workspace/test-repo",
        repo_owner="test-owner",
        repo_name="test-repo",
        pr_number="",
        repo_checkout_note=checkout_note,
    )


@pytest.mark.asyncio
async def test_prepare_renders_definition_prompt(tmp_path: Path) -> None:
    profile_dir = tmp_path / "review"
    profile_dir.mkdir()
    (profile_dir / "adversarial-marker.md").write_text(
        "---\n{}\n---\nADVERSARIAL PROFILE MARKER {repo_owner}/{repo_name}",
        encoding="utf-8",
    )
    review_profile = load_stage_profile(
        "review",
        "adversarial-marker",
        allowed_tools=REVIEW_STAGE_TOOL_NAMES,
        root=tmp_path,
    )
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
            review_profile_name=review_profile.name,
            review_profile_body=review_profile.body,
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
        assert "ADVERSARIAL PROFILE MARKER test-owner/test-repo" not in prompt
        assert "Independent finder pass" in prompt
        assert "test-owner/test-repo#7" in prompt
        assert "/workspace/test-repo" in prompt
        assert "This is a first review" in prompt
        assert updates["diff_text"] == diff
        assert updates["diff_line_set"] is not None

        eval_updates = await prepare({"reviewer_eval": True})
        eval_prompt = cast(str, eval_updates["rendered_system_prompt"])
        assert REVIEWER_EVAL_PROMPT_SUFFIX in eval_prompt
        assert "ADVERSARIAL PROFILE MARKER test-owner/test-repo" not in eval_prompt
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


@pytest.mark.asyncio
async def test_custom_profile_applies_pins_while_body_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requested: list[str] = []
    fake = MagicMock()
    fake.with_config = MagicMock(return_value=fake)
    subagent: dict[str, Any] = {}
    profile = StageProfile(
        stage="review",
        name="custom-review",
        body="CUSTOM REVIEW PROFILE BODY",
        model="profile-model",
        reasoning_effort="high",
        tools=("fetch_review_diff",),
    )

    def make_model(model_id: str, **kwargs: object) -> MagicMock:
        del kwargs
        requested.append(model_id)
        return MagicMock()

    config = cast(
        RunnableConfig,
        {
            "configurable": {
                "thread_id": "adversarial-thread",
                "__is_for_execution__": True,
            }
        },
    )
    caplog.set_level("INFO", logger="agent.reviewer_adversarial")

    with (
        patch("agent.reviewer_adversarial.create_deep_agent", return_value=fake) as create_agent,
        patch("agent.reviewer_adversarial._make_model_or_defer", side_effect=make_model),
        patch(
            "agent.reviewer_adversarial._cached_reviewer_team_defaults",
            new_callable=AsyncMock,
            return_value=(("team-main", "low"), ("team-sub", "low")),
        ),
        patch(
            "agent.reviewer_adversarial._cached_review_profile_name",
            new_callable=AsyncMock,
            return_value=profile.name,
        ),
        patch("agent.reviewer_adversarial.resolve_stage_profile", return_value=profile),
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
        patch("agent.reviewer_adversarial.build_subagents", return_value=[subagent]),
    ):
        await get_reviewer_adversarial_agent(config)

    assert requested == ["profile-model", "profile-model"]
    assert "Ignoring review profile body 'custom-review'" in caplog.text

    kwargs = create_agent.call_args.kwargs
    middleware = cast(list[object], kwargs["middleware"])
    prepare = cast(PrepareAdversarialReviewerRunMiddleware, middleware[0])
    assert prepare._prepare_config_fingerprint()["review_profile"] == profile.name
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
    ):
        updates = await _run_prepare(prepare)
    assert profile.body not in cast(str, updates["rendered_system_prompt"])
    assert profile.tools is not None
    expected_tools = frozenset(profile.tools)
    parent_filter = next(item for item in middleware if isinstance(item, ExcludeToolsMiddleware))
    assert cast(Any, parent_filter)._allowed == expected_tools
    subagent_filter = cast(list[object], subagent["middleware"])[-1]
    assert isinstance(subagent_filter, ExcludeToolsMiddleware)
    assert cast(Any, subagent_filter)._allowed == expected_tools
