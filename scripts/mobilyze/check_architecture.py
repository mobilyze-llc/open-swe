#!/usr/bin/env python3
"""Deterministic architecture checks for the Mobilyze Open SWE fork."""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Change:
    path: str
    added: int
    deleted: int
    base_exists: bool
    base_lines: int
    head_lines: int
    base_path: str | None = None


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    message: str
    waived: bool = False
    waiver_reason: str | None = None


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _active_waiver(config: dict[str, Any], finding: Finding, today: date) -> dict[str, Any] | None:
    for waiver in config.get("waivers", []):
        if not isinstance(waiver, dict):
            continue
        required_strings = ("path", "rule", "reason", "expires")
        if any(
            not isinstance(waiver.get(key), str) or not waiver[key].strip()
            for key in required_strings
        ):
            continue
        if waiver.get("rule") != finding.rule:
            continue
        if not fnmatch.fnmatch(finding.path, waiver["path"]):
            continue
        try:
            expires = date.fromisoformat(waiver["expires"])
        except ValueError:
            continue
        if expires >= today:
            return waiver
    return None


def _evaluate_upstream_path(path: str, added: int, config: dict[str, Any]) -> list[Finding]:
    seams = {item["path"]: item for item in config.get("integration_seams", [])}
    seam = seams.get(path)
    if seam is None:
        return [
            Finding(
                path,
                "fork_delta.undeclared_upstream_change",
                "upstream-owned path changed without a declared integration seam",
            )
        ]
    if added > int(seam["max_added_lines_per_pr"]):
        return [
            Finding(
                path,
                "fork_delta.integration_budget",
                f"added {added} lines; declared seam budget is {seam['max_added_lines_per_pr']}",
            )
        ]
    return []


def evaluate_change(
    change: Change, config: dict[str, Any], *, today: date | None = None
) -> list[Finding]:
    today = today or date.today()
    findings: list[Finding] = []
    custom_globs = list(config["custom_path_globs"])
    exempt_globs = list(config.get("exempt_path_globs", []))
    source_extensions = set(config["source_extensions"])
    path = change.path
    base_path = change.base_path or path

    is_custom = _matches(path, custom_globs)
    base_is_custom = _matches(base_path, custom_globs)
    is_exempt = _matches(path, exempt_globs)
    is_source = Path(path).suffix in source_extensions
    crosses_boundary = base_path != path and base_is_custom != is_custom

    if crosses_boundary and not base_is_custom:
        findings.extend(_evaluate_upstream_path(base_path, 0, config))

    if is_custom and is_source and not is_exempt:
        effective_base_exists = change.base_exists and not crosses_boundary
        effective_base_lines = change.base_lines if effective_base_exists else 0
        if not effective_base_exists and change.head_lines > int(config["new_file_line_cap"]):
            findings.append(
                Finding(
                    path,
                    "file_size.new_file_line_cap",
                    f"new custom source has {change.head_lines} lines; cap is {config['new_file_line_cap']}",
                )
            )
        threshold = int(config["no_growth_line_threshold"])
        if (
            effective_base_exists
            and change.head_lines >= threshold
            and change.head_lines > effective_base_lines
        ):
            findings.append(
                Finding(
                    path,
                    "file_size.no_growth_over_threshold",
                    f"custom source grew from {effective_base_lines} to {change.head_lines} lines at/above {threshold}",
                )
            )
    elif not is_custom:
        added = change.head_lines if crosses_boundary else change.added
        findings.extend(_evaluate_upstream_path(path, added, config))

    resolved: list[Finding] = []
    for finding in findings:
        waiver = _active_waiver(config, finding, today)
        if waiver:
            resolved.append(
                Finding(
                    finding.path,
                    finding.rule,
                    finding.message,
                    waived=True,
                    waiver_reason=str(waiver.get("reason", "")),
                )
            )
        else:
            resolved.append(finding)
    return resolved


def _run_git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _line_count_from_ref(ref: str, path: str) -> tuple[bool, int]:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return False, 0
    return True, result.stdout.count(b"\n") + (
        1 if result.stdout and not result.stdout.endswith(b"\n") else 0
    )


def collect_changes(base_ref: str, head_ref: str) -> list[Change]:
    merge_base = _run_git("merge-base", base_ref, head_ref).strip()
    output = _run_git("diff", "--numstat", "-z", "--find-renames", f"{base_ref}...{head_ref}")
    changes: list[Change] = []
    entries = output.split("\0")
    index = 0
    while index < len(entries) - 1:
        added_raw, deleted_raw, path = entries[index].split("\t", 2)
        index += 1
        if path:
            base_path = path
            head_path = path
        else:
            base_path = entries[index]
            head_path = entries[index + 1]
            index += 2
        added = 0 if added_raw == "-" else int(added_raw)
        deleted = 0 if deleted_raw == "-" else int(deleted_raw)
        base_exists, base_lines = _line_count_from_ref(merge_base, base_path)
        _, head_lines = _line_count_from_ref(head_ref, head_path)
        changes.append(
            Change(
                path=head_path,
                added=added,
                deleted=deleted,
                base_exists=base_exists,
                base_lines=base_lines,
                head_lines=head_lines,
                base_path=base_path,
            )
        )
    return changes


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    required = {
        "custom_path_globs",
        "source_extensions",
        "new_file_line_cap",
        "no_growth_line_threshold",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise ValueError(f"missing config keys: {', '.join(missing)}")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/mobilyze/architecture-guardrails.json"),
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        findings = [
            finding
            for change in collect_changes(args.base_ref, args.head_ref)
            for finding in evaluate_change(change, config)
        ]
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"architecture guard error: {exc}", file=sys.stderr)
        return 2

    failures = [finding for finding in findings if not finding.waived]
    for finding in findings:
        prefix = "WAIVED" if finding.waived else "FAIL"
        suffix = f"; waiver: {finding.waiver_reason}" if finding.waived else ""
        print(f"{prefix} {finding.rule} {finding.path}: {finding.message}{suffix}")

    if failures:
        print(f"architecture guard failed with {len(failures)} violation(s)", file=sys.stderr)
        return 1
    print("architecture guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
