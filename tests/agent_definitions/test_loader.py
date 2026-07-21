from __future__ import annotations

from pathlib import Path

import pytest

from agent.utils.agent_definitions import (
    AgentDefinitionError,
    definitions_root,
    list_agent_definitions,
    load_agent_definition,
)


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _agent_file(*, description: str = "Main agent", tools: str = "") -> str:
    return f"""---
description: {description}
{tools}---
Parent body.
"""


def test_load_full_definition_preserves_bodies_and_sorts_subagents(tmp_path: Path) -> None:
    definition_dir = tmp_path / "reviewer-adversarial"
    _write(
        definition_dir,
        "agent.md",
        """---
description: Adversarial reviewer
tools: [list_findings, web_search]
---
Parent uses {working_dir}.
""",
    )
    _write(definition_dir, "shared.md", "\nShared review rules.\n\n")
    _write(
        definition_dir,
        "subagents/security.md",
        """---
description: Security reviewer
tools: [list_findings]
---
Find exploitable security defects.
""",
    )
    _write(
        definition_dir,
        "subagents/correctness.md",
        """---
description: Correctness reviewer
---
Find reachable correctness defects.
""",
    )

    definition = load_agent_definition("reviewer-adversarial", root=tmp_path)

    assert definition.name == "reviewer-adversarial"
    assert definition.description == "Adversarial reviewer"
    assert definition.tools == ("list_findings", "web_search")
    assert definition.body == "Parent uses {working_dir}.\n"
    assert definition.shared == "Shared review rules."
    assert tuple(subagent.name for subagent in definition.subagents) == (
        "correctness",
        "security",
    )
    correctness, security = definition.subagents
    assert correctness.description == "Correctness reviewer"
    assert correctness.tools == ()
    assert correctness.body == "Find reachable correctness defects.\n"
    assert security.description == "Security reviewer"
    assert security.tools == ("list_findings",)
    assert security.body == "Find exploitable security defects.\n"


def test_omitted_tools_are_empty_for_parent_and_subagent(tmp_path: Path) -> None:
    definition_dir = tmp_path / "minimal"
    _write(definition_dir, "agent.md", _agent_file())
    _write(
        definition_dir,
        "subagents/probe.md",
        """---
description: Probe
---
Probe body.
""",
    )

    definition = load_agent_definition("minimal", root=tmp_path)

    assert definition.tools == ()
    assert definition.subagents[0].tools == ()


def test_missing_agent_file_is_reported(tmp_path: Path) -> None:
    (tmp_path / "missing-agent").mkdir()

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("missing-agent", root=tmp_path)

    assert "agent.md" in str(exc_info.value)


def test_unknown_frontmatter_keys_are_aggregated(tmp_path: Path) -> None:
    definition_dir = tmp_path / "unknown-keys"
    _write(
        definition_dir,
        "agent.md",
        """---
description: Main agent
model: gpt-4
name: x
---
Body.
""",
    )

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("unknown-keys", root=tmp_path)

    errors = exc_info.value.errors
    assert "agent.md: unknown frontmatter key 'model'" in errors
    assert "agent.md: unknown frontmatter key 'name'" in errors


def test_unknown_tool_names_the_file_and_tool(tmp_path: Path) -> None:
    definition_dir = tmp_path / "unknown-tool"
    _write(
        definition_dir,
        "agent.md",
        _agent_file(tools="tools: [frobnicate]\n"),
    )

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("unknown-tool", root=tmp_path)

    assert "agent.md: unknown tool 'frobnicate'" in exc_info.value.errors


def test_errors_aggregate_across_files_deterministically(tmp_path: Path) -> None:
    definition_dir = tmp_path / "aggregate"
    _write(
        definition_dir,
        "agent.md",
        """---
description: Main agent
model: gpt-4
---
Body.
""",
    )
    _write(
        definition_dir,
        "subagents/bad.md",
        """---
description: Bad tools
tools: [frobnicate]
---
Body.
""",
    )
    _write(
        definition_dir,
        "subagents/empty.md",
        "---\ndescription: Empty body\n---\n \n",
    )

    messages: list[str] = []
    for _ in range(2):
        with pytest.raises(AgentDefinitionError) as exc_info:
            load_agent_definition("aggregate", root=tmp_path)
        assert exc_info.value.errors == sorted(exc_info.value.errors)
        messages.append(str(exc_info.value))

    assert messages[0] == messages[1]
    assert "agent.md: unknown frontmatter key 'model'" in messages[0]
    assert "subagents/bad.md: unknown tool 'frobnicate'" in messages[0]
    assert "subagents/empty.md: body must be non-empty" in messages[0]


