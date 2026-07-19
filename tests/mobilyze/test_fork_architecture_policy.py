from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]


def test_fork_policy_defines_only_the_three_approved_executor_kinds() -> None:
    architecture = (REPO_ROOT / "docs/mobilyze/FORK_ARCHITECTURE.md").read_text()
    guidance = (REPO_ROOT / "agent/mobilyze/AGENTS.md").read_text()

    for executor_kind in ("`api_model`", "`cli_agent`", "`external_helper`"):
        assert executor_kind in architecture
        assert executor_kind in guidance

    assert (
        "Every Mobilyze Agent Definition selects exactly one approved executor kind" in architecture
    )
    assert "Direct or unclassified model calls remain prohibited" in architecture
    assert "Open SWE's MCP bridge" in architecture
    assert "approved CLI harness path" not in architecture
    assert "implement only native CLI harness execution" not in guidance
