#!/usr/bin/env python3
"""Audit a final Flutter Apple application containing Fonix native assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import struct
import subprocess
import tempfile
from typing import Any, Sequence


_MANIFEST_NAME = "fonix-native-artifact-manifest.json"
_NOTICE_NAME = "ThirdPartyNotices.txt"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$")
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_MACHO_BYTES = 512 * 1024 * 1024
_CLAIM_BOUNDARY = (
    "Successful staging proves exact archive and member bytes only. It does not "
    "prove loading, linking, provider registration, inference, packaging, "
    "signing, or target-device support."
)
_PLATFORM_CONTRACTS = {
    "macos": {
        "targetOs": "macos",
        "targetVariant": "default",
        "runtimeMode": "bundled",
        "machoPlatform": 1,
    },
    "ios-device": {
        "targetOs": "ios",
        "targetVariant": "device",
        "runtimeMode": "linked",
        "machoPlatform": 2,
    },
    "ios-simulator": {
        "targetOs": "ios",
        "targetVariant": "simulator",
        "runtimeMode": "linked",
        "machoPlatform": 7,
    },
}
_PLATFORM_NAMES = {
    "macos": 1,
    "ios": 2,
    "iossimulator": 7,
    "ios-simulator": 7,
}
_MANIFEST_KEYS = {
    "schema",
    "artifactId",
    "claimBoundary",
    "lock",
    "target",
    "source",
    "containers",
    "payloadFiles",
    "notices",
    "verifiedSymlinks",
    "archiveInspections",
}
_BUILD_MANIFEST_KEYS = {
    "schemaVersion",
    "nativeIdentity",
    "shimAbiVersion",
    "requiredOrtApiVersion",
    "runtimeProfile",
    "androidRuntimeOwner",
    "allowedRuntimeSources",
    "buildId",
    "artifact",
}
_ARTIFACT_IDENTITY_KEYS = {
    "id",
    "lockSha256",
    "sourceSha256",
    "targetOs",
    "targetArchitecture",
    "targetVariant",
    "minimumOs",
    "flavor",
    "runtimeMode",
    "thirdPartyNoticesSha256",
    "providers",
}


class AppleApplicationAuditError(RuntimeError):
    """The final application violates the Fonix Apple packaging contract."""


def _regular_file(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise AppleApplicationAuditError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(mode):
        raise AppleApplicationAuditError(f"{label} is not a regular file: {path}")
    return path


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AppleApplicationAuditError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_manifest(path: Path) -> dict[str, Any]:
    _regular_file(path, "packaged Fonix manifest")
    data = path.read_bytes()
    if len(data) > _MAX_MANIFEST_BYTES:
        raise AppleApplicationAuditError("packaged Fonix manifest is oversized")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppleApplicationAuditError(
            "packaged Fonix manifest is not strict UTF-8 JSON"
        ) from error
    if not isinstance(value, dict):
        raise AppleApplicationAuditError("packaged Fonix manifest is not an object")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppleApplicationAuditError(f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AppleApplicationAuditError(f"{label} must be an array")
    return value


def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise AppleApplicationAuditError(
            f"{label} has a non-closed field set; missing={missing}, extra={extra}"
        )


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AppleApplicationAuditError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise AppleApplicationAuditError(f"{label} must be {qualifier}")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AppleApplicationAuditError(f"{label} must be a non-empty string")
    return value


def _token(value: Any, label: str) -> str:
    result = _string(value, label)
    if _TOKEN.fullmatch(result) is None:
        raise AppleApplicationAuditError(f"{label} is not a closed token")
    return result


def _digest(value: Any, label: str) -> str:
    result = _string(value, label)
    if _SHA256.fullmatch(result) is None:
        raise AppleApplicationAuditError(f"{label} is not lowercase SHA-256")
    return result


def _version(value: str, label: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value)
    if match is None:
        raise AppleApplicationAuditError(
            f"{label} is not strict major.minor[.patch]: {value}"
        )
    return tuple(int(match.group(index) or "0") for index in range(1, 4))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_root(application: Path, platform: str) -> Path:
    if platform == "macos":
        return (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix"
        )
    return application / "Frameworks/App.framework/flutter_assets/assets/fonix"


def _all_named_files(root: Path, name: str) -> list[Path]:
    matches: list[Path] = []
    for directory, directories, files in os.walk(root, followlinks=False):
        directories[:] = [
            entry
            for entry in directories
            if not (Path(directory) / entry).is_symlink()
        ]
        if name in files:
            matches.append(Path(directory) / name)
    return matches


def _platform_contract(platform: str) -> dict[str, Any]:
    contract = _PLATFORM_CONTRACTS.get(platform)
    if contract is None:
        raise AppleApplicationAuditError(
            "platform must be macos, ios-device, or ios-simulator"
        )
    return contract


def _validate_manifest_collections(manifest: dict[str, Any]) -> None:
    containers = _array(manifest.get("containers"), "manifest containers")
    for index, raw_entry in enumerate(containers):
        entry = _object(raw_entry, f"containers[{index}]")
        _require_keys(
            entry,
            {"archive", "depth", "path", "sha256", "sizeBytes"},
            f"containers[{index}]",
        )
        _token(entry.get("archive"), f"containers[{index}].archive")
        if _integer(entry.get("depth"), f"containers[{index}].depth", positive=True) != index + 1:
            raise AppleApplicationAuditError("container depths must be consecutive")
        _string(entry.get("path"), f"containers[{index}].path")
        _digest(entry.get("sha256"), f"containers[{index}].sha256")
        _integer(entry.get("sizeBytes"), f"containers[{index}].sizeBytes", positive=True)

    payloads = _array(manifest.get("payloadFiles"), "manifest payloadFiles")
    payload_paths: set[str] = set()
    for index, raw_entry in enumerate(payloads):
        entry = _object(raw_entry, f"payloadFiles[{index}]")
        _require_keys(
            entry,
            {"archivePath", "sha256", "sizeBytes", "stagedPath"},
            f"payloadFiles[{index}]",
        )
        _string(entry.get("archivePath"), f"payloadFiles[{index}].archivePath")
        _digest(entry.get("sha256"), f"payloadFiles[{index}].sha256")
        _integer(entry.get("sizeBytes"), f"payloadFiles[{index}].sizeBytes", positive=True)
        staged_path = _string(
            entry.get("stagedPath"), f"payloadFiles[{index}].stagedPath"
        )
        if staged_path in payload_paths:
            raise AppleApplicationAuditError("payloadFiles has duplicate staged paths")
        payload_paths.add(staged_path)

    notices = _array(manifest.get("notices"), "manifest notices")
    notice_paths: set[str] = set()
    for index, raw_entry in enumerate(notices):
        entry = _object(raw_entry, f"notices[{index}]")
        _require_keys(
            entry,
            {
                "archivePath",
                "containerDepth",
                "id",
                "sha256",
                "sizeBytes",
                "stagedPath",
            },
            f"notices[{index}]",
        )
        _string(entry.get("archivePath"), f"notices[{index}].archivePath")
        depth = _integer(entry.get("containerDepth"), f"notices[{index}].containerDepth")
        if depth > len(containers):
            raise AppleApplicationAuditError("notice container depth is out of range")
        _string(entry.get("id"), f"notices[{index}].id")
        _digest(entry.get("sha256"), f"notices[{index}].sha256")
        _integer(entry.get("sizeBytes"), f"notices[{index}].sizeBytes", positive=True)
        staged_path = _string(entry.get("stagedPath"), f"notices[{index}].stagedPath")
        if staged_path in notice_paths:
            raise AppleApplicationAuditError("notices has duplicate staged paths")
        notice_paths.add(staged_path)

    symlinks = _array(
        manifest.get("verifiedSymlinks"), "manifest verifiedSymlinks"
    )
    symlink_paths: set[str] = set()
    for index, raw_entry in enumerate(symlinks):
        entry = _object(raw_entry, f"verifiedSymlinks[{index}]")
        _require_keys(entry, {"path", "target"}, f"verifiedSymlinks[{index}]")
        symlink_path = _string(entry.get("path"), f"verifiedSymlinks[{index}].path")
        _string(entry.get("target"), f"verifiedSymlinks[{index}].target")
        if symlink_path in symlink_paths:
            raise AppleApplicationAuditError("verifiedSymlinks has duplicate paths")
        symlink_paths.add(symlink_path)

    inspections = _array(
        manifest.get("archiveInspections"), "manifest archiveInspections"
    )
    if len(inspections) != len(containers) + 1:
        raise AppleApplicationAuditError(
            "archiveInspections must cover the source and every container"
        )
    expected_formats = [
        _string(_object(manifest.get("source"), "source").get("archive"), "source.archive"),
        *[
            _string(entry.get("archive"), f"containers[{index}].archive")
            for index, entry in enumerate(containers)
        ],
    ]
    for index, raw_entry in enumerate(inspections):
        entry = _object(raw_entry, f"archiveInspections[{index}]")
        _require_keys(
            entry,
            {
                "compressedBytes",
                "depth",
                "directoryCount",
                "format",
                "memberCount",
                "regularFileCount",
                "symbolicLinkCount",
                "uncompressedBytes",
            },
            f"archiveInspections[{index}]",
        )
        if _integer(entry.get("depth"), f"archiveInspections[{index}].depth") != index:
            raise AppleApplicationAuditError("archive inspection depths must be consecutive")
        expected_format = "tar" if expected_formats[index] == "tgz" else expected_formats[index]
        if _string(entry.get("format"), f"archiveInspections[{index}].format") != expected_format:
            raise AppleApplicationAuditError("archive inspection format does not match its layer")
        compressed = _integer(
            entry.get("compressedBytes"),
            f"archiveInspections[{index}].compressedBytes",
            positive=True,
        )
        uncompressed = _integer(
            entry.get("uncompressedBytes"),
            f"archiveInspections[{index}].uncompressedBytes",
            positive=True,
        )
        members = _integer(
            entry.get("memberCount"),
            f"archiveInspections[{index}].memberCount",
            positive=True,
        )
        regular = _integer(
            entry.get("regularFileCount"),
            f"archiveInspections[{index}].regularFileCount",
        )
        directories = _integer(
            entry.get("directoryCount"),
            f"archiveInspections[{index}].directoryCount",
        )
        symbolic_links = _integer(
            entry.get("symbolicLinkCount"),
            f"archiveInspections[{index}].symbolicLinkCount",
        )
        if compressed > _MAX_MACHO_BYTES or uncompressed > 2 * 1024 * 1024 * 1024:
            raise AppleApplicationAuditError("archive inspection exceeds audit bounds")
        if regular + directories + symbolic_links > members:
            raise AppleApplicationAuditError("archive inspection counts are inconsistent")


def audit_packaged_metadata(
    application: Path,
    platform: str,
) -> tuple[dict[str, Any], Path, Path, tuple[int, int, int]]:
    contract = _platform_contract(platform)
    asset_root = _asset_root(application, platform)
    manifest_path = asset_root / _MANIFEST_NAME
    notice_path = asset_root / _NOTICE_NAME
    for name in (_MANIFEST_NAME, _NOTICE_NAME):
        matches = _all_named_files(application, name)
        if len(matches) != 1:
            raise AppleApplicationAuditError(
                f"final app must contain exactly one {name}; found {len(matches)}"
            )
    manifest = _read_manifest(manifest_path)
    _require_keys(manifest, _MANIFEST_KEYS, "packaged Fonix manifest")
    if manifest.get("schema") != 2:
        raise AppleApplicationAuditError("unsupported packaged Fonix manifest schema")
    if manifest.get("claimBoundary") != _CLAIM_BOUNDARY:
        raise AppleApplicationAuditError("packaged manifest claim boundary changed")
    artifact_id = _token(manifest.get("artifactId"), "artifactId")
    lock = _object(manifest.get("lock"), "lock")
    _require_keys(lock, {"path", "releaseState", "sha256", "snapshotDate"}, "lock")
    if lock.get("path") != "native/versions.lock.yaml":
        raise AppleApplicationAuditError("manifest lock path is not canonical")
    _string(lock.get("releaseState"), "lock.releaseState")
    _string(lock.get("snapshotDate"), "lock.snapshotDate")
    lock_sha256 = _digest(lock.get("sha256"), "lock.sha256")
    target = _object(manifest.get("target"), "target")
    _require_keys(
        target,
        {"architecture", "flavor", "minimumOs", "os", "runtimeMode", "variant"},
        "target",
    )
    if target.get("os") != contract["targetOs"]:
        raise AppleApplicationAuditError(
            f"manifest target OS {target.get('os')!r} does not match {platform}"
        )
    if target.get("variant") != contract["targetVariant"]:
        raise AppleApplicationAuditError("manifest target variant is wrong for the app")
    if target.get("runtimeMode") != contract["runtimeMode"]:
        raise AppleApplicationAuditError(
            f"manifest runtime mode must be {contract['runtimeMode']} on {platform}"
        )
    if target.get("architecture") != "arm64" or target.get("flavor") != "cpu":
        raise AppleApplicationAuditError(
            "Apple final-app audit currently accepts only locked arm64/cpu tuples"
        )
    minimum_os_text = _string(target.get("minimumOs"), "target.minimumOs")
    minimum_os = _version(minimum_os_text, "target.minimumOs")
    source = _object(manifest.get("source"), "source")
    _require_keys(
        source,
        {"archive", "sha256", "sizeBytes", "sourceRevision", "url"},
        "source",
    )
    _token(source.get("archive"), "source.archive")
    source_sha256 = _digest(source.get("sha256"), "source.sha256")
    _integer(source.get("sizeBytes"), "source.sizeBytes", positive=True)
    _string(source.get("sourceRevision"), "source.sourceRevision")
    _string(source.get("url"), "source.url")
    _validate_manifest_collections(manifest)
    notices = _array(manifest.get("notices"), "manifest notices")
    notice_entries = [
        entry
        for entry in notices
        if isinstance(entry, dict)
        and entry.get("id") == "ThirdPartyNotices"
        and entry.get("stagedPath") == "notices/ThirdPartyNotices.txt"
    ]
    if len(notice_entries) != 1:
        raise AppleApplicationAuditError(
            "manifest must identify one canonical ThirdPartyNotices payload"
        )
    notice_entry = notice_entries[0]
    notice_sha256 = _digest(
        notice_entry.get("sha256"), "ThirdPartyNotices.sha256"
    )
    notice_size = notice_entry.get("sizeBytes")
    notice_size = _integer(
        notice_size, "ThirdPartyNotices.sizeBytes", positive=True
    )
    _regular_file(notice_path, "packaged ThirdPartyNotices")
    if notice_path.stat().st_size != notice_size or _sha256(notice_path) != notice_sha256:
        raise AppleApplicationAuditError(
            "packaged ThirdPartyNotices bytes do not match the selected lock entry"
        )
    manifest["_auditIdentity"] = {
        "artifactId": artifact_id,
        "lockSha256": lock_sha256,
        "sourceSha256": source_sha256,
        "minimumOs": minimum_os_text,
        "thirdPartyNoticesSha256": notice_sha256,
    }
    return manifest, manifest_path, notice_path, minimum_os


def _read_current_lock(repository: Path) -> tuple[dict[str, Any], str]:
    lock_path = _regular_file(
        repository / "native/versions.lock.yaml", "repository native lock"
    )
    data = lock_path.read_bytes()
    if len(data) > _MAX_MANIFEST_BYTES:
        raise AppleApplicationAuditError("repository native lock is oversized")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppleApplicationAuditError(
            "repository native lock is not strict UTF-8 JSON"
        ) from error
    return _object(value, "repository native lock"), hashlib.sha256(data).hexdigest()


def _indexed(entries: list[Any], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw_entry in enumerate(entries):
        entry = _object(raw_entry, f"{label}[{index}]")
        identity = _string(entry.get(key), f"{label}[{index}].{key}")
        if identity in result:
            raise AppleApplicationAuditError(f"{label} contains duplicate {key}")
        result[identity] = entry
    return result


def _assert_locked_manifest(
    manifest: dict[str, Any], repository: Path, platform: str
) -> dict[str, Any]:
    lock, lock_sha256 = _read_current_lock(repository)
    manifest_lock = _object(manifest.get("lock"), "manifest lock")
    if manifest_lock.get("sha256") != lock_sha256:
        raise AppleApplicationAuditError(
            "packaged manifest does not identify the repository native lock"
        )
    if manifest_lock.get("snapshotDate") != lock.get("snapshot_date") or manifest_lock.get(
        "releaseState"
    ) != lock.get("release_state"):
        raise AppleApplicationAuditError(
            "packaged manifest lock metadata differs from the repository lock"
        )

    contract = _platform_contract(platform)
    target = _object(manifest.get("target"), "manifest target")
    candidates: list[dict[str, Any]] = []
    for index, raw_artifact in enumerate(_array(lock.get("artifacts"), "lock artifacts")):
        artifact = _object(raw_artifact, f"lock artifacts[{index}]")
        artifact_target = _object(
            artifact.get("target"), f"lock artifacts[{index}].target"
        )
        if (
            artifact.get("id") == manifest.get("artifactId")
            and artifact_target.get("os") == contract["targetOs"]
            and artifact_target.get("architecture") == target.get("architecture")
            and artifact_target.get("variant") == contract["targetVariant"]
            and artifact.get("flavor") == target.get("flavor")
            and artifact.get("runtime_mode") == contract["runtimeMode"]
        ):
            candidates.append(artifact)
    if len(candidates) != 1:
        raise AppleApplicationAuditError(
            "packaged manifest does not select exactly one current lock artifact"
        )
    artifact = candidates[0]
    artifact_target = _object(artifact.get("target"), "locked artifact target")
    expected_target = {
        "os": artifact_target.get("os"),
        "architecture": artifact_target.get("architecture"),
        "variant": artifact_target.get("variant"),
        "minimumOs": artifact_target.get("min_os"),
        "flavor": artifact.get("flavor"),
        "runtimeMode": artifact.get("runtime_mode"),
    }
    if target != expected_target:
        raise AppleApplicationAuditError("manifest target differs from the lock")

    source = _object(artifact.get("source"), "locked artifact source")
    expected_source = {
        "url": source.get("url"),
        "sourceRevision": source.get("source_revision"),
        "sha256": source.get("sha256"),
        "sizeBytes": source.get("size_bytes"),
        "archive": source.get("archive"),
    }
    if manifest.get("source") != expected_source:
        raise AppleApplicationAuditError("manifest source differs from the lock")

    expected_containers = [
        {
            "path": entry.get("path"),
            "sha256": entry.get("sha256"),
            "sizeBytes": entry.get("size_bytes"),
            "archive": entry.get("archive"),
            "depth": index + 1,
        }
        for index, raw_entry in enumerate(
            _array(artifact.get("containers"), "locked artifact containers")
        )
        for entry in [_object(raw_entry, f"locked container[{index}]")]
    ]
    if manifest.get("containers") != expected_containers:
        raise AppleApplicationAuditError("manifest containers differ from the lock")

    locked_files = _array(artifact.get("expected_files"), "locked expected_files")
    expected_payloads = {
        entry.get("staged_path"): {
            "archivePath": entry.get("path"),
            "stagedPath": entry.get("staged_path"),
            "sha256": entry.get("sha256"),
            "sizeBytes": entry.get("size_bytes"),
        }
        for index, raw_entry in enumerate(locked_files)
        for entry in [_object(raw_entry, f"locked expected_files[{index}]")]
    }
    if len(expected_payloads) != len(locked_files):
        raise AppleApplicationAuditError("locked expected_files has duplicate staged paths")
    if _indexed(
        _array(manifest.get("payloadFiles"), "manifest payloadFiles"),
        "stagedPath",
        "manifest payloadFiles",
    ) != expected_payloads:
        raise AppleApplicationAuditError("manifest payload files differ from the lock")

    locked_notices = _array(artifact.get("notices"), "locked artifact notices")
    expected_notices = {
        entry.get("staged_path"): {
            "id": entry.get("id"),
            "containerDepth": entry.get("container_depth"),
            "archivePath": entry.get("path"),
            "stagedPath": entry.get("staged_path"),
            "sha256": entry.get("sha256"),
            "sizeBytes": entry.get("size_bytes"),
        }
        for index, raw_entry in enumerate(locked_notices)
        for entry in [_object(raw_entry, f"locked notices[{index}]")]
    }
    if len(expected_notices) != len(locked_notices):
        raise AppleApplicationAuditError("locked notices has duplicate staged paths")
    if _indexed(
        _array(manifest.get("notices"), "manifest notices"),
        "stagedPath",
        "manifest notices",
    ) != expected_notices:
        raise AppleApplicationAuditError("manifest notices differ from the lock")

    locked_symlinks = _array(
        artifact.get("expected_symlinks"), "locked expected_symlinks"
    )
    expected_symlinks = {
        entry.get("path"): {
            "path": entry.get("path"),
            "target": entry.get("target"),
        }
        for index, raw_entry in enumerate(locked_symlinks)
        for entry in [_object(raw_entry, f"locked symlink[{index}]")]
    }
    if len(expected_symlinks) != len(locked_symlinks):
        raise AppleApplicationAuditError("locked symlinks has duplicate paths")
    if _indexed(
        _array(manifest.get("verifiedSymlinks"), "manifest verifiedSymlinks"),
        "path",
        "manifest verifiedSymlinks",
    ) != expected_symlinks:
        raise AppleApplicationAuditError("manifest symlinks differ from the lock")
    return artifact


def _plist(application: Path, platform: str) -> tuple[dict[str, Any], Path]:
    plist_path = (
        application / "Contents/Info.plist"
        if platform == "macos"
        else application / "Info.plist"
    )
    _regular_file(plist_path, "application Info.plist")
    try:
        value = plistlib.loads(plist_path.read_bytes())
    except (plistlib.InvalidFileException, ValueError) as error:
        raise AppleApplicationAuditError("application Info.plist is invalid") from error
    if not isinstance(value, dict):
        raise AppleApplicationAuditError("application Info.plist is not a dictionary")
    return value, plist_path


def _run(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise AppleApplicationAuditError(
            f"command failed: {' '.join(command)}"
        ) from error
    return result.stdout


def _macho_arch_name(cpu_type: int) -> str:
    cpu_type &= 0xFFFFFFFF
    if cpu_type == 0x0100000C:
        return "arm64"
    if cpu_type == 0x01000007:
        return "x86_64"
    raise AppleApplicationAuditError(f"unsupported Mach-O CPU type 0x{cpu_type:08x}")


def _thin_macho_architecture(data: bytes, label: str) -> str:
    if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe":
        raise AppleApplicationAuditError(f"{label} is not a little-endian Mach-O 64 slice")
    magic, cpu_type, _, _, command_count, command_bytes, _, _ = struct.unpack_from(
        "<IiiIIIII", data, 0
    )
    if magic != 0xFEEDFACF or command_count > 4096 or command_bytes > len(data) - 32:
        raise AppleApplicationAuditError(f"{label} has an invalid Mach-O header")
    return _macho_arch_name(cpu_type)


def _macho_slices(binary: Path) -> dict[str, bytes]:
    _regular_file(binary, "Mach-O binary")
    size = binary.stat().st_size
    if size <= 0 or size > _MAX_MACHO_BYTES:
        raise AppleApplicationAuditError(f"Mach-O binary exceeds audit bounds: {binary}")
    data = binary.read_bytes()
    if data[:4] == b"\xcf\xfa\xed\xfe":
        architecture = _thin_macho_architecture(data, str(binary))
        return {architecture: data}
    if data[:4] != b"\xca\xfe\xba\xbe" or len(data) < 8:
        raise AppleApplicationAuditError(f"unsupported Mach-O container: {binary}")
    _, count = struct.unpack_from(">II", data, 0)
    if count <= 0 or count > 8 or len(data) < 8 + count * 20:
        raise AppleApplicationAuditError(f"invalid fat Mach-O header: {binary}")
    result: dict[str, bytes] = {}
    ranges: list[tuple[int, int]] = []
    for index in range(count):
        cpu_type, _, offset, slice_size, _ = struct.unpack_from(
            ">iiIII", data, 8 + index * 20
        )
        if slice_size <= 0 or offset < 8 + count * 20 or offset + slice_size > len(data):
            raise AppleApplicationAuditError(f"invalid fat Mach-O slice: {binary}")
        if any(offset < end and start < offset + slice_size for start, end in ranges):
            raise AppleApplicationAuditError(f"overlapping fat Mach-O slices: {binary}")
        ranges.append((offset, offset + slice_size))
        architecture = _macho_arch_name(cpu_type)
        slice_bytes = data[offset : offset + slice_size]
        if _thin_macho_architecture(slice_bytes, str(binary)) != architecture:
            raise AppleApplicationAuditError(f"fat Mach-O CPU metadata mismatch: {binary}")
        if architecture in result:
            raise AppleApplicationAuditError(f"duplicate Mach-O architecture: {binary}")
        result[architecture] = slice_bytes
    return result


def _parse_macho_build_versions(
    output: str, binary: Path
) -> list[tuple[int, tuple[int, int, int]]]:
    records: list[tuple[int, tuple[int, int, int]]] = []
    command: str | None = None
    platform: int | None = None
    minimum: tuple[int, int, int] | None = None

    def finish() -> None:
        nonlocal command, platform, minimum
        if command == "LC_BUILD_VERSION":
            if platform is None or minimum is None:
                raise AppleApplicationAuditError(
                    f"incomplete LC_BUILD_VERSION in {binary}"
                )
            records.append((platform, minimum))
        command = None
        platform = None
        minimum = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Load command "):
            finish()
        elif line.startswith("cmd "):
            command = line[4:]
        elif command == "LC_BUILD_VERSION" and line.startswith("platform "):
            token = line.split()[1]
            try:
                platform = int(token)
            except ValueError:
                platform = _PLATFORM_NAMES.get(token.lower())
                if platform is None:
                    raise AppleApplicationAuditError(
                        f"unknown LC_BUILD_VERSION platform {token!r} in {binary}"
                    )
        elif command == "LC_BUILD_VERSION" and line.startswith("minos "):
            minimum = _version(line.split()[1], f"{binary} minos")
    finish()
    if not records:
        raise AppleApplicationAuditError(f"missing LC_BUILD_VERSION in {binary}")
    return records


def _audit_macho(
    binary: Path,
    *,
    architecture: str,
    platform_number: int,
    minimum_os: tuple[int, int, int],
    otool: str,
) -> None:
    slices = _macho_slices(binary)
    if set(slices) != {architecture}:
        raise AppleApplicationAuditError(
            f"Mach-O architectures do not match {architecture}: {binary}"
        )
    records = _parse_macho_build_versions(_run((otool, "-l", str(binary))), binary)
    if len(records) != 1:
        raise AppleApplicationAuditError(
            f"Mach-O must contain exactly one audited build-version record: {binary}"
        )
    binary_platform, binary_floor = records[0]
    if binary_platform != platform_number:
        raise AppleApplicationAuditError(f"Mach-O platform is wrong: {binary}")
    if binary_floor < minimum_os:
        raise AppleApplicationAuditError(
            f"Mach-O deployment floor is below the lock: {binary}"
        )


def _macho_section_identity(binary: Path, architecture: str) -> dict[str, tuple[int, str]]:
    data = _macho_slices(binary).get(architecture)
    if data is None:
        raise AppleApplicationAuditError(f"missing {architecture} Mach-O slice: {binary}")
    _, _, _, _, command_count, command_bytes, _, _ = struct.unpack_from(
        "<IiiIIIII", data, 0
    )
    offset = 32
    commands_end = offset + command_bytes
    result: dict[str, tuple[int, str]] = {}
    for _ in range(command_count):
        if offset + 8 > commands_end:
            raise AppleApplicationAuditError(f"truncated Mach-O load command: {binary}")
        command, command_size = struct.unpack_from("<II", data, offset)
        if command_size < 8 or offset + command_size > commands_end:
            raise AppleApplicationAuditError(f"invalid Mach-O load command: {binary}")
        if command == 0x19:
            if command_size < 72:
                raise AppleApplicationAuditError(f"invalid LC_SEGMENT_64: {binary}")
            segment_name_bytes, _, _, _, _, _, _, section_count, _ = struct.unpack_from(
                "<16sQQQQiiII", data, offset + 8
            )
            segment_name = segment_name_bytes.split(b"\0", 1)[0].decode("ascii")
            if 72 + section_count * 80 > command_size:
                raise AppleApplicationAuditError(f"truncated Mach-O sections: {binary}")
            for section_index in range(section_count):
                section_offset = offset + 72 + section_index * 80
                section = struct.unpack_from("<16s16sQQIIIIIIII", data, section_offset)
                section_name = section[0].split(b"\0", 1)[0].decode("ascii")
                declared_segment = section[1].split(b"\0", 1)[0].decode("ascii")
                section_size = section[3]
                file_offset = section[4]
                flags = section[8]
                if declared_segment != segment_name:
                    raise AppleApplicationAuditError(f"Mach-O section segment mismatch: {binary}")
                section_type = flags & 0xFF
                is_zero_fill = section_type in {1, 12, 18}
                if is_zero_fill:
                    section_bytes = b""
                else:
                    if file_offset + section_size > len(data):
                        raise AppleApplicationAuditError(f"Mach-O section is out of bounds: {binary}")
                    section_bytes = data[file_offset : file_offset + section_size]
                key = f"{segment_name},{section_name}"
                if key in result:
                    raise AppleApplicationAuditError(f"duplicate Mach-O section: {binary}")
                result[key] = (section_size, hashlib.sha256(section_bytes).hexdigest())
        offset += command_size
    if offset != commands_end or not result:
        raise AppleApplicationAuditError(f"invalid or empty Mach-O section table: {binary}")
    return result


def _macho_uuid(binary: Path, otool: str) -> str:
    matches = re.findall(
        r"(?m)^\s*uuid\s+([0-9A-Fa-f-]{36})\s*$", _run((otool, "-l", str(binary)))
    )
    if len(matches) != 1:
        raise AppleApplicationAuditError(f"Mach-O must contain one LC_UUID: {binary}")
    return matches[0].upper()


def _macho_paths(application: Path, platform: str, executable: str) -> list[Path]:
    if platform == "macos":
        return [
            application / "Contents/MacOS" / executable,
            application
            / "Contents/Frameworks/fonix_shim.framework/Versions/A/fonix_shim",
            application
            / "Contents/Frameworks/onnxruntime.1.framework/Versions/A/onnxruntime.1",
        ]
    return [
        application / executable,
        application / "Frameworks/fonix_shim.framework/fonix_shim",
    ]


def _expected_build_manifest(
    manifest: dict[str, Any], locked_artifact: dict[str, Any]
) -> dict[str, Any]:
    identity = _object(manifest.get("_auditIdentity"), "audit identity")
    target = _object(manifest.get("target"), "manifest target")
    runtime_mode = target.get("runtimeMode")
    allowed_sources = {
        "external": ["process", "file"],
        "bundled": ["bundled"],
        "linked": ["linked"],
    }.get(runtime_mode)
    if allowed_sources is None:
        raise AppleApplicationAuditError(
            "manifest target has an unsupported runtime mode"
        )
    compiled_providers: list[dict[str, Any]] = []
    seen_provider_ids: set[str] = set()
    for index, raw_provider in enumerate(
        _array(locked_artifact.get("providers"), "locked artifact providers")
    ):
        provider = _object(raw_provider, f"locked artifact providers[{index}]")
        _require_keys(
            provider,
            {"wrapper_id", "reported_name"},
            f"locked artifact providers[{index}]",
        )
        wrapper_id = _token(
            provider.get("wrapper_id"),
            f"locked artifact providers[{index}].wrapper_id",
        )
        if wrapper_id in seen_provider_ids:
            raise AppleApplicationAuditError(
                "locked artifact provider inventory contains duplicate IDs"
            )
        seen_provider_ids.add(wrapper_id)
        reported_name = provider.get("reported_name")
        if reported_name is not None:
            reported_name = _string(
                reported_name,
                f"locked artifact providers[{index}].reported_name",
            )
        compiled_providers.append(
            {"wrapperId": wrapper_id, "reportedName": reported_name}
        )
    if not compiled_providers or len(compiled_providers) > 64:
        raise AppleApplicationAuditError(
            "locked artifact provider inventory is empty or oversized"
        )

    artifact = {
        "id": identity.get("artifactId"),
        "lockSha256": identity.get("lockSha256"),
        "sourceSha256": identity.get("sourceSha256"),
        "targetOs": target.get("os"),
        "targetArchitecture": target.get("architecture"),
        "targetVariant": target.get("variant"),
        "minimumOs": target.get("minimumOs"),
        "flavor": target.get("flavor"),
        "runtimeMode": target.get("runtimeMode"),
        "thirdPartyNoticesSha256": identity.get("thirdPartyNoticesSha256"),
        "providers": compiled_providers,
    }
    return {
        "schemaVersion": 3,
        "nativeIdentity": "fonix_shim",
        "shimAbiVersion": 1,
        "requiredOrtApiVersion": 27,
        "runtimeProfile": runtime_mode,
        "androidRuntimeOwner": None,
        "allowedRuntimeSources": allowed_sources,
        "buildId": identity.get("artifactId"),
        "artifact": artifact,
    }


def _validate_build_manifest(value: Any, expected: dict[str, Any], label: str) -> None:
    build_manifest = _object(value, label)
    _require_keys(build_manifest, _BUILD_MANIFEST_KEYS, label)
    artifact = _object(build_manifest.get("artifact"), f"{label}.artifact")
    _require_keys(artifact, _ARTIFACT_IDENTITY_KEYS, f"{label}.artifact")
    if build_manifest != expected:
        raise AppleApplicationAuditError(
            f"{label} does not match the selected packaged artifact"
        )


def _extract_embedded_build_manifest(
    shim: Path, expected: dict[str, Any]
) -> dict[str, Any]:
    data = _regular_file(shim, "Fonix shim").read_bytes()
    prefix = b'{"schemaVersion":3,"nativeIdentity":"fonix_shim",'
    candidates: list[dict[str, Any]] = []
    offset = 0
    while True:
        start = data.find(prefix, offset)
        if start < 0:
            break
        end = data.find(b"\0", start, min(len(data), start + 64 * 1024 + 1))
        if end < 0:
            raise AppleApplicationAuditError("embedded build manifest is not bounded")
        try:
            value = json.loads(
                data[start:end].decode("utf-8"), object_pairs_hook=_strict_object
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AppleApplicationAuditError(
                "embedded build manifest is not strict JSON"
            ) from error
        candidates.append(_object(value, "embedded build manifest"))
        offset = end + 1
    if len(candidates) != 1:
        raise AppleApplicationAuditError(
            f"final shim must embed exactly one build manifest; found {len(candidates)}"
        )
    _validate_build_manifest(candidates[0], expected, "embedded build manifest")
    return candidates[0]


def _runtime_payload_entry(manifest: dict[str, Any]) -> dict[str, Any]:
    runtime_entries = [
        entry
        for entry in _array(manifest.get("payloadFiles"), "manifest payloadFiles")
        if isinstance(entry, dict)
        and entry.get("stagedPath") == "libonnxruntime.1.dylib"
    ]
    if len(runtime_entries) != 1:
        raise AppleApplicationAuditError(
            "manifest must identify one macOS runtime payload"
        )
    return runtime_entries[0]


def audit_application(
    application: Path,
    platform: str,
    declared_minimum_os: str,
    *,
    repository: Path,
    reference_runtime: Path | None = None,
    otool: str = "/usr/bin/otool",
    codesign: str = "/usr/bin/codesign",
) -> dict[str, Any]:
    contract = _platform_contract(platform)
    if not application.is_dir() or application.is_symlink():
        raise AppleApplicationAuditError("--app must be a non-symlink directory")
    manifest, manifest_path, notice_path, locked_minimum = audit_packaged_metadata(
        application, platform
    )
    locked_artifact = _assert_locked_manifest(manifest, repository, platform)
    declared_minimum = _version(
        declared_minimum_os, "declared application minimum OS"
    )
    if declared_minimum < locked_minimum:
        raise AppleApplicationAuditError(
            "declared application minimum OS is below the selected lock tuple"
        )
    plist, plist_path = _plist(application, platform)
    floor_key = "LSMinimumSystemVersion" if platform == "macos" else "MinimumOSVersion"
    plist_floor_text = _string(plist.get(floor_key), f"Info.plist {floor_key}")
    plist_floor = _version(plist_floor_text, f"Info.plist {floor_key}")
    if plist_floor < declared_minimum or plist_floor < locked_minimum:
        raise AppleApplicationAuditError(
            "final Info.plist deployment floor is below the declared/locked floor"
        )
    executable = _string(plist.get("CFBundleExecutable"), "CFBundleExecutable")
    binary_paths = _macho_paths(application, platform, executable)
    target = _object(manifest.get("target"), "manifest target")
    architecture = _string(target.get("architecture"), "target architecture")
    for binary in binary_paths:
        _audit_macho(
            binary,
            architecture=architecture,
            platform_number=contract["machoPlatform"],
            minimum_os=locked_minimum,
            otool=otool,
        )
        _run((codesign, "--verify", "--strict", str(binary)))
    _run((codesign, "--verify", "--strict", str(application)))

    identity = _object(manifest.get("_auditIdentity"), "audit identity")
    shim = binary_paths[1]
    expected_build_manifest = _expected_build_manifest(manifest, locked_artifact)
    _extract_embedded_build_manifest(shim, expected_build_manifest)
    if platform == "macos":
        if reference_runtime is None:
            raise AppleApplicationAuditError(
                "macOS final-app audit requires --reference-runtime"
            )
        runtime = binary_paths[2]
        runtime_entry = _runtime_payload_entry(manifest)
        expected_runtime_sha = _digest(runtime_entry.get("sha256"), "runtime payload sha256")
        expected_runtime_size = _integer(
            runtime_entry.get("sizeBytes"), "runtime payload sizeBytes", positive=True
        )
        reference_runtime = _regular_file(reference_runtime, "reference ONNX Runtime")
        if (
            reference_runtime.stat().st_size != expected_runtime_size
            or _sha256(reference_runtime) != expected_runtime_sha
        ):
            raise AppleApplicationAuditError(
                "reference ONNX Runtime does not match the selected lock payload"
            )
        _audit_macho(
            reference_runtime,
            architecture=architecture,
            platform_number=contract["machoPlatform"],
            minimum_os=locked_minimum,
            otool=otool,
        )
        if _macho_uuid(runtime, otool) != _macho_uuid(reference_runtime, otool):
            raise AppleApplicationAuditError(
                "final ONNX Runtime UUID differs from the selected payload"
            )
        if _macho_section_identity(runtime, architecture) != _macho_section_identity(
            reference_runtime, architecture
        ):
            raise AppleApplicationAuditError(
                "final ONNX Runtime loaded sections differ from the selected payload"
            )
        framework_root = application / "Contents/Frameworks"
        runtime_id = [
            line.strip()
            for line in _run((otool, "-D", str(runtime))).splitlines()[1:]
            if line.strip()
        ]
        shim_id = [
            line.strip()
            for line in _run((otool, "-D", str(shim))).splitlines()[1:]
            if line.strip()
        ]
        if runtime_id != ["@rpath/onnxruntime.1.framework/onnxruntime.1"]:
            raise AppleApplicationAuditError("unexpected ONNX Runtime install name")
        if shim_id != ["@rpath/fonix_shim.framework/fonix_shim"]:
            raise AppleApplicationAuditError("unexpected Fonix shim install name")
        if not framework_root.is_dir():
            raise AppleApplicationAuditError("missing macOS Frameworks directory")
    return {
        "platform": platform,
        "application": str(application),
        "artifactId": identity["artifactId"],
        "lockedMinimumOs": identity["minimumOs"],
        "declaredMinimumOs": declared_minimum_os,
        "plistMinimumOs": plist_floor_text,
        "manifest": str(manifest_path),
        "thirdPartyNotices": str(notice_path),
        "plist": str(plist_path),
        "machOBinaries": [str(path) for path in binary_paths],
        "buildManifest": expected_build_manifest,
    }


def run_packaged_cpu_probe(
    application: Path,
    repository: Path,
    model: Path,
    expected_build_manifest: dict[str, Any],
    *,
    clang: str = "/usr/bin/clang",
) -> dict[str, Any]:
    _regular_file(model, "CPU probe model")
    source = _regular_file(
        repository / "tool/ci/apple/packaged_cpu_probe.c", "CPU probe source"
    )
    header_directory = repository / "src"
    frameworks = application / "Contents/Frameworks"
    with tempfile.TemporaryDirectory(prefix="fonix-packaged-probe-") as directory:
        executable = Path(directory) / "fonix_packaged_cpu_probe"
        _run(
            (
                clang,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-Wconversion",
                "-Wshadow",
                "-Werror",
                f"-I{header_directory}",
                str(source),
                f"-F{frameworks}",
                "-framework",
                "fonix_shim",
                f"-Wl,-rpath,{frameworks}",
                "-o",
                str(executable),
            )
        )
        output = _run((str(executable), str(model)))
        if "Fonix packaged macOS CPU inference passed." not in output:
            raise AppleApplicationAuditError("packaged CPU probe did not confirm success")
        prefixes = [
            line.removeprefix("FONIX_BUILD_MANIFEST=")
            for line in output.splitlines()
            if line.startswith("FONIX_BUILD_MANIFEST=")
        ]
        if len(prefixes) != 1:
            raise AppleApplicationAuditError(
                "packaged CPU probe did not return exactly one build manifest"
            )
        try:
            value = json.loads(prefixes[0], object_pairs_hook=_strict_object)
        except json.JSONDecodeError as error:
            raise AppleApplicationAuditError(
                "packaged CPU probe returned invalid build-manifest JSON"
            ) from error
        _validate_build_manifest(
            value, expected_build_manifest, "packaged probe build manifest"
        )
        return _object(value, "packaged probe build manifest")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument(
        "--platform",
        required=True,
        choices=("macos", "ios-device", "ios-simulator"),
    )
    parser.add_argument("--application-minimum-os", required=True)
    parser.add_argument("--run-cpu-probe", type=Path)
    parser.add_argument(
        "--reference-runtime",
        type=Path,
        help="exact lock-verified staged ORT dylib (required for macOS)",
    )
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--otool", default="/usr/bin/otool")
    parser.add_argument("--clang", default="/usr/bin/clang")
    parser.add_argument("--codesign", default="/usr/bin/codesign")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = audit_application(
            arguments.app.resolve(strict=True),
            arguments.platform,
            arguments.application_minimum_os,
            repository=arguments.repository.resolve(strict=True),
            reference_runtime=(
                arguments.reference_runtime.resolve(strict=True)
                if arguments.reference_runtime is not None
                else None
            ),
            otool=arguments.otool,
            codesign=arguments.codesign,
        )
        if arguments.run_cpu_probe is not None:
            if arguments.platform != "macos":
                raise AppleApplicationAuditError(
                    "the host CPU probe is available only for macOS apps"
                )
            probe_manifest = run_packaged_cpu_probe(
                arguments.app,
                arguments.repository.resolve(strict=True),
                arguments.run_cpu_probe.resolve(strict=True),
                _object(report.get("buildManifest"), "expected build manifest"),
                clang=arguments.clang,
            )
            report["cpuInference"] = "passed"
            report["probeBuildManifest"] = probe_manifest
        print(json.dumps(report, sort_keys=True))
        return 0
    except (AppleApplicationAuditError, FileNotFoundError) as error:
        print(f"audit_apple_application: {error}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
