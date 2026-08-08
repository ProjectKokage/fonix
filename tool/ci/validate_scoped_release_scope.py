#!/usr/bin/env python3
"""Validate Fonix's closed scoped pre-1.0 release policy.

This validator establishes scope structure and exact baseline binding only. It
does not consume target evidence, make a readiness decision, record an
approval, or authorize publication.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import tempfile
from types import ModuleType
from typing import Any, Iterable, Mapping

sys.dont_write_bytecode = True


MAX_SCOPE_BYTES = 256 * 1024
MAX_SCHEMA_BYTES = 256 * 1024
MAX_LOCK_BYTES = 4 * 1024 * 1024
MAX_PUBSPEC_BYTES = 1024 * 1024
MAX_VALIDATOR_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024

SCOPE_PATH = "release/scoped-pre-1.0-v1.json"
SCHEMA_PATH = "templates/ci/scoped_release_scope.schema.json"
NATIVE_LOCK_PATH = "native/versions.lock.yaml"
SHERPA_PUBSPEC_LOCK_PATH = "templates/android/sherpa_reference_app/pubspec.lock"
PUBSPEC_PATH = "pubspec.yaml"
RELEASE_EVIDENCE_HELPER_PATH = "tool/ci/generate_release_sbom.py"
SHERPA_LOCK_HELPER_PATH = "tool/ci/validate_android_load_order_receipt.py"
SOURCE_MANIFEST_HELPER_PATH = "tool/ci/source_checksum_manifest.py"

POLICY_ID = "scoped-pre-1.0-cpu-v1"
CLAIM_STATUS = "scope-only"
SHERPA_PACKAGE_VERSION = "1.13.4"
EXPECTED_SCHEMA_SHA256 = (
    "9d00f5688f17a9f97bed3b0c1748e0d55d3e206276d371727dabfc9426f0fc7e"
)
EXPECTED_RELEASE_EVIDENCE_HELPER_SHA256 = (
    "6add4550b18e9733f52e33bd47847c88c49c61b752c5e4720c54ec456710f130"
)
EXPECTED_SHERPA_LOCK_HELPER_SHA256 = (
    "4b1c2087ca7cf204487a591d512cd11268c0731c478bf4aaf13d9ac069425c79"
)
EXPECTED_SOURCE_MANIFEST_HELPER_SHA256 = (
    "9ef720e3bae376a01b4b61c2b2a4214c31760075c23bd64dca4fb887641dd5b6"
)
CLAIM_BOUNDARY = (
    "Scope validation proves only that the policy is closed and matches the "
    "current package/native-lock baseline. It is not release readiness, "
    "approval, signing evidence, or authorization to publish or distribute."
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,255}$")
_VERSION = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_MINIMUM_OS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

_ROOT_KEYS = frozenset(
    {"schemaVersion", "policyId", "claimStatus", "baseline", "targets", "deferredCapabilities"}
)
_BASELINE_KEYS = frozenset(
    {
        "nativeLockPath",
        "nativeLockSha256",
        "sherpaPubspecLockPath",
        "sherpaPubspecLockSha256",
        "packageName",
        "packageVersion",
        "shimAbi",
        "requiredOrtApi",
    }
)
_TARGET_COMMON_KEYS = frozenset(
    {"os", "architecture", "variant", "flavor", "minimumOs", "disposition"}
)
_COMPOSITION_KEYS = frozenset(
    {"id", "kind", "artifactId", "runtimeOwner", "runtimeMode", "advertisedProviders"}
)
_PROVIDER_KEYS = frozenset({"id", "requirement"})
_RUNTIME_IDENTITY_KEYS = frozenset(
    {"ownerPackage", "ownerPackageVersion", "onnxRuntimeVersion"}
)
_DEFERRED_KEYS = frozenset({"id", "disposition", "reason"})

MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 32_768
MAX_JSON_STRING_BYTES = 64 * 1024

_LOCK_KEYS = frozenset(
    {"schema", "snapshot_date", "release_state", "shim", "onnxruntime", "release_targets", "artifacts"}
)
_LOCK_SHIM_KEYS = frozenset({"abi", "required_ort_api", "source_revision"})
_LOCK_ONNX_KEYS = frozenset({"compatibility_floor"})
_LOCK_COMPATIBILITY_KEYS = frozenset({"c_api", "header", "ep_header", "license"})
_LOCK_RELEASE_TARGET_KEYS = frozenset({"os", "architecture", "variant", "flavor"})
_LOCK_ARTIFACT_KEYS = frozenset(
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
_LOCK_TARGET_KEYS = frozenset({"os", "architecture", "variant", "min_os"})
_LOCK_PROVIDER_KEYS = frozenset({"wrapper_id", "reported_name"})


class ScopedReleaseScopeError(RuntimeError):
    """A scoped-release input violates the closed scope-only contract."""


@dataclass(frozen=True)
class CompositionContract:
    id: str
    kind: str
    artifact_id: str | None
    runtime_owner: str
    runtime_mode: str
    runtime_identity: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class TargetContract:
    os: str
    architecture: str
    variant: str
    flavor: str
    minimum_os: str
    disposition: str
    reason: str | None
    compositions: tuple[CompositionContract, ...]

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.os, self.architecture, self.variant, self.flavor)

    @property
    def display_key(self) -> str:
        return "/".join(self.key)


_CPU_PROVIDER = ({"id": "cpu", "requirement": "full-assignment"},)

TARGET_CONTRACTS = (
    TargetContract(
        "ios",
        "arm64",
        "device",
        "cpu",
        "15.1",
        "selected",
        None,
        (
            CompositionContract(
                "ios-arm64-device-cpu-linked",
                "locked-artifact",
                "onnxruntime-1.27.1-ios-arm64-device-cpu",
                "wrapper",
                "linked",
            ),
        ),
    ),
    TargetContract(
        "ios",
        "arm64",
        "simulator",
        "cpu",
        "15.1",
        "unsupported",
        "Simulator execution is development-only and does not qualify an iOS distribution target.",
        (),
    ),
    TargetContract(
        "macos",
        "arm64",
        "default",
        "cpu",
        "14.0",
        "selected",
        None,
        (
            CompositionContract(
                "macos-arm64-default-cpu-bundled",
                "locked-artifact",
                "onnxruntime-1.27.1-macos-arm64-cpu",
                "wrapper",
                "bundled",
            ),
        ),
    ),
    TargetContract(
        "android",
        "arm64-v8a",
        "default",
        "cpu",
        "24",
        "selected",
        None,
        (
            CompositionContract(
                "android-arm64-v8a-default-cpu-bundled",
                "locked-artifact",
                "onnxruntime-1.27.1-android-arm64-v8a-cpu",
                "wrapper",
                "bundled",
            ),
            CompositionContract(
                "android-arm64-v8a-default-cpu-sherpa-process",
                "android-sherpa-process",
                None,
                "sherpa",
                "process",
                ("sherpa_onnx", "1.13.4", "1.27.0"),
            ),
        ),
    ),
    TargetContract(
        "android",
        "x86_64",
        "default",
        "cpu",
        "24",
        "unsupported",
        "Target execution and final-package evidence are unavailable for Android x86_64.",
        (),
    ),
    TargetContract(
        "linux",
        "x86_64",
        "default",
        "cpu",
        "glibc-2.27",
        "selected",
        None,
        (
            CompositionContract(
                "linux-x86_64-default-cpu-bundled",
                "locked-artifact",
                "onnxruntime-1.27.1-linux-x86_64-cpu",
                "wrapper",
                "bundled",
            ),
        ),
    ),
    TargetContract(
        "linux",
        "arm64",
        "default",
        "cpu",
        "glibc-2.27",
        "unsupported",
        "Target-host execution and final-package evidence are unavailable for Linux arm64.",
        (),
    ),
    TargetContract(
        "windows",
        "x64",
        "default",
        "cpu",
        "10.0",
        "unsupported",
        "Windows target-host, provider, final-package, installer, and clean-machine qualification is deferred.",
        (),
    ),
)

DEFERRED_CAPABILITIES = (
    (
        "android-qnn",
        "Android QNN qualification is deferred until the exact SDK, hardware, firmware, licensing, redistribution, and target-execution inputs exist.",
    ),
    (
        "windows-target-host-provider-final-package-installer-clean-machine",
        "Windows target-host, provider, final-package, installer, and clean-machine qualification is deferred.",
    ),
)


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScopedReleaseScopeError(f"JSON input duplicates key {key!r}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise ScopedReleaseScopeError(f"JSON input uses non-standard constant {value}")


def _read_regular(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise ScopedReleaseScopeError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ScopedReleaseScopeError(f"{label} must be a regular file, not a link")
    if before.st_size <= 0 or before.st_size > maximum:
        raise ScopedReleaseScopeError(f"{label} size is outside the accepted bound")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ScopedReleaseScopeError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if not stat.S_ISREG(opened.st_mode) or opened_identity != before_identity:
            raise ScopedReleaseScopeError(f"{label} changed while being opened")
        contents = bytearray()
        while len(contents) <= maximum:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, maximum + 1 - len(contents)),
            )
            if not chunk:
                break
            contents.extend(chunk)
        if len(contents) > maximum:
            raise ScopedReleaseScopeError(f"{label} exceeds the accepted size bound")
        after_descriptor = os.fstat(descriptor)
        try:
            after_path = path.lstat()
        except OSError as error:
            raise ScopedReleaseScopeError(f"{label} changed while being read") from error
        after_descriptor_identity = (
            after_descriptor.st_dev,
            after_descriptor.st_ino,
            after_descriptor.st_mode,
            after_descriptor.st_size,
            after_descriptor.st_mtime_ns,
            after_descriptor.st_ctime_ns,
        )
        after_path_identity = (
            after_path.st_dev,
            after_path.st_ino,
            after_path.st_mode,
            after_path.st_size,
            after_path.st_mtime_ns,
            after_path.st_ctime_ns,
        )
        if (
            len(contents) != before.st_size
            or after_descriptor_identity != before_identity
            or after_path_identity != before_identity
            or not stat.S_ISREG(after_path.st_mode)
        ):
            raise ScopedReleaseScopeError(f"{label} changed while being read")
        return bytes(contents)
    finally:
        os.close(descriptor)


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_duplicate_keys,
            parse_constant=_invalid_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ScopedReleaseScopeError(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ScopedReleaseScopeError(f"{label} root must be an object")
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ScopedReleaseScopeError(f"{label} structure exceeds its bound")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                encoded = item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ScopedReleaseScopeError(
                    f"{label} contains an invalid Unicode string"
                ) from error
            if (
                len(encoded) > MAX_JSON_STRING_BYTES
                or any(
                    ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
                    for character in item
                )
            ):
                raise ScopedReleaseScopeError(
                    f"{label} contains a string outside the accepted bound"
                )
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScopedReleaseScopeError(f"{label} must be an object")
    return value


def _array(value: Any, label: str, *, length: int) -> list[Any]:
    if not isinstance(value, list) or len(value) != length:
        raise ScopedReleaseScopeError(f"{label} must contain exactly {length} items")
    return value


def _exact_keys(value: dict[str, Any], keys: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != keys:
        unknown = sorted(actual - keys)
        missing = sorted(keys - actual)
        raise ScopedReleaseScopeError(
            f"{label} has an invalid field set (missing={missing}, unknown={unknown})"
        )


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 0xFFFFFFFF:
        raise ScopedReleaseScopeError(f"{label} must be an unsigned 32-bit positive integer")
    return value


def _string(value: Any, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value:
        raise ScopedReleaseScopeError(f"{label} must be a bounded non-empty string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ScopedReleaseScopeError(
            f"{label} must be a bounded non-empty string"
        ) from error
    if (
        len(encoded) > maximum
        or any(
            ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
            for character in value
        )
    ):
        raise ScopedReleaseScopeError(f"{label} must be a bounded non-empty string")
    return value


def _digest(value: Any, label: str) -> str:
    raw = _string(value, label, maximum=64)
    if _DIGEST.fullmatch(raw) is None:
        raise ScopedReleaseScopeError(f"{label} must be a lowercase SHA-256 digest")
    return raw


def _repository_file(repository: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ScopedReleaseScopeError("internal repository path is not canonical")
    current = repository
    for index, part in enumerate(pure.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ScopedReleaseScopeError(
                f"repository source {relative} cannot be inspected"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ScopedReleaseScopeError(
                f"repository source {relative} must not traverse a link"
            )
        if index < len(pure.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise ScopedReleaseScopeError(
                f"repository source {relative} has a non-directory parent"
            )
    return current


def _canonical_repository(repository: Path) -> Path:
    absolute = repository.absolute()
    try:
        metadata = absolute.lstat()
    except OSError as error:
        raise ScopedReleaseScopeError("repository cannot be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ScopedReleaseScopeError("repository must be a directory, not a link")
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise ScopedReleaseScopeError("repository cannot be resolved") from error
    if resolved != absolute:
        raise ScopedReleaseScopeError("repository path must be canonical and contain no link")
    return resolved


def _canonical_scope_path(repository: Path, scope: Path) -> Path:
    expected = repository / SCOPE_PATH
    candidate = scope if scope.is_absolute() else repository / scope
    candidate = candidate.absolute()
    if candidate != expected:
        raise ScopedReleaseScopeError(
            f"--scope must name the canonical repository source {SCOPE_PATH}"
        )
    return _repository_file(repository, SCOPE_PATH)


def _validate_pinned_helper(
    repository: Path,
    *,
    relative_path: str,
    expected_sha256: str,
    label: str,
) -> Path:
    repository_path = _repository_file(repository, relative_path)
    repository_raw = _read_regular(
        repository_path,
        label=f"repository {label}",
        maximum=MAX_VALIDATOR_BYTES,
    )
    if hashlib.sha256(repository_raw).hexdigest() != expected_sha256:
        raise ScopedReleaseScopeError(
            f"repository {label} bytes do not match the validator-pinned helper"
        )

    actual_path = Path(__file__).absolute().parent / PurePosixPath(relative_path).name
    actual_raw = _read_regular(
        actual_path,
        label=f"actual {label}",
        maximum=MAX_VALIDATOR_BYTES,
    )
    if hashlib.sha256(actual_raw).hexdigest() != expected_sha256:
        raise ScopedReleaseScopeError(
            f"actual {label} bytes do not match the validator-pinned helper"
        )
    return actual_path


def _import_pinned_helper(
    *,
    module_name: str,
    expected_path: Path,
    expected_sha256: str,
    label: str,
    pinned_modules: Mapping[str, Any] | None = None,
) -> Any:
    imported_raw = _read_regular(
        expected_path,
        label=f"imported {label}",
        maximum=MAX_VALIDATOR_BYTES,
    )
    if hashlib.sha256(imported_raw).hexdigest() != expected_sha256:
        raise ScopedReleaseScopeError(
            f"imported {label} bytes do not match the validator-pinned helper"
        )
    private_name = f"_fonix_scoped_scope_{module_name}_{secrets.token_hex(16)}"
    module = ModuleType(private_name)
    module.__file__ = str(expected_path)
    module.__package__ = ""
    missing = object()
    previous_modules: dict[str, Any] = {}
    for dependency_name, dependency in (pinned_modules or {}).items():
        previous_modules[dependency_name] = sys.modules.get(
            dependency_name, missing
        )
        sys.modules[dependency_name] = dependency
    sys.modules[private_name] = module
    try:
        code = compile(
            imported_raw,
            str(expected_path),
            "exec",
            dont_inherit=True,
        )
        exec(code, module.__dict__)
    except Exception as error:
        raise ScopedReleaseScopeError(f"{label} could not be loaded") from error
    finally:
        sys.modules.pop(private_name, None)
        for dependency_name, previous in previous_modules.items():
            if previous is missing:
                sys.modules.pop(dependency_name, None)
            else:
                sys.modules[dependency_name] = previous
    return module


def _parse_pubspec(raw: bytes) -> tuple[str, str]:
    if b"\r" in raw or b"\t" in raw or b"\x00" in raw:
        raise ScopedReleaseScopeError("pubspec.yaml must use canonical LF indentation")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ScopedReleaseScopeError("pubspec.yaml must be UTF-8") from error
    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line or line.startswith("#") or line.startswith(" "):
            continue
        match = re.fullmatch(r"([a-z_]+):\s*(\S.*)", line)
        if match is None or match.group(1) not in {"name", "version"}:
            continue
        key, value = match.groups()
        if key in values:
            raise ScopedReleaseScopeError(f"pubspec.yaml duplicates top-level {key}")
        if " #" in value or value.startswith(("'", '"')):
            raise ScopedReleaseScopeError(
                f"pubspec.yaml line {line_number} uses an unsupported scalar"
            )
        values[key] = value
    if frozenset(values) != {"name", "version"}:
        raise ScopedReleaseScopeError("pubspec.yaml must declare one name and version")
    if values["name"] != "fonix":
        raise ScopedReleaseScopeError("pubspec.yaml package name must be fonix")
    if _VERSION.fullmatch(values["version"]) is None:
        raise ScopedReleaseScopeError("pubspec.yaml package version is invalid")
    return values["name"], values["version"]


def _validate_lock(
    lock: dict[str, Any], release_evidence: Any
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], int, int]:
    try:
        release_evidence._validate_lock(lock)
    except release_evidence.ReleaseEvidenceError as error:
        raise ScopedReleaseScopeError(f"native lock is invalid: {error}") from error
    _exact_keys(lock, _LOCK_KEYS, "native lock")
    if type(lock["schema"]) is not int or lock["schema"] != 2:
        raise ScopedReleaseScopeError("native lock schema must be 2")
    if lock["release_state"] not in {"unreleased-preview", "release"}:
        raise ScopedReleaseScopeError("native lock release_state is invalid")

    shim = _object(lock["shim"], "native lock shim")
    _exact_keys(shim, _LOCK_SHIM_KEYS, "native lock shim")
    shim_abi = _integer(shim["abi"], "native lock shim.abi")
    required_api = _integer(
        shim["required_ort_api"], "native lock shim.required_ort_api"
    )
    if shim["source_revision"] is not None and not isinstance(shim["source_revision"], str):
        raise ScopedReleaseScopeError("native lock shim.source_revision is invalid")

    onnx = _object(lock["onnxruntime"], "native lock onnxruntime")
    _exact_keys(onnx, _LOCK_ONNX_KEYS, "native lock onnxruntime")
    compatibility = _object(
        onnx["compatibility_floor"], "native lock compatibility_floor"
    )
    _exact_keys(
        compatibility,
        _LOCK_COMPATIBILITY_KEYS,
        "native lock compatibility_floor",
    )
    compatibility_api = _integer(
        compatibility["c_api"], "native lock compatibility_floor.c_api"
    )
    if compatibility_api != required_api:
        raise ScopedReleaseScopeError(
            "native lock compatibility C API must equal shim required ORT API"
        )

    release_targets = _array(
        lock["release_targets"], "native lock release_targets", length=8
    )
    for index, (raw_target, contract) in enumerate(zip(release_targets, TARGET_CONTRACTS)):
        label = f"native lock release_targets[{index}]"
        target = _object(raw_target, label)
        _exact_keys(target, _LOCK_RELEASE_TARGET_KEYS, label)
        if target != {
            "os": contract.os,
            "architecture": contract.architecture,
            "variant": contract.variant,
            "flavor": contract.flavor,
        }:
            raise ScopedReleaseScopeError(
                "native lock release target inventory or order differs from the closed CPU baseline"
            )

    artifacts = _array(lock["artifacts"], "native lock artifacts", length=8)
    artifacts_by_target: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    artifact_ids: set[str] = set()
    for index, raw_artifact in enumerate(artifacts):
        label = f"native lock artifacts[{index}]"
        artifact = _object(raw_artifact, label)
        _exact_keys(artifact, _LOCK_ARTIFACT_KEYS, label)
        artifact_id = _string(artifact["id"], f"{label}.id", maximum=256)
        if _IDENTIFIER.fullmatch(artifact_id) is None or artifact_id in artifact_ids:
            raise ScopedReleaseScopeError("native lock artifact IDs must be unique closed identifiers")
        artifact_ids.add(artifact_id)
        target = _object(artifact["target"], f"{label}.target")
        _exact_keys(target, _LOCK_TARGET_KEYS, f"{label}.target")
        target_os = _string(target["os"], f"{label}.target.os", maximum=16)
        target_architecture = _string(
            target["architecture"],
            f"{label}.target.architecture",
            maximum=32,
        )
        target_variant = _string(
            target["variant"], f"{label}.target.variant", maximum=16
        )
        minimum_os = _string(target["min_os"], f"{label}.target.min_os", maximum=64)
        if _MINIMUM_OS.fullmatch(minimum_os) is None:
            raise ScopedReleaseScopeError(f"{label}.target.min_os is invalid")
        artifact_flavor = _string(
            artifact["flavor"], f"{label}.flavor", maximum=64
        )
        if artifact_flavor != "cpu":
            raise ScopedReleaseScopeError(f"{label}.flavor must be cpu")
        runtime_mode = _string(
            artifact["runtime_mode"], f"{label}.runtime_mode", maximum=16
        )
        if runtime_mode not in {"linked", "bundled"}:
            raise ScopedReleaseScopeError(f"{label}.runtime_mode is outside the baseline")
        key = (
            target_os,
            target_architecture,
            target_variant,
            artifact_flavor,
        )
        if key != TARGET_CONTRACTS[index].key:
            raise ScopedReleaseScopeError(
                "native lock artifact inventory or order differs from the closed CPU baseline"
            )
        if key in artifacts_by_target:
            raise ScopedReleaseScopeError("native lock duplicates an artifact target")
        providers = artifact["providers"]
        if not isinstance(providers, list) or not 1 <= len(providers) <= 64:
            raise ScopedReleaseScopeError(f"{label}.providers is outside its bound")
        provider_ids: set[str] = set()
        for provider_index, raw_provider in enumerate(providers):
            provider_label = f"{label}.providers[{provider_index}]"
            provider = _object(raw_provider, provider_label)
            _exact_keys(provider, _LOCK_PROVIDER_KEYS, provider_label)
            provider_id = _string(
                provider["wrapper_id"], f"{provider_label}.wrapper_id", maximum=64
            )
            if provider_id in provider_ids:
                raise ScopedReleaseScopeError(f"{label} duplicates a provider ID")
            provider_ids.add(provider_id)
        if "cpu" not in provider_ids:
            raise ScopedReleaseScopeError(f"{label} does not contain the CPU provider")
        artifacts_by_target[key] = artifact

    expected_keys = {contract.key for contract in TARGET_CONTRACTS}
    if set(artifacts_by_target) != expected_keys:
        raise ScopedReleaseScopeError(
            "native lock artifacts do not exactly cover the closed CPU target inventory"
        )
    return artifacts_by_target, shim_abi, required_api


def _validate_provider_claim(value: Any, label: str) -> None:
    providers = _array(value, label, length=1)
    provider = _object(providers[0], f"{label}[0]")
    _exact_keys(provider, _PROVIDER_KEYS, f"{label}[0]")
    if provider != _CPU_PROVIDER[0]:
        raise ScopedReleaseScopeError(
            f"{label} must advertise only CPU with full-assignment evidence"
        )


def _validate_composition(
    value: Any,
    contract: CompositionContract,
    artifact: dict[str, Any],
    sherpa_package_version: str,
    label: str,
) -> None:
    composition = _object(value, label)
    expected_keys = _COMPOSITION_KEYS
    if contract.runtime_identity is not None:
        expected_keys = expected_keys | {"runtimeIdentity"}
    _exact_keys(composition, expected_keys, label)
    expected = {
        "id": contract.id,
        "kind": contract.kind,
        "artifactId": contract.artifact_id,
        "runtimeOwner": contract.runtime_owner,
        "runtimeMode": contract.runtime_mode,
    }
    for key, expected_value in expected.items():
        if composition[key] != expected_value:
            raise ScopedReleaseScopeError(f"{label}.{key} does not match the closed scope")
    _validate_provider_claim(
        composition["advertisedProviders"], f"{label}.advertisedProviders"
    )
    if contract.kind == "locked-artifact":
        if contract.artifact_id != artifact["id"]:
            raise ScopedReleaseScopeError(f"{label}.artifactId does not match the locked target")
        if contract.runtime_mode != artifact["runtime_mode"]:
            raise ScopedReleaseScopeError(f"{label}.runtimeMode does not match the locked artifact")
    else:
        identity = _object(composition["runtimeIdentity"], f"{label}.runtimeIdentity")
        _exact_keys(identity, _RUNTIME_IDENTITY_KEYS, f"{label}.runtimeIdentity")
        owner, owner_version, runtime_version = contract.runtime_identity or ("", "", "")
        if identity != {
            "ownerPackage": owner,
            "ownerPackageVersion": owner_version,
            "onnxRuntimeVersion": runtime_version,
        }:
            raise ScopedReleaseScopeError(
                f"{label}.runtimeIdentity does not match the sherpa-owned runtime"
            )
        if identity["ownerPackageVersion"] != sherpa_package_version:
            raise ScopedReleaseScopeError(
                f"{label}.runtimeIdentity does not match the resolved sherpa package"
            )


def _validate_scope_document(
    scope: dict[str, Any],
    *,
    lock_sha256: str,
    package_version: str,
    shim_abi: int,
    required_ort_api: int,
    sherpa_lock_sha256: str,
    package_name: str,
    sherpa_package_version: str,
    artifacts_by_target: dict[tuple[str, str, str, str], dict[str, Any]],
) -> None:
    _exact_keys(scope, _ROOT_KEYS, "scoped release policy")
    if type(scope["schemaVersion"]) is not int or scope["schemaVersion"] != 1:
        raise ScopedReleaseScopeError("scoped release policy schemaVersion must be 1")
    if scope["policyId"] != POLICY_ID:
        raise ScopedReleaseScopeError("scoped release policy policyId is invalid")
    if scope["claimStatus"] != CLAIM_STATUS:
        raise ScopedReleaseScopeError("scoped release policy must remain scope-only")

    baseline = _object(scope["baseline"], "scoped release policy baseline")
    _exact_keys(baseline, _BASELINE_KEYS, "scoped release policy baseline")
    _integer(baseline["shimAbi"], "scoped release policy baseline.shimAbi")
    _integer(
        baseline["requiredOrtApi"],
        "scoped release policy baseline.requiredOrtApi",
    )
    expected_baseline = {
        "nativeLockPath": NATIVE_LOCK_PATH,
        "nativeLockSha256": lock_sha256,
        "sherpaPubspecLockPath": SHERPA_PUBSPEC_LOCK_PATH,
        "sherpaPubspecLockSha256": sherpa_lock_sha256,
        "packageName": package_name,
        "packageVersion": package_version,
        "shimAbi": shim_abi,
        "requiredOrtApi": required_ort_api,
    }
    if baseline != expected_baseline:
        raise ScopedReleaseScopeError(
            "scoped release policy baseline does not match the current package and native lock"
        )

    targets = _array(scope["targets"], "scoped release policy targets", length=8)
    composition_ids: set[str] = set()
    selected_count = 0
    for index, (raw_target, contract) in enumerate(zip(targets, TARGET_CONTRACTS)):
        label = f"scoped release policy targets[{index}]"
        target = _object(raw_target, label)
        expected_keys = _TARGET_COMMON_KEYS | (
            {"compositions"} if contract.disposition == "selected" else {"reason"}
        )
        _exact_keys(target, expected_keys, label)
        common = {
            "os": contract.os,
            "architecture": contract.architecture,
            "variant": contract.variant,
            "flavor": contract.flavor,
            "minimumOs": contract.minimum_os,
            "disposition": contract.disposition,
        }
        for key, expected in common.items():
            if target[key] != expected:
                raise ScopedReleaseScopeError(f"{label}.{key} does not match the closed scope")
        artifact = artifacts_by_target[contract.key]
        if artifact["target"]["min_os"] != contract.minimum_os:
            raise ScopedReleaseScopeError(f"{label}.minimumOs does not match the locked artifact")
        if contract.disposition == "unsupported":
            if target["reason"] != contract.reason:
                raise ScopedReleaseScopeError(f"{label}.reason does not match the closed exclusion")
            continue
        selected_count += 1
        compositions = _array(
            target["compositions"],
            f"{label}.compositions",
            length=len(contract.compositions),
        )
        for composition_index, (composition, composition_contract) in enumerate(
            zip(compositions, contract.compositions)
        ):
            composition_label = f"{label}.compositions[{composition_index}]"
            _validate_composition(
                composition,
                composition_contract,
                artifact,
                sherpa_package_version,
                composition_label,
            )
            if composition_contract.id in composition_ids:
                raise ScopedReleaseScopeError("scoped release policy duplicates a composition ID")
            composition_ids.add(composition_contract.id)
    if selected_count != 4 or len(composition_ids) != 5:
        raise ScopedReleaseScopeError(
            "scoped release policy must select exactly four targets and five compositions"
        )

    deferred = _array(
        scope["deferredCapabilities"],
        "scoped release policy deferredCapabilities",
        length=2,
    )
    for index, (raw_capability, (expected_id, expected_reason)) in enumerate(
        zip(deferred, DEFERRED_CAPABILITIES)
    ):
        label = f"scoped release policy deferredCapabilities[{index}]"
        capability = _object(raw_capability, label)
        _exact_keys(capability, _DEFERRED_KEYS, label)
        if capability != {
            "id": expected_id,
            "disposition": "unsupported",
            "reason": expected_reason,
        }:
            raise ScopedReleaseScopeError(f"{label} does not match the closed deferral")


def validate_scope(repository: Path, scope_path: Path) -> dict[str, Any]:
    """Validate the canonical policy and return a deterministic path-free record."""

    repository = _canonical_repository(repository)
    canonical_scope = _canonical_scope_path(repository, scope_path)
    release_helper_path = _validate_pinned_helper(
        repository,
        relative_path=RELEASE_EVIDENCE_HELPER_PATH,
        expected_sha256=EXPECTED_RELEASE_EVIDENCE_HELPER_SHA256,
        label="native-lock validator",
    )
    sherpa_helper_path = _validate_pinned_helper(
        repository,
        relative_path=SHERPA_LOCK_HELPER_PATH,
        expected_sha256=EXPECTED_SHERPA_LOCK_HELPER_SHA256,
        label="sherpa-lock validator",
    )
    source_manifest_helper_path = _validate_pinned_helper(
        repository,
        relative_path=SOURCE_MANIFEST_HELPER_PATH,
        expected_sha256=EXPECTED_SOURCE_MANIFEST_HELPER_SHA256,
        label="source-manifest validator",
    )
    source_manifest = _import_pinned_helper(
        module_name="source_checksum_manifest",
        expected_path=source_manifest_helper_path,
        expected_sha256=EXPECTED_SOURCE_MANIFEST_HELPER_SHA256,
        label="source-manifest validator",
    )
    release_evidence = _import_pinned_helper(
        module_name="generate_release_sbom",
        expected_path=release_helper_path,
        expected_sha256=EXPECTED_RELEASE_EVIDENCE_HELPER_SHA256,
        label="native-lock validator",
        pinned_modules={"source_checksum_manifest": source_manifest},
    )
    load_order_receipt = _import_pinned_helper(
        module_name="validate_android_load_order_receipt",
        expected_path=sherpa_helper_path,
        expected_sha256=EXPECTED_SHERPA_LOCK_HELPER_SHA256,
        label="sherpa-lock validator",
    )
    schema_path = _repository_file(repository, SCHEMA_PATH)
    lock_path = _repository_file(repository, NATIVE_LOCK_PATH)
    sherpa_lock_path = _repository_file(repository, SHERPA_PUBSPEC_LOCK_PATH)
    pubspec_path = _repository_file(repository, PUBSPEC_PATH)

    schema_raw = _read_regular(schema_path, label="scope schema", maximum=MAX_SCHEMA_BYTES)
    _strict_json(schema_raw, label="scope schema")
    schema_sha256 = hashlib.sha256(schema_raw).hexdigest()
    if schema_sha256 != EXPECTED_SCHEMA_SHA256:
        raise ScopedReleaseScopeError(
            "scope schema bytes do not match the validator-pinned schema"
        )

    lock_raw = _read_regular(lock_path, label="native lock", maximum=MAX_LOCK_BYTES)
    lock = _strict_json(lock_raw, label="native lock")
    artifacts_by_target, shim_abi, required_ort_api = _validate_lock(
        lock, release_evidence
    )
    lock_sha256 = hashlib.sha256(lock_raw).hexdigest()

    sherpa_lock_raw = _read_regular(
        sherpa_lock_path,
        label="sherpa reference pubspec lock",
        maximum=MAX_PUBSPEC_BYTES,
    )
    try:
        load_order_receipt._validate_pubspec_lock(
            sherpa_lock_raw,
            "arm64-v8a",
            SHERPA_PACKAGE_VERSION,
        )
    except load_order_receipt.LoadOrderReceiptError as error:
        raise ScopedReleaseScopeError(
            f"sherpa reference pubspec lock is invalid: {error}"
        ) from error
    sherpa_lock_sha256 = hashlib.sha256(sherpa_lock_raw).hexdigest()

    pubspec_raw = _read_regular(pubspec_path, label="pubspec", maximum=MAX_PUBSPEC_BYTES)
    package_name, package_version = _parse_pubspec(pubspec_raw)

    scope_raw = _read_regular(canonical_scope, label="scoped release policy", maximum=MAX_SCOPE_BYTES)
    scope = _strict_json(scope_raw, label="scoped release policy")
    _validate_scope_document(
        scope,
        lock_sha256=lock_sha256,
        package_version=package_version,
        shim_abi=shim_abi,
        required_ort_api=required_ort_api,
        sherpa_lock_sha256=sherpa_lock_sha256,
        package_name=package_name,
        sherpa_package_version=SHERPA_PACKAGE_VERSION,
        artifacts_by_target=artifacts_by_target,
    )

    validator_path = Path(__file__).absolute()
    validator_raw = _read_regular(
        validator_path,
        label="scope validator",
        maximum=MAX_VALIDATOR_BYTES,
    )
    selected = [contract.display_key for contract in TARGET_CONTRACTS if contract.disposition == "selected"]
    unsupported = [
        contract.display_key
        for contract in TARGET_CONTRACTS
        if contract.disposition == "unsupported"
    ]
    selected_compositions = [
        composition.id
        for contract in TARGET_CONTRACTS
        if contract.disposition == "selected"
        for composition in contract.compositions
    ]
    return {
        "schemaVersion": 1,
        "result": "validated",
        "policyId": POLICY_ID,
        "claimStatus": CLAIM_STATUS,
        "validatorSha256": hashlib.sha256(validator_raw).hexdigest(),
        "schemaSha256": schema_sha256,
        "scopeSha256": hashlib.sha256(scope_raw).hexdigest(),
        "nativeLockSha256": lock_sha256,
        "sherpaPubspecLockSha256": sherpa_lock_sha256,
        "packageName": package_name,
        "packageVersion": package_version,
        "selectedTargetKeys": selected,
        "selectedCompositionIds": selected_compositions,
        "unsupportedTargetKeys": unsupported,
        "deferredCapabilityIds": [identifier for identifier, _ in DEFERRED_CAPABILITIES],
        "claimBoundary": CLAIM_BOUNDARY,
    }


def _canonical_json(value: Any) -> bytes:
    encoded = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ScopedReleaseScopeError("validation record exceeds its size bound")
    return encoded


def write_validation_record(path: Path, record: dict[str, Any]) -> None:
    """Publish a completed record atomically without replacing an existing path."""

    if not path.is_absolute():
        raise ScopedReleaseScopeError("--output must be absolute")
    parent = path.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise ScopedReleaseScopeError("--output parent cannot be inspected") from error
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise ScopedReleaseScopeError("--output parent must be a directory, not a link")
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as error:
        raise ScopedReleaseScopeError("--output parent cannot be resolved") from error
    if resolved_parent != parent:
        raise ScopedReleaseScopeError(
            "--output parent must be canonical and contain no link"
        )
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ScopedReleaseScopeError("--output cannot be inspected") from error
    else:
        raise ScopedReleaseScopeError("--output must not already exist")

    encoded = _canonical_json(record)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    published = False
    succeeded = False
    owned_identity: tuple[int, int] | None = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_metadata = temporary.lstat()
        owned_identity = (temporary_metadata.st_dev, temporary_metadata.st_ino)
        if not stat.S_ISREG(temporary_metadata.st_mode) or temporary_metadata.st_size != len(encoded):
            raise ScopedReleaseScopeError("temporary validation output changed before publication")
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise ScopedReleaseScopeError("--output must not already exist") from error
        except OSError as error:
            raise ScopedReleaseScopeError("validation output could not be published atomically") from error
        published = True
        published_metadata = path.lstat()
        if (
            not stat.S_ISREG(published_metadata.st_mode)
            or published_metadata.st_dev != temporary_metadata.st_dev
            or published_metadata.st_ino != temporary_metadata.st_ino
            or published_metadata.st_size != len(encoded)
        ):
            raise ScopedReleaseScopeError("published validation output is not the completed record")
        succeeded = True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        if published and not succeeded and owned_identity is not None:
            try:
                final_metadata = path.lstat()
            except OSError:
                final_metadata = None
            if (
                final_metadata is not None
                and (final_metadata.st_dev, final_metadata.st_ino) == owned_identity
            ):
                try:
                    path.unlink()
                except OSError:
                    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        record = validate_scope(arguments.repository, arguments.scope)
        write_validation_record(arguments.output, record)
        print(
            "validated scope-only release policy "
            f"sha256={record['scopeSha256']} selected=4 unsupported=4"
        )
        return 0
    except ScopedReleaseScopeError as error:
        print(f"scoped release scope error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
