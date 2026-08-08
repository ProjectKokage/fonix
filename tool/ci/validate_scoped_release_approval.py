#!/usr/bin/env python3
"""Validate an external Fonix scoped-release evidence and approval bundle.

The externally supplied bundle digest is the authority pin for the detached
approval material.  This validator checks the closed candidate, repository
baseline, referenced bytes, and externally produced signature-verification
receipts.  It does not itself perform cryptographic signature verification and
does not weaken the separate five-platform release gate.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
from types import ModuleType
from typing import Any, Iterable, Iterator, Mapping

sys.dont_write_bytecode = True


MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_SCHEMA_BYTES = 512 * 1024
MAX_REPOSITORY_FILE_BYTES = 16 * 1024 * 1024
MAX_HELPER_BYTES = 2 * 1024 * 1024
MAX_JSON_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_FILE_BYTES = 1024 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_EVIDENCE_REFERENCES = 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_JSON_DEPTH = 48
MAX_JSON_NODES = 131_072
MAX_JSON_STRING_BYTES = 256 * 1024

SCOPE_PATH = "release/scoped-pre-1.0-v1.json"
SCHEMA_PATH = "templates/ci/scoped_release_approval.schema.json"
MACOS_CPU_ASSIGNMENT_SCHEMA_PATH = (
    "templates/ci/macos_cpu_assignment_receipt_v1.schema.json"
)
SCOPE_HELPER_PATH = "tool/ci/validate_scoped_release_scope.py"
SOURCE_HELPER_PATH = "tool/ci/source_checksum_manifest.py"
SOURCE_ARCHIVE_HELPER_PATH = "tool/ci/validate_source_release_archive.py"
SOURCE_MANIFEST_PATH = "MANIFEST.sha256"
PUBSPEC_LOCK_PATH = "pubspec.lock"
NATIVE_LOCK_PATH = "native/versions.lock.yaml"
SHERPA_LOCK_PATH = "templates/android/sherpa_reference_app/pubspec.lock"

EXPECTED_SCHEMA_SHA256 = (
    "a92a0dc73b270906a12d0389a09d22f590196f4e1dbf3c3b957d73d85febb0f2"
)
EXPECTED_MACOS_CPU_ASSIGNMENT_SCHEMA_SHA256 = (
    "81b16c9db50136206aaa9c0b3bafd048e74589f46d731a8a5087570b893aaa77"
)
EXPECTED_SCOPE_HELPER_SHA256 = (
    "fb498411be31111c4540b60d4c18090055aacd61c08646877ca9cefc4b3f0e9d"
)
EXPECTED_SOURCE_HELPER_SHA256 = (
    "9ef720e3bae376a01b4b61c2b2a4214c31760075c23bd64dca4fb887641dd5b6"
)
EXPECTED_SOURCE_ARCHIVE_HELPER_SHA256 = (
    "3ef7aa8ca585b9a260f6e4e01cb8fe242fc02317fd83acdc5524782208de4751"
)

POLICY_ID = "scoped-pre-1.0-cpu-v1"
BUNDLE_CLAIM_STATUS = "candidate-evidence-only"
BUNDLE_CLAIM_BOUNDARY = (
    "This bundle binds candidate evidence and detached approval artifacts only. "
    "It does not itself establish readiness, verify signatures, authorize "
    "publication or distribution, or weaken the global five-platform release gate."
)
REPORT_CLAIM_BOUNDARY = (
    "Readiness applies only to the exact scoped CPU candidate and externally "
    "pinned bundle. Signature-verification receipts are external trust inputs; "
    "this validator does not perform cryptographic verification, authorize "
    "publication or distribution, or weaken the global five-platform release gate."
)
SOURCE_ARCHIVE_CLAIM_BOUNDARY = (
    "Offline source-closure validation only. It binds one archive's declared "
    "Git revision, closed member inventory, contents, manifest, and executable "
    "semantics to the current repository baseline; it does not authenticate "
    "archive origin, prove build reproducibility or target behavior, establish "
    "licensing, signing, or readiness, or authorize publication or distribution."
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@-]{0,127}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)

_ROOT_KEYS = frozenset(
    {
        "schemaVersion",
        "claimStatus",
        "candidateStatement",
        "candidateSubjectSha256",
        "detachedApprovals",
        "claimBoundary",
    }
)
_CANDIDATE_KEYS = frozenset(
    {"bundleId", "scope", "source", "compositions", "sharedRegressionEvidence"}
)
_SCOPE_KEYS = frozenset(
    {"policyId", "scopePath", "scopeSha256", "validationRecord"}
)
_SOURCE_KEYS = frozenset(
    {
        "sourceRevision",
        "sourceArchive",
        "sourceManifest",
        "dartPubspecLock",
        "nativeLock",
        "sherpaPubspecLock",
        "packageName",
        "packageVersion",
    }
)
_COMPOSITION_KEYS = frozenset({"id", "target", "runtime", "provider", "evidence"})
_TARGET_KEYS = frozenset({"os", "architecture", "variant", "flavor", "minimumOs"})
_RUNTIME_KEYS = frozenset({"kind", "artifactId", "runtimeOwner", "runtimeMode"})
_RUNTIME_IDENTITY_KEYS = frozenset(
    {"ownerPackage", "ownerPackageVersion", "onnxRuntimeVersion"}
)
_PROVIDER_KEYS = frozenset({"id", "requirement"})
_EVIDENCE_KEYS = (
    "targetExecutionRecords",
    "providerAssignmentRecords",
    "finalPackageRecords",
    "sbomRecords",
    "auditRecords",
    "reproducibilityRecords",
    "noticesRecords",
    "signingRecords",
)
_EVIDENCE_KEY_SET = frozenset(_EVIDENCE_KEYS)
_SHARED_KEYS = frozenset({"category", "records"})
_REFERENCE_KEYS = frozenset({"id", "path", "sha256", "sizeBytes", "mediaType"})
_SOURCE_ARCHIVE_RECORD_KEYS = frozenset(
    {
        "schemaVersion",
        "result",
        "claimStatus",
        "purpose",
        "validationScope",
        "archive",
        "source",
        "tools",
        "schemas",
        "claimBoundary",
    }
)
_SOURCE_ARCHIVE_IDENTITY_KEYS = frozenset({"sizeBytes", "sha256"})
_SOURCE_ARCHIVE_SOURCE_KEYS = frozenset(
    {
        "revision",
        "revisionBinding",
        "manifestPath",
        "manifestSha256",
        "manifestSizeBytes",
        "manifestEntryCount",
        "memberCount",
        "regularFileCount",
        "directoryCount",
        "executableFileCount",
        "expandedBytes",
        "inventorySha256",
    }
)
_APPROVAL_ENTRY_KEYS = frozenset(
    {
        "category",
        "approverId",
        "keyId",
        "signatureAlgorithm",
        "statement",
        "signature",
        "verificationReceipt",
    }
)
_APPROVAL_STATEMENT_KEYS = frozenset(
    {
        "schemaVersion",
        "category",
        "candidateSubjectSha256",
        "decision",
        "decidedAt",
        "approverId",
        "keyId",
        "signatureAlgorithm",
    }
)
_VERIFICATION_RECEIPT_KEYS = frozenset(
    {
        "schemaVersion",
        "category",
        "candidateSubjectSha256",
        "approvalStatementSha256",
        "signatureSha256",
        "approverId",
        "keyId",
        "signatureAlgorithm",
        "verified",
        "verifiedAt",
        "verifierId",
    }
)

MEDIA_TYPES = frozenset(
    {
        "application/gzip",
        "application/json",
        "application/octet-stream",
        "application/spdx+json",
        "application/vnd.android.package-archive",
        "application/zip",
        "text/plain",
    }
)
JSON_MEDIA_TYPES = frozenset({"application/json", "application/spdx+json"})
SIGNATURE_ALGORITHMS = frozenset(
    {"ed25519", "ecdsa-p256-sha256", "rsa-pss-sha256"}
)
SHARED_CATEGORIES = (
    "source-closure",
    "dart-analysis-tests",
    "bindings-reproduction",
    "native-tests-sanitizers",
    "lifecycle-cancellation-stress",
    "android-qnn-contract-tamper",
    "windows-source-cross-build-loader-security",
)
APPROVAL_CATEGORIES = ("api-abi", "licensing", "security", "signing", "publication")


class ScopedReleaseApprovalError(RuntimeError):
    """An input violates the closed scoped-release approval contract."""


@dataclass(frozen=True)
class EvidenceReference:
    identifier: str
    path: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True)
class EvidenceContents:
    raw: bytes | None
    parsed_json: dict[str, Any] | None


@dataclass(frozen=True)
class RetainedEvidence:
    """One verified evidence descriptor borrowed for bounded inspection."""

    descriptor: int
    contents: EvidenceContents


@dataclass
class ValidatedOutput:
    """An output name bound to one already-opened, no-follow parent directory."""

    path: Path
    parent_path: Path
    name: str
    parent_descriptor: int
    parent_identity: tuple[int, int, int]
    closed: bool = False

    def __fspath__(self) -> str:
        return str(self.path)

    def close(self) -> None:
        if not self.closed:
            os.close(self.parent_descriptor)
            self.closed = True


@dataclass(frozen=True)
class CompositionContract:
    identifier: str
    target: dict[str, Any]
    runtime: dict[str, Any]
    provider: dict[str, Any]


@dataclass(frozen=True)
class EvidenceRecordContract:
    identifier: str
    media_type: str
    semantic_contract: str | None = None


SEMANTIC_VALIDATION_MODE = "closed-default-deny-v1"
MACOS_CPU_ASSIGNMENT_SEMANTIC_CONTRACT = (
    "macos-arm64-cpu-full-assignment-v1"
)
MACOS_CPU_ASSIGNMENT_RECEIPT: dict[str, Any] = {
    "schemaVersion": 1,
    "status": "passed",
    "runtimeVersion": "1.27.1",
    "runtimeSource": "bundled",
    "runtimeOwner": "wrapper",
    "artifactFlavor": "cpu",
    "platform": "macos",
    "architecture": "arm64",
    "shimBuildId": "onnxruntime-1.27.1-macos-arm64-cpu",
    "artifactSha256": (
        "e42b77a7281cc6e55141bf44fcfbac2c782b823a491bbb6ac33c781dd991f8a6"
    ),
    "modelSha256": (
        "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10"
    ),
    "outputValues": [1, 4, 9, 16, 25, 36],
    "activeProviders": ["cpu"],
    "fullCpuAssignment": True,
    "doubleClose": "passed",
}


CPU_PROVIDER = {"id": "cpu", "requirement": "full-assignment"}
ANDROID_TARGET = {
    "os": "android",
    "architecture": "arm64-v8a",
    "variant": "default",
    "flavor": "cpu",
    "minimumOs": "24",
}
COMPOSITION_CONTRACTS = (
    CompositionContract(
        "ios-arm64-device-cpu-linked",
        {
            "os": "ios",
            "architecture": "arm64",
            "variant": "device",
            "flavor": "cpu",
            "minimumOs": "15.1",
        },
        {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-ios-arm64-device-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "linked",
        },
        CPU_PROVIDER,
    ),
    CompositionContract(
        "macos-arm64-default-cpu-bundled",
        {
            "os": "macos",
            "architecture": "arm64",
            "variant": "default",
            "flavor": "cpu",
            "minimumOs": "14.0",
        },
        {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-macos-arm64-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "bundled",
        },
        CPU_PROVIDER,
    ),
    CompositionContract(
        "android-arm64-v8a-default-cpu-bundled",
        ANDROID_TARGET,
        {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-android-arm64-v8a-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "bundled",
        },
        CPU_PROVIDER,
    ),
    CompositionContract(
        "android-arm64-v8a-default-cpu-sherpa-process",
        ANDROID_TARGET,
        {
            "kind": "android-sherpa-process",
            "artifactId": None,
            "runtimeOwner": "sherpa",
            "runtimeMode": "process",
            "runtimeIdentity": {
                "ownerPackage": "sherpa_onnx",
                "ownerPackageVersion": "1.13.4",
                "onnxRuntimeVersion": "1.27.0",
            },
        },
        CPU_PROVIDER,
    ),
    CompositionContract(
        "linux-x86_64-default-cpu-bundled",
        {
            "os": "linux",
            "architecture": "x86_64",
            "variant": "default",
            "flavor": "cpu",
            "minimumOs": "glibc-2.27",
        },
        {
            "kind": "locked-artifact",
            "artifactId": "onnxruntime-1.27.1-linux-x86_64-cpu",
            "runtimeOwner": "wrapper",
            "runtimeMode": "bundled",
        },
        CPU_PROVIDER,
    ),
)


def _record_contract(
    identifier: str,
    media_type: str = "application/json",
    *,
    semantic_contract: str | None = None,
) -> EvidenceRecordContract:
    return EvidenceRecordContract(identifier, media_type, semantic_contract)


COMPOSITION_EVIDENCE_CONTRACTS: dict[
    str, dict[str, tuple[EvidenceRecordContract, ...]]
] = {
    "ios-arm64-device-cpu-linked": {
        "targetExecutionRecords": (
            _record_contract("ios-device-target-execution"),
        ),
        "providerAssignmentRecords": (
            _record_contract("ios-device-cpu-full-assignment"),
        ),
        "finalPackageRecords": (
            _record_contract("ios-device-release-ipa", "application/zip"),
        ),
        "sbomRecords": (
            _record_contract("ios-device-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            _record_contract("ios-device-final-package-audit"),
        ),
        "reproducibilityRecords": (
            _record_contract("ios-device-reproducibility"),
        ),
        "noticesRecords": (
            _record_contract("ios-device-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            _record_contract("ios-device-distribution-signing"),
        ),
    },
    "macos-arm64-default-cpu-bundled": {
        "targetExecutionRecords": (
            _record_contract("macos-arm64-clean-machine-execution"),
        ),
        "providerAssignmentRecords": (
            _record_contract(
                "macos-arm64-cpu-full-assignment",
                semantic_contract=MACOS_CPU_ASSIGNMENT_SEMANTIC_CONTRACT,
            ),
        ),
        "finalPackageRecords": (
            _record_contract("macos-arm64-release-archive", "application/zip"),
        ),
        "sbomRecords": (
            _record_contract("macos-arm64-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            _record_contract("macos-arm64-final-package-audit"),
        ),
        "reproducibilityRecords": (
            _record_contract("macos-arm64-reproducibility"),
        ),
        "noticesRecords": (
            _record_contract("macos-arm64-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            _record_contract("macos-arm64-distribution-signing"),
        ),
    },
    "android-arm64-v8a-default-cpu-bundled": {
        "targetExecutionRecords": (
            _record_contract("android-bundled-api24-device-execution"),
            _record_contract("android-bundled-aab-split-install-execution"),
        ),
        "providerAssignmentRecords": (
            _record_contract("android-bundled-cpu-full-assignment"),
        ),
        "finalPackageRecords": (
            _record_contract(
                "android-bundled-release-apk",
                "application/vnd.android.package-archive",
            ),
            _record_contract("android-bundled-release-aab", "application/zip"),
        ),
        "sbomRecords": (
            _record_contract("android-bundled-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            _record_contract("android-bundled-apk-audit"),
            _record_contract("android-bundled-aab-audit"),
        ),
        "reproducibilityRecords": (
            _record_contract("android-bundled-reproducibility"),
        ),
        "noticesRecords": (
            _record_contract("android-bundled-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            _record_contract("android-bundled-distribution-signing"),
        ),
    },
    "android-arm64-v8a-default-cpu-sherpa-process": {
        "targetExecutionRecords": (
            _record_contract("android-sherpa-dart-first-4k-target"),
            _record_contract("android-sherpa-sherpa-first-4k-target"),
            _record_contract("android-sherpa-dart-first-16k-target"),
            _record_contract("android-sherpa-sherpa-first-16k-target"),
        ),
        "providerAssignmentRecords": (
            _record_contract("android-sherpa-cpu-full-assignment-aggregate"),
        ),
        "finalPackageRecords": (
            _record_contract(
                "android-sherpa-release-apk",
                "application/vnd.android.package-archive",
            ),
            _record_contract("android-sherpa-release-aab", "application/zip"),
        ),
        "sbomRecords": (
            _record_contract("android-sherpa-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            _record_contract("android-sherpa-apk-aab-static-audit"),
            _record_contract("android-sherpa-four-record-validation-aggregate"),
        ),
        "reproducibilityRecords": (
            _record_contract("android-sherpa-reproducibility"),
        ),
        "noticesRecords": (
            _record_contract("android-sherpa-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            _record_contract("android-sherpa-distribution-signing"),
        ),
    },
    "linux-x86_64-default-cpu-bundled": {
        "targetExecutionRecords": (
            _record_contract("linux-x86_64-clean-machine-execution"),
        ),
        "providerAssignmentRecords": (
            _record_contract("linux-x86_64-cpu-full-assignment"),
        ),
        "finalPackageRecords": (
            _record_contract("linux-x86_64-release-archive", "application/gzip"),
        ),
        "sbomRecords": (
            _record_contract("linux-x86_64-spdx-sbom", "application/spdx+json"),
        ),
        "auditRecords": (
            _record_contract("linux-x86_64-final-package-audit"),
        ),
        "reproducibilityRecords": (
            _record_contract("linux-x86_64-reproducibility"),
        ),
        "noticesRecords": (
            _record_contract("linux-x86_64-third-party-notices", "text/plain"),
        ),
        "signingRecords": (
            _record_contract("linux-x86_64-distribution-signing"),
        ),
    },
}


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScopedReleaseApprovalError(f"JSON input duplicates key {key!r}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise ScopedReleaseApprovalError(f"JSON input uses non-standard constant {value}")


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_duplicate_keys,
            parse_constant=_invalid_json_constant,
        )
    except ScopedReleaseApprovalError as error:
        raise ScopedReleaseApprovalError(
            f"{label} is not strict JSON: {error}"
        ) from error
    except (UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError, RecursionError) as error:
        raise ScopedReleaseApprovalError(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ScopedReleaseApprovalError(f"{label} root must be an object")

    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ScopedReleaseApprovalError(f"{label} structure exceeds its bound")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                encoded = item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ScopedReleaseApprovalError(
                    f"{label} contains an invalid Unicode string"
                ) from error
            if len(encoded) > MAX_JSON_STRING_BYTES or any(
                ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
                for character in item
            ):
                raise ScopedReleaseApprovalError(
                    f"{label} contains a string outside the accepted bound"
                )
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_pretty_json(value: Any) -> bytes:
    encoded = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ScopedReleaseApprovalError("validation record exceeds its size bound")
    return encoded


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScopedReleaseApprovalError(f"{label} must be an object")
    return value


def _array(value: Any, label: str, *, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ScopedReleaseApprovalError(
            f"{label} must contain between {minimum} and {maximum} items"
        )
    return value


def _exact_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise ScopedReleaseApprovalError(
            f"{label} has an invalid field set "
            f"(missing={sorted(expected - actual)}, unknown={sorted(actual - expected)})"
        )


def _string(value: Any, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value:
        raise ScopedReleaseApprovalError(f"{label} must be a bounded non-empty string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ScopedReleaseApprovalError(
            f"{label} must be a bounded non-empty string"
        ) from error
    if len(encoded) > maximum or any(
        ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
        for character in value
    ):
        raise ScopedReleaseApprovalError(f"{label} must be a bounded non-empty string")
    return value


def _digest(value: Any, label: str) -> str:
    result = _string(value, label, maximum=64)
    if _DIGEST.fullmatch(result) is None:
        raise ScopedReleaseApprovalError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _token(value: Any, label: str) -> str:
    result = _string(value, label, maximum=128)
    if _TOKEN.fullmatch(result) is None:
        raise ScopedReleaseApprovalError(f"{label} must be a closed token")
    return result


def _identity(value: Any, label: str) -> str:
    result = _string(value, label, maximum=128)
    if _IDENTITY.fullmatch(result) is None:
        raise ScopedReleaseApprovalError(f"{label} must be a bounded identity token")
    return result


def _positive_integer(value: Any, label: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ScopedReleaseApprovalError(f"{label} must be a bounded positive integer")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    text = _string(value, label, maximum=20)
    if _UTC_TIMESTAMP.fullmatch(text) is None:
        raise ScopedReleaseApprovalError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ScopedReleaseApprovalError(f"{label} is not a real UTC timestamp") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != text:
        raise ScopedReleaseApprovalError(f"{label} must be a canonical UTC timestamp")
    return parsed


def _safe_relative_path(value: Any, label: str) -> str:
    text = _string(value, label, maximum=1024)
    if "\\" in text or "\x00" in text:
        raise ScopedReleaseApprovalError(f"{label} must be a canonical relative path")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ScopedReleaseApprovalError(f"{label} must be a canonical relative path")
    if path.as_posix() != text:
        raise ScopedReleaseApprovalError(f"{label} must be a canonical relative path")
    return text


def _stat_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise ScopedReleaseApprovalError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ScopedReleaseApprovalError(f"{label} must be a regular file, not a link")
    if before.st_size <= 0 or before.st_size > maximum:
        raise ScopedReleaseApprovalError(f"{label} size is outside the accepted bound")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ScopedReleaseApprovalError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        identity = _stat_identity(before)
        if not stat.S_ISREG(opened.st_mode) or _stat_identity(opened) != identity:
            raise ScopedReleaseApprovalError(f"{label} changed while being opened")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum:
                raise ScopedReleaseApprovalError(f"{label} exceeds the accepted bound")
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
        try:
            after_path = path.lstat()
        except OSError as error:
            raise ScopedReleaseApprovalError(f"{label} changed while being read") from error
        if (
            consumed != before.st_size
            or _stat_identity(after_fd) != identity
            or _stat_identity(after_path) != identity
        ):
            raise ScopedReleaseApprovalError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _canonical_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ScopedReleaseApprovalError(f"{label} must be absolute")
    absolute = path.absolute()
    try:
        metadata = absolute.lstat()
    except OSError as error:
        raise ScopedReleaseApprovalError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ScopedReleaseApprovalError(f"{label} must be a directory, not a link")
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise ScopedReleaseApprovalError(f"{label} cannot be resolved") from error
    if resolved != absolute:
        raise ScopedReleaseApprovalError(f"{label} must be canonical and contain no link")
    return resolved


def _canonical_external_file(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ScopedReleaseApprovalError(f"{label} must be absolute")
    absolute = path.absolute()
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise ScopedReleaseApprovalError(f"{label} cannot be resolved") from error
    if resolved != absolute:
        raise ScopedReleaseApprovalError(f"{label} must be canonical and contain no link")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _repository_file(repository: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ScopedReleaseApprovalError("repository source path is not canonical")
    current = repository
    for index, part in enumerate(path.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ScopedReleaseApprovalError(
                f"repository source {relative} cannot be inspected"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ScopedReleaseApprovalError(
                f"repository source {relative} must not traverse a link"
            )
        if index < len(path.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise ScopedReleaseApprovalError(
                f"repository source {relative} has a non-directory parent"
            )
    return current


class EvidenceReader:
    """Read a bounded external tree through no-follow directory descriptors."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._root_descriptor: int | None = None
        self._root_identity: tuple[int, int, int, int, int, int, int] | None = None
        self._seen_ids: set[str] = set()
        self._seen_paths: set[str] = set()
        self._seen_files: set[tuple[int, int]] = set()
        self.reported_references: list[dict[str, Any]] = []
        self.reference_count = 0
        self.total_bytes = 0

    def __enter__(self) -> EvidenceReader:
        before = self.root.lstat()
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(self.root, flags)
        except OSError as error:
            raise ScopedReleaseApprovalError(
                "--evidence-root cannot be opened safely"
            ) from error
        opened = os.fstat(descriptor)
        identity = _stat_identity(before)
        if not stat.S_ISDIR(opened.st_mode) or _stat_identity(opened) != identity:
            os.close(descriptor)
            raise ScopedReleaseApprovalError("--evidence-root changed while being opened")
        self._root_descriptor = descriptor
        self._root_identity = identity
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._root_descriptor is not None:
            os.close(self._root_descriptor)
            self._root_descriptor = None

    def _check_root(self) -> None:
        if self._root_descriptor is None or self._root_identity is None:
            raise ScopedReleaseApprovalError("evidence reader is not open")
        try:
            path_metadata = self.root.lstat()
            descriptor_metadata = os.fstat(self._root_descriptor)
        except OSError as error:
            raise ScopedReleaseApprovalError(
                "--evidence-root changed while evidence was read"
            ) from error
        if (
            _stat_identity(path_metadata) != self._root_identity
            or _stat_identity(descriptor_metadata) != self._root_identity
        ):
            raise ScopedReleaseApprovalError(
                "--evidence-root changed while evidence was read"
            )

    def _open_relative(self, relative: str) -> int:
        if self._root_descriptor is None:
            raise ScopedReleaseApprovalError("evidence reader is not open")
        parts = PurePosixPath(relative).parts
        current = os.dup(self._root_descriptor)
        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(
                os, "O_CLOEXEC", 0
            )
            for part in parts[:-1]:
                try:
                    following = os.open(part, directory_flags, dir_fd=current)
                except OSError as error:
                    raise ScopedReleaseApprovalError(
                        f"evidence path {relative!r} cannot traverse safely"
                    ) from error
                os.close(current)
                current = following
            file_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            file_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(
                os, "O_CLOEXEC", 0
            )
            try:
                return os.open(parts[-1], file_flags, dir_fd=current)
            except OSError as error:
                raise ScopedReleaseApprovalError(
                    f"evidence path {relative!r} cannot be opened safely"
                ) from error
        finally:
            os.close(current)

    @contextmanager
    def retain_verified(
        self,
        reference: EvidenceReference,
        *,
        capture_raw: bool = False,
    ) -> Iterator[RetainedEvidence]:
        """Yield one same-inode, hash-verified descriptor without transferring it."""

        if reference.identifier in self._seen_ids:
            raise ScopedReleaseApprovalError(
                f"evidence duplicates ID {reference.identifier!r}"
            )
        if reference.path in self._seen_paths:
            raise ScopedReleaseApprovalError(
                f"evidence duplicates path {reference.path!r}"
            )
        next_reference_count = self.reference_count + 1
        if next_reference_count > MAX_EVIDENCE_REFERENCES:
            raise ScopedReleaseApprovalError("evidence reference count exceeds its bound")
        next_total_bytes = self.total_bytes + reference.size_bytes
        if next_total_bytes > MAX_EVIDENCE_TOTAL_BYTES:
            raise ScopedReleaseApprovalError("evidence bytes exceed the aggregate bound")

        descriptor = self._open_relative(reference.path)
        raw: bytes | None = None
        try:
            before = os.fstat(descriptor)
            identity = _stat_identity(before)
            if not stat.S_ISREG(before.st_mode):
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} must be a regular file"
                )
            if before.st_nlink != 1:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} must not have external hard links"
                )
            if before.st_size != reference.size_bytes:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} size does not match its reference"
                )
            file_identity = (before.st_dev, before.st_ino)
            if file_identity in self._seen_files:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} aliases another referenced file"
                )

            keep_raw = capture_raw or reference.media_type in JSON_MEDIA_TYPES
            if keep_raw and before.st_size > MAX_JSON_EVIDENCE_BYTES:
                raise ScopedReleaseApprovalError(
                    f"JSON evidence {reference.identifier!r} exceeds its size bound"
                )
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            consumed = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > MAX_EVIDENCE_FILE_BYTES:
                    raise ScopedReleaseApprovalError(
                        f"evidence {reference.identifier!r} exceeds its size bound"
                    )
                digest.update(chunk)
                if keep_raw:
                    chunks.append(chunk)
            after = os.fstat(descriptor)
            if consumed != reference.size_bytes or _stat_identity(after) != identity:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} changed while being read"
                )
            if digest.hexdigest() != reference.sha256:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} SHA-256 does not match its reference"
                )
            if keep_raw:
                raw = b"".join(chunks)
            parsed = None
            if raw is not None and reference.media_type in JSON_MEDIA_TYPES:
                parsed = _strict_json(
                    raw, label=f"evidence {reference.identifier!r}"
                )
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
            except OSError as error:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} cannot be retained for inspection"
                ) from error
            yield RetainedEvidence(
                descriptor=descriptor,
                contents=EvidenceContents(raw=raw, parsed_json=parsed),
            )
            try:
                offset = os.lseek(descriptor, 0, os.SEEK_CUR)
                after_inspection = os.fstat(descriptor)
            except OSError as error:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} was not retained safely"
                ) from error
            if offset != 0:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} inspector did not restore its offset"
                )
            if _stat_identity(after_inspection) != identity:
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} changed while being inspected"
                )
        finally:
            try:
                os.close(descriptor)
            except OSError:
                # A borrower that improperly closed the descriptor is rejected by
                # the post-yield lseek/fstat checks; never mask that primary error.
                pass

        reopened = self._open_relative(reference.path)
        try:
            reopened_metadata = os.fstat(reopened)
            if (
                reopened_metadata.st_nlink != 1
                or _stat_identity(reopened_metadata) != identity
            ):
                raise ScopedReleaseApprovalError(
                    f"evidence {reference.identifier!r} changed after being read"
                )
        finally:
            os.close(reopened)
        self._check_root()

        self.reference_count = next_reference_count
        self.total_bytes = next_total_bytes
        self._seen_ids.add(reference.identifier)
        self._seen_paths.add(reference.path)
        self._seen_files.add(file_identity)
        self.reported_references.append(
            {
                "id": reference.identifier,
                "sha256": reference.sha256,
                "sizeBytes": reference.size_bytes,
                "mediaType": reference.media_type,
            }
        )

    def read(
        self, reference: EvidenceReference, *, capture_raw: bool = False
    ) -> EvidenceContents:
        with self.retain_verified(
            reference, capture_raw=capture_raw
        ) as retained:
            return retained.contents


