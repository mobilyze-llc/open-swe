"""Load and resolve versioned stage profiles."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any, Literal

import yaml

from agent.dashboard.options import SUPPORTED_MODEL_IDS, model_supports_effort

logger = logging.getLogger(__name__)

Stage = Literal["plan", "review"]

DEEP_AGENT_TOOL_NAMES = frozenset(
    {
        "edit_file",
        "execute",
        "glob",
        "grep",
        "ls",
        "read_file",
        "task",
        "write_file",
        "write_todos",
    }
)

_FRONTMATTER_KEYS = frozenset({"model", "reasoning_effort", "tools"})
_TEMPLATE_FIELDS: dict[Stage, frozenset[str]] = {
    "plan": frozenset({"plan_url"}),
    "review": frozenset(
        {
            "historical_review_guidance",
            "pr_number",
            "repo_checkout_note",
            "repo_name",
            "repo_owner",
            "review_finding_cap",
            "working_dir",
        }
    ),
}
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StageProfileError(ValueError):
    """A stage profile could not be parsed or validated."""

    def __init__(self, stage: Stage, name: str, errors: list[str]) -> None:
        self.stage = stage
        self.name = name
        self.errors = sorted(errors)
        message = f"{stage} stage profile {name!r} invalid:\n" + "\n".join(
            f"- {error}" for error in self.errors
        )
        super().__init__(message)


@dataclass(frozen=True)
class StageProfile:
    stage: Stage
    name: str
    body: str
    model: str | None = None
    reasoning_effort: str | None = None
    tools: tuple[str, ...] | None = None


def profiles_root() -> Path:
    """Return the bundled stage-profile directory."""
    return Path(__file__).resolve().parent.parent / "profiles"


def load_stage_profile(
    stage: Stage,
    name: str,
    *,
    allowed_tools: frozenset[str],
    root: Path | None = None,
) -> StageProfile:
    """Load and validate one stage profile."""
    errors: list[str] = []
    if not _PROFILE_NAME.fullmatch(name):
        raise StageProfileError(stage, name, ["name must be a single safe filename stem"])

    path = (root if root is not None else profiles_root()) / stage / f"{name}.md"
    if not path.is_file():
        raise StageProfileError(stage, name, [f"profile file does not exist: {path}"])

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise StageProfileError(stage, name, ["missing opening frontmatter delimiter"])
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        raise StageProfileError(stage, name, ["unterminated frontmatter block"])

    try:
        frontmatter = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None)
        detail = f": {problem}" if isinstance(problem, str) and problem else ""
        raise StageProfileError(stage, name, [f"invalid YAML{detail}"]) from exc
    if not isinstance(frontmatter, dict):
        raise StageProfileError(stage, name, ["frontmatter must be a mapping"])

    for key in sorted((key for key in frontmatter if key not in _FRONTMATTER_KEYS), key=repr):
        errors.append(
            f"unknown frontmatter key {key!r}; stage profiles cannot declare capabilities"
        )

    body = "".join(lines[closing + 1 :])
    if not body.strip():
        errors.append("body must be non-empty")
    else:
        errors.extend(_validate_template(stage, body))

    model = frontmatter.get("model")
    effort = frontmatter.get("reasoning_effort")
    model_value = model if isinstance(model, str) and model.strip() else None
    effort_value = effort if isinstance(effort, str) and effort.strip() else None
    if model is not None and model_value is None:
        errors.append("model must be a non-empty string")
    if effort is not None and effort_value is None:
        errors.append("reasoning_effort must be a non-empty string")
    if (model_value is None) != (effort_value is None):
        errors.append("model and reasoning_effort must be set together")
    elif model_value is not None and effort_value is not None:
        if model_value not in SUPPORTED_MODEL_IDS:
            errors.append(f"unsupported model {model_value!r}")
        elif not model_supports_effort(model_value, effort_value):
            errors.append(
                f"reasoning effort {effort_value!r} is not supported by model {model_value!r}"
            )

    tools = _validate_tools(frontmatter.get("tools"), allowed_tools, errors)
    if errors:
        raise StageProfileError(stage, name, errors)
    return StageProfile(
        stage=stage,
        name=name,
        body=body,
        model=model_value,
        reasoning_effort=effort_value,
        tools=tools,
    )


def resolve_stage_profile(
    stage: Stage,
    selection: str | None,
    *,
    allowed_tools: frozenset[str],
    fallback_body: str,
    root: Path | None = None,
) -> StageProfile:
    """Resolve a selection, falling back without aborting graph construction."""
    selected = selection.strip() if isinstance(selection, str) and selection.strip() else "default"
    try:
        return load_stage_profile(stage, selected, allowed_tools=allowed_tools, root=root)
    except Exception:
        logger.error(
            "Failed to load selected %s stage profile %r; falling back to default",
            stage,
            selected,
            exc_info=True,
        )

    if selected != "default":
        try:
            return load_stage_profile(stage, "default", allowed_tools=allowed_tools, root=root)
        except Exception:
            logger.error(
                "Failed to load default %s stage profile; using built-in fallback",
                stage,
                exc_info=True,
            )
    return StageProfile(stage=stage, name="default", body=fallback_body)


def _validate_template(stage: Stage, body: str) -> list[str]:
    errors: list[str] = []
    try:
        fields = [field for _, field, _, _ in Formatter().parse(body) if field is not None]
    except ValueError as exc:
        return [f"body has invalid format syntax: {exc}"]
    for field in fields:
        if field not in _TEMPLATE_FIELDS[stage]:
            errors.append(f"body uses unsupported template field {field!r}")
    return errors


def _validate_tools(
    raw_tools: Any,
    allowed_tools: frozenset[str],
    errors: list[str],
) -> tuple[str, ...] | None:
    if raw_tools is None:
        return None
    if not isinstance(raw_tools, list):
        errors.append("tools must be a list")
        return None

    tools: list[str] = []
    seen: set[str] = set()
    for index, tool in enumerate(raw_tools):
        if not isinstance(tool, str) or not tool.strip():
            errors.append(f"tools[{index}] must be a non-empty string")
            continue
        tools.append(tool)
        if tool in seen:
            errors.append(f"duplicate tool {tool!r}")
        seen.add(tool)
        if tool not in allowed_tools:
            errors.append(
                f"tool {tool!r} is not in the stage's curated toolset; profiles may only restrict tools"
            )
    return tuple(tools)
