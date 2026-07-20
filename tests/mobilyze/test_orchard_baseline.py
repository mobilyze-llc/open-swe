from __future__ import annotations

import hashlib
import json
import plistlib
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Literal

import pytest

from agent.mobilyze.orchard_baseline import (
    backup,
    host,
    install,
    rollback,
    services,
    status,
    uninstall,
)
from agent.mobilyze.orchard_baseline.constants import (
    ACCOUNT_GID,
    ACCOUNT_HOME,
    ACCOUNT_NAME,
    ACCOUNT_SHELL,
    ACCOUNT_UID,
    ADMIN_TOKEN,
    CURRENT_ORCHARD,
    CURRENT_ORCHARD_BINARY,
    CURRENT_TART,
    CURRENT_TART_APP,
    CURRENT_TART_BINARY,
    DATA_ROOT,
    DEFAULT_CPU,
    DEFAULT_DISK_GIB,
    DEFAULT_MEMORY_MIB,
    LISTENER_HOST,
    ORCHARD_HOME,
    SECRETS_ROOT,
    SUPPORT_ROOT,
    SUPPORT_ROOT_MODE,
    SUPPORT_ROOT_OWNER,
    SUPPORT_ROOT_UID,
    TAILSCALE_BINARY,
    TART_APP,
    TART_EXECUTABLE_RELATIVE,
    VM_SLOTS,
    WORKER_TOKEN,
)
from agent.mobilyze.orchard_baseline.service_files import (
    controller_plist,
    controller_wrapper,
    worker_plist,
    worker_wrapper,
)


def test_reinstall_from_installed_path_never_copies_onto_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = tmp_path / "installed"
    installed.write_bytes(b"pinned binary")
    source = tmp_path / "current"
    source.symlink_to(installed)
    expected = hashlib.sha256(installed.read_bytes()).hexdigest()

    def unexpected_copy(*args: object, **kwargs: object) -> None:
        raise AssertionError("copy2 must not run for the installed file")

    monkeypatch.setattr(host.shutil, "copy2", unexpected_copy)

    assert host.install_pinned_file(source, installed, expected) is False
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755


def test_reinstall_from_installed_tart_app_reuses_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = tmp_path / "release" / "tart.app"
    executable = installed / "Contents" / "MacOS" / "tart"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"pinned tart")
    source = tmp_path / "current.app"
    source.symlink_to(installed, target_is_directory=True)
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()

    def unexpected_copy(*args: object, **kwargs: object) -> None:
        raise AssertionError("copytree must not run for the installed app")

    monkeypatch.setattr(host.shutil, "copytree", unexpected_copy)

    assert (
        host.install_pinned_tree(source, installed, Path("Contents/MacOS/tart"), expected) is False
    )
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755


def test_backup_archive_is_private_while_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "state"
    data_root.mkdir()
    (data_root / "secret").write_text("redacted")
    destination = tmp_path / "backup.tar"
    real_open = tarfile.open
    observed_modes: list[int] = []

    def checked_open(name: str | Path, mode: Literal["w"]) -> tarfile.TarFile:
        observed_modes.append(stat.S_IMODE(Path(name).stat().st_mode))
        return real_open(name, mode)

    monkeypatch.setattr(backup, "DATA_ROOT", data_root)
    monkeypatch.setattr(backup.tarfile, "open", checked_open)

    backup.create_archive(destination)

    assert observed_modes == [0o600]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_install_file_snapshot_restores_content_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed = tmp_path / "managed"
    managed.write_bytes(b"working configuration")
    managed.chmod(0o640)
    snapshot = {managed: install._file_snapshot(managed)}
    managed.write_bytes(b"broken configuration")
    managed.chmod(0o600)
    monkeypatch.setattr(install, "chown", lambda path, uid, gid: None)

    install._restore_files(snapshot)

    assert managed.read_bytes() == b"working configuration"
    assert stat.S_IMODE(managed.stat().st_mode) == 0o640


def test_worker_limits_inspect_the_managed_launchd_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        args: list[str], *, input_text: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del input_text, check
        calls.append(args)
        if args[0] == "/bin/launchctl":
            return subprocess.CompletedProcess(args, 0, "state = running\n    pid = 42\n", "")
        return subprocess.CompletedProcess(
            args,
            0,
            "orchard worker run --resources oswe.vm-slots=2 --default-cpu 4 "
            "--default-memory 8192\n",
            "",
        )

    monkeypatch.setattr(status, "run", fake_run)

    assert status._worker_limits() == "slots=2 cpu=4 memory_mib=8192"
    assert calls[1] == ["/bin/ps", "-p", "42", "-o", "command="]


