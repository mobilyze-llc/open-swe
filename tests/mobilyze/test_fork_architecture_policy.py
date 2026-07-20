import re
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
APPROVED_EXECUTOR_KINDS = ("api_model", "cli_agent", "external_helper")


def test_fork_policy_defines_only_the_three_approved_executor_kinds() -> None:
    architecture = (REPO_ROOT / "docs/mobilyze/FORK_ARCHITECTURE.md").read_text()
    guidance = (REPO_ROOT / "agent/mobilyze/AGENTS.md").read_text()

    architecture_executor_kinds = tuple(
        re.findall(r"^- \*\*`([^`]+)`\*\* —", architecture, flags=re.MULTILINE)
    )
    guidance_executor_kinds = tuple(
        re.findall(r"^- \*\*`([^`]+)`\*\* —", guidance, flags=re.MULTILINE)
    )

    assert architecture_executor_kinds == APPROVED_EXECUTOR_KINDS
    assert guidance_executor_kinds == APPROVED_EXECUTOR_KINDS
    assert (
        "Every Mobilyze Agent Definition selects exactly one approved executor kind" in architecture
    )
    assert "Direct or unclassified model calls remain prohibited" in architecture
    assert "Open SWE's MCP bridge" in architecture
    assert "approved CLI harness path" not in architecture
    assert "implement only native CLI harness execution" not in guidance
