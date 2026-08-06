#!/usr/bin/env python3
"""Generate a fail-closed Fonix/sherpa Android compatibility manifest.

The generator consumes exact input and final artifacts plus target-host test
receipts.  It does not download dependencies or infer compatibility from
Gradle metadata.  Native bytes are inspected with the repository-owned ELF
verifier, and every selected non-platform final loaded segment is tied to its
source.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import types
from typing import Any, Iterable
from urllib.parse import urlparse
import zipfile


sys.dont_write_bytecode = True
REPOSITORY = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY / "templates/android/verify_native_libs.py"
LOAD_ORDER_VALIDATOR_PATH = (
    REPOSITORY / "tool/ci/validate_android_load_order_receipt.py"
)
LOAD_ORDER_RECEIPT_SCHEMA_PATH = (
    REPOSITORY / "templates/android/load_order_receipt.schema.json"
)
MAX_TOOL_SOURCE_BYTES = 16 * 1024 * 1024


class CompatibilityManifestError(RuntimeError):
    """An input cannot support the requested compatibility claim."""


def _status_identity(status: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _read_stable_file_bytes(
    path: Path, label: str, maximum: int
) -> tuple[bytes, dict[str, Any]]:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise CompatibilityManifestError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(before.st_mode):
        raise CompatibilityManifestError(f"{label} is not a regular file: {path}")
    if before.st_size <= 0 or before.st_size > maximum:
        raise CompatibilityManifestError(f"{label} size is outside its bound")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CompatibilityManifestError(
            f"could not securely open {label}: {error}"
        ) from error
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _status_identity(opened) != _status_identity(before)
        ):
            raise CompatibilityManifestError(
                f"{label} changed while it was being opened"
            )
        raw = source.read(maximum + 1)
        opened_after = os.fstat(source.fileno())
    try:
        after = path.lstat()
    except FileNotFoundError as error:
        raise CompatibilityManifestError(
            f"{label} disappeared while it was being read"
        ) from error
    if (
        len(raw) != before.st_size
        or len(raw) > maximum
        or _status_identity(opened_after) != _status_identity(before)
        or _status_identity(after) != _status_identity(before)
    ):
        raise CompatibilityManifestError(f"{label} changed while it was being read")
    return raw, {
        "sizeBytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _stable_file_identity(
    path: Path, label: str, maximum: int
) -> dict[str, Any]:
    _raw, identity = _read_stable_file_bytes(path, label, maximum)
    return identity


_VERIFIER_SOURCE, EXECUTED_VERIFIER_IDENTITY = _read_stable_file_bytes(
    VERIFIER_PATH, "Android native verifier", MAX_TOOL_SOURCE_BYTES
)
VERIFIER = types.ModuleType("fonix_android_native_verifier")
VERIFIER.__file__ = str(VERIFIER_PATH)
exec(compile(_VERIFIER_SOURCE, str(VERIFIER_PATH), "exec"), VERIFIER.__dict__)


MAX_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
MAX_VALIDATION_RECORD_BYTES = 64 * 1024 * 1024
MAX_QNN_QUALIFICATION_BYTES = 16 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LOAD_ORDERS = frozenset({"dart-first", "sherpa-first"})
BUILD_TYPES = frozenset({"debug", "release-minified"})
PAGE_SIZES = frozenset({4096, 16384})
SHERPA_LIBRARY_PROFILES = {
    "jni": (VERIFIER.SHERPA_JNI_NAME,),
    "flutter-ffi": (
        VERIFIER.SHERPA_C_API_NAME,
        VERIFIER.SHERPA_CXX_API_NAME,
    ),
}
KNOWN_SHERPA_LIBRARY_NAMES = frozenset(
    name for names in SHERPA_LIBRARY_PROFILES.values() for name in names
)
FINAL_PLATFORM_LIBRARY_NAMES = frozenset({"libapp.so", "libflutter.so"})
VALIDATION_RECORD_KEYS = frozenset(
    {
        "schemaVersion",
        "result",
        "claimStatus",
        "targetEvidenceProvenance",
        "validatorSha256",
        "receiptSchemaSha256",
        "nativeVerifierSha256",
        "loadOrderReceiptSha256",
        "matrix",
        "build",
        "device",
        "process",
        "runtime",
        "sherpa",
        "fixtures",
        "initialization",
        "workload",
        "lifecycle",
        "evidenceBindings",
        "nativeLibraries",
        "claimBoundary",
    }
)
EVIDENCE_BINDING_KEYS = frozenset(
    {
        "receipt",
        "receiptSchema",
        "nativeVerifier",
        "finalApk",
        "harnessContract",
        "pubspecLock",
        "targetEvidence",
        "logcatEvidence",
        "launchChallenge",
        "fonixModel",
        "fonixInput",
        "fonixReferenceOutput",
        "fonixCancellationModel",
        "fonixCancellationInput",
        "sherpaModel",
        "sherpaAudio",
        "sherpaReference",
    }
)
STABLE_EVIDENCE_BINDING_KEYS = frozenset(
    {
        "finalApk",
        "harnessContract",
        "pubspecLock",
        "fonixModel",
        "fonixInput",
        "fonixReferenceOutput",
        "fonixCancellationModel",
        "fonixCancellationInput",
        "sherpaModel",
        "sherpaAudio",
        "sherpaReference",
    }
)
EXPECTED_SHERPA_PROFILE = {
    "provider": "cpu",
    "sampleRateHz": 16000,
    "windowSamples": 512,
    "numThreads": 1,
    "thresholdMillionths": 500000,
    "minimumSpeechMilliseconds": 250,
    "minimumSilenceMilliseconds": 800,
    "maximumSpeechMilliseconds": 30000,
    "bufferMilliseconds": 60000,
}
MAX_CYCLES = 64
MAX_SEGMENTS = 32
MAX_COUNT = 1_000_000
MAX_SOURCE_SAMPLES = 57_600_000


@dataclass(frozen=True)
class ArtifactIdentity:
    file_name: str
    kind: str
    size_bytes: int
    sha256: str
    digest_scope: str

    def to_json(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "kind": self.kind,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "digestScope": self.digest_scope,
        }


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CompatibilityManifestError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _regular_file(path: Path, label: str, maximum: int) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise CompatibilityManifestError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(status.st_mode):
        raise CompatibilityManifestError(f"{label} is not a regular file: {path}")
    if status.st_size <= 0 or status.st_size > maximum:
        raise CompatibilityManifestError(f"{label} size is outside its bound")
    return path


def _native_input(path: Path, label: str) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise CompatibilityManifestError(f"missing {label}: {path}") from error
    if stat.S_ISLNK(status.st_mode):
        raise CompatibilityManifestError(f"{label} must not be a symbolic link")
    if stat.S_ISREG(status.st_mode):
        if status.st_size <= 0 or status.st_size > MAX_ARTIFACT_BYTES:
            raise CompatibilityManifestError(f"{label} size is outside its bound")
        return path
    if stat.S_ISDIR(status.st_mode):
        return path
    raise CompatibilityManifestError(
        f"{label} is not a regular archive or native-library directory: {path}"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _snapshot_archive(
    path: Path, label: str
) -> Iterable[tuple[Path, ArtifactIdentity]]:
    """Copy one regular archive once, then inspect and hash that exact copy."""

    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise CompatibilityManifestError(f"{label} is not a regular file: {path}")
    if before.st_size <= 0 or before.st_size > MAX_ARTIFACT_BYTES:
        raise CompatibilityManifestError(f"{label} size is outside its bound")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CompatibilityManifestError(
            f"could not securely open {label}: {error}"
        ) from error

    suffix = path.suffix.lower()
    with os.fdopen(descriptor, "rb") as source, tempfile.TemporaryDirectory(
        prefix="fonix-android-artifact-"
    ) as temporary_root:
        opened = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _status_identity(opened) != _status_identity(before)
        ):
            raise CompatibilityManifestError(
                f"{label} changed while it was being opened"
            )
        snapshot = Path(temporary_root) / f"artifact{suffix}"
        digest = hashlib.sha256()
        copied = 0
        with snapshot.open("xb") as destination:
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > before.st_size or copied > MAX_ARTIFACT_BYTES:
                    raise CompatibilityManifestError(
                        f"{label} changed while it was being snapshotted"
                    )
                digest.update(chunk)
                destination.write(chunk)
        try:
            after = path.lstat()
        except FileNotFoundError as error:
            raise CompatibilityManifestError(
                f"{label} disappeared while it was being snapshotted"
            ) from error
        if copied != before.st_size or _status_identity(after) != _status_identity(
            before
        ):
            raise CompatibilityManifestError(
                f"{label} changed while it was being snapshotted"
            )
        try:
            yield snapshot, ArtifactIdentity(
                file_name=path.name,
                kind="",
                size_bytes=copied,
                sha256=digest.hexdigest(),
                digest_scope="archive-bytes-v1",
            )
        finally:
            try:
                final_status = path.lstat()
            except FileNotFoundError as error:
                raise CompatibilityManifestError(
                    f"{label} disappeared while its snapshot was inspected"
                ) from error
            if _status_identity(final_status) != _status_identity(before):
                raise CompatibilityManifestError(
                    f"{label} changed while its snapshot was inspected"
                )


def _read_strict_json(
    path: Path, label: str, maximum: int
) -> tuple[dict[str, Any], str, int]:
    raw, identity = _read_stable_file_bytes(path, label, maximum)

    def reject_constant(value: str) -> None:
        raise CompatibilityManifestError(
            f"invalid {label}: non-finite JSON value {value}"
        )

    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompatibilityManifestError(f"invalid {label}: {path}") from error
    if not isinstance(value, dict):
        raise CompatibilityManifestError(f"{label} must be an object")
    return value, identity["sha256"], identity["sizeBytes"]


def _directory_artifact_identity(
    path: Path, report: dict[str, Any]
) -> ArtifactIdentity:
    libraries = [
        {
            "abi": entry["abi"],
            "name": entry["name"],
            "path": entry["path"],
            "sizeBytes": entry["size"],
            "sha256": entry["sha256"],
        }
        for entry in report["libraries"]
    ]
    if not libraries:
        raise CompatibilityManifestError(
            f"native-library directory contains no loadable libraries: {path}"
        )
    encoded = json.dumps(
        {
            "domain": "fonix-android-loadable-native-library-inventory",
            "schemaVersion": 1,
            "libraries": libraries,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return ArtifactIdentity(
        file_name=path.name,
        kind="native-directory",
        size_bytes=sum(entry["sizeBytes"] for entry in libraries),
        sha256=hashlib.sha256(encoded).hexdigest(),
        digest_scope="loadable-native-library-inventory-v1",
    )


def _inspect(path: Path, label: str) -> dict[str, Any]:
    _native_input(path, label)
    if path.is_file():
        with _snapshot_archive(path, label) as (snapshot, snapshot_identity):
            try:
                report = VERIFIER.inspect(snapshot)
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                raise CompatibilityManifestError(
                    f"could not inspect {label}: {error}"
                ) from error
            if (
                snapshot.stat().st_size != snapshot_identity.size_bytes
                or _sha256(snapshot) != snapshot_identity.sha256
            ):
                raise CompatibilityManifestError(
                    f"{label} changed while it was being inspected"
                )
            identity = ArtifactIdentity(
                file_name=snapshot_identity.file_name,
                kind=report["kind"],
                size_bytes=snapshot_identity.size_bytes,
                sha256=snapshot_identity.sha256,
                digest_scope=snapshot_identity.digest_scope,
            )
            report["artifact"] = str(path)
    else:
        try:
            report = VERIFIER.inspect(path)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            raise CompatibilityManifestError(
                f"could not inspect {label}: {error}"
            ) from error
        try:
            confirmation = VERIFIER.inspect(path)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            raise CompatibilityManifestError(
                f"could not confirm stable {label}: {error}"
            ) from error
        if report != confirmation:
            raise CompatibilityManifestError(
                f"{label} changed while it was being inspected"
            )
        identity = _directory_artifact_identity(path, report)
    problems: list[str] = []
    if report["duplicate_paths"]:
        problems.append("duplicate archive paths")
    if report["invalid_archive_paths"]:
        problems.append("non-canonical archive paths")
    if report["invalid_libraries"]:
        problems.append("invalid native libraries")
    if problems:
        raise CompatibilityManifestError(f"{label} contains " + ", ".join(problems))
    if not report["libraries"]:
        raise CompatibilityManifestError(
            f"{label} contains no valid loadable native libraries"
        )
    report["_fonixArtifactIdentity"] = identity
    return report


def _entries(report: dict[str, Any], name: str, abi: str) -> list[dict[str, Any]]:
    return [
        entry
        for entry in report["libraries"]
        if entry["name"] == name and entry["abi"] == abi
    ]


def _single(
    report: dict[str, Any], name: str, abi: str, label: str
) -> dict[str, Any]:
    matches = _entries(report, name, abi)
    if len(matches) != 1:
        raise CompatibilityManifestError(
            f"{label} must contain exactly one {name} for {abi}; found {len(matches)}"
        )
    return matches[0]


def _entries_across(
    reports: Iterable[dict[str, Any]], name: str, abi: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (report, entry)
        for report in reports
        for entry in _entries(report, name, abi)
    ]


def _single_across(
    reports: Iterable[dict[str, Any]], name: str, abi: str, label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    matches = _entries_across(reports, name, abi)
    if len(matches) != 1:
        raise CompatibilityManifestError(
            f"{label} must contain exactly one {name} for {abi}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _unique_reports(
    *report_groups: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_artifact: dict[str, dict[str, Any]] = {}
    for reports in report_groups:
        for report in reports:
            by_artifact.setdefault(report["artifact"], report)
    return [by_artifact[key] for key in sorted(by_artifact)]


def _resolve_unique_paths(values: Iterable[Path], label: str) -> tuple[Path, ...]:
    paths_list: list[Path] = []
    for value in values:
        absolute = value.absolute()
        _native_input(absolute, label)
        paths_list.append(absolute.resolve(strict=True))
    paths = tuple(paths_list)
    if not paths:
        raise CompatibilityManifestError(f"at least one {label} is required")
    if len(set(paths)) != len(paths):
        raise CompatibilityManifestError(f"duplicate {label} path")
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left in right.parents or right in left.parents:
                raise CompatibilityManifestError(
                    f"overlapping {label} paths are not allowed"
                )
    return paths


def _resolve_regular_path(path: Path, label: str, maximum: int) -> Path:
    absolute = path.absolute()
    _regular_file(absolute, label, maximum)
    return absolute.resolve(strict=True)


def _reject_output_overlap(
    output: Path,
    *,
    exact_inputs: Iterable[Path],
    native_inputs: Iterable[Path],
) -> None:
    output_path = output.absolute().resolve(strict=False)
    temporary_path = output.with_name(output.name + ".tmp").absolute().resolve(
        strict=False
    )
    input_paths = set(exact_inputs)
    for candidate, label in (
        (output_path, "output"),
        (temporary_path, "temporary output"),
    ):
        if candidate in input_paths:
            raise CompatibilityManifestError(f"{label} must not overwrite an input")
        if any(
            native_input.is_dir()
            and (candidate == native_input or native_input in candidate.parents)
            for native_input in native_inputs
        ):
            raise CompatibilityManifestError(
                f"{label} must not be inside a native-directory input"
            )


def _inspect_many(paths: Iterable[Path], label: str) -> list[dict[str, Any]]:
    return [
        _inspect(path, f"{label} {index + 1}")
        for index, path in enumerate(paths)
    ]


def _identity_map(
    paths: Iterable[Path], reports: Iterable[dict[str, Any]]
) -> dict[str, ArtifactIdentity]:
    return {
        report["artifact"]: report["_fonixArtifactIdentity"]
        for path, report in zip(paths, reports, strict=True)
    }


def _reject_cross_role_overlap(
    left_paths: Iterable[Path],
    left_label: str,
    right_paths: Iterable[Path],
    right_label: str,
) -> None:
    for left in left_paths:
        for right in right_paths:
            if left == right or left in right.parents or right in left.parents:
                raise CompatibilityManifestError(
                    f"{left_label} and {right_label} inputs must not overlap"
                )


def _loaded_identity(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "flags": segment["flags"],
            "virtualAddress": segment["virtualAddress"],
            "fileSize": segment["fileSize"],
            "memorySize": segment["memorySize"],
            "sha256": segment["sha256"],
        }
        for segment in entry["elf"]["loadSegments"]
    ]


def _tie_source_to_final(
    source: dict[str, Any], final: dict[str, Any], role: str, abi: str
) -> None:
    if source["elf"]["class"] != final["elf"]["class"] or source["elf"][
        "machine"
    ] != final["elf"]["machine"]:
        raise CompatibilityManifestError(f"final {role} architecture drift for {abi}")
    if source["elf"]["soname"] != final["elf"]["soname"]:
        raise CompatibilityManifestError(f"final {role} SONAME drift for {abi}")
    if source["elf"]["needed"] != final["elf"]["needed"]:
        raise CompatibilityManifestError(f"final {role} dependency drift for {abi}")
    if _loaded_identity(source) != _loaded_identity(final):
        raise CompatibilityManifestError(
            f"final {role} loaded bytes do not match the selected source for {abi}"
        )


def _library_record(
    source: dict[str, Any],
    final: dict[str, Any],
    source_artifact: ArtifactIdentity,
) -> dict[str, Any]:
    return {
        "source": {
            "artifactSha256": source_artifact.sha256,
            "artifactDigestScope": source_artifact.digest_scope,
            "path": source["path"],
            "sizeBytes": source["size"],
            "sha256": source["sha256"],
        },
        "final": {
            "path": final["path"],
            "sizeBytes": final["size"],
            "sha256": final["sha256"],
        },
        "elf": {
            "class": final["elf"]["class"],
            "machine": final["elf"]["machine"],
            "soname": final["elf"]["soname"],
            "needed": final["elf"]["needed"],
            "pageSize16KiBCompatible": final["elf"][
                "pageSize16KiBCompatible"
            ],
            "loadedSegments": _loaded_identity(final),
        },
    }


def _validate_final_graph(report: dict[str, Any], abis: tuple[str, ...]) -> None:
    if report["kind"] not in {"apk", "aab"}:
        raise CompatibilityManifestError("final artifact must be an APK or AAB")
    present = {entry["abi"] for entry in report["libraries"]}
    if present != set(abis):
        raise CompatibilityManifestError(
            "final artifact ABI set does not exactly match the declared set"
        )
    for abi in abis:
        abi_entries = [entry for entry in report["libraries"] if entry["abi"] == abi]
        duplicate_names = sorted(
            name
            for name in {entry["name"] for entry in abi_entries}
            if sum(entry["name"] == name for entry in abi_entries) != 1
        )
        if duplicate_names:
            raise CompatibilityManifestError(
                f"final artifact has duplicate native library basenames for {abi}: "
                + ", ".join(duplicate_names)
            )
        packaged_names = {entry["name"] for entry in abi_entries}
        for entry in abi_entries:
            soname = entry["elf"]["soname"]
            flutter_aot_without_soname = (
                entry["name"] == VERIFIER.FLUTTER_AOT_NAME and soname is None
            )
            if soname != entry["name"] and not flutter_aot_without_soname:
                raise CompatibilityManifestError(
                    f"final {entry['path']} SONAME does not match its basename"
                )
            if abi in VERIFIER.ANDROID_16K_ABIS and not entry["elf"][
                "pageSize16KiBCompatible"
            ]:
                raise CompatibilityManifestError(
                    f"final {entry['path']} is not 16 KiB compatible"
                )
            unresolved = [
                needed
                for needed in entry["elf"]["needed"]
                if needed not in VERIFIER.ANDROID_SYSTEM_LIBRARIES
                and needed not in packaged_names
            ]
            if unresolved:
                raise CompatibilityManifestError(
                    f"final {entry['path']} has unresolved dependencies: "
                    + ", ".join(sorted(unresolved))
                )
        libcxx = _entries(report, VERIFIER.LIBCXX_NAME, abi)
        libcxx_users = [
            entry
            for entry in abi_entries
            if entry["name"] != VERIFIER.LIBCXX_NAME
            and VERIFIER.LIBCXX_NAME in entry["elf"]["needed"]
        ]
        if len(libcxx) > 1:
            raise CompatibilityManifestError(
                f"final artifact has multiple {VERIFIER.LIBCXX_NAME} owners for {abi}"
            )
        if libcxx_users and len(libcxx) != 1:
            raise CompatibilityManifestError(
                f"final artifact does not contain exactly one "
                f"{VERIFIER.LIBCXX_NAME} owner for {abi}"
            )
        if libcxx and not libcxx_users:
            raise CompatibilityManifestError(
                f"final artifact packages unused {VERIFIER.LIBCXX_NAME} for {abi}"
            )


def _validate_ort_candidate_inventory(
    report: dict[str, Any], label: str
) -> None:
    canonical_entries = [
        entry
        for entry in report["libraries"]
        if entry["name"] == VERIFIER.ORT_NAME
    ]
    expected = {entry["path"] for entry in canonical_entries}
    candidates = report.get("ort_candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(candidate, str) for candidate in candidates
    ):
        raise CompatibilityManifestError(
            f"{label} has an invalid ONNX Runtime candidate inventory"
        )
    if set(candidates) != expected or len(candidates) != len(set(candidates)):
        raise CompatibilityManifestError(
            f"{label} has a hidden or invalid ONNX Runtime candidate"
        )
    if any(entry["elf"].get("definesOrtApi") is not True for entry in canonical_entries):
        raise CompatibilityManifestError(
            f"{label} has a canonical ONNX Runtime owner without OrtGetApiBase"
        )


def _validate_sherpa_consumer(
    entry: dict[str, Any], name: str, abi: str
) -> None:
    if entry["elf"]["soname"] != name:
        raise CompatibilityManifestError(
            f"sherpa {name} has the wrong SONAME for {abi}"
        )
    required_dependencies = {VERIFIER.ORT_NAME}
    if name == VERIFIER.SHERPA_CXX_API_NAME:
        required_dependencies.add(VERIFIER.SHERPA_C_API_NAME)
    missing = sorted(required_dependencies - set(entry["elf"]["needed"]))
    if missing:
        raise CompatibilityManifestError(
            f"sherpa {name} does not use the required shared-runtime graph "
            f"for {abi}; missing {', '.join(missing)}"
        )


def _profile_consumers(
    reports: list[dict[str, Any]],
    final_report: dict[str, Any],
    profile: str,
    abi: str,
) -> list[
    tuple[
        str,
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]
]:
    expected = set(SHERPA_LIBRARY_PROFILES[profile])
    source_observed = {
        entry["name"]
        for report in reports
        for entry in report["libraries"]
        if entry["abi"] == abi and entry["name"] in KNOWN_SHERPA_LIBRARY_NAMES
    }
    final_observed = {
        entry["name"]
        for entry in final_report["libraries"]
        if entry["abi"] == abi and entry["name"] in KNOWN_SHERPA_LIBRARY_NAMES
    }
    if source_observed != expected:
        raise CompatibilityManifestError(
            f"sherpa source library profile {profile!r} for {abi} requires "
            f"{sorted(expected)}, found {sorted(source_observed)}"
        )
    if final_observed != expected:
        raise CompatibilityManifestError(
            f"final sherpa library profile {profile!r} for {abi} requires "
            f"{sorted(expected)}, found {sorted(final_observed)}"
        )

    consumers = []
    for name in sorted(expected):
        source_report, source_entry = _single_across(
            reports, name, abi, "sherpa artifacts"
        )
        final_entry = _single(final_report, name, abi, "final artifact")
        _validate_sherpa_consumer(source_entry, name, abi)
        _validate_sherpa_consumer(final_entry, name, abi)
        consumers.append((name, source_report, source_entry, final_entry))
    return consumers


def _exact_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise CompatibilityManifestError(f"{label} has an unexpected field set")


def _receipt_text(value: Any, label: str, pattern: re.Pattern[str] = TOKEN) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CompatibilityManifestError(f"receipt {label} is invalid")
    return value


def _receipt_digest(value: Any, label: str) -> str:
    result = _receipt_text(value, label, SHA256)
    return result


def _record_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise CompatibilityManifestError(
            f"validation record {label} must be an integer in range"
        )
    return value


def _record_exact_integer(value: Any, expected: int, label: str) -> int:
    result = _record_integer(value, label, expected, expected)
    return result


def _record_boolean(value: Any, expected: bool, label: str) -> None:
    if value is not expected:
        raise CompatibilityManifestError(
            f"validation record {label} must be {str(expected).lower()}"
        )


def _record_reference_bytes(
    value: Any, expected_sha256: str, label: str
) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_REFERENCE_BYTES * 2:
        raise CompatibilityManifestError(
            f"validation record {label} must be bounded base64"
        )
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise CompatibilityManifestError(
            f"validation record {label} is invalid base64"
        ) from error
    if (
        not decoded
        or len(decoded) > MAX_REFERENCE_BYTES
        or base64.b64encode(decoded).decode("ascii") != value
        or hashlib.sha256(decoded).hexdigest() != expected_sha256
    ):
        raise CompatibilityManifestError(
            f"validation record {label} does not bind the reference bytes"
        )
    return decoded


def _validate_fonix_observation_record(
    value: Any,
    *,
    expected_sha256: str,
    label: str,
    step: bool,
) -> int:
    observation = _record_object(value, f"validation record {label}")
    expected_keys = {"outputEncoding", "outputBytesBase64", "outputSha256"}
    if step:
        expected_keys.update({"ordinal", "engine"})
    _exact_keys(observation, expected_keys, f"validation record {label}")
    if observation["outputEncoding"] != "float32-le":
        raise CompatibilityManifestError(
            f"validation record {label}.outputEncoding is invalid"
        )
    if (
        _receipt_digest(observation["outputSha256"], f"{label}.outputSha256")
        != expected_sha256
    ):
        raise CompatibilityManifestError(
            f"validation record {label}.outputSha256 is inconsistent"
        )
    decoded = _record_reference_bytes(
        observation["outputBytesBase64"],
        expected_sha256,
        f"{label}.outputBytesBase64",
    )
    if len(decoded) % 4:
        raise CompatibilityManifestError(
            f"validation record {label} is not whole float32-le output"
        )
    return len(decoded)


def _validate_vad_observation_record(
    value: Any,
    *,
    profile: dict[str, Any],
    label: str,
    step: bool,
    expected_source_samples: int | None,
) -> int:
    observation = _record_object(value, f"validation record {label}")
    expected_keys = {
        "sourceSamples",
        "submittedSamples",
        "segments",
        "queueEmptyAfterDrain",
        "detectedAfterDrain",
    }
    if step:
        expected_keys.update({"ordinal", "engine"})
    _exact_keys(observation, expected_keys, f"validation record {label}")
    source_samples = _record_integer(
        observation["sourceSamples"],
        f"{label}.sourceSamples",
        1,
        MAX_SOURCE_SAMPLES,
    )
    if (
        expected_source_samples is not None
        and source_samples != expected_source_samples
    ):
        raise CompatibilityManifestError(
            "validation record VAD observations disagree on source samples"
        )
    window_samples = profile["windowSamples"]
    submitted_samples = (
        (source_samples + window_samples - 1) // window_samples
    ) * window_samples
    _record_exact_integer(
        observation["submittedSamples"],
        submitted_samples,
        f"{label}.submittedSamples",
    )
    _record_boolean(
        observation["queueEmptyAfterDrain"],
        True,
        f"{label}.queueEmptyAfterDrain",
    )
    _record_boolean(
        observation["detectedAfterDrain"],
        False,
        f"{label}.detectedAfterDrain",
    )
    segments = observation["segments"]
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise CompatibilityManifestError(
            f"validation record {label}.segments is outside its bound"
        )
    previous_end = 0
    total_samples = 0
    for index, raw_segment in enumerate(segments):
        segment_label = f"{label}.segments[{index}]"
        segment = _record_object(
            raw_segment, f"validation record {segment_label}"
        )
        _exact_keys(
            segment,
            {"startSample", "sampleCount"},
            f"validation record {segment_label}",
        )
        start = _record_integer(
            segment["startSample"], f"{segment_label}.startSample", 0, source_samples
        )
        count = _record_integer(
            segment["sampleCount"],
            f"{segment_label}.sampleCount",
            1,
            source_samples,
        )
        if start < previous_end or count > source_samples - start:
            raise CompatibilityManifestError(
                f"validation record {segment_label} is overlapping or out of range"
            )
        previous_end = start + count
        total_samples += count
    if not 1 <= total_samples <= source_samples:
        raise CompatibilityManifestError(
            f"validation record {label} has an impossible segment total"
        )
    return source_samples


def _validate_workload_record(
    value: Any,
    *,
    profile: dict[str, Any],
    reference_sha256: str,
) -> tuple[int, int, int]:
    workload = _record_object(value, "validation record workload")
    _exact_keys(
        workload,
        {"requestedCycles", "completedCycles", "steps"},
        "validation record workload",
    )
    cycles = _record_integer(
        workload["requestedCycles"], "workload.requestedCycles", 2, MAX_CYCLES
    )
    _record_exact_integer(
        workload["completedCycles"], cycles, "workload.completedCycles"
    )
    steps = workload["steps"]
    if not isinstance(steps, list) or len(steps) != cycles * 2:
        raise CompatibilityManifestError(
            "validation record workload must contain two steps per cycle"
        )
    source_samples: int | None = None
    reference_size: int | None = None
    for index, raw_step in enumerate(steps):
        label = f"workload.steps[{index}]"
        step = _record_object(raw_step, f"validation record {label}")
        _record_exact_integer(step.get("ordinal"), index + 1, f"{label}.ordinal")
        engine = "fonix" if index % 2 == 0 else "sherpa"
        if step.get("engine") != engine:
            raise CompatibilityManifestError(
                "validation record workload does not strictly alternate from Fonix"
            )
        if engine == "fonix":
            observed_reference_size = _validate_fonix_observation_record(
                step,
                expected_sha256=reference_sha256,
                label=label,
                step=True,
            )
            if reference_size is None:
                reference_size = observed_reference_size
            elif reference_size != observed_reference_size:
                raise CompatibilityManifestError(
                    "validation record Fonix observations disagree on output size"
                )
        else:
            source_samples = _validate_vad_observation_record(
                step,
                profile=profile,
                label=label,
                step=True,
                expected_source_samples=source_samples,
            )
    if source_samples is None or reference_size is None:
        raise CompatibilityManifestError(
            "validation record workload is missing a closed engine observation"
        )
    return cycles, source_samples, reference_size


def _validate_lifecycle_record(
    value: Any,
    *,
    profile: dict[str, Any],
    reference_sha256: str,
    source_samples: int,
) -> dict[str, Any]:
    lifecycle = _record_object(value, "validation record lifecycle")
    _exact_keys(
        lifecycle,
        {
            "fonixCancellation",
            "sherpaCancellation",
            "staleCompletion",
            "recovery",
            "disposal",
        },
        "validation record lifecycle",
    )

    fonix = _record_object(
        lifecycle["fonixCancellation"],
        "validation record lifecycle.fonixCancellation",
    )
    expected_fonix = {
        "mode": "active-native-termination",
        "requestCount": 1,
        "nativeRequestAcceptedCount": 1,
        "settlementCount": 1,
        "cancelledResultCount": 1,
        "publishedOutputCount": 0,
        "outstandingRunsAfterSettlement": 0,
        "settledBeforeRecovery": True,
    }
    _exact_keys(
        fonix,
        expected_fonix,
        "validation record lifecycle.fonixCancellation",
    )
    if json.dumps(fonix, sort_keys=True) != json.dumps(
        expected_fonix, sort_keys=True
    ):
        raise CompatibilityManifestError(
            "validation record Fonix cancellation is not the closed passing contract"
        )

    sherpa = _record_object(
        lifecycle["sherpaCancellation"],
        "validation record lifecycle.sherpaCancellation",
    )
    _exact_keys(
        sherpa,
        {
            "mode",
            "requestCount",
            "framesAcceptedBeforeRequest",
            "framesAcceptedAfterRequest",
            "flushCallsAfterRequest",
            "segmentsPublishedAfterRequest",
            "detectorRetiredBeforeRecovery",
        },
        "validation record lifecycle.sherpaCancellation",
    )
    if sherpa["mode"] != "between-bounded-frames":
        raise CompatibilityManifestError(
            "validation record Sherpa cancellation mode is invalid"
        )
    _record_exact_integer(
        sherpa["requestCount"], 1, "lifecycle.sherpaCancellation.requestCount"
    )
    maximum_frames = (
        source_samples + profile["windowSamples"] - 1
    ) // profile["windowSamples"]
    if maximum_frames <= 1:
        raise CompatibilityManifestError(
            "validation record VAD input has no between-frame cancellation boundary"
        )
    _record_integer(
        sherpa["framesAcceptedBeforeRequest"],
        "lifecycle.sherpaCancellation.framesAcceptedBeforeRequest",
        1,
        maximum_frames - 1,
    )
    for key in (
        "framesAcceptedAfterRequest",
        "flushCallsAfterRequest",
        "segmentsPublishedAfterRequest",
    ):
        _record_exact_integer(
            sherpa[key], 0, f"lifecycle.sherpaCancellation.{key}"
        )
    _record_boolean(
        sherpa["detectorRetiredBeforeRecovery"],
        True,
        "lifecycle.sherpaCancellation.detectorRetiredBeforeRecovery",
    )

    stale = _record_object(
        lifecycle["staleCompletion"],
        "validation record lifecycle.staleCompletion",
    )
    _exact_keys(
        stale,
        {
            "inducedCount",
            "observedCount",
            "suppressedCount",
            "publishedOutputCount",
            "retiredGeneration",
            "authoritativeGeneration",
        },
        "validation record lifecycle.staleCompletion",
    )
    for key in ("inducedCount", "observedCount", "suppressedCount"):
        _record_exact_integer(stale[key], 1, f"lifecycle.staleCompletion.{key}")
    _record_exact_integer(
        stale["publishedOutputCount"],
        0,
        "lifecycle.staleCompletion.publishedOutputCount",
    )
    retired = _record_integer(
        stale["retiredGeneration"],
        "lifecycle.staleCompletion.retiredGeneration",
        1,
        MAX_COUNT,
    )
    authoritative = _record_integer(
        stale["authoritativeGeneration"],
        "lifecycle.staleCompletion.authoritativeGeneration",
        2,
        MAX_COUNT,
    )
    if authoritative <= retired:
        raise CompatibilityManifestError(
            "validation record authoritative generation does not follow retirement"
        )

    recovery = _record_object(
        lifecycle["recovery"], "validation record lifecycle.recovery"
    )
    _exact_keys(
        recovery,
        {
            "fonixOutputEncoding",
            "fonixOutputBytesBase64",
            "fonixOutputSha256",
            "sherpa",
        },
        "validation record lifecycle.recovery",
    )
    _validate_fonix_observation_record(
        {
            "outputEncoding": recovery["fonixOutputEncoding"],
            "outputBytesBase64": recovery["fonixOutputBytesBase64"],
            "outputSha256": recovery["fonixOutputSha256"],
        },
        expected_sha256=reference_sha256,
        label="lifecycle.recovery.fonix",
        step=False,
    )
    _validate_vad_observation_record(
        recovery["sherpa"],
        profile=profile,
        label="lifecycle.recovery.sherpa",
        step=False,
        expected_source_samples=source_samples,
    )

    disposal = _record_object(
        lifecycle["disposal"], "validation record lifecycle.disposal"
    )
    _exact_keys(
        disposal,
        {
            "ordersTested",
            "fonixSessionsCreated",
            "fonixSessionsClosed",
            "sherpaDetectorsCreated",
            "sherpaDetectorsFreed",
            "fonixDoubleClose",
            "sherpaDoubleFree",
            "pendingFonixRuns",
            "queuedSherpaSegments",
            "temporaryRootsCreated",
            "temporaryRootsRemoved",
            "temporaryRootsRemaining",
        },
        "validation record lifecycle.disposal",
    )
    if disposal["ordersTested"] != [
        "fonix-then-sherpa",
        "sherpa-then-fonix",
    ]:
        raise CompatibilityManifestError(
            "validation record does not prove both disposal orders"
        )
    fonix_created = _record_integer(
        disposal["fonixSessionsCreated"],
        "lifecycle.disposal.fonixSessionsCreated",
        2,
        MAX_COUNT,
    )
    _record_exact_integer(
        disposal["fonixSessionsClosed"],
        fonix_created,
        "lifecycle.disposal.fonixSessionsClosed",
    )
    sherpa_created = _record_integer(
        disposal["sherpaDetectorsCreated"],
        "lifecycle.disposal.sherpaDetectorsCreated",
        2,
        MAX_COUNT,
    )
    _record_exact_integer(
        disposal["sherpaDetectorsFreed"],
        sherpa_created,
        "lifecycle.disposal.sherpaDetectorsFreed",
    )
    if (
        disposal["fonixDoubleClose"] != "passed"
        or disposal["sherpaDoubleFree"] != "passed"
    ):
        raise CompatibilityManifestError(
            "validation record double-disposal checks did not pass"
        )
    for key in (
        "pendingFonixRuns",
        "queuedSherpaSegments",
        "temporaryRootsRemaining",
    ):
        _record_exact_integer(disposal[key], 0, f"lifecycle.disposal.{key}")
    roots_created = _record_integer(
        disposal["temporaryRootsCreated"],
        "lifecycle.disposal.temporaryRootsCreated",
        1,
        MAX_COUNT,
    )
    _record_exact_integer(
        disposal["temporaryRootsRemoved"],
        roots_created,
        "lifecycle.disposal.temporaryRootsRemoved",
    )
    return lifecycle


def _record_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CompatibilityManifestError(f"{label} must be an object")
    return value


def _record_identity(value: Any, label: str) -> dict[str, Any]:
    identity = _record_object(value, label)
    _exact_keys(identity, {"sizeBytes", "sha256"}, label)
    size = identity["sizeBytes"]
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or size > MAX_ARTIFACT_BYTES
    ):
        raise CompatibilityManifestError(f"{label}.sizeBytes is outside its bound")
    _receipt_digest(identity["sha256"], f"{label}.sha256")
    return identity


def _validated_native_library(
    value: Any,
    *,
    role: str,
    name: str,
    abi: str,
    final_report: dict[str, Any],
) -> dict[str, Any]:
    record = _record_object(value, f"validation record nativeLibraries.{role}")
    _exact_keys(
        record,
        {"sizeBytes", "sha256", "soname", "needed", "pageSize16KiBCompatible"},
        f"validation record nativeLibraries.{role}",
    )
    final = _single(final_report, name, abi, "final artifact")
    expected = {
        "sizeBytes": final["size"],
        "sha256": final["sha256"],
        "soname": final["elf"]["soname"],
        "needed": final["elf"]["needed"],
        "pageSize16KiBCompatible": final["elf"]["pageSize16KiBCompatible"],
    }
    if record != expected:
        raise CompatibilityManifestError(
            f"validation record native library {role} does not match the "
            f"inspected final artifact for {abi}"
        )
    if record["pageSize16KiBCompatible"] is not True:
        raise CompatibilityManifestError(
            f"validation record native library {role} is not 16 KiB compatible"
        )
    return record


def _read_validation_record(
    path: Path,
    *,
    abis: tuple[str, ...],
    mode: str,
    sherpa_library_profile: str,
    sherpa_revision: str,
    final_identity: ArtifactIdentity,
    runtime_version: str,
    ort_api: int,
    expected_build_type: str,
    final_report: dict[str, Any],
    final_ort_by_abi: dict[str, dict[str, Any]],
    expected_validator_sha256: str,
    expected_receipt_schema_sha256: str,
    expected_receipt_schema_size: int,
    expected_native_verifier_sha256: str,
    expected_native_verifier_size: int,
) -> dict[str, Any]:
    value, record_sha256, record_size = _read_strict_json(
        path, "load-order validation record", MAX_VALIDATION_RECORD_BYTES
    )
    _exact_keys(value, VALIDATION_RECORD_KEYS, "load-order validation record")
    if mode != "sherpa-owned" or sherpa_library_profile != "flutter-ffi":
        raise CompatibilityManifestError(
            "the current load-order validator accepts only sherpa-owned "
            "Flutter FFI APK records"
        )
    if final_report["kind"] != "apk":
        raise CompatibilityManifestError(
            "the current load-order validator accepts only final APK records"
        )
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise CompatibilityManifestError(
            "load-order validation record schemaVersion must be integer 1"
        )
    if value["result"] != "passed":
        raise CompatibilityManifestError(
            "load-order validation record did not record a pass"
        )
    if (
        value["claimStatus"] != "offline-consistency-only"
        or value["targetEvidenceProvenance"] != "unverified"
    ):
        raise CompatibilityManifestError(
            "load-order validation record overstates its evidence provenance"
        )
    if (
        _receipt_digest(value["validatorSha256"], "validatorSha256")
        != expected_validator_sha256
    ):
        raise CompatibilityManifestError(
            "load-order validation record does not identify the current validator"
        )
    if (
        _receipt_digest(value["receiptSchemaSha256"], "receiptSchemaSha256")
        != expected_receipt_schema_sha256
    ):
        raise CompatibilityManifestError(
            "load-order validation record does not identify the current receipt schema"
        )
    if (
        _receipt_digest(value["nativeVerifierSha256"], "nativeVerifierSha256")
        != expected_native_verifier_sha256
    ):
        raise CompatibilityManifestError(
            "load-order validation record does not identify the current native verifier"
        )
    load_order_receipt_sha256 = _receipt_digest(
        value["loadOrderReceiptSha256"], "loadOrderReceiptSha256"
    )

    matrix = _record_object(value["matrix"], "validation record matrix")
    _exact_keys(
        matrix,
        {"abi", "loadOrder", "buildType", "pageSizeBytes"},
        "validation record matrix",
    )
    abi = _receipt_text(matrix["abi"], "matrix.abi")
    if abi not in abis:
        raise CompatibilityManifestError("validation record ABI is not declared")
    if matrix["loadOrder"] not in LOAD_ORDERS:
        raise CompatibilityManifestError("validation record load order is invalid")
    if matrix["buildType"] != expected_build_type:
        raise CompatibilityManifestError(
            "load-order validation record belongs to another final build type"
        )
    if type(matrix["pageSizeBytes"]) is not int or matrix[
        "pageSizeBytes"
    ] not in PAGE_SIZES:
        raise CompatibilityManifestError(
            "validation record page size is outside the matrix"
        )

    build = _record_object(value["build"], "validation record build")
    _exact_keys(
        build,
        {
            "applicationId",
            "finalApkSha256",
            "harnessContractSha256",
            "pubspecLockSha256",
            "targetEvidenceSha256",
            "logcatEvidenceSha256",
        },
        "validation record build",
    )
    if (
        not isinstance(build["applicationId"], str)
        or not 3 <= len(build["applicationId"]) <= 255
        or re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+",
            build["applicationId"],
        )
        is None
    ):
        raise CompatibilityManifestError("validation record applicationId is invalid")
    for key in build:
        if key != "applicationId":
            _receipt_digest(build[key], f"build.{key}")
    if build["finalApkSha256"] != final_identity.sha256:
        raise CompatibilityManifestError(
            "load-order validation record does not bind the selected final APK"
        )

    device = _record_object(value["device"], "validation record device")
    _exact_keys(
        device,
        {"kind", "modelToken", "androidApi", "fingerprintSha256"},
        "validation record device",
    )
    if device["kind"] not in {"physical", "emulator"}:
        raise CompatibilityManifestError("validation record device kind is invalid")
    _receipt_text(device["modelToken"], "device.modelToken")
    if (
        not isinstance(device["androidApi"], int)
        or isinstance(device["androidApi"], bool)
        or not 24 <= device["androidApi"] <= 100
    ):
        raise CompatibilityManifestError(
            "validation record Android API is outside its bound"
        )
    _receipt_digest(device["fingerprintSha256"], "device.fingerprintSha256")

    process = _record_object(value["process"], "validation record process")
    _exact_keys(
        process,
        {"launchChallengeSha256", "uid", "pid"},
        "validation record process",
    )
    launch_challenge = _receipt_digest(
        process["launchChallengeSha256"], "process.launchChallengeSha256"
    )
    uid = _record_object(process["uid"], "validation record process.uid")
    _exact_keys(
        uid,
        {"packageManager", "logcatFilter", "receiptLine"},
        "validation record process.uid",
    )
    if (
        any(
            not isinstance(uid[key], int)
            or isinstance(uid[key], bool)
            or not 10000 <= uid[key] <= 2**31 - 1
            for key in uid
        )
        or len(set(uid.values())) != 1
    ):
        raise CompatibilityManifestError(
            "validation record UID evidence does not identify one app"
        )
    pid = _record_object(process["pid"], "validation record process.pid")
    live_pid_keys = (
        "observedAfterLaunch",
        "logcatFilter",
        "receiptLine",
        "afterReceipt",
    )
    _exact_keys(
        pid,
        {"beforeLaunch", *live_pid_keys, "afterForceStop"},
        "validation record process.pid",
    )
    if (
        pid["beforeLaunch"] is not None
        or pid["afterForceStop"] is not None
        or any(
            not isinstance(pid[key], int)
            or isinstance(pid[key], bool)
            or not 1 <= pid[key] <= 2**31 - 1
            for key in live_pid_keys
        )
        or len({pid[key] for key in live_pid_keys}) != 1
    ):
        raise CompatibilityManifestError(
            "validation record PID evidence does not identify one process"
        )

    runtime = _record_object(value["runtime"], "validation record runtime")
    _exact_keys(
        runtime,
        {
            "runtimeOwner",
            "runtimeSource",
            "ortVersion",
            "requiredOrtApi",
            "negotiatedOrtApi",
            "ortSha256",
            "shimAbi",
            "shimBuildId",
        },
        "validation record runtime",
    )
    expected_owner = "sherpa" if mode == "sherpa-owned" else "application"
    expected_source = "process" if mode == "sherpa-owned" else "bundled"
    expected_build_id = f"android-owner-{expected_owner}-source-{expected_source}"
    if (
        runtime["runtimeOwner"] != expected_owner
        or runtime["runtimeSource"] != expected_source
        or runtime["shimBuildId"] != expected_build_id
        or type(runtime["shimAbi"]) is not int
        or runtime["shimAbi"] != 1
    ):
        raise CompatibilityManifestError(
            "validation record runtime ownership does not match the selected mode"
        )
    if (
        _receipt_text(runtime["ortVersion"], "runtime.ortVersion", VERSION)
        != runtime_version
        or type(runtime["requiredOrtApi"]) is not int
        or runtime["requiredOrtApi"] != ort_api
        or type(runtime["negotiatedOrtApi"]) is not int
        or runtime["negotiatedOrtApi"] != ort_api
        or _receipt_digest(runtime["ortSha256"], "runtime.ortSha256")
        != final_ort_by_abi[abi]["sha256"]
    ):
        raise CompatibilityManifestError(
            "validation record runtime does not match the inspected final runtime"
        )

    sherpa = _record_object(value["sherpa"], "validation record sherpa")
    _exact_keys(
        sherpa,
        {
            "packageVersion",
            "nativeVersion",
            "sourceRevision",
            "nativeRevision",
            "profile",
        },
        "validation record sherpa",
    )
    package_version = _receipt_text(
        sherpa["packageVersion"], "sherpa.packageVersion", VERSION
    )
    if (
        _receipt_text(sherpa["nativeVersion"], "sherpa.nativeVersion", VERSION)
        != package_version
        or sherpa["sourceRevision"] != sherpa_revision
        or not isinstance(sherpa["nativeRevision"], str)
        or not sherpa_revision.startswith(sherpa["nativeRevision"])
        or not 7 <= len(sherpa["nativeRevision"]) <= 40
    ):
        raise CompatibilityManifestError(
            "validation record Sherpa identity does not match the selected revision"
        )
    sherpa_profile = _record_object(
        sherpa["profile"], "validation record sherpa.profile"
    )
    _exact_keys(
        sherpa_profile,
        {"id", *EXPECTED_SHERPA_PROFILE},
        "validation record sherpa.profile",
    )
    _receipt_text(sherpa_profile["id"], "sherpa.profile.id")
    if any(
        sherpa_profile[key] != expected
        or type(sherpa_profile[key]) is not type(expected)
        for key, expected in EXPECTED_SHERPA_PROFILE.items()
    ):
        raise CompatibilityManifestError(
            "validation record Sherpa profile is outside the closed VAD contract"
        )

    fixtures = _record_object(value["fixtures"], "validation record fixtures")
    fixture_bindings = {
        "fonixModelSha256": "fonixModel",
        "fonixInputSha256": "fonixInput",
        "fonixReferenceOutputSha256": "fonixReferenceOutput",
        "fonixCancellationModelSha256": "fonixCancellationModel",
        "fonixCancellationInputSha256": "fonixCancellationInput",
        "sherpaModelSha256": "sherpaModel",
        "sherpaAudioSha256": "sherpaAudio",
        "sherpaReferenceSha256": "sherpaReference",
    }
    _exact_keys(fixtures, fixture_bindings, "validation record fixtures")
    for key in fixtures:
        _receipt_digest(fixtures[key], f"fixtures.{key}")

    bindings = _record_object(
        value["evidenceBindings"], "validation record evidenceBindings"
    )
    _exact_keys(bindings, EVIDENCE_BINDING_KEYS, "validation record evidenceBindings")
    for key in EVIDENCE_BINDING_KEYS:
        _record_identity(bindings[key], f"validation record evidenceBindings.{key}")
    expected_binding_hashes = {
        "receipt": load_order_receipt_sha256,
        "receiptSchema": expected_receipt_schema_sha256,
        "nativeVerifier": expected_native_verifier_sha256,
        "finalApk": build["finalApkSha256"],
        "harnessContract": build["harnessContractSha256"],
        "pubspecLock": build["pubspecLockSha256"],
        "targetEvidence": build["targetEvidenceSha256"],
        "logcatEvidence": build["logcatEvidenceSha256"],
        "launchChallenge": launch_challenge,
        **{
            binding_name: fixtures[fixture_name]
            for fixture_name, binding_name in fixture_bindings.items()
        },
    }
    for key, expected_hash in expected_binding_hashes.items():
        if bindings[key]["sha256"] != expected_hash:
            raise CompatibilityManifestError(
                f"validation record evidence binding {key} is inconsistent"
            )
    expected_tool_bindings = {
        "receiptSchema": {
            "sizeBytes": expected_receipt_schema_size,
            "sha256": expected_receipt_schema_sha256,
        },
        "nativeVerifier": {
            "sizeBytes": expected_native_verifier_size,
            "sha256": expected_native_verifier_sha256,
        },
    }
    for key, expected_identity in expected_tool_bindings.items():
        if bindings[key] != expected_identity:
            raise CompatibilityManifestError(
                f"validation record evidence binding {key} does not bind the "
                "current repository file"
            )

    initialization = _record_object(
        value["initialization"], "validation record initialization"
    )
    _exact_keys(
        initialization,
        {"events", "firstOwnerAliveWhenSecondReady"},
        "validation record initialization",
    )
    expected_events = (
        ["fonix-session-ready", "sherpa-vad-ready"]
        if matrix["loadOrder"] == "dart-first"
        else ["sherpa-vad-ready", "fonix-session-ready"]
    )
    if (
        initialization["events"] != expected_events
        or initialization["firstOwnerAliveWhenSecondReady"] is not True
    ):
        raise CompatibilityManifestError(
            "validation record initialization does not match its load order"
        )

    cycles, source_samples, reference_size = _validate_workload_record(
        value["workload"],
        profile=sherpa_profile,
        reference_sha256=fixtures["fonixReferenceOutputSha256"],
    )
    lifecycle = _validate_lifecycle_record(
        value["lifecycle"],
        profile=sherpa_profile,
        reference_sha256=fixtures["fonixReferenceOutputSha256"],
        source_samples=source_samples,
    )
    fonix_cancellation = lifecycle["fonixCancellation"]
    sherpa_cancellation = lifecycle["sherpaCancellation"]
    disposal = lifecycle["disposal"]
    stale = lifecycle["staleCompletion"]

    if bindings["finalApk"]["sizeBytes"] != final_identity.size_bytes:
        raise CompatibilityManifestError(
            "validation record final APK evidence size is inconsistent"
        )
    if bindings["fonixReferenceOutput"]["sizeBytes"] != reference_size:
        raise CompatibilityManifestError(
            "validation record Fonix reference evidence size is inconsistent"
        )

    native_libraries = _record_object(
        value["nativeLibraries"], "validation record nativeLibraries"
    )
    roles_by_profile = {
        "flutter-ffi": {
            "onnxRuntime": VERIFIER.ORT_NAME,
            "fonixShim": VERIFIER.SHIM_NAME,
            "sherpaCapi": VERIFIER.SHERPA_C_API_NAME,
            "sherpaCxxApi": VERIFIER.SHERPA_CXX_API_NAME,
        },
        "jni": {
            "onnxRuntime": VERIFIER.ORT_NAME,
            "fonixShim": VERIFIER.SHIM_NAME,
            "sherpaJni": VERIFIER.SHERPA_JNI_NAME,
        },
    }
    expected_roles = roles_by_profile[sherpa_library_profile]
    _exact_keys(
        native_libraries,
        expected_roles,
        "validation record nativeLibraries",
    )
    validated_libraries = {
        role: _validated_native_library(
            native_libraries[role],
            role=role,
            name=name,
            abi=abi,
            final_report=final_report,
        )
        for role, name in expected_roles.items()
    }

    claim_boundary = value["claimBoundary"]
    if (
        not isinstance(claim_boundary, str)
        or not claim_boundary
        or len(claim_boundary.encode("utf-8")) > 4096
    ):
        raise CompatibilityManifestError(
            "validation record claim boundary is missing or oversized"
        )

    return {
        "validationRecord": {
            "sizeBytes": record_size,
            "sha256": record_sha256,
        },
        "claimStatus": "offline-consistency-only",
        "targetEvidenceProvenance": "unverified",
        "validatorSha256": expected_validator_sha256,
        "receiptSchemaSha256": expected_receipt_schema_sha256,
        "nativeVerifierSha256": expected_native_verifier_sha256,
        "loadOrderReceiptSha256": load_order_receipt_sha256,
        "matrix": matrix,
        "build": build,
        "device": device,
        "process": process,
        "runtime": runtime,
        "sherpa": sherpa,
        "fixtures": fixtures,
        "initialization": initialization,
        "workload": {
            "requestedCycles": cycles,
            "completedCycles": cycles,
            "stepCount": cycles * 2,
            "sourceSamples": source_samples,
        },
        "lifecycle": {
            "fonixCancellationMode": fonix_cancellation["mode"],
            "sherpaCancellationMode": sherpa_cancellation["mode"],
            "staleSuppressedCount": stale["suppressedCount"],
            "disposalOrders": disposal.get("ordersTested"),
            "pendingFonixRuns": disposal["pendingFonixRuns"],
            "queuedSherpaSegments": disposal["queuedSherpaSegments"],
            "temporaryRootsRemaining": disposal["temporaryRootsRemaining"],
        },
        "evidenceBindings": bindings,
        "nativeLibraries": validated_libraries,
        "claimBoundary": (
            "This record satisfies the current repository's closed validation-"
            "record shape, type, hash, workload, lifecycle, and native-library "
            "contract for one exact Android APK tuple. Tool hashes do not "
            "authenticate who produced it; trusted external capture provenance "
            "is required before making a target-compatibility claim."
        ),
    }


def _validate_receipt_coverage(
    receipts: list[dict[str, Any]], abis: tuple[str, ...]
) -> None:
    seen: set[tuple[Any, ...]] = set()
    challenges: set[str] = set()
    target_evidence: set[str] = set()
    logcat_evidence: set[str] = set()
    record_hashes: set[str] = set()
    raw_receipt_hashes: set[str] = set()
    for receipt in receipts:
        matrix = receipt["matrix"]
        identity = (
            matrix["abi"],
            matrix["loadOrder"],
            matrix["buildType"],
            matrix["pageSizeBytes"],
        )
        if identity in seen:
            raise CompatibilityManifestError("duplicate load-order evidence tuple")
        seen.add(identity)
        challenges.add(receipt["process"]["launchChallengeSha256"])
        target_evidence.add(receipt["build"]["targetEvidenceSha256"])
        logcat_evidence.add(receipt["build"]["logcatEvidenceSha256"])
        record_hashes.add(receipt["validationRecord"]["sha256"])
        raw_receipt_hashes.add(receipt["loadOrderReceiptSha256"])
    if not (
        len(challenges)
        == len(target_evidence)
        == len(logcat_evidence)
        == len(record_hashes)
        == len(raw_receipt_hashes)
        == len(receipts)
    ):
        raise CompatibilityManifestError(
            "each load-order matrix run requires a distinct challenge and evidence tuple"
        )

    stable_keys = (
        ("build", "applicationId"),
        ("build", "finalApkSha256"),
        ("build", "harnessContractSha256"),
        ("build", "pubspecLockSha256"),
    )
    first = receipts[0]
    stable_runtime_keys = (
        "runtimeOwner",
        "runtimeSource",
        "ortVersion",
        "requiredOrtApi",
        "negotiatedOrtApi",
        "shimAbi",
        "shimBuildId",
    )
    for receipt in receipts[1:]:
        if any(
            receipt[parent][key] != first[parent][key]
            for parent, key in stable_keys
        ) or any(
            receipt[key] != first[key]
            for key in ("sherpa", "fixtures")
        ) or any(
            receipt["runtime"][key] != first["runtime"][key]
            for key in stable_runtime_keys
        ) or receipt["workload"] != first["workload"] or any(
            receipt["evidenceBindings"][key]
            != first["evidenceBindings"][key]
            for key in STABLE_EVIDENCE_BINDING_KEYS
        ):
            raise CompatibilityManifestError(
                "load-order validation records do not describe one build and fixture set"
            )

    required_pairs = {
        (load_order, page_size)
        for load_order in LOAD_ORDERS
        for page_size in PAGE_SIZES
    }
    for abi in abis:
        abi_receipts = [
            receipt for receipt in receipts if receipt["matrix"]["abi"] == abi
        ]
        observed_pairs = {
            (receipt["matrix"]["loadOrder"], receipt["matrix"]["pageSizeBytes"])
            for receipt in abi_receipts
        }
        if observed_pairs != required_pairs or len(abi_receipts) != len(
            required_pairs
        ):
            raise CompatibilityManifestError(
                "the full load-order/page-size Cartesian matrix is not "
                f"proven for {abi}"
            )
        if any(
            receipt["runtime"] != abi_receipts[0]["runtime"]
            for receipt in abi_receipts[1:]
        ):
            raise CompatibilityManifestError(
                f"load-order validation records disagree on the runtime for {abi}"
            )


def _validate_source_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
        or not value.isascii()
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CompatibilityManifestError("sherpa source must be a plain HTTPS URL")
    if len(value.encode("utf-8")) > 1024:
        raise CompatibilityManifestError("sherpa source URL is oversized")
    return value


def _read_qnn_qualification(
    path: Path,
    *,
    mode: str,
    abis: tuple[str, ...],
    build_type: str,
    final_sha256: str,
    runtime_version: str,
    ort_api: int,
    final_ort_by_abi: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if mode != "aligned":
        raise CompatibilityManifestError(
            "QNN qualification is accepted only for aligned mode"
        )
    value, record_sha256, _record_size = _read_strict_json(
        path, "QNN qualification record", MAX_QNN_QUALIFICATION_BYTES
    )
    _exact_keys(
        value,
        {
            "schemaVersion",
            "result",
            "validatorSha256",
            "qualificationReceiptSha256",
            "build",
            "model",
            "runtime",
            "qnn",
            "device",
            "assignment",
            "executions",
            "contextCache",
            "loadOrders",
            "evidenceBindings",
            "claimBoundary",
        },
        "QNN qualification record",
    )
    if value["schemaVersion"] != 1 or value["result"] != "passed":
        raise CompatibilityManifestError("QNN qualification record did not pass")
    _receipt_digest(value["validatorSha256"], "QNN validator digest")
    _receipt_digest(
        value["qualificationReceiptSha256"], "QNN qualification receipt digest"
    )
    build = value["build"]
    runtime = value["runtime"]
    device = value["device"]
    qnn = value["qnn"]
    assignment = value["assignment"]
    if not all(
        isinstance(record, dict)
        for record in (build, runtime, device, qnn, assignment)
    ):
        raise CompatibilityManifestError(
            "QNN qualification core bindings must be objects"
        )
    if (
        build.get("buildType") != build_type
        or build.get("finalApkSha256") != final_sha256
        or not isinstance(build.get("finalApk"), dict)
        or build["finalApk"].get("sha256") != final_sha256
    ):
        raise CompatibilityManifestError(
            "QNN qualification belongs to another final artifact/build type"
        )
    abi = device.get("abi")
    if abi not in abis:
        raise CompatibilityManifestError(
            "QNN qualification ABI is not declared by this build"
        )
    if (
        runtime.get("ortVersion") != runtime_version
        or runtime.get("requiredOrtApi") != ort_api
        or runtime.get("ortSha256") != final_ort_by_abi[abi]["sha256"]
    ):
        raise CompatibilityManifestError(
            "QNN qualification runtime does not match the final aligned runtime"
        )
    for field in (
        "sdkManifestSha256",
        "backendLibrarySha256",
        "providerOptionsSha256",
    ):
        _receipt_digest(qnn.get(field), f"QNN qualification {field}")
    _receipt_text(qnn.get("backendId"), "QNN qualification backendId")
    total_nodes = assignment.get("totalNodes")
    if (
        assignment.get("fallbackPolicy") != "reject-cpu"
        or assignment.get("cpuNodes") != 0
        or not isinstance(total_nodes, int)
        or isinstance(total_nodes, bool)
        or not 0 < total_nodes <= 1_000_000
        or assignment.get("qnnNodes") != total_nodes
    ):
        raise CompatibilityManifestError(
            "QNN qualification does not prove zero CPU fallback"
        )
    load_orders = value["loadOrders"]
    if not isinstance(load_orders, list) or len(load_orders) != 2 or any(
        not isinstance(entry, dict) for entry in load_orders
    ):
        raise CompatibilityManifestError(
            "QNN qualification does not prove both load orders with parity"
        )
    if (
        {entry.get("loadOrder") for entry in load_orders} != LOAD_ORDERS
        or any(entry.get("parity") is not True for entry in load_orders)
    ):
        raise CompatibilityManifestError(
            "QNN qualification does not prove both load orders with parity"
        )
    if not isinstance(value["claimBoundary"], str) or not value["claimBoundary"]:
        raise CompatibilityManifestError("QNN qualification claim boundary is missing")
    return {"recordSha256": record_sha256, **value}


def generate_manifest(arguments: argparse.Namespace) -> dict[str, Any]:
    abis = tuple(sorted(set(arguments.abi)))
    if not abis or len(abis) != len(arguments.abi):
        raise CompatibilityManifestError("--abi must be non-empty and unique")
    if any(abi not in VERIFIER.ANDROID_ABIS for abi in abis):
        raise CompatibilityManifestError("an unsupported Android ABI was requested")
    if arguments.ort_api_required != 27:
        raise CompatibilityManifestError("this snapshot requires ORT C API 27")
    if arguments.build_type not in BUILD_TYPES:
        raise CompatibilityManifestError("final build type is outside the matrix")
    if VERSION.fullmatch(arguments.ort_version_observed) is None:
        raise CompatibilityManifestError("observed ORT version must be strict semver")
    ort_major, ort_minor, _ort_patch = (
        int(part) for part in arguments.ort_version_observed.split(".")
    )
    if ort_major != 1 or ort_minor < 27:
        raise CompatibilityManifestError(
            "observed ORT version cannot expose the required C API 27"
        )
    if REVISION.fullmatch(arguments.sherpa_revision) is None:
        raise CompatibilityManifestError("sherpa revision must be a full commit SHA")
    if ISO_DATE.fullmatch(arguments.snapshot_date) is None:
        raise CompatibilityManifestError("snapshot date must be YYYY-MM-DD")
    try:
        date.fromisoformat(arguments.snapshot_date)
    except ValueError as error:
        raise CompatibilityManifestError("snapshot date must be YYYY-MM-DD") from error

    sherpa_paths = _resolve_unique_paths(
        arguments.sherpa_artifact, "sherpa artifact"
    )
    wrapper_paths = _resolve_unique_paths(
        arguments.wrapper_artifact, "wrapper artifact"
    )
    final_path = _resolve_regular_path(
        arguments.final_artifact, "final artifact", MAX_ARTIFACT_BYTES
    )
    if arguments.mode == "sherpa-owned" and arguments.runtime_artifact:
        raise CompatibilityManifestError(
            "sherpa-owned mode derives ORT from the sherpa artifacts and "
            "does not accept --runtime-artifact"
        )
    if arguments.mode == "aligned" and not arguments.runtime_artifact:
        raise CompatibilityManifestError("aligned mode requires --runtime-artifact")
    runtime_paths = (
        sherpa_paths
        if arguments.mode == "sherpa-owned"
        else _resolve_unique_paths(arguments.runtime_artifact, "runtime artifact")
    )
    _reject_cross_role_overlap(
        sherpa_paths, "sherpa", wrapper_paths, "wrapper"
    )
    _reject_cross_role_overlap(
        wrapper_paths, "wrapper", runtime_paths, "runtime"
    )
    if arguments.mode == "aligned":
        _reject_cross_role_overlap(
            sherpa_paths, "sherpa", runtime_paths, "runtime"
        )

    validation_record_paths = tuple(
        _resolve_regular_path(
            record,
            "load-order validation record",
            MAX_VALIDATION_RECORD_BYTES,
        )
        for record in arguments.load_order_validation_record
    )
    qnn_path = (
        None
        if arguments.qnn_qualification_record is None
        else _resolve_regular_path(
            arguments.qnn_qualification_record,
            "QNN qualification record",
            MAX_QNN_QUALIFICATION_BYTES,
        )
    )
    all_native_paths = tuple(
        dict.fromkeys((*sherpa_paths, *wrapper_paths, *runtime_paths))
    )
    _reject_output_overlap(
        arguments.output,
        exact_inputs=(
            *all_native_paths,
            final_path,
            *validation_record_paths,
            *((qnn_path,) if qnn_path is not None else ()),
            Path(__file__).resolve(strict=True),
            VERIFIER_PATH.resolve(strict=True),
            LOAD_ORDER_VALIDATOR_PATH.resolve(strict=True),
            LOAD_ORDER_RECEIPT_SCHEMA_PATH.resolve(strict=True),
        ),
        native_inputs=all_native_paths,
    )

    sherpa_reports = _inspect_many(sherpa_paths, "sherpa artifact")
    wrapper_reports = _inspect_many(wrapper_paths, "wrapper artifact")
    runtime_reports = (
        sherpa_reports
        if arguments.mode == "sherpa-owned"
        else _inspect_many(runtime_paths, "runtime artifact")
    )
    final_report = _inspect(final_path, "final artifact")
    for index, report in enumerate(sherpa_reports):
        _validate_ort_candidate_inventory(report, f"sherpa artifact {index + 1}")
    for index, report in enumerate(wrapper_reports):
        _validate_ort_candidate_inventory(report, f"wrapper artifact {index + 1}")
    for index, report in enumerate(runtime_reports):
        _validate_ort_candidate_inventory(report, f"runtime artifact {index + 1}")
    _validate_ort_candidate_inventory(final_report, "final artifact")
    _validate_final_graph(final_report, abis)

    undeclared_input_abis = sorted(
        {
            entry["abi"]
            for report in _unique_reports(
                sherpa_reports, wrapper_reports, runtime_reports
            )
            for entry in report["libraries"]
        }
        - set(abis)
    )
    if undeclared_input_abis:
        raise CompatibilityManifestError(
            "input artifacts contain undeclared ABIs: "
            + ", ".join(undeclared_input_abis)
        )
    if any(report["ort_candidates"] for report in wrapper_reports):
        raise CompatibilityManifestError(
            "external Fonix wrapper artifacts must own no ORT"
        )
    if arguments.mode == "aligned" and any(
        report["ort_candidates"] for report in sherpa_reports
    ):
        raise CompatibilityManifestError(
            "aligned sherpa artifacts must own no ORT; the application runtime "
            "artifacts are the sole source owners"
        )

    sherpa_identities = _identity_map(sherpa_paths, sherpa_reports)
    wrapper_identities = _identity_map(wrapper_paths, wrapper_reports)
    runtime_identities = (
        sherpa_identities
        if arguments.mode == "sherpa-owned"
        else _identity_map(runtime_paths, runtime_reports)
    )
    all_source_reports = _unique_reports(
        sherpa_reports, wrapper_reports, runtime_reports
    )

    source_entries_by_abi: dict[
        str, dict[str, tuple[dict[str, Any], dict[str, Any]]]
    ] = {}
    for abi in abis:
        entries_by_name: dict[
            str, list[tuple[dict[str, Any], dict[str, Any]]]
        ] = {}
        for report in all_source_reports:
            for entry in report["libraries"]:
                if entry["abi"] == abi:
                    entries_by_name.setdefault(entry["name"], []).append(
                        (report, entry)
                    )
        duplicates = sorted(
            name for name, entries in entries_by_name.items() if len(entries) != 1
        )
        if duplicates:
            raise CompatibilityManifestError(
                f"multiple input artifacts own native libraries for {abi}: "
                + ", ".join(duplicates)
            )
        source_entries_by_abi[abi] = {
            name: entries[0] for name, entries in entries_by_name.items()
        }
        final_names = {
            entry["name"]
            for entry in final_report["libraries"]
            if entry["abi"] == abi
        }
        unexpected_final = sorted(
            final_names
            - set(entries_by_name)
            - FINAL_PLATFORM_LIBRARY_NAMES
        )
        if unexpected_final:
            raise CompatibilityManifestError(
                f"final artifact contains native libraries without a selected "
                f"source input for {abi}: {', '.join(unexpected_final)}"
            )

    libraries_by_abi: dict[str, Any] = {}
    final_ort_by_abi: dict[str, dict[str, Any]] = {}
    for abi in abis:
        runtime_report, runtime_ort = _single_across(
            runtime_reports, VERIFIER.ORT_NAME, abi, "runtime owner artifacts"
        )
        wrapper_report, wrapper_shim = _single_across(
            wrapper_reports, VERIFIER.SHIM_NAME, abi, "wrapper artifacts"
        )
        final_ort = _single(final_report, VERIFIER.ORT_NAME, abi, "final artifact")
        final_shim = _single(final_report, VERIFIER.SHIM_NAME, abi, "final artifact")
        if runtime_ort["elf"]["soname"] != VERIFIER.ORT_NAME:
            raise CompatibilityManifestError(
                f"runtime owner has wrong ORT SONAME for {abi}"
            )
        if (
            wrapper_shim["elf"]["soname"] != VERIFIER.SHIM_NAME
            or VERIFIER.ORT_NAME in wrapper_shim["elf"]["needed"]
        ):
            raise CompatibilityManifestError(
                f"Fonix wrapper is not an external/process shim for {abi}"
            )
        _tie_source_to_final(runtime_ort, final_ort, "ORT", abi)
        _tie_source_to_final(wrapper_shim, final_shim, "Fonix shim", abi)

        sherpa_consumers: dict[str, Any] = {}
        for (
            name,
            source_report,
            source_entry,
            final_entry,
        ) in _profile_consumers(
            sherpa_reports,
            final_report,
            arguments.sherpa_library_profile,
            abi,
        ):
            _tie_source_to_final(source_entry, final_entry, name, abi)
            sherpa_consumers[name] = _library_record(
                source_entry,
                final_entry,
                sherpa_identities[source_report["artifact"]],
            )

        source_libcxx = _entries_across(
            all_source_reports, VERIFIER.LIBCXX_NAME, abi
        )
        final_libcxx = _entries(final_report, VERIFIER.LIBCXX_NAME, abi)
        if len(source_libcxx) > 1:
            raise CompatibilityManifestError(
                f"multiple input artifacts own {VERIFIER.LIBCXX_NAME} for {abi}"
            )
        if bool(source_libcxx) != bool(final_libcxx):
            raise CompatibilityManifestError(
                f"source/final {VERIFIER.LIBCXX_NAME} ownership differs for {abi}"
            )

        libcxx_record = None
        if source_libcxx:
            libcxx_report, libcxx_source = source_libcxx[0]
            libcxx_final = final_libcxx[0]
            _tie_source_to_final(
                libcxx_source, libcxx_final, VERIFIER.LIBCXX_NAME, abi
            )
            source_identity = (
                sherpa_identities.get(libcxx_report["artifact"])
                or wrapper_identities.get(libcxx_report["artifact"])
                or runtime_identities[libcxx_report["artifact"]]
            )
            libcxx_record = _library_record(
                libcxx_source, libcxx_final, source_identity
            )

        core_names = {
            VERIFIER.ORT_NAME,
            VERIFIER.SHIM_NAME,
            VERIFIER.LIBCXX_NAME,
            *KNOWN_SHERPA_LIBRARY_NAMES,
        }
        companion_records: dict[str, Any] = {}
        for name, (source_report, source_entry) in sorted(
            source_entries_by_abi[abi].items()
        ):
            if name in core_names:
                continue
            final_entry = _single(final_report, name, abi, "final artifact")
            _tie_source_to_final(source_entry, final_entry, name, abi)
            source_identity = (
                sherpa_identities.get(source_report["artifact"])
                or wrapper_identities.get(source_report["artifact"])
                or runtime_identities[source_report["artifact"]]
            )
            companion_records[name] = _library_record(
                source_entry, final_entry, source_identity
            )

        final_ort_by_abi[abi] = final_ort
        libraries_by_abi[abi] = {
            "onnxruntime": _library_record(
                runtime_ort,
                final_ort,
                runtime_identities[runtime_report["artifact"]],
            ),
            "sherpaRuntimeConsumers": sherpa_consumers,
            "fonixShim": _library_record(
                wrapper_shim,
                final_shim,
                wrapper_identities[wrapper_report["artifact"]],
            ),
            "libcxxShared": libcxx_record,
            "companionLibraries": companion_records,
        }

    final_identity = final_report["_fonixArtifactIdentity"]
    validator_identity = _stable_file_identity(
        LOAD_ORDER_VALIDATOR_PATH,
        "load-order validator",
        MAX_VALIDATION_RECORD_BYTES,
    )
    expected_validator_sha256 = validator_identity["sha256"]
    receipt_schema_identity = _stable_file_identity(
        LOAD_ORDER_RECEIPT_SCHEMA_PATH,
        "load-order receipt schema",
        MAX_VALIDATION_RECORD_BYTES,
    )
    expected_receipt_schema_sha256 = receipt_schema_identity["sha256"]
    native_verifier_identity = _stable_file_identity(
        VERIFIER_PATH, "Android native verifier", MAX_VALIDATION_RECORD_BYTES
    )
    if native_verifier_identity != EXECUTED_VERIFIER_IDENTITY:
        raise CompatibilityManifestError(
            "Android native verifier changed after its exact bytes were loaded"
        )
    expected_native_verifier_sha256 = native_verifier_identity["sha256"]
    receipts = [
        _read_validation_record(
            record,
            abis=abis,
            mode=arguments.mode,
            sherpa_library_profile=arguments.sherpa_library_profile,
            sherpa_revision=arguments.sherpa_revision,
            final_identity=final_identity,
            runtime_version=arguments.ort_version_observed,
            ort_api=arguments.ort_api_required,
            expected_build_type=arguments.build_type,
            final_report=final_report,
            final_ort_by_abi=final_ort_by_abi,
            expected_validator_sha256=expected_validator_sha256,
            expected_receipt_schema_sha256=expected_receipt_schema_sha256,
            expected_receipt_schema_size=receipt_schema_identity["sizeBytes"],
            expected_native_verifier_sha256=expected_native_verifier_sha256,
            expected_native_verifier_size=native_verifier_identity["sizeBytes"],
        )
        for record in validation_record_paths
    ]
    _validate_receipt_coverage(receipts, abis)
    qnn_qualification = (
        None
        if qnn_path is None
        else _read_qnn_qualification(
            qnn_path,
            mode=arguments.mode,
            abis=abis,
            build_type=arguments.build_type,
            final_sha256=final_identity.sha256,
            runtime_version=arguments.ort_version_observed,
            ort_api=arguments.ort_api_required,
            final_ort_by_abi=final_ort_by_abi,
        )
    )

    ordered_sherpa_identities = sorted(
        (identity.to_json() for identity in sherpa_identities.values()),
        key=lambda value: (value["fileName"], value["sha256"]),
    )
    ordered_wrapper_identities = sorted(
        (identity.to_json() for identity in wrapper_identities.values()),
        key=lambda value: (value["fileName"], value["sha256"]),
    )
    ordered_runtime_identities = sorted(
        (identity.to_json() for identity in runtime_identities.values()),
        key=lambda value: (value["fileName"], value["sha256"]),
    )

    return {
        "schemaVersion": 2,
        "claimStatus": "offline-consistency-only",
        "snapshotDate": arguments.snapshot_date,
        "sherpaOnnx": {
            "source": _validate_source_url(arguments.sherpa_source),
            "revision": arguments.sherpa_revision,
            "libraryProfile": arguments.sherpa_library_profile,
            "artifacts": ordered_sherpa_identities,
        },
        "android": {
            "integrationMode": arguments.mode,
            "buildType": arguments.build_type,
            "abis": list(abis),
            "ortOwner": "sherpa" if arguments.mode == "sherpa-owned" else "application",
            "ortVersionObserved": arguments.ort_version_observed,
            "ortApiRequired": arguments.ort_api_required,
            "artifacts": {
                "wrapperInputs": ordered_wrapper_identities,
                "runtimeOwnerInputs": ordered_runtime_identities,
                "final": final_identity.to_json(),
            },
            "librariesByAbi": libraries_by_abi,
            "loadOrderEvidence": sorted(
                receipts,
                key=lambda receipt: (
                    receipt["matrix"]["abi"],
                    receipt["matrix"]["loadOrder"],
                    receipt["matrix"]["buildType"],
                    receipt["matrix"]["pageSizeBytes"],
                ),
            ),
            "qnnQualification": qnn_qualification,
        },
        "claimBoundary": (
            "This record ties inspected source native-library inventories and "
            "final loaded segments to records satisfying the repository's "
            "offline consistency contract. Directory digests cover only their "
            "closed native-library inventories. Neither tool hashes nor JSON "
            "bindings authenticate device capture; trusted external capture "
            "provenance is required before claiming target compatibility."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sherpa-owned", "aligned"), required=True)
    parser.add_argument("--sherpa-source", required=True)
    parser.add_argument("--sherpa-revision", required=True)
    parser.add_argument(
        "--sherpa-library-profile",
        choices=sorted(SHERPA_LIBRARY_PROFILES),
        default="jni",
        help=(
            "Closed sherpa native-library topology. Defaults to legacy jni "
            "for command compatibility; current Flutter FFI packages must "
            "select flutter-ffi explicitly."
        ),
    )
    parser.add_argument(
        "--sherpa-artifact", action="append", type=Path, required=True
    )
    parser.add_argument(
        "--wrapper-artifact", action="append", type=Path, required=True
    )
    parser.add_argument("--runtime-artifact", action="append", type=Path)
    parser.add_argument("--final-artifact", type=Path, required=True)
    parser.add_argument("--abi", action="append", required=True)
    parser.add_argument("--ort-version-observed", required=True)
    parser.add_argument("--ort-api-required", type=int, required=True)
    parser.add_argument("--build-type", choices=sorted(BUILD_TYPES), required=True)
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument(
        "--load-order-validation-record",
        action="append",
        type=Path,
        required=True,
    )
    parser.add_argument("--qnn-qualification-record", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _write_new(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise CompatibilityManifestError("output must not already exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise CompatibilityManifestError("temporary output already exists")
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor: int | None = None
    owned_temporary: tuple[int, int] | None = None
    publication_attempted = False
    linked_output: tuple[int, int] | None = None
    publication_complete = False

    def opened_output_is_exact() -> tuple[os.stat_result, bool]:
        assert descriptor is not None
        metadata = os.fstat(descriptor)
        if metadata.st_size != len(encoded):
            return metadata, False
        os.lseek(descriptor, 0, os.SEEK_SET)
        observed = bytearray()
        while len(observed) <= len(encoded):
            chunk = os.read(
                descriptor,
                min(1024 * 1024, len(encoded) + 1 - len(observed)),
            )
            if not chunk:
                break
            observed.extend(chunk)
        return metadata, bytes(observed) == encoded

    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        initial_status = os.fstat(descriptor)
        owned_temporary = (initial_status.st_dev, initial_status.st_ino)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CompatibilityManifestError(
                    "could not write the compatibility manifest"
                )
            view = view[written:]
        os.fsync(descriptor)
        opened, opened_is_exact = opened_output_is_exact()
        if not opened_is_exact:
            raise CompatibilityManifestError(
                "temporary output bytes are not the completed manifest"
            )
        linked_source = temporary.lstat()
        if (
            not stat.S_ISREG(linked_source.st_mode)
            or opened.st_dev != linked_source.st_dev
            or opened.st_ino != linked_source.st_ino
            or opened.st_mode != linked_source.st_mode
            or opened.st_size != linked_source.st_size
        ):
            raise CompatibilityManifestError(
                "temporary output changed before publication"
            )
        publication_attempted = True
        os.link(temporary, path, follow_symlinks=False)
        published = path.lstat()
        linked_output = (published.st_dev, published.st_ino)
        opened_after_link, published_bytes_are_exact = opened_output_is_exact()
        if (
            not stat.S_ISREG(published.st_mode)
            or opened_after_link.st_dev != published.st_dev
            or opened_after_link.st_ino != published.st_ino
            or opened_after_link.st_mode != published.st_mode
            or opened_after_link.st_size != published.st_size
            or not published_bytes_are_exact
        ):
            raise CompatibilityManifestError(
                "published output is not the completed manifest"
            )
        publication_complete = True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if (
            publication_attempted
            and not publication_complete
            and (linked_output is not None or owned_temporary is not None)
        ):
            try:
                published_status = path.lstat()
            except FileNotFoundError:
                pass
            else:
                cleanup_identity = linked_output or owned_temporary
                if cleanup_identity is not None and (
                    published_status.st_dev,
                    published_status.st_ino,
                ) == cleanup_identity:
                    path.unlink()
        if owned_temporary is not None:
            try:
                temporary_status = temporary.lstat()
            except FileNotFoundError:
                pass
            else:
                if (
                    temporary_status.st_dev,
                    temporary_status.st_ino,
                ) == owned_temporary:
                    temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        manifest = generate_manifest(arguments)
        output = arguments.output
        _write_new(output, manifest)
    except (CompatibilityManifestError, FileNotFoundError, OSError) as error:
        print(f"android_compatibility_manifest: {error}", file=sys.stderr)
        return 1
    print(f"Wrote Android compatibility manifest: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