def test_worker_limits_reject_prefix_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        args: list[str], *, input_text: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del input_text, check
        if args[0] == "/bin/launchctl":
            return subprocess.CompletedProcess(args, 0, "state = running\n    pid = 42\n", "")
        return subprocess.CompletedProcess(
            args,
            0,
            "orchard worker run --resources oswe.vm-slots=20 --default-cpu 40 "
            "--default-memory 81920\n",
            "",
        )

    monkeypatch.setattr(status, "run", fake_run)

    with pytest.raises(RuntimeError, match="worker command is missing"):
        status._worker_limits()


def test_rollback_rejects_lexical_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    releases = tmp_path / "releases"
    (releases / "nested").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "previous"
    link.symlink_to(releases / "nested" / ".." / ".." / "outside")
    monkeypatch.setattr(rollback, "RELEASES_ROOT", releases)

    with pytest.raises(RuntimeError, match="outside"):
        rollback._validated_target(link, Path("orchard"))


def test_uninstall_rejects_an_unexpected_user_when_group_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedUser:
        pw_uid = 999
        pw_gid = 999

    def missing_group(name: str) -> None:
        del name
        raise KeyError

    monkeypatch.setattr(uninstall.pwd, "getpwnam", lambda name: UnexpectedUser())
    monkeypatch.setattr(uninstall.grp, "getgrnam", missing_group)

    with pytest.raises(RuntimeError, match="unexpected user"):
        uninstall._validate_identity()


def test_degraded_status_retains_diagnostics_and_returns_non_success() -> None:
    diagnostics = [
        status.Diagnostic("controller_service", False, "job is stopped"),
        status.Diagnostic("disk_report", True, "available_gib=1577 threshold=none", False),
    ]

    output, exit_code = status.render_status(diagnostics)
    payload = json.loads(output)

    assert exit_code == 1
    assert payload["status"] == "degraded"
    assert payload["diagnostics"][0]["detail"] == "job is stopped"
    assert payload["diagnostics"][1]["detail"] == "available_gib=1577 threshold=none"


