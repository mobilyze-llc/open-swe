"""Hide named tools from the model without rebuilding the agent.

`create_deep_agent` always wires the `task` tool when the auto-added
general-purpose subagent is present. The reviewer agent has no use for
subagent dispatch, so this middleware drops the named tools from the
request before the model sees them. Mirrors the behavior of deepagents'
own private `_ToolExclusionMiddleware` but lives here so we don't depend
on a private import path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.tools import BaseTool


def _tool_name(tool: BaseTool | dict[str, Any] | Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class ExcludeToolsMiddleware(AgentMiddleware):
    """Strip named tools from each model request.

    Place this AFTER tool-injecting middleware (FilesystemMiddleware,
    SubAgentMiddleware) so it can remove middleware-injected tools too.
    """

    state_schema = AgentState

    def __init__(
        self,
        *,
        excluded: frozenset[str] = frozenset(),
        allowed: frozenset[str] | None = None,
    ) -> None:
        self._excluded = excluded
        self._allowed = allowed

    def _filter(self, request: ModelRequest) -> ModelRequest:
        if not self._excluded and self._allowed is None:
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

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._filter(request))
