"""Plan-mode tool gating.

Hides tools that mutate external systems whenever plan mode is active — either
when the run starts in plan mode (the per-thread ``plan_mode`` carried in
configurable, e.g. a reject re-dispatch) OR after the model calls
``enter_plan_mode`` mid-run, which sets ``plan_mode`` in the run state. Installed
unconditionally so self-activation actually restricts the *next* model turn (the
tool list is recomputed on every model call), rather than only affecting a future
run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool


class PlanModeState(AgentState):
    # Declared so ``enter_plan_mode``'s Command update is a tracked channel that
    # this middleware can read back from ``request.state``.
    plan_mode: NotRequired[bool]


def _tool_name(tool: BaseTool | dict[str, Any] | Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class PlanModeMiddleware(AgentMiddleware):
    """Strip disallowed tools from each model request while plan mode is active."""

    state_schema = PlanModeState

    def __init__(
        self,
        *,
        excluded: frozenset[str],
        allowed: frozenset[str] | None = None,
        model: BaseChatModel | None = None,
        base_model: BaseChatModel | None = None,
        prompt: str | None = None,
        initial: bool = False,
    ) -> None:
        self._excluded = excluded
        self._allowed = allowed
        self._model = model
        self._base_model = base_model
        self._prompt = prompt
        self._initial = initial

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
        # Reset plan_mode to the value resolved for THIS run so a stale ``True``
        # left in the thread state by a previous run's ``enter_plan_mode`` does
        # not silently force a later (e.g. approved/implementing) run back into
        # plan mode. ``enter_plan_mode`` can still flip it on within this run.
        return {"plan_mode": self._initial}

    def _active(self, request: ModelRequest) -> bool:
        state = getattr(request, "state", None)
        if isinstance(state, dict):
            value = state.get("plan_mode")
            if isinstance(value, bool):
                return value
        else:
            value = getattr(state, "plan_mode", None)
            if isinstance(value, bool):
                return value
        return self._initial

    def _filter(self, request: ModelRequest) -> ModelRequest:
        if not self._active(request):
            return request
        filtered = [
            tool
            for tool in request.tools
            if _tool_name(tool) not in self._excluded
            and (self._allowed is None or _tool_name(tool) in self._allowed)
        ]
        if len(filtered) == len(request.tools):
            return request
        return request.override(tools=filtered)

    def _apply_profile(self, request: ModelRequest) -> ModelRequest:
        request = self._filter(request)
        if not self._active(request):
            return request
        if self._model is not None and (
            self._base_model is None
            or request.model is self._base_model
            or request.model is self._model
        ):
            request = request.override(model=self._model)
        if self._prompt is not None and not self._initial:
            existing = request.system_message.text if request.system_message is not None else ""
            content = f"{self._prompt}\n\n{existing}" if existing else self._prompt
            request = request.override(system_message=SystemMessage(content=content))
        return request

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._apply_profile(request))
