from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "scripts" / "mobilyze" / "check_architecture.py"
PROJECT_ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("mobilyze_architecture_guard", MODULE_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GUARD
SPEC.loader.exec_module(GUARD)


def config() -> dict:
    return {
        "custom_path_globs": ["agent/mobilyze/**", "tests/mobilyze/**"],
        "source_extensions": [".py"],
        "exempt_path_globs": ["tests/**"],
        "new_file_line_cap": 350,
        "no_growth_line_threshold": 600,
        "integration_seams": [
            {"path": "agent/server.py", "max_added_lines_per_pr": 10, "purpose": "registration"}
        ],
        "waivers": [],
    }


def change(path: str, *, added: int, base_exists: bool, base_lines: int, head_lines: int):
    return GUARD.Change(path, added, 0, base_exists, base_lines, head_lines)


def rules(findings):
    return {finding.rule for finding in findings if not finding.waived}


def workflow_script(step_name: str) -> str:
    lines = (PROJECT_ROOT / ".github/workflows/mobilyze-architecture.yml").read_text().splitlines()
    step_index = next(
        index for index, line in enumerate(lines) if line.strip() == f"- name: {step_name}"
    )
    run_index = next(
        index for index in range(step_index + 1, len(lines)) if lines[index].strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    script_lines: list[str] = []
    for line in lines[run_index + 1 :]:
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent <= run_indent:
            break
        script_lines.append(line[run_indent + 2 :])
    return "\n".join(script_lines)


class ArchitectureGuardTests(unittest.TestCase):
    def test_checked_in_config_keys_are_allowed_by_strict_schema(self):
        schema = json.loads(
            (PROJECT_ROOT / "config/mobilyze/architecture-guardrails.schema.json").read_text()
        )
        checked_in_config = json.loads(
            (PROJECT_ROOT / "config/mobilyze/architecture-guardrails.json").read_text()
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(checked_in_config).difference(schema["properties"]), set())
        self.assertEqual(schema["properties"]["$schema"]["type"], "string")

    def test_collect_changes_uses_merge_base_and_requested_head_for_all_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            git("init", "--initial-branch=main")
            git("config", "user.name", "Test User")
            git("config", "user.email", "test@example.com")
            source = repo / "agent/mobilyze/nested/source.py"
            ordinary = repo / "agent/mobilyze/ordinary.py"
            source.parent.mkdir(parents=True)
            source.write_text("".join(f"line_{index} = {index}\n" for index in range(600)))
            ordinary.write_text("".join(f"ordinary_{index} = {index}\n" for index in range(10)))
            git("add", ".")
            git("commit", "-m", "merge base")
            git("branch", "feature")

            with source.open("a") as file:
                file.write("main_source_one = True\nmain_source_two = True\n")
            with ordinary.open("a") as file:
                file.write("main_ordinary_one = True\nmain_ordinary_two = True\n")
            git("add", ".")
            git("commit", "-m", "advance base")
            base_ref = git("rev-parse", "HEAD")

            git("switch", "feature")
            destination = repo / "agent/mobilyze/deeper/nested/renamed.py"
            destination.parent.mkdir(parents=True)
            git("mv", str(source.relative_to(repo)), str(destination.relative_to(repo)))
            with destination.open("a") as file:
                file.write("grown = True\n")
            with ordinary.open("a") as file:
                file.write("feature_ordinary = True\n")
            git("add", ".")
            git("commit", "-m", "feature changes")
            head_ref = git("rev-parse", "HEAD")
            with destination.open("a") as file:
                file.write("dirty = True\n")
            with ordinary.open("a") as file:
                file.write("dirty_ordinary = True\n")

            with contextlib.chdir(repo):
                changes = GUARD.collect_changes(base_ref, head_ref)

        self.assertEqual(len(changes), 2)
        changes_by_path = {change.path: change for change in changes}
        renamed = changes_by_path["agent/mobilyze/deeper/nested/renamed.py"]
        self.assertTrue(renamed.base_exists)
        self.assertEqual(renamed.base_lines, 600)
        self.assertEqual(renamed.head_lines, 601)
        unchanged_path = changes_by_path["agent/mobilyze/ordinary.py"]
        self.assertTrue(unchanged_path.base_exists)
        self.assertEqual(unchanged_path.base_lines, 10)
        self.assertEqual(unchanged_path.head_lines, 11)

    def test_cross_boundary_rename_checks_upstream_source_and_custom_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            git("init", "--initial-branch=main")
            git("config", "user.name", "Test User")
            git("config", "user.email", "test@example.com")
            source = repo / "agent/reviewer.py"
            source.parent.mkdir(parents=True)
            source.write_text("".join(f"line_{index} = {index}\n" for index in range(351)))
            git("add", ".")
            git("commit", "-m", "base")
            base_ref = git("rev-parse", "HEAD")

            destination = repo / "agent/mobilyze/reviewer.py"
            destination.parent.mkdir(parents=True)
            git("mv", str(source.relative_to(repo)), str(destination.relative_to(repo)))
            git("commit", "-m", "cross boundary rename")
            head_ref = git("rev-parse", "HEAD")

            with contextlib.chdir(repo):
                changes = GUARD.collect_changes(base_ref, head_ref)

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].base_path, "agent/reviewer.py")
        findings = GUARD.evaluate_change(changes[0], config())
        self.assertEqual(
            {(finding.path, finding.rule) for finding in findings},
            {
                ("agent/reviewer.py", "fork_delta.undeclared_upstream_change"),
                ("agent/mobilyze/reviewer.py", "file_size.new_file_line_cap"),
            },
        )

    def test_rejects_new_custom_source_over_cap(self):
        findings = GUARD.evaluate_change(
            change(
                "agent/mobilyze/large.py",
                added=351,
                base_exists=False,
                base_lines=0,
                head_lines=351,
            ),
            config(),
        )
        self.assertEqual(rules(findings), {"file_size.new_file_line_cap"})

    def test_allows_new_custom_source_at_cap(self):
        findings = GUARD.evaluate_change(
            change(
                "agent/mobilyze/ok.py",
                added=350,
                base_exists=False,
                base_lines=0,
                head_lines=350,
            ),
            config(),
        )
        self.assertEqual(findings, [])

    def test_rejects_growth_once_custom_source_reaches_threshold(self):
        findings = GUARD.evaluate_change(
            change(
                "agent/mobilyze/runtime.py",
                added=1,
                base_exists=True,
                base_lines=600,
                head_lines=601,
            ),
            config(),
        )
        self.assertEqual(rules(findings), {"file_size.no_growth_over_threshold"})

    def test_rejects_crossing_threshold_but_preserves_no_growth_and_exemptions(self):
        crossing = GUARD.evaluate_change(
            change(
                "agent/mobilyze/runtime.py",
                added=1,
                base_exists=True,
                base_lines=599,
                head_lines=600,
            ),
            config(),
        )
        unchanged_over_threshold = GUARD.evaluate_change(
            change(
                "agent/mobilyze/runtime.py",
                added=0,
                base_exists=True,
                base_lines=601,
                head_lines=601,
            ),
            config(),
        )
        exempt_crossing = GUARD.evaluate_change(
            change(
                "tests/mobilyze/test_runtime.py",
                added=1,
                base_exists=True,
                base_lines=599,
                head_lines=600,
            ),
            config(),
        )
        self.assertEqual(rules(crossing), {"file_size.no_growth_over_threshold"})
        self.assertEqual(unchanged_over_threshold, [])
        self.assertEqual(exempt_crossing, [])

    def test_rejects_undeclared_upstream_change(self):
        findings = GUARD.evaluate_change(
            change("agent/reviewer.py", added=1, base_exists=True, base_lines=10, head_lines=11),
            config(),
        )
        self.assertEqual(rules(findings), {"fork_delta.undeclared_upstream_change"})

    def test_enforces_declared_integration_budget(self):
        passing = GUARD.evaluate_change(
            change("agent/server.py", added=10, base_exists=True, base_lines=10, head_lines=20),
            config(),
        )
        failing = GUARD.evaluate_change(
            change("agent/server.py", added=11, base_exists=True, base_lines=10, head_lines=21),
            config(),
        )
        self.assertEqual(passing, [])
        self.assertEqual(rules(failing), {"fork_delta.integration_budget"})

    def test_active_waiver_reports_without_failure_and_expired_waiver_fails(self):
        cfg = config()
        cfg["waivers"] = [
            {
                "path": "agent/mobilyze/large.py",
                "rule": "file_size.new_file_line_cap",
                "reason": "temporary split follow-up",
                "expires": "2026-08-01",
            }
        ]
        target = change(
            "agent/mobilyze/large.py",
            added=351,
            base_exists=False,
            base_lines=0,
            head_lines=351,
        )
        active = GUARD.evaluate_change(target, cfg, today=date(2026, 7, 19))
        expired = GUARD.evaluate_change(target, cfg, today=date(2026, 8, 2))
        self.assertEqual(len(active), 1)
        self.assertTrue(active[0].waived)
        self.assertEqual(rules(expired), {"file_size.new_file_line_cap"})

    def test_malformed_waiver_records_never_suppress_a_finding(self):
        valid_waiver = {
            "path": "agent/mobilyze/large.py",
            "rule": "file_size.new_file_line_cap",
            "reason": "temporary split follow-up",
            "expires": "2026-08-01",
        }
        malformed_waivers = [
            {key: value for key, value in valid_waiver.items() if key != "reason"},
            {**valid_waiver, "reason": ""},
            {key: value for key, value in valid_waiver.items() if key != "path"},
            {**valid_waiver, "rule": ""},
            {**valid_waiver, "expires": "not-a-date"},
            "not-an-object",
        ]
        target = change(
            "agent/mobilyze/large.py",
            added=351,
            base_exists=False,
            base_lines=0,
            head_lines=351,
        )

        for waiver in malformed_waivers:
            with self.subTest(waiver=waiver):
                cfg = config()
                cfg["waivers"] = [waiver]
                findings = GUARD.evaluate_change(target, cfg, today=date(2026, 7, 19))
                self.assertEqual(rules(findings), {"file_size.new_file_line_cap"})

    def test_workflow_resolves_valid_push_fallbacks_and_preserves_pr_base(self):
        script = workflow_script("Resolve comparison base")
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)

            def git(*args: str) -> str:
                return subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            def resolve(*, event: str, before: str, pr_base: str = "") -> str:
                output = repo / "github-output"
                output.unlink(missing_ok=True)
                env = {
                    **os.environ,
                    "EVENT_NAME": event,
                    "PR_BASE_SHA": pr_base,
                    "PUSH_BEFORE_SHA": before,
                    "GITHUB_OUTPUT": str(output),
                }
                subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", script],
                    cwd=repo,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return output.read_text().strip().removeprefix("ref=")

            git("init", "--initial-branch=main")
            git("config", "user.name", "Test User")
            git("config", "user.email", "test@example.com")
            (repo / "file.txt").write_text("first\n")
            git("add", ".")
            git("commit", "-m", "first")
            first = git("rev-parse", "HEAD")

            self.assertEqual(resolve(event="push", before="0" * 40), first)

            (repo / "file.txt").write_text("second\n")
            git("commit", "-am", "second")
            self.assertEqual(resolve(event="push", before="f" * 40), first)
            self.assertEqual(resolve(event="push", before=first), first)
            self.assertEqual(resolve(event="pull_request", before="", pr_base=first), first)


if __name__ == "__main__":
    unittest.main()
