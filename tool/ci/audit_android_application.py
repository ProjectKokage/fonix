#!/usr/bin/env python3
"""Audit a final Fonix Android APK or AAB without executing it."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from typing import Any, Mapping, NamedTuple, Sequence
import xml.etree.ElementTree as ET
import zipfile

from android_gate_common import (
    AndroidGateCommonError,
    regular_file,
    run_bounded,
    sha256_file,
    strict_json,
)


ABI = "arm64-v8a"
ARTIFACT_ID = "onnxruntime-1.27.1-android-arm64-v8a-cpu"
ARTIFACT_SOURCE_SHA256 = (
    "9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383"
)
ORT_SHA256 = "a7579e85ecc5465840d352c35f355e5b7418d36901670d36afd46555304458c2"
ORT_SIZE_BYTES = 27_983_536
MODEL_SHA256 = "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10"
MODEL_SIZE_BYTES = 130
MODEL_METADATA_SHA256 = (
    "20ab7b1150a37516159c714abca3cb1cb6e48692da0c77f21c46e15336f71449"
)
MODEL_METADATA_SIZE_BYTES = 687
XNNPACK_MODEL_SHA256 = (
    "c75aaa93b0e1ae09e0bb12ddee5786c2fda803dfa5f565234e2b65e238623482"
)
XNNPACK_MODEL_SIZE_BYTES = 311
XNNPACK_METADATA_SHA256 = (
    "76eb202b02211f34ee64ca6a88a2a24d9f58f334136fa7f1b7fcfe2e763f30ab"
)
XNNPACK_METADATA_SIZE_BYTES = 1_298
APPLICATION_ID = "dev.fonix.fonix_reference"
MAIN_ACTIVITY = f"{APPLICATION_ID}.MainActivity"
PRIVATE_RECEIVER_PERMISSION = (
    f"{APPLICATION_ID}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"
)
MINIMUM_SDK = 24
TARGET_SDK = 36
BUNDLETOOL_VERSION = "1.18.3"
BUNDLETOOL_SIZE_BYTES = 32_520_401
BUNDLETOOL_SHA256 = (
    "a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29"
)
EXPECTED_NATIVE_NAMES = frozenset(
    {"libapp.so", "libflutter.so", "libfonix_shim.so", "libonnxruntime.so"}
)
EXPECTED_AAB_SIGNATURE_METADATA = frozenset(
    {"META-INF/ANDROIDD.SF", "META-INF/ANDROIDD.RSA"}
)
EXPECTED_SHIM_BUILD_ID = (
    "android-owner-application-source-bundled-artifact-"
    "onnxruntime-1.27.1-android-arm64-v8a-cpu"
)
EXPECTED_ARCHIVE_INSPECTIONS = (
    {
        "depth": 0,
        "format": "zip",
        "memberCount": 49,
        "regularFileCount": 49,
        "directoryCount": 0,
        "symbolicLinkCount": 0,
        "compressedBytes": 135_144_668,
        "uncompressedBytes": 213_482_687,
    },
    {
        "depth": 1,
        "format": "zip",
        "memberCount": 36,
        "regularFileCount": 25,
        "directoryCount": 11,
        "symbolicLinkCount": 0,
        "compressedBytes": 44_516_967,
        "uncompressedBytes": 117_418_063,
    },
)
CLAIM_BOUNDARY = (
    "Successful staging proves exact archive and member bytes only. It does not "
    "prove loading, linking, provider registration, inference, packaging, "
    "signing, or target-device support."
)

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_NATIVE_BYTES = 512 * 1024 * 1024
MAX_XML_BYTES = 256 * 1024
MAX_LOCK_BYTES = 1024 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NS}}}"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL_LINE = re.compile(
    r"^\s*\d+:\s+\S+\s+\d+\s+\S+\s+"
    r"(?P<binding>GLOBAL|WEAK)\s+\S+\s+(?P<section>\S+)\s+"
    r"(?P<name>\S+)\s*$"
)
_PEM_CERTIFICATE = re.compile(
    r"-----BEGIN CERTIFICATE-----\s*"
    r"(?P<body>[A-Za-z0-9+/=\s]+?)"
    r"\s*-----END CERTIFICATE-----"
)


class _ArtifactSnapshot(NamedTuple):
    source: Path
    snapshot: Path
    source_device: int
    source_inode: int
    snapshot_device: int
    snapshot_inode: int
    size: int
    sha256: str


class _ModelAssetContract(NamedTuple):
    model_path: str
    model_size_bytes: int
    model_sha256: str
    metadata_path: str
    metadata_size_bytes: int
    metadata_sha256: str
    manifest: Mapping[str, object]


MODEL_ASSET_CONTRACTS = (
    _ModelAssetContract(
        model_path="assets/models/mul_1.onnx",
        model_size_bytes=MODEL_SIZE_BYTES,
        model_sha256=MODEL_SHA256,
        metadata_path="assets/models/model.json",
        metadata_size_bytes=MODEL_METADATA_SIZE_BYTES,
        metadata_sha256=MODEL_METADATA_SHA256,
        manifest={
            "schemaVersion": 1,
            "id": f"mul-1-sha256-{MODEL_SHA256}",
            "path": "assets/models/mul_1.onnx",
            "source": "onnxruntime/test/testdata/mul_1.onnx",
            "sourceRevision": "v1.27.1",
            "sha256": MODEL_SHA256,
            "sizeBytes": MODEL_SIZE_BYTES,
            "input": {
                "name": "X",
                "elementType": "float32",
                "shape": [3, 2],
                "values": [1, 2, 3, 4, 5, 6],
            },
            "output": {
                "name": "Y",
                "elementType": "float32",
                "shape": [3, 2],
                "values": [1, 4, 9, 16, 25, 36],
            },
            "claimBoundary": (
                "API and packaging smoke only; not representative performance "
                "or provider qualification."
            ),
        },
    ),
    _ModelAssetContract(
        model_path="assets/models/xnnpack_matmul.onnx",
        model_size_bytes=XNNPACK_MODEL_SIZE_BYTES,
        model_sha256=XNNPACK_MODEL_SHA256,
        metadata_path="assets/models/xnnpack_matmul.json",
        metadata_size_bytes=XNNPACK_METADATA_SIZE_BYTES,
        metadata_sha256=XNNPACK_METADATA_SHA256,
        manifest={
            "schemaVersion": 1,
            "id": f"xnnpack-matmul-sha256-{XNNPACK_MODEL_SHA256}",
            "path": "assets/models/xnnpack_matmul.onnx",
            "generator": "assets/models/generate_xnnpack_matmul.py",
            "generatorVersion": "xnnpack-matmul-v1",
            "sha256": XNNPACK_MODEL_SHA256,
            "sizeBytes": XNNPACK_MODEL_SIZE_BYTES,
            "onnxIrVersion": 8,
            "opset": 17,
            "operator": "MatMul",
            "input": {
                "name": "input",
                "elementType": "float32",
                "shape": [3, 2],
                "values": [1, 2, 3, 4, 5, 6],
            },
            "initializer": {
                "name": "weight",
                "elementType": "float32",
                "shape": [2, 2],
                "values": [1, 2, 3, 4],
            },
            "matrixMultiplication": {
                "leftShape": [3, 2],
                "rightShape": [2, 2],
            },
            "output": {
                "name": "output",
                "elementType": "float32",
                "shape": [3, 2],
                "values": [7, 10, 15, 22, 23, 34],
            },
            "referencePolicy": "exact-float32",
            "claimBoundary": (
                "Functional provider-assignment and CPU-parity fixture only; "
                "not a performance, thermal, physical-device, or "
                "provider-qualification workload."
            ),
        },
    ),
)


class AndroidApplicationAuditError(RuntimeError):
    """The final Android package violates the closed Fonix contract."""


def _fail_common(error: AndroidGateCommonError) -> AndroidApplicationAuditError:
    return AndroidApplicationAuditError(str(error))


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _copy_artifact_snapshot(source: Path, destination: Path) -> _ArtifactSnapshot:
    try:
        source = regular_file(source, "final Android artifact", maximum=MAX_ARCHIVE_BYTES)
        source_path_metadata = source.lstat()
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            source_before = os.fstat(input_stream.fileno())
            if (
                not stat.S_ISREG(source_before.st_mode)
                or source_before.st_dev != source_path_metadata.st_dev
                or source_before.st_ino != source_path_metadata.st_ino
            ):
                raise AndroidApplicationAuditError(
                    "final Android artifact changed before snapshotting"
                )
            while True:
                chunk = input_stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise AndroidApplicationAuditError(
                        "final Android artifact exceeds its snapshot bound"
                    )
                if output_stream.write(chunk) != len(chunk):
                    raise AndroidApplicationAuditError(
                        "could not write the complete Android artifact snapshot"
                    )
                digest.update(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
            source_after = os.fstat(input_stream.fileno())
        if size != source_before.st_size or _metadata_identity(
            source_after
        ) != _metadata_identity(source_before):
            raise AndroidApplicationAuditError(
                "final Android artifact changed while being snapshotted"
            )
        destination.chmod(0o400)
        snapshot_metadata = destination.lstat()
        if not stat.S_ISREG(snapshot_metadata.st_mode) or snapshot_metadata.st_size != size:
            raise AndroidApplicationAuditError(
                "Android artifact snapshot publication failed"
            )
        return _ArtifactSnapshot(
            source=source,
            snapshot=destination,
            source_device=source_before.st_dev,
            source_inode=source_before.st_ino,
            snapshot_device=snapshot_metadata.st_dev,
            snapshot_inode=snapshot_metadata.st_ino,
            size=size,
            sha256=digest.hexdigest(),
        )
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error
    except OSError as error:
        raise AndroidApplicationAuditError(
            "could not create an immutable Android artifact snapshot"
        ) from error


def _hash_stable_file(
    path: Path,
    label: str,
    *,
    expected_device: int,
    expected_inode: int,
) -> tuple[int, str]:
    path_before = path.lstat()
    if (
        not stat.S_ISREG(path_before.st_mode)
        or path_before.st_dev != expected_device
        or path_before.st_ino != expected_inode
    ):
        raise AndroidApplicationAuditError(f"{label} was replaced during the audit")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        file_before = os.fstat(stream.fileno())
        if (
            file_before.st_dev != expected_device
            or file_before.st_ino != expected_inode
        ):
            raise AndroidApplicationAuditError(
                f"{label} changed before final verification"
            )
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise AndroidApplicationAuditError(
                    f"{label} exceeds its final verification bound"
                )
            digest.update(chunk)
        file_after = os.fstat(stream.fileno())
    path_after = path.lstat()
    if (
        _metadata_identity(file_after) != _metadata_identity(file_before)
        or path_after.st_dev != expected_device
        or path_after.st_ino != expected_inode
    ):
        raise AndroidApplicationAuditError(f"{label} changed during final verification")
    return size, digest.hexdigest()


def _verify_artifact_snapshot(snapshot: _ArtifactSnapshot) -> None:
    try:
        source_size, source_digest = _hash_stable_file(
            snapshot.source,
            "final Android artifact",
            expected_device=snapshot.source_device,
            expected_inode=snapshot.source_inode,
        )
        snapshot_size, snapshot_digest = _hash_stable_file(
            snapshot.snapshot,
            "Android artifact snapshot",
            expected_device=snapshot.snapshot_device,
            expected_inode=snapshot.snapshot_inode,
        )
    except OSError as error:
        raise AndroidApplicationAuditError(
            "could not revalidate the Android artifact snapshot"
        ) from error
    expected = (snapshot.size, snapshot.sha256)
    if (source_size, source_digest) != expected:
        raise AndroidApplicationAuditError(
            "final Android artifact bytes changed during the audit"
        )
    if (snapshot_size, snapshot_digest) != expected:
        raise AndroidApplicationAuditError(
            "Android artifact snapshot bytes changed during the audit"
        )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AndroidApplicationAuditError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AndroidApplicationAuditError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise AndroidApplicationAuditError(
            f"{label} has a non-closed field set; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _canonical_archive_path(value: str) -> str | None:
    if not value or value.startswith("/") or "\\" in value:
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _archive_index(artifact: Path) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(artifact)
    except (OSError, zipfile.BadZipFile) as error:
        raise AndroidApplicationAuditError("final Android artifact is not a ZIP") from error
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        archive.close()
        raise AndroidApplicationAuditError("final Android artifact has too many entries")
    result: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        raw = info.filename.rstrip("/")
        canonical = _canonical_archive_path(raw)
        if canonical is None:
            archive.close()
            raise AndroidApplicationAuditError(
                f"final Android artifact contains a non-canonical path: {info.filename!r}"
            )
        if canonical in result:
            archive.close()
            raise AndroidApplicationAuditError(
                f"final Android artifact contains duplicate path {canonical!r}"
            )
        result[canonical] = info
    return archive, result


def _audit_aab_module_set(index: Mapping[str, zipfile.ZipInfo]) -> list[str]:
    allowed_roots = {"base", "BUNDLE-METADATA", "META-INF", "BundleConfig.pb"}
    unexpected_roots = sorted(
        {
            PurePosixPath(path).parts[0]
            for path in index
            if PurePosixPath(path).parts[0] not in allowed_roots
        }
    )
    modules = {
        parts[0]
        for path in index
        for parts in [PurePosixPath(path).parts]
        if len(parts) == 3
        and parts[1] == "manifest"
        and parts[2] == "AndroidManifest.xml"
    }
    if modules != {"base"} or unexpected_roots:
        raise AndroidApplicationAuditError(
            "AAB module set must be exactly ['base']; "
            f"found modules={sorted(modules)}, unexpectedRoots={unexpected_roots}"
        )
    return ["base"]


def _is_jar_signature_metadata(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) != 2 or parts[0].upper() != "META-INF":
        return False
    name = parts[1].upper()
    return (
        name.endswith((".SF", ".RSA", ".DSA", ".EC"))
        or name.startswith("SIG-")
    )


def _parse_jar_manifest_sections(data: bytes) -> dict[str, str]:
    if not data or len(data) > MAX_ASSET_BYTES:
        raise AndroidApplicationAuditError("AAB JAR manifest size is outside its bound")
    if not data.endswith(b"\r\n") or b"\n" in data.replace(b"\r\n", b""):
        raise AndroidApplicationAuditError("AAB JAR manifest line endings changed")
    physical_lines = data[:-2].split(b"\r\n")
    logical_lines: list[bytes] = []
    for line in physical_lines:
        if line.startswith(b" "):
            if not logical_lines or logical_lines[-1] == b"":
                raise AndroidApplicationAuditError(
                    "AAB JAR manifest has an invalid continuation"
                )
            logical_lines[-1] += line[1:]
        else:
            logical_lines.append(line)

    sections: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in [*logical_lines, b""]:
        if raw_line == b"":
            if current:
                sections.append(current)
                current = {}
            continue
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AndroidApplicationAuditError(
                "AAB JAR manifest is not UTF-8"
            ) from error
        key, separator, value = line.partition(": ")
        if not separator or not key or key in current:
            raise AndroidApplicationAuditError(
                "AAB JAR manifest contains an invalid or duplicate attribute"
            )
        current[key] = value
    if not sections or sections[0].get("Manifest-Version") != "1.0":
        raise AndroidApplicationAuditError("AAB JAR manifest header changed")

    signed_entries: dict[str, str] = {}
    for section in sections[1:]:
        if set(section) != {"Name", "SHA-256-Digest"}:
            raise AndroidApplicationAuditError(
                "AAB JAR manifest entry has a non-closed field set"
            )
        name = section["Name"]
        if _canonical_archive_path(name) != name or name in signed_entries:
            raise AndroidApplicationAuditError(
                "AAB JAR manifest contains an invalid or duplicate entry name"
            )
        encoded_digest = section["SHA-256-Digest"]
        try:
            decoded_digest = base64.b64decode(encoded_digest, validate=True)
        except (binascii.Error, ValueError) as error:
            raise AndroidApplicationAuditError(
                "AAB JAR manifest contains an invalid SHA-256 digest"
            ) from error
        if len(decoded_digest) != hashlib.sha256().digest_size:
            raise AndroidApplicationAuditError(
                "AAB JAR manifest contains a non-SHA-256 digest"
            )
        signed_entries[name] = encoded_digest
    return signed_entries


def _audit_aab_signed_entries(
    index: Mapping[str, zipfile.ZipInfo], manifest_bytes: bytes
) -> int:
    signed_entries = _parse_jar_manifest_sections(manifest_bytes)
    signature_metadata = {
        path
        for path, info in index.items()
        if not info.is_dir() and _is_jar_signature_metadata(path)
    }
    if signature_metadata != EXPECTED_AAB_SIGNATURE_METADATA:
        raise AndroidApplicationAuditError(
            "AAB JAR signature metadata inventory changed; "
            f"found={sorted(signature_metadata)}"
        )
    expected_entries = {
        path
        for path, info in index.items()
        if not info.is_dir()
        and path != "META-INF/MANIFEST.MF"
        and not _is_jar_signature_metadata(path)
    }
    actual_entries = set(signed_entries)
    if actual_entries != expected_entries:
        missing = sorted(expected_entries - actual_entries)
        extra = sorted(actual_entries - expected_entries)
        raise AndroidApplicationAuditError(
            "every signable AAB entry must be covered by the signed manifest; "
            f"missing={missing[:8]}, extra={extra[:8]}"
        )
    return len(actual_entries) + 1  # META-INF/MANIFEST.MF is signed by the block.


def _read_member(
    archive: zipfile.ZipFile,
    index: Mapping[str, zipfile.ZipInfo],
    path: str,
    *,
    label: str,
    maximum: int,
) -> bytes:
    info = index.get(path)
    if info is None or info.is_dir():
        raise AndroidApplicationAuditError(f"missing {label}: {path}")
    if info.flag_bits & 0x1:
        raise AndroidApplicationAuditError(f"{label} must not be encrypted")
    if info.file_size <= 0 or info.file_size > maximum:
        raise AndroidApplicationAuditError(f"{label} size is outside its bound")
    if info.compress_size <= 0 or info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
        raise AndroidApplicationAuditError(f"{label} has a suspicious compression ratio")
    chunks: list[bytes] = []
    consumed = 0
    with archive.open(info, "r") as stream:
        while True:
            chunk = stream.read(min(1024 * 1024, maximum + 1 - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum:
                raise AndroidApplicationAuditError(f"{label} exceeds its bound")
            chunks.append(chunk)
    if consumed != info.file_size:
        raise AndroidApplicationAuditError(f"{label} changed while being read")
    return b"".join(chunks)


def _artifact_member(kind: str, relative: str) -> str:
    prefix = "" if kind == "apk" else "base/"
    return f"{prefix}{relative}"


def _validate_exact_json(
    actual: object,
    expected: object,
    label: str,
    *,
    location: str = "$",
) -> None:
    if type(actual) is not type(expected):
        raise AndroidApplicationAuditError(
            f"{label} {location} has the wrong JSON type; "
            f"expected={type(expected).__name__}, actual={type(actual).__name__}"
        )
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        _exact_keys(actual, set(expected), f"{label} {location}")
        for key, expected_value in expected.items():
            _validate_exact_json(
                actual[key],
                expected_value,
                label,
                location=f"{location}.{key}",
            )
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        if len(actual) != len(expected):
            raise AndroidApplicationAuditError(
                f"{label} {location} has the wrong array length"
            )
        for index, (actual_value, expected_value) in enumerate(
            zip(actual, expected, strict=True)
        ):
            _validate_exact_json(
                actual_value,
                expected_value,
                label,
                location=f"{location}[{index}]",
            )
        return
    if actual != expected:
        raise AndroidApplicationAuditError(
            f"{label} {location} has the wrong value"
        )


def _validate_model_manifest(
    value: object,
    contract: _ModelAssetContract,
    model_bytes: bytes,
) -> None:
    label = f"packaged metadata {contract.metadata_path}"
    _validate_exact_json(value, contract.manifest, label)
    manifest = _object(value, label)
    model_sha256 = hashlib.sha256(model_bytes).hexdigest()
    if (
        manifest["path"] != contract.model_path
        or manifest["sizeBytes"] != len(model_bytes)
        or manifest["sha256"] != model_sha256
    ):
        raise AndroidApplicationAuditError(
            f"{label} is not bound to its packaged model"
        )


def _audit_model_assets(
    archive: zipfile.ZipFile,
    index: Mapping[str, zipfile.ZipInfo],
    kind: str,
) -> dict[str, object]:
    asset_root = _artifact_member(
        kind, "assets/flutter_assets/assets/models"
    )
    expected_members = {
        _artifact_member(
            kind, f"assets/flutter_assets/{relative_path}"
        )
        for contract in MODEL_ASSET_CONTRACTS
        for relative_path in (contract.model_path, contract.metadata_path)
    }
    actual_members = {
        path
        for path, info in index.items()
        if path.startswith(f"{asset_root}/") and not info.is_dir()
    }
    unexpected_directories = sorted(
        path
        for path, info in index.items()
        if path.startswith(f"{asset_root}/") and info.is_dir()
    )
    if actual_members != expected_members or unexpected_directories:
        raise AndroidApplicationAuditError(
            "packaged model asset inventory changed; "
            f"missing={sorted(expected_members - actual_members)}, "
            f"extra={sorted(actual_members - expected_members)}, "
            f"directories={unexpected_directories}"
        )

    report: dict[str, object] = {}
    for contract in MODEL_ASSET_CONTRACTS:
        model_member = _artifact_member(
            kind, f"assets/flutter_assets/{contract.model_path}"
        )
        metadata_member = _artifact_member(
            kind, f"assets/flutter_assets/{contract.metadata_path}"
        )
        model_bytes = _read_member(
            archive,
            index,
            model_member,
            label=f"packaged model {contract.model_path}",
            maximum=contract.model_size_bytes,
        )
        metadata_bytes = _read_member(
            archive,
            index,
            metadata_member,
            label=f"packaged model metadata {contract.metadata_path}",
            maximum=contract.metadata_size_bytes,
        )
        if (
            len(model_bytes) != contract.model_size_bytes
            or hashlib.sha256(model_bytes).hexdigest() != contract.model_sha256
        ):
            raise AndroidApplicationAuditError(
                f"packaged model identity changed: {contract.model_path}"
            )
        if (
            len(metadata_bytes) != contract.metadata_size_bytes
            or hashlib.sha256(metadata_bytes).hexdigest()
            != contract.metadata_sha256
        ):
            raise AndroidApplicationAuditError(
                "packaged model metadata identity changed: "
                f"{contract.metadata_path}"
            )
        try:
            manifest = strict_json(
                metadata_bytes,
                f"packaged model metadata {contract.metadata_path}",
                maximum=contract.metadata_size_bytes,
            )
        except AndroidGateCommonError as error:
            raise _fail_common(error) from error
        _validate_model_manifest(manifest, contract, model_bytes)
        report[contract.model_path] = {
            "sizeBytes": contract.model_size_bytes,
            "sha256": contract.model_sha256,
            "metadataPath": contract.metadata_path,
            "metadataSizeBytes": contract.metadata_size_bytes,
            "metadataSha256": contract.metadata_sha256,
        }
    return report


def _load_lock(repository: Path) -> tuple[dict[str, Any], str, dict[str, Any]]:
    lock_path = repository / "native/versions.lock.yaml"
    try:
        data = regular_file(lock_path, "native artifact lock", maximum=MAX_LOCK_BYTES).read_bytes()
        value = strict_json(data, "native artifact lock", maximum=MAX_LOCK_BYTES)
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error
    lock = _object(value, "native artifact lock")
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(lock.get("artifacts"), "lock artifacts")):
        artifact = _object(raw, f"lock artifacts[{index}]")
        target = _object(artifact.get("target"), f"lock artifacts[{index}].target")
        if (
            artifact.get("id") == ARTIFACT_ID
            and target.get("os") == "android"
            and target.get("architecture") == ABI
            and target.get("variant") == "default"
            and artifact.get("flavor") == "cpu"
            and artifact.get("runtime_mode") == "bundled"
        ):
            candidates.append(artifact)
    if len(candidates) != 1:
        raise AndroidApplicationAuditError(
            "native lock must select exactly one Android arm64 CPU artifact"
        )
    artifact = candidates[0]
    source = _object(artifact.get("source"), "locked artifact source")
    if source.get("sha256") != ARTIFACT_SOURCE_SHA256:
        raise AndroidApplicationAuditError("locked Android source SHA-256 changed")
    expected_files = _array(artifact.get("expected_files"), "locked expected files")
    ort = [
        _object(raw, f"locked expected files[{index}]")
        for index, raw in enumerate(expected_files)
        if isinstance(raw, dict) and raw.get("staged_path") == "libonnxruntime.so"
    ]
    if len(ort) != 1 or ort[0].get("sha256") != ORT_SHA256:
        raise AndroidApplicationAuditError("locked Android ORT payload identity changed")
    return lock, hashlib.sha256(data).hexdigest(), artifact


def _indexed(values: list[Any], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(values):
        entry = _object(raw, f"{label}[{index}]")
        identity = entry.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise AndroidApplicationAuditError(f"{label} has an invalid or duplicate {key}")
        result[identity] = entry
    return result


def _validate_archive_inspections(value: object) -> None:
    inspections = _array(value, "archive inspections")
    expected_keys = {
        "depth",
        "format",
        "memberCount",
        "regularFileCount",
        "directoryCount",
        "symbolicLinkCount",
        "compressedBytes",
        "uncompressedBytes",
    }
    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(inspections):
        entry = _object(raw, f"archive inspections[{index}]")
        _exact_keys(entry, expected_keys, f"archive inspections[{index}]")
        for key in expected_keys - {"format"}:
            if type(entry[key]) is not int or entry[key] < 0:
                raise AndroidApplicationAuditError(
                    f"archive inspections[{index}].{key} must be a non-negative integer"
                )
        if not isinstance(entry["format"], str):
            raise AndroidApplicationAuditError(
                f"archive inspections[{index}].format must be a string"
            )
        normalized.append(entry)
    if normalized != list(EXPECTED_ARCHIVE_INSPECTIONS):
        raise AndroidApplicationAuditError(
            "Android archive inspection evidence differs from the exact locked archive"
        )


def _validate_packaged_manifest(
    manifest_bytes: bytes,
    notice_bytes: bytes,
    *,
    lock: dict[str, Any],
    lock_sha256: str,
    artifact: dict[str, Any],
) -> dict[str, object]:
    try:
        value = strict_json(
            manifest_bytes, "packaged Fonix manifest", maximum=MAX_ASSET_BYTES
        )
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error
    manifest = _object(value, "packaged Fonix manifest")
    _exact_keys(
        manifest,
        {
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
        },
        "packaged Fonix manifest",
    )
    if manifest.get("schema") != 2 or manifest.get("artifactId") != ARTIFACT_ID:
        raise AndroidApplicationAuditError("packaged Fonix artifact identity changed")
    if manifest.get("claimBoundary") != CLAIM_BOUNDARY:
        raise AndroidApplicationAuditError("packaged Fonix claim boundary changed")
    expected_lock = {
        "path": "native/versions.lock.yaml",
        "sha256": lock_sha256,
        "snapshotDate": lock.get("snapshot_date"),
        "releaseState": lock.get("release_state"),
    }
    if manifest.get("lock") != expected_lock:
        raise AndroidApplicationAuditError("packaged manifest lock identity differs")
    target = _object(artifact.get("target"), "locked artifact target")
    expected_target = {
        "os": "android",
        "architecture": ABI,
        "variant": "default",
        "minimumOs": target.get("min_os"),
        "flavor": "cpu",
        "runtimeMode": "bundled",
    }
    if manifest.get("target") != expected_target:
        raise AndroidApplicationAuditError("packaged manifest target differs from lock")
    source = _object(artifact.get("source"), "locked artifact source")
    expected_source = {
        "url": source.get("url"),
        "sourceRevision": source.get("source_revision"),
        "sha256": source.get("sha256"),
        "sizeBytes": source.get("size_bytes"),
        "archive": source.get("archive"),
    }
    if manifest.get("source") != expected_source:
        raise AndroidApplicationAuditError("packaged manifest source differs from lock")
    containers = _array(artifact.get("containers"), "locked containers")
    expected_containers = [
        {
            "path": entry.get("path"),
            "sha256": entry.get("sha256"),
            "sizeBytes": entry.get("size_bytes"),
            "archive": entry.get("archive"),
            "depth": index + 1,
        }
        for index, raw in enumerate(containers)
        for entry in [_object(raw, f"locked containers[{index}]")]
    ]
    if manifest.get("containers") != expected_containers:
        raise AndroidApplicationAuditError("packaged manifest containers differ from lock")

    locked_files = _array(artifact.get("expected_files"), "locked expected files")
    expected_payloads = {
        entry.get("staged_path"): {
            "archivePath": entry.get("path"),
            "stagedPath": entry.get("staged_path"),
            "sha256": entry.get("sha256"),
            "sizeBytes": entry.get("size_bytes"),
        }
        for index, raw in enumerate(locked_files)
        for entry in [_object(raw, f"locked expected files[{index}]")]
    }
    if _indexed(_array(manifest.get("payloadFiles"), "manifest payloads"), "stagedPath", "manifest payloads") != expected_payloads:
        raise AndroidApplicationAuditError("packaged manifest payloads differ from lock")

    locked_notices = _array(artifact.get("notices"), "locked notices")
    expected_notices = {
        entry.get("staged_path"): {
            "id": entry.get("id"),
            "containerDepth": entry.get("container_depth"),
            "archivePath": entry.get("path"),
            "stagedPath": entry.get("staged_path"),
            "sha256": entry.get("sha256"),
            "sizeBytes": entry.get("size_bytes"),
        }
        for index, raw in enumerate(locked_notices)
        for entry in [_object(raw, f"locked notices[{index}]")]
    }
    actual_notices = _indexed(
        _array(manifest.get("notices"), "manifest notices"),
        "stagedPath",
        "manifest notices",
    )
    if actual_notices != expected_notices:
        raise AndroidApplicationAuditError("packaged manifest notices differ from lock")
    notice = expected_notices.get("notices/ThirdPartyNotices.txt")
    if notice is None:
        raise AndroidApplicationAuditError("locked notice inventory is incomplete")
    if len(notice_bytes) != notice["sizeBytes"] or hashlib.sha256(notice_bytes).hexdigest() != notice["sha256"]:
        raise AndroidApplicationAuditError("packaged ThirdPartyNotices bytes differ from lock")
    if manifest.get("verifiedSymlinks") != []:
        raise AndroidApplicationAuditError("Android packaged manifest must not contain symlinks")
    _validate_archive_inspections(manifest.get("archiveInspections"))
    return {
        "artifactId": ARTIFACT_ID,
        "sourceSha256": ARTIFACT_SOURCE_SHA256,
        "thirdPartyNoticesSha256": notice["sha256"],
    }


def _load_native_verifier(repository: Path) -> Any:
    path = repository / "templates/android/verify_native_libs.py"
    try:
        path = regular_file(path, "Android native verifier", maximum=2 * 1024 * 1024)
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error
    spec = importlib.util.spec_from_file_location("fonix_android_final_verifier", path)
    if spec is None or spec.loader is None:
        raise AndroidApplicationAuditError("could not load Android native verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit_native_libraries(repository: Path, artifact: Path) -> dict[str, Any]:
    verifier = _load_native_verifier(repository)
    report = verifier.inspect(artifact)
    arguments = argparse.Namespace(
        policy="fonix-standalone-final",
        require_final_single_ort=False,
        reject_multiple_ort_owners=False,
        reject_multiple_libcxx_owners=False,
        forbid_ort_in=[],
        require_abi=[ABI],
        require_16k_page_alignment=True,
    )
    errors = verifier.validate([report], arguments)
    if errors:
        raise AndroidApplicationAuditError(
            "final native-library audit failed: " + "; ".join(errors)
        )
    present_abis = {entry["abi"] for entry in report["libraries"]}
    if present_abis != {ABI}:
        raise AndroidApplicationAuditError(
            f"final package ABI set must be exactly {ABI}; found {sorted(present_abis)}"
        )
    native_names = [entry["name"] for entry in report["libraries"]]
    if len(native_names) != len(EXPECTED_NATIVE_NAMES) or set(native_names) != set(
        EXPECTED_NATIVE_NAMES
    ):
        raise AndroidApplicationAuditError(
            "final reference native-library inventory changed; "
            f"found {sorted(native_names)}"
        )
    by_name = {
        name: [entry for entry in report["libraries"] if entry["name"] == name]
        for name in ("libonnxruntime.so", "libfonix_shim.so")
    }
    if any(len(entries) != 1 for entries in by_name.values()):
        raise AndroidApplicationAuditError("final package native ownership is incomplete")
    return report


def _audit_runtime_identity(
    repository: Path,
    reference_runtime: Path,
    packaged_runtime: bytes,
    packaged_entry: Mapping[str, Any],
) -> dict[str, object]:
    try:
        reference_runtime = regular_file(
            reference_runtime,
            "lock-verified reference runtime",
            maximum=ORT_SIZE_BYTES,
        )
        size, digest = sha256_file(
            reference_runtime,
            "lock-verified reference runtime",
            maximum=ORT_SIZE_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error
    if size != ORT_SIZE_BYTES or digest != ORT_SHA256:
        raise AndroidApplicationAuditError(
            "reference runtime differs from the exact locked ORT payload"
        )
    if len(packaged_runtime) != ORT_SIZE_BYTES:
        raise AndroidApplicationAuditError("packaged ONNX Runtime size changed")

    verifier = _load_native_verifier(repository)
    with reference_runtime.open("rb") as stream:
        reference_elf, reference_error = verifier.inspect_elf(stream, size, ABI)
    packaged_elf = packaged_entry.get("elf")
    if reference_error is not None or not isinstance(reference_elf, dict):
        raise AndroidApplicationAuditError(
            f"locked reference runtime is not a valid {ABI} ELF: {reference_error}"
        )
    if not isinstance(packaged_elf, dict):
        raise AndroidApplicationAuditError("packaged ONNX Runtime ELF metadata is missing")

    identity_fields = (
        "class",
        "machine",
        "programHeaderCount",
        "pageSize16KiBCompatible",
        "soname",
        "needed",
    )
    reference_identity = {field: reference_elf.get(field) for field in identity_fields}
    packaged_identity = {field: packaged_elf.get(field) for field in identity_fields}
    if packaged_identity != reference_identity:
        raise AndroidApplicationAuditError(
            "packaged ONNX Runtime ELF identity differs from the locked payload"
        )
    reference_segments = reference_elf.get("loadSegments")
    packaged_segments = packaged_elf.get("loadSegments")
    if packaged_segments != reference_segments or not isinstance(reference_segments, list):
        raise AndroidApplicationAuditError(
            "packaged ONNX Runtime ordered PT_LOAD bytes differ from the locked payload"
        )
    packaged_digest = hashlib.sha256(packaged_runtime).hexdigest()
    if packaged_entry.get("sha256") != packaged_digest:
        raise AndroidApplicationAuditError(
            "native inventory digest is not bound to the packaged ONNX Runtime"
        )
    return {
        "referenceSha256": ORT_SHA256,
        "packagedSha256": packaged_digest,
        "sizeBytes": ORT_SIZE_BYTES,
        "elfIdentity": reference_identity,
        "orderedLoadSegments": reference_segments,
    }


def _parse_exports(output: str) -> set[str]:
    exports: set[str] = set()
    for line in output.splitlines():
        match = _SYMBOL_LINE.match(line)
        if match is None or match.group("section") == "UND":
            continue
        name = match.group("name").split("@", 1)[0]
        if name:
            exports.add(name)
    return exports


def _expected_shim_exports(repository: Path) -> set[str]:
    try:
        data = regular_file(
            repository / "src/fonix_exports.map",
            "Fonix export allowlist",
            maximum=64 * 1024,
        ).read_text(encoding="utf-8")
    except (AndroidGateCommonError, UnicodeDecodeError) as error:
        raise AndroidApplicationAuditError("could not read Fonix export allowlist") from error
    names = set(re.findall(r"^\s+(dort_[a-z0-9_]+);\s*$", data, re.MULTILINE))
    if not names or data.count("global:") != 1 or data.count("local:") != 1:
        raise AndroidApplicationAuditError("Fonix export allowlist is not closed")
    return names


def _audit_exports(
    repository: Path,
    readelf: Path,
    shim_bytes: bytes,
    ort_bytes: bytes,
) -> dict[str, object]:
    try:
        readelf = regular_file(readelf, "NDK llvm-readelf")
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error
    if not stat.S_IMODE(readelf.stat().st_mode) & 0o111:
        raise AndroidApplicationAuditError("NDK llvm-readelf is not executable")
    with tempfile.TemporaryDirectory(prefix="fonix-android-symbols-") as temporary:
        root = Path(temporary)
        shim = root / "libfonix_shim.so"
        ort = root / "libonnxruntime.so"
        shim.write_bytes(shim_bytes)
        ort.write_bytes(ort_bytes)
        try:
            shim_output = run_bounded(
                (
                    str(readelf),
                    "--elf-output-style=GNU",
                    "--dyn-symbols",
                    str(shim),
                ),
                operation="Fonix shim dynamic-symbol audit",
                maximum_output=512 * 1024,
            ).stdout
            ort_output = run_bounded(
                (
                    str(readelf),
                    "--elf-output-style=GNU",
                    "--dyn-symbols",
                    str(ort),
                ),
                operation="ONNX Runtime dynamic-symbol audit",
                maximum_output=2 * 1024 * 1024,
            ).stdout
        except AndroidGateCommonError as error:
            raise _fail_common(error) from error
    shim_exports = _parse_exports(shim_output)
    expected = _expected_shim_exports(repository)
    allowed_version_symbols = {"FONIX_DORT_1.0"}
    if shim_exports - allowed_version_symbols != expected:
        raise AndroidApplicationAuditError("Fonix shim exported-symbol set changed")
    ort_exports = _parse_exports(ort_output)
    if "OrtGetApiBase" not in ort_exports:
        raise AndroidApplicationAuditError("ONNX Runtime does not export OrtGetApiBase")
    return {
        "shimExportCount": len(expected),
        "ortGetApiBase": "present",
    }


def _normalize_component(name: str) -> str:
    if name.startswith("."):
        return APPLICATION_ID + name
    if "." not in name:
        return f"{APPLICATION_ID}.{name}"
    return name


def _exact_attributes(
    element: ET.Element,
    expected: Mapping[str, str],
    label: str,
) -> None:
    if element.attrib != dict(expected):
        raise AndroidApplicationAuditError(f"{label} attributes changed")


def _empty_element(element: ET.Element, label: str) -> None:
    if list(element):
        raise AndroidApplicationAuditError(f"{label} must not contain child elements")


def _decoded_integer(value: str | None, expected: int, label: str) -> None:
    try:
        actual = int(value or "", 0)
    except ValueError as error:
        raise AndroidApplicationAuditError(f"{label} is not a decoded integer") from error
    if actual != expected:
        raise AndroidApplicationAuditError(f"{label} changed")


def _single_child(parent: ET.Element, tag: str, label: str) -> ET.Element:
    matches = parent.findall(tag)
    if len(matches) != 1:
        raise AndroidApplicationAuditError(f"{label} must appear exactly once")
    return matches[0]


def _audit_intent_filter(
    element: ET.Element,
    *,
    action: str,
    category: str | None = None,
    label: str,
) -> None:
    _exact_attributes(element, {}, label)
    actions = element.findall("action")
    categories = element.findall("category")
    expected_children = 1 + (1 if category is not None else 0)
    if len(list(element)) != expected_children or len(actions) != 1:
        raise AndroidApplicationAuditError(f"{label} children changed")
    _exact_attributes(actions[0], {ANDROID + "name": action}, f"{label} action")
    _empty_element(actions[0], f"{label} action")
    if category is None:
        if categories:
            raise AndroidApplicationAuditError(f"{label} category changed")
    elif len(categories) != 1:
        raise AndroidApplicationAuditError(f"{label} category changed")
    else:
        _exact_attributes(
            categories[0], {ANDROID + "name": category}, f"{label} category"
        )
        _empty_element(categories[0], f"{label} category")


def _audit_manifest_xml(xml_text: str) -> dict[str, object]:
    encoded = xml_text.encode("utf-8")
    if len(encoded) > MAX_XML_BYTES or "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise AndroidApplicationAuditError("decoded Android manifest is outside its bound")
    try:
        root = ET.fromstring(encoded)
    except ET.ParseError as error:
        raise AndroidApplicationAuditError("decoded Android manifest is invalid XML") from error
    if root.tag != "manifest":
        raise AndroidApplicationAuditError("Android application ID changed")
    _exact_attributes(
        root,
        {
            ANDROID + "versionCode": "1",
            ANDROID + "versionName": "0.1.0",
            ANDROID + "compileSdkVersion": "36",
            ANDROID + "compileSdkVersionCodename": "16",
            "package": APPLICATION_ID,
            "platformBuildVersionCode": "36",
            "platformBuildVersionName": "16",
        },
        "Android manifest",
    )
    if [child.tag for child in root] != [
        "uses-sdk",
        "queries",
        "permission",
        "uses-permission",
        "application",
    ]:
        raise AndroidApplicationAuditError("Android manifest top-level inventory changed")
    uses_sdk = _single_child(root, "uses-sdk", "uses-sdk")
    _exact_attributes(
        uses_sdk,
        {
            ANDROID + "minSdkVersion": str(MINIMUM_SDK),
            ANDROID + "targetSdkVersion": str(TARGET_SDK),
        },
        "uses-sdk",
    )
    _empty_element(uses_sdk, "uses-sdk")

    queries = _single_child(root, "queries", "queries")
    _exact_attributes(queries, {}, "queries")
    query_intent = _single_child(queries, "intent", "process-text query")
    if len(list(queries)) != 1 or [child.tag for child in query_intent] != [
        "action",
        "data",
    ]:
        raise AndroidApplicationAuditError("process-text query inventory changed")
    _exact_attributes(query_intent, {}, "process-text query")
    query_action = query_intent[0]
    query_data = query_intent[1]
    _exact_attributes(
        query_action,
        {ANDROID + "name": "android.intent.action.PROCESS_TEXT"},
        "process-text query action",
    )
    _exact_attributes(
        query_data,
        {ANDROID + "mimeType": "text/plain"},
        "process-text query data",
    )
    _empty_element(query_action, "process-text query action")
    _empty_element(query_data, "process-text query data")

    permission = _single_child(root, "permission", "private receiver permission")
    if set(permission.attrib) != {ANDROID + "name", ANDROID + "protectionLevel"}:
        raise AndroidApplicationAuditError("private receiver permission attributes changed")
    if permission.get(ANDROID + "name") != PRIVATE_RECEIVER_PERMISSION:
        raise AndroidApplicationAuditError("private receiver permission name changed")
    _decoded_integer(
        permission.get(ANDROID + "protectionLevel"),
        2,
        "private receiver permission protection level",
    )
    _empty_element(permission, "private receiver permission")
    uses_permission = _single_child(root, "uses-permission", "uses-permission")
    _exact_attributes(
        uses_permission,
        {ANDROID + "name": PRIVATE_RECEIVER_PERMISSION},
        "uses-permission",
    )
    _empty_element(uses_permission, "uses-permission")

    application = _single_child(root, "application", "application")
    expected_application_attributes = {
        ANDROID + "label": "Fonix Reference",
        ANDROID + "name": "android.app.Application",
        ANDROID + "allowBackup": "false",
        ANDROID + "extractNativeLibs": "false",
        ANDROID + "usesCleartextTraffic": "false",
        ANDROID + "appComponentFactory": "androidx.core.app.CoreComponentFactory",
    }
    if set(application.attrib) != set(expected_application_attributes) | {
        ANDROID + "icon"
    }:
        raise AndroidApplicationAuditError("application attributes changed")
    for key, expected in expected_application_attributes.items():
        if application.get(key) != expected:
            raise AndroidApplicationAuditError("application attributes changed")
    if application.get(ANDROID + "icon") not in {
        "@ref/0x7f0a0000",
        "@mipmap/ic_launcher",
    }:
        raise AndroidApplicationAuditError("application icon reference changed")
    if [child.tag for child in application] != [
        "activity",
        "meta-data",
        "uses-library",
        "uses-library",
        "provider",
        "receiver",
    ]:
        raise AndroidApplicationAuditError("application component inventory changed")

    activity = application[0]
    name = activity.get(ANDROID + "name")
    if not isinstance(name, str) or _normalize_component(name) != MAIN_ACTIVITY:
        raise AndroidApplicationAuditError("reference launcher activity changed")
    expected_activity_attributes = {
        ANDROID + "name": MAIN_ACTIVITY,
        ANDROID + "exported": "true",
        ANDROID + "taskAffinity": "",
        ANDROID + "hardwareAccelerated": "true",
    }
    if set(activity.attrib) != set(expected_activity_attributes) | {
        ANDROID + "theme",
        ANDROID + "launchMode",
        ANDROID + "configChanges",
        ANDROID + "windowSoftInputMode",
    }:
        raise AndroidApplicationAuditError("launcher activity attributes changed")
    for key, expected in expected_activity_attributes.items():
        if activity.get(key) != expected:
            raise AndroidApplicationAuditError("launcher activity attributes changed")
    if activity.get(ANDROID + "theme") not in {
        "@ref/0x7f0c0000",
        "@style/LaunchTheme",
    }:
        raise AndroidApplicationAuditError("launcher theme reference changed")
    _decoded_integer(activity.get(ANDROID + "launchMode"), 1, "launcher launch mode")
    _decoded_integer(
        activity.get(ANDROID + "configChanges"),
        0x40003FB4,
        "launcher config changes",
    )
    _decoded_integer(
        activity.get(ANDROID + "windowSoftInputMode"),
        0x10,
        "launcher soft-input mode",
    )
    if [child.tag for child in activity] != ["meta-data", "intent-filter"]:
        raise AndroidApplicationAuditError("launcher child inventory changed")
    normal_theme = activity[0]
    if set(normal_theme.attrib) != {ANDROID + "name", ANDROID + "resource"}:
        raise AndroidApplicationAuditError("normal-theme metadata attributes changed")
    if normal_theme.get(ANDROID + "name") != "io.flutter.embedding.android.NormalTheme":
        raise AndroidApplicationAuditError("normal-theme metadata name changed")
    if normal_theme.get(ANDROID + "resource") not in {
        "@ref/0x7f0c0001",
        "@style/NormalTheme",
    }:
        raise AndroidApplicationAuditError("normal-theme resource changed")
    _empty_element(normal_theme, "normal-theme metadata")
    _audit_intent_filter(
        activity[1],
        action="android.intent.action.MAIN",
        category="android.intent.category.LAUNCHER",
        label="launcher intent filter",
    )

    _exact_attributes(
        application[1],
        {ANDROID + "name": "flutterEmbedding", ANDROID + "value": "2"},
        "Flutter embedding metadata",
    )
    _empty_element(application[1], "Flutter embedding metadata")
    for element, library_name in zip(
        application[2:4],
        ("androidx.window.extensions", "androidx.window.sidecar"),
        strict=True,
    ):
        _exact_attributes(
            element,
            {ANDROID + "name": library_name, ANDROID + "required": "false"},
            f"uses-library {library_name}",
        )
        _empty_element(element, f"uses-library {library_name}")

    provider = application[4]
    _exact_attributes(
        provider,
        {
            ANDROID + "name": "androidx.startup.InitializationProvider",
            ANDROID + "authorities": f"{APPLICATION_ID}.androidx-startup",
            ANDROID + "exported": "false",
        },
        "AndroidX startup provider",
    )
    if [child.tag for child in provider] != ["meta-data", "meta-data"]:
        raise AndroidApplicationAuditError("AndroidX startup metadata inventory changed")
    expected_initializers = (
        "androidx.lifecycle.ProcessLifecycleInitializer",
        "androidx.profileinstaller.ProfileInstallerInitializer",
    )
    for element, initializer in zip(provider, expected_initializers, strict=True):
        _exact_attributes(
            element,
            {ANDROID + "name": initializer, ANDROID + "value": "androidx.startup"},
            f"AndroidX initializer {initializer}",
        )
        _empty_element(element, f"AndroidX initializer {initializer}")

    receiver = application[5]
    _exact_attributes(
        receiver,
        {
            ANDROID + "name": "androidx.profileinstaller.ProfileInstallReceiver",
            ANDROID + "permission": "android.permission.DUMP",
            ANDROID + "enabled": "true",
            ANDROID + "exported": "true",
            ANDROID + "directBootAware": "false",
        },
        "profile installer receiver",
    )
    expected_actions = (
        "androidx.profileinstaller.action.INSTALL_PROFILE",
        "androidx.profileinstaller.action.SKIP_FILE",
        "androidx.profileinstaller.action.SAVE_PROFILE",
        "androidx.profileinstaller.action.BENCHMARK_OPERATION",
    )
    if [child.tag for child in receiver] != ["intent-filter"] * len(expected_actions):
        raise AndroidApplicationAuditError("profile installer receiver filters changed")
    for element, action in zip(receiver, expected_actions, strict=True):
        _audit_intent_filter(
            element,
            action=action,
            label=f"profile installer filter {action}",
        )
    return {
        "applicationId": APPLICATION_ID,
        "mainActivity": MAIN_ACTIVITY,
        "minimumSdk": MINIMUM_SDK,
        "targetSdk": TARGET_SDK,
        "debuggable": False,
        "allowBackup": False,
        "requestedPermissions": [PRIVATE_RECEIVER_PERMISSION],
        "networkPermissions": [],
        "queries": ["android.intent.action.PROCESS_TEXT:text/plain"],
        "components": {
            "activities": [MAIN_ACTIVITY],
            "providers": ["androidx.startup.InitializationProvider"],
            "receivers": ["androidx.profileinstaller.ProfileInstallReceiver"],
        },
    }


def _decoded_manifest(
    artifact: Path,
    kind: str,
    *,
    apkanalyzer: Path | None,
    java: Path | None,
    bundletool: Path | None,
) -> str:
    try:
        if kind == "apk":
            if apkanalyzer is None:
                raise AndroidApplicationAuditError("--apkanalyzer is required for APK audit")
            tool = regular_file(apkanalyzer, "apkanalyzer")
            return run_bounded(
                (str(tool), "manifest", "print", str(artifact)),
                operation="APK manifest decode",
                maximum_output=MAX_XML_BYTES,
            ).stdout
        if java is None or bundletool is None:
            raise AndroidApplicationAuditError(
                "--java and --bundletool are required for AAB audit"
            )
        java = regular_file(java, "Java executable")
        bundletool = regular_file(
            bundletool, "bundletool jar", maximum=BUNDLETOOL_SIZE_BYTES
        )
        bundletool_size, bundletool_digest = sha256_file(
            bundletool, "bundletool jar", maximum=BUNDLETOOL_SIZE_BYTES
        )
        if (
            bundletool_size != BUNDLETOOL_SIZE_BYTES
            or bundletool_digest != BUNDLETOOL_SHA256
        ):
            raise AndroidApplicationAuditError("bundletool identity changed")
        version = run_bounded(
            (str(java), "-jar", str(bundletool), "version"),
            operation="bundletool version check",
            maximum_output=256 * 1024,
        ).stdout.strip()
        if version != BUNDLETOOL_VERSION:
            raise AndroidApplicationAuditError("bundletool version changed")
        return run_bounded(
            (
                str(java),
                "-jar",
                str(bundletool),
                "dump",
                "manifest",
                f"--bundle={artifact}",
                "--module=base",
            ),
            operation="AAB manifest decode",
            maximum_output=MAX_XML_BYTES,
        ).stdout
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error


def _parse_keytool_signer_certificate(output: str) -> str:
    signer_numbers = re.findall(r"(?m)^Signer #(\d+):\s*$", output)
    if signer_numbers != ["1"]:
        raise AndroidApplicationAuditError(
            "AAB must have exactly one unambiguous signer"
        )
    certificates = list(_PEM_CERTIFICATE.finditer(output))
    if not certificates:
        raise AndroidApplicationAuditError(
            "AAB signer certificate could not be extracted"
        )
    encoded = re.sub(r"\s+", "", certificates[0].group("body"))
    try:
        certificate = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise AndroidApplicationAuditError(
            "AAB signer certificate is not valid DER"
        ) from error
    if not certificate:
        raise AndroidApplicationAuditError("AAB signer certificate is empty")
    return hashlib.sha256(certificate).hexdigest()


def _aab_signer_certificate(jarsigner: Path, artifact: Path) -> str:
    keytool = jarsigner.with_name("keytool")
    try:
        keytool = regular_file(keytool, "keytool")
        if not stat.S_IMODE(keytool.stat().st_mode) & 0o111:
            raise AndroidApplicationAuditError("keytool is not executable")
        output = run_bounded(
            (str(keytool), "-printcert", "-rfc", "-jarfile", str(artifact)),
            operation="AAB signer certificate extraction",
            maximum_output=512 * 1024,
        ).stdout
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error
    return _parse_keytool_signer_certificate(output)


def _audit_signature_and_alignment(
    artifact: Path,
    kind: str,
    *,
    zipalign: Path | None,
    apksigner: Path | None,
    jarsigner: Path | None,
    aab_index: Mapping[str, zipfile.ZipInfo] | None,
    aab_manifest_bytes: bytes | None,
) -> dict[str, object]:
    try:
        if kind == "apk":
            if zipalign is None or apksigner is None:
                raise AndroidApplicationAuditError(
                    "--zipalign and --apksigner are required for APK audit"
                )
            zipalign = regular_file(zipalign, "zipalign")
            apksigner = regular_file(apksigner, "apksigner")
            run_bounded(
                (str(zipalign), "-c", "-P", "16", "4", str(artifact)),
                operation="APK ZIP alignment audit",
                maximum_output=256 * 1024,
            )
            output = run_bounded(
                (str(apksigner), "verify", "--verbose", "--print-certs", str(artifact)),
                operation="APK signature audit",
                maximum_output=512 * 1024,
            ).stdout
            if re.search(r"Verified using v(?:2|3|4) scheme[^:]*:\s*true", output) is None:
                raise AndroidApplicationAuditError("APK lacks a verified modern signature")
            certificate_digests = re.findall(
                r"Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]{64})",
                output,
            )
            signer_numbers = re.findall(
                r"Signer #(\d+) certificate SHA-256 digest:", output
            )
            if len(certificate_digests) != 1 or signer_numbers != ["1"]:
                raise AndroidApplicationAuditError(
                    "APK must have exactly one unambiguous signing certificate digest"
                )
            return {
                "signature": "verified-development",
                "certificateSha256": certificate_digests[0].lower(),
                "zipAlignment16KiB": "passed",
            }
        if jarsigner is None:
            raise AndroidApplicationAuditError("--jarsigner is required for AAB audit")
        if aab_index is None or aab_manifest_bytes is None:
            raise AndroidApplicationAuditError(
                "AAB signed-entry inventory is required for signature audit"
            )
        signed_entry_count = _audit_aab_signed_entries(
            aab_index, aab_manifest_bytes
        )
        jarsigner = regular_file(jarsigner, "jarsigner")
        output = run_bounded(
            (str(jarsigner), "-verify", "-verbose", "-certs", str(artifact)),
            operation="AAB JAR signature audit",
            maximum_output=512 * 1024,
        )
        combined = f"{output.stdout}\n{output.stderr}".lower()
        if combined.count("jar verified.") != 1:
            raise AndroidApplicationAuditError("AAB JAR signature was not verified")
        unsigned_warnings = (
            "contains unsigned entries",
            "unsigned entries which have not been integrity-checked",
            "unsigned entry which has not been integrity-checked",
        )
        if any(warning in combined for warning in unsigned_warnings):
            raise AndroidApplicationAuditError(
                "AAB JAR signature audit reported unsigned entries"
            )
        certificate_sha256 = _aab_signer_certificate(jarsigner, artifact)
        return {
            "signature": "verified-development",
            "certificateSha256": certificate_sha256,
            "signedEntryCount": signed_entry_count,
            "zipAlignment16KiB": "not-applicable-to-aab",
        }
    except AndroidGateCommonError as error:
        raise _fail_common(error) from error


def _audit_android_application_snapshot(
    *,
    repository: Path,
    artifact: Path,
    reported_artifact: Path,
    reference_runtime: Path,
    readelf: Path,
    apkanalyzer: Path | None,
    java: Path | None,
    bundletool: Path | None,
    zipalign: Path | None,
    apksigner: Path | None,
    jarsigner: Path | None,
) -> dict[str, object]:
    try:
        repository = Path(repository).resolve(strict=True)
        artifact = regular_file(
            Path(artifact).resolve(strict=True),
            "final Android artifact",
            maximum=MAX_ARCHIVE_BYTES,
        )
        artifact_size, artifact_sha256 = sha256_file(
            artifact, "final Android artifact", maximum=MAX_ARCHIVE_BYTES
        )
    except (OSError, AndroidGateCommonError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _fail_common(error) from error
        raise AndroidApplicationAuditError("could not resolve Android audit inputs") from error
    kind = artifact.suffix.lower().lstrip(".")
    if kind not in {"apk", "aab"}:
        raise AndroidApplicationAuditError("final Android artifact must be APK or AAB")
    lock, lock_sha256, locked_artifact = _load_lock(repository)
    archive, index = _archive_index(artifact)
    bundle_modules: list[str] | None = None
    aab_manifest_bytes: bytes | None = None
    try:
        if kind == "aab":
            bundle_modules = _audit_aab_module_set(index)
            aab_manifest_bytes = _read_member(
                archive,
                index,
                "META-INF/MANIFEST.MF",
                label="AAB JAR manifest",
                maximum=MAX_ASSET_BYTES,
            )
        manifest_bytes = _read_member(
            archive,
            index,
            _artifact_member(
                kind,
                "assets/flutter_assets/assets/fonix/fonix-native-artifact-manifest.json",
            ),
            label="packaged Fonix manifest",
            maximum=MAX_ASSET_BYTES,
        )
        notice_bytes = _read_member(
            archive,
            index,
            _artifact_member(
                kind, "assets/flutter_assets/assets/fonix/ThirdPartyNotices.txt"
            ),
            label="packaged ThirdPartyNotices",
            maximum=MAX_ASSET_BYTES,
        )
        model_assets = _audit_model_assets(archive, index, kind)
        shim_path = _artifact_member(kind, f"lib/{ABI}/libfonix_shim.so")
        ort_path = _artifact_member(kind, f"lib/{ABI}/libonnxruntime.so")
        shim_bytes = _read_member(
            archive, index, shim_path, label="packaged Fonix shim", maximum=MAX_NATIVE_BYTES
        )
        ort_bytes = _read_member(
            archive, index, ort_path, label="packaged ONNX Runtime", maximum=MAX_NATIVE_BYTES
        )
    finally:
        archive.close()
    if EXPECTED_SHIM_BUILD_ID.encode("ascii") not in shim_bytes:
        raise AndroidApplicationAuditError("packaged shim lacks the Android owner/artifact identity")
    packaged_metadata = _validate_packaged_manifest(
        manifest_bytes,
        notice_bytes,
        lock=lock,
        lock_sha256=lock_sha256,
        artifact=locked_artifact,
    )
    native_report = _audit_native_libraries(repository, artifact)
    ort_entry = next(
        entry for entry in native_report["libraries"] if entry["name"] == "libonnxruntime.so"
    )
    runtime_identity = _audit_runtime_identity(
        repository,
        Path(reference_runtime).resolve(strict=True),
        ort_bytes,
        ort_entry,
    )
    exports = _audit_exports(repository, readelf, shim_bytes, ort_bytes)
    manifest = _audit_manifest_xml(
        _decoded_manifest(
            artifact,
            kind,
            apkanalyzer=apkanalyzer,
            java=java,
            bundletool=bundletool,
        )
    )
    signing = _audit_signature_and_alignment(
        artifact,
        kind,
        zipalign=zipalign,
        apksigner=apksigner,
        jarsigner=jarsigner,
        aab_index=index if kind == "aab" else None,
        aab_manifest_bytes=aab_manifest_bytes,
    )
    shim_entry = next(
        entry for entry in native_report["libraries"] if entry["name"] == "libfonix_shim.so"
    )
    report: dict[str, object] = {
        "schemaVersion": 1,
        "result": "passed",
        "artifact": str(reported_artifact),
        "artifactKind": kind,
        "artifactSizeBytes": artifact_size,
        "artifactSha256": artifact_sha256,
        "abi": ABI,
        "packagedMetadata": packaged_metadata,
        "modelSha256": MODEL_SHA256,
        "modelAssets": model_assets,
        "ortSha256": runtime_identity["packagedSha256"],
        "ortReferenceSha256": ORT_SHA256,
        "ortRuntimeIdentity": runtime_identity,
        "shimSha256": shim_entry["sha256"],
        "shimBuildId": EXPECTED_SHIM_BUILD_ID,
        "nativeLibraryAudit": {
            "policy": "fonix-standalone-final",
            "libraryCount": len(native_report["libraries"]),
            "static16KiBAlignment": "passed",
        },
        "exports": exports,
        "manifest": manifest,
        "signing": signing,
        "bundleModules": bundle_modules,
        "claimBoundary": (
            "This report proves only the inspected final package bytes. It does not "
            "prove installation, target execution, device page size, inference, "
            "distribution signing, or release approval."
        ),
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise AndroidApplicationAuditError("Android audit report exceeds its bound")
    return report


def audit_android_application(
    *,
    repository: Path,
    artifact: Path,
    reference_runtime: Path,
    readelf: Path,
    apkanalyzer: Path | None,
    java: Path | None,
    bundletool: Path | None,
    zipalign: Path | None,
    apksigner: Path | None,
    jarsigner: Path | None,
) -> dict[str, object]:
    try:
        repository = Path(repository).resolve(strict=True)
        artifact = regular_file(
            Path(artifact).resolve(strict=True),
            "final Android artifact",
            maximum=MAX_ARCHIVE_BYTES,
        )
    except (OSError, AndroidGateCommonError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _fail_common(error) from error
        raise AndroidApplicationAuditError(
            "could not resolve Android audit inputs"
        ) from error
    kind = artifact.suffix.lower().lstrip(".")
    if kind not in {"apk", "aab"}:
        raise AndroidApplicationAuditError("final Android artifact must be APK or AAB")
    with tempfile.TemporaryDirectory(prefix="fonix-android-artifact-snapshot-") as temporary:
        snapshot = _copy_artifact_snapshot(
            artifact, Path(temporary) / f"artifact.{kind}"
        )
        report = _audit_android_application_snapshot(
            repository=repository,
            artifact=snapshot.snapshot,
            reported_artifact=artifact,
            reference_runtime=reference_runtime,
            readelf=readelf,
            apkanalyzer=apkanalyzer,
            java=java,
            bundletool=bundletool,
            zipalign=zipalign,
            apksigner=apksigner,
            jarsigner=jarsigner,
        )
        if (
            report.get("artifactSizeBytes") != snapshot.size
            or report.get("artifactSha256") != snapshot.sha256
        ):
            raise AndroidApplicationAuditError(
                "Android audit passes were not bound to the artifact snapshot"
            )
        _verify_artifact_snapshot(snapshot)
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--reference-runtime", type=Path, required=True)
    parser.add_argument("--readelf", type=Path, required=True)
    parser.add_argument("--apkanalyzer", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--bundletool", type=Path)
    parser.add_argument("--zipalign", type=Path)
    parser.add_argument("--apksigner", type=Path)
    parser.add_argument("--jarsigner", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = audit_android_application(
            repository=arguments.repository,
            artifact=arguments.artifact,
            reference_runtime=arguments.reference_runtime,
            readelf=arguments.readelf,
            apkanalyzer=arguments.apkanalyzer,
            java=arguments.java,
            bundletool=arguments.bundletool,
            zipalign=arguments.zipalign,
            apksigner=arguments.apksigner,
            jarsigner=arguments.jarsigner,
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (AndroidApplicationAuditError, AndroidGateCommonError, OSError) as error:
        print(f"audit_android_application: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
