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
    prompt_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("agent/reviewer-adversarial").rglob("*.md")
    )
    assert "zero-findings re-walk" not in prompt_text
    assert "same-file independence" not in prompt_text
    assert "top changed" not in prompt_text

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
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(MagicMock(), None),
        ),
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.reviewer.prepare_review_repo",
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
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(backend, "token"),
        ),
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.reviewer.prepare_review_repo",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.reviewer.fetch_pr_diff",
            new_callable=AsyncMock,
            return_value=diff,
        ),
        patch(
            "agent.reviewer.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(diff_text=diff),
        ),
        patch(
            "agent.reviewer.fetch_pr_metadata",
            new_callable=AsyncMock,
            return_value=("A title", "A body"),
        ),
    ):
        updates = await prepare()
        prompt = cast(str, updates["rendered_system_prompt"])
        assert "A finding is a claim about a concrete failure" in prompt
        assert "ADVERSARIAL PROFILE MARKER test-owner/test-repo" not in prompt
        assert "parent adjudicator" in prompt
        assert "Independent finder pass" not in prompt
        assert "test-owner/test-repo#7" in prompt
        assert "/workspace/test-repo" in prompt
        assert "This is a first review" in prompt
        assert "Follow the review workflow in your instructions." in prompt
        assert "Review using the ordered passes" not in prompt
        assert "mechanical" + " grep" not in prompt
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
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=(MagicMock(), None),
        ),
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.reviewer.prepare_review_repo",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.reviewer.fetch_pr_diff",
            new_callable=AsyncMock,
        ) as fetch_diff,
        patch(
            "agent.reviewer.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(diff_text=diff),
        ) as materialize,
        patch(
            "agent.reviewer.fetch_pr_metadata",
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

    fake_stage = MagicMock()
    fake_graph = MagicMock()
    fake_graph.with_config.return_value = fake_graph
    config = cast(
        RunnableConfig,
        {"configurable": {"thread_id": "adversarial-thread", "__is_for_execution__": True}},
    )
    caplog.set_level("INFO", logger="agent.reviewer_adversarial")
    with (
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
        patch("agent.reviewer_adversarial._bounded_agent", return_value=fake_stage) as bounded,
        patch("agent.reviewer_adversarial.StateGraph.compile", return_value=fake_graph),
    ):
        result = await get_reviewer_adversarial_agent(config)

    assert result is fake_graph
    assert requested == ["profile-model", "profile-model"]
    assert "Ignoring review profile body 'custom-review'" in caplog.text
    assert profile.body not in str(bounded.call_args_list)
    assert any(
        isinstance(item, ExcludeToolsMiddleware)
        for call in bounded.call_args_list
        for item in call.kwargs.get("middleware", [])
    )


def _candidate(candidate_id: str, file: str = "src/app.py") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "file": file,
        "start_line": 10,
        "end_line": 10,
        "quoted_line": "changed()",
        "failure_mode": f"failure {candidate_id}",
        "severity": "high",
        "category": "correctness",
        "side": "RIGHT",
    }


def test_publication_blocker_rejects_incomplete_finders_and_verdicts() -> None:
    from agent.review.adversarial import publication_blocker

    candidate = _candidate("c1")
    incomplete = {
        "finders_expected": ["correctness", "security"],
        "finder_results": [{"finder": "correctness", "candidates": [], "error": None}],
        "candidates": [candidate],
        "verdicts": [{"candidate_id": "c1", "verdict": "keep-confirmed", "evidence": "x"}],
    }
    assert publication_blocker(cast(Any, incomplete)) == "finder fanout incomplete or failed"

    unadjudicated = {
        **incomplete,
        "finder_results": [
            {"finder": "correctness", "candidates": [], "error": None},
            {"finder": "security", "candidates": [], "error": None},
        ],
        "verdicts": [],
    }
    assert "every candidate ID" in str(publication_blocker(cast(Any, unadjudicated)))


