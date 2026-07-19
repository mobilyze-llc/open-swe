from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "mobilyze" / "check_architecture.py"
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


class ArchitectureGuardTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
