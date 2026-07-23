from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import reviewer
from agent.middleware.prepare_run import PrepareRunState
from agent.reviewer_adversarial import PrepareAdversarialReviewerRunMiddleware

DIFF = (
    "diff --git a/example.py b/example.py\n"
    "--- a/example.py\n"
    "+++ b/example.py\n"
    "@@ -1 +1 @@\n"
    "-old = 1\n"
    "+new = 2\n"
)
SNAPSHOT_DIR = Path(__file__).parent / "snapshots/reviewer_prepare"


def _config(*, eval_mode: bool, re_review: bool = False) -> RunnableConfig:
    configurable: dict[str, object] = {
        "thread_id": "reviewer-thread",
        "repo": {"owner": "test-owner", "name": "test-repo"},
        "pr_number": 7,
        "pr_url": "https://github.com/test-owner/test-repo/pull/7",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "reviewer_eval": eval_mode,
    }
    if re_review:
        configurable.update({"re_review": True, "last_reviewed_sha": "c" * 40})
    return cast(RunnableConfig, {"configurable": configurable})


def _common_patches(stack: ExitStack, *, token: str | None = "token") -> None:
    backend = MagicMock()
    stack.enter_context(
        patch(
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(backend, token),
        )
    )
    stack.enter_context(
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        )
    )
    stack.enter_context(
        patch("agent.reviewer.prepare_review_repo", new_callable=AsyncMock, return_value=True)
    )
    stack.enter_context(
        patch("agent.reviewer.fetch_agents_md", new_callable=AsyncMock, return_value=None)
    )
    stack.enter_context(
        patch("agent.reviewer.fetch_scoped_agents_md", new_callable=AsyncMock, return_value={})
    )
    stack.enter_context(
        patch("agent.reviewer.materialize_trusted_skills", new_callable=AsyncMock, return_value=[])
    )
    stack.enter_context(
        patch("agent.reviewer.fetch_pr_diff", new_callable=AsyncMock, return_value=DIFF)
    )
    stack.enter_context(
        patch(
            "agent.reviewer.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(diff_text=DIFF),
        )
    )
    stack.enter_context(
        patch(
            "agent.reviewer.fetch_pr_metadata",
            new_callable=AsyncMock,
            return_value=("A title", "A body"),
        )
    )


def _stock_patches(stack: ExitStack) -> None:
    stack.enter_context(
        patch("agent.reviewer.fetch_pr_review_threads", new_callable=AsyncMock, return_value=[])
    )
    stack.enter_context(
        patch("agent.reviewer.reconcile_findings_with_review_threads", new_callable=AsyncMock)
    )
    stack.enter_context(
        patch(
            "agent.dashboard.review_styles.get_repo_custom_prompt",
            new_callable=AsyncMock,
            return_value=None,
        )
    )
    stack.enter_context(
        patch(
            "agent.reviewer._cached_org_review_guidelines",
            new_callable=AsyncMock,
            return_value=None,
        )
    )
    stack.enter_context(
        patch(
            "agent.reviewer._cached_api_standards_skill", new_callable=AsyncMock, return_value=None
        )
    )
    stack.enter_context(
        patch("agent.reviewer.prepare_pr_trace_context", new_callable=AsyncMock, return_value=None)
    )
    stack.enter_context(
        patch("agent.reviewer.list_findings_async", new_callable=AsyncMock, return_value=[])
    )
    stack.enter_context(
        patch(
            "agent.reviewer._resolve_grouping_model",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        )
    )
    stack.enter_context(
        patch("agent.reviewer.maybe_generate_and_store_diff_groups", new_callable=AsyncMock)
    )


async def _prepare_stock(*, eval_mode: bool, re_review: bool) -> dict[str, Any]:
    middleware = reviewer.PrepareReviewerRunMiddleware(
        thread_id="reviewer-thread",
        config=_config(eval_mode=eval_mode, re_review=re_review),
        use_gateway=False,
        review_profile_name="default",
        review_profile_body=reviewer.REVIEWER_PROMPT_TEMPLATE,
    )
    with ExitStack() as stack:
        _common_patches(stack)
        _stock_patches(stack)
        result = await middleware._prepare(cast(PrepareRunState, {"messages": []}), MagicMock())
        await asyncio.sleep(0)
    return result