def test_all_prepublish_gates_fire_on_their_triggers() -> None:
    from agent.review.adversarial import gate_triggers

    production_diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    triggers, _ = gate_triggers(production_diff, [])
    assert triggers == ["zero-findings", "uncovered-major-prefix"]

    kept = [_candidate("c1"), _candidate("c2")]
    triggers, collisions = gate_triggers(production_diff, cast(Any, kept))
    assert triggers == ["same-file-independence"]
    assert collisions == [["c1", "c2"]]


def test_gate_classification_excludes_nonproduction_and_includes_major_ties() -> None:
    from agent.review.adversarial import gate_triggers

    docs_diff = "diff --git a/docs/a.md b/docs/a.md\n--- a/docs/a.md\n+++ b/docs/a.md\n-old\n+new\n"
    assert gate_triggers(docs_diff, [])[0] == ["uncovered-major-prefix"]
    root_docs = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n-old\n+new\n"
    assert gate_triggers(root_docs, [])[0] == ["uncovered-major-prefix"]
    tied = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n-old\n+new\n"
        "diff --git a/lib/b.py b/lib/b.py\n--- a/lib/b.py\n+++ b/lib/b.py\n-old\n+new\n"
    )
    triggers, _ = gate_triggers(tied, cast(Any, [_candidate("c1")]))
    assert triggers == ["uncovered-major-prefix"]


@pytest.mark.asyncio
async def test_prepare_node_materializes_gathered_degraded_diff() -> None:
    from agent.reviewer import ReviewContextBundle
    from agent.reviewer_adversarial import _prepare_context

    backend = MagicMock()
    bundle = ReviewContextBundle(
        sandbox_backend=backend,
        github_token="token",
        work_dir="/workspace",
        repo_owner="owner",
        repo_name="repo",
        pr_number=7,
        pr_url="https://github.com/owner/repo/pull/7",
        base_sha="a" * 40,
        head_sha="b" * 40,
        repo_ready=False,
        reviewer_eval=False,
        diff_text="api-backed diff",
        diff_line_set={},
        pr_title="title",
        pr_body="body",
    )
    with (
        patch(
            "agent.reviewer_adversarial.gather_review_context",
            new_callable=AsyncMock,
            return_value=bundle,
        ),
        patch(
            "agent.reviewer_adversarial.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=MagicMock(path="/workspace/repo/review.patch"),
        ) as materialize,
    ):
        prepared = await _prepare_context("thread", {}, materialize_path=True)

    assert prepared["diff_path"] == "/workspace/repo/review.patch"
    assert materialize.await_args is not None
    assert materialize.await_args.kwargs["diff_text"] == "api-backed diff"


def test_gate_policy_handles_quoted_paths() -> None:
    from agent.review.adversarial import changed_prefix_counts

    quoted = (
        'diff --git "a/src/my file.py" "b/src/my file.py"\n'
        '--- "a/src/my file.py"\n+++ "b/src/my file.py"\n-old\n+new\n'
    )
    assert changed_prefix_counts(quoted) == {"src": 2}