@pytest.mark.parametrize(
    ("tools_yaml", "expected"),
    [
        ("tools: list_findings\n", "tools must be a list"),
        ("tools: [list_findings, 7]\n", "tools[1] must be a non-empty string"),
        ("tools: [list_findings, '']\n", "tools[1] must be a non-empty string"),
        ("tools: [list_findings, list_findings]\n", "duplicate tool 'list_findings'"),
    ],
)
def test_invalid_tools_are_reported(
    tmp_path: Path,
    tools_yaml: str,
    expected: str,
) -> None:
    definition_dir = tmp_path / "invalid-tools"
    _write(definition_dir, "agent.md", _agent_file(tools=tools_yaml))

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("invalid-tools", root=tmp_path)

    assert expected in str(exc_info.value)


def test_missing_subagent_description_is_reported(tmp_path: Path) -> None:
    definition_dir = tmp_path / "missing-description"
    _write(definition_dir, "agent.md", _agent_file())
    _write(
        definition_dir,
        "subagents/probe.md",
        """---
tools: []
---
Probe body.
""",
    )

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("missing-description", root=tmp_path)

    assert "subagents/probe.md: description must be a non-empty string" in exc_info.value.errors


def test_non_markdown_entries_in_subagents_are_reported(tmp_path: Path) -> None:
    definition_dir = tmp_path / "bad-entries"
    _write(definition_dir, "agent.md", _agent_file())
    _write(definition_dir, "subagents/notes.txt", "notes")
    _write(definition_dir, "subagents/nested/probe.md", "ignored")

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("bad-entries", root=tmp_path)

    assert "subagents/notes.txt: must be a .md file" in exc_info.value.errors
    assert "subagents/nested: must be a .md file" in exc_info.value.errors


def test_blank_shared_file_is_reported(tmp_path: Path) -> None:
    definition_dir = tmp_path / "blank-shared"
    _write(definition_dir, "agent.md", _agent_file())
    _write(definition_dir, "shared.md", " \n\t")

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("blank-shared", root=tmp_path)

    assert "shared.md: body must be non-empty" in exc_info.value.errors


def test_list_agent_definitions_includes_only_definition_directories(tmp_path: Path) -> None:
    _write(tmp_path / "zeta", "agent.md", _agent_file())
    _write(tmp_path / "alpha", "agent.md", _agent_file())
    _write(tmp_path / "utils", "__init__.py", "")
    _write(tmp_path, "root-file.md", "ignored")

    assert list_agent_definitions(tmp_path) == ("alpha", "zeta")


def test_real_package_listing_only_returns_definition_directories() -> None:
    root = definitions_root()
    names = list_agent_definitions()

    assert all((root / name / "agent.md").is_file() for name in names)
    assert not {"utils", "tools", "middleware", "graphs", "resources", "skills", "review"} & set(
        names
    )


def test_nonexistent_definition_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("does-not-exist", root=tmp_path)

    assert exc_info.value.errors == ["definition directory does not exist"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("description: no delimiters\nBody.\n", "missing opening frontmatter delimiter"),
        ("---\ndescription: unfinished\n", "unterminated frontmatter block"),
        ("---\ndescription: [\n---\nBody.\n", "invalid YAML"),
        ("---\n- description\n---\nBody.\n", "frontmatter must be a mapping"),
    ],
)
def test_invalid_frontmatter_blocks_are_reported(
    tmp_path: Path,
    content: str,
    expected: str,
) -> None:
    definition_dir = tmp_path / "invalid-frontmatter"
    _write(definition_dir, "agent.md", content)

    with pytest.raises(AgentDefinitionError) as exc_info:
        load_agent_definition("invalid-frontmatter", root=tmp_path)

    assert expected in str(exc_info.value)
