#!/usr/bin/env python3
"""Build, audit, and exercise the Fonix Linux x86_64 reference application.

Passing this gate is deliberately restricted to the exact glibc 2.27 target
profile.  Running the final ELF auditor on a newer distribution is useful
build evidence, but it is not accepted as target-host execution evidence.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Iterable, Mapping, NamedTuple, Sequence
import urllib.parse


_DIRECTORY = Path(__file__).resolve().parent


def _load_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:  # pragma: no cover
        raise RuntimeError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        specification.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


_COMMON = _load_module(
    "_fonix_linux_gate_common", _DIRECTORY / "run_macos_reference_app_gate.py"
)
_SOURCE_MANIFEST = _load_module(
    "_fonix_linux_gate_source_manifest", _DIRECTORY / "source_checksum_manifest.py"
)
_AUDITOR = _load_module(
    "_fonix_linux_gate_auditor", _DIRECTORY / "audit_linux_application.py"
)

# Extend only the common copier's closed generated-path inventory.  The Linux
# generated registrant and CMake list are committed source and are not omitted.
_COMMON.GENERATED_EXCLUSIONS = frozenset(
    set(_COMMON.GENERATED_EXCLUSIONS)
    | {
        "linux/flutter/ephemeral",
    }
)

LinuxReferenceAppGateError = _COMMON.MacOsReferenceAppGateError

TARGET_PROFILE = "required-ubuntu-18.04.6-glibc-2.27-x86_64-v1"
HOST_OS_ID = "ubuntu"
HOST_OS_VERSION_ID = "18.04"
HOST_GLIBC_VERSION = "2.27"
HOST_ARCHITECTURE = "x86_64"

REQUIRED_FLUTTER_REVISION = "bd1e75d918605c91b411e8789fb911e6c9a84534"
REQUIRED_FLUTTER_VERSION = "3.47.0-0.1.pre"
REQUIRED_LLVM_VERSION = "10.0.0"
REQUIRED_CMAKE_VERSION = "3.22.1"
REQUIRED_NINJA_VERSION = "1.10.2"
REQUIRED_BINUTILS_VERSION = "2.30"
REQUIRED_PKG_CONFIG_VERSION = "0.29.1"
REQUIRED_GTK_VERSION = "3.22.30"
REQUIRED_XORG_VERSION = "1.19.6"
REQUIRED_XVFB_PACKAGE_VERSION = "2:1.19.6-1ubuntu4.15"
REQUIRED_PYTHON_VERSION = "3.11.9"

ARTIFACT_ID = _AUDITOR.ARTIFACT_ID
ARCHIVE_BASENAME = "onnxruntime-linux-x64-1.27.1.tgz"
ARCHIVE_SHA256 = _AUDITOR.ARTIFACT_SHA256
ARCHIVE_SIZE_BYTES = _AUDITOR.ARTIFACT_SIZE_BYTES
ARCHIVE_SOURCE_REVISION = _AUDITOR.ARTIFACT_SOURCE_REVISION
ARCHIVE_URL = _AUDITOR.ARTIFACT_URL
APPLICATION_EXECUTABLE = _AUDITOR.APPLICATION_EXECUTABLE
APPLICATION_RELATIVE = Path("build/linux/x64/release/bundle")

REFERENCE_RECEIPT_PREFIX = "FONIX_REFERENCE_RECEIPT="
EXPECTED_REFERENCE_RECEIPT: dict[str, object] = {
    "schemaVersion": 1,
    "status": "passed",
    "runtimeVersion": "1.27.1",
    "runtimeSource": "bundled",
    "runtimeOwner": "wrapper",
    "artifactFlavor": "cpu",
    "platform": "linux",
    "architecture": "x86_64",
    "shimBuildId": ARTIFACT_ID,
    "artifactSha256": ARCHIVE_SHA256,
    "modelSha256": _AUDITOR.MODEL_SHA256,
    "outputValues": [1, 4, 9, 16, 25, 36],
    "activeProviders": ["cpu"],
    "fullCpuAssignment": True,
    "doubleClose": "passed",
}

COMMAND_TIMEOUT_SECONDS = 30 * 60
REFERENCE_TIMEOUT_SECONDS = 90
PROCESS_SETTLEMENT_SECONDS = 10
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_OUTPUT_BYTES = 64 * 1024
MAX_REPORT_BYTES = 32 * 1024
MAX_PATH_BYTES = 4096
MAX_COPY_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_HOOK_INVOCATIONS = 32
MAX_HOOK_FILES = 256
MAX_PRIVATE_ENTRIES = 64
MAX_GATE_TREE_ENTRIES = 100_000
MAX_GATE_TREE_BYTES = 512 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024

SHA256 = re.compile(r"^[0-9a-f]{64}$")
HOOK_INVOCATION = re.compile(r"^[0-9a-f]{10}$")
DISPLAY_TOKEN = re.compile(r"^:[0-9]{1,5}$")


class PinnedArchive(NamedTuple):
    artifact_id: str
    basename: str
    sha256: str
    size_bytes: int


class TreeIdentity(NamedTuple):
    file_count: int
    byte_count: int
    tree_sha256: str


class HookProvenance(NamedTuple):
    input_path: Path
    shim: Path
    runtime: Path
    provider: Path
    staging: Path


class ToolchainIdentity(NamedTuple):
    python_version: str
    python_sha256: str
    flutter_version: str
    flutter_revision: str
    clang_version: str
    archiver_version: str
    linker_version: str
    cmake_version: str
    ninja_version: str
    binutils_version: str
    pkg_config_version: str
    gtk_version: str
    xorg_version: str
    xvfb_package_version: str
    xvfb_sha256: str


def _strict_json(data: bytes | str, label: str, *, maximum: int = MAX_COMMAND_OUTPUT_BYTES) -> Any:
    return _COMMON._strict_json(data, label, maximum=maximum)


def _regular_file(path: Path, label: str, *, maximum: int | None = None) -> Path:
    return _COMMON._regular_file(path, label, maximum=maximum)


def _directory(path: Path, label: str) -> Path:
    return _COMMON._directory(path, label)


def _sha256(path: Path) -> str:
    path = _regular_file(
        path, "gate hash input", maximum=_AUDITOR.MAX_FILE_BYTES
    )
    before = path.lstat()
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    after = path.lstat()
    if identity != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise LinuxReferenceAppGateError("gate hash input changed while hashing")
    return digest.hexdigest()


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
    maximum_output: int = MAX_COMMAND_OUTPUT_BYTES,
    operation: str,
) -> Any:
    stdout, stderr = _execute_group(
        command,
        cwd=cwd,
        environment=environment,
        timeout_seconds=timeout_seconds,
        maximum_output=maximum_output,
        operation=operation,
    )
    return _COMMON.CommandOutput(stdout=stdout, stderr=stderr)


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    source = _regular_file(path, "host os-release", maximum=64 * 1024).read_text(
        encoding="utf-8"
    )
    result: dict[str, str] = {}
    for line in source.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise LinuxReferenceAppGateError("host os-release contains an invalid line")
        key, raw = line.split("=", 1)
        if key in result or re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None:
            raise LinuxReferenceAppGateError("host os-release contains an invalid key")
        if raw.startswith('"'):
            if not raw.endswith('"') or len(raw) < 2 or "\\" in raw[1:-1]:
                raise LinuxReferenceAppGateError("host os-release quoting is unsupported")
            raw = raw[1:-1]
        if len(raw.encode("utf-8")) > 1024 or any(ord(character) < 0x20 for character in raw):
            raise LinuxReferenceAppGateError("host os-release value is outside its bound")
        result[key] = raw
    return result


def _single_version_line(output: str, pattern: re.Pattern[str], label: str) -> str:
    matches = pattern.findall(output)
    if len(matches) != 1:
        raise LinuxReferenceAppGateError(f"could not identify exact {label} version")
    value = matches[0]
    return value if isinstance(value, str) else value[0]


def _verify_exact_host() -> None:
    if platform.system() != "Linux" or platform.machine() != HOST_ARCHITECTURE:
        raise LinuxReferenceAppGateError(
            "the Linux target gate requires the exact Ubuntu 18.04 glibc 2.27 x86_64 host profile"
        )
    release = _read_os_release()
    if (
        release.get("ID") != HOST_OS_ID
        or release.get("VERSION_ID") != HOST_OS_VERSION_ID
        or release.get("VERSION") != "18.04.6 LTS (Bionic Beaver)"
        or release.get("VERSION_CODENAME") != "bionic"
        or release.get("UBUNTU_CODENAME") != "bionic"
    ):
        raise LinuxReferenceAppGateError(
            "newer or different Linux hosts may audit bytes but cannot pass the glibc 2.27 target-host gate"
        )
    libc_name, libc_version = platform.libc_ver()
    if libc_name != "glibc" or libc_version != HOST_GLIBC_VERSION:
        raise LinuxReferenceAppGateError("target-host glibc identity changed")


def _executable(path: Path, label: str) -> Path:
    path = _regular_file(path.resolve(strict=True), label, maximum=256 * 1024 * 1024)
    if not path.is_absolute() or not os.access(path, os.X_OK):
        raise LinuxReferenceAppGateError(f"{label} must be an absolute executable")
    return path


def _tool_alias(tool_bin: Path, name: str, target: Path) -> Path:
    tool_bin = _directory(tool_bin, "gate-owned tool directory")
    alias = tool_bin / name
    try:
        mode = alias.lstat().st_mode
    except OSError as error:
        raise LinuxReferenceAppGateError(f"missing verified tool alias {name}") from error
    if (
        not stat.S_ISLNK(mode)
        or alias.resolve(strict=True) != target
        or not os.access(alias, os.X_OK)
    ):
        raise LinuxReferenceAppGateError(f"verified tool alias {name} changed")
    return alias.absolute()


def _version_output(
    path: Path,
    arguments: Sequence[str],
    label: str,
    environment: Mapping[str, str],
) -> str:
    result = _run(
        (str(path), *arguments),
        environment=environment,
        operation=f"{label} version",
    )
    return result.stdout + "\n" + result.stderr


def _require_isolated_python_invocation() -> None:
    if (
        sys.flags.isolated != 1
        or sys.flags.ignore_environment != 1
        or sys.flags.no_user_site != 1
        or sys.flags.no_site != 1
        or not sys.dont_write_bytecode
    ):
        raise LinuxReferenceAppGateError(
            "run the Linux target gate with the exact Python interpreter and -I -S -B"
        )


def _verify_python_runtime() -> tuple[str, str]:
    version = platform.python_version()
    if version != REQUIRED_PYTHON_VERSION:
        raise LinuxReferenceAppGateError(
            f"Python {REQUIRED_PYTHON_VERSION} is required by the target profile"
        )
    executable = _executable(Path(sys.executable), "Python executable")
    return version, _sha256(executable)


def _verify_flutter(
    flutter: Path, environment: Mapping[str, str]
) -> Mapping[str, object]:
    result = _run(
        (str(flutter), "--version", "--machine"),
        environment=environment,
        operation="Flutter version",
    )
    value = _strict_json(result.stdout, "Flutter version report")
    if not isinstance(value, dict):
        raise LinuxReferenceAppGateError("Flutter version report is not an object")
    return value


def _verify_toolchain(
    *,
    flutter: Path,
    clang: Path,
    clangxx: Path,
    archiver: Path,
    linker: Path,
    cmake: Path,
    ninja: Path,
    readelf: Path,
    pkg_config: Path,
    dpkg_query: Path,
    xvfb: Path,
    tool_bin: Path,
    environment: Mapping[str, str],
) -> ToolchainIdentity:
    tools = {
        "flutter": _executable(flutter, "Flutter executable"),
        "clang": _executable(clang, "Clang executable"),
        "clangxx": _executable(clangxx, "Clang++ executable"),
        "archiver": _executable(archiver, "LLVM archiver executable"),
        "linker": _executable(linker, "LLVM linker executable"),
        "cmake": _executable(cmake, "CMake executable"),
        "ninja": _executable(ninja, "Ninja executable"),
        "readelf": _executable(readelf, "GNU readelf executable"),
        "pkg_config": _executable(pkg_config, "pkg-config executable"),
        "dpkg_query": _executable(dpkg_query, "dpkg-query executable"),
        "xvfb": _executable(xvfb, "Xvfb executable"),
    }
    probes = {
        "clang": _tool_alias(tool_bin, "clang", tools["clang"]),
        "clangxx": _tool_alias(tool_bin, "clang++", tools["clangxx"]),
        "archiver": _tool_alias(tool_bin, "llvm-ar", tools["archiver"]),
        "linker": _tool_alias(tool_bin, "ld.lld", tools["linker"]),
        "cmake": _tool_alias(tool_bin, "cmake", tools["cmake"]),
        "ninja": _tool_alias(tool_bin, "ninja", tools["ninja"]),
        "readelf": _tool_alias(tool_bin, "readelf", tools["readelf"]),
        "pkg_config": _tool_alias(tool_bin, "pkg-config", tools["pkg_config"]),
        "dpkg_query": _tool_alias(tool_bin, "dpkg-query", tools["dpkg_query"]),
    }
    _tool_alias(tool_bin, "llvm-ar-10", tools["archiver"])
    python_version, python_sha256 = _verify_python_runtime()
    flutter_value = _verify_flutter(tools["flutter"], environment)
    if (
        flutter_value.get("frameworkRevision") != REQUIRED_FLUTTER_REVISION
        or flutter_value.get("frameworkVersion") != REQUIRED_FLUTTER_VERSION
    ):
        raise LinuxReferenceAppGateError("Flutter version/revision differs from the target profile")
    clang_output = _version_output(
        probes["clang"], ("--version",), "Clang", environment
    )
    clangxx_output = _version_output(
        probes["clangxx"], ("--version",), "Clang++", environment
    )
    clang_version = _single_version_line(
        clang_output, re.compile(r"\bclang version ([0-9]+(?:\.[0-9]+){2})\b"), "Clang"
    )
    clangxx_version = _single_version_line(
        clangxx_output, re.compile(r"\bclang version ([0-9]+(?:\.[0-9]+){2})\b"), "Clang++"
    )
    archiver_version = _single_version_line(
        _version_output(
            probes["archiver"], ("--version",), "LLVM archiver", environment
        ),
        re.compile(r"\bLLVM version ([0-9]+(?:\.[0-9]+){2})\b"),
        "LLVM archiver",
    )
    linker_version = _single_version_line(
        _version_output(
            probes["linker"], ("--version",), "LLVM linker", environment
        ),
        re.compile(r"\bLLD ([0-9]+(?:\.[0-9]+){2})\b"),
        "LLVM linker",
    )
    cmake_version = _single_version_line(
        _version_output(probes["cmake"], ("--version",), "CMake", environment),
        re.compile(r"^cmake version ([0-9]+(?:\.[0-9]+){2})$", re.MULTILINE),
        "CMake",
    )
    ninja_version = _version_output(
        probes["ninja"], ("--version",), "Ninja", environment
    ).strip()
    binutils_version = _single_version_line(
        _version_output(
            probes["readelf"], ("--version",), "GNU readelf", environment
        ),
        re.compile(r"GNU readelf .*? ([0-9]+\.[0-9]+)(?:\.[0-9]+)?(?:\s|$)"),
        "GNU binutils",
    )
    pkg_config_version = _version_output(
        probes["pkg_config"], ("--version",), "pkg-config", environment
    ).strip()
    gtk_version = _version_output(
        probes["pkg_config"],
        ("--modversion", "gtk+-3.0"),
        "GTK",
        environment,
    ).strip()
    if (
        tools["xvfb"] != Path("/usr/bin/Xvfb")
        or tools["dpkg_query"] != Path("/usr/bin/dpkg-query")
    ):
        raise LinuxReferenceAppGateError(
            "target profile requires packaged /usr/bin/Xvfb and dpkg-query"
        )
    package_identity = _version_output(
        probes["dpkg_query"],
        ("-W", "-f=${Package}\\t${Version}\\t${Architecture}\\n", "xvfb"),
        "Xvfb package",
        environment,
    ).strip()
    expected_package_identity = (
        f"xvfb\t{REQUIRED_XVFB_PACKAGE_VERSION}\tamd64"
    )
    ownership = _version_output(
        probes["dpkg_query"],
        ("-S", "/usr/bin/Xvfb"),
        "Xvfb package ownership",
        environment,
    ).strip()
    if package_identity != expected_package_identity or ownership != "xvfb: /usr/bin/Xvfb":
        raise LinuxReferenceAppGateError("Xvfb package identity changed")
    xorg_version = REQUIRED_XORG_VERSION
    xvfb_sha256 = _sha256(tools["xvfb"])
    actual = {
        "clang": clang_version,
        "clangxx": clangxx_version,
        "archiver": archiver_version,
        "linker": linker_version,
        "cmake": cmake_version,
        "ninja": ninja_version,
        "binutils": binutils_version,
        "pkg-config": pkg_config_version,
        "gtk": gtk_version,
        "xorg": xorg_version,
    }
    expected = {
        "clang": REQUIRED_LLVM_VERSION,
        "clangxx": REQUIRED_LLVM_VERSION,
        "archiver": REQUIRED_LLVM_VERSION,
        "linker": REQUIRED_LLVM_VERSION,
        "cmake": REQUIRED_CMAKE_VERSION,
        "ninja": REQUIRED_NINJA_VERSION,
        "binutils": REQUIRED_BINUTILS_VERSION,
        "pkg-config": REQUIRED_PKG_CONFIG_VERSION,
        "gtk": REQUIRED_GTK_VERSION,
        "xorg": REQUIRED_XORG_VERSION,
    }
    if actual != expected:
        raise LinuxReferenceAppGateError("Linux target toolchain tuple changed")
    return ToolchainIdentity(
        python_version=python_version,
        python_sha256=python_sha256,
        flutter_version=REQUIRED_FLUTTER_VERSION,
        flutter_revision=REQUIRED_FLUTTER_REVISION,
        clang_version=clang_version,
        archiver_version=archiver_version,
        linker_version=linker_version,
        cmake_version=cmake_version,
        ninja_version=ninja_version,
        binutils_version=binutils_version,
        pkg_config_version=pkg_config_version,
        gtk_version=gtk_version,
        xorg_version=xorg_version,
        xvfb_package_version=REQUIRED_XVFB_PACKAGE_VERSION,
        xvfb_sha256=xvfb_sha256,
    )


def _tree_identity(root: Path, label: str) -> TreeIdentity:
    root = _directory(root, label)
    root_mode = stat.S_IMODE(root.lstat().st_mode)
    if root_mode not in {0o700, 0o755}:
        raise LinuxReferenceAppGateError(f"{label} root mode is invalid")
    canonical: list[dict[str, object]] = [
        {
            "path": ".",
            "type": "directory",
            "mode": format(root_mode, "04o"),
        }
    ]
    file_count = 0
    byte_count = 0
    entry_count = 0
    for current_raw, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_raw)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            candidate = current / name
            relative = candidate.relative_to(root).as_posix()
            _AUDITOR._safe_relative_path(relative, f"{label} directory path")
            entry_count += 1
            directory_mode = candidate.lstat().st_mode
            if entry_count > MAX_GATE_TREE_ENTRIES or not stat.S_ISDIR(directory_mode):
                raise LinuxReferenceAppGateError(f"{label} directory inventory is invalid")
            mode = stat.S_IMODE(directory_mode)
            if mode not in {0o700, 0o755}:
                raise LinuxReferenceAppGateError(f"{label} directory mode is invalid")
            canonical.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": format(mode, "04o"),
                }
            )
        for name in file_names:
            candidate = current / name
            entry_count += 1
            if entry_count > MAX_GATE_TREE_ENTRIES:
                raise LinuxReferenceAppGateError(f"{label} exceeds its entry bound")
            relative = candidate.relative_to(root).as_posix()
            _AUDITOR._safe_relative_path(relative, f"{label} path")
            candidate = _regular_file(candidate, f"{label} file", maximum=_AUDITOR.MAX_FILE_BYTES)
            metadata = candidate.lstat()
            byte_count += metadata.st_size
            if byte_count > MAX_GATE_TREE_BYTES:
                raise LinuxReferenceAppGateError(f"{label} exceeds its byte bound")
            file_count += 1
            canonical.append(
                {
                    "path": relative,
                    "type": "file",
                    "sizeBytes": metadata.st_size,
                    "sha256": _sha256(candidate),
                    "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
                }
            )
    canonical.sort(key=lambda entry: (str(entry["path"]), str(entry["type"])))
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return TreeIdentity(file_count, byte_count, digest)


def _snapshot_source_epoch(repository: Path, destination: Path) -> tuple[Path, dict[str, object]]:
    if destination.exists() or destination.is_symlink():
        raise LinuxReferenceAppGateError("source epoch destination must not exist")
    manifest_path = repository / "MANIFEST.sha256"
    try:
        initial_sha = _SOURCE_MANIFEST.check_manifest(repository, manifest_path)
        manifest_bytes = _SOURCE_MANIFEST._regular_file_bytes(
            manifest_path,
            label="source checksum manifest",
            maximum=_SOURCE_MANIFEST.MAX_MANIFEST_BYTES,
        )
        entries = _SOURCE_MANIFEST.parse_manifest(manifest_bytes)
    except _SOURCE_MANIFEST.SourceManifestError as error:
        raise LinuxReferenceAppGateError("live Fonix source is not one closed manifest epoch") from error
    destination.mkdir(mode=0o700)
    total = 0
    try:
        for relative, expected_sha in entries:
            source = _regular_file(
                repository.joinpath(*relative.split("/")),
                f"manifest source {relative}",
                maximum=_SOURCE_MANIFEST.MAX_FILE_BYTES,
            )
            before = source.lstat()
            contents = _SOURCE_MANIFEST._regular_file_bytes(
                source,
                label=f"manifest source {relative}",
                maximum=_SOURCE_MANIFEST.MAX_FILE_BYTES,
            )
            after = source.lstat()
            if (
                hashlib.sha256(contents).hexdigest() != expected_sha
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode)
            ):
                raise LinuxReferenceAppGateError(f"manifest source changed while freezing: {relative}")
            total += len(contents)
            if total > _SOURCE_MANIFEST.MAX_TOTAL_BYTES:
                raise LinuxReferenceAppGateError("source epoch exceeds its byte bound")
            target = destination.joinpath(*relative.split("/"))
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            target.chmod(0o700 if before.st_mode & 0o111 else 0o600)
        target_manifest = destination / "MANIFEST.sha256"
        with target_manifest.open("xb") as stream:
            stream.write(manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        target_manifest.chmod(0o600)
        if (
            _SOURCE_MANIFEST.check_manifest(destination, target_manifest) != initial_sha
            or _SOURCE_MANIFEST.check_manifest(repository, manifest_path) != initial_sha
        ):
            raise LinuxReferenceAppGateError("source manifest identity changed during snapshot")
        tree = _tree_identity(destination, "frozen source epoch")
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination, {
        "manifestSha256": initial_sha,
        "fileCount": tree.file_count,
        "byteCount": tree.byte_count,
        "treeSha256": tree.tree_sha256,
    }


def _load_pinned_archive(lock_path: Path) -> PinnedArchive:
    lock = _strict_json(
        _regular_file(lock_path, "native lock", maximum=1024 * 1024).read_bytes(),
        "native lock",
        maximum=1024 * 1024,
    )
    artifacts = lock.get("artifacts") if isinstance(lock, dict) else None
    if not isinstance(artifacts, list):
        raise LinuxReferenceAppGateError("native lock artifact list is invalid")
    selected = []
    for value in artifacts:
        target = value.get("target") if isinstance(value, dict) else None
        if (
            isinstance(target, dict)
            and target.get("os") == "linux"
            and target.get("architecture") == "x86_64"
            and target.get("variant") == "default"
            and value.get("flavor") == "cpu"
        ):
            selected.append(value)
    if len(selected) != 1:
        raise LinuxReferenceAppGateError("lock must select one Linux x86_64 default CPU artifact")
    artifact = selected[0]
    source = artifact.get("source")
    target = artifact.get("target")
    if (
        artifact.get("id") != ARTIFACT_ID
        or artifact.get("runtime_mode") != "bundled"
        or target.get("min_os") != "glibc-2.27"
        or not isinstance(source, dict)
        or source.get("archive") != "tgz"
        or source.get("url") != ARCHIVE_URL
        or source.get("source_revision") != ARCHIVE_SOURCE_REVISION
        or source.get("sha256") != ARCHIVE_SHA256
        or source.get("size_bytes") != ARCHIVE_SIZE_BYTES
    ):
        raise LinuxReferenceAppGateError("selected Linux archive identity changed")
    parsed = urllib.parse.urlsplit(ARCHIVE_URL)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or PurePosixPath(parsed.path).name != ARCHIVE_BASENAME
    ):
        raise LinuxReferenceAppGateError("selected Linux archive URL is unsafe")
    return PinnedArchive(ARTIFACT_ID, ARCHIVE_BASENAME, ARCHIVE_SHA256, ARCHIVE_SIZE_BYTES)


def _copy_archive(source_cache: Path, work: Path, archive: PinnedArchive) -> Path:
    source = _regular_file(
        _directory(source_cache.resolve(strict=True), "artifact cache") / archive.basename,
        "pinned Linux archive",
        maximum=MAX_ARCHIVE_BYTES,
    )
    destination_directory = work / ".fonix-artifact-cache"
    if destination_directory.exists() or destination_directory.is_symlink():
        raise LinuxReferenceAppGateError("private artifact cache already exists")
    destination_directory.mkdir(mode=0o700)
    destination = destination_directory / archive.basename
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            size, digest = _COMMON._copy_and_hash(
                input_stream, output_stream, MAX_ARCHIVE_BYTES
            )
            output_stream.flush()
            os.fsync(output_stream.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if size != archive.size_bytes or digest != archive.sha256:
        destination.unlink(missing_ok=True)
        raise LinuxReferenceAppGateError("cached Linux archive differs from the lock")
    destination.chmod(0o600)
    return destination


def _patch_linux_pubspec(pubspec: Path, repository: Path) -> None:
    _COMMON._patch_fonix_path_dependency(pubspec, repository)
    pubspec = _regular_file(pubspec, "external reference pubspec", maximum=1024 * 1024)
    source = pubspec.read_text(encoding="utf-8")
    apple_line = "      application_minimum_os: '14.0'\n"
    if (
        source.count("      runtime_mode: bundled\n") != 1
        or source.count("      artifact_cache: .fonix-artifact-cache\n") != 1
        or source.count(apple_line) != 1
    ):
        raise LinuxReferenceAppGateError("external pubspec hook defines are not exact")


def _patch_linux_lockfile(lockfile: Path, repository: Path) -> None:
    lockfile = _regular_file(
        lockfile, "external reference lockfile", maximum=2 * 1024 * 1024
    )
    source = lockfile.read_text(encoding="utf-8")
    relative = '      path: ".."\n      relative: true\n'
    absolute = (
        f"      path: {json.dumps(repository.as_posix())}\n"
        "      relative: false\n"
    )
    if source.count(relative) != 1 or source.count(absolute) != 0:
        raise LinuxReferenceAppGateError("reference lockfile Fonix path is not exact")
    updated = source.replace(relative, absolute)
    if updated.replace(absolute, relative) != source:
        raise LinuxReferenceAppGateError("reference lockfile path patch changed unexpected bytes")
    lockfile.write_text(updated, encoding="utf-8", newline="")


def _require_dependency_epoch(
    application: Path, *, pubspec_sha256: str, lock_sha256: str
) -> None:
    pubspec = _regular_file(
        application / "pubspec.yaml", "external reference pubspec", maximum=1024 * 1024
    )
    lock = _regular_file(
        application / "pubspec.lock", "external reference lockfile", maximum=2 * 1024 * 1024
    )
    if _sha256(pubspec) != pubspec_sha256 or _sha256(lock) != lock_sha256:
        raise LinuxReferenceAppGateError("external reference dependency epoch changed")


def _build_environment(
    *,
    home: Path,
    temporary: Path,
    pub_cache: Path,
    tool_bin: Path,
    clang: Path,
    clangxx: Path,
    archiver: Path,
    linker: Path,
) -> dict[str, str]:
    home = _directory(home, "gate-owned build home")
    temporary = _directory(temporary, "gate-owned build temporary directory")
    pub_cache = _directory(pub_cache.resolve(strict=True), "offline Dart pub cache")
    tool_bin = _directory(tool_bin, "gate-owned tool directory")
    return {
        "PATH": f"{tool_bin}:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "PUB_CACHE": str(pub_cache),
        "CC": str(clang),
        "CXX": str(clangxx),
        "AR": str(archiver),
        "LD": str(linker),
        "CMAKE_GENERATOR": "Ninja",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "CI": "true",
        "DART_SUPPRESS_ANALYTICS": "true",
        "FLUTTER_SUPPRESS_ANALYTICS": "true",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _prepare_tool_bin(root: Path, tools: Mapping[str, Path]) -> Path:
    if root.exists() or root.is_symlink():
        raise LinuxReferenceAppGateError("gate-owned tool directory already exists")
    root.mkdir(mode=0o700)
    aliases: dict[str, Path] = {}
    for name, tool in tools.items():
        if (
            not name
            or "/" in name
            or name in {".", ".."}
            or any(ord(character) < 0x21 for character in name)
        ):
            raise LinuxReferenceAppGateError("verified tool alias is invalid")
        aliases[name] = _executable(tool, f"verified {name} executable")
    for name, tool in sorted(aliases.items()):
        link = root / name
        link.symlink_to(tool)
        if link.resolve(strict=True) != tool:
            raise LinuxReferenceAppGateError("gate-owned tool binding changed")
    search_path = f"{root}:/usr/bin:/bin"
    for name, tool in aliases.items():
        selected = shutil.which(name, path=search_path)
        if selected is None or Path(selected).resolve(strict=True) != tool:
            raise LinuxReferenceAppGateError("PATH does not select the verified tool tuple")
    return root


def _probe_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _discover_hook_provenance(work: Path, application: Path) -> HookProvenance:
    hook_root = _directory(work / ".dart_tool/hooks_runner/fonix", "Fonix hook invocation root")
    inputs = sorted(hook_root.glob("*/input.json"))
    if len(inputs) == 0 or len(inputs) > MAX_HOOK_INVOCATIONS:
        raise LinuxReferenceAppGateError("hook invocation count is outside its bound")
    matches: list[tuple[HookProvenance, str]] = []
    for path in inputs:
        if HOOK_INVOCATION.fullmatch(path.parent.name) is None:
            raise LinuxReferenceAppGateError("hook invocation directory name is invalid")
        value = _strict_json(
            _regular_file(path, "hook input", maximum=_AUDITOR.MAX_JSON_BYTES).read_bytes(),
            "hook input",
            maximum=_AUDITOR.MAX_JSON_BYTES,
        )
        code = value.get("config", {}).get("extensions", {}).get("code_assets", {}) if isinstance(value, dict) else {}
        if code.get("target_os") != "linux" or code.get("target_architecture") != "x64":
            continue
        if not isinstance(value.get("out_dir_shared"), str):
            raise LinuxReferenceAppGateError("Linux x64 hook shared output is invalid")
        try:
            shared = _directory(
                Path(value["out_dir_shared"]).resolve(strict=True),
                "hook shared output",
            )
            shared.relative_to(work.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise LinuxReferenceAppGateError(
                "hook shared output escaped the external build"
            ) from error
        output_path = _regular_file(
            path.parent / "output.json",
            "Linux x64 hook output",
            maximum=_AUDITOR.MAX_JSON_BYTES,
        )
        output = _strict_json(
            output_path.read_bytes(),
            "Linux x64 hook output",
            maximum=_AUDITOR.MAX_JSON_BYTES,
        )
        if not isinstance(output, dict) or set(output) != {
            "assets",
            "assets_for_linking",
            "dependencies",
            "status",
            "timestamp",
        }:
            raise LinuxReferenceAppGateError("Linux x64 hook output field set changed")
        if output["assets_for_linking"] != {} or output["status"] != "success":
            raise LinuxReferenceAppGateError("Linux x64 hook output success/link state changed")
        dependencies = output["dependencies"]
        if (
            not isinstance(dependencies, list)
            or not 1 <= len(dependencies) <= 256
            or any(not isinstance(item, str) for item in dependencies)
        ):
            raise LinuxReferenceAppGateError("Linux x64 hook dependency list is invalid")
        timestamp = output["timestamp"]
        if not isinstance(timestamp, str) or _AUDITOR.HOOK_TIMESTAMP.fullmatch(timestamp) is None:
            raise LinuxReferenceAppGateError("Linux x64 hook timestamp changed")
        assets = output["assets"]
        if not isinstance(assets, list) or len(assets) != 3:
            raise LinuxReferenceAppGateError("Linux x64 hook output asset count changed")
        expected_names = {
            "package:fonix/fonix_shim": _AUDITOR.SHIM_NAME,
            "package:fonix/onnxruntime": _AUDITOR.RUNTIME_NAME,
            "package:fonix/onnxruntime_providers_shared": _AUDITOR.PROVIDER_NAME,
        }
        resolved: dict[str, Path] = {}
        matches_final = True
        normalized_assets: list[dict[str, object]] = []
        for raw_asset in assets:
            if not isinstance(raw_asset, dict) or set(raw_asset) != {"encoding", "type"}:
                raise LinuxReferenceAppGateError("Linux x64 hook asset field set changed")
            encoding = raw_asset.get("encoding")
            if (
                raw_asset.get("type") != "code_assets/code"
                or not isinstance(encoding, dict)
                or set(encoding) != {"file", "id", "link_mode"}
                or encoding.get("link_mode") != {"type": "dynamic_loading_bundle"}
            ):
                raise LinuxReferenceAppGateError("Linux x64 hook asset encoding changed")
            identifier = encoding.get("id")
            raw_file = encoding.get("file")
            if (
                not isinstance(identifier, str)
                or identifier in resolved
                or identifier not in expected_names
                or not isinstance(raw_file, str)
            ):
                raise LinuxReferenceAppGateError("Linux x64 hook asset identity changed")
            try:
                candidate = _regular_file(
                    Path(raw_file).resolve(strict=True),
                    f"Linux x64 hook asset {identifier}",
                )
                candidate.relative_to(shared)
            except (OSError, ValueError) as error:
                raise LinuxReferenceAppGateError(
                    "Linux x64 hook asset escaped shared output"
                ) from error
            if candidate.name != expected_names[identifier]:
                raise LinuxReferenceAppGateError("Linux x64 hook asset filename changed")
            installed = _regular_file(
                application / "lib" / expected_names[identifier],
                f"installed hook asset {identifier}",
            )
            if (
                candidate.stat().st_size != installed.stat().st_size
                or _sha256(candidate) != _sha256(installed)
            ):
                matches_final = False
            resolved[identifier] = candidate
            normalized_assets.append(
                {
                    "id": identifier,
                    "name": candidate.name,
                    "sizeBytes": candidate.stat().st_size,
                    "sha256": _sha256(candidate),
                }
            )
        if set(resolved) != set(expected_names):
            raise LinuxReferenceAppGateError("Linux x64 hook output asset set changed")
        runtime = resolved["package:fonix/onnxruntime"]
        provider = resolved["package:fonix/onnxruntime_providers_shared"]
        if runtime.parent != provider.parent:
            raise LinuxReferenceAppGateError("hook runtime/provider staging adjacency changed")
        staging = _directory(runtime.parent, "hook native staging directory")
        _regular_file(staging / "fonix-native-artifact-manifest.json", "hook resolver manifest")
        _regular_file(staging / "notices/ThirdPartyNotices.txt", "hook notices")
        if not matches_final:
            continue
        normalized_input = {
            key: item
            for key, item in value.items()
            if key not in {"out_dir_shared", "out_file"}
        }
        signature_value = {
            "input": normalized_input,
            "assets": sorted(normalized_assets, key=lambda item: str(item["id"])),
            "dependencies": sorted(set(dependencies)),
        }
        signature = hashlib.sha256(
            json.dumps(signature_value, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        matches.append(
            (
                HookProvenance(
                    path,
                    resolved["package:fonix/fonix_shim"],
                    runtime,
                    provider,
                    staging,
                ),
                signature,
            )
        )
    if not matches:
        raise LinuxReferenceAppGateError(
            "no Linux x64 hook invocation produced the final installed bytes"
        )
    if len({signature for _, signature in matches}) != 1:
        raise LinuxReferenceAppGateError(
            "multiple non-equivalent hook invocations produced the final installed bytes"
        )
    return matches[-1][0]


def _validate_audit_report(value: Any, tree: TreeIdentity) -> dict[str, object]:
    expected_fields = {
        "schemaVersion",
        "result",
        "target",
        "artifact",
        "applicationTree",
        "assets",
        "flutterRuntime",
        "hook",
        "shimBuildManifestSha256",
        "elf",
        "dortExportCount",
        "hardening",
        "claimBoundary",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or value.get("schemaVersion") != 1
        or value.get("result") != "passed"
    ):
        raise LinuxReferenceAppGateError("Linux application auditor did not pass")
    if value.get("target") != {"os": "linux", "architecture": "x86_64", "minimumOs": "glibc-2.27"}:
        raise LinuxReferenceAppGateError("Linux application audit target changed")
    application_tree = value.get("applicationTree")
    if not isinstance(application_tree, dict) or set(application_tree) != {
        "fileCount",
        "byteCount",
        "sha256",
        "identityFormat",
    } or (
        application_tree.get("fileCount"),
        application_tree.get("byteCount"),
        application_tree.get("sha256"),
    ) != tree:
        raise LinuxReferenceAppGateError("Linux application audit tree binding changed")
    if (
        application_tree.get("identityFormat")
        != "canonical-root-directory-file-size-sha256-mode-v2"
    ):
        raise LinuxReferenceAppGateError("Linux application audit identity format changed")
    if value.get("dortExportCount") != 67:
        raise LinuxReferenceAppGateError("Linux application audit export count changed")

    def require_sha(raw: object, label: str) -> str:
        if not isinstance(raw, str) or SHA256.fullmatch(raw) is None:
            raise LinuxReferenceAppGateError(f"Linux application audit {label} changed")
        return raw

    artifact = value.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {
        "id",
        "sourceSha256",
        "resolverManifestSha256",
    } or artifact.get("id") != ARTIFACT_ID or artifact.get("sourceSha256") != ARCHIVE_SHA256:
        raise LinuxReferenceAppGateError("Linux application audit artifact changed")
    require_sha(artifact.get("resolverManifestSha256"), "resolver manifest")
    assets = value.get("assets")
    if not isinstance(assets, dict) or set(assets) != {
        "modelSha256",
        "xnnpackModelSha256",
        "nativeAssetsManifestSha256",
        "noticesSha256",
    } or assets.get("modelSha256") != _AUDITOR.MODEL_SHA256 or assets.get(
        "xnnpackModelSha256"
    ) != _AUDITOR.XNNPACK_MODEL_SHA256:
        raise LinuxReferenceAppGateError("Linux application audit assets changed")
    require_sha(assets.get("nativeAssetsManifestSha256"), "native-assets manifest")
    require_sha(assets.get("noticesSha256"), "Flutter notices")
    if value.get("flutterRuntime") != {
        "engineSha256": _AUDITOR.FLUTTER_ENGINE_SHA256,
        "icuSha256": _AUDITOR.FLUTTER_ICU_SHA256,
    }:
        raise LinuxReferenceAppGateError("Linux application audit Flutter runtime changed")
    hook = value.get("hook")
    if not isinstance(hook, dict) or set(hook) != {
        "invocation",
        "inputSha256",
        "outputSha256",
        "compilerSha256",
    } or not isinstance(hook.get("invocation"), str) or HOOK_INVOCATION.fullmatch(
        hook["invocation"]
    ) is None:
        raise LinuxReferenceAppGateError("Linux application audit hook changed")
    require_sha(hook.get("inputSha256"), "hook input")
    require_sha(hook.get("outputSha256"), "hook output")
    compiler_hashes = hook.get("compilerSha256")
    if not isinstance(compiler_hashes, dict) or set(compiler_hashes) != {
        "ar",
        "cc",
        "ld",
    }:
        raise LinuxReferenceAppGateError("Linux application audit compiler hashes changed")
    for name, digest in compiler_hashes.items():
        require_sha(digest, f"hook compiler {name}")
    require_sha(value.get("shimBuildManifestSha256"), "shim build manifest")
    elf = value.get("elf")
    if not isinstance(elf, list) or len(elf) != len(_AUDITOR.ELF_PATHS):
        raise LinuxReferenceAppGateError("Linux application audit ELF inventory changed")
    seen_elf: set[str] = set()
    for record in elf:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "sha256",
            "sizeBytes",
            "soname",
            "needed",
            "runpath",
            "buildId",
            "versionMaxima",
        }:
            raise LinuxReferenceAppGateError("Linux application audit ELF record changed")
        path = record.get("path")
        if not isinstance(path, str) or path in seen_elf or path not in _AUDITOR.ELF_PATHS:
            raise LinuxReferenceAppGateError("Linux application audit ELF path changed")
        seen_elf.add(path)
        require_sha(record.get("sha256"), f"ELF {path}")
        size = record.get("sizeBytes")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise LinuxReferenceAppGateError("Linux application audit ELF size changed")
        if record.get("soname") != _AUDITOR.EXPECTED_SONAMES[path]:
            raise LinuxReferenceAppGateError("Linux application audit ELF SONAME changed")
        if record.get("needed") != list(_AUDITOR.EXPECTED_NEEDED[path]):
            raise LinuxReferenceAppGateError("Linux application audit ELF dependencies changed")
        if record.get("runpath") != list(_AUDITOR.EXPECTED_RUNPATHS[path]):
            raise LinuxReferenceAppGateError("Linux application audit ELF RUNPATH changed")
        build_id = record.get("buildId")
        if not isinstance(build_id, str) or _AUDITOR.BUILD_ID.fullmatch(build_id) is None:
            raise LinuxReferenceAppGateError("Linux application audit ELF build ID changed")
        maxima = record.get("versionMaxima")
        if not isinstance(maxima, dict) or set(maxima) != set(_AUDITOR.VERSION_MAXIMA):
            raise LinuxReferenceAppGateError("Linux application audit ELF versions changed")
        for maximum in maxima.values():
            if maximum is not None and (
                not isinstance(maximum, str)
                or re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", maximum) is None
            ):
                raise LinuxReferenceAppGateError("Linux application audit ELF version changed")
    if seen_elf != set(_AUDITOR.ELF_PATHS):
        raise LinuxReferenceAppGateError("Linux application audit ELF set changed")
    if value.get("hardening") != {
        "relro": "full",
        "bindNow": True,
        "nxStack": True,
        "buildIds": "present-closed-per-elf-runner-sha1",
    }:
        raise LinuxReferenceAppGateError("Linux application audit hardening changed")
    if value.get("claimBoundary") != (
        "Closed installed bytes, hook provenance, ELF metadata, symbol/version floors, "
        "and hardening only; target-host execution is required separately."
    ):
        raise LinuxReferenceAppGateError("Linux application audit claim boundary changed")
    return value


def _load_frozen_linux_auditor(repository: Path) -> Any:
    auditor_path = _regular_file(
        repository / "tool/ci/audit_linux_application.py",
        "frozen Linux application auditor",
        maximum=_SOURCE_MANIFEST.MAX_FILE_BYTES,
    )
    return _load_module(
        "_fonix_linux_gate_frozen_auditor",
        auditor_path,
    )


def _run_linux_application_audit(
    *,
    repository: Path,
    application: Path,
    provenance: HookProvenance,
    readelf: Path,
    clang: Path,
    archiver: Path,
    linker: Path,
    tree: TreeIdentity,
) -> dict[str, object]:
    auditor = _load_frozen_linux_auditor(repository)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        raw_report = auditor.audit_application(
            repository=repository,
            application=application,
            hook_input=provenance.input_path,
            reference_shim=provenance.shim,
            reference_runtime=provenance.runtime,
            reference_provider=provenance.provider,
            staging_directory=provenance.staging,
            readelf=readelf,
            expected_cc=clang,
            expected_ar=archiver,
            expected_ld=linker,
        )
        serialized = (
            json.dumps(raw_report, sort_keys=True, separators=(",", ":")) + "\n"
        )
    except (auditor.LinuxApplicationAuditError, OSError, ValueError) as error:
        raise LinuxReferenceAppGateError(
            f"independent Linux application audit failed: {error}"
        ) from error
    except (KeyError, TypeError, IndexError) as error:
        raise LinuxReferenceAppGateError(
            "independent Linux application audit returned malformed closed-contract input"
        ) from error
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
    report = _strict_json(
        serialized,
        "Linux application audit report",
        maximum=MAX_COMMAND_OUTPUT_BYTES,
    )
    return _validate_audit_report(report, tree)


def _parse_reference_receipt(stdout: str) -> dict[str, object]:
    encoded = stdout.encode("utf-8")
    if len(encoded) > MAX_REFERENCE_OUTPUT_BYTES or "\x00" in stdout:
        raise LinuxReferenceAppGateError("reference stdout is outside its bound")
    receipts = [
        line[len(REFERENCE_RECEIPT_PREFIX) :]
        for line in stdout.splitlines()
        if line.startswith(REFERENCE_RECEIPT_PREFIX)
    ]
    if len(receipts) != 1:
        raise LinuxReferenceAppGateError("reference application must emit exactly one receipt")
    value = _strict_json(receipts[0], "Linux reference receipt", maximum=16 * 1024)
    if not isinstance(value, dict) or set(value) != set(EXPECTED_REFERENCE_RECEIPT):
        raise LinuxReferenceAppGateError("Linux reference receipt field set changed")
    for key, expected in EXPECTED_REFERENCE_RECEIPT.items():
        actual = value[key]
        if type(actual) is not type(expected) or actual != expected:
            raise LinuxReferenceAppGateError(f"Linux reference receipt field {key!r} changed")
    return {key: value[key] for key in EXPECTED_REFERENCE_RECEIPT}


def _private_environment(root: Path) -> tuple[dict[str, str], Mapping[str, Path]]:
    root.mkdir(mode=0o700)
    directories = {
        "home": root / "home",
        "tmp": root / "tmp",
        "config": root / "xdg-config",
        "cache": root / "xdg-cache",
        "data": root / "xdg-data",
        "state": root / "xdg-state",
        "runtime": root / "xdg-runtime",
    }
    for directory in directories.values():
        directory.mkdir(mode=0o700)
    # env -i semantics: no caller-controlled DBus, GTK/GDK, dynamic-loader,
    # allocator, Dart/Flutter, instrumentation, or locale variable survives.
    environment = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "HOME": str(directories["home"]),
        "TMPDIR": str(directories["tmp"]),
        "TMP": str(directories["tmp"]),
        "TEMP": str(directories["tmp"]),
        "XDG_CONFIG_HOME": str(directories["config"]),
        "XDG_CACHE_HOME": str(directories["cache"]),
        "XDG_DATA_HOME": str(directories["data"]),
        "XDG_STATE_HOME": str(directories["state"]),
        "XDG_RUNTIME_DIR": str(directories["runtime"]),
        "FONIX_REFERENCE_SMOKE": "1",
        "GSETTINGS_BACKEND": "memory",
        "MESA_GLSL_CACHE_DISABLE": "true",
        "MESA_SHADER_CACHE_DISABLE": "true",
        "NO_AT_BRIDGE": "1",
    }
    return environment, directories


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if process.poll() is None:
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.poll() is None:
        process.wait(timeout=5)


def _execute_group(
    command: Sequence[str],
    *,
    cwd: Path | None,
    environment: Mapping[str, str] | None,
    timeout_seconds: int,
    maximum_output: int,
    operation: str,
) -> tuple[str, str]:
    if timeout_seconds <= 0 or timeout_seconds > COMMAND_TIMEOUT_SECONDS:
        raise LinuxReferenceAppGateError(f"{operation} timeout is outside its bound")
    if maximum_output <= 0 or maximum_output > MAX_COMMAND_OUTPUT_BYTES:
        raise LinuxReferenceAppGateError(f"{operation} output bound is invalid")
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(_probe_environment() if environment is None else environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise LinuxReferenceAppGateError(f"could not launch {operation} process group") from error
    assert process.stdout is not None and process.stderr is not None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow: list[str] = []
    lock = threading.Lock()

    def drain(name: str, stream: Any) -> None:
        try:
            while chunk := stream.read(8192):
                with lock:
                    remaining = maximum_output - len(buffers[name])
                    buffers[name].extend(chunk[: max(0, remaining)])
                    if len(chunk) > remaining and not overflow:
                        overflow.append(name)
                        _terminate_group(process)
        finally:
            stream.close()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_group(process)
        return_code = process.wait(timeout=5)
    for reader in readers:
        reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        _terminate_group(process)
        for reader in readers:
            reader.join(timeout=5)
        if any(reader.is_alive() for reader in readers):
            raise LinuxReferenceAppGateError(f"{operation} output readers did not settle")
    deadline = time.monotonic() + PROCESS_SETTLEMENT_SECONDS
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        except PermissionError as error:
            raise LinuxReferenceAppGateError(f"cannot verify {operation} process-group settlement") from error
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _terminate_group(process)
            raise LinuxReferenceAppGateError(f"{operation} process group left residual processes")
        time.sleep(0.05)
    if timed_out:
        raise LinuxReferenceAppGateError(f"{operation} timed out")
    if overflow:
        raise LinuxReferenceAppGateError(f"{operation} output exceeded its bound")
    try:
        stdout = bytes(buffers["stdout"]).decode("utf-8")
        stderr = bytes(buffers["stderr"]).decode("utf-8")
    except UnicodeDecodeError as error:
        raise LinuxReferenceAppGateError(f"{operation} output is not UTF-8") from error
    if return_code != 0:
        raise LinuxReferenceAppGateError(
            f"{operation} failed with exit code {return_code}: "
            f"{_COMMON._diagnostic(stderr)}"
        )
    return stdout, stderr


def _run_process_group(
    command: Sequence[str], *, cwd: Path, environment: Mapping[str, str]
) -> tuple[str, str]:
    return _execute_group(
        command,
        cwd=cwd,
        environment=environment,
        timeout_seconds=REFERENCE_TIMEOUT_SECONDS,
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        operation="reference application",
    )


def _assert_private_profile_empty(
    private_root: Path,
    directories: Mapping[str, Path],
    launch_cwd: Path,
) -> None:
    private_root = _directory(private_root, "private profile root")
    launch_cwd = _directory(launch_cwd, "unrelated launch directory")
    if stat.S_IMODE(private_root.lstat().st_mode) != 0o700:
        raise LinuxReferenceAppGateError("private profile root mode changed")
    if stat.S_IMODE(launch_cwd.lstat().st_mode) != 0o700:
        raise LinuxReferenceAppGateError("unrelated launch directory mode changed")
    expected_children = {path.name for path in directories.values()}
    actual_children = {path.name for path in private_root.iterdir()}
    if actual_children != expected_children:
        raise LinuxReferenceAppGateError("private profile contains residual top-level state")
    seen = 0
    for label, root in {**directories, "launch": launch_cwd}.items():
        root = _directory(root, f"private {label} directory")
        if stat.S_IMODE(root.lstat().st_mode) != 0o700:
            raise LinuxReferenceAppGateError(f"private {label} directory mode changed")
        for current_raw, directory_names, file_names in os.walk(
            root, followlinks=False
        ):
            current = Path(current_raw)
            for name in directory_names + file_names:
                seen += 1
                if seen > MAX_PRIVATE_ENTRIES:
                    raise LinuxReferenceAppGateError(
                        "private profile exceeds its residual bound"
                    )
                candidate = current / name
                if candidate.is_symlink():
                    raise LinuxReferenceAppGateError(
                        "private profile contains a residual link"
                    )
                raise LinuxReferenceAppGateError(
                    f"reference application left residual private-profile state in {label}"
                )


def _read_xvfb_display(
    descriptor: int, process: subprocess.Popen[bytes]
) -> str:
    deadline = time.monotonic() + 10
    value = bytearray()
    while b"\n" not in value:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LinuxReferenceAppGateError("Xvfb display allocation timed out")
        if process.poll() is not None:
            raise LinuxReferenceAppGateError("Xvfb exited before allocating a display")
        ready, _, _ = select.select((descriptor,), (), (), remaining)
        if not ready:
            raise LinuxReferenceAppGateError("Xvfb display allocation timed out")
        chunk = os.read(descriptor, 16)
        if not chunk:
            raise LinuxReferenceAppGateError("Xvfb closed its display descriptor")
        value.extend(chunk)
        if len(value) > 8:
            raise LinuxReferenceAppGateError("Xvfb display number exceeds its bound")
    if value.count(b"\n") != 1 or not value.endswith(b"\n"):
        raise LinuxReferenceAppGateError("Xvfb emitted an invalid display number")
    try:
        number_text = value[:-1].decode("ascii")
    except UnicodeDecodeError as error:
        raise LinuxReferenceAppGateError("Xvfb display number is not ASCII") from error
    if re.fullmatch(r"(?:0|[1-9][0-9]{0,4})", number_text) is None:
        raise LinuxReferenceAppGateError("Xvfb emitted an invalid display number")
    number = int(number_text)
    if number > 65535:
        raise LinuxReferenceAppGateError("Xvfb display number exceeds its bound")
    display = f":{number}"
    if DISPLAY_TOKEN.fullmatch(display) is None:
        raise LinuxReferenceAppGateError("Xvfb emitted an invalid display token")
    return display


def _launch_reference(
    *, executable: Path, xvfb: Path, work_root: Path
) -> dict[str, object]:
    launch_cwd = work_root / "unrelated-launch-cwd"
    launch_cwd.mkdir(mode=0o700)
    private_root = work_root / "private-profile"
    environment, directories = _private_environment(private_root)
    executable = _regular_file(executable, "reference executable")
    xvfb = _executable(xvfb, "Xvfb executable")
    display_read, display_write = os.pipe()
    try:
        process = subprocess.Popen(
            (
                str(xvfb),
                "-displayfd",
                str(display_write),
                "-screen",
                "0",
                "1280x720x24",
                "-nolisten",
                "tcp",
            ),
            cwd=launch_cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(display_write,),
            start_new_session=True,
        )
    except OSError as error:
        os.close(display_read)
        os.close(display_write)
        raise LinuxReferenceAppGateError("could not launch verified Xvfb") from error
    os.close(display_write)
    assert process.stdout is not None and process.stderr is not None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow: list[str] = []
    lock = threading.Lock()

    def drain(name: str, stream: Any) -> None:
        try:
            while chunk := stream.read(8192):
                with lock:
                    remaining = MAX_REFERENCE_OUTPUT_BYTES - len(buffers[name])
                    buffers[name].extend(chunk[: max(0, remaining)])
                    if len(chunk) > remaining and not overflow:
                        overflow.append(name)
                        _terminate_group(process)
        finally:
            stream.close()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    display: str | None = None
    launch_error: BaseException | None = None
    try:
        display = _read_xvfb_display(display_read, process)
        environment = {**environment, "DISPLAY": display}
        stdout, stderr = _run_process_group(
            (str(executable),), cwd=launch_cwd, environment=environment
        )
        if process.poll() is not None:
            raise LinuxReferenceAppGateError("Xvfb exited during reference execution")
    except BaseException as error:
        launch_error = error
        stdout = ""
        stderr = ""
    finally:
        os.close(display_read)
        _terminate_group(process)
        for reader in readers:
            reader.join(timeout=5)
    if any(reader.is_alive() for reader in readers):
        raise LinuxReferenceAppGateError("Xvfb output readers did not settle") from launch_error
    if overflow:
        raise LinuxReferenceAppGateError("Xvfb output exceeded its bound") from launch_error
    if buffers["stdout"] or buffers["stderr"]:
        raise LinuxReferenceAppGateError("Xvfb emitted unexpected diagnostics") from launch_error
    if display is not None:
        number = display[1:]
        for residual in (Path(f"/tmp/.X{number}-lock"), Path(f"/tmp/.X11-unix/X{number}")):
            if residual.exists() or residual.is_symlink():
                raise LinuxReferenceAppGateError("Xvfb left residual display state") from launch_error
    if launch_error is not None:
        raise launch_error
    if stderr:
        raise LinuxReferenceAppGateError("reference application emitted unexpected stderr")
    receipt = _parse_reference_receipt(stdout)
    _assert_private_profile_empty(private_root, directories, launch_cwd)
    return receipt


def run_gate(
    *,
    repository: Path,
    flutter: Path,
    artifact_cache: Path,
    pub_cache: Path,
    work_directory: Path,
    clang: Path,
    clangxx: Path,
    archiver: Path,
    linker: Path,
    cmake: Path,
    ninja: Path,
    readelf: Path,
    pkg_config: Path,
    dpkg_query: Path,
    xvfb: Path,
) -> dict[str, object]:
    # Host refusal happens before any gate-owned directory or external build is
    # created, so foreign-host static use cannot be mistaken for target evidence.
    _verify_exact_host()
    repository = _directory(repository.resolve(strict=True), "Fonix repository")
    artifact_cache = _directory(artifact_cache.resolve(strict=True), "artifact cache")
    if not work_directory.is_absolute():
        raise LinuxReferenceAppGateError("--work-dir must be absolute")
    work_parent = _directory(work_directory.parent.resolve(strict=True), "work parent")
    work_directory = work_parent / work_directory.name
    if work_directory.exists() or work_directory.is_symlink():
        raise LinuxReferenceAppGateError("--work-dir must not already exist")
    flutter = _executable(flutter, "Flutter executable")
    clang = _executable(clang, "Clang executable")
    clangxx = _executable(clangxx, "Clang++ executable")
    archiver = _executable(archiver, "LLVM archiver executable")
    linker = _executable(linker, "LLVM linker executable")
    cmake = _executable(cmake, "CMake executable")
    ninja = _executable(ninja, "Ninja executable")
    readelf = _executable(readelf, "GNU readelf executable")
    pkg_config = _executable(pkg_config, "pkg-config executable")
    dpkg_query = _executable(dpkg_query, "dpkg-query executable")
    xvfb = _executable(xvfb, "Xvfb executable")
    pub_cache = _directory(pub_cache.resolve(strict=True), "offline Dart pub cache")
    dart = _executable(
        flutter.parent / "cache/dart-sdk/bin/dart", "Flutter-bundled Dart executable"
    )
    work_directory.mkdir(mode=0o700)
    build_home = work_directory / "build-home"
    build_temporary = work_directory / "build-tmp"
    for private_directory in (
        build_home,
        build_temporary,
    ):
        private_directory.mkdir(mode=0o700)
    tool_bin = _prepare_tool_bin(
        work_directory / "tool-bin",
        {
            "clang": clang,
            "clang++": clangxx,
            "llvm-ar": archiver,
            "llvm-ar-10": archiver,
            "ld.lld": linker,
            "cmake": cmake,
            "ninja": ninja,
            "readelf": readelf,
            "pkg-config": pkg_config,
            "dpkg-query": dpkg_query,
            "Xvfb": xvfb,
        },
    )
    environment = _build_environment(
        home=build_home,
        temporary=build_temporary,
        pub_cache=pub_cache,
        tool_bin=tool_bin,
        clang=tool_bin / "clang",
        clangxx=tool_bin / "clang++",
        archiver=tool_bin / "llvm-ar",
        linker=tool_bin / "ld.lld",
    )
    toolchain = _verify_toolchain(
        flutter=flutter,
        clang=clang,
        clangxx=clangxx,
        archiver=archiver,
        linker=linker,
        cmake=cmake,
        ninja=ninja,
        readelf=readelf,
        pkg_config=pkg_config,
        dpkg_query=dpkg_query,
        xvfb=xvfb,
        tool_bin=tool_bin,
        environment=environment,
    )
    source_epoch, source_evidence = _snapshot_source_epoch(
        repository, work_directory / "source_epoch"
    )
    frozen_identity = _tree_identity(source_epoch, "frozen source epoch")
    archive = _load_pinned_archive(source_epoch / "native/versions.lock.yaml")
    application_work = work_directory / "application"
    summary = _COMMON._copy_example(source_epoch / "example", application_work)
    if summary.file_count <= 0 or summary.byte_count <= 0:
        raise LinuxReferenceAppGateError("external reference source copy was empty")
    _patch_linux_pubspec(application_work / "pubspec.yaml", source_epoch)
    _patch_linux_lockfile(application_work / "pubspec.lock", source_epoch)
    external_pubspec_sha256 = _sha256(application_work / "pubspec.yaml")
    external_lock_sha256 = _sha256(application_work / "pubspec.lock")
    _copy_archive(artifact_cache, application_work, archive)
    _run(
        (str(flutter), "pub", "get", "--offline", "--enforce-lockfile"),
        cwd=application_work,
        environment=environment,
        operation="offline Flutter pub get",
    )
    _require_dependency_epoch(
        application_work,
        pubspec_sha256=external_pubspec_sha256,
        lock_sha256=external_lock_sha256,
    )
    _run(
        (
            str(dart), "run", "fonix:fonix_prepare_flutter_assets",
            "--target-os", "linux", "--architecture", "x86_64", "--variant", "default",
            "--package-root", str(source_epoch),
            "--cache", str(application_work / ".fonix-artifact-cache"),
            "--output", str(application_work / "assets/fonix"),
        ),
        cwd=application_work,
        environment=environment,
        operation="Linux Fonix asset regeneration",
    )
    _run(
        (str(flutter), "analyze", "--no-pub"),
        cwd=application_work,
        environment=environment,
        operation="Linux reference analysis",
    )
    _run(
        (str(flutter), "test", "--no-pub"),
        cwd=application_work,
        environment=environment,
        operation="Linux reference tests",
    )
    _run(
        (str(flutter), "clean"),
        cwd=application_work,
        environment=environment,
        operation="clean Linux reference build",
    )
    _run(
        (str(flutter), "pub", "get", "--offline", "--enforce-lockfile"),
        cwd=application_work,
        environment=environment,
        operation="post-clean offline Flutter pub get",
    )
    _require_dependency_epoch(
        application_work,
        pubspec_sha256=external_pubspec_sha256,
        lock_sha256=external_lock_sha256,
    )
    _run(
        (str(flutter), "build", "linux", "--release", "--no-pub"),
        cwd=application_work,
        environment=environment,
        operation="Linux x86_64 Release build",
    )
    _require_dependency_epoch(
        application_work,
        pubspec_sha256=external_pubspec_sha256,
        lock_sha256=external_lock_sha256,
    )
    if _tree_identity(source_epoch, "frozen source epoch") != frozen_identity:
        raise LinuxReferenceAppGateError("frozen source epoch changed during the build")
    application = _directory(application_work / APPLICATION_RELATIVE, "final Linux application")
    executable = _regular_file(application / APPLICATION_EXECUTABLE, "final Linux executable")
    before = _tree_identity(application, "final Linux application")
    provenance = _discover_hook_provenance(application_work, application)
    audit = _run_linux_application_audit(
        repository=source_epoch,
        application=application,
        provenance=provenance,
        readelf=readelf,
        clang=clang,
        archiver=archiver,
        linker=linker,
        tree=before,
    )
    receipt = _launch_reference(
        executable=executable, xvfb=xvfb, work_root=work_directory
    )
    after = _tree_identity(application, "postrun Linux application")
    if after != before:
        raise LinuxReferenceAppGateError("final application tree changed during execution")
    if _tree_identity(source_epoch, "frozen source epoch") != frozen_identity:
        raise LinuxReferenceAppGateError("frozen source epoch changed during audit or execution")
    report: dict[str, object] = {
        "schemaVersion": 1,
        "result": "passed",
        "targetProfile": TARGET_PROFILE,
        "host": {
            "os": HOST_OS_ID,
            "version": HOST_OS_VERSION_ID,
            "architecture": HOST_ARCHITECTURE,
            "glibc": HOST_GLIBC_VERSION,
        },
        "toolchain": toolchain._asdict(),
        "sourceEpoch": source_evidence,
        "artifact": {"id": archive.artifact_id, "sha256": archive.sha256},
        "applicationTree": {
            "fileCount": before.file_count,
            "byteCount": before.byte_count,
            "sha256": before.tree_sha256,
        },
        "analysis": "passed",
        "tests": "passed",
        "releaseBuild": "passed",
        "finalApplicationAuditSha256": hashlib.sha256(
            json.dumps(audit, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "referenceReceipt": receipt,
        "processGroupSettled": True,
        "privateProfileResiduals": 0,
        "postrunTreeUnchanged": True,
        "claimBoundary": (
            "Exact Ubuntu 18.04/glibc 2.27 x86_64 host, pinned toolchain, closed final "
            "bytes, and one real CPU inference; no newer host or other Linux tuple."
        ),
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise LinuxReferenceAppGateError("Linux target-gate report exceeds its bound")
    for path in (repository, work_directory, application, artifact_cache):
        if str(path) in encoded:
            raise LinuxReferenceAppGateError("Linux target-gate report leaked a path")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--flutter", type=Path, required=True)
    parser.add_argument("--artifact-cache", type=Path, required=True)
    parser.add_argument("--pub-cache", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--clang", type=Path, default=Path("/usr/bin/clang-10"))
    parser.add_argument("--clangxx", type=Path, default=Path("/usr/bin/clang++-10"))
    parser.add_argument("--archiver", type=Path, default=Path("/usr/bin/llvm-ar-10"))
    parser.add_argument("--linker", type=Path, default=Path("/usr/bin/ld.lld-10"))
    parser.add_argument("--cmake", type=Path, default=Path("/usr/local/bin/cmake"))
    parser.add_argument("--ninja", type=Path, default=Path("/usr/local/bin/ninja"))
    parser.add_argument("--readelf", type=Path, default=Path("/usr/bin/readelf"))
    parser.add_argument("--pkg-config", type=Path, default=Path("/usr/bin/pkg-config"))
    parser.add_argument("--dpkg-query", type=Path, default=Path("/usr/bin/dpkg-query"))
    parser.add_argument("--xvfb", type=Path, default=Path("/usr/bin/Xvfb"))
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        _require_isolated_python_invocation()
        arguments = _parser().parse_args(argv)
        report = run_gate(
            repository=arguments.repository,
            flutter=arguments.flutter,
            artifact_cache=arguments.artifact_cache,
            pub_cache=arguments.pub_cache,
            work_directory=arguments.work_dir,
            clang=arguments.clang,
            clangxx=arguments.clangxx,
            archiver=arguments.archiver,
            linker=arguments.linker,
            cmake=arguments.cmake,
            ninja=arguments.ninja,
            readelf=arguments.readelf,
            pkg_config=arguments.pkg_config,
            dpkg_query=arguments.dpkg_query,
            xvfb=arguments.xvfb,
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (LinuxReferenceAppGateError, OSError, ValueError) as error:
        print(f"Linux reference-app gate error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
