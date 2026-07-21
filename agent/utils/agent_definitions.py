"""Load file-based agent definitions without importing code from their directories.

Loading only parses and validates; runtime choices, hot reload, and parent prompt rendering
belong to consuming graphs. Omitted subagent tools always mean an empty list because an absent
deepagents spec key inherits parent tools. A spec middleware carries each subagent prompt past
the Open SWE harness profile's declarative-prompt replacement. It prepends shared.md verbatim to
each subagent body; parent-body str.format rendering remains the consuming graph's responsibility.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage

import agent.tools as agent_tools

_FRONTMATTER_KEYS = frozenset({"description", "tools"})
_CURATED_TOOL_NAMES = frozenset(agent_tools.__all__)


class AgentDefinitionError(Exception):
    """All validation errors for one definition directory, reported together."""

    def __init__(self, name: str, errors: list[str]) -> None:
        self.name = name
        self.errors = sorted(errors)
        message = f"agent definition '{name}' invalid:\n" + "\n".join(f"- {e}" for e in self.errors)
        super().__init__(message)


@dataclass(frozen=True)
class SubagentDefinition:
    name: str
    description: str
    tools: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    tools: tuple[str, ...]
    body: str
    shared: str | None
    subagents: tuple[SubagentDefinition, ...]


def definitions_root() -> Path:
    """Return the filesystem directory for the ``agent`` package.

    ``agent`` is a namespace package, so ``importlib.resources.files("agent")``
    yields a ``MultiplexedPath`` whose traversal is order-dependent; anchoring on
    ``__file__`` (the ``agent/skills`` precedent) is deterministic and holds in
    the installed wheel.
    """
    return Path(__file__).resolve().parent.parent


def list_agent_definitions(root: Path | None = None) -> tuple[str, ...]:
    """Return sorted definition-directory names below ``root``."""
    directory = root if root is not None else definitions_root()
    if not directory.is_dir():
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in directory.iterdir()
            if entry.is_dir() and (entry / "agent.md").is_file()
        )
    )


def load_agent_definition(name: str, root: Path | None = None) -> AgentDefinition:
    """Parse and validate one file-based agent definition."""
    directory = (root if root is not None else definitions_root()) / name
    if not directory.is_dir():
        raise AgentDefinitionError(name, ["definition directory does not exist"])

    errors: list[str] = []
    agent_path = directory / "agent.md"
    agent_data: tuple[str, tuple[str, ...], str] | None = None
    if not agent_path.is_file():
        errors.append("agent.md: file is missing")
    else:
        agent_data = _load_prompt_file(agent_path, "agent.md", errors)

    shared: str | None = None
    shared_path = directory / "shared.md"
    if shared_path.exists():
        if not shared_path.is_file():
            errors.append("shared.md: must be a file")
        else:
            shared = shared_path.read_text(encoding="utf-8").strip()
            if not shared:
                errors.append("shared.md: body must be non-empty")

    subagents: list[SubagentDefinition] = []
    subagents_path = directory / "subagents"
    if subagents_path.exists():
        if not subagents_path.is_dir():
            errors.append("subagents: must be a directory")
        else:
            for entry in sorted(subagents_path.iterdir(), key=lambda path: path.name):
                relative = f"subagents/{entry.name}"
                if not entry.is_file() or entry.suffix != ".md":
                    errors.append(f"{relative}: must be a .md file")
                    continue
                parsed = _load_prompt_file(entry, relative, errors)
                if parsed is not None:
                    description, tools, body = parsed
                    subagents.append(
                        SubagentDefinition(
                            name=entry.stem,
                            description=description,
                            tools=tools,
                            body=body,
                        )
                    )

    if errors:
        raise AgentDefinitionError(name, errors)
    assert agent_data is not None
    description, tools, body = agent_data
    return AgentDefinition(
        name=name,
        description=description,
        tools=tools,
        body=body,
        shared=shared,
        subagents=tuple(sorted(subagents, key=lambda subagent: subagent.name)),
    )


class _SubagentSystemPromptMiddleware(AgentMiddleware):
    def __init__(self, prompt: str) -> None:
        self._prompt = prompt

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        existing = request.system_message.text if request.system_message is not None else ""
        content = f"{self._prompt}\n\n{existing}" if existing else self._prompt
        request = request.override(system_message=SystemMessage(content=content))
        return await handler(request)


def build_subagents(
    definition: AgentDefinition,
    *,
    model: BaseChatModel,
    reserved_tools: frozenset[str] = frozenset(),
) -> list[SubAgent]:
    """Hydrate validated subagent definitions into deepagents specs.

    Unless the definition supplies its own, a toolless ``general-purpose``
    override is appended so deepagents' auto-added default cannot inherit the
    parent's tools past the capability ceiling. The ceiling governs
    curated-registry tools only: deepagents' filesystem middleware still equips
    every subagent with the shared sandbox's read/write/execute surface, so
    subagents operate inside the parent's sandbox trust boundary, same as the
    stock reviewer's.
    """
    errors = [
        f"subagents/{subagent.name}.md: tool '{tool}' is reserved for the parent agent"
        for subagent in definition.subagents
        for tool in subagent.tools
        if tool in reserved_tools
    ]
    if errors:
        raise AgentDefinitionError(definition.name, errors)

    specs: list[SubAgent] = []
    for subagent in definition.subagents:
        prompt = (
            f"{definition.shared}\n\n{subagent.body}"
            if definition.shared is not None
            else subagent.body
        )
        specs.append(
            {
                "name": subagent.name,
                "description": subagent.description,
                "system_prompt": "",
                "model": model,
                "tools": [getattr(agent_tools, tool) for tool in subagent.tools],
                "middleware": [_SubagentSystemPromptMiddleware(prompt)],
            }
        )
    gp_name = GENERAL_PURPOSE_SUBAGENT["name"]
    if all(spec["name"] != gp_name for spec in specs):
        specs.append(
            {
                "name": gp_name,
                "description": "Not part of this agent's roster; do not dispatch.",
                "system_prompt": "",
                "model": model,
                "tools": [],
                "middleware": [],
            }
        )
    return specs


def _load_prompt_file(
    path: Path,
    relative: str,
    errors: list[str],
) -> tuple[str, tuple[str, ...], str] | None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        errors.append(f"{relative}: missing opening frontmatter delimiter")
        return None
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        errors.append(f"{relative}: unterminated frontmatter block")
        return None
    try:
        frontmatter = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None)
        detail = f": {problem}" if isinstance(problem, str) and problem else ""
        errors.append(f"{relative}: invalid YAML{detail}")
        return None
    if not isinstance(frontmatter, dict):
        errors.append(f"{relative}: frontmatter must be a mapping")
        return None
    body = "".join(lines[closing + 1 :])

    valid = True
    for key in sorted((key for key in frontmatter if key not in _FRONTMATTER_KEYS), key=repr):
        errors.append(f"{relative}: unknown frontmatter key {key!r}")
        valid = False

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{relative}: description must be a non-empty string")
        valid = False

    tools: tuple[str, ...] = ()
    tools_valid = True
    if "tools" in frontmatter:
        raw_tools = frontmatter["tools"]
        if not isinstance(raw_tools, list):
            errors.append(f"{relative}: tools must be a list")
            tools_valid = False
        else:
            tools, tools_valid = _validate_tool_list(raw_tools, relative, errors)
    valid = valid and tools_valid
    if not body.strip():
        errors.append(f"{relative}: body must be non-empty")
        valid = False

    if not valid or not isinstance(description, str):
        return None
    return description, tools, body


def _validate_tool_list(
    raw_tools: list[Any],
    relative: str,
    errors: list[str],
) -> tuple[tuple[str, ...], bool]:
    tools: list[str] = []
    valid = True
    seen: set[str] = set()
    duplicate_errors: set[str] = set()
    unknown_errors: set[str] = set()
    for index, tool in enumerate(raw_tools):
        if not isinstance(tool, str) or not tool.strip():
            errors.append(f"{relative}: tools[{index}] must be a non-empty string")
            valid = False
            continue
        tools.append(tool)
        if tool in seen and tool not in duplicate_errors:
            errors.append(f"{relative}: duplicate tool {tool!r}")
            duplicate_errors.add(tool)
            valid = False
        seen.add(tool)
        if tool not in _CURATED_TOOL_NAMES and tool not in unknown_errors:
            errors.append(f"{relative}: unknown tool {tool!r}")
            unknown_errors.add(tool)
            valid = False
    return tuple(tools), valid
