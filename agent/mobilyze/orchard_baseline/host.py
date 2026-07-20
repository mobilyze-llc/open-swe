from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def run(
    args: list[str], *, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input_text, capture_output=True, text=True, check=check)


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this operation must run as root")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: expected {expected}, got {actual}")


def same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except FileNotFoundError:
        return left.resolve(strict=False) == right.resolve(strict=False)


def install_pinned_file(source: Path, destination: Path, expected_sha256: str) -> bool:
    verify_sha256(source, expected_sha256)
    if destination.exists():
        verify_sha256(destination, expected_sha256)
        os.chmod(destination, 0o755)
        if same_path(source, destination):
            return False
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        shutil.copy2(source, temporary)
        verify_sha256(temporary, expected_sha256)
        os.chmod(temporary, 0o755)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def install_pinned_tree(
    source: Path, destination: Path, executable_relative: Path, expected_sha256: str
) -> bool:
    verify_sha256(source / executable_relative, expected_sha256)
    if destination.exists():
        executable = destination / executable_relative
        verify_sha256(executable, expected_sha256)
        os.chmod(executable, 0o755)
        if same_path(source, destination):
            return False
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        shutil.copytree(source, temporary, symlinks=True)
        executable = temporary / executable_relative
        verify_sha256(executable, expected_sha256)
        os.chmod(executable, 0o755)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return True


def atomic_write(path: Path, content: str | bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    binary = isinstance(content, bytes)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb" if binary else "w") as file:
            file.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.tmp-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def chown(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)


def command_error(result: subprocess.CompletedProcess[Any]) -> str:
    return (result.stderr or result.stdout or f"exit {result.returncode}").strip()