def test_service_restoration_starts_only_missing_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(services, "is_loaded", lambda label: label == "controller")

    def fake_run(
        args: list[str], *, input_text: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del input_text, check
        started.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(services, "run", fake_run)
    monkeypatch.setattr(
        services,
        "SERVICE_PLISTS",
        {"controller": Path("/controller.plist"), "worker": Path("/worker.plist")},
    )

    services.start_services(("controller", "worker"))

    assert started == [["/bin/launchctl", "bootstrap", "system", "/worker.plist"]]


def test_backup_restores_services_once_and_preserves_primary_failure() -> None:
    primary = RuntimeError("archive failed")
    restore_calls = 0

    def stop() -> None:
        return None

    def archive() -> None:
        raise primary

    def restore() -> None:
        nonlocal restore_calls
        restore_calls += 1
        raise RuntimeError("restore failed")

    with pytest.raises(RuntimeError) as raised:
        backup.backup_with_restore(stop, archive, restore)

    assert raised.value is primary
    assert restore_calls == 1
    assert "service restoration also failed" in " ".join(raised.value.__notes__)


def test_service_files_are_redacted_and_pin_static_limits() -> None:
    controller = controller_wrapper()
    worker = worker_wrapper()
    rendered = controller + worker + controller_plist().decode() + worker_plist().decode()

    assert str(ADMIN_TOKEN) in controller
    assert str(WORKER_TOKEN) in worker
    assert "--bootstrap-token-stdin" in worker
    assert f"--resources oswe.vm-slots={VM_SLOTS}" in worker
    assert f"--default-cpu {DEFAULT_CPU}" in worker
    assert f"--default-memory {DEFAULT_MEMORY_MIB}" in worker
    assert DEFAULT_DISK_GIB == 40
    assert "secret value" not in rendered


def test_live_secret_paths_and_protected_root_are_exact() -> None:
    assert SUPPORT_ROOT == Path("/Library/Application Support/Mobilyze/OpenSWEOrchard")
    assert SUPPORT_ROOT_OWNER == "root"
    assert SUPPORT_ROOT_UID == 0
    assert SUPPORT_ROOT_MODE == 0o750
    assert ORCHARD_HOME == SUPPORT_ROOT
    assert SECRETS_ROOT == SUPPORT_ROOT / "secrets"
    assert ADMIN_TOKEN == SECRETS_ROOT / "bootstrap-admin"
    assert WORKER_TOKEN == SECRETS_ROOT / "worker-bootstrap"


def test_installed_secret_reuse_normalizes_mode_and_owner_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "bootstrap-admin"
    secret.write_bytes(b"protected")
    secret.chmod(0o600)
    ownership: list[tuple[Path, int, int]] = []

    def unexpected_write(path: Path, content: str | bytes, mode: int) -> None:
        del path, content, mode
        raise AssertionError("installed secret must not be rewritten onto itself")

    monkeypatch.setattr(install, "atomic_write", unexpected_write)
    monkeypatch.setattr(install, "chown", lambda path, uid, gid: ownership.append((path, uid, gid)))

    install._install_secret(secret, secret)

    assert stat.S_IMODE(secret.stat().st_mode) == 0o400
    assert ownership == [(secret, ACCOUNT_UID, ACCOUNT_GID)]


def test_protected_directory_reuse_rejects_symlinks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    managed = tmp_path / "managed"
    managed.symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        install._prepare_directory(managed, 0o700, owner=(ACCOUNT_UID, ACCOUNT_GID))


def test_install_preserves_root_owned_traversable_support_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared: list[tuple[Path, int, tuple[int, int] | None]] = []
    monkeypatch.setattr(
        install,
        "_prepare_directory",
        lambda path, mode, *, owner: prepared.append((path, mode, owner)),
    )

    install._prepare_directories()

    assert (SUPPORT_ROOT, 0o750, (0, ACCOUNT_GID)) in prepared
    assert (SECRETS_ROOT, 0o700, (ACCOUNT_UID, ACCOUNT_GID)) in prepared


def test_wrappers_preserve_environment_and_quote_spaced_orchard_home() -> None:
    controller = controller_wrapper()
    worker = worker_wrapper()
    expected_home = f"HOME={ACCOUNT_HOME}"
    expected_orchard_home = f"ORCHARD_HOME='{ORCHARD_HOME}'"

    for wrapper in (controller, worker):
        assert expected_home in wrapper
        assert expected_orchard_home in wrapper
        assert "export HOME ORCHARD_HOME" in wrapper
    assert f"'{ADMIN_TOKEN}'" in controller
    assert f"'{WORKER_TOKEN}'" in worker


def test_controller_wrapper_uses_direct_tailscale_activation_and_readiness() -> None:
    wrapper = controller_wrapper()
    activation = f"{TAILSCALE_BINARY} up"
    readiness = f'test "$({TAILSCALE_BINARY} ip -4)" = {LISTENER_HOST}'

    assert activation in wrapper
    assert readiness in wrapper
    assert wrapper.index(activation) < wrapper.index(" controller run ")
    assert "while " not in wrapper
    assert "sleep " not in wrapper
    assert plistlib.loads(controller_plist())["KeepAlive"] is True


def test_current_artifact_links_include_tart_app_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    linked: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        install, "atomic_symlink", lambda target, link: linked.append((target, link))
    )

    install._link_current_artifacts()

    assert linked == [
        (CURRENT_ORCHARD / "orchard", CURRENT_ORCHARD_BINARY),
        (CURRENT_TART / TART_EXECUTABLE_RELATIVE, CURRENT_TART_BINARY),
        (CURRENT_TART / TART_APP.name, CURRENT_TART_APP),
    ]


def test_status_proves_secret_metadata_without_reading_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "worker-bootstrap"
    secret.write_bytes(b"must not be read")
    secret.chmod(0o400)
    metadata = secret.lstat()

    monkeypatch.setattr(status, "ACCOUNT_UID", metadata.st_uid)
    monkeypatch.setattr(status, "ACCOUNT_GID", metadata.st_gid)
    monkeypatch.setattr(
        status.pwd, "getpwuid", lambda uid: type("Owner", (), {"pw_name": ACCOUNT_NAME})()
    )
    monkeypatch.setattr(
        status.grp, "getgrgid", lambda gid: type("Group", (), {"gr_name": ACCOUNT_NAME})()
    )

    def unexpected_read(path: Path) -> bytes:
        del path
        raise AssertionError("status must not read secret contents")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)

    detail = status._protected_path(secret, 0o400, directory=False)

    assert f"path={secret}" in detail
    assert "mode=0400" in detail
    assert f"owner={ACCOUNT_NAME}" in detail
    assert f"group={ACCOUNT_NAME}" in detail


