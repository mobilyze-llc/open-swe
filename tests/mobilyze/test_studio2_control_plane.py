from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from agent.utils.linear_team_repo_map import LINEAR_TEAM_TO_REPO

PROJECT_ROOT = Path(__file__).parents[2]
MODULE_PATH = PROJECT_ROOT / "scripts" / "mobilyze" / "studio2_control_plane.py"
MANIFEST_PATH = PROJECT_ROOT / "config" / "mobilyze" / "studio2-control-plane.json"
INSTALLER_PATH = PROJECT_ROOT / "scripts" / "mobilyze" / "install_studio2_control_plane.sh"
RUNNER_PATH = PROJECT_ROOT / "scripts" / "mobilyze" / "run_studio2_control_plane.sh"
RUNBOOK_PATH = PROJECT_ROOT / "docs" / "mobilyze" / "STUDIO2_CONTROL_PLANE.md"
SPEC = importlib.util.spec_from_file_location("studio2_control_plane", MODULE_PATH)
assert SPEC and SPEC.loader
CONTROL_PLANE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROL_PLANE
SPEC.loader.exec_module(CONTROL_PLANE)


def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def test_checked_in_manifest_is_valid_and_pinned() -> None:
    data = manifest()

    CONTROL_PLANE.validate_manifest(data)

    assert data["application"]["commit"] == "f4e2a6833e403184ee710b102ee9d31bd12a0387"
    assert data["application"]["dependency_locks"] == {
        "ui/pnpm-lock.yaml": "dc5638471a8fbbf0a0d0de4da0c0b4a7a664ac9a27edcf87234bdb3df3df0b6d",
        "uv.lock": "43aca3bf3c7ac2e975682b01039e10c5998d7a03d70790225c60b673abda770d",
    }
    assert data["github"]["repository_selection"] == "selected"
    assert data["github"]["repositories"] == ["mobilyze-llc/open-swe"]
    assert data["github"]["permissions"]["statuses"] == "read"
    assert data["linear"]["team_key"] == "OSWE"
    assert data["linear"]["team_to_repository"] == {
        "Open SWE": {"name": "open-swe", "owner": "mobilyze-llc"}
    }
    assert LINEAR_TEAM_TO_REPO["Open SWE"] == {
        "default": {"owner": "mobilyze-llc", "name": "open-swe"}
    }


def test_manifest_contains_secret_names_but_no_values() -> None:
    data = manifest()

    assert set(data["secrets"]) >= {
        "DASHBOARD_JWT_SECRET",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_WEBHOOK_SECRET",
        "LINEAR_API_KEY",
        "LINEAR_WEBHOOK_SECRET",
        "TOKEN_ENCRYPTION_KEY",
    }
    assert "secret_values" not in data
    serialized = json.dumps(data)
    assert "BEGIN PRIVATE KEY" not in serialized
    assert "lin_api_" not in serialized
    assert "ghp_" not in serialized


def test_manifest_rejects_interactive_or_orchard_paths() -> None:
    data = manifest()
    data["host"]["deployment_root"] = "/Users/ericlitman/projects/openswe"

    with pytest.raises(ValueError, match="interactive or excluded root"):
        CONTROL_PLANE.validate_manifest(data)

    data = manifest()
    data["host"]["state_root"] = "/var/db/mobilyze-open-swe-orchard/controller"
    with pytest.raises(ValueError, match="interactive or excluded root"):
        CONTROL_PLANE.validate_manifest(data)


def test_launchd_services_use_dedicated_identity_and_roots() -> None:
    data = manifest()

    rendered = CONTROL_PLANE.render_launchd_plists(data)

    assert set(rendered) == {
        "com.mobilyze.open-swe-control-plane.backend",
        "com.mobilyze.open-swe-control-plane.dashboard",
    }
    for label, raw in rendered.items():
        plist = plistlib.loads(raw)
        assert plist["Label"] == label
        assert plist["UserName"] == "_openswectl"
        assert plist["WorkingDirectory"] == "/var/db/mobilyze-open-swe-control-plane"
        assert plist["ProgramArguments"][:2] == [
            "/Library/Application Support/MobilyzeOpenSWEControlPlane/run",
            label.rsplit(".", 1)[-1],
        ]
        assert plist["ProcessType"] == "Background"
        assert plist["RunAtLoad"] is True
        assert plist["KeepAlive"]["SuccessfulExit"] is False


def test_runtime_environment_template_lists_names_only() -> None:
    data = manifest()

    template = CONTROL_PLANE.render_env_template(data)

    assert template.endswith("\n")
    assert [line.split("=", 1)[0] for line in template.splitlines()] == data["secrets"]
    assert all(line.endswith("=") for line in template.splitlines())