def _canonical_repository(path: Path) -> Path:
    return _canonical_directory(path.absolute(), label="--repository")


def _canonical_scope_path(repository: Path, scope: Path) -> Path:
    expected = repository / SCOPE_PATH
    candidate = scope if scope.is_absolute() else repository / scope
    if candidate.absolute() != expected:
        raise ScopedReleaseApprovalError(
            f"--scope must name the canonical repository source {SCOPE_PATH}"
        )
    return _repository_file(repository, SCOPE_PATH)


def _import_pinned_helper(
    repository: Path,
    *,
    relative_path: str,
    module_name: str,
    expected_sha256: str,
    label: str,
) -> Any:
    repository_path = _repository_file(repository, relative_path)
    repository_raw = _read_regular(
        repository_path, label=f"repository {label}", maximum=MAX_HELPER_BYTES
    )
    if hashlib.sha256(repository_raw).hexdigest() != expected_sha256:
        raise ScopedReleaseApprovalError(
            f"repository {label} bytes do not match the validator-pinned helper"
        )

    actual_path = Path(__file__).absolute().parent / PurePosixPath(relative_path).name
    actual_raw = _read_regular(
        actual_path, label=f"actual {label}", maximum=MAX_HELPER_BYTES
    )
    if hashlib.sha256(actual_raw).hexdigest() != expected_sha256:
        raise ScopedReleaseApprovalError(
            f"actual {label} bytes do not match the validator-pinned helper"
        )
    private_name = (
        f"_fonix_scoped_release_{module_name}_{secrets.token_hex(16)}"
    )
    module = ModuleType(private_name)
    module.__file__ = str(repository_path)
    module.__package__ = ""
    sys.modules[private_name] = module
    try:
        code = compile(
            repository_raw,
            str(repository_path),
            "exec",
            dont_inherit=True,
        )
        exec(code, module.__dict__)
    except Exception as error:
        sys.modules.pop(private_name, None)
        raise ScopedReleaseApprovalError(f"{label} could not be loaded") from error
    return module