def test_status_accepts_root_owned_support_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = type(
        "Metadata",
        (),
        {
            "st_mode": stat.S_IFDIR | 0o750,
            "st_uid": 0,
            "st_gid": ACCOUNT_GID,
        },
    )()
    monkeypatch.setattr(Path, "lstat", lambda path: metadata)
    monkeypatch.setattr(
        status.pwd, "getpwuid", lambda uid: type("Owner", (), {"pw_name": "root"})()
    )
    monkeypatch.setattr(
        status.grp, "getgrgid", lambda gid: type("Group", (), {"gr_name": ACCOUNT_NAME})()
    )

    detail = status._protected_path(
        SUPPORT_ROOT,
        SUPPORT_ROOT_MODE,
        directory=True,
        owner_name=SUPPORT_ROOT_OWNER,
        owner_uid=SUPPORT_ROOT_UID,
    )

    assert "mode=0750" in detail
    assert "owner=root" in detail
    assert f"group={ACCOUNT_NAME}" in detail


def test_status_main_requires_root(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def checked_root() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(status, "require_root", checked_root)
    monkeypatch.setattr(status, "collect_diagnostics", lambda: [])

    assert status.main() == 0
    assert calls == 1


def test_status_identity_requires_live_home_and_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = type(
        "User",
        (),
        {
            "pw_uid": ACCOUNT_UID,
            "pw_gid": ACCOUNT_GID,
            "pw_dir": str(ACCOUNT_HOME),
            "pw_shell": ACCOUNT_SHELL,
        },
    )()
    group = type("Group", (), {"gr_gid": ACCOUNT_GID})()
    monkeypatch.setattr(status.pwd, "getpwnam", lambda name: user)
    monkeypatch.setattr(status.grp, "getgrnam", lambda name: group)

    detail = status._identity()

    assert f"home={ACCOUNT_HOME}" in detail
    assert f"shell={ACCOUNT_SHELL}" in detail


def test_uninstall_manages_only_the_exact_protected_support_root() -> None:
    assert SUPPORT_ROOT in uninstall.MANAGED_PATHS
    assert SUPPORT_ROOT.parent not in uninstall.MANAGED_PATHS
    assert SUPPORT_ROOT.parent.parent not in uninstall.MANAGED_PATHS


def test_manifest_and_runbook_describe_live_paths_and_state_only_backups() -> None:
    project_root = Path(__file__).parents[2]
    manifest = json.loads(
        (project_root / "config/mobilyze/studio2-orchard-baseline.json").read_text()
    )
    runbook = (project_root / "docs/mobilyze/STUDIO2_ORCHARD_BASELINE.md").read_text()

    assert manifest["environment"] == {
        "HOME": str(DATA_ROOT),
        "ORCHARD_HOME": str(SUPPORT_ROOT),
    }
    assert manifest["secrets"]["paths"] == [str(ADMIN_TOKEN), str(WORKER_TOKEN)]
    assert manifest["secrets"]["support_root_mode"] == "0750"
    assert manifest["secrets"]["support_root_owner"] == "root"
    assert manifest["secrets"]["support_root_group"] == ACCOUNT_NAME
    assert manifest["secrets"]["mode"] == "0400"
    assert manifest["secrets"]["owner"] == ACCOUNT_NAME
    assert manifest["secrets"]["group"] == ACCOUNT_NAME
    assert manifest["releases"]["tart"]["compatibility_path"] == str(CURRENT_TART_APP)
    assert str(SUPPORT_ROOT) in runbook
    assert "`root:_opensweorchard` mode `0750`" in runbook
    assert "status is intentionally root-only" in runbook
    assert "deliberately exclude the protected support root and bootstrap secrets" in runbook
    assert "does not remove either parent directory" in runbook


def test_state_backup_excludes_protected_bootstrap_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "var" / "db" / "mobilyze-open-swe-orchard"
    data_root.mkdir(parents=True)
    (data_root / "controller-state").write_text("state")
    support_root = tmp_path / "Library" / "Application Support" / "Mobilyze" / "OpenSWEOrchard"
    secrets_root = support_root / "secrets"
    secrets_root.mkdir(parents=True)
    (secrets_root / "bootstrap-admin").write_text("secret")
    destination = tmp_path / "state.tar"
    monkeypatch.setattr(backup, "DATA_ROOT", data_root)

    backup.create_archive(destination)

    with tarfile.open(destination, "r") as archive:
        names = archive.getnames()
    assert any(name.endswith("controller-state") for name in names)
    assert all("bootstrap-admin" not in name for name in names)
    assert all(str(support_root.relative_to("/")) not in name for name in names)