async def _prepare_adversarial(*, eval_mode: bool) -> dict[str, Any]:
    middleware = PrepareAdversarialReviewerRunMiddleware(
        thread_id="reviewer-thread",
        config=_config(eval_mode=eval_mode),
        use_gateway=False,
        review_profile_name="default",
        review_profile_body=reviewer.REVIEWER_PROMPT_TEMPLATE,
    )
    with ExitStack() as stack:
        _common_patches(stack)
        return await middleware._prepare(cast(PrepareRunState, {"messages": []}), MagicMock())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot_name", "eval_mode", "re_review"),
    [
        ("stock_first_non_eval", False, False),
        ("stock_first_eval", True, False),
        ("stock_re_review_non_eval", False, True),
        ("stock_re_review_eval", True, True),
    ],
)
async def test_stock_prepare_matches_pre_refactor_snapshot(
    snapshot_name: str, eval_mode: bool, re_review: bool
) -> None:
    result = await _prepare_stock(eval_mode=eval_mode, re_review=re_review)

    assert result == {
        "work_dir": "/workspace",
        "rendered_system_prompt": (SNAPSHOT_DIR / f"{snapshot_name}.txt").read_text(),
        "diff_text": DIFF,
        "diff_line_set": {"example.py": {"RIGHT": {1}, "LEFT": {1}}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot_name", "eval_mode"),
    [("adversarial_non_eval", False), ("adversarial_eval", True)],
)
async def test_adversarial_prepare_matches_pre_refactor_snapshot(
    snapshot_name: str, eval_mode: bool
) -> None:
    result = await _prepare_adversarial(eval_mode=eval_mode)

    assert result["work_dir"] == "/workspace"
    assert result["diff_text"] == DIFF
    assert result["diff_line_set"] == {"example.py": {"RIGHT": {1}, "LEFT": {1}}}
    rendered = cast(str, result["rendered_system_prompt"])
    snapshot = (SNAPSHOT_DIR / f"{snapshot_name}.txt").read_text()
    shared = snapshot.split("\n\nYou are an adversarial code reviewer agent.", 1)[0]
    context = (
        "\n\n## Pull request to review" + snapshot.split("\n\n## Pull request to review", 1)[1]
    )
    assert rendered.startswith(shared)
    assert rendered.endswith(context)
    assert "parent adjudicator" in rendered


@pytest.mark.asyncio
async def test_stock_prepare_consumes_bundle_skill_sources_once() -> None:
    backend = MagicMock()
    bundle = reviewer.ReviewContextBundle(
        sandbox_backend=backend,
        github_token="token",
        work_dir="/workspace",
        repo_owner="test-owner",
        repo_name="test-repo",
        pr_number=7,
        pr_url="https://github.com/test-owner/test-repo/pull/7",
        base_sha="a" * 40,
        head_sha="b" * 40,
        repo_ready=True,
        reviewer_eval=False,
        diff_text=DIFF,
        diff_line_set={"example.py": {"RIGHT": {1}, "LEFT": {1}}},
        pr_title="A title",
        pr_body="A body",
        skill_sources=["/workspace/.open-swe/trusted-skills/.agents/skills/"],
    )
    skills = MagicMock()
    skills.abefore_agent = AsyncMock(return_value={"skills_metadata": [], "skills_load_errors": []})
    skills._format_skills_locations.return_value = "SKILL LOCATIONS"
    skills._format_skills_list.return_value = "SKILL LIST"
    skills._format_skills_load_warnings.return_value = ""
    skills.system_prompt_template = "{skills_locations}\n{skills_load_warnings}\n{skills_list}"
    middleware = reviewer.PrepareReviewerRunMiddleware(
        thread_id="reviewer-thread",
        config=_config(eval_mode=False),
        use_gateway=False,
        review_profile_name="default",
        review_profile_body=reviewer.REVIEWER_PROMPT_TEMPLATE,
    )

    with (
        patch(
            "agent.reviewer.gather_review_context",
            new_callable=AsyncMock,
            return_value=bundle,
        ),
        patch("agent.reviewer.SkillsMiddleware", return_value=skills) as skills_type,
        patch("agent.reviewer._schedule_diff_grouping", new_callable=AsyncMock),
    ):
        result = await middleware._prepare(cast(PrepareRunState, {"messages": []}), MagicMock())

    skills_type.assert_called_once_with(backend=backend, sources=bundle.skill_sources)
    skills.abefore_agent.assert_awaited_once()
    assert "SKILL LOCATIONS" in result["rendered_system_prompt"]
    assert "SKILL LIST" in result["rendered_system_prompt"]


@pytest.mark.asyncio
async def test_gather_review_context_returns_shared_parent_inputs() -> None:
    config = cast(dict[str, Any], _config(eval_mode=False).get("configurable") or {})
    trace_context = MagicMock()

    with ExitStack() as stack:
        _common_patches(stack)
        stack.enter_context(
            patch(
                "agent.reviewer._fetch_existing_threads_block",
                new_callable=AsyncMock,
                return_value="THREADS",
            )
        )
        stack.enter_context(
            patch(
                "agent.reviewer._fetch_repo_style_prompt",
                new_callable=AsyncMock,
                return_value="STYLE",
            )
        )
        stack.enter_context(
            patch(
                "agent.reviewer._cached_org_review_guidelines",
                new_callable=AsyncMock,
                return_value="ORG",
            )
        )
        stack.enter_context(
            patch(
                "agent.reviewer._cached_api_standards_skill",
                new_callable=AsyncMock,
                return_value="API",
            )
        )
        stack.enter_context(
            patch(
                "agent.reviewer._prepare_pr_trace_context_best_effort",
                new_callable=AsyncMock,
                return_value=trace_context,
            )
        )
        fetch_agents = stack.enter_context(
            patch(
                "agent.reviewer.fetch_agents_md",
                new_callable=AsyncMock,
                return_value="ROOT RULE",
            )
        )
        fetch_scoped = stack.enter_context(
            patch(
                "agent.reviewer.fetch_scoped_agents_md",
                new_callable=AsyncMock,
                return_value={"src/AGENTS.md": "SCOPED RULE"},
            )
        )
        materialize_skills = stack.enter_context(
            patch(
                "agent.reviewer.materialize_trusted_skills",
                new_callable=AsyncMock,
                return_value=["/workspace/.open-swe/trusted-skills/.agents/skills/"],
            )
        )
        bundle = await reviewer.gather_review_context(
            "reviewer-thread", config, diff_mode="adversarial"
        )

    assert bundle.existing_threads_block == "THREADS"
    assert bundle.org_guidelines == "ORG"
    assert bundle.repo_style_prompt == "STYLE"
    assert bundle.api_standards_skill == "API"
    assert bundle.pr_trace_context is trace_context
    assert bundle.agents_md_content == "ROOT RULE"
    assert bundle.scoped_agents_md == {"src/AGENTS.md": "SCOPED RULE"}
    assert bundle.skill_sources == ["/workspace/.open-swe/trusted-skills/.agents/skills/"]
    fetch_agents.assert_awaited_once_with("test-owner", "test-repo", "a" * 40, token="token")
    fetch_scoped.assert_awaited_once_with(
        "test-owner",
        "test-repo",
        "a" * 40,
        ["example.py"],
        token="token",
    )
    materialize_skills.assert_awaited_once_with(
        bundle.sandbox_backend,
        repo_dir="/workspace/test-repo",
        trusted_ref="a" * 40,
    )


@pytest.mark.asyncio
async def test_gather_review_context_fetches_both_rename_scopes() -> None:
    config = cast(dict[str, Any], _config(eval_mode=False).get("configurable") or {})
    rename_diff = (
        "diff --git a/legacy/foo.py b/src/foo.py\n"
        "--- a/legacy/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old = 1\n"
        "+new = 2\n"
    )

    with ExitStack() as stack:
        _common_patches(stack)
        stack.enter_context(
            patch(
                "agent.reviewer.materialize_review_diff",
                new_callable=AsyncMock,
                return_value=MagicMock(diff_text=rename_diff),
            )
        )
        fetch_scoped = stack.enter_context(
            patch(
                "agent.reviewer.fetch_scoped_agents_md",
                new_callable=AsyncMock,
                return_value={},
            )
        )
        await reviewer.gather_review_context("reviewer-thread", config, diff_mode="adversarial")

    fetch_scoped.assert_awaited_once_with(
        "test-owner",
        "test-repo",
        "a" * 40,
        ["legacy/foo.py", "src/foo.py"],
        token="token",
    )


@pytest.mark.asyncio
async def test_gather_review_context_preserves_diff_policy_split() -> None:
    config = cast(dict[str, Any], _config(eval_mode=False).get("configurable") or {})
    backend = MagicMock()

    with (
        patch(
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(backend, None),
        ),
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch("agent.reviewer.prepare_review_repo", new_callable=AsyncMock, return_value=True),
        patch("agent.reviewer.fetch_pr_diff", new_callable=AsyncMock) as fetch_diff,
        patch(
            "agent.reviewer.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(diff_text=DIFF),
        ) as materialize,
        patch("agent.reviewer.fetch_pr_metadata", new_callable=AsyncMock) as fetch_metadata,
    ):
        stock = await reviewer.gather_review_context("reviewer-thread", config)
        adversarial = await reviewer.gather_review_context(
            "reviewer-thread", config, diff_mode="adversarial"
        )

    assert stock.diff_text == ""
    assert stock.diff_line_set is None
    assert adversarial.diff_text == DIFF
    assert adversarial.diff_line_set == {"example.py": {"RIGHT": {1}, "LEFT": {1}}}
    fetch_diff.assert_not_awaited()
    fetch_metadata.assert_not_awaited()
    materialize.assert_awaited_once()
    assert materialize.await_args is not None
    assert materialize.await_args.kwargs["diff_text"] is None


@pytest.mark.asyncio
async def test_gather_review_context_preserves_empty_diff_line_sets() -> None:
    config = cast(dict[str, Any], _config(eval_mode=False).get("configurable") or {})

    with (
        patch(
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(MagicMock(), "token"),
        ),
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch("agent.reviewer.prepare_review_repo", new_callable=AsyncMock, return_value=True),
        patch("agent.reviewer.fetch_pr_diff", new_callable=AsyncMock, return_value=DIFF),
        patch(
            "agent.reviewer.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(diff_text=""),
        ),
        patch(
            "agent.reviewer.fetch_pr_metadata",
            new_callable=AsyncMock,
            return_value=("", ""),
        ),
    ):
        stock = await reviewer.gather_review_context("reviewer-thread", config)
        adversarial = await reviewer.gather_review_context(
            "reviewer-thread", config, diff_mode="adversarial"
        )

    assert stock.diff_text == ""
    assert stock.diff_line_set == {}
    assert adversarial.diff_text == ""
    assert adversarial.diff_line_set is None


def test_stock_prepare_fingerprint_keeps_review_profile() -> None:
    config = cast(
        RunnableConfig,
        {
            "configurable": {
                "prepare_run_id": "prepare-1",
                "repo": {"owner": "test-owner", "name": "test-repo"},
                "pr_number": 7,
                "base_sha": "base",
                "head_sha": "head",
                "last_reviewed_sha": "last",
                "reviewer_event": "finding_reply",
                "reviewer_eval": True,
                "eval": True,
                "finding_reply_id": "finding-1",
            }
        },
    )
    middleware = reviewer.PrepareReviewerRunMiddleware(
        thread_id="reviewer-thread",
        config=config,
        use_gateway=False,
        review_profile_name="custom-profile",
        review_profile_body="profile body",
    )

    assert middleware._prepare_config_fingerprint() == {
        "prepare_run_id": "prepare-1",
        "thread_id": "reviewer-thread",
        "review_profile": "custom-profile",
        "repo": {"owner": "test-owner", "name": "test-repo"},
        "pr_number": 7,
        "base_sha": "base",
        "head_sha": "head",
        "last_reviewed_sha": "last",
        "reviewer_event": "finding_reply",
        "reviewer_eval": True,
        "eval": True,
        "finding_reply_id": "finding-1",
    }