def _reference(value: Any, label: str) -> EvidenceReference:
    item = _object(value, label)
    _exact_keys(item, _REFERENCE_KEYS, label)
    identifier = _token(item["id"], f"{label}.id")
    path = _safe_relative_path(item["path"], f"{label}.path")
    sha256 = _digest(item["sha256"], f"{label}.sha256")
    size_bytes = _positive_integer(
        item["sizeBytes"], f"{label}.sizeBytes", maximum=MAX_EVIDENCE_FILE_BYTES
    )
    media_type = _string(item["mediaType"], f"{label}.mediaType", maximum=64)
    if media_type not in MEDIA_TYPES:
        raise ScopedReleaseApprovalError(f"{label}.mediaType is outside the closed set")
    return EvidenceReference(identifier, path, sha256, size_bytes, media_type)


def _require_reference(
    reference: EvidenceReference,
    *,
    label: str,
    identifier: str | None = None,
    path: str | None = None,
    media_types: frozenset[str] | None = None,
) -> None:
    if identifier is not None and reference.identifier != identifier:
        raise ScopedReleaseApprovalError(f"{label}.id does not match the closed contract")
    if path is not None and reference.path != path:
        raise ScopedReleaseApprovalError(f"{label}.path does not match the repository source")
    if media_types is not None and reference.media_type not in media_types:
        raise ScopedReleaseApprovalError(f"{label}.mediaType does not match the closed contract")