def test_dedupe_merges_only_confirmed_cross_file_locations() -> None:
    from pydantic import ValidationError

    from agent.review.adversarial import CandidateDraft, dedupe_candidates, merge_kept_candidates

    candidates = dedupe_candidates(
        [
            {
                "file": "src/a.py",
                "start_line": 4,
                "end_line": 4,
                "quoted_line": "removed_guard()",
                "failure_mode": "missing guard allows invalid state",
                "severity": "high",
                "side": "LEFT",
            },
            {
                "file": "lib/b.py",
                "start_line": 9,
                "end_line": 9,
                "quoted_line": "removed_guard()",
                "failure_mode": "  Missing guard allows invalid state ",
                "severity": "medium",
                "side": "LEFT",
            },
        ]
    )
    assert len(candidates) == 2
    assert [item["affected_locations"] for item in candidates] == [
        ["lib/b.py:9-9 (LEFT)"],
        ["src/a.py:4-4 (LEFT)"],
    ]
    assert merge_kept_candidates([candidates[0]])[0]["affected_locations"] == [
        "lib/b.py:9-9 (LEFT)"
    ]
    confirmed = merge_kept_candidates(candidates)
    assert len(confirmed) == 1
    assert confirmed[0]["affected_locations"] == [
        "lib/b.py:9-9 (LEFT)",
        "src/a.py:4-4 (LEFT)",
    ]
    gate_duplicate = {
        **confirmed[0],
        "candidate_id": "g1",
        "file": "other/c.py",
        "start_line": 12,
        "end_line": 12,
        "quoted_line": "removed_guard()",
        "affected_locations": ["other/c.py:12-12 (LEFT)"],
    }
    merged = merge_kept_candidates([confirmed[0], gate_duplicate])
    assert merged[0]["affected_locations"] == [
        "lib/b.py:9-9 (LEFT)",
        "src/a.py:4-4 (LEFT)",
        "other/c.py:12-12 (LEFT)",
    ]
    with pytest.raises(ValidationError):
        CandidateDraft.model_validate(
            {
                "file": "src/a.py",
                "start_line": 1,
                "end_line": 1,
                "quoted_line": "removed",
                "failure_mode": "missing side",
                "severity": "high",
            }
        )
    with pytest.raises(ValidationError):
        CandidateDraft.model_validate(
            {
                "file": "src/a.py",
                "start_line": 1,
                "end_line": 1,
                "quoted_line": "removed",
                "failure_mode": "fabricated locations",
                "severity": "high",
                "side": "LEFT",
                "affected_locations": ["fake.py:1-1 (RIGHT)"],
            }
        )


def test_gate_added_same_file_candidate_requires_independence() -> None:
    from agent.review.adversarial import IndependenceDecision, apply_independence, gate_triggers

    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    candidates = [
        cast(dict[str, Any], _candidate("c1")),
        cast(dict[str, Any], _candidate("g1")),
    ]
    triggers, collisions = gate_triggers(diff, candidates)
    assert triggers == ["same-file-independence"]
    result = apply_independence(
        candidates,
        collisions,
        [
            IndependenceDecision(
                candidate_ids=["c1", "g1"],
                independent=False,
                keep_candidate_ids=["c1"],
                rationale="same failure",
            )
        ],
    )
    assert [item["candidate_id"] for item in result] == ["c1"]


@pytest.mark.asyncio
async def test_finder_timeout_fails_closed_and_settles_terminal_check() -> None:
    from agent.review.adversarial import FinderOutput

    stages = [object() for _ in range(5)]
    stage_iter = iter(stages)

    async def run_stage(graph: object, *_args: object, **_kwargs: object) -> FinderOutput:
        if graph is stages[2]:
            raise TimeoutError("security finder timed out")
        return FinderOutput(candidates=[])

    config = cast(
        RunnableConfig,
        {"configurable": {"thread_id": "adversarial-thread", "__is_for_execution__": True}},
    )
    with (
        patch(
            "agent.reviewer_adversarial._cached_reviewer_team_defaults",
            new_callable=AsyncMock,
            return_value=(("team-main", "low"), ("team-sub", "low")),
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
        patch("agent.reviewer_adversarial._make_model_or_defer", return_value=MagicMock()),
        patch(
            "agent.reviewer_adversarial._bounded_agent",
            side_effect=lambda **_kwargs: next(stage_iter),
        ),
        patch(
            "agent.reviewer_adversarial._prepare_context",
            new_callable=AsyncMock,
            return_value={
                "work_dir": "/workspace",
                "working_dir": "/workspace/repo",
                "rendered_system_prompt": "prompt",
                "diff_text": "",
                "diff_line_set": None,
                "pr_title": "title",
                "diff_path": "/tmp/review.diff",
            },
        ),
        patch("agent.reviewer_adversarial._run_stage", side_effect=run_stage),
        patch("agent.reviewer_adversarial.agent_tools.add_finding", new_callable=AsyncMock) as add,
        patch(
            "agent.reviewer_adversarial.agent_tools.publish_review", new_callable=AsyncMock
        ) as publish,
        patch.object(
            __import__(
                "agent.reviewer_adversarial", fromlist=["settle_review_check_on_exit"]
            ).settle_review_check_on_exit,
            "aafter_agent",
            new_callable=AsyncMock,
        ) as settle,
    ):
        graph = await get_reviewer_adversarial_agent(config)
        result = await graph.ainvoke({"messages": []})

    assert result["error"] == "finder fanout incomplete or failed"
    add.assert_not_awaited()
    publish.assert_not_awaited()
    settle.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("complete_verdicts", "publishes"), [(False, False), (True, True)])
