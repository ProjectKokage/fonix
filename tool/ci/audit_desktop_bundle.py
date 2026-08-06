#!/usr/bin/env python3
"""Audit an extracted Fonix Linux/Windows bundled native-asset directory.

The directory may contain unrelated application files, but it must contain
exactly one adjacent shim, ONNX Runtime, and provider-shared library. The
resolver manifest and both upstream notices must be adjacent to that native
library set. Binary metadata is read with an explicit objdump-compatible tool.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any


MAX_MANIFEST_BYTES = 1024 * 1024
MAX_LOCK_BYTES = 1024 * 1024
MAX_NATIVE_LIBRARY_BYTES = 512 * 1024 * 1024
MAX_NOTICE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_ENTRIES = 8192
MAX_MANIFEST_FILES = 32
MAX_OBJDUMP_OUTPUT_BYTES = 1024 * 1024
OBJDUMP_TIMEOUT_SECONDS = 30
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ELF_FIELD = re.compile(r"^\s*(NEEDED|SONAME|RUNPATH|RPATH)\s+(\S+)\s*$", re.M)
PE_IMPORT = re.compile(r"^\s*DLL Name:\s*(\S+)\s*$", re.M)
FILE_FORMAT = re.compile(r"\bfile format\s+(\S+)", re.I)

MANIFEST_KEYS = frozenset(
    {
        "schema",
        "artifactId",
        "lock",
        "target",
        "source",
        "containers",
        "payloadFiles",
        "verifiedSymlinks",
        "notices",
        "archiveInspections",
        "claimBoundary",
    }
)
MANIFEST_LOCK_KEYS = frozenset(
    {"path", "sha256", "snapshotDate", "releaseState"}
)
MANIFEST_SOURCE_KEYS = frozenset(
    {"url", "sourceRevision", "archive", "sha256", "sizeBytes"}
)
MANIFEST_TARGET_KEYS = frozenset(
    {"os", "architecture", "variant", "minimumOs", "flavor", "runtimeMode"}
)
MANIFEST_PAYLOAD_KEYS = frozenset(
    {"archivePath", "stagedPath", "sha256", "sizeBytes"}
)
MANIFEST_NOTICE_KEYS = frozenset(
    {
        "id",
        "containerDepth",
        "archivePath",
        "stagedPath",
        "sha256",
        "sizeBytes",
    }
)

LINUX_PROVIDER_NEEDED = frozenset(
    {"libstdc++.so.6", "libm.so.6", "libgcc_s.so.1", "libc.so.6"}
)
WINDOWS_RUNTIME_IMPORTS = frozenset(
    name.lower()
    for name in {
        "KERNEL32.dll",
        "ADVAPI32.dll",
        "MSVCP140.dll",
        "MSVCP140_1.dll",
        "api-ms-win-core-path-l1-1-0.dll",
        "dbghelp.dll",
        "SETUPAPI.dll",
        "dxgi.dll",
        "VCRUNTIME140_1.dll",
        "VCRUNTIME140.dll",
        "api-ms-win-crt-heap-l1-1-0.dll",
        "api-ms-win-crt-runtime-l1-1-0.dll",
        "api-ms-win-crt-stdio-l1-1-0.dll",
        "api-ms-win-crt-string-l1-1-0.dll",
        "api-ms-win-crt-time-l1-1-0.dll",
        "api-ms-win-crt-filesystem-l1-1-0.dll",
        "api-ms-win-crt-convert-l1-1-0.dll",
        "api-ms-win-crt-locale-l1-1-0.dll",
        "api-ms-win-crt-math-l1-1-0.dll",
    }
)
WINDOWS_PROVIDER_IMPORTS = frozenset(
    name.lower()
    for name in {
        "VCRUNTIME140.dll",
        "api-ms-win-crt-runtime-l1-1-0.dll",
        "KERNEL32.dll",
    }
)


class DesktopBundleAuditError(RuntimeError):
    """The extracted desktop native bundle violates its closed contract."""


@dataclass(frozen=True)
class TargetPolicy:
    os_name: str
    architecture: str
    object_format: str
    minimum_os: str
    shim: str
    runtime: str
    provider: str
    runtime_dependencies: frozenset[str]

    @property
    def payloads(self) -> frozenset[str]:
        return frozenset({self.runtime, self.provider})


POLICIES = {
    "linux-x64": TargetPolicy(
        os_name="linux",
        architecture="x86_64",
        object_format="elf64-x86-64",
        minimum_os="glibc-2.27",
        shim="libfonix_shim.so",
        runtime="libonnxruntime.so.1",
        provider="libonnxruntime_providers_shared.so",
        runtime_dependencies=frozenset(
            {
                "libdl.so.2",
                "librt.so.1",
                "libpthread.so.0",
                "libstdc++.so.6",
                "libm.so.6",
                "libgcc_s.so.1",
                "libc.so.6",
                "ld-linux-x86-64.so.2",
            }
        ),
    ),
    "linux-arm64": TargetPolicy(
        os_name="linux",
        architecture="arm64",
        object_format="elf64-littleaarch64",
        minimum_os="glibc-2.27",
        shim="libfonix_shim.so",
        runtime="libonnxruntime.so.1",
        provider="libonnxruntime_providers_shared.so",
        runtime_dependencies=frozenset(
            {
                "libdl.so.2",
                "librt.so.1",
                "libpthread.so.0",
                "libstdc++.so.6",
                "libm.so.6",
                "libgcc_s.so.1",
                "libc.so.6",
                "ld-linux-aarch64.so.1",
            }
        ),
    ),
    "windows-x64": TargetPolicy(
        os_name="windows",
        architecture="x64",
        object_format="coff-x86-64",
        minimum_os="10.0",
        shim="fonix_shim.dll",
        runtime="onnxruntime.dll",
        provider="onnxruntime_providers_shared.dll",
        runtime_dependencies=WINDOWS_RUNTIME_IMPORTS,
    ),
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DesktopBundleAuditError(f"JSON input duplicates key {key!r}")
        result[key] = value
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(root: Path) -> list[Path]:
    result: list[Path] = []
    entry_count = 0
    for current_root, directory_names, file_names in os.walk(
        root, followlinks=False
    ):
        current = Path(current_root)
        entry_count += len(directory_names) + len(file_names)
        if entry_count > MAX_BUNDLE_ENTRIES:
            raise DesktopBundleAuditError(
                f"bundle exceeds the {MAX_BUNDLE_ENTRIES}-entry bound"
            )
        for name in list(directory_names):
            candidate = current / name
            if candidate.is_symlink():
                raise DesktopBundleAuditError(
                    f"bundle contains symbolic-link directory {candidate}"
                )
        for name in file_names:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_file():
                raise DesktopBundleAuditError(
                    f"bundle contains non-regular file {candidate}"
                )
            result.append(candidate)
    return sorted(result)


def _single_named(files: list[Path], name: str, *, windows: bool) -> Path:
    matches = [
        candidate
        for candidate in files
        if (
            candidate.name.lower() == name.lower()
            if windows
            else candidate.name == name
        )
    ]
    if len(matches) != 1:
        raise DesktopBundleAuditError(
            f"bundle must contain exactly one {name}; found {len(matches)}"
        )
    return matches[0]


def _read_json(path: Path, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DesktopBundleAuditError(f"{label} must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > maximum_bytes:
        raise DesktopBundleAuditError(f"{label} size is outside the accepted bound")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DesktopBundleAuditError(f"{label} is invalid: {error}") from error
    if not isinstance(value, dict):
        raise DesktopBundleAuditError(f"{label} root must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if frozenset(value) != expected:
        raise DesktopBundleAuditError(f"{label} has an unexpected field set")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DesktopBundleAuditError(f"{label} must be an object")
    return value


def _list(value: Any, label: str, *, maximum: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        raise DesktopBundleAuditError(f"{label} must be an array")
    if maximum is not None and len(value) > maximum:
        raise DesktopBundleAuditError(f"{label} exceeds its item bound")
    return value


def _string(value: Any, label: str, *, allow_none: bool = False) -> str | None:
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise DesktopBundleAuditError(f"{label} must be a bounded non-empty string")
    return value


def _integer(value: Any, label: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DesktopBundleAuditError(f"{label} must be an integer")
    if value <= 0 or value > maximum:
        raise DesktopBundleAuditError(f"{label} is outside the accepted bound")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise DesktopBundleAuditError(f"{label} must be a lowercase SHA-256")
    return value


def _safe_posix_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise DesktopBundleAuditError(f"{label} must be a bounded relative path")
    if (
        value.startswith("/")
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or PurePosixPath(value).as_posix() != value
        or any(
            ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F
            for character in value
        )
    ):
        raise DesktopBundleAuditError(f"{label} is not a safe canonical path")
    return value


def _bounded_regular_file(path: Path, *, label: str, maximum: int) -> int:
    if path.is_symlink() or not path.is_file():
        raise DesktopBundleAuditError(f"{label} must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > maximum:
        raise DesktopBundleAuditError(f"{label} size is outside the accepted bound")
    return size


def _manifest_file_entries(
    value: Any,
    kind: str,
    *,
    keys: frozenset[str],
    maximum_size: int,
) -> dict[str, dict[str, Any]]:
    entries = _list(value, f"manifest {kind}", maximum=MAX_MANIFEST_FILES)
    result: dict[str, dict[str, Any]] = {}
    archive_paths: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _object(raw_entry, f"manifest {kind}[{index}]")
        _exact_keys(entry, keys, f"manifest {kind}[{index}]")
        archive_path = _safe_posix_path(
            entry["archivePath"], f"manifest {kind}[{index}].archivePath"
        )
        staged_path = _safe_posix_path(
            entry["stagedPath"], f"manifest {kind}[{index}].stagedPath"
        )
        _digest(entry["sha256"], f"manifest {kind}[{index}].sha256")
        _integer(
            entry["sizeBytes"],
            f"manifest {kind}[{index}].sizeBytes",
            maximum=maximum_size,
        )
        if archive_path in archive_paths:
            raise DesktopBundleAuditError(
                f"manifest duplicates {kind} archive path {archive_path}"
            )
        archive_paths.add(archive_path)
        if staged_path in result:
            raise DesktopBundleAuditError(
                f"manifest duplicates {kind} staged path {staged_path}"
            )
        result[staged_path] = entry
    return result


def _trusted_artifact(
    trusted_lock: dict[str, Any], artifact_id: str
) -> dict[str, Any]:
    if trusted_lock.get("schema") != 2:
        raise DesktopBundleAuditError("trusted lock schema must be 2")
    _string(trusted_lock.get("snapshot_date"), "trusted lock snapshot_date")
    _string(trusted_lock.get("release_state"), "trusted lock release_state")
    artifacts = _list(
        trusted_lock.get("artifacts"), "trusted lock artifacts", maximum=256
    )
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_artifact in enumerate(artifacts):
        artifact = _object(raw_artifact, f"trusted lock artifacts[{index}]")
        current_id = _string(
            artifact.get("id"), f"trusted lock artifacts[{index}].id"
        )
        assert current_id is not None
        if current_id in seen_ids:
            raise DesktopBundleAuditError(
                f"trusted lock duplicates artifact ID {current_id}"
            )
        seen_ids.add(current_id)
        if current_id == artifact_id:
            matches.append(artifact)
    if len(matches) != 1:
        raise DesktopBundleAuditError(
            f"manifest artifact ID has no unique trusted lock entry: {artifact_id}"
        )
    return matches[0]


def _locked_file_entries(
    value: Any,
    kind: str,
    *,
    notice: bool,
    maximum_size: int,
) -> dict[str, dict[str, Any]]:
    entries = _list(value, f"trusted lock {kind}", maximum=MAX_MANIFEST_FILES)
    result: dict[str, dict[str, Any]] = {}
    archive_paths: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _object(raw_entry, f"trusted lock {kind}[{index}]")
        archive_path = _safe_posix_path(
            entry.get("path"), f"trusted lock {kind}[{index}].path"
        )
        staged_path = _safe_posix_path(
            entry.get("staged_path"),
            f"trusted lock {kind}[{index}].staged_path",
        )
        digest = _digest(
            entry.get("sha256"), f"trusted lock {kind}[{index}].sha256"
        )
        size = _integer(
            entry.get("size_bytes"),
            f"trusted lock {kind}[{index}].size_bytes",
            maximum=maximum_size,
        )
        if archive_path in archive_paths or staged_path in result:
            raise DesktopBundleAuditError(f"trusted lock duplicates a {kind} path")
        archive_paths.add(archive_path)
        transformed: dict[str, Any] = {
            "archivePath": archive_path,
            "stagedPath": staged_path,
            "sha256": digest,
            "sizeBytes": size,
        }
        if notice:
            transformed = {
                "id": _string(
                    entry.get("id"), f"trusted lock {kind}[{index}].id"
                ),
                "containerDepth": entry.get("container_depth"),
                **transformed,
            }
            if (
                isinstance(transformed["containerDepth"], bool)
                or not isinstance(transformed["containerDepth"], int)
                or transformed["containerDepth"] < 0
                or transformed["containerDepth"] > 8
            ):
                raise DesktopBundleAuditError(
                    f"trusted lock {kind}[{index}].container_depth is invalid"
                )
        result[staged_path] = transformed
    return result


def _validate_manifest(
    manifest: dict[str, Any],
    native_directory: Path,
    policy: TargetPolicy,
    *,
    trusted_lock: dict[str, Any],
    trusted_lock_sha256: str,
) -> None:
    _exact_keys(manifest, MANIFEST_KEYS, "manifest")
    if manifest.get("schema") != 2:
        raise DesktopBundleAuditError("manifest schema/artifact identity is invalid")
    artifact_id = _string(manifest.get("artifactId"), "manifest artifactId")
    assert artifact_id is not None
    artifact = _trusted_artifact(trusted_lock, artifact_id)

    lock = _object(manifest.get("lock"), "manifest lock")
    source = _object(manifest.get("source"), "manifest source")
    target = _object(manifest.get("target"), "manifest target")
    _exact_keys(lock, MANIFEST_LOCK_KEYS, "manifest lock")
    _exact_keys(source, MANIFEST_SOURCE_KEYS, "manifest source")
    _exact_keys(target, MANIFEST_TARGET_KEYS, "manifest target")
    if (
        lock.get("path") != "native/versions.lock.yaml"
        or _digest(lock.get("sha256"), "manifest lock sha256")
        != trusted_lock_sha256
        or lock.get("snapshotDate") != trusted_lock.get("snapshot_date")
        or lock.get("releaseState") != trusted_lock.get("release_state")
    ):
        raise DesktopBundleAuditError("manifest lock identity is invalid")

    expected_target = {
        "os": policy.os_name,
        "architecture": policy.architecture,
        "variant": "default",
        "minimumOs": policy.minimum_os,
        "flavor": "cpu",
        "runtimeMode": "bundled",
    }
    locked_target = _object(artifact.get("target"), "trusted lock artifact target")
    expected_locked_target = {
        "os": policy.os_name,
        "architecture": policy.architecture,
        "variant": "default",
        "min_os": policy.minimum_os,
    }
    if locked_target != expected_locked_target:
        raise DesktopBundleAuditError("trusted lock target does not match policy")
    if (
        target != expected_target
        or artifact.get("flavor") != "cpu"
        or artifact.get("runtime_mode") != "bundled"
    ):
        raise DesktopBundleAuditError("manifest target does not match trusted policy")

    locked_source = _object(artifact.get("source"), "trusted lock artifact source")
    expected_source = {
        "url": locked_source.get("url"),
        "sourceRevision": locked_source.get("source_revision"),
        "archive": locked_source.get("archive"),
        "sha256": locked_source.get("sha256"),
        "sizeBytes": locked_source.get("size_bytes"),
    }
    _string(expected_source["url"], "trusted lock source url")
    _string(
        expected_source["sourceRevision"],
        "trusted lock source revision",
        allow_none=True,
    )
    if expected_source["archive"] not in {"zip", "tgz", "tar.gz"}:
        raise DesktopBundleAuditError("trusted lock source archive is invalid")
    _digest(expected_source["sha256"], "trusted lock source sha256")
    _integer(
        expected_source["sizeBytes"],
        "trusted lock source size_bytes",
        maximum=MAX_SOURCE_ARCHIVE_BYTES,
    )
    if source != expected_source:
        raise DesktopBundleAuditError("manifest source does not match trusted lock")

    payloads = _manifest_file_entries(
        manifest.get("payloadFiles"),
        "payload",
        keys=MANIFEST_PAYLOAD_KEYS,
        maximum_size=MAX_NATIVE_LIBRARY_BYTES,
    )
    if frozenset(payloads) != policy.payloads:
        raise DesktopBundleAuditError("manifest payload file set is not closed")
    notices = _manifest_file_entries(
        manifest.get("notices"),
        "notice",
        keys=MANIFEST_NOTICE_KEYS,
        maximum_size=MAX_NOTICE_BYTES,
    )
    expected_notices = {
        "notices/LICENSE": "MIT",
        "notices/ThirdPartyNotices.txt": "ThirdPartyNotices",
    }
    if set(notices) != set(expected_notices):
        raise DesktopBundleAuditError("manifest notice file set is not closed")

    locked_payloads = _locked_file_entries(
        artifact.get("expected_files"),
        "expected_files",
        notice=False,
        maximum_size=MAX_NATIVE_LIBRARY_BYTES,
    )
    locked_notices = _locked_file_entries(
        artifact.get("notices"),
        "notices",
        notice=True,
        maximum_size=MAX_NOTICE_BYTES,
    )
    if payloads != locked_payloads or notices != locked_notices:
        raise DesktopBundleAuditError(
            "manifest payload/notice identity does not match trusted lock"
        )

    if not isinstance(manifest.get("containers"), list) or not isinstance(
        manifest.get("verifiedSymlinks"), list
    ):
        raise DesktopBundleAuditError("manifest container/symlink data is invalid")
    if not isinstance(manifest.get("archiveInspections"), list):
        raise DesktopBundleAuditError("manifest archive inspections are invalid")
    _string(manifest.get("claimBoundary"), "manifest claimBoundary")

    for staged_path, entry in {**payloads, **notices}.items():
        candidate = native_directory.joinpath(*staged_path.split("/"))
        maximum = (
            MAX_NOTICE_BYTES
            if staged_path in notices
            else MAX_NATIVE_LIBRARY_BYTES
        )
        actual_size = _bounded_regular_file(
            candidate, label=f"manifest file {staged_path}", maximum=maximum
        )
        if entry.get("sizeBytes") != actual_size:
            raise DesktopBundleAuditError(f"manifest size drift: {staged_path}")
        if _sha256(candidate) != entry["sha256"]:
            raise DesktopBundleAuditError(f"manifest digest drift: {staged_path}")
        if staged_path in expected_notices:
            if entry.get("id") != expected_notices[staged_path]:
                raise DesktopBundleAuditError(
                    f"manifest notice ID drift: {staged_path}"
                )
            if entry.get("containerDepth") != 0:
                raise DesktopBundleAuditError(
                    f"manifest notice container depth drift: {staged_path}"
                )


def _objdump(executable: Path, binary: Path) -> str:
    if executable.is_symlink() or not executable.is_file() or not os.access(
        executable, os.X_OK
    ):
        raise DesktopBundleAuditError("objdump must be a regular executable file")
    _bounded_regular_file(
        binary, label=f"native binary {binary}", maximum=MAX_NATIVE_LIBRARY_BYTES
    )
    try:
        result = subprocess.run(
            [str(executable), "-p", str(binary)],
            check=True,
            capture_output=True,
            timeout=OBJDUMP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise DesktopBundleAuditError(f"objdump timed out for {binary}") from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise DesktopBundleAuditError(f"objdump failed for {binary}") from error
    if len(result.stdout) + len(result.stderr) > MAX_OBJDUMP_OUTPUT_BYTES:
        raise DesktopBundleAuditError(f"objdump output is oversized for {binary}")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DesktopBundleAuditError(
            f"objdump output is not valid UTF-8 for {binary}"
        ) from error


def _elf_metadata(report: str) -> tuple[str, frozenset[str], str | None, str | None]:
    format_match = FILE_FORMAT.search(report)
    if format_match is None:
        raise DesktopBundleAuditError("objdump report has no object format")
    fields: dict[str, list[str]] = {}
    for name, value in ELF_FIELD.findall(report):
        fields.setdefault(name.upper(), []).append(value)
    for single in ("SONAME", "RUNPATH", "RPATH"):
        if len(fields.get(single, [])) > 1:
            raise DesktopBundleAuditError(f"objdump report duplicates {single}")
    needed = fields.get("NEEDED", [])
    if len(needed) != len(set(needed)):
        raise DesktopBundleAuditError("objdump report duplicates DT_NEEDED")
    if fields.get("RPATH"):
        raise DesktopBundleAuditError("desktop ELF uses DT_RPATH instead of RUNPATH")
    return (
        format_match.group(1),
        frozenset(needed),
        next(iter(fields.get("SONAME", [])), None),
        next(iter(fields.get("RUNPATH", [])), None),
    )


def _pe_metadata(report: str) -> tuple[str, frozenset[str]]:
    format_match = FILE_FORMAT.search(report)
    if format_match is None:
        raise DesktopBundleAuditError("objdump report has no object format")
    imports = [name.lower() for name in PE_IMPORT.findall(report)]
    if not imports or len(imports) != len(set(imports)):
        raise DesktopBundleAuditError("PE import report is empty or duplicated")
    return format_match.group(1), frozenset(imports)


def audit_bundle(
    bundle: Path, policy: TargetPolicy, objdump: Path, trusted_lock_path: Path
) -> dict[str, Any]:
    if not bundle.is_dir() or bundle.is_symlink():
        raise DesktopBundleAuditError("bundle must be a regular directory")
    trusted_lock = _read_json(
        trusted_lock_path, label="trusted lock", maximum_bytes=MAX_LOCK_BYTES
    )
    trusted_lock_sha256 = _sha256(trusted_lock_path)
    files = _regular_files(bundle)
    windows = policy.os_name == "windows"
    shim = _single_named(files, policy.shim, windows=windows)
    runtime = _single_named(files, policy.runtime, windows=windows)
    provider = _single_named(files, policy.provider, windows=windows)
    if len({shim.parent, runtime.parent, provider.parent}) != 1:
        raise DesktopBundleAuditError(
            "shim, runtime, and provider-shared library are not adjacent"
        )
    native_directory = runtime.parent
    manifest_path = native_directory / "fonix-native-artifact-manifest.json"
    license_path = native_directory / "notices" / "LICENSE"
    notices_path = native_directory / "notices" / "ThirdPartyNotices.txt"
    for required in (manifest_path, license_path, notices_path):
        if required.is_symlink() or not required.is_file():
            raise DesktopBundleAuditError(
                f"required bundle metadata is missing: {required}"
            )

    expected_ort_names = {policy.runtime.lower(), policy.provider.lower()}
    for candidate in files:
        lower = candidate.name.lower()
        if "onnxruntime" in lower and lower not in expected_ort_names:
            raise DesktopBundleAuditError(
                f"bundle contains an unexpected ONNX Runtime binary: {candidate}"
            )

    manifest = _read_json(
        manifest_path, label="manifest", maximum_bytes=MAX_MANIFEST_BYTES
    )
    _validate_manifest(
        manifest,
        native_directory,
        policy,
        trusted_lock=trusted_lock,
        trusted_lock_sha256=trusted_lock_sha256,
    )
    for role, candidate in {
        "shim": shim,
        "runtime": runtime,
        "provider": provider,
    }.items():
        _bounded_regular_file(
            candidate, label=f"{role} binary", maximum=MAX_NATIVE_LIBRARY_BYTES
        )
    _bounded_regular_file(
        license_path, label="license notice", maximum=MAX_NOTICE_BYTES
    )
    _bounded_regular_file(
        notices_path, label="third-party notices", maximum=MAX_NOTICE_BYTES
    )
    reports = {
        "shim": _objdump(objdump, shim),
        "runtime": _objdump(objdump, runtime),
        "provider": _objdump(objdump, provider),
    }
    if policy.os_name == "linux":
        metadata = {name: _elf_metadata(report) for name, report in reports.items()}
        for role, (object_format, _, _, _) in metadata.items():
            if object_format != policy.object_format:
                raise DesktopBundleAuditError(f"{role} has wrong ELF architecture")
        _, shim_needed, shim_soname, shim_runpath = metadata["shim"]
        expected_shim_needed = {"libc.so.6", "libdl.so.2"}
        if policy.architecture == "arm64":
            expected_shim_needed.add("ld-linux-aarch64.so.1")
        if (
            shim_needed != frozenset(expected_shim_needed)
            or shim_soname != policy.shim
            or shim_runpath != "$ORIGIN"
        ):
            raise DesktopBundleAuditError(
                "Linux shim dependency/SONAME/RUNPATH drift: "
                f"needed={sorted(shim_needed)!r}, soname={shim_soname!r}, "
                f"runpath={shim_runpath!r}"
            )
        _, runtime_needed, runtime_soname, runtime_runpath = metadata["runtime"]
        if (
            runtime_needed != policy.runtime_dependencies
            or runtime_soname != "libonnxruntime.so.1"
            or runtime_runpath != "$ORIGIN"
        ):
            raise DesktopBundleAuditError("Linux runtime metadata drift")
        _, provider_needed, provider_soname, provider_runpath = metadata["provider"]
        if (
            provider_needed != LINUX_PROVIDER_NEEDED
            or provider_soname != "libonnxruntime_providers_shared.so"
            or provider_runpath is not None
        ):
            raise DesktopBundleAuditError("Linux provider-shared metadata drift")
    else:
        metadata = {name: _pe_metadata(report) for name, report in reports.items()}
        for role, (object_format, _) in metadata.items():
            if object_format != policy.object_format:
                raise DesktopBundleAuditError(f"{role} has wrong PE architecture")
        shim_imports = metadata["shim"][1]
        if "kernel32.dll" not in shim_imports or any(
            "onnxruntime" in name for name in shim_imports
        ):
            raise DesktopBundleAuditError("Windows shim import contract drift")
        if metadata["runtime"][1] != WINDOWS_RUNTIME_IMPORTS:
            raise DesktopBundleAuditError("Windows runtime import contract drift")
        if metadata["provider"][1] != WINDOWS_PROVIDER_IMPORTS:
            raise DesktopBundleAuditError("Windows provider import contract drift")

    return {
        "schema": 1,
        "target": f"{policy.os_name}/{policy.architecture}",
        "artifactId": manifest["artifactId"],
        "nativeDirectory": str(native_directory),
        "files": {
            role: {
                "path": str(candidate),
                "sizeBytes": candidate.stat().st_size,
                "sha256": _sha256(candidate),
            }
            for role, candidate in {
                "shim": shim,
                "runtime": runtime,
                "providerShared": provider,
                "license": license_path,
                "thirdPartyNotices": notices_path,
                "manifest": manifest_path,
            }.items()
        },
        "claimBoundary": (
            "This audit proves extracted native bundle layout and static binary "
            "metadata. It does not prove target-host loading, inference, signing, "
            "or installer behavior."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--target", choices=sorted(POLICIES), required=True)
    parser.add_argument("--objdump", type=Path, required=True)
    parser.add_argument(
        "--lock",
        type=Path,
        required=True,
        help="Trusted native/versions.lock.yaml used to bind manifest identity.",
    )
    parser.add_argument("--json-out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = audit_bundle(
            arguments.bundle.absolute(),
            POLICIES[arguments.target],
            arguments.objdump.absolute(),
            arguments.lock.absolute(),
        )
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if arguments.json_out is not None:
            arguments.json_out.parent.mkdir(parents=True, exist_ok=True)
            arguments.json_out.write_text(encoded, encoding="utf-8")
    except (OSError, DesktopBundleAuditError) as error:
        print(f"Desktop bundle audit failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Desktop bundle audit passed for {report['target']} "
        f"({report['artifactId']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