def _validate_repository_baseline(
    repository: Path, scope_path: Path
) -> tuple[dict[str, Any], dict[str, Any], Any, Any, Any]:
    schema_path = _repository_file(repository, SCHEMA_PATH)
    schema_raw = _read_regular(
        schema_path, label="approval schema", maximum=MAX_SCHEMA_BYTES
    )
    _strict_json(schema_raw, label="approval schema")
    schema_sha256 = hashlib.sha256(schema_raw).hexdigest()
    if schema_sha256 != EXPECTED_SCHEMA_SHA256:
        raise ScopedReleaseApprovalError(
            "approval schema bytes do not match the validator-pinned schema"
        )
    semantic_schema_path = _repository_file(
        repository, MACOS_CPU_ASSIGNMENT_SCHEMA_PATH
    )
    semantic_schema_raw = _read_regular(
        semantic_schema_path,
        label="macOS CPU-assignment receipt schema",
        maximum=MAX_SCHEMA_BYTES,
    )
    _strict_json(
        semantic_schema_raw,
        label="macOS CPU-assignment receipt schema",
    )
    semantic_schema_sha256 = hashlib.sha256(semantic_schema_raw).hexdigest()
    if semantic_schema_sha256 != EXPECTED_MACOS_CPU_ASSIGNMENT_SCHEMA_SHA256:
        raise ScopedReleaseApprovalError(
            "macOS CPU-assignment receipt schema bytes do not match the "
            "validator-pinned schema"
        )

    # Load the source helper first so transitive imports by the scope helper
    # cannot select a same-named module from another directory.
    source_helper = _import_pinned_helper(
        repository,
        relative_path=SOURCE_HELPER_PATH,
        module_name="source_checksum_manifest",
        expected_sha256=EXPECTED_SOURCE_HELPER_SHA256,
        label="source-manifest validator",
    )
    source_archive_helper = _import_pinned_helper(
        repository,
        relative_path=SOURCE_ARCHIVE_HELPER_PATH,
        module_name="validate_source_release_archive",
        expected_sha256=EXPECTED_SOURCE_ARCHIVE_HELPER_SHA256,
        label="source-release archive validator",
    )
    if (
        source_archive_helper.SOURCE_HELPER_PATH != SOURCE_HELPER_PATH
        or source_archive_helper.EXPECTED_SOURCE_HELPER_SHA256
        != EXPECTED_SOURCE_HELPER_SHA256
    ):
        raise ScopedReleaseApprovalError(
            "source-release archive validator has a mismatched source-helper pin"
        )
    scope_helper = _import_pinned_helper(
        repository,
        relative_path=SCOPE_HELPER_PATH,
        module_name="validate_scoped_release_scope",
        expected_sha256=EXPECTED_SCOPE_HELPER_SHA256,
        label="scope validator",
    )
    try:
        scope_record = scope_helper.validate_scope(repository, scope_path)
    except scope_helper.ScopedReleaseScopeError as error:
        raise ScopedReleaseApprovalError(f"scope baseline is invalid: {error}") from error

    manifest_path = _repository_file(repository, SOURCE_MANIFEST_PATH)
    try:
        source_manifest_sha256 = source_helper.check_manifest(repository, manifest_path)
    except source_helper.SourceManifestError as error:
        raise ScopedReleaseApprovalError(
            f"repository source manifest is invalid: {error}"
        ) from error
    source_manifest_raw = _read_regular(
        manifest_path,
        label="repository source manifest",
        maximum=source_helper.MAX_MANIFEST_BYTES,
    )
    if hashlib.sha256(source_manifest_raw).hexdigest() != source_manifest_sha256:
        raise ScopedReleaseApprovalError(
            "repository source manifest digest changed during validation"
        )
    try:
        source_manifest_entry_count = len(
            source_helper.parse_manifest(source_manifest_raw)
        )
    except source_helper.SourceManifestError as error:
        raise ScopedReleaseApprovalError(
            f"repository source manifest is invalid: {error}"
        ) from error

    current_files: dict[str, bytes] = {}
    for relative in (PUBSPEC_LOCK_PATH, NATIVE_LOCK_PATH, SHERPA_LOCK_PATH):
        current_files[relative] = _read_regular(
            _repository_file(repository, relative),
            label=f"repository source {relative}",
            maximum=MAX_REPOSITORY_FILE_BYTES,
        )
    current_files[SOURCE_MANIFEST_PATH] = source_manifest_raw
    git_environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        revision_result = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(repository),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ],
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_environment,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ScopedReleaseApprovalError(
            "repository source revision cannot be resolved"
        ) from error
    if len(revision_result.stdout) > 128:
        raise ScopedReleaseApprovalError("repository source revision output is unbounded")
    try:
        source_revision = revision_result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ScopedReleaseApprovalError(
            "repository source revision output is not ASCII"
        ) from error
    if revision_result.returncode != 0 or _SOURCE_REVISION.fullmatch(source_revision) is None:
        raise ScopedReleaseApprovalError("repository HEAD is not one exact Git commit")
    baseline = {
        "schemaSha256": schema_sha256,
        "macosCpuAssignmentReceiptSchemaSha256": semantic_schema_sha256,
        "sourceManifestSha256": source_manifest_sha256,
        "sourceManifestSizeBytes": len(source_manifest_raw),
        "sourceManifestEntryCount": source_manifest_entry_count,
        "sourceRevision": source_revision,
        "files": current_files,
    }
    return (
        scope_record,
        baseline,
        scope_helper,
        source_helper,
        source_archive_helper,
    )


def _validate_scope_binding(
    value: Any,
    *,
    scope_record: dict[str, Any],
    reader: EvidenceReader,
) -> str:
    binding = _object(value, "candidateStatement.scope")
    _exact_keys(binding, _SCOPE_KEYS, "candidateStatement.scope")
    if binding["policyId"] != POLICY_ID:
        raise ScopedReleaseApprovalError("candidate scope policyId is invalid")
    if binding["scopePath"] != SCOPE_PATH:
        raise ScopedReleaseApprovalError("candidate scopePath is outside the frozen policy")
    if binding["scopeSha256"] != scope_record["scopeSha256"]:
        raise ScopedReleaseApprovalError("candidate scope digest does not match the frozen scope")

    reference = _reference(
        binding["validationRecord"], "candidateStatement.scope.validationRecord"
    )
    _require_reference(
        reference,
        label="candidateStatement.scope.validationRecord",
        identifier="scope-validation",
        media_types=frozenset({"application/json"}),
    )
    contents = reader.read(reference)
    if contents.parsed_json != scope_record:
        raise ScopedReleaseApprovalError(
            "scope validation record does not exactly match current validation"
        )
    return reference.sha256