async def test_adjudication_barrier_controls_single_publish(
    complete_verdicts: bool, publishes: bool
) -> None:
    from agent.review.adversarial import CandidateDraft, FinderOutput, Verdict, VerdictBatch

    stages = [object() for _ in range(5)]
    stage_iter = iter(stages)
    candidate = {
        "file": "src/app.py",
        "start_line": 1,
        "end_line": 1,
        "quoted_line": "new",
        "failure_mode": "returns the wrong value",
        "severity": "high",
        "category": "correctness",
        "side": "LEFT",
    }

    async def run_stage(graph: object, *_args: object, **_kwargs: object) -> Any:
        if graph is stages[1]:
            return FinderOutput(candidates=[CandidateDraft.model_validate(candidate)])
        if graph is stages[2]:
            return FinderOutput(candidates=[])
        if graph is stages[0]:
            verdicts = (
                [
                    Verdict(
                        candidate_id="c1",
                        verdict="keep-confirmed",
                        evidence="reachable from the changed line",
                    )
                ]
                if complete_verdicts
                else []
            )
            return VerdictBatch(verdicts=verdicts)
        raise AssertionError("unexpected gate stage")

    config = cast(
        RunnableConfig,
        {"configurable": {"thread_id": "adversarial-thread", "__is_for_execution__": True}},
    )
    with (
        patch(
            "agent.reviewer_adversarial._cached_reviewer_team_defaults",
            new_callable=AsyncMock,
            return_value=(("team-main", "low"), ("team-sub", "low")),
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
        patch("agent.reviewer_adversarial._make_model_or_defer", return_value=MagicMock()),
        patch(
            "agent.reviewer_adversarial._bounded_agent",
            side_effect=lambda **_kwargs: next(stage_iter),
        ),
        patch(
            "agent.reviewer_adversarial._prepare_context",
            new_callable=AsyncMock,
            return_value={
                "work_dir": "/workspace",
                "working_dir": "/workspace/repo",
                "rendered_system_prompt": "prompt",
                "diff_text": (
                    "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n"
                    "+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"
                ),
                "diff_line_set": {"src/app.py": {"RIGHT": {1}, "LEFT": {1}}},
                "pr_title": "title",
                "diff_path": "/tmp/review.diff",
            },
        ),
        patch("agent.reviewer_adversarial._run_stage", side_effect=run_stage),
        patch(
            "agent.reviewer_adversarial.agent_tools.add_finding",
            new_callable=AsyncMock,
            return_value={"success": True, "finding_id": "f1"},
        ) as add,
        patch(
            "agent.reviewer_adversarial.agent_tools.publish_review",
            new_callable=AsyncMock,
            return_value={"success": True, "review_id": 7},
        ) as publish,
        patch.object(
            __import__(
                "agent.reviewer_adversarial", fromlist=["settle_review_check_on_exit"]
            ).settle_review_check_on_exit,
            "aafter_agent",
            new_callable=AsyncMock,
        ) as settle,
    ):
        graph = await get_reviewer_adversarial_agent(config)
        result = await graph.ainvoke({"messages": []})

    if publishes:
        assert result["publication"]["review_id"] == 7
        add.assert_awaited_once()
        assert add.await_args is not None
        assert add.await_args.kwargs["side"] == "LEFT"
        publish.assert_awaited_once()
    else:
        assert "adjudication failed" in result["error"]
        add.assert_not_awaited()
        publish.assert_not_awaited()
    settle.assert_awaited_once()


@pytest.mark.asyncio
async def test_compiled_graph_runs_all_prepublish_gates_with_bounded_passes() -> None:
    from agent.review.adversarial import (
        CandidateDraft,
        FinderOutput,
        GateOutput,
        IndependenceDecision,
        Verdict,
        VerdictBatch,
    )

    stages = [object() for _ in range(5)]
    stage_iter = iter(stages)
    gate_calls = 0

    async def run_stage(graph: object, *_args: object, **_kwargs: object) -> Any:
        nonlocal gate_calls
        if graph in {stages[1], stages[2]}:
            return FinderOutput(candidates=[])
        if graph is stages[0]:
            return VerdictBatch(
                verdicts=[
                    Verdict(
                        candidate_id=candidate_id,
                        verdict="keep-confirmed",
                        evidence="reachable",
                    )
                    for candidate_id in ("g1", "g2")
                ]
            )
        if graph is stages[4]:
            gate_calls += 1
            if gate_calls == 1:
                return GateOutput(
                    candidates=[
                        CandidateDraft(
                            file="src/app.py",
                            start_line=line,
                            end_line=line,
                            quoted_line=f"changed_{line}",
                            failure_mode=f"failure {line}",
                            severity="high",
                            side="RIGHT",
                        )
                        for line in (1, 2)
                    ]
                )
            return GateOutput(
                independence=[
                    IndependenceDecision(
                        candidate_ids=["g1", "g2"],
                        independent=False,
                        keep_candidate_ids=["g1"],
                        rationale="same user-visible failure",
                    )
                ]
            )
        raise AssertionError("unexpected parent adjudicator stage")

    config = cast(
        RunnableConfig,
        {"configurable": {"thread_id": "adversarial-thread", "__is_for_execution__": True}},
    )
    with (
        patch(
            "agent.reviewer_adversarial._cached_reviewer_team_defaults",
            new_callable=AsyncMock,
            return_value=(("team-main", "low"), ("team-sub", "low")),
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
        patch("agent.reviewer_adversarial._make_model_or_defer", return_value=MagicMock()),
        patch(
            "agent.reviewer_adversarial._bounded_agent",
            side_effect=lambda **_kwargs: next(stage_iter),
        ),
        patch(
            "agent.reviewer_adversarial._prepare_context",
            new_callable=AsyncMock,
            return_value={
                "work_dir": "/workspace",
                "working_dir": "/workspace/repo",
                "rendered_system_prompt": "prompt",
                "stage_context": "context",
                "diff_text": (
                    "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n"
                    "+++ b/src/app.py\n@@ -1,2 +1,2 @@\n-old1\n-old2\n+new1\n+new2\n"
                ),
                "diff_line_set": {"src/app.py": {"RIGHT": {1, 2}, "LEFT": {1, 2}}},
                "diff_path": "/tmp/review.diff",
                "pr_title": "change app",
            },
        ),
        patch("agent.reviewer_adversarial._run_stage", side_effect=run_stage),
        patch(
            "agent.reviewer_adversarial.agent_tools.add_finding",
            new_callable=AsyncMock,
            return_value={"success": True, "finding_id": "f1"},
        ) as add,
        patch(
            "agent.reviewer_adversarial.agent_tools.publish_review",
            new_callable=AsyncMock,
            return_value={"success": True, "review_id": 7},
        ) as publish,
        patch.object(
            __import__(
                "agent.reviewer_adversarial", fromlist=["settle_review_check_on_exit"]
            ).settle_review_check_on_exit,
            "aafter_agent",
            new_callable=AsyncMock,
        ),
    ):
        graph = await get_reviewer_adversarial_agent(config)
        result = await graph.ainvoke({"messages": []})

    assert result["gate_triggers"] == [
        "zero-findings",
        "uncovered-major-prefix",
        "same-file-independence",
    ]
    assert gate_calls == 2
    add.assert_awaited_once()
    publish.assert_awaited_once()
