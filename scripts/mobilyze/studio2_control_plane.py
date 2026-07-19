#!/usr/bin/env python3
"""Validate and render the pinned studio2 Open SWE deployment contract."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
from pathlib import Path
from typing import Any

SCHEMA = "mobilyze.open-swe.studio2-control-plane.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
EXCLUDED_ROOTS = (
    "/Users/ericlitman",
    "/opt/mobilyze/open-swe-orchard",
    "/var/db/mobilyze-open-swe-orchard",
    "/var/log/mobilyze-open-swe-orchard",
)


def _require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _require_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")

    application = _require_mapping(manifest.get("application"), "application")
    commit = _require_string(application, "commit")
    if not SHA_RE.fullmatch(commit):
        raise ValueError("application commit must be a full lowercase SHA")
    locks = _require_mapping(application.get("dependency_locks"), "dependency_locks")
    if set(locks) != {"uv.lock", "ui/pnpm-lock.yaml"}:
        raise ValueError("dependency_locks must pin uv.lock and ui/pnpm-lock.yaml")
    if any(not isinstance(value, str) or not HASH_RE.fullmatch(value) for value in locks.values()):
        raise ValueError("dependency lock values must be SHA-256 hashes")

    host = _require_mapping(manifest.get("host"), "host")
    if _require_string(host, "service_identity") != "_openswectl":
        raise ValueError("service_identity must be _openswectl")
    roots = [
        _require_string(host, key)
        for key in ("deployment_root", "state_root", "log_root", "config_root")
    ]
    if len(set(roots)) != len(roots):
        raise ValueError("host roots must be distinct")
    for root in roots:
        if any(root == excluded or root.startswith(f"{excluded}/") for excluded in EXCLUDED_ROOTS):
            raise ValueError(f"host path uses interactive or excluded root: {root}")

    services = _require_mapping(manifest.get("services"), "services")
    if set(services) != {"backend", "dashboard"}:
        raise ValueError("services must contain backend and dashboard")
    labels = []
    for service in services.values():
        service_config = _require_mapping(service, "service")
        labels.append(_require_string(service_config, "label"))
        if not _require_string(service_config, "listen").startswith("127.0.0.1:"):
            raise ValueError("services must bind to localhost")
    if len(set(labels)) != 2:
        raise ValueError("service labels must be distinct")

    github = _require_mapping(manifest.get("github"), "github")
    if github.get("repository_selection") != "selected":
        raise ValueError("GitHub App repository selection must be selected")
    repositories = github.get("repositories")
    if repositories != ["mobilyze-llc/open-swe"]:
        raise ValueError("GitHub App must be limited to mobilyze-llc/open-swe")

    linear = _require_mapping(manifest.get("linear"), "linear")
    mapping = _require_mapping(linear.get("team_to_repository"), "team_to_repository")
    if linear.get("team_key") != "OSWE":
        raise ValueError("Linear team key must be OSWE")
    if mapping != {"Open SWE": {"owner": "mobilyze-llc", "name": "open-swe"}}:
        raise ValueError("Linear mapping must route OSWE to mobilyze-llc/open-swe")

    secrets = manifest.get("secrets")
    if not isinstance(secrets, list) or not secrets or any(not isinstance(v, str) for v in secrets):
        raise ValueError("secrets must be a non-empty list of names")
    if secrets != sorted(set(secrets)):
        raise ValueError("secret names must be sorted and unique")
    if any("=" in name or not re.fullmatch(r"[A-Z][A-Z0-9_]+", name) for name in secrets):
        raise ValueError("secrets may contain names only")


def render_env_template(manifest: dict[str, Any]) -> str:
    validate_manifest(manifest)
    return "".join(f"{name}=\n" for name in manifest["secrets"])


def invalid_environment_names(manifest: dict[str, Any], contents: str) -> list[str]:
    validate_manifest(manifest)
    assignments: dict[str, list[str]] = {}
    for line in contents.splitlines():
        name, separator, value = line.partition("=")
        if separator and name in manifest["secrets"]:
            assignments.setdefault(name, []).append(value)
    return [
        name
        for name in manifest["secrets"]
        if len(assignments.get(name, [])) != 1 or not assignments[name][0].strip()
    ]


def render_launchd_plists(manifest: dict[str, Any]) -> dict[str, bytes]:
    validate_manifest(manifest)
    host = manifest["host"]
    rendered: dict[str, bytes] = {}
    runner = f"{host['config_root']}/run"
    for role, service in manifest["services"].items():
        label = service["label"]
        payload = {
            "Label": label,
            "UserName": host["service_identity"],
            "ProgramArguments": [runner, role],
            "WorkingDirectory": host["state_root"],
            "ProcessType": "Background",
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
            "StandardOutPath": f"{host['log_root']}/{role}.log",
            "StandardErrorPath": f"{host['log_root']}/{role}.error.log",
            "SoftResourceLimits": {"NumberOfFiles": 4096, "NumberOfProcesses": 64},
        }
        rendered[label] = plistlib.dumps(payload, sort_keys=True)
    return rendered


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    manifest = _require_mapping(value, "manifest")
    validate_manifest(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/mobilyze/studio2-control-plane.json"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    env_parser = subparsers.add_parser("render-env-template")
    env_parser.add_argument("--output", type=Path, required=True)
    check_env_parser = subparsers.add_parser("validate-environment")
    check_env_parser.add_argument("--input", type=Path, required=True)
    plist_parser = subparsers.add_parser("render-launchd")
    plist_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.command == "validate":
        print(f"validated {manifest['application']['commit']}")
        return 0
    if args.command == "render-env-template":
        args.output.write_text(render_env_template(manifest))
        args.output.chmod(0o600)
        return 0
    if args.command == "validate-environment":
        contents = args.input.read_text() if args.input.is_file() else ""
        return 0 if not invalid_environment_names(manifest, contents) else 78
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for label, payload in render_launchd_plists(manifest).items():
        (args.output_dir / f"{label}.plist").write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
