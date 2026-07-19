import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "config/mobilyze/studio2-orchard-baseline.json"
OPERATOR = ROOT / "scripts/mobilyze/studio2-orchard"


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text())


def test_baseline_is_pinned_redacted_and_non_wildcard() -> None:
    manifest = load_manifest()

    assert manifest["schema"] == "mobilyze.open-swe.studio2-orchard-baseline.v1"
    assert manifest["host"]["name"] == "studio2"
    assert manifest["host"]["management_address"] == "100.107.128.12"
    assert manifest["controller"]["url"] == "https://100.107.128.12:6120"
    assert manifest["controller"]["authentication"] == "orchard-service-account"
    assert manifest["controller"]["credential"] == "REDACTED"
    assert manifest["software"]["tart"]["version"] == "2.32.1"
    assert manifest["software"]["orchard"]["version"] == "0.55.0"
    assert manifest["software"]["tart"]["sha256"] != ""
    assert manifest["software"]["orchard"]["sha256"] != ""


def test_runtime_roots_and_static_limits_are_explicit() -> None:
    manifest = load_manifest()
    paths = manifest["paths"]
    limits = manifest["limits"]

    assert all(str(path).startswith(("/opt/", "/var/", "/Library/")) for path in paths.values())
    assert all("/Users/" not in str(path) for path in paths.values())
    assert limits == {
        "max_concurrent_vms": 2,
        "default_cpu": 4,
        "default_memory_mib": 8192,
        "default_disk_gib": 40,
        "dhcp_lease_seconds": 600,
    }


def test_operator_is_fixed_surface_and_validates_manifest() -> None:
    result = subprocess.run(
        [str(OPERATOR), "validate", str(MANIFEST)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "baseline valid" in result.stdout
    source = OPERATOR.read_text()
    assert "--insecure-no-tls" not in source
    assert "--insecure-ssh-no-client-auth" not in source
    assert "eval " not in source
    assert "ghcr.io/cirruslabs/ubuntu@sha256:" in source
    assert "observed_free_disk_gib" in source
    assert 'TAILSCALE_BIN="/usr/local/bin/tailscale"' in source
    assert "-dict-add DHCPLeaseTimeSecs -int 600" in source
    assert 'DHCP_LEASE_BACKUP="${CONFIG_ROOT}/dhcp-lease.before"' in source
    assert "Delete :bootpd:DHCPLeaseTimeSecs" in source
    assert "vm-exec" in source
    assert "vm-stop" in source
    assert "vm-delete" in source
    assert "if dscl . -list /Groups PrimaryGroupID" in source
    assert "if dscl . -list /Users UniqueID" in source
    assert "trap start_all EXIT" in source
    assert 'nc -z "$CONTROLLER_ADDRESS" 6120' in source
    assert 'trap "orchard delete vm $1 >/dev/null 2>&1 || true" EXIT' in source
    assert "worker-bootstrap.XXXXXX" in source
    assert 'mv -f "$worker_bootstrap" "${SECRETS_ROOT}/worker-bootstrap"' in source
    assert 'launchctl print "system/${CONTROLLER_LABEL}" 2>/dev/null' in source
    assert 'controller_state="${controller_state:-absent}"' in source
    assert 'orchard list workers 2>/dev/null || echo "Orchard workers unavailable"' in source
    assert 'sysadminctl -deleteUser "$SERVICE_USER"' in source
    assert "rollback TART_RELEASE ORCHARD_VERSION" in source
    assert "installed rollback pair not found" in source
    assert '"$BACKUP_ROOT" "$SERVICE_USER"' in source


def test_uninstall_preview_names_backup_and_rollback_rejects_invalid_targets(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_id = fake_bin / "id"
    fake_id.write_text("#!/bin/sh\necho 0\n")
    fake_id.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    preview = subprocess.run(
        [str(OPERATOR), "uninstall"], check=False, capture_output=True, text=True, env=env
    )
    invalid_rollback = subprocess.run(
        [str(OPERATOR), "rollback", "../escape", "0.55.0"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert preview.returncode == 0
    assert "/var/backups/mobilyze-open-swe-orchard" in preview.stdout
    assert invalid_rollback.returncode == 64
