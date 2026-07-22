from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from agent.middleware.exclude_tools import ExcludeToolsMiddleware
from agent.middleware.plan_mode import PlanModeMiddleware
from agent.prompt import PLAN_MODE_SECTION, construct_system_prompt
from agent.reviewer import (
    REVIEW_STAGE_TOOL_NAMES,
    REVIEWER_PROMPT_TEMPLATE,
    _reviewer_system_prompt,
)
from agent.server import PLAN_STAGE_TOOL_NAMES
from agent.utils.stage_profiles import (
    StageProfileError,
    load_stage_profile,
    resolve_stage_profile,
)


def _write_profile(root: Path, stage: str, name: str, frontmatter: str, body: str) -> None:
    directory = root / stage
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")


def test_default_plan_profile_is_byte_identical() -> None:
    profile = load_stage_profile("plan", "default", allowed_tools=PLAN_STAGE_TOOL_NAMES)

    assert profile.body == PLAN_MODE_SECTION
    assert construct_system_prompt(working_dir="/work", plan_mode=True) == construct_system_prompt(
        working_dir="/work",
        plan_mode=True,
        plan_profile_body=profile.body,
    )


def test_default_review_profile_is_byte_identical() -> None:
    profile = load_stage_profile("review", "default", allowed_tools=REVIEW_STAGE_TOOL_NAMES)

    assert profile.body == REVIEWER_PROMPT_TEMPLATE
    expected = _reviewer_system_prompt(
        "/work/repo", repo_owner="acme", repo_name="repo", pr_number=7
    )
    actual = _reviewer_system_prompt(
        "/work/repo",
        repo_owner="acme",
        repo_name="repo",
        pr_number=7,
        profile_body=profile.body,
    )
    assert actual == expected


def test_non_default_profile_changes_assembled_prompt(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        "plan",
        "concise-v2",
        "tools:\n  - read_file\n  - save_plan",
        "CUSTOM PLAN PROFILE at {plan_url}",
    )
    profile = load_stage_profile(
        "plan", "concise-v2", allowed_tools=PLAN_STAGE_TOOL_NAMES, root=tmp_path
    )

    prompt = construct_system_prompt(
        working_dir="/work",
        plan_mode=True,
        plan_url="https://plans.example/7",
        plan_profile_body=profile.body,
    )

    assert "CUSTOM PLAN PROFILE at https://plans.example/7" in prompt
    assert "Plan Mode (ACTIVE)" not in prompt


def test_profile_honors_model_effort_and_tool_restriction(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        "review",
        "focused",
        "model: openai:gpt-5.6-sol\nreasoning_effort: medium\ntools:\n  - read_file\n  - add_finding",
        "Review {repo_owner}/{repo_name}#{pr_number}.",
    )

    profile = load_stage_profile(
        "review", "focused", allowed_tools=REVIEW_STAGE_TOOL_NAMES, root=tmp_path
    )

    assert profile.model == "openai:gpt-5.6-sol"
    assert profile.reasoning_effort == "medium"
    assert profile.tools == ("read_file", "add_finding")


def test_profile_rejects_additive_tool(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        "plan",
        "additive",
        "tools:\n  - open_pull_request",
        "Plan safely.",
    )

    with pytest.raises(StageProfileError, match="profiles may only restrict tools"):
        load_stage_profile("plan", "additive", allowed_tools=PLAN_STAGE_TOOL_NAMES, root=tmp_path)


def test_profile_rejects_unknown_capability_fields(tmp_path: Path) -> None:
    _write_profile(
        tmp_path,
        "review",
        "capabilities",
        "mcp_servers:\n  - internal\nsubagents:\n  - audit",
        "Review carefully.",
    )

    with pytest.raises(StageProfileError) as exc_info:
        load_stage_profile(
            "review", "capabilities", allowed_tools=REVIEW_STAGE_TOOL_NAMES, root=tmp_path
        )

    assert "unknown frontmatter key 'mcp_servers'" in str(exc_info.value)
    assert "unknown frontmatter key 'subagents'" in str(exc_info.value)
    assert "cannot declare capabilities" in str(exc_info.value)


def test_invalid_selection_falls_back_to_default(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_profile(tmp_path, "plan", "default", "{}", PLAN_MODE_SECTION)
    _write_profile(tmp_path, "plan", "broken", "tools:\n  - open_pull_request", "Broken.")

    profile = resolve_stage_profile(
        "plan",
        "broken",
        allowed_tools=PLAN_STAGE_TOOL_NAMES,
        fallback_body=PLAN_MODE_SECTION,
        root=tmp_path,
    )

    assert profile.name == "default"
    assert profile.body == PLAN_MODE_SECTION
    assert "falling back to default" in caplog.text


class _Request:
    def __init__(self, tools: list[dict[str, str]], state: dict[str, Any] | None = None) -> None:
        self.tools = tools
        self.state = state or {}

    def override(self, **kwargs: Any) -> _Request:
        return _Request(kwargs.get("tools", self.tools), self.state)


def test_plan_profile_tool_restriction_is_applied_at_model_request() -> None:
    middleware = PlanModeMiddleware(
        excluded=frozenset(), allowed=frozenset({"read_file", "save_plan"}), initial=True
    )
    request = _Request([{"name": "read_file"}, {"name": "save_plan"}, {"name": "web_search"}])

    filtered = cast(_Request, middleware._filter(cast(Any, request)))

    assert [tool["name"] for tool in filtered.tools] == ["read_file", "save_plan"]


def test_review_profile_tool_restriction_is_applied_at_model_request() -> None:
    middleware = ExcludeToolsMiddleware(allowed=frozenset({"read_file", "add_finding"}))
    request = _Request([{"name": "read_file"}, {"name": "add_finding"}, {"name": "execute"}])

    filtered = cast(_Request, middleware._filter(cast(Any, request)))

    assert [tool["name"] for tool in filtered.tools] == ["read_file", "add_finding"]
