from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self

import pytest
from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
from deepagents._models import get_model_provider
from deepagents.profiles.harness import harness_profiles
from deepagents.profiles.harness.harness_profiles import _apply_profile_prompt
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LangSmithParams
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent.prompt import OPEN_SWE_SHARED_BASE
from agent.tools import list_findings
from agent.utils.agent_definitions import (
    AgentDefinitionError,
    _SubagentSystemPromptMiddleware,
    build_subagents,
    load_agent_definition,
)


class _ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    provider_key: str
    model_name: str
    call_count: int = 0
    system_prompts: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "agent-definition-test"

    def _get_ls_params(
        self,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> LangSmithParams:
        del stop, kwargs
        return LangSmithParams(ls_provider=self.provider_key)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Self:
        del tools, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self.system_prompts.extend(
            message.text for message in messages if isinstance(message, SystemMessage)
        )
        response = self.responses[self.call_count]
        self.call_count += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.fixture
def isolated_harness_profiles() -> Iterator[None]:
    snapshot = harness_profiles._HARNESS_PROFILES.copy()
    try:
        yield
    finally:
        harness_profiles._HARNESS_PROFILES.clear()
        harness_profiles._HARNESS_PROFILES.update(snapshot)


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _model(provider_key: str = "factory-fake") -> _ScriptedChatModel:
    return _ScriptedChatModel(
        responses=[AIMessage(content="done")],
        provider_key=provider_key,
        model_name=f"{provider_key}-model",
    )


def _definition_with_two_subagents(tmp_path: Path):
    definition_dir = tmp_path / "factory"
    _write(
        definition_dir,
        "agent.md",
        """---
description: Parent
---
Parent body.
""",
    )
    _write(definition_dir, "shared.md", " Shared persona. \n")
    _write(
        definition_dir,
        "subagents/with-tools.md",
        """---
description: Has a tool
tools: [list_findings]
---
Tool persona.
""",
    )
    _write(
        definition_dir,
        "subagents/without-tools.md",
        """---
description: Has no tools
---
No-tool persona.
""",
    )
    return load_agent_definition("factory", root=tmp_path)


async def test_build_subagents_resolves_tools_and_never_inherits_parent_tools(
    tmp_path: Path,
) -> None:
    definition = _definition_with_two_subagents(tmp_path)
    model = _model()

    specs = build_subagents(definition, model=model)

    assert [spec["name"] for spec in specs] == ["with-tools", "without-tools", "general-purpose"]
    with_tools, without_tools, general_purpose = specs
    assert general_purpose.get("tools") == []
    assert general_purpose.get("middleware") == []
    assert "do not dispatch" in general_purpose["description"]
    assert with_tools.get("model") is model
    assert with_tools.get("tools") == [list_findings]
    assert "tools" in without_tools
    assert without_tools.get("tools") == []
    assert with_tools["system_prompt"] == ""
    assert without_tools["system_prompt"] == ""
    with_tools_middleware = with_tools.get("middleware") or []
    without_tools_middleware = without_tools.get("middleware") or []
    assert len(with_tools_middleware) == 1
    assert len(without_tools_middleware) == 1
    assert with_tools_middleware[0] is not without_tools_middleware[0]

    middleware = with_tools_middleware[0]
    assert isinstance(middleware, _SubagentSystemPromptMiddleware)
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        seen.append(request)
        return ModelResponse(result=[AIMessage(content="done")])

    await middleware.awrap_model_call(ModelRequest(model=model, messages=[]), handler)
    assert seen[0].system_message is not None
    assert seen[0].system_message.text == "Shared persona.\n\nTool persona.\n"


def test_definition_supplied_general_purpose_is_not_double_appended(tmp_path: Path) -> None:
    definition_dir = tmp_path / "own-gp"
    _write(
        definition_dir,
        "agent.md",
        """---
description: Parent
---
Parent body.
""",
    )
    _write(
        definition_dir,
        "subagents/general-purpose.md",
        """---
description: Custom general purpose
---
Custom body.
""",
    )
    definition = load_agent_definition("own-gp", root=tmp_path)

    specs = build_subagents(definition, model=_model())

    assert [spec["name"] for spec in specs] == ["general-purpose"]
    assert specs[0]["description"] == "Custom general purpose"


def test_reserved_subagent_tools_are_aggregated_without_touching_parent_tools(
    tmp_path: Path,
) -> None:
    definition_dir = tmp_path / "reserved"
    _write(
        definition_dir,
        "agent.md",
        """---
description: Parent
tools: [publish_review]
---
Parent body.
""",
    )
    _write(
        definition_dir,
        "subagents/reviewer.md",
        """---
description: Reviewer
tools: [publish_review]
---
Review.
""",
    )
    definition = load_agent_definition("reserved", root=tmp_path)

    with pytest.raises(AgentDefinitionError) as exc_info:
        build_subagents(
            definition,
            model=_model(),
            reserved_tools=frozenset({"publish_review", "add_finding"}),
        )

    assert definition.tools == ("publish_review",)
    assert exc_info.value.errors == [
        "subagents/reviewer.md: tool 'publish_review' is reserved for the parent agent"
    ]


async def test_prompt_middleware_prepends_persona_to_profile_prompt() -> None:
    model = _model()
    assembled = "Shared persona.\n\nSpecific persona."
    middleware = _SubagentSystemPromptMiddleware(assembled)
    profile_prompt = _apply_profile_prompt(
        HarnessProfile(base_system_prompt=OPEN_SWE_SHARED_BASE),
        "declarative prompt that should be replaced",
    )
    request = ModelRequest(
        model=model,
        messages=[],
        system_message=SystemMessage(content=profile_prompt),
    )
    seen: list[ModelRequest] = []

    async def handler(overridden: ModelRequest) -> ModelResponse:
        seen.append(overridden)
        return ModelResponse(result=[AIMessage(content="done")])

    await middleware.awrap_model_call(request, handler)

    assert seen[0].system_message is not None
    assert seen[0].system_message.text == f"{assembled}\n\n{OPEN_SWE_SHARED_BASE}"


async def test_persona_prompt_survives_real_deepagents_profile_replacement(
    tmp_path: Path,
    isolated_harness_profiles: None,
) -> None:
    del isolated_harness_profiles
    definition_dir = tmp_path / "integration"
    _write(
        definition_dir,
        "agent.md",
        """---
description: Parent
---
Parent body.
""",
    )
    _write(definition_dir, "shared.md", "SHARED-MARKER-77f\n")
    _write(
        definition_dir,
        "subagents/probe.md",
        """---
description: Prompt probe
---
PERSONA-MARKER-77f
""",
    )
    definition = load_agent_definition("integration", root=tmp_path)
    parent_model = _ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "Report the prompt.", "subagent_type": "probe"},
                        "id": "call-task-probe",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="finished"),
        ],
        provider_key="parent-fake",
        model_name="parent-model",
    )
    persona_model = _ScriptedChatModel(
        responses=[AIMessage(content="persona complete")],
        provider_key="persona-fake",
        model_name="persona-model",
    )
    provider_key = get_model_provider(persona_model)
    assert provider_key == "persona-fake"
    register_harness_profile(
        provider_key,
        HarnessProfile(base_system_prompt=OPEN_SWE_SHARED_BASE),
    )

    graph = create_deep_agent(
        model=parent_model,
        system_prompt="x",
        tools=[],
        subagents=build_subagents(definition, model=persona_model),
    )
    await graph.ainvoke({"messages": [HumanMessage(content="go")]})

    assert len(persona_model.system_prompts) == 1
    received = persona_model.system_prompts[0]
    shared_index = received.index("SHARED-MARKER-77f")
    persona_index = received.index("PERSONA-MARKER-77f")
    fleet_index = received.index("You are **Open SWE**")
    assert shared_index < persona_index < fleet_index
    assert OPEN_SWE_SHARED_BASE in received