def test_environment_validation_requires_each_declared_name_once_and_nonempty() -> None:
    data = manifest()
    valid = "".join(f"{name}=configured-{index}\n" for index, name in enumerate(data["secrets"]))

    assert CONTROL_PLANE.invalid_environment_names(data, valid) == []
    assert CONTROL_PLANE.invalid_environment_names(data, valid.replace("LINEAR_API_KEY=", "")) == [
        "LINEAR_API_KEY"
    ]
    assert CONTROL_PLANE.invalid_environment_names(data, f"{valid}LINEAR_API_KEY=duplicate\n") == [
        "LINEAR_API_KEY"
    ]
    assert CONTROL_PLANE.invalid_environment_names(
        data, valid.replace("LINEAR_API_KEY=configured-15", "LINEAR_API_KEY=   ")
    ) == ["LINEAR_API_KEY"]
    environment = dict.fromkeys(data["secrets"], "configured")
    assert CONTROL_PLANE.invalid_process_environment_names(data, environment) == []
    environment["LINEAR_API_KEY"] = ""
    assert CONTROL_PLANE.invalid_process_environment_names(data, environment) == ["LINEAR_API_KEY"]


def test_environment_validation_cli_reports_status_without_names(
    tmp_path: Path,
) -> None:
    data = manifest()
    env_path = tmp_path / "env"
    command = [
        sys.executable,
        str(MODULE_PATH),
        "--manifest",
        str(MANIFEST_PATH),
        "validate-environment",
        "--input",
        str(env_path),
    ]
    env_path.write_text("".join(f"{name}=configured\n" for name in data["secrets"]))

    valid = subprocess.run(command, capture_output=True, text=True, check=False)
    env_path.write_text("LINEAR_API_KEY=\n")
    invalid = subprocess.run(command, capture_output=True, text=True, check=False)
    process_command = [
        sys.executable,
        str(MODULE_PATH),
        "--manifest",
        str(MANIFEST_PATH),
        "validate-process-environment",
    ]
    environment = dict.fromkeys(data["secrets"], "configured")
    valid_process = subprocess.run(
        process_command, env=environment, capture_output=True, text=True, check=False
    )
    environment["LINEAR_API_KEY"] = ""
    invalid_process = subprocess.run(
        process_command, env=environment, capture_output=True, text=True, check=False
    )

    assert (valid.returncode, valid.stdout, valid.stderr) == (0, "", "")
    assert (invalid.returncode, invalid.stdout, invalid.stderr) == (78, "", "")
    assert (valid_process.returncode, valid_process.stdout, valid_process.stderr) == (0, "", "")
    assert (invalid_process.returncode, invalid_process.stdout, invalid_process.stderr) == (
        78,
        "",
        "",
    )


def test_installer_is_pinned_and_uses_the_dedicated_service_boundary() -> None:
    installer = INSTALLER_PATH.read_text()
    runner = RUNNER_PATH.read_text()

    assert "IDENTITY=_openswectl" in installer
    assert "--frozen --no-dev" in installer
    assert '--directory "$release"' in installer
    assert "$staging" not in installer
    assert "pnpm install --frozen-lockfile" in installer
    assert 'validate-environment --input "$ENV_FILE"' in installer
    assert "validate-process-environment" in installer
    assert '/usr/bin/sudo -u "$IDENTITY" /bin/sh -c' in installer
    assert "required environment values are missing, duplicated, or empty" in installer
    assert "cleanup_release=true" in installer
    assert '/bin/rm -rf "$release"' in installer
    assert "stop control-plane services before installing a release" in installer
    assert "GID $IDENTITY_ID is already allocated" in installer
    assert installer.index('"$PROJECT_ROOT/scripts/mobilyze/run_studio2_control_plane.sh"') > (
        installer.index("install_services()")
    )
    assert 'launchctl disable "system/$label"' in installer
    assert installer.index('launchctl enable "system/$label"') < installer.index(
        'launchctl bootstrap system "$plist"'
    )
    assert 'while launchctl print "system/$label" >/dev/null 2>&1; do' in installer
    assert 'echo "service did not unload: $label" >&2' in installer
    assert "open-swe-orchard" not in installer
    assert "crucible" not in installer.lower()
    assert 'set -a\n. "$ENV_FILE"\nset +a' in runner
    assert "--host 127.0.0.1 --port 2029" in runner
    assert "export HOST=127.0.0.1" in runner
    assert "export PORT=3029" in runner
    assert "exec /opt/homebrew/bin/node .output/server/index.mjs" in runner
    assert "vite/bin/vite.js preview" not in runner


def test_runbook_separates_reviewed_tooling_from_the_pinned_application() -> None:
    runbook = RUNBOOK_PATH.read_text()

    assert "TOOLING_REF=refs/pull/5/head" in runbook
    assert "TOOLING_SHA=$(git rev-parse FETCH_HEAD)" in runbook
    assert "APP_SHA=f4e2a6833e403184ee710b102ee9d31bd12a0387" in runbook
    assert 'git archive "$TOOLING_SHA"' in runbook
    assert (
        'git archive --format=tar.gz --output="/tmp/open-swe-$APP_SHA.tar.gz" "$APP_SHA"' in runbook
    )
    assert "/usr/bin/mktemp -d /tmp/oswe-29-deploy.XXXXXX" in runbook
    assert r"chmod 0755 \"$REMOTE_TOOLING_DIR\"" in runbook
    assert "--set-path=/dashboard/api http://127.0.0.1:2029/dashboard/api" in runbook
    assert "--set-path=/webhooks http://127.0.0.1:2029/webhooks" in runbook
