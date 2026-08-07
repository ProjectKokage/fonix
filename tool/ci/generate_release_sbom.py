#!/usr/bin/env python3
"""Generate deterministic, offline Fonix release-audit metadata and SPDX 2.3.

The input is one exact artifact selected by ID from the committed native lock
and a fresh resolver staging directory containing that artifact's manifest and
bytes. The output is audit evidence only. It is never signing evidence,
provider qualification, release approval, or authorization to distribute.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from typing import Any, Iterable
from urllib.parse import urlsplit
import uuid

import source_checksum_manifest


MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_LOCK_BYTES = 4 * 1024 * 1024
MAX_PUBSPEC_LOCK_BYTES = 4 * 1024 * 1024
MAX_PUBSPEC_BYTES = 1024 * 1024
MAX_STAGE_ENTRIES = 4096
MAX_STAGE_FILE_BYTES = 512 * 1024 * 1024
MAX_STAGE_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
MAX_NOTICE_BYTES = 16 * 1024 * 1024
MAX_COMPATIBILITY_INPUT_BYTES = 16 * 1024 * 1024

NATIVE_LOCK_PATH = "native/versions.lock.yaml"
PUBSPEC_LOCK_PATH = "pubspec.lock"
PUBSPEC_PATH = "pubspec.yaml"
SOURCE_MANIFEST_PATH = "MANIFEST.sha256"
STAGED_MANIFEST_NAME = "fonix-native-artifact-manifest.json"
STAGING_CLAIM_BOUNDARY = (
    "Successful staging proves exact archive and member bytes only. It does not "
    "prove loading, linking, provider registration, inference, packaging, "
    "signing, or target-device support."
)
AUDIT_CLAIM_BOUNDARY = (
    "Unreleased audit metadata only; this document is not release approval, "
    "signing evidence, provider qualification, or authorization to publish or "
    "distribute."
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_FLAVOR = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")
_MINIMUM_OS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

_TARGET_ARCHITECTURES = {
    "android": frozenset({"arm64-v8a", "x86_64", "armeabi-v7a", "x86"}),
    "ios": frozenset({"arm64", "x86_64"}),
    "macos": frozenset({"arm64", "x86_64"}),
    "linux": frozenset({"arm64", "x86_64"}),
    "windows": frozenset({"arm64", "x64"}),
}
_REQUIRED_RELEASE_TARGETS = frozenset(
    {
        ("ios", "arm64", "device", "cpu"),
        ("ios", "arm64", "simulator", "cpu"),
        ("macos", "arm64", "default", "cpu"),
        ("android", "arm64-v8a", "default", "cpu"),
        ("android", "x86_64", "default", "cpu"),
        ("linux", "x86_64", "default", "cpu"),
        ("linux", "arm64", "default", "cpu"),
        ("windows", "x64", "default", "cpu"),
    }
)

_LOCK_KEYS = frozenset(
    {
        "schema",
        "snapshot_date",
        "release_state",
        "shim",
        "onnxruntime",
        "release_targets",
        "artifacts",
    }
)
_SHIM_KEYS = frozenset({"abi", "required_ort_api", "source_revision"})
_ONNX_KEYS = frozenset({"compatibility_floor"})
_COMPATIBILITY_KEYS = frozenset({"c_api", "header", "ep_header", "license"})
_SOURCE_INPUT_KEYS = frozenset(
    {"version", "source_repository", "source_ref", "path", "sha256", "size_bytes"}
)
_RELEASE_TARGET_KEYS = frozenset({"os", "architecture", "variant", "flavor"})
_ARTIFACT_KEYS = frozenset(
    {
        "id",
        "target",
        "flavor",
        "runtime_mode",
        "source",
        "containers",
        "expected_files",
        "expected_symlinks",
        "notices",
        "providers",
        "build",
        "licenses",
    }
)
_TARGET_KEYS = frozenset({"os", "architecture", "variant", "min_os"})
_SOURCE_KEYS = frozenset(
    {"url", "source_revision", "sha256", "size_bytes", "archive"}
)
_CONTAINER_KEYS = frozenset({"path", "sha256", "size_bytes", "archive"})
_EXPECTED_FILE_KEYS = frozenset({"path", "staged_path", "sha256", "size_bytes"})
_SYMLINK_KEYS = frozenset({"path", "target"})
_NOTICE_KEYS = frozenset(
    {"id", "container_depth", "path", "staged_path", "sha256", "size_bytes"}
)
_PROVIDER_KEYS = frozenset({"wrapper_id", "reported_name"})
_BUILD_KEYS = frozenset({"source_built", "toolchain", "flags", "patches"})
_PATCH_KEYS = frozenset({"url", "sha256"})
_LICENSE_KEYS = frozenset({"id", "notice_path", "sha256", "size_bytes"})

_MANIFEST_KEYS = frozenset(
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
_MANIFEST_LOCK_KEYS = frozenset({"path", "sha256", "snapshotDate", "releaseState"})
_MANIFEST_TARGET_KEYS = frozenset(
    {"os", "architecture", "variant", "minimumOs", "flavor", "runtimeMode"}
)
_MANIFEST_SOURCE_KEYS = frozenset(
    {"url", "sourceRevision", "archive", "sha256", "sizeBytes"}
)
_MANIFEST_CONTAINER_KEYS = frozenset(
    {"depth", "path", "archive", "sha256", "sizeBytes"}
)
_MANIFEST_PAYLOAD_KEYS = frozenset(
    {"archivePath", "stagedPath", "sha256", "sizeBytes"}
)
_MANIFEST_SYMLINK_KEYS = frozenset({"path", "target"})
_MANIFEST_NOTICE_KEYS = frozenset(
    {"id", "containerDepth", "archivePath", "stagedPath", "sha256", "sizeBytes"}
)
_INSPECTION_KEYS = frozenset(
    {
        "depth",
        "format",
        "memberCount",
        "regularFileCount",
        "directoryCount",
        "symbolicLinkCount",
        "compressedBytes",
        "uncompressedBytes",
    }
)


class ReleaseEvidenceError(RuntimeError):
    """A release-evidence input violates its closed, offline contract."""


@dataclass(frozen=True)
class LockedDependency:
    name: str
    dependency: str
    version: str
    sha256: str
    source_url: str


@dataclass(frozen=True)
class VerifiedStageFile:
    logical_path: str
    sha256: str
    sha1: str
    size_bytes: int
    is_notice: bool
    notice_id: str | None = None


@dataclass(frozen=True)
class EvidenceDocuments:
    sbom: dict[str, Any]
    metadata: dict[str, Any]

    def sbom_bytes(self) -> bytes:
        return _canonical_json(self.sbom)

    def metadata_bytes(self) -> bytes:
        return _canonical_json(self.metadata)


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseEvidenceError(f"JSON input duplicates key {key!r}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise ReleaseEvidenceError(f"JSON input uses non-standard constant {value}")


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _read_regular(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise ReleaseEvidenceError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseEvidenceError(f"{label} must be a regular file, not a link")
    if before.st_size <= 0 or before.st_size > maximum:
        raise ReleaseEvidenceError(f"{label} size is outside the accepted bound")

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseEvidenceError(f"{label} cannot be opened safely") from error
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            raise ReleaseEvidenceError(f"{label} changed type while being read")
        if (
            before.st_size != after.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
        ):
            raise ReleaseEvidenceError(f"{label} changed while being read")
        contents = bytearray()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(contents)))
            if not chunk:
                break
            contents.extend(chunk)
            if len(contents) > maximum:
                raise ReleaseEvidenceError(f"{label} exceeds the accepted size bound")
        if len(contents) != before.st_size:
            raise ReleaseEvidenceError(f"{label} changed while being read")
        return bytes(contents)
    finally:
        os.close(descriptor)


def _hash_regular(path: Path, *, label: str, maximum: int) -> tuple[int, str, str]:
    """Hash a bounded regular file without retaining its bytes in memory."""

    try:
        before = path.lstat()
    except OSError as error:
        raise ReleaseEvidenceError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReleaseEvidenceError(f"{label} must be a regular file, not a link")
    if before.st_size <= 0 or before.st_size > maximum:
        raise ReleaseEvidenceError(f"{label} size is outside the accepted bound")

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseEvidenceError(f"{label} cannot be opened safely") from error
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            raise ReleaseEvidenceError(f"{label} changed type while being read")
        if (
            before.st_size != after.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
        ):
            raise ReleaseEvidenceError(f"{label} changed while being read")
        sha256 = hashlib.sha256()
        sha1 = hashlib.sha1(usedforsecurity=False)
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum:
                raise ReleaseEvidenceError(f"{label} exceeds the accepted size bound")
            sha256.update(chunk)
            sha1.update(chunk)
        if consumed != before.st_size:
            raise ReleaseEvidenceError(f"{label} changed while being read")
        return consumed, sha256.hexdigest(), sha1.hexdigest()
    finally:
        os.close(descriptor)


def _json_file(path: Path, *, label: str, maximum: int) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, label=label, maximum=maximum)
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_keys,
            parse_constant=_invalid_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseEvidenceError(f"{label} is not strict UTF-8 JSON") from error
    except RecursionError as error:
        raise ReleaseEvidenceError(f"{label} nesting exceeds the accepted bound") from error
    if not isinstance(value, dict):
        raise ReleaseEvidenceError(f"{label} root must be an object")
    return value, raw


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseEvidenceError(f"{label} must be an object")
    return value


def _array(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ReleaseEvidenceError(f"{label} item count is outside the accepted bound")
    return value


def _exact_keys(value: dict[str, Any], keys: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != keys:
        unknown = sorted(actual - keys)
        missing = sorted(keys - actual)
        details: list[str] = []
        if unknown:
            details.append(f"unknown={','.join(unknown)}")
        if missing:
            details.append(f"missing={','.join(missing)}")
        raise ReleaseEvidenceError(f"{label} has an invalid field set ({'; '.join(details)})")


def _string(value: Any, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseEvidenceError(f"{label} must be a bounded non-empty string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ReleaseEvidenceError(
            f"{label} must contain well-formed Unicode text"
        ) from error
    if (
        len(encoded) > maximum
        or "\x00" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ReleaseEvidenceError(f"{label} must be a bounded non-empty string")
    return value


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReleaseEvidenceError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ReleaseEvidenceError(f"{label} is outside the accepted bound")
    return value


def _digest(value: Any, label: str) -> str:
    digest = _string(value, label, maximum=64)
    if _DIGEST.fullmatch(digest) is None:
        raise ReleaseEvidenceError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _relative_path(value: Any, label: str) -> str:
    raw = _string(value, label, maximum=1024)
    if "\\" in raw:
        raise ReleaseEvidenceError(f"{label} must use canonical POSIX separators")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseEvidenceError(f"{label} must be a safe relative path")
    if path.as_posix() != raw:
        raise ReleaseEvidenceError(f"{label} must be a canonical relative path")
    return raw


def _https_url(value: Any, label: str) -> str:
    raw = _string(value, label, maximum=2048)
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in raw)
        or ":" in parsed.netloc
    ):
        raise ReleaseEvidenceError(f"{label} must be a credential-free HTTPS URL")
    return raw


def _identifier(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    raw = _string(value, label, maximum=128)
    if pattern.fullmatch(raw) is None:
        raise ReleaseEvidenceError(f"{label} is not a closed identifier")
    return raw


def _repository_file(repository: Path, relative: str, *, maximum: int) -> bytes:
    safe = _relative_path(relative, "repository-relative path")
    current = repository
    for part in safe.split("/"):
        current = current / part
        if current.is_symlink():
            raise ReleaseEvidenceError(f"repository source {safe} must not traverse a link")
    return _read_regular(current, label=f"repository source {safe}", maximum=maximum)


def _validate_source_input(value: Any, label: str) -> dict[str, Any]:
    item = _object(value, label)
    _exact_keys(item, _SOURCE_INPUT_KEYS, label)
    _string(item["version"], f"{label}.version", maximum=128)
    _https_url(item["source_repository"], f"{label}.source_repository")
    _string(item["source_ref"], f"{label}.source_ref", maximum=256)
    _relative_path(item["path"], f"{label}.path")
    _digest(item["sha256"], f"{label}.sha256")
    _integer(
        item["size_bytes"],
        f"{label}.size_bytes",
        minimum=1,
        maximum=MAX_COMPATIBILITY_INPUT_BYTES,
    )
    return item


def _validate_target_dimensions(
    value: dict[str, Any], label: str
) -> tuple[str, str, str]:
    operating_system = _string(value["os"], f"{label}.os", maximum=16)
    architectures = _TARGET_ARCHITECTURES.get(operating_system)
    if architectures is None:
        raise ReleaseEvidenceError(f"{label}.os is unsupported")
    architecture = _string(
        value["architecture"], f"{label}.architecture", maximum=32
    )
    if architecture not in architectures:
        raise ReleaseEvidenceError(
            f"{label}.architecture is unsupported for {operating_system}"
        )
    variant = _string(value["variant"], f"{label}.variant", maximum=16)
    variants = {"device", "simulator"} if operating_system == "ios" else {"default"}
    if variant not in variants:
        raise ReleaseEvidenceError(
            f"{label}.variant is unsupported for {operating_system}"
        )
    return operating_system, architecture, variant


def _validate_target(value: Any, label: str) -> dict[str, Any]:
    target = _object(value, label)
    _exact_keys(target, _TARGET_KEYS, label)
    _validate_target_dimensions(target, label)
    minimum_os = _string(target["min_os"], f"{label}.min_os", maximum=64)
    if _MINIMUM_OS.fullmatch(minimum_os) is None:
        raise ReleaseEvidenceError(f"{label}.min_os is invalid")
    return target


def _validate_artifact_source(value: Any, label: str) -> dict[str, Any]:
    source = _object(value, label)
    _exact_keys(source, _SOURCE_KEYS, label)
    _https_url(source["url"], f"{label}.url")
    revision = _string(source["source_revision"], f"{label}.source_revision", maximum=64)
    if _REVISION.fullmatch(revision) is None:
        raise ReleaseEvidenceError(f"{label}.source_revision is invalid")
    _digest(source["sha256"], f"{label}.sha256")
    _integer(
        source["size_bytes"],
        f"{label}.size_bytes",
        minimum=1,
        maximum=MAX_SOURCE_ARCHIVE_BYTES,
    )
    if source["archive"] not in {"zip", "tar.gz", "tgz"}:
        raise ReleaseEvidenceError(f"{label}.archive is unsupported")
    return source


def _validate_lock(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _exact_keys(lock, _LOCK_KEYS, "native lock")
    if lock["schema"] != 2:
        raise ReleaseEvidenceError("native lock schema must be 2")
    snapshot_date = _string(lock["snapshot_date"], "native lock snapshot_date", maximum=10)
    try:
        year, month, day = (int(part) for part in snapshot_date.split("-"))
        if f"{year:04d}-{month:02d}-{day:02d}" != snapshot_date:
            raise ValueError
        import datetime

        datetime.date(year, month, day)
    except (ValueError, TypeError) as error:
        raise ReleaseEvidenceError("native lock snapshot_date is invalid") from error
    if lock["release_state"] not in {"unreleased-preview", "release"}:
        raise ReleaseEvidenceError("native lock release_state is invalid")

    shim = _object(lock["shim"], "native lock shim")
    _exact_keys(shim, _SHIM_KEYS, "native lock shim")
    _integer(shim["abi"], "native lock shim.abi", minimum=1, maximum=0xFFFFFFFF)
    required_ort_api = _integer(
        shim["required_ort_api"],
        "native lock shim.required_ort_api",
        minimum=1,
        maximum=0xFFFFFFFF,
    )
    revision = shim["source_revision"]
    if revision is not None:
        revision = _string(revision, "native lock shim.source_revision", maximum=64)
        if _REVISION.fullmatch(revision) is None:
            raise ReleaseEvidenceError("native lock shim.source_revision is invalid")
    if lock["release_state"] == "release" and revision is None:
        raise ReleaseEvidenceError("release native lock requires a shim source revision")

    onnx = _object(lock["onnxruntime"], "native lock onnxruntime")
    _exact_keys(onnx, _ONNX_KEYS, "native lock onnxruntime")
    compatibility = _object(onnx["compatibility_floor"], "native lock compatibility_floor")
    _exact_keys(compatibility, _COMPATIBILITY_KEYS, "native lock compatibility_floor")
    compatibility_api = _integer(
        compatibility["c_api"],
        "native lock compatibility_floor.c_api",
        minimum=1,
        maximum=0xFFFFFFFF,
    )
    if required_ort_api != compatibility_api:
        raise ReleaseEvidenceError(
            "native lock shim.required_ort_api must equal "
            "onnxruntime.compatibility_floor.c_api"
        )
    for name in ("header", "ep_header", "license"):
        _validate_source_input(
            compatibility[name], f"native lock compatibility_floor.{name}"
        )

    release_targets = _array(
        lock["release_targets"], "native lock release_targets", minimum=8, maximum=8
    )
    release_target_keys: set[tuple[str, str, str, str]] = set()
    for index, raw_target in enumerate(release_targets):
        label = f"native lock release_targets[{index}]"
        target = _object(raw_target, label)
        _exact_keys(target, _RELEASE_TARGET_KEYS, label)
        operating_system, architecture, variant = _validate_target_dimensions(
            target, label
        )
        flavor = _identifier(target["flavor"], f"{label}.flavor", _FLAVOR)
        if flavor != "cpu":
            raise ReleaseEvidenceError(f"{label}.flavor is unsupported")
        key = (operating_system, architecture, variant, flavor)
        if key in release_target_keys:
            raise ReleaseEvidenceError("native lock release_targets contains a duplicate")
        release_target_keys.add(key)
    if release_target_keys != _REQUIRED_RELEASE_TARGETS:
        missing = sorted(_REQUIRED_RELEASE_TARGETS - release_target_keys)
        unknown = sorted(release_target_keys - _REQUIRED_RELEASE_TARGETS)
        raise ReleaseEvidenceError(
            "native lock release_targets must exactly declare the schema-v2 "
            f"Tier-1 CPU matrix (missing={missing}, unknown={unknown})"
        )

    artifacts = _array(lock["artifacts"], "native lock artifacts", minimum=1, maximum=256)
    by_id: dict[str, dict[str, Any]] = {}
    covered_release_targets: set[tuple[str, str, str, str]] = set()
    for index, raw_artifact in enumerate(artifacts):
        label = f"native lock artifacts[{index}]"
        artifact = _object(raw_artifact, label)
        _exact_keys(artifact, _ARTIFACT_KEYS, label)
        artifact_id = _identifier(artifact["id"], f"{label}.id", _IDENTIFIER)
        if artifact_id in by_id:
            raise ReleaseEvidenceError(f"native lock duplicates artifact ID {artifact_id}")
        target = _validate_target(artifact["target"], f"{label}.target")
        flavor = _identifier(artifact["flavor"], f"{label}.flavor", _FLAVOR)
        if artifact["runtime_mode"] not in {"linked", "bundled", "process", "aligned", "file"}:
            raise ReleaseEvidenceError(f"{label}.runtime_mode is unsupported")
        _validate_artifact_source(artifact["source"], f"{label}.source")

        containers = _array(artifact["containers"], f"{label}.containers", maximum=8)
        selected_members: dict[int, set[str]] = {}

        def add_selected_member(depth: int, path: str, value_label: str) -> None:
            members = selected_members.setdefault(depth, set())
            if path in members:
                raise ReleaseEvidenceError(
                    f"{value_label} overlaps another selected member at "
                    f"container depth {depth}"
                )
            members.add(path)

        for container_index, raw_container in enumerate(containers):
            container_label = f"{label}.containers[{container_index}]"
            container = _object(raw_container, container_label)
            _exact_keys(container, _CONTAINER_KEYS, container_label)
            container_path = _relative_path(
                container["path"], f"{container_label}.path"
            )
            add_selected_member(
                container_index, container_path, f"{container_label}.path"
            )
            _digest(container["sha256"], f"{container_label}.sha256")
            _integer(
                container["size_bytes"],
                f"{container_label}.size_bytes",
                minimum=1,
                maximum=MAX_SOURCE_ARCHIVE_BYTES,
            )
            if container["archive"] not in {"zip", "tar.gz", "tgz"}:
                raise ReleaseEvidenceError(f"{container_label}.archive is unsupported")

        staged_paths: set[str] = set()
        staged_paths_casefolded: set[str] = set()
        expected_file_paths: set[str] = set()
        expected_files = _array(
            artifact["expected_files"],
            f"{label}.expected_files",
            minimum=1,
            maximum=2048,
        )
        for file_index, raw_file in enumerate(expected_files):
            file_label = f"{label}.expected_files[{file_index}]"
            entry = _object(raw_file, file_label)
            _exact_keys(entry, _EXPECTED_FILE_KEYS, file_label)
            file_path = _relative_path(entry["path"], f"{file_label}.path")
            if file_path in expected_file_paths:
                raise ReleaseEvidenceError(
                    f"{file_label}.path duplicates expected path {file_path}"
                )
            expected_file_paths.add(file_path)
            add_selected_member(len(containers), file_path, f"{file_label}.path")
            staged_path = _relative_path(entry["staged_path"], f"{file_label}.staged_path")
            if (
                staged_path in staged_paths
                or staged_path.casefold() in staged_paths_casefolded
            ):
                raise ReleaseEvidenceError(f"{label} duplicates staged path {staged_path}")
            staged_paths.add(staged_path)
            staged_paths_casefolded.add(staged_path.casefold())
            _digest(entry["sha256"], f"{file_label}.sha256")
            _integer(
                entry["size_bytes"],
                f"{file_label}.size_bytes",
                minimum=1,
                maximum=MAX_STAGE_FILE_BYTES,
            )

        symlinks = _array(
            artifact["expected_symlinks"], f"{label}.expected_symlinks", maximum=256
        )
        symlink_targets: dict[str, str] = {}
        for symlink_index, raw_symlink in enumerate(symlinks):
            symlink_label = f"{label}.expected_symlinks[{symlink_index}]"
            entry = _object(raw_symlink, symlink_label)
            _exact_keys(entry, _SYMLINK_KEYS, symlink_label)
            link_path = _relative_path(entry["path"], f"{symlink_label}.path")
            target_path = _relative_path(entry["target"], f"{symlink_label}.target")
            if link_path in symlink_targets:
                raise ReleaseEvidenceError(f"{label} duplicates expected symlink path")
            symlink_targets[link_path] = target_path
            add_selected_member(
                len(containers), link_path, f"{symlink_label}.path"
            )

        for link_path, target_path in symlink_targets.items():
            resolved_target = (
                PurePosixPath(link_path).parent / PurePosixPath(target_path)
            ).as_posix()
            visited = {link_path}
            while resolved_target not in expected_file_paths:
                next_target = symlink_targets.get(resolved_target)
                if next_target is None or resolved_target in visited:
                    raise ReleaseEvidenceError(
                        f"{label} expected symlink {link_path} must resolve through "
                        "an acyclic declared chain to an expected regular file"
                    )
                visited.add(resolved_target)
                resolved_target = (
                    PurePosixPath(resolved_target).parent
                    / PurePosixPath(next_target)
                ).as_posix()

        notices = _array(
            artifact["notices"], f"{label}.notices", minimum=1, maximum=32
        )
        for notice_index, raw_notice in enumerate(notices):
            notice_label = f"{label}.notices[{notice_index}]"
            entry = _object(raw_notice, notice_label)
            _exact_keys(entry, _NOTICE_KEYS, notice_label)
            _string(entry["id"], f"{notice_label}.id", maximum=128)
            container_depth = _integer(
                entry["container_depth"],
                f"{notice_label}.container_depth",
                maximum=len(containers),
            )
            notice_path = _relative_path(entry["path"], f"{notice_label}.path")
            add_selected_member(
                container_depth, notice_path, f"{notice_label}.path"
            )
            staged_path = _relative_path(entry["staged_path"], f"{notice_label}.staged_path")
            if (
                staged_path in staged_paths
                or staged_path.casefold() in staged_paths_casefolded
            ):
                raise ReleaseEvidenceError(f"{label} duplicates staged path {staged_path}")
            staged_paths.add(staged_path)
            staged_paths_casefolded.add(staged_path.casefold())
            _digest(entry["sha256"], f"{notice_label}.sha256")
            _integer(
                entry["size_bytes"],
                f"{notice_label}.size_bytes",
                minimum=1,
                maximum=MAX_NOTICE_BYTES,
            )

        providers = _array(
            artifact["providers"], f"{label}.providers", minimum=1, maximum=64
        )
        provider_ids: set[str] = set()
        for provider_index, raw_provider in enumerate(providers):
            provider_label = f"{label}.providers[{provider_index}]"
            provider = _object(raw_provider, provider_label)
            _exact_keys(provider, _PROVIDER_KEYS, provider_label)
            provider_id = _identifier(
                provider["wrapper_id"], f"{provider_label}.wrapper_id", _PROVIDER_ID
            )
            if provider_id in provider_ids:
                raise ReleaseEvidenceError(f"{label} duplicates provider {provider_id}")
            provider_ids.add(provider_id)
            if provider["reported_name"] is not None:
                _string(
                    provider["reported_name"],
                    f"{provider_label}.reported_name",
                    maximum=128,
                )
            elif lock["release_state"] == "release":
                raise ReleaseEvidenceError(
                    f"{provider_label}.reported_name is required for a release"
                )

        build = _object(artifact["build"], f"{label}.build")
        _exact_keys(build, _BUILD_KEYS, f"{label}.build")
        if not isinstance(build["source_built"], bool):
            raise ReleaseEvidenceError(f"{label}.build.source_built must be a boolean")
        _string(build["toolchain"], f"{label}.build.toolchain", maximum=256)
        for flag_index, flag in enumerate(
            _array(build["flags"], f"{label}.build.flags", maximum=128)
        ):
            _string(flag, f"{label}.build.flags[{flag_index}]", maximum=512)
        for patch_index, raw_patch in enumerate(
            _array(build["patches"], f"{label}.build.patches", maximum=64)
        ):
            patch_label = f"{label}.build.patches[{patch_index}]"
            patch = _object(raw_patch, patch_label)
            _exact_keys(patch, _PATCH_KEYS, patch_label)
            _https_url(patch["url"], f"{patch_label}.url")
            _digest(patch["sha256"], f"{patch_label}.sha256")

        licenses = _array(
            artifact["licenses"], f"{label}.licenses", minimum=1, maximum=128
        )
        license_ids: set[str] = set()
        for license_index, raw_license in enumerate(licenses):
            license_label = f"{label}.licenses[{license_index}]"
            license_entry = _object(raw_license, license_label)
            _exact_keys(license_entry, _LICENSE_KEYS, license_label)
            license_id = _string(
                license_entry["id"], f"{license_label}.id", maximum=128
            )
            if license_id in license_ids:
                raise ReleaseEvidenceError(
                    f"{license_label}.id duplicates license ID {license_id}"
                )
            license_ids.add(license_id)
            _relative_path(license_entry["notice_path"], f"{license_label}.notice_path")
            _digest(license_entry["sha256"], f"{license_label}.sha256")
            _integer(
                license_entry["size_bytes"],
                f"{license_label}.size_bytes",
                minimum=1,
                maximum=MAX_NOTICE_BYTES,
            )

        release_key = (target["os"], target["architecture"], target["variant"], flavor)
        if release_key not in release_target_keys:
            raise ReleaseEvidenceError(f"{label} target/flavor is not release-targeted")
        covered_release_targets.add(release_key)
        by_id[artifact_id] = artifact
    if lock["release_state"] == "release":
        missing_targets = sorted(_REQUIRED_RELEASE_TARGETS - covered_release_targets)
        if missing_targets:
            raise ReleaseEvidenceError(
                "native lock artifacts do not cover release targets: "
                f"{missing_targets}"
            )
    return by_id


def _parse_scalar(value: str, *, label: str) -> str:
    raw = value.strip()
    if not raw:
        raise ReleaseEvidenceError(f"{label} has an empty scalar")
    if raw.startswith('"'):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ReleaseEvidenceError(f"{label} has an invalid quoted scalar") from error
        if not isinstance(decoded, str):
            raise ReleaseEvidenceError(f"{label} must be a string")
        return decoded
    if raw.startswith("'") or " #" in raw or any(character in raw for character in "{}[]"):
        raise ReleaseEvidenceError(f"{label} uses an unsupported YAML scalar")
    return raw


def _parse_pubspec_lock(raw: bytes) -> tuple[LockedDependency, ...]:
    if b"\r" in raw or b"\t" in raw or b"\x00" in raw:
        raise ReleaseEvidenceError("pubspec.lock must use canonical LF indentation")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseEvidenceError("pubspec.lock must be UTF-8") from error

    index = 0
    while index < len(lines) and (not lines[index] or lines[index].startswith("#")):
        index += 1
    if index >= len(lines) or lines[index] != "packages:":
        raise ReleaseEvidenceError("pubspec.lock must begin with packages")
    index += 1
    dependencies: list[LockedDependency] = []
    seen: set[str] = set()
    while index < len(lines) and lines[index] != "sdks:":
        line = lines[index]
        match = re.fullmatch(r"  ([A-Za-z_][A-Za-z0-9_]*):", line)
        if match is None:
            raise ReleaseEvidenceError(f"pubspec.lock line {index + 1} is malformed")
        package_name = match.group(1)
        if package_name in seen:
            raise ReleaseEvidenceError(f"pubspec.lock duplicates package {package_name}")
        seen.add(package_name)
        index += 1
        fields: dict[str, str] = {}
        description: dict[str, str] = {}
        while index < len(lines):
            current = lines[index]
            if current == "sdks:" or re.fullmatch(r"  [A-Za-z_][A-Za-z0-9_]*:", current):
                break
            field_match = re.fullmatch(r"    ([a-z_]+):(.*)", current)
            if field_match is None:
                raise ReleaseEvidenceError(f"pubspec.lock line {index + 1} is malformed")
            field, scalar = field_match.groups()
            if field in fields:
                raise ReleaseEvidenceError(
                    f"pubspec.lock package {package_name} duplicates field {field}"
                )
            if field == "description":
                if scalar.strip():
                    raise ReleaseEvidenceError("pubspec.lock description must be a mapping")
                fields[field] = "mapping"
                index += 1
                while index < len(lines):
                    child_match = re.fullmatch(r"      ([a-z0-9_]+):(.*)", lines[index])
                    if child_match is None:
                        break
                    child, child_scalar = child_match.groups()
                    if child in description:
                        raise ReleaseEvidenceError(
                            f"pubspec.lock package {package_name} duplicates "
                            f"description field {child}"
                        )
                    description[child] = _parse_scalar(
                        child_scalar,
                        label=f"pubspec.lock package {package_name} description {child}",
                    )
                    index += 1
                continue
            fields[field] = _parse_scalar(
                scalar, label=f"pubspec.lock package {package_name} field {field}"
            )
            index += 1

        if frozenset(fields) != {"dependency", "description", "source", "version"}:
            raise ReleaseEvidenceError(
                f"pubspec.lock package {package_name} has an invalid field set"
            )
        if frozenset(description) != {"name", "sha256", "url"}:
            raise ReleaseEvidenceError(
                f"pubspec.lock package {package_name} description has an invalid field set"
            )
        if description["name"] != package_name:
            raise ReleaseEvidenceError(
                f"pubspec.lock package {package_name} description name mismatches"
            )
        if fields["dependency"] not in {"direct main", "direct dev", "transitive"}:
            raise ReleaseEvidenceError(
                f"pubspec.lock package {package_name} dependency class is invalid"
            )
        if fields["source"] != "hosted" or description["url"] != "https://pub.dev":
            raise ReleaseEvidenceError(
                f"pubspec.lock package {package_name} is not exactly hosted on pub.dev"
            )
        digest = _digest(
            description["sha256"], f"pubspec.lock package {package_name} sha256"
        )
        version = _string(
            fields["version"], f"pubspec.lock package {package_name} version", maximum=128
        )
        if _VERSION.fullmatch(version) is None:
            raise ReleaseEvidenceError(
                f"pubspec.lock package {package_name} version is invalid"
            )
        dependencies.append(
            LockedDependency(
                name=package_name,
                dependency=fields["dependency"],
                version=version,
                sha256=digest,
                source_url=description["url"],
            )
        )

    if index >= len(lines) or lines[index] != "sdks:":
        raise ReleaseEvidenceError("pubspec.lock is missing sdks")
    index += 1
    sdks: dict[str, str] = {}
    while index < len(lines):
        if not lines[index]:
            index += 1
            continue
        match = re.fullmatch(r"  ([a-z]+):(.*)", lines[index])
        if match is None:
            raise ReleaseEvidenceError(f"pubspec.lock line {index + 1} is malformed")
        name, scalar = match.groups()
        if name in sdks:
            raise ReleaseEvidenceError(f"pubspec.lock duplicates SDK {name}")
        sdks[name] = _parse_scalar(scalar, label=f"pubspec.lock SDK {name}")
        index += 1
    if frozenset(sdks) not in {frozenset({"dart"}), frozenset({"dart", "flutter"})}:
        raise ReleaseEvidenceError("pubspec.lock has an invalid SDK field set")
    if not dependencies:
        raise ReleaseEvidenceError("pubspec.lock contains no packages")
    if dependencies != sorted(dependencies, key=lambda item: item.name):
        raise ReleaseEvidenceError("pubspec.lock packages are not sorted")
    return tuple(dependencies)


def _parse_pubspec(raw: bytes) -> tuple[str, str]:
    if b"\r" in raw or b"\t" in raw or b"\x00" in raw:
        raise ReleaseEvidenceError("pubspec.yaml must use canonical LF indentation")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseEvidenceError("pubspec.yaml must be UTF-8") from error
    values: dict[str, str] = {}
    for index, line in enumerate(lines, start=1):
        if not line or line.startswith("#") or line.startswith(" "):
            continue
        match = re.fullmatch(r"([a-z_]+):(.*)", line)
        if match is None:
            continue
        key, scalar = match.groups()
        if key not in {"name", "version"}:
            continue
        if key in values:
            raise ReleaseEvidenceError(f"pubspec.yaml duplicates top-level {key}")
        values[key] = _parse_scalar(scalar, label=f"pubspec.yaml line {index}")
    if frozenset(values) != {"name", "version"}:
        raise ReleaseEvidenceError("pubspec.yaml must declare one name and version")
    if _PACKAGE_NAME.fullmatch(values["name"]) is None:
        raise ReleaseEvidenceError("pubspec.yaml package name is invalid")
    if _VERSION.fullmatch(values["version"]) is None:
        raise ReleaseEvidenceError("pubspec.yaml package version is invalid")
    return values["name"], values["version"]


def _manifest_projection(
    artifact: dict[str, Any], lock: dict[str, Any], lock_sha: str
) -> dict[str, Any]:
    target = artifact["target"]
    source = artifact["source"]
    return {
        "schema": 2,
        "artifactId": artifact["id"],
        "lock": {
            "path": NATIVE_LOCK_PATH,
            "sha256": lock_sha,
            "snapshotDate": lock["snapshot_date"],
            "releaseState": lock["release_state"],
        },
        "target": {
            "os": target["os"],
            "architecture": target["architecture"],
            "variant": target["variant"],
            "minimumOs": target["min_os"],
            "flavor": artifact["flavor"],
            "runtimeMode": artifact["runtime_mode"],
        },
        "source": {
            "url": source["url"],
            "sourceRevision": source["source_revision"],
            "archive": source["archive"],
            "sha256": source["sha256"],
            "sizeBytes": source["size_bytes"],
        },
        "containers": [
            {
                "depth": index + 1,
                "path": entry["path"],
                "archive": entry["archive"],
                "sha256": entry["sha256"],
                "sizeBytes": entry["size_bytes"],
            }
            for index, entry in enumerate(artifact["containers"])
        ],
        "payloadFiles": [
            {
                "archivePath": entry["path"],
                "stagedPath": entry["staged_path"],
                "sha256": entry["sha256"],
                "sizeBytes": entry["size_bytes"],
            }
            for entry in sorted(artifact["expected_files"], key=lambda item: item["staged_path"])
        ],
        "verifiedSymlinks": [
            {"path": entry["path"], "target": entry["target"]}
            for entry in sorted(artifact["expected_symlinks"], key=lambda item: item["path"])
        ],
        "notices": [
            {
                "id": entry["id"],
                "containerDepth": entry["container_depth"],
                "archivePath": entry["path"],
                "stagedPath": entry["staged_path"],
                "sha256": entry["sha256"],
                "sizeBytes": entry["size_bytes"],
            }
            for entry in sorted(artifact["notices"], key=lambda item: item["staged_path"])
        ],
        "claimBoundary": STAGING_CLAIM_BOUNDARY,
    }


def _validate_inspections(value: Any, artifact: dict[str, Any]) -> None:
    inspections = _array(
        value,
        "staged manifest archiveInspections",
        minimum=1,
        maximum=1 + len(artifact["containers"]),
    )
    if len(inspections) != 1 + len(artifact["containers"]):
        raise ReleaseEvidenceError("staged manifest archiveInspections depth set is incomplete")
    archives = [artifact["source"], *artifact["containers"]]
    for index, (raw_inspection, archive) in enumerate(zip(inspections, archives)):
        label = f"staged manifest archiveInspections[{index}]"
        inspection = _object(raw_inspection, label)
        _exact_keys(inspection, _INSPECTION_KEYS, label)
        if inspection["depth"] != index:
            raise ReleaseEvidenceError(f"{label}.depth is invalid")
        expected_format = "zip" if archive["archive"] == "zip" else "tar"
        if inspection["format"] != expected_format:
            raise ReleaseEvidenceError(f"{label}.format is invalid")
        member_count = _integer(
            inspection["memberCount"], f"{label}.memberCount", minimum=1, maximum=1_000_000
        )
        regular = _integer(
            inspection["regularFileCount"],
            f"{label}.regularFileCount",
            maximum=member_count,
        )
        directories = _integer(
            inspection["directoryCount"], f"{label}.directoryCount", maximum=member_count
        )
        symlinks = _integer(
            inspection["symbolicLinkCount"],
            f"{label}.symbolicLinkCount",
            maximum=member_count,
        )
        if regular + directories + symlinks != member_count:
            raise ReleaseEvidenceError(f"{label} member counts are inconsistent")
        _integer(
            inspection["compressedBytes"],
            f"{label}.compressedBytes",
            minimum=1,
            maximum=archive["size_bytes"],
        )
        _integer(
            inspection["uncompressedBytes"],
            f"{label}.uncompressedBytes",
            minimum=1,
            maximum=MAX_ARCHIVE_EXPANDED_BYTES,
        )


def _validate_staged_manifest(
    manifest: dict[str, Any],
    *,
    artifact: dict[str, Any],
    lock: dict[str, Any],
    lock_sha: str,
) -> None:
    _exact_keys(manifest, _MANIFEST_KEYS, "staged manifest")
    projection = _manifest_projection(artifact, lock, lock_sha)
    for key, expected in projection.items():
        if manifest.get(key) != expected:
            raise ReleaseEvidenceError(f"staged manifest {key} does not match the native lock")

    for key, keys in (
        ("lock", _MANIFEST_LOCK_KEYS),
        ("target", _MANIFEST_TARGET_KEYS),
        ("source", _MANIFEST_SOURCE_KEYS),
    ):
        label = f"staged manifest {key}"
        _exact_keys(_object(manifest[key], label), keys, label)
    for key, keys, minimum, maximum in (
        ("containers", _MANIFEST_CONTAINER_KEYS, 0, 8),
        ("payloadFiles", _MANIFEST_PAYLOAD_KEYS, 1, 2048),
        ("verifiedSymlinks", _MANIFEST_SYMLINK_KEYS, 0, 256),
        ("notices", _MANIFEST_NOTICE_KEYS, 1, 32),
    ):
        label = f"staged manifest {key}"
        for index, item in enumerate(
            _array(manifest[key], label, minimum=minimum, maximum=maximum)
        ):
            item_label = f"{label}[{index}]"
            _exact_keys(_object(item, item_label), keys, item_label)
    _validate_inspections(manifest["archiveInspections"], artifact)


def _stage_entries(stage: Path) -> tuple[set[str], set[str]]:
    if stage.is_symlink() or not stage.is_dir():
        raise ReleaseEvidenceError("staged directory must be a directory, not a link")
    files: set[str] = set()
    directories: set[str] = set()
    count = 0
    for current_root, directory_names, file_names in os.walk(stage, followlinks=False):
        current = Path(current_root)
        directory_names.sort()
        file_names.sort()
        count += len(directory_names) + len(file_names)
        if count > MAX_STAGE_ENTRIES:
            raise ReleaseEvidenceError("staged directory exceeds the entry bound")
        for name in directory_names:
            candidate = current / name
            relative = candidate.relative_to(stage).as_posix()
            _relative_path(relative, "staged directory path")
            if candidate.is_symlink() or not candidate.is_dir():
                raise ReleaseEvidenceError(f"staged directory {relative} must not be a link")
            directories.add(relative)
        for name in file_names:
            candidate = current / name
            relative = candidate.relative_to(stage).as_posix()
            _relative_path(relative, "staged file path")
            if candidate.is_symlink() or not candidate.is_file():
                raise ReleaseEvidenceError(f"staged file {relative} must be regular, not a link")
            files.add(relative)
    return files, directories


def _verify_staged_files(
    stage: Path, manifest: dict[str, Any]
) -> tuple[VerifiedStageFile, ...]:
    expected: dict[str, tuple[dict[str, Any], bool]] = {}
    for entry in manifest["payloadFiles"]:
        expected[entry["stagedPath"]] = (entry, False)
    for entry in manifest["notices"]:
        if entry["stagedPath"] in expected:
            raise ReleaseEvidenceError("staged manifest duplicates a staged path")
        expected[entry["stagedPath"]] = (entry, True)
    expected_names = set(expected)
    expected_names.add(STAGED_MANIFEST_NAME)

    expected_directories: set[str] = set()
    for relative in expected_names:
        parts = relative.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            expected_directories.add("/".join(parts[:index]))

    actual_names, actual_directories = _stage_entries(stage)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unknown = sorted(actual_names - expected_names)
        details: list[str] = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unknown:
            details.append(f"unknown={','.join(unknown)}")
        raise ReleaseEvidenceError(
            f"staged directory file set is not closed ({'; '.join(details)})"
        )
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        unknown = sorted(actual_directories - expected_directories)
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unknown:
            details.append(f"unknown={','.join(unknown)}")
        raise ReleaseEvidenceError(
            f"staged directory set is not closed ({'; '.join(details)})"
        )

    result: list[VerifiedStageFile] = []
    total = 0
    for relative in sorted(expected):
        entry, is_notice = expected[relative]
        maximum = MAX_NOTICE_BYTES if is_notice else MAX_STAGE_FILE_BYTES
        size_bytes, digest, sha1 = _hash_regular(
            stage.joinpath(*relative.split("/")),
            label=f"staged file {relative}",
            maximum=maximum,
        )
        total += size_bytes
        if total > MAX_STAGE_TOTAL_BYTES:
            raise ReleaseEvidenceError("staged file set exceeds the total byte bound")
        if size_bytes != entry["sizeBytes"]:
            raise ReleaseEvidenceError(f"staged file {relative} size does not match the lock")
        if digest != entry["sha256"]:
            raise ReleaseEvidenceError(f"staged file {relative} SHA-256 does not match the lock")
        result.append(
            VerifiedStageFile(
                logical_path=relative,
                sha256=digest,
                sha1=sha1,
                size_bytes=size_bytes,
                is_notice=is_notice,
                notice_id=entry.get("id") if is_notice else None,
            )
        )
    return tuple(result)


def _verify_compatibility_inputs(
    repository: Path, lock: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    compatibility = lock["onnxruntime"]["compatibility_floor"]
    result: list[dict[str, Any]] = []
    for name in ("header", "ep_header", "license"):
        entry = compatibility[name]
        contents = _repository_file(
            repository, entry["path"], maximum=MAX_COMPATIBILITY_INPUT_BYTES
        )
        if len(contents) != entry["size_bytes"]:
            raise ReleaseEvidenceError(
                f"repository source {entry['path']} size does not match native lock"
            )
        if hashlib.sha256(contents).hexdigest() != entry["sha256"]:
            raise ReleaseEvidenceError(
                f"repository source {entry['path']} SHA-256 does not match native lock"
            )
        result.append(
            {
                "kind": name,
                "path": entry["path"],
                "sha256": entry["sha256"],
                "sizeBytes": entry["size_bytes"],
                "version": entry["version"],
                "sourceRepository": entry["source_repository"],
                "sourceRef": entry["source_ref"],
            }
        )
    return tuple(result)


def _spdx_id(kind: str, identity: str) -> str:
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"SPDXRef-{kind}-{suffix}"


def _package_verification_code(files: tuple[VerifiedStageFile, ...]) -> str:
    concatenated = "".join(sorted(file.sha1 for file in files)).encode("ascii")
    return hashlib.sha1(concatenated, usedforsecurity=False).hexdigest()


def _build_sbom(
    *,
    package_name: str,
    package_version: str,
    dependencies: tuple[LockedDependency, ...],
    artifact: dict[str, Any],
    staged_files: tuple[VerifiedStageFile, ...],
    compatibility_inputs: tuple[dict[str, Any], ...],
    snapshot_date: str,
    source_manifest_sha: str,
    source_manifest_entries: int,
    lock_sha: str,
    pubspec_lock_sha: str,
    staged_manifest_sha: str,
) -> dict[str, Any]:
    namespace_identity = json.dumps(
        {
            "artifact": artifact["id"],
            "lock": lock_sha,
            "pubspecLock": pubspec_lock_sha,
            "sourceManifest": source_manifest_sha,
            "stagedManifest": staged_manifest_sha,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, namespace_identity)
    project_id = _spdx_id("Package", f"dart:{package_name}@{package_version}")
    native_id = _spdx_id("Package", f"native:{artifact['id']}")

    packages: list[dict[str, Any]] = [
        {
            "SPDXID": project_id,
            "name": package_name,
            "versionInfo": package_version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "comment": (
                f"Closed source manifest contains {source_manifest_entries} files; "
                "the project license is not inferred by this audit tool."
            ),
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": project_id,
        }
    ]
    for dependency in dependencies:
        dependency_id = _spdx_id(
            "DartPackage", f"{dependency.name}@{dependency.version}:{dependency.sha256}"
        )
        packages.append(
            {
                "SPDXID": dependency_id,
                "name": dependency.name,
                "versionInfo": dependency.version,
                "downloadLocation": f"https://pub.dev/packages/{dependency.name}",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": dependency.sha256}
                ],
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pub/{dependency.name}@{dependency.version}",
                    }
                ],
                "comment": f"pubspec.lock dependency class: {dependency.dependency}",
            }
        )
        relationships.append(
            {
                "spdxElementId": project_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": dependency_id,
            }
        )

    license_ids = sorted({entry["id"] for entry in artifact["licenses"]})
    declared_license = "MIT" if license_ids == ["MIT"] else "NOASSERTION"
    native_file_licenses = sorted(
        {
            "MIT" if file.notice_id == "MIT" else "NOASSERTION"
            for file in staged_files
        }
    )
    packages.append(
        {
            "SPDXID": native_id,
            "name": artifact["id"],
            "versionInfo": compatibility_inputs[0]["version"],
            "downloadLocation": artifact["source"]["url"],
            "filesAnalyzed": True,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": declared_license,
            "licenseInfoFromFiles": native_file_licenses,
            "copyrightText": "NOASSERTION",
            "checksums": [
                {
                    "algorithm": "SHA256",
                    "checksumValue": artifact["source"]["sha256"],
                }
            ],
            "packageVerificationCode": {
                "packageVerificationCodeValue": _package_verification_code(staged_files)
            },
            "externalRefs": [
                {
                    "referenceCategory": "OTHER",
                    "referenceType": "fonix-artifact-id",
                    "referenceLocator": artifact["id"],
                },
                {
                    "referenceCategory": "OTHER",
                    "referenceType": "vcs",
                    "referenceLocator": (
                        f"git+https://github.com/microsoft/onnxruntime.git@"
                        f"{artifact['source']['source_revision']}"
                    ),
                },
            ],
            "comment": STAGING_CLAIM_BOUNDARY,
        }
    )
    relationships.extend(
        [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": native_id,
            },
            {
                "spdxElementId": project_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": native_id,
            },
        ]
    )

    files: list[dict[str, Any]] = []
    for staged_file in staged_files:
        file_id = _spdx_id("NativeFile", staged_file.logical_path)
        files.append(
            {
                "SPDXID": file_id,
                "fileName": f"./native/staged/{staged_file.logical_path}",
                "fileTypes": ["TEXT" if staged_file.is_notice else "BINARY"],
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": staged_file.sha256},
                    {"algorithm": "SHA1", "checksumValue": staged_file.sha1},
                ],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": [
                    "MIT" if staged_file.notice_id == "MIT" else "NOASSERTION"
                ],
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": native_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file_id,
            }
        )

    for compatibility in compatibility_inputs:
        file_id = _spdx_id("OrtSource", compatibility["path"])
        files.append(
            {
                "SPDXID": file_id,
                "fileName": f"./{compatibility['path']}",
                "fileTypes": ["TEXT" if compatibility["kind"] == "license" else "SOURCE"],
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": compatibility["sha256"]}
                ],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": [
                    (
                        "MIT"
                        if compatibility["kind"] == "license"
                        and declared_license == "MIT"
                        else "NOASSERTION"
                    )
                ],
                "copyrightText": "NOASSERTION",
                "comment": (
                    f"Exact ONNX Runtime {compatibility['version']} source input from "
                    f"{compatibility['sourceRepository']} ref {compatibility['sourceRef']}."
                ),
            }
        )
        relationships.append(
            {
                "spdxElementId": file_id,
                "relationshipType": (
                    "DOCUMENTATION_OF"
                    if compatibility["kind"] == "license"
                    else "BUILD_DEPENDENCY_OF"
                ),
                "relatedSpdxElement": (
                    native_id if compatibility["kind"] == "license" else project_id
                ),
            }
        )

    files.extend(
        [
            {
                "SPDXID": "SPDXRef-SourceChecksumManifest",
                "fileName": f"./{SOURCE_MANIFEST_PATH}",
                "fileTypes": ["TEXT"],
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": source_manifest_sha}
                ],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            },
            {
                "SPDXID": "SPDXRef-NativeLock",
                "fileName": f"./{NATIVE_LOCK_PATH}",
                "fileTypes": ["TEXT"],
                "checksums": [{"algorithm": "SHA256", "checksumValue": lock_sha}],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            },
            {
                "SPDXID": "SPDXRef-PubspecLock",
                "fileName": f"./{PUBSPEC_LOCK_PATH}",
                "fileTypes": ["TEXT"],
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": pubspec_lock_sha}
                ],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            },
            {
                "SPDXID": "SPDXRef-StagedResolverManifest",
                "fileName": f"./native/staged/{STAGED_MANIFEST_NAME}",
                "fileTypes": ["TEXT"],
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": staged_manifest_sha}
                ],
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
                "copyrightText": "NOASSERTION",
            },
        ]
    )
    relationships.extend(
        [
            {
                "spdxElementId": "SPDXRef-SourceChecksumManifest",
                "relationshipType": "METAFILE_OF",
                "relatedSpdxElement": project_id,
            },
            {
                "spdxElementId": "SPDXRef-NativeLock",
                "relationshipType": "DEPENDENCY_MANIFEST_OF",
                "relatedSpdxElement": project_id,
            },
            {
                "spdxElementId": "SPDXRef-PubspecLock",
                "relationshipType": "DEPENDENCY_MANIFEST_OF",
                "relatedSpdxElement": project_id,
            },
            {
                "spdxElementId": "SPDXRef-StagedResolverManifest",
                "relationshipType": "METAFILE_OF",
                "relatedSpdxElement": native_id,
            },
        ]
    )

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"fonix-{artifact['id']}-audit-sbom",
        "documentNamespace": f"urn:uuid:{namespace}",
        "creationInfo": {
            "created": f"{snapshot_date}T00:00:00Z",
            "creators": ["Tool: fonix-generate-release-sbom-1"],
            "comment": AUDIT_CLAIM_BOUNDARY,
        },
        "documentDescribes": [project_id, native_id],
        "packages": sorted(packages, key=lambda package: package["SPDXID"]),
        "files": sorted(files, key=lambda file: file["SPDXID"]),
        "relationships": sorted(
            relationships,
            key=lambda relation: (
                relation["spdxElementId"],
                relation["relationshipType"],
                relation["relatedSpdxElement"],
            ),
        ),
        "comment": (
            f"Exact lock SHA-256 {lock_sha}; pubspec.lock SHA-256 {pubspec_lock_sha}; "
            f"resolver manifest SHA-256 {staged_manifest_sha}. {AUDIT_CLAIM_BOUNDARY}"
        ),
    }


def _release_blockers(repository: Path, lock: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    license_path = repository / "LICENSE"
    if not license_path.exists():
        blockers.append("root-license-missing")
    elif license_path.is_symlink() or not license_path.is_file():
        blockers.append("root-license-not-regular")
    else:
        blockers.append("project-license-identity-not-assessed")
    if lock["release_state"] != "release":
        blockers.append(f"native-lock-release-state-{lock['release_state']}")
    if lock["shim"]["source_revision"] is None:
        blockers.append("shim-source-revision-unpinned")
    blockers.extend(
        [
            "provider-qualification-not-assessed",
            "signing-evidence-not-assessed",
            "distribution-not-authorized",
        ]
    )
    return blockers


def generate_evidence(
    repository: Path,
    staged_directory: Path,
    artifact_id: str,
) -> EvidenceDocuments:
    """Validate every input and return deterministic in-memory documents."""

    if repository.is_symlink() or not repository.is_dir():
        raise ReleaseEvidenceError("repository must be a directory, not a link")
    artifact_id = _identifier(artifact_id, "selected artifact ID", _IDENTIFIER)

    try:
        source_manifest_sha = source_checksum_manifest.check_manifest(
            repository, repository / SOURCE_MANIFEST_PATH
        )
        source_entries = source_checksum_manifest.parse_manifest(
            _read_regular(
                repository / SOURCE_MANIFEST_PATH,
                label="source checksum manifest",
                maximum=source_checksum_manifest.MAX_MANIFEST_BYTES,
            )
        )
    except source_checksum_manifest.SourceManifestError as error:
        raise ReleaseEvidenceError(f"source checksum manifest is invalid: {error}") from error

    lock, lock_raw = _json_file(
        repository / NATIVE_LOCK_PATH, label="native lock", maximum=MAX_LOCK_BYTES
    )
    artifacts = _validate_lock(lock)
    artifact = artifacts.get(artifact_id)
    if artifact is None:
        raise ReleaseEvidenceError("selected artifact ID is absent from the native lock")
    lock_sha = hashlib.sha256(lock_raw).hexdigest()

    pubspec_raw = _repository_file(repository, PUBSPEC_PATH, maximum=MAX_PUBSPEC_BYTES)
    package_name, package_version = _parse_pubspec(pubspec_raw)
    pubspec_lock_raw = _repository_file(
        repository, PUBSPEC_LOCK_PATH, maximum=MAX_PUBSPEC_LOCK_BYTES
    )
    dependencies = _parse_pubspec_lock(pubspec_lock_raw)
    pubspec_lock_sha = hashlib.sha256(pubspec_lock_raw).hexdigest()
    compatibility_inputs = _verify_compatibility_inputs(repository, lock)

    manifest_path = staged_directory / STAGED_MANIFEST_NAME
    manifest, manifest_raw = _json_file(
        manifest_path, label="staged resolver manifest", maximum=MAX_JSON_BYTES
    )
    _validate_staged_manifest(
        manifest, artifact=artifact, lock=lock, lock_sha=lock_sha
    )
    staged_files = _verify_staged_files(staged_directory, manifest)
    staged_manifest_sha = hashlib.sha256(manifest_raw).hexdigest()

    sbom = _build_sbom(
        package_name=package_name,
        package_version=package_version,
        dependencies=dependencies,
        artifact=artifact,
        staged_files=staged_files,
        compatibility_inputs=compatibility_inputs,
        snapshot_date=lock["snapshot_date"],
        source_manifest_sha=source_manifest_sha,
        source_manifest_entries=len(source_entries),
        lock_sha=lock_sha,
        pubspec_lock_sha=pubspec_lock_sha,
        staged_manifest_sha=staged_manifest_sha,
    )
    sbom_raw = _canonical_json(sbom)
    blockers = _release_blockers(repository, lock)
    metadata = {
        "schemaVersion": 1,
        "claimBoundary": AUDIT_CLAIM_BOUNDARY,
        "generatedAt": f"{lock['snapshot_date']}T00:00:00Z",
        "releaseState": lock["release_state"],
        "releaseReadiness": {
            "ready": False,
            "blockers": blockers,
            "explanation": (
                "This offline audit deliberately cannot establish signing, provider "
                "qualification, publication, or distribution authorization."
            ),
        },
        "sourceManifest": {
            "path": SOURCE_MANIFEST_PATH,
            "sha256": source_manifest_sha,
            "entryCount": len(source_entries),
        },
        "dartLock": {
            "path": PUBSPEC_LOCK_PATH,
            "sha256": pubspec_lock_sha,
            "dependencyCount": len(dependencies),
        },
        "nativeSelection": {
            "artifactId": artifact["id"],
            "flavor": artifact["flavor"],
            "runtimeMode": artifact["runtime_mode"],
            "target": artifact["target"],
            "lockPath": NATIVE_LOCK_PATH,
            "lockSha256": lock_sha,
            "resolverManifestSha256": staged_manifest_sha,
            "source": {
                "url": artifact["source"]["url"],
                "sourceRevision": artifact["source"]["source_revision"],
                "sha256": artifact["source"]["sha256"],
                "sizeBytes": artifact["source"]["size_bytes"],
            },
            "payloadFiles": [
                {
                    "stagedPath": file.logical_path,
                    "sha256": file.sha256,
                    "sizeBytes": file.size_bytes,
                }
                for file in staged_files
                if not file.is_notice
            ],
            "notices": [
                {
                    "id": file.notice_id,
                    "stagedPath": file.logical_path,
                    "sha256": file.sha256,
                    "sizeBytes": file.size_bytes,
                }
                for file in staged_files
                if file.is_notice
            ],
            "providers": [
                {
                    "wrapperId": provider["wrapper_id"],
                    "reportedName": provider["reported_name"],
                }
                for provider in artifact["providers"]
            ],
        },
        "sbom": {
            "format": "SPDX",
            "version": "2.3",
            "sha256": hashlib.sha256(sbom_raw).hexdigest(),
        },
    }
    return EvidenceDocuments(sbom=sbom, metadata=metadata)


def _validate_output(path: Path, label: str) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ReleaseEvidenceError(f"{label} parent must be a directory, not a link")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ReleaseEvidenceError(f"{label} must be a regular file")


def _atomic_write(path: Path, contents: bytes) -> None:
    _validate_output(path, "evidence output")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def write_evidence(documents: EvidenceDocuments, sbom_output: Path, metadata_output: Path) -> None:
    if sbom_output == metadata_output:
        raise ReleaseEvidenceError("SBOM and metadata outputs must be distinct")
    _validate_output(sbom_output, "SBOM output")
    _validate_output(metadata_output, "metadata output")
    _atomic_write(sbom_output, documents.sbom_bytes())
    _atomic_write(metadata_output, documents.metadata_bytes())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--staged-directory", type=Path, required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--sbom-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument(
        "--require-release-ready",
        action="store_true",
        help=(
            "Fail after writing audit documents unless external release readiness "
            "has been established. This tool currently always fails this gate."
        ),
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        documents = generate_evidence(
            arguments.repository, arguments.staged_directory, arguments.artifact_id
        )
        write_evidence(documents, arguments.sbom_output, arguments.metadata_output)
        sbom_sha = documents.metadata["sbom"]["sha256"]
        print(
            f"generated offline SPDX 2.3 audit SBOM sha256={sbom_sha}; "
            "releaseReady=false"
        )
        if arguments.require_release_ready:
            print(
                "release readiness failed closed: "
                + ", ".join(documents.metadata["releaseReadiness"]["blockers"]),
                file=sys.stderr,
            )
            return 1
        return 0
    except ReleaseEvidenceError as error:
        print(f"release evidence error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
