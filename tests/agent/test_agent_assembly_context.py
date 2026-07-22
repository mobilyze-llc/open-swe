"""Assembly contract for the main agent's context-management + middleware wiring.

Locks in that `get_agent` hands a sandbox `backend` to `create_deep_agent` (which
is what makes deepagents auto-wire `FilesystemMiddleware` tool-result eviction and
`SummarizationMiddleware` history offloading), and that the redundant custom
`RepairOrphanedToolCallsMiddleware` is no longer added explicitly — the built-in
`PatchToolCallsMiddleware` that `create_deep_agent` adds covers it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import server
from agent.server import get_agent
from agent.utils.repo_prep import PreparedRepoSkills


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> _DummyAgent:
        self.config = config
        return self


def _base_config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "github_login": "octocat",
        },
        "metadata": {},
    }


async def _capture_create_deep_agent_kwargs(
    *,
    require_plan_approval: bool = False,
    configurable: dict[str, object] | None = None,
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs: object) -> _DummyAgent:
        captured.update(kwargs)
        return _DummyAgent()

    config = _base_config()
    config_values = config.get("configurable")
    assert isinstance(config_values, dict)
    if configurable:
        config_values.update(configurable)
    thread_update = AsyncMock()
    fake_client = MagicMock()
    fake_client.threads.update = thread_update

    with (
        patch("agent.server.client", fake_client),
        patch(
            "agent.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch("agent.server.resolve_triggering_user_identity", return_value=None),
        patch(
            "agent.server.ensure_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "agent.server.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.server._resolve_prompt_default_repo",
            new_callable=AsyncMock,
            return_value={"owner": "acme", "name": "widget"},
        ),
        patch(
            "agent.server.prepare_main_agent_repo_skills",
            new_callable=AsyncMock,
            return_value=PreparedRepoSkills(
                trusted_ref="a" * 40,
                sources=("/workspace/.agent-skills/acme/widget/.agents/skills/",),
            ),
        ),
        patch(
            "agent.server.get_team_default_model_pair",
            new_callable=AsyncMock,
            return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
        ),
        patch(
            "agent.server._cached_require_plan_approval",
            new_callable=AsyncMock,
            return_value=require_plan_approval,
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[MagicMock(), MagicMock()]),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        agent = await get_agent(config)

    captured["bound_config"] = agent.config
    captured["thread_update"] = thread_update
    return captured


@pytest.mark.asyncio
async def test_agent_is_built_with_a_backend_for_eviction_and_summarization() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    # The backend is what enables deepagents' auto-wired FilesystemMiddleware
    # eviction + SummarizationMiddleware offloading.
    assert callable(captured["backend"])


@pytest.mark.asyncio
async def test_agent_and_general_purpose_subagent_share_repo_skills() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    expected = ["/workspace/.agent-skills/acme/widget/.agents/skills/"]

    assert captured["skills"] == expected
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    general_purpose = next(
        subagent for subagent in subagents if subagent["name"] == "general-purpose"
    )
    assert general_purpose["skills"] == expected
    browser = next((subagent for subagent in subagents if subagent["name"] == "browser"), None)
    if browser is not None:
        assert "skills" not in browser


@pytest.mark.asyncio
async def test_agent_does_not_add_custom_repair_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    names = {type(m).__name__ for m in middleware}
    # Built-in PatchToolCallsMiddleware (added by create_deep_agent) replaces it.
    assert "RepairOrphanedToolCallsMiddleware" not in names
    assert "SanitizeOpenAIResponsesMiddleware" not in names


@pytest.mark.asyncio
async def test_agent_keeps_message_queue_and_step_limit_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    # The dashboard depends on check_message_queue_before_model; the step-limit
    # notifier must still fire when the lowered run budget is hit.
    present = {type(m).__name__ for m in middleware}
    assert "check_message_queue_before_model" in present
    assert "notify_step_limit_reached" in present


@pytest.mark.asyncio
async def test_agent_includes_report_platform_issue_tool() -> None:
    from agent.tools import report_platform_issue

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert report_platform_issue in tools


@pytest.mark.asyncio
async def test_task_retry_wraps_inside_tool_error_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    names = [type(m).__name__ for m in middleware]

    assert names.index("ToolErrorMiddleware") < names.index("ToolRetryMiddleware")


def _plan_mode_middleware(captured: dict[str, object]) -> server.PlanModeMiddleware:
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    return next(item for item in middleware if isinstance(item, server.PlanModeMiddleware))


def _prepare_run_middleware(captured: dict[str, object]) -> server.PrepareAgentRunMiddleware:
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    return next(item for item in middleware if isinstance(item, server.PrepareAgentRunMiddleware))


@pytest.mark.asyncio
async def test_plan_approval_policy_forces_plan_mode_at_run_construction() -> None:
    captured = await _capture_create_deep_agent_kwargs(require_plan_approval=True)

    plan_mode = _plan_mode_middleware(captured)
    prepare_run = _prepare_run_middleware(captured)
    bound_config = captured["bound_config"]
    assert isinstance(bound_config, dict)
    assert bound_config["configurable"]["plan_mode"] is True
    assert plan_mode._initial is True
    assert prepare_run._plan_mode is True
    assert plan_mode._excluded == server.PLAN_MODE_EXCLUDED_TOOLS | {"approve_plan"}


@pytest.mark.asyncio
async def test_plan_gate_bypass_skips_force_and_records_audit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agent.server")
    captured = await _capture_create_deep_agent_kwargs(
        require_plan_approval=True,
        configurable={"plan_gate_bypass": True},
    )

    plan_mode = _plan_mode_middleware(captured)
    assert plan_mode._initial is False
    assert plan_mode._excluded is server.PLAN_MODE_EXCLUDED_TOOLS
    thread_update = captured["thread_update"]
    assert isinstance(thread_update, AsyncMock)
    thread_update.assert_awaited_once()
    call = thread_update.await_args
    assert call is not None
    assert call.kwargs["thread_id"] == "thread-ctx"
    stamp = call.kwargs["metadata"]["plan_gate_bypass"]
    assert stamp["by"] == "octocat"
    datetime.fromisoformat(stamp["at"])
    assert "Plan gate bypass for thread thread-ctx by octocat" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_chosen_plan_mode_keeps_approve_plan_available() -> None:
    captured = await _capture_create_deep_agent_kwargs(
        require_plan_approval=True,
        configurable={"plan_mode": True},
    )

    plan_mode = _plan_mode_middleware(captured)
    assert plan_mode._initial is True
    assert plan_mode._excluded is server.PLAN_MODE_EXCLUDED_TOOLS
    assert "approve_plan" not in plan_mode._excluded


@pytest.mark.asyncio
async def test_policy_unset_preserves_legacy_tool_and_middleware_wiring() -> None:
    captured = await _capture_create_deep_agent_kwargs()

    tools = captured["tools"]
    assert isinstance(tools, list)
    assert [getattr(tool, "name", getattr(tool, "__name__", None)) for tool in tools] == [
        "http_request",
        "fetch_url",
        "web_search",
        "approve_plan",
        "enter_plan_mode",
        "save_plan",
        "linear_comment",
        "linear_create_issue",
        "linear_delete_issue",
        "linear_get_issue",
        "linear_get_issue_comments",
        "linear_list_teams",
        "linear_search_issues",
        "linear_update_issue",
        "open_pull_request",
        "request_pr_review",
        "report_platform_issue",
        "schedule_thread_wakeup",
        "slack_add_reaction",
        "slack_read_thread_messages",
        "slack_start_new_thread",
        "slack_thread_reply",
    ]
    plan_mode = _plan_mode_middleware(captured)
    assert plan_mode._initial is False
    assert plan_mode._excluded is server.PLAN_MODE_EXCLUDED_TOOLS
    bound_config = captured["bound_config"]
    assert isinstance(bound_config, dict)
    baseline_configurable = _base_config().get("configurable")
    assert bound_config["configurable"] == baseline_configurable
