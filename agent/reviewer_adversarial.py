"""File-defined adversarial reviewer graph.

This is the first consumer of the ``agent/<name>/`` file-based definition
convention and is additive beside the stock reviewer rather than replacing it.
The parent template lives in ``agent/reviewer-adversarial/agent.md``; this
module renders its per-run repository and PR context. It deliberately imports
the private reviewer helpers ``_build_first_review_context``,
``_cached_gateway_enabled``, ``_cached_reviewer_team_defaults``,
``_ensure_reviewer_sandbox_for_thread``, ``_make_model_or_defer``, and
``_repo_checkout_note`` so an upstream rename breaks this module's import
loudly instead of allowing behavior to drift. The definition is loaded at
import time so malformed markdown fails the process at boot, not at the first
review. Version one treats every dispatch as a first review and does not handle
re-reviews or finding replies; production webhook routing remains on the stock
reviewer. Reach this graph through evals with
``REVIEWER_ASSISTANT_ID=reviewer_adversarial`` or by direct LangGraph API
dispatch.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

import agent.tools as agent_tools

from .dashboard.options import gate_fable_model
from .dashboard.team_settings import get_team_fable_enabled
from .middleware import (
    RepairOrphanedToolCallsMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    SlackAssistantStatusMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
    check_message_queue_before_model,
    refresh_github_proxy_before_model,
    settle_review_check_on_exit,
)
from .middleware.prepare_run import PrepareRunState
from .review.diff import (
    compute_diff_line_set,
    fetch_pr_diff,
    fetch_pr_metadata,
    materialize_review_diff,
    review_diff_range,
)
from .review.findings import REVIEW_FINDING_CAP
from .reviewer import (
    REVIEWER_EVAL_PROMPT_SUFFIX,
    PrepareReviewerRunMiddleware,
    _build_first_review_context,
    _cached_gateway_enabled,
    _cached_reviewer_team_defaults,
    _ensure_reviewer_sandbox_for_thread,
    _make_model_or_defer,
    _repo_checkout_note,
)
from .runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from .utils.agent_definitions import build_subagents, load_agent_definition
from .utils.model import DEFAULT_LLM_REASONING, provider_model_kwargs
from .utils.repo_prep import prepare_review_repo
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.tracing import REVIEW_TRACING_PROJECT, traced_graph_factory

logger = logging.getLogger(__name__)

DEFINITION_NAME = "reviewer-adversarial"

RESERVED_SUBAGENT_TOOLS = frozenset(
    {
        "add_finding",
        "update_finding",
        "publish_review",
        "resolve_finding_thread",
        "reply_to_finding_thread",
    }
)

_DEFINITION = load_agent_definition(DEFINITION_NAME)
_PARENT_TOOLS = [getattr(agent_tools, name) for name in _DEFINITION.tools]


def _render_parent_prompt(
    *,
    working_dir: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int | str,
    repo_checkout_note: str,
) -> str:
    body = _DEFINITION.body.format(
        working_dir=working_dir,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        review_finding_cap=REVIEW_FINDING_CAP,
        repo_checkout_note=repo_checkout_note,
    )
    return f"{_DEFINITION.shared}\n\n{body}" if _DEFINITION.shared else body


_ = _render_parent_prompt(
    working_dir="/tmp",
    repo_owner="o",
    repo_name="r",
    pr_number=1,
    repo_checkout_note="note",
)


class PrepareAdversarialReviewerRunMiddleware(PrepareReviewerRunMiddleware):
    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:
        configurable = self._config.get("configurable") or {}
        repo_config = configurable.get("repo") or {}
        sandbox_backend, github_token = await _ensure_reviewer_sandbox_for_thread(
            self._thread_id, configurable
        )
        work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

        repo_owner = str(repo_config.get("owner", ""))
        repo_name = str(repo_config.get("name", ""))
        base_sha = str(configurable.get("base_sha", "") or "")
        head_sha = str(configurable.get("head_sha", "") or "")
        configured_pr_number = configurable.get("pr_number")
        pr_number: int | str = configured_pr_number if isinstance(configured_pr_number, int) else ""
        pr_url = str(configurable.get("pr_url", "") or "")

        repo_ready = await prepare_review_repo(
            sandbox_backend,
            work_dir=work_dir,
            repo_owner=repo_owner,
            repo_name=repo_name,
            head_sha=head_sha,
            pr_number=pr_number if isinstance(pr_number, int) else None,
            base_sha=base_sha,
        )
        reviewer_eval = (
            configurable.get("reviewer_eval") is True or configurable.get("eval") is True
        )
        if configurable.get("re_review") or configurable.get("reviewer_event"):
            logger.warning(
                "The v1 adversarial reviewer treats every dispatch as a first review and "
                "is ignoring the re-review or reviewer-event signal"
            )

        can_fetch_pr = (
            isinstance(pr_number, int)
            and bool(repo_owner)
            and bool(repo_name)
            and bool(github_token)
        )
        diff_text = ""
        diff_line_set: dict[str, dict[str, set[int]]] | None = None
        if can_fetch_pr and github_token is not None and isinstance(pr_number, int):
            fetched = await fetch_pr_diff(
                owner=repo_owner,
                repo=repo_name,
                pr_number=pr_number,
                token=github_token,
            )
            if fetched is not None:
                try:
                    diff_base, diff_head, merge_base = review_diff_range(
                        base_sha=base_sha,
                        head_sha=head_sha,
                        last_reviewed_sha="",
                        re_review=False,
                    )
                    materialized = await materialize_review_diff(
                        sandbox_backend,
                        work_dir=f"{work_dir}/{repo_name}",
                        base_ref=diff_base,
                        head_ref=diff_head,
                        merge_base=merge_base,
                        diff_text=fetched,
                    )
                    diff_text = materialized.diff_text
                except (RuntimeError, ValueError):
                    logger.exception("Failed to materialize adversarial review diff")
                    diff_text = fetched
                diff_line_set = compute_diff_line_set(diff_text) if diff_text else None

        pr_title = ""
        pr_body = ""
        if can_fetch_pr and github_token is not None and isinstance(pr_number, int):
            metadata = await fetch_pr_metadata(
                owner=repo_owner,
                repo=repo_name,
                pr_number=pr_number,
                token=github_token,
            )
            pr_title, pr_body = metadata if metadata is not None else ("", "")

        review_context = ""
        if isinstance(pr_number, int):
            review_context = _build_first_review_context(
                pr_url=pr_url,
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                base_sha=base_sha,
                head_sha=head_sha,
                pr_title=pr_title,
                pr_body=pr_body,
                existing_threads_block="",
                include_historical_guidance=False,
            )

        working_dir = f"{work_dir}/{repo_name}" if repo_name else work_dir
        system_prompt = _render_parent_prompt(
            working_dir=working_dir,
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number if isinstance(pr_number, int) else "",
            repo_checkout_note=_repo_checkout_note(
                repo_ready=repo_ready,
                working_dir=working_dir,
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                head_sha=head_sha,
            ),
        )
        if reviewer_eval:
            system_prompt = f"{system_prompt}\n{REVIEWER_EVAL_PROMPT_SUFFIX}"
        if review_context:
            system_prompt = f"{system_prompt}\n\n{review_context}"

        return {
            "work_dir": work_dir,
            "rendered_system_prompt": system_prompt,
            "diff_text": diff_text or "",
            "diff_line_set": diff_line_set,
        }


async def get_reviewer_adversarial_agent(config: RunnableConfig) -> Pregel:
    """Get the file-defined adversarial reviewer with checkpointed run preparation."""
    config = config.copy()
    configurable = dict(config.get("configurable") or {})
    config["configurable"] = configurable
    config.setdefault("recursion_limit", DEFAULT_RECURSION_LIMIT)
    thread_id = configurable.get("thread_id")

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning reviewer without sandbox")
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    configured_model_id = configurable.get("reviewer_adversarial_model_id")
    configured_effort = configurable.get("reviewer_adversarial_reasoning_effort")
    if isinstance(configured_model_id, str) and configured_model_id:
        model_id = configured_model_id
        reasoning_effort = configured_effort if isinstance(configured_effort, str) else None
        subagent_model_id = model_id
        subagent_effort = reasoning_effort
    else:
        (
            (model_id, reasoning_effort),
            (subagent_model_id, subagent_effort),
        ) = await _cached_reviewer_team_defaults()

    configured_subagent_model_id = configurable.get("reviewer_adversarial_subagent_model_id")
    configured_subagent_effort = configurable.get("reviewer_adversarial_subagent_reasoning_effort")
    if isinstance(configured_subagent_model_id, str) and configured_subagent_model_id:
        subagent_model_id = configured_subagent_model_id
        subagent_effort = (
            configured_subagent_effort if isinstance(configured_subagent_effort, str) else None
        )

    fable_enabled = await get_team_fable_enabled()
    model_id, reasoning_effort = gate_fable_model(
        model_id, reasoning_effort, fable_enabled=fable_enabled
    )
    subagent_model_id, subagent_effort = gate_fable_model(
        subagent_model_id, subagent_effort, fable_enabled=fable_enabled
    )
    model_kwargs = provider_model_kwargs(
        model_id,
        reasoning_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )
    subagent_model_kwargs = provider_model_kwargs(
        subagent_model_id,
        subagent_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )

    use_gateway = await _cached_gateway_enabled()
    parent_model = _make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs)
    subagent_model = _make_model_or_defer(
        subagent_model_id,
        use_gateway=use_gateway,
        **subagent_model_kwargs,
    )

    async def reconnect_backend(
        _thread_id: str = thread_id,
        _configurable: dict[str, Any] = configurable,
    ) -> SandboxBackendProtocol:
        sandbox_backend, _github_token = await _ensure_reviewer_sandbox_for_thread(
            _thread_id, _configurable
        )
        return sandbox_backend

    def backend_factory(_runtime: object, _thread_id: str = thread_id):
        return get_cached_sandbox_backend(_thread_id, reconnect=reconnect_backend)

    return create_deep_agent(
        model=parent_model,
        system_prompt="",
        tools=_PARENT_TOOLS,
        subagents=build_subagents(
            _DEFINITION,
            model=subagent_model,
            reserved_tools=RESERVED_SUBAGENT_TOOLS,
        ),
        backend=backend_factory,
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareAdversarialReviewerRunMiddleware(
                    thread_id=thread_id,
                    config=config,
                    use_gateway=use_gateway,
                ),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(
                    run_limit=MODEL_CALL_RECURSION_LIMIT,
                    exit_behavior="end",
                ),
                ToolErrorMiddleware(),
                refresh_github_proxy_before_model,
                check_message_queue_before_model,
                SlackAssistantStatusMiddleware(),
                TimeoutWrapupMiddleware(),
                SanitizeFireworksMessagesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                RepairOrphanedToolCallsMiddleware(),
                settle_review_check_on_exit,
            ],
        ),
    ).with_config(config)


traced_reviewer_adversarial = traced_graph_factory(
    get_reviewer_adversarial_agent,
    REVIEW_TRACING_PROJECT,
)
