from __future__ import annotations

import hashlib
import json
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
    DEFAULT_CPU,
    DEFAULT_DISK_GIB,
    DEFAULT_MEMORY_MIB,
    VM_SLOTS,
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

    assert "bootstrap-admin.token" in controller
    assert "worker-bootstrap.token" in worker
    assert "--bootstrap-token-stdin" in worker
    assert f"--resources oswe.vm-slots={VM_SLOTS}" in worker
    assert f"--default-cpu {DEFAULT_CPU}" in worker
    assert f"--default-memory {DEFAULT_MEMORY_MIB}" in worker
    assert DEFAULT_DISK_GIB == 40
    assert "secret value" not in rendered