def _validate_source_archive_record(
    value: Any,
    *,
    reference: EvidenceReference,
    source_revision: str,
    baseline: dict[str, Any],
    source_archive_helper: Any,
) -> bytes:
    record = _object(value, "derived source archive validation record")
    _exact_keys(
        record,
        _SOURCE_ARCHIVE_RECORD_KEYS,
        "derived source archive validation record",
    )
    expected_constants = {
        "schemaVersion": 1,
        "result": "validated",
        "claimStatus": "source-closure-only",
        "purpose": "source-release-archive-closure-validation",
        "validationScope": "offline-consistency-only",
        "claimBoundary": SOURCE_ARCHIVE_CLAIM_BOUNDARY,
    }
    for key, expected in expected_constants.items():
        if record[key] != expected or type(record[key]) is not type(expected):
            raise ScopedReleaseApprovalError(
                f"derived source archive validation record {key} is invalid"
            )

    archive = _object(record["archive"], "derived source archive archive")
    _exact_keys(
        archive,
        frozenset({"format", "mediaType", "sha256", "sizeBytes"}),
        "derived source archive archive",
    )
    expected_archive_format = {
        "application/zip": "git-archive-zip-v1",
        "application/gzip": "git-archive-tar-gzip-v1",
    }[reference.media_type]
    if type(archive["sizeBytes"]) is not int or archive != {
        "format": expected_archive_format,
        "mediaType": reference.media_type,
        "sha256": reference.sha256,
        "sizeBytes": reference.size_bytes,
    }:
        raise ScopedReleaseApprovalError(
            "derived source archive identity differs from its retained evidence"
        )

    source = _object(record["source"], "derived source archive source")
    _exact_keys(
        source,
        _SOURCE_ARCHIVE_SOURCE_KEYS,
        "derived source archive source",
    )
    expected_revision_binding = (
        "zip-comment"
        if reference.media_type == "application/zip"
        else "pax-global-comment"
    )
    expected_source = {
        "revision": source_revision,
        "revisionBinding": expected_revision_binding,
        "manifestPath": SOURCE_MANIFEST_PATH,
        "manifestSha256": baseline["sourceManifestSha256"],
        "manifestSizeBytes": baseline["sourceManifestSizeBytes"],
        "manifestEntryCount": baseline["sourceManifestEntryCount"],
    }
    for key, expected in expected_source.items():
        if source[key] != expected or type(source[key]) is not type(expected):
            raise ScopedReleaseApprovalError(
                f"derived source archive source {key} differs from the baseline"
            )
    inventory_sha256 = _digest(
        source["inventorySha256"],
        "derived source archive source.inventorySha256",
    )
    bounded_counts = {
        "memberCount": (2, source_archive_helper.MAXIMUM_MEMBER_COUNT),
        "regularFileCount": (
            2,
            source_archive_helper.MAXIMUM_REGULAR_FILE_COUNT,
        ),
        "directoryCount": (
            1,
            source_archive_helper.MAXIMUM_DIRECTORY_COUNT,
        ),
        "executableFileCount": (0, 16_384),
        "expandedBytes": (1, 516 * 1024 * 1024),
    }
    counts: dict[str, int] = {}
    for key, (minimum, maximum) in bounded_counts.items():
        item = source[key]
        if type(item) is not int or not minimum <= item <= maximum:
            raise ScopedReleaseApprovalError(
                f"derived source archive source.{key} is outside its bound"
            )
        counts[key] = item
    if (
        counts["memberCount"]
        != counts["regularFileCount"] + counts["directoryCount"]
        or counts["regularFileCount"]
        != baseline["sourceManifestEntryCount"] + 1
        or counts["executableFileCount"] > counts["regularFileCount"]
        or counts["expandedBytes"] < baseline["sourceManifestSizeBytes"]
    ):
        raise ScopedReleaseApprovalError(
            "derived source archive counts are internally inconsistent"
        )

    tools = _object(record["tools"], "derived source archive tools")
    _exact_keys(
        tools,
        frozenset({"validator", "sourceManifestValidator"}),
        "derived source archive tools",
    )
    schemas = _object(record["schemas"], "derived source archive schemas")
    _exact_keys(
        schemas,
        frozenset({"validation"}),
        "derived source archive schemas",
    )

    def validate_identity(value: Any, label: str, expected_sha256: str) -> None:
        identity = _object(value, label)
        _exact_keys(identity, _SOURCE_ARCHIVE_IDENTITY_KEYS, label)
        _positive_integer(identity["sizeBytes"], f"{label}.sizeBytes", maximum=4 * 1024 * 1024)
        if _digest(identity["sha256"], f"{label}.sha256") != expected_sha256:
            raise ScopedReleaseApprovalError(f"{label} digest is not pinned")

    validate_identity(
        tools["validator"],
        "derived source archive tools.validator",
        EXPECTED_SOURCE_ARCHIVE_HELPER_SHA256,
    )
    validate_identity(
        tools["sourceManifestValidator"],
        "derived source archive tools.sourceManifestValidator",
        EXPECTED_SOURCE_HELPER_SHA256,
    )
    validate_identity(
        schemas["validation"],
        "derived source archive schemas.validation",
        source_archive_helper.EXPECTED_VALIDATION_SCHEMA_SHA256,
    )
    if inventory_sha256 != source["inventorySha256"]:
        raise ScopedReleaseApprovalError(
            "derived source archive inventory digest is invalid"
        )
    return _canonical_pretty_json(record)


def _validate_source_binding(
    value: Any,
    *,
    scope_record: dict[str, Any],
    baseline: dict[str, Any],
    reader: EvidenceReader,
    repository: Path,
    source_helper: Any,
    source_archive_helper: Any,
) -> tuple[dict[str, Any], bytes]:
    source = _object(value, "candidateStatement.source")
    _exact_keys(source, _SOURCE_KEYS, "candidateStatement.source")
    source_revision = _string(
        source["sourceRevision"], "candidateStatement.source.sourceRevision", maximum=40
    )
    if _SOURCE_REVISION.fullmatch(source_revision) is None:
        raise ScopedReleaseApprovalError("candidate sourceRevision must be lowercase 40-hex")
    if source_revision != baseline["sourceRevision"]:
        raise ScopedReleaseApprovalError(
            "candidate sourceRevision differs from the repository baseline"
        )
    if source["packageName"] != scope_record["packageName"]:
        raise ScopedReleaseApprovalError("candidate package name differs from the scope baseline")
    if source["packageVersion"] != scope_record["packageVersion"]:
        raise ScopedReleaseApprovalError(
            "candidate package version differs from the scope baseline"
        )

    references = {
        name: _reference(source[name], f"candidateStatement.source.{name}")
        for name in (
            "sourceArchive",
            "sourceManifest",
            "dartPubspecLock",
            "nativeLock",
            "sherpaPubspecLock",
        )
    }
    _require_reference(
        references["sourceArchive"],
        label="candidateStatement.source.sourceArchive",
        identifier="source-archive",
        media_types=frozenset({"application/gzip", "application/zip"}),
    )
    _require_reference(
        references["sourceManifest"],
        label="candidateStatement.source.sourceManifest",
        identifier="source-manifest",
        path=SOURCE_MANIFEST_PATH,
        media_types=frozenset({"text/plain"}),
    )
    _require_reference(
        references["dartPubspecLock"],
        label="candidateStatement.source.dartPubspecLock",
        identifier="dart-pubspec-lock",
        path=PUBSPEC_LOCK_PATH,
        media_types=frozenset({"text/plain"}),
    )
    _require_reference(
        references["nativeLock"],
        label="candidateStatement.source.nativeLock",
        identifier="native-lock",
        path=NATIVE_LOCK_PATH,
        media_types=frozenset({"application/json"}),
    )
    _require_reference(
        references["sherpaPubspecLock"],
        label="candidateStatement.source.sherpaPubspecLock",
        identifier="sherpa-pubspec-lock",
        path=SHERPA_LOCK_PATH,
        media_types=frozenset({"text/plain"}),
    )

    manifest_contents = reader.read(references["sourceManifest"], capture_raw=True)
    dart_lock_contents = reader.read(references["dartPubspecLock"], capture_raw=True)
    native_lock_contents = reader.read(references["nativeLock"], capture_raw=True)
    sherpa_lock_contents = reader.read(
        references["sherpaPubspecLock"], capture_raw=True
    )
    external = {
        SOURCE_MANIFEST_PATH: manifest_contents.raw,
        PUBSPEC_LOCK_PATH: dart_lock_contents.raw,
        NATIVE_LOCK_PATH: native_lock_contents.raw,
        SHERPA_LOCK_PATH: sherpa_lock_contents.raw,
    }
    for relative, raw in external.items():
        if raw != baseline["files"][relative]:
            raise ScopedReleaseApprovalError(
                f"candidate source {relative} differs from the repository baseline"
            )
    if references["nativeLock"].sha256 != scope_record["nativeLockSha256"]:
        raise ScopedReleaseApprovalError("candidate native lock differs from the scope baseline")
    if references["sherpaPubspecLock"].sha256 != scope_record["sherpaPubspecLockSha256"]:
        raise ScopedReleaseApprovalError("candidate sherpa lock differs from the scope baseline")

    archive_reference = references["sourceArchive"]
    try:
        with reader.retain_verified(archive_reference) as retained:
            archive_record = source_archive_helper.validate_archive_descriptor(
                repository,
                retained.descriptor,
                media_type=archive_reference.media_type,
                expected_sha256=archive_reference.sha256,
                expected_size_bytes=archive_reference.size_bytes,
                expected_source_revision=source_revision,
                source_helper=source_helper,
            )
    except source_archive_helper.SourceReleaseArchiveError as error:
        raise ScopedReleaseApprovalError(
            f"candidate source archive is invalid: {error}"
        ) from error
    archive_record_bytes = _validate_source_archive_record(
        archive_record,
        reference=archive_reference,
        source_revision=source_revision,
        baseline=baseline,
        source_archive_helper=source_archive_helper,
    )

    return {
        "sourceRevision": source_revision,
        "sourceArchiveSha256": references["sourceArchive"].sha256,
        "sourceManifestSha256": references["sourceManifest"].sha256,
        "dartPubspecLockSha256": references["dartPubspecLock"].sha256,
        "nativeLockSha256": references["nativeLock"].sha256,
        "sherpaPubspecLockSha256": references["sherpaPubspecLock"].sha256,
        "packageName": source["packageName"],
        "packageVersion": source["packageVersion"],
        "sourceArchiveValidation": {
            "recordSha256": hashlib.sha256(archive_record_bytes).hexdigest(),
            "inventorySha256": archive_record["source"]["inventorySha256"],
            "validatorSha256": archive_record["tools"]["validator"]["sha256"],
            "schemaSha256": archive_record["schemas"]["validation"]["sha256"],
        },
    }, archive_record_bytes


def _strict_json_value_matches(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _strict_json_value_matches(actual[key], expected_value)
            for key, expected_value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_json_value_matches(actual_value, expected_value)
            for actual_value, expected_value in zip(actual, expected, strict=True)
        )
    return actual == expected


