import json
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
    assert "DHCPLeaseTimeSecs -int 600" in source
    assert "vm-exec" in source
    assert "vm-stop" in source
    assert "vm-delete" in source