def _validate_semantic_evidence(
    semantic_contract: str,
    contents: EvidenceContents,
    *,
    label: str,
) -> None:
    if semantic_contract != MACOS_CPU_ASSIGNMENT_SEMANTIC_CONTRACT:
        raise ScopedReleaseApprovalError(
            f"{label} selects an unknown internal semantic contract"
        )
    record = _object(contents.parsed_json, f"{label} document")
    _exact_keys(
        record,
        frozenset(MACOS_CPU_ASSIGNMENT_RECEIPT),
        f"{label} document",
    )
    for field, expected in MACOS_CPU_ASSIGNMENT_RECEIPT.items():
        if not _strict_json_value_matches(record[field], expected):
            raise ScopedReleaseApprovalError(
                f"{label} document field {field!r} differs from its closed contract"
            )


def _validate_record_subset(
    value: Any,
    *,
    contracts: tuple[EvidenceRecordContract, ...],
    label: str,
    slot_prefix: str,
    reader: EvidenceReader,
    expected_raw_by_id: Mapping[str, bytes] | None = None,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    records = _array(value, label, minimum=0, maximum=8)
    contract_indexes = {
        contract.identifier: index for index, contract in enumerate(contracts)
    }
    present: set[str] = set()
    satisfied: set[str] = set()
    last_index = -1
    for record_index, raw_reference in enumerate(records):
        reference_label = f"{label}[{record_index}]"
        reference = _reference(raw_reference, reference_label)
        try:
            expected_index = contract_indexes[reference.identifier]
        except KeyError as error:
            raise ScopedReleaseApprovalError(
                f"{reference_label}.id is outside the exact evidence inventory"
            ) from error
        if expected_index <= last_index:
            raise ScopedReleaseApprovalError(
                f"{label} must be a unique canonical ordered subset"
            )
        last_index = expected_index
        contract = contracts[expected_index]
        if reference.media_type != contract.media_type:
            raise ScopedReleaseApprovalError(
                f"{reference_label}.mediaType does not match the exact evidence record"
            )
        present.add(reference.identifier)
        expected_raw = (
            None
            if expected_raw_by_id is None
            else expected_raw_by_id.get(reference.identifier)
        )
        contents = reader.read(reference, capture_raw=expected_raw is not None)
        if expected_raw is not None and contents.raw != expected_raw:
            raise ScopedReleaseApprovalError(
                f"{reference_label} does not match its exact derived record"
            )
        if expected_raw is not None:
            satisfied.add(reference.identifier)
        elif contract.semantic_contract is not None:
            _validate_semantic_evidence(
                contract.semantic_contract,
                contents,
                label=f"{reference_label} evidence {reference.identifier!r}",
            )
            satisfied.add(reference.identifier)

    required_slots = [
        f"{slot_prefix}:{contract.identifier}" for contract in contracts
    ]
    present_slots = [
        f"{slot_prefix}:{contract.identifier}"
        for contract in contracts
        if contract.identifier in present
    ]
    satisfied_slots = [
        f"{slot_prefix}:{contract.identifier}"
        for contract in contracts
        if contract.identifier in satisfied
    ]
    unvalidated_slots = [
        f"{slot_prefix}:{contract.identifier}"
        for contract in contracts
        if contract.identifier in present and contract.identifier not in satisfied
    ]
    missing_slots = [
        f"{slot_prefix}:{contract.identifier}"
        for contract in contracts
        if contract.identifier not in present
    ]
    return (
        required_slots,
        present_slots,
        satisfied_slots,
        unvalidated_slots,
        missing_slots,
    )


def _validate_compositions(
    value: Any, *, reader: EvidenceReader
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    compositions = _array(value, "candidateStatement.compositions", minimum=5, maximum=5)
    required_slots: list[str] = []
    present_slots: list[str] = []
    satisfied_slots: list[str] = []
    unvalidated_slots: list[str] = []
    missing_slots: list[str] = []
    for index, (raw_composition, contract) in enumerate(
        zip(compositions, COMPOSITION_CONTRACTS)
    ):
        label = f"candidateStatement.compositions[{index}]"
        composition = _object(raw_composition, label)
        _exact_keys(composition, _COMPOSITION_KEYS, label)
        if composition["id"] != contract.identifier:
            raise ScopedReleaseApprovalError(
                "candidate compositions do not match the frozen inventory or order"
            )
        target = _object(composition["target"], f"{label}.target")
        _exact_keys(target, _TARGET_KEYS, f"{label}.target")
        if target != contract.target:
            raise ScopedReleaseApprovalError(
                f"{label}.target does not match the exact selected target"
            )
        runtime = _object(composition["runtime"], f"{label}.runtime")
        expected_runtime_keys = _RUNTIME_KEYS
        if "runtimeIdentity" in contract.runtime:
            expected_runtime_keys |= {"runtimeIdentity"}
        _exact_keys(runtime, expected_runtime_keys, f"{label}.runtime")
        if "runtimeIdentity" in runtime:
            identity = _object(runtime["runtimeIdentity"], f"{label}.runtime.runtimeIdentity")
            _exact_keys(identity, _RUNTIME_IDENTITY_KEYS, f"{label}.runtime.runtimeIdentity")
        if runtime != contract.runtime:
            raise ScopedReleaseApprovalError(
                f"{label}.runtime does not match the exact selected composition"
            )
        provider = _object(composition["provider"], f"{label}.provider")
        _exact_keys(provider, _PROVIDER_KEYS, f"{label}.provider")
        if provider != contract.provider:
            raise ScopedReleaseApprovalError(
                f"{label}.provider may advertise only CPU full assignment"
            )
        evidence = _object(composition["evidence"], f"{label}.evidence")
        _exact_keys(evidence, _EVIDENCE_KEY_SET, f"{label}.evidence")
        evidence_contract = COMPOSITION_EVIDENCE_CONTRACTS[contract.identifier]
        for evidence_key in _EVIDENCE_KEYS:
            required, present, satisfied, unvalidated, missing = (
                _validate_record_subset(
                    evidence[evidence_key],
                    contracts=evidence_contract[evidence_key],
                    label=f"{label}.evidence.{evidence_key}",
                    slot_prefix=(
                        f"composition:{contract.identifier}:{evidence_key}"
                    ),
                    reader=reader,
                )
            )
            required_slots.extend(required)
            present_slots.extend(present)
            satisfied_slots.extend(satisfied)
            unvalidated_slots.extend(unvalidated)
            missing_slots.extend(missing)
    return (
        required_slots,
        present_slots,
        satisfied_slots,
        unvalidated_slots,
        missing_slots,
    )


def _validate_shared_regressions(
    value: Any, *, reader: EvidenceReader, source_closure_record: bytes
) -> tuple[
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
]:
    regressions = _array(
        value, "candidateStatement.sharedRegressionEvidence", minimum=0, maximum=7
    )
    present_categories: list[str] = []
    present_slots: list[str] = []
    satisfied_slots: list[str] = []
    unvalidated_slots: list[str] = []
    missing_slots: list[str] = []
    last_index = -1
    for index, raw_regression in enumerate(regressions):
        label = f"candidateStatement.sharedRegressionEvidence[{index}]"
        regression = _object(raw_regression, label)
        _exact_keys(regression, _SHARED_KEYS, label)
        category = _string(regression["category"], f"{label}.category", maximum=64)
        try:
            category_index = SHARED_CATEGORIES.index(category)
        except ValueError as error:
            raise ScopedReleaseApprovalError(
                f"{label}.category is outside the closed inventory"
            ) from error
        if category_index <= last_index:
            raise ScopedReleaseApprovalError(
                "shared regression categories must be a unique canonical ordered subset"
            )
        last_index = category_index
        present_categories.append(category)
        _, present, satisfied, unvalidated, missing = _validate_record_subset(
            regression["records"],
            contracts=(
                _record_contract(f"shared-{category}"),
            ),
            label=f"{label}.records",
            slot_prefix=f"shared:{category}",
            reader=reader,
            expected_raw_by_id=(
                {"shared-source-closure": source_closure_record}
                if category == "source-closure"
                else None
            ),
        )
        present_slots.extend(present)
        satisfied_slots.extend(satisfied)
        unvalidated_slots.extend(unvalidated)
        missing_slots.extend(missing)

    present = set(present_categories)
    for category in SHARED_CATEGORIES:
        if category not in present:
            missing_slots.append(f"shared:{category}:shared-{category}")
    required_slots = [
        f"shared:{category}:shared-{category}" for category in SHARED_CATEGORIES
    ]
    # Restore canonical order when an explicitly empty present category and an
    # absent category were encountered at different points in the subset.
    missing_set = set(missing_slots)
    missing_slots = [slot for slot in required_slots if slot in missing_set]
    present_set = set(present_slots)
    present_slots = [slot for slot in required_slots if slot in present_set]
    satisfied_set = set(satisfied_slots)
    satisfied_slots = [slot for slot in required_slots if slot in satisfied_set]
    unvalidated_set = set(unvalidated_slots)
    unvalidated_slots = [
        slot for slot in required_slots if slot in unvalidated_set
    ]
    absent_categories = [
        category for category in SHARED_CATEGORIES if category not in present
    ]
    return (
        present_categories,
        absent_categories,
        required_slots,
        present_slots,
        satisfied_slots,
        unvalidated_slots,
        missing_slots,
    )


def _validate_detached_approvals(
    value: Any,
    *,
    candidate_subject_sha256: str,
    reader: EvidenceReader,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    approvals = _array(value, "detachedApprovals", minimum=0, maximum=5)
    results: list[dict[str, Any]] = []
    approved: list[str] = []
    rejected: list[str] = []
    present: list[str] = []
    last_index = -1
    for index, raw_approval in enumerate(approvals):
        label = f"detachedApprovals[{index}]"
        approval = _object(raw_approval, label)
        _exact_keys(approval, _APPROVAL_ENTRY_KEYS, label)
        category = _string(approval["category"], f"{label}.category", maximum=32)
        try:
            category_index = APPROVAL_CATEGORIES.index(category)
        except ValueError as error:
            raise ScopedReleaseApprovalError(
                f"{label}.category is outside the closed approval inventory"
            ) from error
        if category_index <= last_index:
            raise ScopedReleaseApprovalError(
                "detached approvals must be a unique canonical ordered subset"
            )
        last_index = category_index
        present.append(category)

        approver_id = _identity(approval["approverId"], f"{label}.approverId")
        key_id = _identity(approval["keyId"], f"{label}.keyId")
        algorithm = _string(
            approval["signatureAlgorithm"], f"{label}.signatureAlgorithm", maximum=32
        )
        if algorithm not in SIGNATURE_ALGORITHMS:
            raise ScopedReleaseApprovalError(
                f"{label}.signatureAlgorithm is outside the closed set"
            )

        statement_ref = _reference(approval["statement"], f"{label}.statement")
        signature_ref = _reference(approval["signature"], f"{label}.signature")
        receipt_ref = _reference(
            approval["verificationReceipt"], f"{label}.verificationReceipt"
        )
        _require_reference(
            statement_ref,
            label=f"{label}.statement",
            identifier=f"approval-{category}-statement",
            media_types=frozenset({"application/json"}),
        )
        _require_reference(
            signature_ref,
            label=f"{label}.signature",
            identifier=f"approval-{category}-signature",
            media_types=frozenset({"application/octet-stream"}),
        )
        _require_reference(
            receipt_ref,
            label=f"{label}.verificationReceipt",
            identifier=f"approval-{category}-verification",
            media_types=frozenset({"application/json"}),
        )

        statement_contents = reader.read(statement_ref)
        reader.read(signature_ref)
        receipt_contents = reader.read(receipt_ref)
        statement = _object(statement_contents.parsed_json, f"{label}.statement document")
        _exact_keys(statement, _APPROVAL_STATEMENT_KEYS, f"{label}.statement document")
        if type(statement["schemaVersion"]) is not int or statement["schemaVersion"] != 1:
            raise ScopedReleaseApprovalError(
                f"{label}.statement document schemaVersion must be 1"
            )
        if statement["category"] != category:
            raise ScopedReleaseApprovalError(
                f"{label}.statement document category differs from its bundle entry"
            )
        if statement["candidateSubjectSha256"] != candidate_subject_sha256:
            raise ScopedReleaseApprovalError(
                f"{label}.statement document binds a different candidate subject"
            )
        decision = _string(statement["decision"], f"{label}.statement decision", maximum=16)
        if decision not in {"approved", "rejected"}:
            raise ScopedReleaseApprovalError(
                f"{label}.statement decision must be approved or rejected"
            )
        decided_at = _timestamp(statement["decidedAt"], f"{label}.statement decidedAt")
        if statement["approverId"] != approver_id:
            raise ScopedReleaseApprovalError(
                f"{label}.statement approver identity differs from its bundle entry"
            )
        if statement["keyId"] != key_id:
            raise ScopedReleaseApprovalError(
                f"{label}.statement key identity differs from its bundle entry"
            )
        if statement["signatureAlgorithm"] != algorithm:
            raise ScopedReleaseApprovalError(
                f"{label}.statement algorithm differs from its bundle entry"
            )

        receipt = _object(
            receipt_contents.parsed_json, f"{label}.verification receipt document"
        )
        _exact_keys(
            receipt,
            _VERIFICATION_RECEIPT_KEYS,
            f"{label}.verification receipt document",
        )
        if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1:
            raise ScopedReleaseApprovalError(
                f"{label}.verification receipt schemaVersion must be 1"
            )
        expected_receipt = {
            "category": category,
            "candidateSubjectSha256": candidate_subject_sha256,
            "approvalStatementSha256": statement_ref.sha256,
            "signatureSha256": signature_ref.sha256,
            "approverId": approver_id,
            "keyId": key_id,
            "signatureAlgorithm": algorithm,
        }
        for key, expected in expected_receipt.items():
            if receipt[key] != expected:
                raise ScopedReleaseApprovalError(
                    f"{label}.verification receipt {key} differs from the detached artifacts"
                )
        if type(receipt["verified"]) is not bool or receipt["verified"] is not True:
            raise ScopedReleaseApprovalError(
                f"{label}.verification receipt must record verified=true"
            )
        verified_at = _timestamp(
            receipt["verifiedAt"], f"{label}.verification receipt verifiedAt"
        )
        if verified_at < decided_at:
            raise ScopedReleaseApprovalError(
                f"{label}.verification receipt predates the approval decision"
            )
        verifier_id = _identity(
            receipt["verifierId"], f"{label}.verification receipt verifierId"
        )

        if decision == "approved":
            approved.append(category)
        else:
            rejected.append(category)
        results.append(
            {
                "category": category,
                "decision": decision,
                "decidedAt": statement["decidedAt"],
                "approverId": approver_id,
                "keyId": key_id,
                "signatureAlgorithm": algorithm,
                "approvalStatementSha256": statement_ref.sha256,
                "signatureSha256": signature_ref.sha256,
                "verificationReceiptSha256": receipt_ref.sha256,
                "verifiedAt": receipt["verifiedAt"],
                "verifierId": verifier_id,
            }
        )
    present_set = set(present)
    missing = [category for category in APPROVAL_CATEGORIES if category not in present_set]
    return results, approved, rejected, missing


def _validate_external_locations(
    repository: Path,
    bundle: Path,
    evidence_root: Path,
) -> tuple[Path, Path]:
    root = _canonical_directory(evidence_root, label="--evidence-root")
    bundle_path = _canonical_external_file(bundle, label="--bundle")
    if _is_within(root, repository) or _is_within(repository, root):
        raise ScopedReleaseApprovalError(
            "--evidence-root must be external and disjoint from the repository"
        )
    if _is_within(bundle_path, repository):
        raise ScopedReleaseApprovalError("--bundle must be external to the repository")
    return bundle_path, root


def validate_approval(
    repository: Path,
    scope_path: Path,
    bundle_path: Path,
    bundle_sha256: str,
    evidence_root: Path,
) -> dict[str, Any]:
    """Validate one externally pinned candidate bundle and derive readiness."""

    expected_bundle_sha256 = _digest(bundle_sha256, "--bundle-sha256")
    repository = _canonical_repository(repository)
    canonical_scope = _canonical_scope_path(repository, scope_path)
    bundle_path, evidence_root = _validate_external_locations(
        repository, bundle_path, evidence_root
    )
    bundle_raw = _read_regular(
        bundle_path, label="external approval bundle", maximum=MAX_BUNDLE_BYTES
    )
    actual_bundle_sha256 = hashlib.sha256(bundle_raw).hexdigest()
    if actual_bundle_sha256 != expected_bundle_sha256:
        raise ScopedReleaseApprovalError(
            "external approval bundle does not match --bundle-sha256"
        )
    bundle = _strict_json(bundle_raw, label="external approval bundle")
    _exact_keys(bundle, _ROOT_KEYS, "external approval bundle")
    if type(bundle["schemaVersion"]) is not int or bundle["schemaVersion"] != 1:
        raise ScopedReleaseApprovalError("external approval bundle schemaVersion must be 1")
    if bundle["claimStatus"] != BUNDLE_CLAIM_STATUS:
        raise ScopedReleaseApprovalError("external approval bundle claimStatus is invalid")
    if bundle["claimBoundary"] != BUNDLE_CLAIM_BOUNDARY:
        raise ScopedReleaseApprovalError("external approval bundle claimBoundary is invalid")

    candidate = _object(bundle["candidateStatement"], "candidateStatement")
    _exact_keys(candidate, _CANDIDATE_KEYS, "candidateStatement")
    bundle_id = _token(candidate["bundleId"], "candidateStatement.bundleId")
    candidate_subject_sha256 = hashlib.sha256(_canonical_json(candidate)).hexdigest()
    if bundle["candidateSubjectSha256"] != candidate_subject_sha256:
        raise ScopedReleaseApprovalError(
            "candidateSubjectSha256 does not match canonical candidateStatement bytes"
        )

    (
        scope_record,
        baseline,
        scope_helper,
        source_helper,
        source_archive_helper,
    ) = _validate_repository_baseline(repository, canonical_scope)
    with EvidenceReader(evidence_root) as reader:
        scope_validation_record_sha256 = _validate_scope_binding(
            candidate["scope"], scope_record=scope_record, reader=reader
        )
        source_baseline, source_closure_record = _validate_source_binding(
            candidate["source"],
            scope_record=scope_record,
            baseline=baseline,
            reader=reader,
            repository=repository,
            source_helper=source_helper,
            source_archive_helper=source_archive_helper,
        )
        (
            composition_required_slots,
            composition_present_slots,
            composition_satisfied_slots,
            composition_unvalidated_slots,
            composition_missing_slots,
        ) = _validate_compositions(candidate["compositions"], reader=reader)
        (
            present_shared_categories,
            absent_shared_categories,
            shared_required_slots,
            shared_present_slots,
            shared_satisfied_slots,
            shared_unvalidated_slots,
            shared_missing_slots,
        ) = _validate_shared_regressions(
            candidate["sharedRegressionEvidence"],
            reader=reader,
            source_closure_record=source_closure_record,
        )
        approval_results, approved_categories, rejected_categories, missing_categories = (
            _validate_detached_approvals(
                bundle["detachedApprovals"],
                candidate_subject_sha256=candidate_subject_sha256,
                reader=reader,
            )
        )
        reader._check_root()
        reported_references = list(reader.reported_references)
        evidence_reference_count = reader.reference_count
        evidence_bytes = reader.total_bytes

    required_slots = composition_required_slots + shared_required_slots
    present_set = set(composition_present_slots + shared_present_slots)
    satisfied_set = set(composition_satisfied_slots + shared_satisfied_slots)
    unvalidated_set = set(
        composition_unvalidated_slots + shared_unvalidated_slots
    )
    missing_set = set(composition_missing_slots + shared_missing_slots)
    present_slots = [slot for slot in required_slots if slot in present_set]
    satisfied_slots = [slot for slot in required_slots if slot in satisfied_set]
    unvalidated_slots = [
        slot for slot in required_slots if slot in unvalidated_set
    ]
    missing_slots = [slot for slot in required_slots if slot in missing_set]
    if (
        present_set | missing_set != set(required_slots)
        or present_set & missing_set
        or satisfied_set | unvalidated_set != present_set
        or satisfied_set & unvalidated_set
    ):
        raise ScopedReleaseApprovalError(
            "internal semantic evidence partition is inconsistent"
        )
    blocker_ids: list[str] = []
    for slot in required_slots:
        if slot in missing_set:
            blocker_ids.append(f"missing-evidence:{slot}")
        elif slot in unvalidated_set:
            blocker_ids.append(f"unvalidated-evidence:{slot}")
    for category in APPROVAL_CATEGORIES:
        if category in missing_categories:
            blocker_ids.append(f"missing-approval:{category}")
        elif category in rejected_categories:
            blocker_ids.append(f"rejected-approval:{category}")
    ready = not blocker_ids

    validator_raw = _read_regular(
        Path(__file__).absolute(),
        label="approval validator",
        maximum=MAX_HELPER_BYTES,
    )
    referenced_evidence_sha256 = hashlib.sha256(
        _canonical_json(reported_references)
    ).hexdigest()
    return {
        "schemaVersion": 1,
        "result": "ready" if ready else "blocked",
        "claimStatus": "scoped-release-readiness",
        "ready": ready,
        "policyId": POLICY_ID,
        "bundleId": bundle_id,
        "bundleSha256": actual_bundle_sha256,
        "candidateSubjectSha256": candidate_subject_sha256,
        "referencedEvidenceSha256": referenced_evidence_sha256,
        "validatorSha256": hashlib.sha256(validator_raw).hexdigest(),
        "schemaSha256": baseline["schemaSha256"],
        "macosCpuAssignmentReceiptSchemaSha256": baseline[
            "macosCpuAssignmentReceiptSchemaSha256"
        ],
        "scopeValidatorSha256": EXPECTED_SCOPE_HELPER_SHA256,
        "sourceManifestValidatorSha256": EXPECTED_SOURCE_HELPER_SHA256,
        "scopeValidationRecordSha256": scope_validation_record_sha256,
        "sourceBaseline": source_baseline,
        "compositionIds": [contract.identifier for contract in COMPOSITION_CONTRACTS],
        "semanticValidationMode": SEMANTIC_VALIDATION_MODE,
        "requiredEvidenceSlotIds": required_slots,
        "presentEvidenceSlotIds": present_slots,
        "satisfiedEvidenceSlotIds": satisfied_slots,
        "unvalidatedEvidenceSlotIds": unvalidated_slots,
        "missingEvidenceSlotIds": missing_slots,
        "requiredSharedRegressionCategoryIds": list(SHARED_CATEGORIES),
        "presentSharedRegressionCategoryIds": present_shared_categories,
        "missingSharedRegressionCategoryIds": absent_shared_categories,
        "requiredApprovalCategoryIds": list(APPROVAL_CATEGORIES),
        "approvalResults": approval_results,
        "approvedApprovalCategoryIds": approved_categories,
        "rejectedApprovalCategoryIds": rejected_categories,
        "missingApprovalCategoryIds": missing_categories,
        "evidenceReferenceCount": evidence_reference_count,
        "evidenceBytes": evidence_bytes,
        "blockerIds": blocker_ids,
        "signatureVerificationMode": "external-verification-receipts-only",
        "claimBoundary": REPORT_CLAIM_BOUNDARY,
    }


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode)


def _open_validated_output(path: Path) -> ValidatedOutput:
    if not path.is_absolute():
        raise ScopedReleaseApprovalError("--output must be absolute")
    if path != Path(os.path.normpath(str(path))):
        raise ScopedReleaseApprovalError("--output must be canonical")
    if not path.name or path.name in {".", ".."}:
        raise ScopedReleaseApprovalError("--output must name one file")
    parent = _canonical_directory(path.parent, label="--output parent")
    candidate = parent / path.name
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        parent_descriptor = os.open(parent, flags)
    except OSError as error:
        raise ScopedReleaseApprovalError(
            "--output parent cannot be opened safely"
        ) from error
    try:
        path_metadata = parent.lstat()
        descriptor_metadata = os.fstat(parent_descriptor)
        parent_identity = _directory_identity(path_metadata)
        if (
            not stat.S_ISDIR(descriptor_metadata.st_mode)
            or _directory_identity(descriptor_metadata) != parent_identity
        ):
            raise ScopedReleaseApprovalError(
                "--output parent changed while being opened"
            )
        try:
            os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ScopedReleaseApprovalError("--output cannot be inspected") from error
        else:
            raise ScopedReleaseApprovalError("--output must not already exist")
        return ValidatedOutput(
            path=candidate,
            parent_path=parent,
            name=path.name,
            parent_descriptor=parent_descriptor,
            parent_identity=parent_identity,
        )
    except BaseException:
        os.close(parent_descriptor)
        raise


def _validate_output_location(
    path: Path, repository: Path, evidence_root: Path
) -> ValidatedOutput:
    output = _open_validated_output(path)
    candidate = output.path
    if _is_within(candidate, repository):
        output.close()
        raise ScopedReleaseApprovalError("--output must be external to the repository")
    if _is_within(candidate, evidence_root):
        output.close()
        raise ScopedReleaseApprovalError("--output must be disjoint from --evidence-root")
    return output


def _check_output_parent(output: ValidatedOutput) -> None:
    if output.closed:
        raise ScopedReleaseApprovalError("--output parent descriptor is closed")
    try:
        path_metadata = output.parent_path.lstat()
        descriptor_metadata = os.fstat(output.parent_descriptor)
    except OSError as error:
        raise ScopedReleaseApprovalError(
            "--output parent changed during publication"
        ) from error
    if (
        not stat.S_ISDIR(path_metadata.st_mode)
        or not stat.S_ISDIR(descriptor_metadata.st_mode)
        or _directory_identity(path_metadata) != output.parent_identity
        or _directory_identity(descriptor_metadata) != output.parent_identity
    ):
        raise ScopedReleaseApprovalError(
            "--output parent changed during publication"
        )


def _new_temporary_output(output: ValidatedOutput) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    for _ in range(128):
        name = f".{output.name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=output.parent_descriptor,
            )
        except FileExistsError:
            continue
        except OSError as error:
            raise ScopedReleaseApprovalError(
                "temporary validation output cannot be created safely"
            ) from error
        return descriptor, name
    raise ScopedReleaseApprovalError("temporary validation output name space is exhausted")


def _unlink_owned_output(
    output: ValidatedOutput, name: str, owned_identity: tuple[int, int]
) -> None:
    try:
        metadata = os.stat(
            name,
            dir_fd=output.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        return
    if (metadata.st_dev, metadata.st_ino) != owned_identity:
        return
    try:
        os.unlink(name, dir_fd=output.parent_descriptor)
    except OSError:
        pass


def write_validation_record(
    path: Path | ValidatedOutput, record: dict[str, Any]
) -> None:
    """Publish a completed path-free record atomically without replacement."""

    output = path if isinstance(path, ValidatedOutput) else _open_validated_output(path)
    temporary_name: str | None = None
    published = False
    succeeded = False
    owned_identity: tuple[int, int] | None = None
    try:
        encoded = _canonical_pretty_json(record)
        _check_output_parent(output)
        descriptor, temporary_name = _new_temporary_output(output)
        created_metadata = os.fstat(descriptor)
        owned_identity = (created_metadata.st_dev, created_metadata.st_ino)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        metadata = os.stat(
            temporary_name,
            dir_fd=output.parent_descriptor,
            follow_symlinks=False,
        )
        if (metadata.st_dev, metadata.st_ino) != owned_identity:
            raise ScopedReleaseApprovalError(
                "temporary validation output changed while being written"
            )
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(encoded):
            raise ScopedReleaseApprovalError(
                "temporary validation output changed before publication"
            )
        _check_output_parent(output)
        # Treat the destination as potentially published before entering the
        # link call: a fault-injecting wrapper can link successfully and then
        # raise. Cleanup still removes only our verified inode.
        published = True
        try:
            os.link(
                temporary_name,
                output.name,
                src_dir_fd=output.parent_descriptor,
                dst_dir_fd=output.parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ScopedReleaseApprovalError("--output must not already exist") from error
        except OSError as error:
            raise ScopedReleaseApprovalError(
                "validation output could not be published atomically"
            ) from error
        read_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        read_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            final_descriptor = os.open(
                output.name,
                read_flags,
                dir_fd=output.parent_descriptor,
            )
        except OSError as error:
            raise ScopedReleaseApprovalError(
                "published validation output cannot be opened safely"
            ) from error
        try:
            final = os.fstat(final_descriptor)
            if (
                not stat.S_ISREG(final.st_mode)
                or final.st_dev != metadata.st_dev
                or final.st_ino != metadata.st_ino
                or final.st_mode != metadata.st_mode
                or final.st_size != len(encoded)
            ):
                raise ScopedReleaseApprovalError(
                    "published validation output is not the completed record"
                )
            opened_identity = _stat_identity(final)
            chunks: list[bytes] = []
            consumed = 0
            while consumed <= len(encoded):
                chunk = os.read(
                    final_descriptor,
                    min(1024 * 1024, len(encoded) + 1 - consumed),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                consumed += len(chunk)
            after_read = os.fstat(final_descriptor)
            if (
                consumed != len(encoded)
                or b"".join(chunks) != encoded
                or _stat_identity(after_read) != opened_identity
            ):
                raise ScopedReleaseApprovalError(
                    "published validation output bytes changed during publication"
                )
        finally:
            os.close(final_descriptor)
        _check_output_parent(output)
        succeeded = True
    finally:
        if temporary_name is not None and owned_identity is not None:
            _unlink_owned_output(output, temporary_name, owned_identity)
        if published and not succeeded and owned_identity is not None:
            _unlink_owned_output(output, output.name, owned_identity)
        output.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-scoped-ready", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    output: ValidatedOutput | None = None
    try:
        repository = _canonical_repository(arguments.repository)
        evidence_root = _canonical_directory(
            arguments.evidence_root, label="--evidence-root"
        )
        output = _validate_output_location(
            arguments.output, repository, evidence_root
        )
        record = validate_approval(
            repository,
            arguments.scope,
            arguments.bundle,
            arguments.bundle_sha256,
            evidence_root,
        )
        write_validation_record(output, record)
        print(
            "validated scoped release approval bundle "
            f"sha256={record['bundleSha256']} result={record['result']} "
            f"blockers={len(record['blockerIds'])}"
        )
        if arguments.require_scoped_ready and not record["ready"]:
            return 1
        return 0
    except ScopedReleaseApprovalError as error:
        print(f"scoped release approval error: {error}", file=sys.stderr)
        return 1
    finally:
        if output is not None and not output.closed:
            output.close()


if __name__ == "__main__":
    raise SystemExit(main())
