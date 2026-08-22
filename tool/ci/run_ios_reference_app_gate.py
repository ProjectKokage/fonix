#!/usr/bin/env python3
"""Build, exercise, and audit the committed Fonix iOS reference app."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, NamedTuple, Sequence, TypeVar
import urllib.parse


_COMMON_PATH = Path(__file__).with_name("run_macos_reference_app_gate.py")
_COMMON_SPEC = importlib.util.spec_from_file_location(
    "_fonix_ios_reference_gate_common", _COMMON_PATH
)
if _COMMON_SPEC is None or _COMMON_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("could not load the reference-gate common helpers")
_COMMON = importlib.util.module_from_spec(_COMMON_SPEC)
_COMMON_SPEC.loader.exec_module(_COMMON)

_BOUNDED_PROCESS_PATH = Path(__file__).with_name("bounded_process.py")
_BOUNDED_PROCESS_SPEC = importlib.util.spec_from_file_location(
    "_fonix_ios_reference_gate_bounded_process", _BOUNDED_PROCESS_PATH
)
if (
    _BOUNDED_PROCESS_SPEC is None or _BOUNDED_PROCESS_SPEC.loader is None
):  # pragma: no cover
    raise RuntimeError("could not load the bounded process helper")
_BOUNDED_PROCESS = importlib.util.module_from_spec(_BOUNDED_PROCESS_SPEC)
_BOUNDED_PROCESS_SPEC.loader.exec_module(_BOUNDED_PROCESS)

# The common copier is loaded into a private module instance. Extend its exact
# generated-path inventory before copying the committed iOS scaffold.
_COMMON.GENERATED_EXCLUSIONS = frozenset(
    set(_COMMON.GENERATED_EXCLUSIONS)
    | {
        "ios/.symlinks",
        "ios/Flutter/ephemeral",
        "ios/Flutter/Generated.xcconfig",
        "ios/Flutter/flutter_export_environment.sh",
        "ios/Podfile.lock",
        "ios/Pods",
        "ios/Runner/GeneratedPluginRegistrant.h",
        "ios/Runner/GeneratedPluginRegistrant.m",
    }
)

IosReferenceAppGateError = _COMMON.MacOsReferenceAppGateError

VALIDATED_FLUTTER_REVISION = "bd1e75d918605c91b411e8789fb911e6c9a84534"
VALIDATED_XCODE_VERSION = "26.6"
VALIDATED_XCODE_BUILD = "17F113"
VALIDATED_IOS_SDK = "26.5"
VALIDATED_MACOS_VERSION = "26.5.2"
VALIDATED_MACOS_BUILD = "25F84"
VALIDATED_DEVELOPER_DIRECTORY = "/Applications/Xcode.app/Contents/Developer"
VALIDATED_SIMULATOR_RUNTIME = "com.apple.CoreSimulator.SimRuntime.iOS-26-5"

APPLICATION_MINIMUM_OS = "15.1"
APPLICATION_BUNDLE_NAME = "Runner.app"
APPLICATION_EXECUTABLE_NAME = "Runner"
APPLICATION_BUNDLE_IDENTIFIER = "dev.fonix.fonixReference"
ARTIFACT_VERSION = "1.27.1"
ARCHIVE_BASENAME = "microsoft.ml.onnxruntime.1.27.1.nupkg"
ARCHIVE_SHA256 = "9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383"
ARCHIVE_SIZE_BYTES = 135_152_698
MODEL_SHA256 = "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10"

ARTIFACT_IDS = {
    "device": "onnxruntime-1.27.1-ios-arm64-device-cpu",
    "simulator": "onnxruntime-1.27.1-ios-arm64-simulator-cpu",
}

REFERENCE_RECEIPT_PREFIX = "FONIX_REFERENCE_RECEIPT="
REFERENCE_FAILURE_PREFIX = "FONIX_REFERENCE_FAILURE="
REFERENCE_FAILURE_KEYS = frozenset(
    {"schemaVersion", "status", "errorType", "challenge", "processId"}
)
REFERENCE_RECEIPT_BASENAME_PREFIX = "fonix-reference-receipt-"
REFERENCE_RECEIPT_SUFFIX = ".txt"
REFERENCE_STAGING_SUFFIX = ".txt.tmp"

COMMAND_TIMEOUT_SECONDS = 30 * 60
ENVIRONMENT_CHECK_TIMEOUT_SECONDS = 2 * 60
DEPENDENCY_RESOLUTION_TIMEOUT_SECONDS = 10 * 60
SOURCE_CHECK_TIMEOUT_SECONDS = 30 * 60
ASSET_PREPARATION_TIMEOUT_SECONDS = 30 * 60
APPLICATION_BUILD_TIMEOUT_SECONDS = 30 * 60
SIMULATOR_COMMAND_TIMEOUT_SECONDS = 2 * 60
SIMULATOR_BOOT_TIMEOUT_SECONDS = 10 * 60
REFERENCE_TIMEOUT_SECONDS = 2 * 60
REFERENCE_POLL_INTERVAL_SECONDS = 0.05
PROCESS_SETTLEMENT_TIMEOUT_SECONDS = 10
PROCESS_PROBE_TIMEOUT_SECONDS = 30
PLUTIL_TIMEOUT_SECONDS = 30
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_OUTPUT_BYTES = 256 * 1024
MAX_REFERENCE_FILE_BYTES = 16 * 1024 + 256
MAX_REPORT_BYTES = 32 * 1024
MAX_LOCK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_SIMCTL_BYTES = 2 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_APPLICATION_ENTRIES = 100_000
MAX_APPLICATION_FILES = 20_000
MAX_APPLICATION_BYTES = 512 * 1024 * 1024
MAX_HOOK_JSON_BYTES = 2 * 1024 * 1024
MAX_HOOK_INVOCATIONS = 256
COPY_CHUNK_BYTES = 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UDID = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)
_CLOSED_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HOOK_INVOCATION = re.compile(r"^[0-9a-f]{10}$")

_IOS_REFERENCE_SHIM_CLAIM_BOUNDARY = (
    "The closed final Mach-O inventory and load-command profiles prove that no "
    "separately packaged raw Mach-O or audited load-command dependency is "
    "attributable to ONNX Runtime. Validated native-assets hook metadata, exact "
    "embedded schema-3 identity, dyld exports/fixups, and normalized runtime fields "
    "bind the packaged shim to the supplied prepackage hook output across the "
    "explicit local-symbol-strip, install-name, and code-sign boundary. This does "
    "not prove the absence of runtime dlopen behavior, an extra static ONNX Runtime "
    "copy in another Mach-O, or that the selected static archive was linked exactly once."
)
_IOS_NORMALIZED_COMPARISON_SCOPE = (
    "Mach header CPU/subtype/filetype/flags, pair-matched LC_UUID, non-LINKEDIT "
    "loadable segment and section metadata/bytes/padding outside the mutable "
    "load-command region, retained external/undefined symbols and string "
    "semantics, canonical LC_DYSYMTAB table payloads, immutable load commands, "
    "and referenced dyld fixup/export payloads"
)
_IOS_NORMALIZED_RUNTIME_FIELDS_SHA256 = {
    "device": "268705a45b764c01370f6e78b5e28d7d403dac008ee685391b666f9d1a3880ab",
    "simulator": "303896da46bab6fef75f29e4a3ff15a9f6b0042ba06187940e086090114c7892",
}
_IOS_ACCOUNTED_TRANSFORMATIONS = {
    "device": [
        "absolute-prepackage-id-to-framework-rpath-id",
        "local-symbol-strip",
        "adhoc-code-signature-addition",
        "linkedit-resize",
    ],
    "simulator": [
        "absolute-prepackage-id-to-framework-rpath-id",
        "adhoc-code-signature-replacement",
        "linkedit-resize",
    ],
}
_IOS_MACHO_RELATIVE_PATHS = (
    "Frameworks/App.framework/App",
    "Frameworks/Flutter.framework/Flutter",
    "Frameworks/fonix_shim.framework/fonix_shim",
    "Runner",
)
_IOS_FRAMEWORK_INVENTORY = [
    {
        "path": "Frameworks/App.framework",
        "machOBinaries": ["Frameworks/App.framework/App"],
    },
    {
        "path": "Frameworks/Flutter.framework",
        "machOBinaries": ["Frameworks/Flutter.framework/Flutter"],
    },
    {
        "path": "Frameworks/fonix_shim.framework",
        "machOBinaries": ["Frameworks/fonix_shim.framework/fonix_shim"],
    },
]
_IOS_MACHO_RECORD_KEYS = {
    "path",
    "owner",
    "architecture",
    "machoPlatform",
    "minimumOs",
    "cpuSubtype",
    "fileType",
    "headerFlags",
    "codeSignatureLoadCommands",
    "dynamicDependencies",
    "dylibDependencies",
    "dependencyMetadataSha256",
    "loadCommandKindsSha256",
    "dylibId",
    "rpaths",
}
_IOS_DYLIB_DEPENDENCY_KEYS = {
    "kind",
    "path",
    "currentVersion",
    "compatibilityVersion",
}
_IOS_DYLIB_ID_KEYS = {
    "path",
    "timestamp",
    "currentVersion",
    "compatibilityVersion",
}
_IOS_DYLIB_DEPENDENCY_KINDS = frozenset(
    {"load", "lazy", "weak", "reexport", "upward"}
)
_IOS_MACHO_MINIMUM_OS = {
    "Frameworks/App.framework/App": "15.0.0",
    "Frameworks/Flutter.framework/Flutter": "15.0.0",
    "Frameworks/fonix_shim.framework/fonix_shim": "15.1.0",
    "Runner": "15.1.0",
}
_IOS_MACHO_FILE_TYPES = {
    "Frameworks/App.framework/App": 6,
    "Frameworks/Flutter.framework/Flutter": 6,
    "Frameworks/fonix_shim.framework/fonix_shim": 6,
    "Runner": 2,
}
_IOS_MACHO_DYLIB_IDS = {
    "device": {
        "Frameworks/App.framework/App": {
            "path": "@rpath/App.framework/App",
            "timestamp": 0,
            "currentVersion": 0,
            "compatibilityVersion": 0,
        },
        "Frameworks/Flutter.framework/Flutter": {
            "path": "@rpath/Flutter.framework/Flutter",
            "timestamp": 0,
            "currentVersion": 0,
            "compatibilityVersion": 0,
        },
        "Frameworks/fonix_shim.framework/fonix_shim": {
            "path": "@rpath/fonix_shim.framework/fonix_shim",
            "timestamp": 1,
            "currentVersion": 0,
            "compatibilityVersion": 0,
        },
        "Runner": None,
    },
    "simulator": {
        "Frameworks/App.framework/App": {
            "path": "@rpath/App.framework/App",
            "timestamp": 1,
            "currentVersion": 0,
            "compatibilityVersion": 0,
        },
        "Frameworks/Flutter.framework/Flutter": {
            "path": "@rpath/Flutter.framework/Flutter",
            "timestamp": 0,
            "currentVersion": 0,
            "compatibilityVersion": 0,
        },
        "Frameworks/fonix_shim.framework/fonix_shim": {
            "path": "@rpath/fonix_shim.framework/fonix_shim",
            "timestamp": 1,
            "currentVersion": 0,
            "compatibilityVersion": 0,
        },
        "Runner": None,
    },
}
_IOS_MACHO_RPATHS = {
    "Frameworks/App.framework/App": [
        "@executable_path/Frameworks",
        "@loader_path/Frameworks",
    ],
    "Frameworks/Flutter.framework/Flutter": [],
    "Frameworks/fonix_shim.framework/fonix_shim": [],
    "Runner": [
        "/usr/lib/swift",
        "@executable_path/Frameworks",
    ],
}
_IOS_AUDITED_EXECUTABLE_PATHS = frozenset(_IOS_MACHO_RELATIVE_PATHS)
_IOS_INSTALLED_EXECUTABLE_PATHS = frozenset({"Runner"})
_IOS_TRANSPORT_CLEARED_EXECUTABLE_PATHS = (
    _IOS_AUDITED_EXECUTABLE_PATHS - _IOS_INSTALLED_EXECUTABLE_PATHS
)
_IOS_MACHO_HEADER_FLAGS = {
    "device": {
        "Frameworks/App.framework/App": 0x100085,
        "Frameworks/Flutter.framework/Flutter": 0x2910085,
        "Frameworks/fonix_shim.framework/fonix_shim": 0x910085,
        "Runner": 0x210085,
    },
    "simulator": {
        "Frameworks/App.framework/App": 0x2100085,
        "Frameworks/Flutter.framework/Flutter": 0x2900085,
        "Frameworks/fonix_shim.framework/fonix_shim": 0x910085,
        "Runner": 0x210085,
    },
}
_T = TypeVar("_T")


class PinnedArchive(NamedTuple):
    variant: str
    artifact_id: str
    basename: str
    sha256: str
    size_bytes: int


class AppleEnvironment(NamedTuple):
    xcode_version: str
    xcode_build: str
    device_sdk: str
    simulator_sdk: str
    macos_version: str
    macos_build: str


class SimulatorIdentity(NamedTuple):
    udid: str
    udid_sha256: str
    name: str
    runtime: str
    initial_state: str


class CommandStatus(NamedTuple):
    return_code: int
    stdout: str
    stderr: str


class TreeIdentity(NamedTuple):
    files: dict[str, dict[str, object]]
    file_count: int
    byte_count: int
    tree_sha256: str


class ReferenceShim(NamedTuple):
    path: Path
    sha256: str
    invocation_hash: str


class InstalledApplicationOwnership(NamedTuple):
    container: Path
    tree: TreeIdentity


class InstalledApplicationExpectation(NamedTuple):
    container: Path
    tree: TreeIdentity


class IosReferenceLifecycleError(IosReferenceAppGateError):
    """Both primary simulator work and owned-state cleanup failed."""

    def __init__(self, primary_error: BaseException, cleanup_error: BaseException):
        super().__init__("simulator work and owned-state cleanup both failed")
        self.primary_error = primary_error
        self.cleanup_error = cleanup_error


class IosReferenceCleanupError(IosReferenceAppGateError):
    """One or more owned simulator resources failed to settle."""

    def __init__(self, errors: Sequence[BaseException]):
        super().__init__("owned simulator cleanup did not settle")
        self.errors = tuple(errors)


def _strict_json(data: bytes | str, label: str, *, maximum: int) -> Any:
    return _COMMON._strict_json(data, label, maximum=maximum)


def _regular_file(path: Path, label: str, *, maximum: int | None = None) -> Path:
    return _COMMON._regular_file(path, label, maximum=maximum)


def _directory(path: Path, label: str) -> Path:
    return _COMMON._directory(path, label)


def _sha256_file(path: Path, label: str) -> str:
    path = _regular_file(path, label)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a host-tool environment with simulator child injection removed."""

    environment = _COMMON._tool_environment(base)
    for key in tuple(environment):
        if key.startswith("SIMCTL_CHILD_") or key in {
            "FONIX_REFERENCE_SMOKE",
            "FONIX_REFERENCE_CHALLENGE",
        }:
            environment.pop(key)
    return environment


def _run(
    command: Sequence[str],
    *,
    timeout_seconds: int,
    maximum_output: int,
    operation: str,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Any:
    if timeout_seconds <= 0 or timeout_seconds > COMMAND_TIMEOUT_SECONDS:
        raise IosReferenceAppGateError(f"{operation} timeout is outside its bound")
    if maximum_output <= 0 or maximum_output > MAX_COMMAND_OUTPUT_BYTES:
        raise IosReferenceAppGateError(
            f"{operation} output bound is outside its allowed range"
        )
    try:
        output = _BOUNDED_PROCESS.run_bounded(
            command,
            operation=operation,
            cwd=cwd,
            environment=dict(
                _tool_environment() if environment is None else environment
            ),
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=maximum_output,
            maximum_stderr_bytes=maximum_output,
        )
    except _BOUNDED_PROCESS.BoundedProcessError as error:
        raise IosReferenceAppGateError(str(error)) from error
    return _COMMON.CommandOutput(stdout=output.stdout, stderr=output.stderr)


def _run_status(
    command: Sequence[str],
    *,
    timeout_seconds: int,
    maximum_output: int,
    operation: str,
) -> CommandStatus:
    if timeout_seconds <= 0 or timeout_seconds > PROCESS_PROBE_TIMEOUT_SECONDS:
        raise IosReferenceAppGateError(f"{operation} timeout is outside its bound")
    if maximum_output <= 0 or maximum_output > MAX_COMMAND_OUTPUT_BYTES:
        raise IosReferenceAppGateError(f"{operation} output bound is invalid")
    try:
        output = _BOUNDED_PROCESS.run_bounded(
            command,
            operation=operation,
            cwd=None,
            environment=_tool_environment(),
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=maximum_output,
            maximum_stderr_bytes=maximum_output,
        )
    except _BOUNDED_PROCESS.BoundedProcessExitError as error:
        if error.residual_group_members or error.cleanup_failures:
            raise IosReferenceAppGateError(str(error)) from error
        return CommandStatus(
            return_code=error.return_code,
            stdout=error.stdout or "",
            stderr=error.stderr or "",
        )
    except _BOUNDED_PROCESS.BoundedProcessError as error:
        raise IosReferenceAppGateError(str(error)) from error
    return CommandStatus(return_code=0, stdout=output.stdout, stderr=output.stderr)


def _run_with_input(
    command: Sequence[str],
    *,
    input_bytes: bytes,
    maximum_input: int,
    maximum_output: int,
    timeout_seconds: int,
    operation: str,
) -> Any:
    if maximum_input <= 0 or maximum_input > MAX_SIMCTL_BYTES:
        raise IosReferenceAppGateError(
            f"{operation} input bound is outside its allowed range"
        )
    if len(input_bytes) > maximum_input:
        raise IosReferenceAppGateError(f"{operation} input exceeds {maximum_input} bytes")
    if maximum_output <= 0 or maximum_output > MAX_SIMCTL_BYTES:
        raise IosReferenceAppGateError(
            f"{operation} output bound is outside its allowed range"
        )
    if timeout_seconds <= 0 or timeout_seconds > PLUTIL_TIMEOUT_SECONDS:
        raise IosReferenceAppGateError(f"{operation} timeout is outside its bound")
    if not command or command[-1] != "-":
        raise IosReferenceAppGateError(
            f"{operation} command must end with the bounded input placeholder"
        )
    with tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix="fonix-ios-plist-",
        suffix=".plist",
    ) as input_file:
        input_file.write(input_bytes)
        input_file.flush()
        os.fsync(input_file.fileno())
        return _run(
            (*command[:-1], input_file.name),
            timeout_seconds=timeout_seconds,
            maximum_output=maximum_output,
            operation=operation,
        )


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise IosReferenceAppGateError(f"{label} must be a non-empty string")
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_PATH_BYTES or any(byte < 0x20 for byte in encoded):
        raise IosReferenceAppGateError(f"{label} is outside its text bound")
    return value


def _positive_integer(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IosReferenceAppGateError(f"{label} must be an integer")
    if value <= 0 or value > maximum:
        raise IosReferenceAppGateError(f"{label} is outside its bound")
    return value


def _nonnegative_integer(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IosReferenceAppGateError(f"{label} must be an integer")
    if value < 0 or value > maximum:
        raise IosReferenceAppGateError(f"{label} is outside its bound")
    return value


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _load_pinned_archives(lock_path: Path) -> dict[str, PinnedArchive]:
    lock_path = _regular_file(lock_path, "native version lock", maximum=MAX_LOCK_BYTES)
    value = _strict_json(
        lock_path.read_bytes(), "native version lock", maximum=MAX_LOCK_BYTES
    )
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
        raise IosReferenceAppGateError("native version lock artifacts are invalid")

    selected: dict[str, PinnedArchive] = {}
    for raw_artifact in value["artifacts"]:
        if not isinstance(raw_artifact, dict):
            raise IosReferenceAppGateError("native artifact entry must be an object")
        target = raw_artifact.get("target")
        if not isinstance(target, dict):
            raise IosReferenceAppGateError("native artifact target must be an object")
        variant = target.get("variant")
        if (
            target.get("os") != "ios"
            or target.get("architecture") != "arm64"
            or variant not in ARTIFACT_IDS
            or raw_artifact.get("flavor") != "cpu"
        ):
            continue
        if variant in selected:
            raise IosReferenceAppGateError(
                f"native lock has duplicate iOS arm64 {variant} CPU artifacts"
            )
        if (
            raw_artifact.get("id") != ARTIFACT_IDS[variant]
            or raw_artifact.get("runtime_mode") != "linked"
            or target.get("min_os") != APPLICATION_MINIMUM_OS
        ):
            raise IosReferenceAppGateError(
                f"selected iOS {variant} artifact identity changed"
            )
        source = raw_artifact.get("source")
        if not isinstance(source, dict) or source.get("archive") != "zip":
            raise IosReferenceAppGateError(
                f"selected iOS {variant} artifact source is invalid"
            )
        url = _string(source.get("url"), f"selected iOS {variant} artifact URL")
        expected_url = (
            "https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime/"
            f"{ARTIFACT_VERSION}/{ARCHIVE_BASENAME}"
        )
        parsed = urllib.parse.urlsplit(url)
        if (
            url != expected_url
            or parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or PurePosixPath(parsed.path).name != ARCHIVE_BASENAME
        ):
            raise IosReferenceAppGateError(
                f"selected iOS {variant} artifact URL changed"
            )
        digest = _string(
            source.get("sha256"), f"selected iOS {variant} archive SHA-256"
        )
        size = _positive_integer(
            source.get("size_bytes"),
            f"selected iOS {variant} archive size",
            MAX_ARCHIVE_BYTES,
        )
        if digest != ARCHIVE_SHA256 or size != ARCHIVE_SIZE_BYTES:
            raise IosReferenceAppGateError(
                f"selected iOS {variant} archive identity changed"
            )
        selected[variant] = PinnedArchive(
            variant=variant,
            artifact_id=ARTIFACT_IDS[variant],
            basename=ARCHIVE_BASENAME,
            sha256=digest,
            size_bytes=size,
        )
    if set(selected) != set(ARTIFACT_IDS):
        raise IosReferenceAppGateError(
            "native lock must select exactly one device and simulator iOS arm64 CPU artifact"
        )
    return selected


def _copy_archive(
    source_cache: Path, work_directory: Path, archive: PinnedArchive
) -> Path:
    source_cache = _directory(source_cache, "supplied artifact cache")
    destination_directory = work_directory / ".fonix-artifact-cache"
    if destination_directory.exists() or destination_directory.is_symlink():
        raise IosReferenceAppGateError(
            "relative Fonix artifact cache survived the source copy"
        )
    destination_directory.mkdir(mode=0o700)
    return _copy_cache_member(
        source_cache=source_cache,
        destination_directory=destination_directory,
        basename=archive.basename,
        expected_sha256=archive.sha256,
        expected_size=archive.size_bytes,
        label="selected iOS archive",
    )


def _copy_cache_member(
    *,
    source_cache: Path,
    destination_directory: Path,
    basename: str,
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> Path:
    source_cache = _directory(source_cache, "supplied artifact cache")
    destination_directory = _directory(
        destination_directory, "relative Fonix artifact cache"
    )
    source = _regular_file(
        source_cache / basename,
        label,
        maximum=expected_size,
    )
    if source.stat().st_size != expected_size:
        raise IosReferenceAppGateError(f"{label} size differs from lock")
    destination = destination_directory / basename
    if destination.exists() or destination.is_symlink():
        raise IosReferenceAppGateError(f"{label} destination already exists")
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            while chunk := input_stream.read(COPY_CHUNK_BYTES):
                size += len(chunk)
                if size > expected_size:
                    raise IosReferenceAppGateError(
                        f"{label} exceeds its locked size"
                    )
                digest.update(chunk)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise IosReferenceAppGateError(
                f"{label} size or SHA-256 differs from lock"
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return _regular_file(destination, f"relative cached {label}")


def _patch_ios_hook_config(pubspec: Path) -> None:
    pubspec = _regular_file(pubspec, "reference pubspec", maximum=1024 * 1024)
    source = pubspec.read_text(encoding="utf-8")
    if "\r" in source:
        raise IosReferenceAppGateError("reference pubspec must use LF newlines")
    replacements = {
        "      runtime_mode: bundled\n": "      runtime_mode: linked\n",
        "      application_minimum_os: '14.0'\n": (
            "      application_minimum_os: '15.1'\n"
        ),
    }
    updated = source
    for old, new in replacements.items():
        if updated.count(old) != 1 or updated.count(new) != 0:
            raise IosReferenceAppGateError(
                "reference pubspec iOS hook patch no longer has one exact source scalar"
            )
        updated = updated.replace(old, new)
    reverse = updated
    for old, new in replacements.items():
        reverse = reverse.replace(new, old)
    if reverse != source:
        raise IosReferenceAppGateError(
            "reference pubspec iOS hook patch changed unexpected bytes"
        )
    pubspec.write_text(updated, encoding="utf-8", newline="")


def _require_ios_source_contract(work_directory: Path) -> None:
    project = _regular_file(
        work_directory / "ios/Runner.xcodeproj/project.pbxproj",
        "committed iOS project",
        maximum=4 * 1024 * 1024,
    )
    source = project.read_text(encoding="utf-8")
    floors = re.findall(r"IPHONEOS_DEPLOYMENT_TARGET = ([^;]+);", source)
    if len(floors) != 3 or set(floors) != {APPLICATION_MINIMUM_OS}:
        raise IosReferenceAppGateError(
            "committed iOS project must declare exactly three 15.1 deployment floors"
        )
    identifiers = re.findall(r"PRODUCT_BUNDLE_IDENTIFIER = ([^;]+);", source)
    if APPLICATION_BUNDLE_IDENTIFIER not in identifiers:
        raise IosReferenceAppGateError(
            "committed iOS project bundle identifier changed"
        )
    delegate = _regular_file(
        work_directory / "ios/Runner/AppDelegate.swift",
        "committed iOS application delegate",
        maximum=256 * 1024,
    ).read_text(encoding="utf-8")
    required_delegate_fragments = (
        'private static let referenceLaunchChannelName = '
        '"dev.fonix.reference/launch"',
        'private static let referenceLaunchMethod = "readSmokeActivation"',
        'private static let referenceSmokeKey = "FONIX_REFERENCE_SMOKE"',
        'private static let referenceChallengeKey = '
        '"FONIX_REFERENCE_CHALLENGE"',
        "FlutterImplicitEngineDelegate",
        "didInitializeImplicitFlutterEngine",
        "engineBridge.applicationRegistrar.messenger()",
        "guard call.method == Self.referenceLaunchMethod else",
        "result(FlutterMethodNotImplemented)",
        "guard call.arguments == nil else",
        "private lazy var referenceLaunchActivation = "
        "Self.readReferenceLaunchActivation()",
        "switch self.referenceLaunchActivation",
        "ProcessInfo.processInfo.environment",
        "if smoke == nil && challenge == nil",
        'smoke == "1"',
        "Self.isLowercaseHexChallenge(challenge)",
        "bytes.count == 64",
        'result(["schemaVersion": 1, "challenge": challenge])',
    )
    if any(fragment not in delegate for fragment in required_delegate_fragments):
        raise IosReferenceAppGateError(
            "committed iOS launch activation contract changed"
        )
    if "result(environment)" in delegate:
        raise IosReferenceAppGateError(
            "committed iOS launch activation exposes raw environment data"
        )
    main = _regular_file(
        work_directory / "lib/main.dart",
        "committed reference application entrypoint",
        maximum=256 * 1024,
    ).read_text(encoding="utf-8")
    resident_dispatch = (
        "      unawaited(\n"
        "        _runResidentPackagedSmoke(challenge, pid).catchError((Object _) {\n"
        "          stderr.writeln(residentReferencePublicationFailureDiagnostic);\n"
        "        }),\n"
        "      );"
    )
    desktop_guard = (
        "  if ((Platform.isMacOS || Platform.isLinux) &&\n"
        "      desktopReferenceSmokeEnabled(\n"
        "        isMacOS: Platform.isMacOS,\n"
        "        isLinux: Platform.isLinux,\n"
        "        environment: Platform.environment,\n"
        "      )) {"
    )
    benchmark_guard = (
        "  if ((Platform.isMacOS || Platform.isLinux) &&\n"
        "      desktopCpuBenchmarkEnabled(\n"
        "        isMacOS: Platform.isMacOS,\n"
        "        isLinux: Platform.isLinux,\n"
        "        environment: Platform.environment,\n"
        "      )) {"
    )
    required_main_fragments = (
        "import 'dart:async';",
        "import 'src/cpu_benchmark.dart';",
        "if (Platform.isIOS)",
        "challenge = await readIosResidentReferenceChallenge()",
        resident_dispatch,
        "residentReferenceActivationFailureDiagnostic",
        desktop_guard,
        benchmark_guard,
        "final int status = await _runPackagedCpuBenchmark();",
        "Future<int> _runPackagedCpuBenchmark() async {",
        "stdout.writeln('$cpuBenchmarkResultPrefix${result.toJsonString()}');",
        "Fonix CPU benchmark failed (${error.runtimeType}).",
        "exit(status);",
    )
    if (
        any(fragment not in main for fragment in required_main_fragments)
        or main.index(desktop_guard) >= main.index(benchmark_guard)
        or main.count("Platform.environment") != 2
        or main.count("Platform.isMacOS || Platform.isLinux") != 2
        or main.count("unawaited(") != 1
        or main.count("_runResidentPackagedSmoke(challenge, pid)") != 1
        or main.count("desktopReferenceSmokeEnabled(") != 1
        or main.count("desktopCpuBenchmarkEnabled(") != 1
        or main.count("_runPackagedCpuBenchmark()") != 2
        or main.count("cpuBenchmarkResultPrefix") != 1
        or main.count("exit(status);") != 2
        or ".ignore()" in main
        or "requireResidentReferenceChallenge(Platform.environment)" in main
    ):
        raise IosReferenceAppGateError(
            "committed iOS resident smoke entrypoint contract changed"
        )


def _validate_prepared_assets(directory: Path, archive: PinnedArchive) -> str:
    inventory = _COMMON._asset_pair(directory, "prepared Fonix asset directory")
    manifest_bytes = inventory["fonix-native-artifact-manifest.json"]
    manifest = _strict_json(
        manifest_bytes,
        f"prepared iOS {archive.variant} manifest",
        maximum=MAX_ASSET_BYTES,
    )
    if not isinstance(manifest, dict):
        raise IosReferenceAppGateError("prepared Fonix manifest must be an object")
    target = manifest.get("target")
    source = manifest.get("source")
    if not isinstance(target, dict) or not isinstance(source, dict):
        raise IosReferenceAppGateError("prepared Fonix manifest identity is invalid")
    if (
        manifest.get("schema") != 2
        or manifest.get("artifactId") != archive.artifact_id
        or target
        != {
            "os": "ios",
            "architecture": "arm64",
            "variant": archive.variant,
            "minimumOs": APPLICATION_MINIMUM_OS,
            "flavor": "cpu",
            "runtimeMode": "linked",
        }
        or source.get("sha256") != archive.sha256
        or source.get("sizeBytes") != archive.size_bytes
    ):
        raise IosReferenceAppGateError(
            f"prepared iOS {archive.variant} manifest does not bind the lock tuple"
        )
    notice = inventory["ThirdPartyNotices.txt"]
    if not notice or len(notice) > MAX_ASSET_BYTES:
        raise IosReferenceAppGateError("prepared third-party notices are invalid")
    return hashlib.sha256(manifest_bytes).hexdigest()


def _single_line(output: Any, label: str) -> str:
    lines = output.stdout.splitlines()
    if output.stderr or len(lines) != 1 or not lines[0]:
        raise IosReferenceAppGateError(f"{label} output changed")
    return lines[0]


def _verify_flutter(flutter: Path) -> dict[str, object]:
    output = _run(
        (str(flutter), "--version", "--machine"),
        operation="Flutter version check",
        timeout_seconds=ENVIRONMENT_CHECK_TIMEOUT_SECONDS,
        maximum_output=MAX_COMMAND_OUTPUT_BYTES,
    )
    value = _strict_json(
        output.stdout, "Flutter version output", maximum=MAX_COMMAND_OUTPUT_BYTES
    )
    if not isinstance(value, dict):
        raise IosReferenceAppGateError("Flutter version output must be an object")
    if value.get("frameworkRevision") != VALIDATED_FLUTTER_REVISION:
        raise IosReferenceAppGateError(
            "Flutter revision differs from the reference-app gate revision"
        )
    framework_version = value.get("frameworkVersion")
    if not isinstance(framework_version, str) or not framework_version:
        raise IosReferenceAppGateError("Flutter framework version is missing")
    if len(framework_version.encode("utf-8")) > 128:
        raise IosReferenceAppGateError("Flutter framework version is too long")
    return value


def _verify_apple_environment(flutter: Path) -> tuple[dict[str, object], AppleEnvironment]:
    flutter_version = _verify_flutter(flutter)
    if flutter_version.get("frameworkRevision") != VALIDATED_FLUTTER_REVISION:
        raise IosReferenceAppGateError("Flutter revision changed")

    xcode = _run(
        ("/usr/bin/xcodebuild", "-version"),
        operation="Xcode version check",
        timeout_seconds=ENVIRONMENT_CHECK_TIMEOUT_SECONDS,
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    if xcode.stderr or xcode.stdout.splitlines() != [
        f"Xcode {VALIDATED_XCODE_VERSION}",
        f"Build version {VALIDATED_XCODE_BUILD}",
    ]:
        raise IosReferenceAppGateError("Xcode version differs from the iOS gate tuple")
    developer = _single_line(
        _run(
            ("/usr/bin/xcode-select", "-p"),
            operation="Xcode selection check",
            timeout_seconds=ENVIRONMENT_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        ),
        "Xcode selection",
    )
    if developer != VALIDATED_DEVELOPER_DIRECTORY:
        raise IosReferenceAppGateError("selected Xcode developer directory changed")
    device_sdk = _single_line(
        _run(
            ("/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-version"),
            operation="iPhoneOS SDK check",
            timeout_seconds=ENVIRONMENT_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        ),
        "iPhoneOS SDK",
    )
    simulator_sdk = _single_line(
        _run(
            ("/usr/bin/xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"),
            operation="iPhoneSimulator SDK check",
            timeout_seconds=ENVIRONMENT_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        ),
        "iPhoneSimulator SDK",
    )
    if device_sdk != VALIDATED_IOS_SDK or simulator_sdk != VALIDATED_IOS_SDK:
        raise IosReferenceAppGateError("selected Apple SDK versions changed")
    macos_version = _single_line(
        _run(
            ("/usr/bin/sw_vers", "-productVersion"),
            operation="macOS version check",
            timeout_seconds=ENVIRONMENT_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        ),
        "macOS version",
    )
    macos_build = _single_line(
        _run(
            ("/usr/bin/sw_vers", "-buildVersion"),
            operation="macOS build check",
            timeout_seconds=ENVIRONMENT_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        ),
        "macOS build",
    )
    if (
        macos_version != VALIDATED_MACOS_VERSION
        or macos_build != VALIDATED_MACOS_BUILD
    ):
        raise IosReferenceAppGateError("macOS host identity changed")
    return flutter_version, AppleEnvironment(
        xcode_version=VALIDATED_XCODE_VERSION,
        xcode_build=VALIDATED_XCODE_BUILD,
        device_sdk=device_sdk,
        simulator_sdk=simulator_sdk,
        macos_version=macos_version,
        macos_build=macos_build,
    )


def _simulator_identity(udid: str) -> SimulatorIdentity:
    if _UDID.fullmatch(udid) is None:
        raise IosReferenceAppGateError("--simulator-udid must be a canonical UUID")
    output = _run(
        ("/usr/bin/xcrun", "simctl", "list", "devices", "available", "--json"),
        operation="available simulator inventory",
        timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
        maximum_output=MAX_SIMCTL_BYTES,
    )
    value = _strict_json(
        output.stdout, "available simulator inventory", maximum=MAX_SIMCTL_BYTES
    )
    if not isinstance(value, dict) or set(value) != {"devices"}:
        raise IosReferenceAppGateError("simulator inventory has an unexpected shape")
    devices = value.get("devices")
    if not isinstance(devices, dict):
        raise IosReferenceAppGateError("simulator inventory devices must be an object")
    matches: list[tuple[str, dict[str, Any]]] = []
    for runtime, raw_devices in devices.items():
        if not isinstance(runtime, str) or not isinstance(raw_devices, list):
            raise IosReferenceAppGateError("simulator inventory entry is invalid")
        for raw_device in raw_devices:
            if not isinstance(raw_device, dict):
                raise IosReferenceAppGateError("simulator device entry is invalid")
            if raw_device.get("udid") == udid:
                matches.append((runtime, raw_device))
    if len(matches) != 1:
        raise IosReferenceAppGateError(
            "selected simulator must occur exactly once in the available inventory"
        )
    runtime, device = matches[0]
    name = _string(device.get("name"), "selected simulator name")
    state = device.get("state")
    if (
        runtime != VALIDATED_SIMULATOR_RUNTIME
        or state not in {"Shutdown", "Booted"}
        or device.get("isAvailable", True) is not True
    ):
        raise IosReferenceAppGateError(
            "selected simulator does not match the validated runtime/state tuple"
        )
    return SimulatorIdentity(
        udid=udid,
        udid_sha256=hashlib.sha256(udid.encode("ascii")).hexdigest(),
        name=name,
        runtime=runtime,
        initial_state=state,
    )


def _validate_challenge(challenge: object) -> str:
    if not isinstance(challenge, str) or _SHA256.fullmatch(challenge) is None:
        raise IosReferenceAppGateError(
            "reference challenge must be exactly 64 lowercase hexadecimal characters"
        )
    return challenge


def _reference_environment(
    challenge: str, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    challenge = _validate_challenge(challenge)
    environment = _tool_environment(base)
    environment["SIMCTL_CHILD_FONIX_REFERENCE_SMOKE"] = "1"
    environment["SIMCTL_CHILD_FONIX_REFERENCE_CHALLENGE"] = challenge
    return environment


def _process_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IosReferenceAppGateError("reference processId must be an integer")
    if value <= 0 or value > 2_147_483_647:
        raise IosReferenceAppGateError("reference processId is outside its bound")
    return value


def _expected_reference_receipt(
    archive: PinnedArchive, challenge: str, process_id: int
) -> dict[str, object]:
    challenge = _validate_challenge(challenge)
    process_id = _process_id(process_id)
    return {
        "schemaVersion": 1,
        "status": "passed",
        "runtimeVersion": ARTIFACT_VERSION,
        "runtimeSource": "linked",
        "runtimeOwner": "wrapper",
        "artifactFlavor": "cpu",
        "platform": "ios",
        "architecture": "arm64",
        "shimBuildId": archive.artifact_id,
        "artifactSha256": archive.sha256,
        "modelSha256": MODEL_SHA256,
        "outputValues": [1, 4, 9, 16, 25, 36],
        "activeProviders": ["cpu"],
        "fullCpuAssignment": True,
        "doubleClose": "passed",
        "challenge": challenge,
        "processId": process_id,
    }


def _validate_reference_receipt(
    value: Any, archive: PinnedArchive, challenge: str, process_id: int
) -> dict[str, object]:
    expected = _expected_reference_receipt(archive, challenge, process_id)
    if not isinstance(value, dict) or set(value) != set(expected):
        raise IosReferenceAppGateError("iOS reference receipt has an unexpected shape")
    for key, expected_value in expected.items():
        actual = value[key]
        if type(actual) is not type(expected_value) or actual != expected_value:
            raise IosReferenceAppGateError(
                f"iOS reference receipt field {key!r} is unexpected"
            )
    return {key: value[key] for key in expected}


def _parse_reference_output(
    output: str, archive: PinnedArchive, challenge: str, process_id: int
) -> dict[str, object]:
    if len(output.encode("utf-8")) > MAX_REFERENCE_OUTPUT_BYTES:
        raise IosReferenceAppGateError("iOS reference output exceeds its bound")
    if "\r" in output or "\x00" in output or "\n" in output:
        raise IosReferenceAppGateError(
            "iOS reference receipt must be exactly one canonical line"
        )
    receipt_count = output.count(REFERENCE_RECEIPT_PREFIX)
    failure_count = output.count(REFERENCE_FAILURE_PREFIX)
    if receipt_count > 1 or failure_count > 1:
        raise IosReferenceAppGateError("iOS reference output has an ambiguous marker")
    if output.startswith(REFERENCE_FAILURE_PREFIX):
        if receipt_count != 0 or failure_count != 1:
            raise IosReferenceAppGateError("iOS reference failure marker is ambiguous")
        failure_payload = output[len(REFERENCE_FAILURE_PREFIX) :]
        failure = _strict_json(
            failure_payload, "iOS reference failure", maximum=16 * 1024
        )
        if (
            not isinstance(failure, dict)
            or set(failure) != REFERENCE_FAILURE_KEYS
            or failure.get("schemaVersion") != 1
            or failure.get("status") != "failed"
            or not isinstance(failure.get("errorType"), str)
            or _CLOSED_TOKEN.fullmatch(failure["errorType"]) is None
            or failure.get("challenge") != _validate_challenge(challenge)
            or failure.get("processId") != _process_id(process_id)
        ):
            raise IosReferenceAppGateError(
                "iOS reference application emitted an invalid failure receipt"
            )
        canonical_failure = {
            "schemaVersion": 1,
            "status": "failed",
            "errorType": failure["errorType"],
            "challenge": challenge,
            "processId": process_id,
        }
        expected_line = REFERENCE_FAILURE_PREFIX + json.dumps(
            canonical_failure, separators=(",", ":")
        )
        if output != expected_line:
            raise IosReferenceAppGateError(
                "iOS reference failure receipt is not canonical JSON"
            )
        raise IosReferenceAppGateError(
            f"iOS reference application failed ({failure['errorType']})"
        )
    if not output.startswith(REFERENCE_RECEIPT_PREFIX):
        raise IosReferenceAppGateError(
            "iOS reference application must emit one exact leading marker"
        )
    if receipt_count != 1 or failure_count != 0:
        raise IosReferenceAppGateError("iOS reference receipt marker is ambiguous")
    receipt_payload = output[len(REFERENCE_RECEIPT_PREFIX) :]
    receipt = _strict_json(
        receipt_payload, "iOS reference receipt", maximum=16 * 1024
    )
    validated = _validate_reference_receipt(
        receipt, archive, challenge, process_id
    )
    expected_line = REFERENCE_RECEIPT_PREFIX + json.dumps(
        _expected_reference_receipt(archive, challenge, process_id),
        separators=(",", ":"),
    )
    if output != expected_line:
        raise IosReferenceAppGateError(
            "iOS reference receipt is not canonical JSON"
        )
    return validated


def _application_identity(application: Path) -> tuple[Path, str]:
    application = _directory(application, "final iOS reference application")
    plist_path = _regular_file(
        application / "Info.plist",
        "final iOS reference application Info.plist",
        maximum=1024 * 1024,
    )
    try:
        value = plistlib.loads(plist_path.read_bytes())
    except (plistlib.InvalidFileException, ValueError) as error:
        raise IosReferenceAppGateError("final iOS Info.plist is invalid") from error
    if not isinstance(value, dict):
        raise IosReferenceAppGateError("final iOS Info.plist must be a dictionary")
    if (
        value.get("CFBundleExecutable") != APPLICATION_EXECUTABLE_NAME
        or value.get("CFBundleIdentifier") != APPLICATION_BUNDLE_IDENTIFIER
        or value.get("MinimumOSVersion") != APPLICATION_MINIMUM_OS
    ):
        raise IosReferenceAppGateError("final iOS application identity changed")
    return _regular_file(
        application / APPLICATION_EXECUTABLE_NAME,
        "final iOS reference executable",
    ), APPLICATION_BUNDLE_IDENTIFIER


def _expected_macho_paths(application: Path) -> list[str]:
    return [str(application / relative) for relative in _IOS_MACHO_RELATIVE_PATHS]


def _unique_string_list(
    value: object, label: str, *, maximum: int = 512
) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise IosReferenceAppGateError(f"{label} is not a closed unique string list")
    return value


def _closed_string_list(value: object, label: str, *, maximum: int = 512) -> list[str]:
    strings = _unique_string_list(value, label, maximum=maximum)
    if strings != sorted(strings):
        raise IosReferenceAppGateError(f"{label} is not a closed sorted string list")
    return strings


def _validate_build_manifest_binding(
    value: object, archive: PinnedArchive
) -> dict[str, Any]:
    manifest = _exact_keys(
        value,
        {
            "schemaVersion",
            "nativeIdentity",
            "shimAbiVersion",
            "requiredOrtApiVersion",
            "runtimeProfile",
            "androidRuntimeOwner",
            "allowedRuntimeSources",
            "buildId",
            "artifact",
        },
        "final Apple build manifest",
    )
    artifact = _exact_keys(
        manifest["artifact"],
        {
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
        },
        "final Apple build-manifest artifact",
    )
    if (
        manifest["schemaVersion"] != 3
        or manifest["nativeIdentity"] != "fonix_shim"
        or manifest["shimAbiVersion"] != 1
        or manifest["requiredOrtApiVersion"] != 27
        or manifest["runtimeProfile"] != "linked"
        or manifest["androidRuntimeOwner"] is not None
        or manifest["allowedRuntimeSources"] != ["linked"]
        or manifest["buildId"] != archive.artifact_id
        or artifact["id"] != archive.artifact_id
        or artifact["targetOs"] != "ios"
        or artifact["targetArchitecture"] != "arm64"
        or artifact["targetVariant"] != archive.variant
        or artifact["minimumOs"] != APPLICATION_MINIMUM_OS
        or artifact["flavor"] != "cpu"
        or artifact["runtimeMode"] != "linked"
        or _SHA256.fullmatch(str(artifact["lockSha256"])) is None
        or artifact["sourceSha256"] != archive.sha256
        or _SHA256.fullmatch(str(artifact["thirdPartyNoticesSha256"])) is None
        or not isinstance(artifact["providers"], list)
        or not artifact["providers"]
    ):
        raise IosReferenceAppGateError(
            "final Apple build manifest does not bind the arm64 linked tuple"
        )
    return manifest


def _expected_hook_invocation_metadata(
    reference_shim: ReferenceShim,
) -> dict[str, str]:
    path = reference_shim.path
    invocation_id = reference_shim.invocation_hash
    if not path.is_absolute() or _HOOK_INVOCATION.fullmatch(invocation_id) is None:
        raise IosReferenceAppGateError(
            "resolver-built reference shim identity is invalid"
        )
    try:
        work_directory = path.parents[6]
    except IndexError as error:
        raise IosReferenceAppGateError(
            "resolver-built reference shim path is outside the hook tree"
        ) from error
    expected_reference = (
        work_directory
        / ".dart_tool/hooks_runner/shared/fonix/build"
        / invocation_id
        / "libfonix_shim.dylib"
    )
    if path != expected_reference:
        raise IosReferenceAppGateError(
            "resolver-built reference shim path is outside the hook tree"
        )
    invocation_directory = (
        work_directory / ".dart_tool/hooks_runner/fonix" / invocation_id
    )
    return {
        "invocationId": invocation_id,
        "input": str(invocation_directory / "input.json"),
        "output": str(invocation_directory / "output.json"),
        "status": "validated",
    }


def _validate_dylib_dependencies(
    value: object,
    *,
    dynamic_dependencies: list[str],
    record_index: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 512:
        raise IosReferenceAppGateError("Mach-O dylib dependency records are invalid")
    paths: list[str] = []
    for dependency_index, raw_dependency in enumerate(value):
        dependency = _exact_keys(
            raw_dependency,
            _IOS_DYLIB_DEPENDENCY_KEYS,
            (
                f"final iOS Mach-O record {record_index} dylib dependency "
                f"{dependency_index}"
            ),
        )
        if dependency["kind"] not in _IOS_DYLIB_DEPENDENCY_KINDS:
            raise IosReferenceAppGateError("Mach-O dylib dependency kind is invalid")
        paths.append(
            _string(dependency["path"], "Mach-O dylib dependency path")
        )
        _nonnegative_integer(
            dependency["currentVersion"],
            "Mach-O dylib dependency current version",
            0xFFFFFFFF,
        )
        _nonnegative_integer(
            dependency["compatibilityVersion"],
            "Mach-O dylib dependency compatibility version",
            0xFFFFFFFF,
        )
    if len(paths) != len(set(paths)) or set(paths) != set(dynamic_dependencies):
        raise IosReferenceAppGateError(
            "Mach-O dylib dependency records do not bind the dependency paths"
        )
    return value


def _validate_dylib_id(
    value: object,
    *,
    expected: dict[str, object] | None,
    record_index: int,
) -> None:
    if expected is None:
        if value is not None:
            raise IosReferenceAppGateError("Mach-O executable has a dylib ID")
        return
    identity = _exact_keys(
        value,
        _IOS_DYLIB_ID_KEYS,
        f"final iOS Mach-O record {record_index} dylib ID",
    )
    _string(identity["path"], "Mach-O dylib ID path")
    for field in ("timestamp", "currentVersion", "compatibilityVersion"):
        _nonnegative_integer(
            identity[field],
            f"Mach-O dylib ID {field}",
            0xFFFFFFFF,
        )
    if identity != expected:
        raise IosReferenceAppGateError("Mach-O dylib ID changed")


def _validate_native_inventory(
    value: object,
    *,
    archive: PinnedArchive,
    reference_shim: ReferenceShim,
) -> dict[str, Any]:
    inventory = _exact_keys(
        value,
        {
            "profile",
            "frameworks",
            "machOBinaries",
            "shimExports",
            "linkedRuntimeIdentity",
        },
        "final iOS native inventory",
    )
    expected_profile = (
        "ios-device-release"
        if archive.variant == "device"
        else "ios-simulator-debug-no-debug-dylib"
    )
    if (
        inventory["profile"] != expected_profile
        or inventory["frameworks"] != _IOS_FRAMEWORK_INVENTORY
    ):
        raise IosReferenceAppGateError("final iOS native profile/framework inventory changed")

    records = inventory["machOBinaries"]
    if not isinstance(records, list) or len(records) != len(_IOS_MACHO_RELATIVE_PATHS):
        raise IosReferenceAppGateError("final iOS Mach-O inventory must contain four records")
    expected_owners = {
        "Runner": "application",
        "Frameworks/App.framework/App": "Frameworks/App.framework",
        "Frameworks/Flutter.framework/Flutter": "Frameworks/Flutter.framework",
        "Frameworks/fonix_shim.framework/fonix_shim": "Frameworks/fonix_shim.framework",
    }
    expected_platform = 2 if archive.variant == "device" else 7
    for index, expected_path in enumerate(_IOS_MACHO_RELATIVE_PATHS):
        record = _exact_keys(
            records[index],
            _IOS_MACHO_RECORD_KEYS,
            f"final iOS Mach-O record {index}",
        )
        expected_signatures = (
            0
            if archive.variant == "device" and expected_path == "Runner"
            else 1
        )
        if (
            record["path"] != expected_path
            or record["owner"] != expected_owners[expected_path]
            or record["architecture"] != "arm64"
            or record["machoPlatform"] != expected_platform
            or record["minimumOs"] != _IOS_MACHO_MINIMUM_OS[expected_path]
            or type(record["cpuSubtype"]) is not int
            or record["cpuSubtype"] != 0
            or record["fileType"] != _IOS_MACHO_FILE_TYPES[expected_path]
            or record["headerFlags"]
            != _IOS_MACHO_HEADER_FLAGS[archive.variant][expected_path]
            or type(record["codeSignatureLoadCommands"]) is not int
            or record["codeSignatureLoadCommands"] != expected_signatures
        ):
            raise IosReferenceAppGateError(
                "final iOS Mach-O record does not bind the exact arm64 profile"
            )
        _validate_dylib_id(
            record["dylibId"],
            expected=_IOS_MACHO_DYLIB_IDS[archive.variant][expected_path],
            record_index=index,
        )
        dynamic_dependencies = _closed_string_list(
            record["dynamicDependencies"], "Mach-O dependencies"
        )
        dylib_dependencies = _validate_dylib_dependencies(
            record["dylibDependencies"],
            dynamic_dependencies=dynamic_dependencies,
            record_index=index,
        )
        if (
            record["dependencyMetadataSha256"]
            != _canonical_json_sha256(dylib_dependencies)
            or _SHA256.fullmatch(str(record["loadCommandKindsSha256"])) is None
        ):
            raise IosReferenceAppGateError(
                "final iOS Mach-O load-command evidence changed"
            )
        rpaths = _unique_string_list(record["rpaths"], "Mach-O RPATHs")
        if rpaths != _IOS_MACHO_RPATHS[expected_path]:
            raise IosReferenceAppGateError("final iOS Mach-O RPATH order changed")

    exports = _exact_keys(
        inventory["shimExports"],
        {
            "allowlist",
            "allowlistSha256",
            "symbolCount",
            "nlistSymbolSetSha256",
            "dyldExportSetSha256",
        },
        "final iOS shim exports",
    )
    if (
        exports["allowlist"] != "src/fonix_exports.apple"
        or _SHA256.fullmatch(str(exports["allowlistSha256"])) is None
        or _SHA256.fullmatch(str(exports["nlistSymbolSetSha256"])) is None
        or exports["dyldExportSetSha256"] != exports["nlistSymbolSetSha256"]
    ):
        raise IosReferenceAppGateError("final iOS shim export evidence changed")
    _positive_integer(exports["symbolCount"], "shim export count", 256)

    linked = _exact_keys(
        inventory["linkedRuntimeIdentity"],
        {
            "runtimeMode",
            "packagedShim",
            "referenceShim",
            "hookInvocationMetadata",
            "normalizedRuntimeFields",
            "normalizedRuntimeFieldsSha256",
            "comparisonScope",
            "accountedTransformations",
            "embeddedBuildIdentity",
            "nlistAndDyldExports",
            "separatelyPackagedOrtMachOs",
            "auditedOrtLoadCommandDependencies",
            "runtimeDlopenBehavior",
            "otherMachOStaticOrtCopies",
            "staticArchiveMultiplicity",
            "claimBoundary",
        },
        "final iOS linked-runtime identity",
    )
    hook_metadata = _exact_keys(
        linked["hookInvocationMetadata"],
        {"invocationId", "input", "output", "status"},
        "final iOS hook invocation metadata",
    )
    embedded = _exact_keys(
        linked["embeddedBuildIdentity"],
        {"schemaVersion", "artifactId", "buildId", "status"},
        "final iOS embedded build identity",
    )
    if (
        linked["runtimeMode"] != "linked"
        or linked["packagedShim"]
        != "Frameworks/fonix_shim.framework/fonix_shim"
        or linked["referenceShim"] != str(reference_shim.path)
        or hook_metadata != _expected_hook_invocation_metadata(reference_shim)
        or linked["normalizedRuntimeFields"] != "matched"
        or linked["normalizedRuntimeFieldsSha256"]
        != _IOS_NORMALIZED_RUNTIME_FIELDS_SHA256[archive.variant]
        or linked["comparisonScope"] != _IOS_NORMALIZED_COMPARISON_SCOPE
        or linked["accountedTransformations"]
        != _IOS_ACCOUNTED_TRANSFORMATIONS[archive.variant]
        or embedded
        != {
            "schemaVersion": 3,
            "artifactId": archive.artifact_id,
            "buildId": archive.artifact_id,
            "status": "matched",
        }
        or linked["nlistAndDyldExports"] != "matched"
        or linked["separatelyPackagedOrtMachOs"] != []
        or linked["auditedOrtLoadCommandDependencies"] != []
        or linked["runtimeDlopenBehavior"] != "not-proved"
        or linked["otherMachOStaticOrtCopies"] != "not-proved"
        or linked["staticArchiveMultiplicity"]
        != "not-provable-from-final-bundle"
        or linked["claimBoundary"] != _IOS_REFERENCE_SHIM_CLAIM_BOUNDARY
    ):
        raise IosReferenceAppGateError("final iOS linked-runtime identity changed")
    return inventory


def _validate_audit_binding(
    value: Any,
    *,
    application: Path,
    archive: PinnedArchive,
    unsigned_device: bool,
    reference_shim: ReferenceShim,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IosReferenceAppGateError("final Apple audit report must be an object")
    platform_name = "ios-device" if archive.variant == "device" else "ios-simulator"
    if (
        value.get("platform") != platform_name
        or value.get("application") != str(application)
        or value.get("artifactId") != archive.artifact_id
        or value.get("lockedMinimumOs") != APPLICATION_MINIMUM_OS
        or value.get("declaredMinimumOs") != APPLICATION_MINIMUM_OS
        or value.get("plistMinimumOs") != APPLICATION_MINIMUM_OS
        or value.get("machOBinaries") != _expected_macho_paths(application)
    ):
        raise IosReferenceAppGateError(
            f"final Apple audit did not bind the iOS {archive.variant} app"
        )
    _validate_build_manifest_binding(value.get("buildManifest"), archive)
    native_inventory = _validate_native_inventory(
        value.get("nativeInventory"),
        archive=archive,
        reference_shim=reference_shim,
    )
    if unsigned_device:
        expected_framework_signatures = [
            {
                "path": "Frameworks/App.framework",
                "binary": "Frameworks/App.framework/App",
                "status": "verified-adhoc",
                "teamIdentifier": None,
                "authorities": [],
            },
            {
                "path": "Frameworks/Flutter.framework",
                "binary": "Frameworks/Flutter.framework/Flutter",
                "status": "verified-adhoc",
                "teamIdentifier": None,
                "authorities": [],
            },
            {
                "path": "Frameworks/fonix_shim.framework",
                "binary": "Frameworks/fonix_shim.framework/fonix_shim",
                "status": "verified-adhoc",
                "teamIdentifier": None,
                "authorities": [],
            },
        ]
        expected_signature_details = {
            "rootBundle": "unsigned",
            "rootExecutable": "unsigned",
            "provisioningProfile": "absent",
            "nestedFrameworks": expected_framework_signatures,
        }
        if (
            value.get("signaturePolicy") != "ios-device-unsigned-development"
            or value.get("signatureStatus") != "unsigned"
            or value.get("claimStatus") != "static-only"
            or value.get("signatureDetails") != expected_signature_details
        ):
            raise IosReferenceAppGateError(
                "device audit did not preserve the unsigned static-only policy"
            )
    else:
        expected_verified = sorted(
            [*_IOS_MACHO_RELATIVE_PATHS, *(item["path"] for item in _IOS_FRAMEWORK_INVENTORY)]
        )
        if (
            value.get("signaturePolicy") != "strict"
            or value.get("signatureStatus") != "verified"
            or value.get("claimStatus") != "signature-verified-package-audit"
            or value.get("signatureDetails")
            != {
                "rootBundle": "verified",
                "rootExecutable": "verified",
                "verifiedCodeObjects": expected_verified,
            }
        ):
            raise IosReferenceAppGateError(
                "simulator audit did not preserve strict signature verification"
            )
    return value


def _load_apple_auditor(repository: Path, variant: str) -> Any:
    audit_path = _regular_file(
        repository / "tool/ci/audit_apple_application.py",
        "frozen Apple application auditor",
        maximum=MAX_COMMAND_OUTPUT_BYTES,
    )
    specification = importlib.util.spec_from_file_location(
        f"_fonix_ios_reference_gate_apple_audit_{variant}", audit_path
    )
    if specification is None or specification.loader is None:  # pragma: no cover
        raise IosReferenceAppGateError("could not load the frozen Apple auditor")
    audit_module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(audit_module)
    return audit_module


def _audit_application(
    *,
    repository: Path,
    application: Path,
    archive: PinnedArchive,
    reference_shim: ReferenceShim,
) -> dict[str, Any]:
    platform_name = "ios-device" if archive.variant == "device" else "ios-simulator"
    signature_policy = (
        "ios-device-unsigned-development"
        if archive.variant == "device"
        else "strict"
    )
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        audit_module = _load_apple_auditor(repository, archive.variant)
        try:
            raw_value = audit_module.audit_application_and_optional_probe(
                application.resolve(strict=True),
                platform_name,
                APPLICATION_MINIMUM_OS,
                repository=repository.resolve(strict=True),
                reference_shim=reference_shim.path,
                signature_policy=signature_policy,
            )
        except audit_module.AppleApplicationAuditError as error:
            raise IosReferenceAppGateError(
                f"final iOS {archive.variant} application audit failed: {error}"
            ) from error
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
    value = _strict_json(
        json.dumps(raw_value, sort_keys=True) + "\n",
        f"final iOS {archive.variant} application audit report",
        maximum=MAX_COMMAND_OUTPUT_BYTES,
    )
    if (
        _sha256_file(reference_shim.path, "resolver-built reference shim")
        != reference_shim.sha256
    ):
        raise IosReferenceAppGateError(
            "resolver-built reference shim changed during final-app audit"
        )
    return _validate_audit_binding(
        value,
        application=application,
        archive=archive,
        unsigned_device=archive.variant == "device",
        reference_shim=reference_shim,
    )


def _audit_stable_application(
    *,
    repository: Path,
    application: Path,
    archive: PinnedArchive,
    reference_shim: ReferenceShim,
) -> tuple[dict[str, Any], TreeIdentity]:
    before = _tree_identity(application, f"iOS {archive.variant} audited application")
    _application_identity(application)
    audit = _audit_application(
        repository=repository,
        application=application,
        archive=archive,
        reference_shim=reference_shim,
    )
    after = _tree_identity(application, f"iOS {archive.variant} audited application")
    if after != before:
        raise IosReferenceAppGateError(
            f"iOS {archive.variant} application changed during final audit"
        )
    return audit, before


def _prepare_variant(
    *,
    repository: Path,
    source_example: Path,
    destination: Path,
    flutter: Path,
    dart: Path,
    artifact_cache: Path,
    archive: PinnedArchive,
    host_archive: Any,
    run_source_checks: bool,
) -> tuple[dict[str, int], str]:
    summary = _COMMON._copy_example(source_example, destination)
    if summary.file_count == 0:
        raise IosReferenceAppGateError("committed example copy was empty")
    pubspec = destination / "pubspec.yaml"
    _COMMON._patch_fonix_path_dependency(pubspec, repository)
    _require_ios_source_contract(destination)
    relative_archive = _copy_archive(artifact_cache, destination, archive)
    # Every Dart command activates the package build hook on this macOS host,
    # including the explicit iOS asset-preparation executable. Keep the exact
    # host tuple beside the selected iOS archive in both clean variant copies.
    _copy_cache_member(
        source_cache=artifact_cache,
        destination_directory=relative_archive.parent,
        basename=host_archive.basename,
        expected_sha256=host_archive.sha256,
        expected_size=host_archive.size_bytes,
        label="selected macOS host archive",
    )
    _run(
        (str(flutter), "pub", "get", "--offline"),
        cwd=destination,
        operation=f"offline Flutter pub get ({archive.variant})",
        timeout_seconds=DEPENDENCY_RESOLUTION_TIMEOUT_SECONDS,
        maximum_output=MAX_COMMAND_OUTPUT_BYTES,
    )
    if run_source_checks:
        _run(
            (str(flutter), "analyze", "--no-pub"),
            cwd=destination,
            operation="iOS reference Flutter analysis",
            timeout_seconds=SOURCE_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_COMMAND_OUTPUT_BYTES,
        )
        _run(
            (str(flutter), "test", "--no-pub"),
            cwd=destination,
            operation="iOS reference Flutter tests",
            timeout_seconds=SOURCE_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_COMMAND_OUTPUT_BYTES,
        )
    _run(
        (
            str(dart),
            "run",
            "fonix:fonix_prepare_flutter_assets",
            "--target-os",
            "ios",
            "--architecture",
            "arm64",
            "--variant",
            archive.variant,
            "--package-root",
            str(repository),
            "--cache",
            str(relative_archive.parent),
            "--output",
            str(destination / "assets/fonix"),
        ),
        cwd=destination,
        operation=f"Fonix iOS {archive.variant} asset preparation",
        timeout_seconds=ASSET_PREPARATION_TIMEOUT_SECONDS,
        maximum_output=MAX_COMMAND_OUTPUT_BYTES,
    )
    manifest_sha256 = _validate_prepared_assets(
        destination / "assets/fonix", archive
    )
    # Host-side analysis/tests must retain the bundled macOS composition. Only
    # the subsequent iOS build consumes linked mode and the iOS deployment floor.
    _patch_ios_hook_config(pubspec)
    return {
        "fileCount": summary.file_count,
        "byteCount": summary.byte_count,
    }, manifest_sha256


def _exact_keys(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise IosReferenceAppGateError(f"{label} has an unexpected shape")
    return value


def _resolver_path(value: object, expected: Path, label: str, *, slash: bool = False) -> None:
    actual = _string(value, label)
    expected_text = str(expected) + ("/" if slash else "")
    if actual != expected_text:
        raise IosReferenceAppGateError(f"{label} does not match the frozen build tuple")


def _resolve_reference_shim(
    *,
    work_directory: Path,
    repository: Path,
    archive: PinnedArchive,
) -> ReferenceShim:
    invocation_root = _directory(
        work_directory / ".dart_tool/hooks_runner/fonix",
        f"iOS {archive.variant} hook invocation root",
    )
    invocation_directories: list[Path] = []
    for child in sorted(invocation_root.iterdir(), key=lambda path: path.name):
        if _HOOK_INVOCATION.fullmatch(child.name) is None:
            continue
        if child.is_symlink() or not child.is_dir():
            raise IosReferenceAppGateError(
                "iOS hook invocation must be a non-symlink directory"
            )
        invocation_directories.append(child)
        if len(invocation_directories) > MAX_HOOK_INVOCATIONS:
            raise IosReferenceAppGateError("iOS hook invocation inventory is oversized")

    expected_sdk = "iphoneos" if archive.variant == "device" else "iphonesimulator"
    expected_linking = archive.variant == "device"
    selected: list[ReferenceShim] = []
    for invocation in invocation_directories:
        input_path = _regular_file(
            invocation / "input.json",
            "iOS hook input",
            maximum=MAX_HOOK_JSON_BYTES,
        )
        input_value = _exact_keys(
            _strict_json(
                input_path.read_bytes(),
                "iOS hook input",
                maximum=MAX_HOOK_JSON_BYTES,
            ),
            {
                "assets",
                "config",
                "out_dir_shared",
                "out_file",
                "package_name",
                "package_root",
                "user_defines",
            },
            "iOS hook input",
        )
        if input_value["assets"] != {} or input_value["package_name"] != "fonix":
            raise IosReferenceAppGateError("iOS hook input package identity changed")
        config = _exact_keys(
            input_value["config"],
            {"build_asset_types", "extensions", "linking_enabled"},
            "iOS hook config",
        )
        extensions = _exact_keys(
            config["extensions"], {"code_assets"}, "iOS hook extensions"
        )
        code_assets = extensions["code_assets"]
        if not isinstance(code_assets, dict):
            raise IosReferenceAppGateError("iOS hook code-assets config is invalid")
        if code_assets.get("target_os") != "ios":
            continue
        code_assets = _exact_keys(
            code_assets,
            {
                "c_compiler",
                "ios",
                "link_mode_preference",
                "target_architecture",
                "target_os",
            },
            "iOS hook code-assets config",
        )
        compiler = _exact_keys(
            code_assets["c_compiler"], {"ar", "cc", "ld"}, "iOS hook compiler"
        )
        if any(
            not Path(
                _string(compiler[key], f"iOS hook compiler {key}")
            ).is_absolute()
            for key in compiler
        ):
            raise IosReferenceAppGateError("iOS hook compiler paths must be absolute")
        ios = _exact_keys(
            code_assets["ios"], {"target_sdk", "target_version"}, "iOS hook target"
        )
        tuple_matches = (
            config["build_asset_types"] == ["code_assets/code"]
            and config["linking_enabled"] is expected_linking
            and code_assets["target_os"] == "ios"
            and code_assets["target_architecture"] == "arm64"
            and code_assets["link_mode_preference"] == "dynamic"
            and ios["target_sdk"] == expected_sdk
            and _positive_integer(
                ios["target_version"], "iOS hook target version", 999
            )
            == 13
        )
        workspace = _exact_keys(
            _exact_keys(
                input_value["user_defines"],
                {"workspace_pubspec"},
                "iOS hook user defines",
            )["workspace_pubspec"],
            {"base_path", "defines"},
            "iOS hook workspace pubspec",
        )
        defines = _exact_keys(
            workspace["defines"],
            {"runtime_mode", "artifact_cache", "application_minimum_os"},
            "iOS hook workspace defines",
        )
        if defines != {
            "runtime_mode": "linked",
            "artifact_cache": ".fonix-artifact-cache",
            "application_minimum_os": APPLICATION_MINIMUM_OS,
        }:
            raise IosReferenceAppGateError("iOS hook user defines changed")
        _resolver_path(
            workspace["base_path"], work_directory / "pubspec.yaml", "iOS hook pubspec"
        )
        _resolver_path(
            input_value["package_root"], repository, "iOS hook package root", slash=True
        )
        output_path = invocation / "output.json"
        _resolver_path(input_value["out_file"], output_path, "iOS hook output")
        shared_root = work_directory / ".dart_tool/hooks_runner/shared/fonix/build"
        _resolver_path(
            input_value["out_dir_shared"], shared_root, "iOS hook shared output", slash=True
        )

        output_value = _exact_keys(
            _strict_json(
                _regular_file(
                    output_path, "iOS hook output", maximum=MAX_HOOK_JSON_BYTES
                ).read_bytes(),
                "iOS hook output",
                maximum=MAX_HOOK_JSON_BYTES,
            ),
            {"assets", "assets_for_linking", "dependencies", "status", "timestamp"},
            "iOS hook output",
        )
        dependencies = output_value["dependencies"]
        if (
            output_value["status"] != "success"
            or output_value["assets_for_linking"] != {}
            or not isinstance(dependencies, list)
            or not dependencies
            or len(dependencies) > 4096
            or any(not isinstance(item, str) or not item for item in dependencies)
            or not isinstance(output_value["timestamp"], str)
            or not output_value["timestamp"]
        ):
            raise IosReferenceAppGateError("iOS hook output status is invalid")
        for index, dependency in enumerate(dependencies):
            _string(dependency, f"iOS hook dependency {index}")
        _string(output_value["timestamp"], "iOS hook output timestamp")
        assets = output_value["assets"]
        if not isinstance(assets, list) or len(assets) != 1:
            raise IosReferenceAppGateError("iOS hook output must contain one code asset")
        asset = _exact_keys(assets[0], {"encoding", "type"}, "iOS hook code asset")
        encoding = _exact_keys(
            asset["encoding"], {"file", "id", "link_mode"}, "iOS hook asset encoding"
        )
        link_mode = _exact_keys(
            encoding["link_mode"], {"type"}, "iOS hook asset link mode"
        )
        if (
            asset["type"] != "code_assets/code"
            or encoding["id"] != "package:fonix/fonix_shim"
            or link_mode != {"type": "dynamic_loading_bundle"}
        ):
            raise IosReferenceAppGateError("iOS hook code-asset identity changed")
        shim_path = shared_root / invocation.name / "libfonix_shim.dylib"
        _resolver_path(encoding["file"], shim_path, "iOS hook shim output")
        shim_path = _regular_file(
            shim_path, "resolver-built reference shim", maximum=MAX_APPLICATION_BYTES
        )
        if tuple_matches:
            selected.append(
                ReferenceShim(
                    path=shim_path,
                    sha256=_sha256_file(
                        shim_path, "resolver-built reference shim"
                    ),
                    invocation_hash=invocation.name,
                )
            )
    if len(selected) != 1:
        raise IosReferenceAppGateError(
            f"iOS {archive.variant} build must resolve exactly one matching hook shim"
        )
    return selected[0]


def _simulator_apps(identity: SimulatorIdentity) -> dict[str, Any]:
    output = _run(
        ("/usr/bin/xcrun", "simctl", "listapps", identity.udid),
        operation="simulator installed-application inventory",
        timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
        maximum_output=MAX_SIMCTL_BYTES,
    )
    if output.stderr:
        raise IosReferenceAppGateError(
            "simulator installed-application inventory emitted stderr"
        )
    converted = _run_with_input(
        ("/usr/bin/plutil", "-convert", "json", "-o", "-", "--", "-"),
        input_bytes=output.stdout.encode("utf-8"),
        maximum_input=MAX_SIMCTL_BYTES,
        maximum_output=MAX_SIMCTL_BYTES,
        timeout_seconds=PLUTIL_TIMEOUT_SECONDS,
        operation="simulator application plist conversion",
    )
    if converted.stderr:
        raise IosReferenceAppGateError(
            "simulator application plist conversion emitted stderr"
        )
    value = _strict_json(
        converted.stdout,
        "simulator installed-application inventory",
        maximum=MAX_SIMCTL_BYTES,
    )
    if not isinstance(value, dict):
        raise IosReferenceAppGateError(
            "simulator installed-application inventory must be an object"
        )
    return value


def _require_package_absent(identity: SimulatorIdentity) -> None:
    if APPLICATION_BUNDLE_IDENTIFIER in _simulator_apps(identity):
        raise IosReferenceAppGateError(
            "Fonix reference app unexpectedly remains installed on the simulator"
        )


def _require_package_present(identity: SimulatorIdentity) -> None:
    if APPLICATION_BUNDLE_IDENTIFIER not in _simulator_apps(identity):
        raise IosReferenceAppGateError(
            "Fonix reference app is absent after the simulator receipt"
        )


def _verify_simulator_arm64(identity: SimulatorIdentity) -> dict[str, object]:
    capability = _single_line(
        _run(
            (
                "/usr/bin/xcrun",
                "simctl",
                "spawn",
                identity.udid,
                "/usr/sbin/sysctl",
                "-n",
                "hw.optional.arm64",
            ),
            operation="simulator arm64 capability check",
            timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        ),
        "simulator arm64 capability",
    )
    if capability != "1":
        raise IosReferenceAppGateError(
            "selected simulator does not advertise arm64 process capability"
        )
    process_architecture = _single_line(
        _run(
            (
                "/usr/bin/xcrun",
                "simctl",
                "spawn",
                identity.udid,
                "/usr/bin/arch",
                "-arm64",
                "/usr/bin/uname",
                "-m",
            ),
            operation="explicit simulator arm64 process check",
            timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        ),
        "explicit simulator arm64 process",
    )
    if process_architecture != "arm64":
        raise IosReferenceAppGateError(
            "selected simulator could not execute an explicit arm64 process"
        )
    return {
        "advertisesArm64": True,
        "explicitArm64CapabilityProcess": "passed",
    }


def _tree_identity(
    root: Path,
    label: str,
    *,
    expected_executable_paths: frozenset[str] | None = None,
    canonical_executable_paths: frozenset[str] | None = None,
) -> TreeIdentity:
    if (expected_executable_paths is None) != (
        canonical_executable_paths is None
    ):
        raise IosReferenceAppGateError(
            f"{label} executable identity policy is incomplete"
        )
    root = _directory(root, label)
    result: dict[str, dict[str, object]] = {}
    directory_paths: list[str] = []
    actual_executable_paths: set[str] = set()
    entry_count = 0
    byte_count = 0
    for directory, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        parent = Path(directory)
        for name in (*directories, *files):
            entry_count += 1
            if entry_count > MAX_APPLICATION_ENTRIES:
                raise IosReferenceAppGateError(
                    "application tree exceeds its entry bound"
                )
            path = parent / name
            if path.is_symlink():
                raise IosReferenceAppGateError(
                    "application tree contains a symbolic link"
                )
        directory_paths.extend(
            (parent / name).relative_to(root).as_posix()
            for name in directories
        )
        for name in files:
            path = _regular_file(parent / name, f"{label} file")
            if len(result) >= MAX_APPLICATION_FILES:
                raise IosReferenceAppGateError(
                    "application tree exceeds its file bound"
                )
            before = path.lstat()
            size = before.st_size
            if size > MAX_APPLICATION_BYTES - byte_count:
                raise IosReferenceAppGateError(
                    "application tree exceeds its byte bound"
                )
            byte_count += size
            digest = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            try:
                descriptor = os.open(path, flags)
            except OSError as error:
                raise IosReferenceAppGateError(
                    f"could not open {label} file safely"
                ) from error
            consumed = 0
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mode)
                    != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode)
                ):
                    raise IosReferenceAppGateError(
                        f"{label} file changed before identity read"
                    )
                while chunk := os.read(descriptor, COPY_CHUNK_BYTES):
                    consumed += len(chunk)
                    if consumed > size:
                        raise IosReferenceAppGateError(
                            f"{label} file grew during identity read"
                        )
                    digest.update(chunk)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if (
                consumed != size
                or (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                    opened.st_mode,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_mode,
                )
            ):
                raise IosReferenceAppGateError(
                    f"{label} file changed during identity read"
                )
            relative = path.relative_to(root).as_posix()
            executable = bool(opened.st_mode & 0o111)
            if executable:
                actual_executable_paths.add(relative)
            result[relative] = {
                "sizeBytes": size,
                "sha256": digest.hexdigest(),
                "executable": executable,
            }
    if not result:
        raise IosReferenceAppGateError(f"{label} is empty")
    if expected_executable_paths is not None:
        if actual_executable_paths != expected_executable_paths:
            raise IosReferenceAppGateError(
                f"{label} executable-path inventory changed"
            )
        if not canonical_executable_paths.issubset(result):
            raise IosReferenceAppGateError(
                f"{label} canonical executable-path inventory is incomplete"
            )
        for relative, record in result.items():
            record["executable"] = relative in canonical_executable_paths
    encoded = json.dumps(
        {"directories": directory_paths, "files": result},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return TreeIdentity(
        files=result,
        file_count=len(result),
        byte_count=byte_count,
        tree_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _install_transport_identity(
    application: Path,
    label: str,
    *,
    installed: bool,
) -> TreeIdentity:
    return _tree_identity(
        application,
        label,
        expected_executable_paths=(
            _IOS_INSTALLED_EXECUTABLE_PATHS
            if installed
            else _IOS_AUDITED_EXECUTABLE_PATHS
        ),
        canonical_executable_paths=_IOS_INSTALLED_EXECUTABLE_PATHS,
    )


def _tree_evidence(identity: TreeIdentity) -> dict[str, object]:
    return {
        "fileCount": identity.file_count,
        "byteCount": identity.byte_count,
        "treeSha256": identity.tree_sha256,
        "identityFormat": "directory-paths-file-bytes-executable-bit-v1",
    }


def _installed_transport_evidence(
    identity: TreeIdentity,
    source_identity: TreeIdentity,
) -> dict[str, object]:
    return {
        "fileCount": identity.file_count,
        "byteCount": identity.byte_count,
        "treeSha256": identity.tree_sha256,
        "sourceApplicationTreeSha256": source_identity.tree_sha256,
        "identityFormat": (
            "directory-paths-file-bytes-install-transport-executable-map-v1"
        ),
        "transportNormalization": {
            "operation": "executable-bit-true-to-false",
            "paths": sorted(_IOS_TRANSPORT_CLEARED_EXECUTABLE_PATHS),
            "retainedExecutablePaths": sorted(_IOS_INSTALLED_EXECUTABLE_PATHS),
        },
    }


def _git_source_revision(repository: Path) -> str:
    status = _run(
        ("git", "status", "--porcelain=v2", "--branch", "--untracked-files=no"),
        cwd=repository,
        timeout_seconds=SOURCE_CHECK_TIMEOUT_SECONDS,
        maximum_output=MAX_COMMAND_OUTPUT_BYTES,
        operation="Fonix Git source state",
    ).stdout.splitlines()
    revisions = [line.removeprefix("# branch.oid ") for line in status if line.startswith("# branch.oid ")]
    if (
        len(revisions) != 1
        or re.fullmatch(r"[0-9a-f]{40,64}", revisions[0]) is None
        or any(line and not line.startswith("# ") for line in status)
    ):
        raise IosReferenceAppGateError(
            "Fonix tracked source must be one clean Git revision"
        )
    return revisions[0]


def _snapshot_source_epoch(repository: Path, destination: Path) -> tuple[Path, dict[str, object]]:
    if destination.exists() or destination.is_symlink():
        raise IosReferenceAppGateError("source epoch destination must not exist")
    revision = _git_source_revision(repository)
    archive = destination.with_suffix(".tar")
    if archive.exists() or archive.is_symlink():
        raise IosReferenceAppGateError("source archive staging path already exists")
    destination.mkdir(mode=0o700)
    try:
        _run(
            ("git", "archive", "--format=tar", f"--output={archive}", revision),
            cwd=repository,
            timeout_seconds=SOURCE_CHECK_TIMEOUT_SECONDS,
            maximum_output=MAX_COMMAND_OUTPUT_BYTES,
            operation="Fonix Git source export",
        )
        _regular_file(archive, "Fonix Git source archive", maximum=MAX_ARCHIVE_BYTES)
        shutil.unpack_archive(archive, destination, "tar")
        archive.unlink()
        if not any(destination.iterdir()) or _git_source_revision(repository) != revision:
            raise IosReferenceAppGateError("Fonix Git source changed during export")
    except BaseException:
        archive.unlink(missing_ok=True)
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination, {"gitRevision": revision}


def _canonical_simulator_device_root(identity: SimulatorIdentity) -> Path:
    devices = (
        Path.home()
        / "Library/Developer/CoreSimulator/Devices"
    ).resolve(strict=True)
    root = _directory(devices / identity.udid, "selected simulator device root")
    root = root.resolve(strict=True)
    if root.parent != devices or root.name != identity.udid:
        raise IosReferenceAppGateError("selected simulator device root is not canonical")
    return root


def _installed_application_path(
    identity: SimulatorIdentity, device_root: Path
) -> Path:
    output = _run(
        (
            "/usr/bin/xcrun",
            "simctl",
            "get_app_container",
            identity.udid,
            APPLICATION_BUNDLE_IDENTIFIER,
            "app",
        ),
        operation="installed simulator application lookup",
        timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    installed = Path(_single_line(output, "installed simulator application lookup"))
    if not installed.is_absolute():
        raise IosReferenceAppGateError(
            "installed simulator application path is not absolute"
        )
    installed = installed.resolve(strict=True)
    try:
        relative = installed.relative_to(device_root)
    except ValueError as error:
        raise IosReferenceAppGateError(
            "installed simulator application escaped the selected device root"
        ) from error
    parts = relative.parts
    if (
        len(parts) != 6
        or parts[:4] != ("data", "Containers", "Bundle", "Application")
        or _UDID.fullmatch(parts[4]) is None
        or parts[5] != APPLICATION_BUNDLE_NAME
    ):
        raise IosReferenceAppGateError(
            "installed simulator application has a noncanonical container path"
        )
    return installed


def _bind_installed_application(
    identity: SimulatorIdentity,
    expectation: InstalledApplicationExpectation,
    device_root: Path,
) -> InstalledApplicationOwnership:
    installed = _installed_application_path(identity, device_root)
    if installed != expectation.container:
        raise IosReferenceAppGateError(
            "installed simulator application changed containers before binding"
        )
    installed_tree = _install_transport_identity(
        installed,
        "installed simulator application",
        installed=True,
    )
    if installed_tree != expectation.tree:
        raise IosReferenceAppGateError(
            "installed simulator application differs from the audited "
            "install-transport identity"
        )
    return InstalledApplicationOwnership(container=installed, tree=installed_tree)


def _revalidate_installed_ownership(
    identity: SimulatorIdentity,
    ownership: InstalledApplicationOwnership,
    device_root: Path,
) -> None:
    current = _installed_application_path(identity, device_root)
    if current != ownership.container:
        raise IosReferenceAppGateError(
            "installed simulator application ownership changed containers"
        )
    current_tree = _install_transport_identity(
        current,
        "owned installed simulator application",
        installed=True,
    )
    if current_tree != ownership.tree:
        raise IosReferenceAppGateError(
            "installed simulator application ownership changed tree identity"
        )


def _simulator_data_container(
    identity: SimulatorIdentity, device_root: Path
) -> Path:
    output = _run(
        (
            "/usr/bin/xcrun",
            "simctl",
            "get_app_container",
            identity.udid,
            APPLICATION_BUNDLE_IDENTIFIER,
            "data",
        ),
        operation="simulator data-container lookup",
        timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    raw_path = Path(_single_line(output, "simulator data-container lookup"))
    if not raw_path.is_absolute():
        raise IosReferenceAppGateError("simulator data-container path is not absolute")
    _directory(raw_path, "simulator data container")
    resolved = raw_path.resolve(strict=True)
    try:
        relative = resolved.relative_to(device_root)
    except ValueError as error:
        raise IosReferenceAppGateError(
            "simulator data container escaped the selected device root"
        ) from error
    if (
        len(relative.parts) != 5
        or relative.parts[:4] != ("data", "Containers", "Data", "Application")
        or _UDID.fullmatch(relative.parts[4]) is None
    ):
        raise IosReferenceAppGateError(
            "simulator data container has a noncanonical path"
        )
    _directory(resolved / "tmp", "simulator data-container temporary directory")
    return resolved


def _receipt_paths(data_container: Path, challenge: str) -> tuple[Path, Path]:
    challenge = _validate_challenge(challenge)
    temporary = _directory(
        data_container / "tmp", "simulator data-container temporary directory"
    )
    return (
        temporary
        / f"{REFERENCE_RECEIPT_BASENAME_PREFIX}{challenge}{REFERENCE_RECEIPT_SUFFIX}",
        temporary
        / f"{REFERENCE_RECEIPT_BASENAME_PREFIX}{challenge}{REFERENCE_STAGING_SUFFIX}",
    )


def _receipt_metadata(path: Path, label: str) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        raise IosReferenceAppGateError(f"{label} is a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise IosReferenceAppGateError(f"{label} is not a regular file")
    if metadata.st_size > MAX_REFERENCE_FILE_BYTES:
        raise IosReferenceAppGateError(
            f"{label} exceeds {MAX_REFERENCE_FILE_BYTES} bytes"
        )
    return metadata


def _require_receipt_paths_absent(receipt_path: Path, staging_path: Path) -> None:
    for path, label in (
        (receipt_path, "reference receipt file"),
        (staging_path, "reference receipt staging file"),
    ):
        if _receipt_metadata(path, label) is not None:
            raise IosReferenceAppGateError(f"stale {label} exists before launch")


def _read_receipt_file(
    path: Path,
    archive: PinnedArchive,
    challenge: str,
    process_id: int,
) -> tuple[dict[str, object], dict[str, object]]:
    expected = _receipt_metadata(path, "reference receipt file")
    if expected is None:
        raise IosReferenceAppGateError("reference receipt file disappeared")
    if expected.st_size == 0:
        raise IosReferenceAppGateError("reference receipt file is partial or empty")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise IosReferenceAppGateError("could not open reference receipt file safely") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or (before.st_dev, before.st_ino) != (expected.st_dev, expected.st_ino)
            or before.st_size != expected.st_size
            or before.st_size > MAX_REFERENCE_FILE_BYTES
        ):
            raise IosReferenceAppGateError(
                "reference receipt file changed before bounded read"
            )
        chunks: list[bytes] = []
        remaining = MAX_REFERENCE_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(data) != before.st_size
        or len(data) > MAX_REFERENCE_FILE_BYTES
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise IosReferenceAppGateError(
            "reference receipt file changed during bounded read"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IosReferenceAppGateError("reference receipt file is not UTF-8") from error
    if "\r" in text or "\x00" in text or text.count("\n") != 1 or not text.endswith("\n"):
        raise IosReferenceAppGateError(
            "reference receipt file is partial or has an invalid line boundary"
        )
    receipt = _parse_reference_output(
        text[:-1], archive, challenge, process_id
    )
    return receipt, {
        "sha256": hashlib.sha256(data).hexdigest(),
        "sizeBytes": len(data),
        "publication": "atomic-sandbox-file",
    }


def _poll_reference_receipt(
    receipt_path: Path,
    staging_path: Path,
    archive: PinnedArchive,
    challenge: str,
    process_id: int,
    *,
    timeout_seconds: float = REFERENCE_TIMEOUT_SECONDS,
    poll_interval_seconds: float = REFERENCE_POLL_INTERVAL_SECONDS,
    clock: Any = time.monotonic,
    sleeper: Any = time.sleep,
) -> tuple[dict[str, object], dict[str, object]]:
    if timeout_seconds <= 0 or timeout_seconds > REFERENCE_TIMEOUT_SECONDS:
        raise IosReferenceAppGateError("reference receipt timeout is outside its bound")
    if poll_interval_seconds <= 0 or poll_interval_seconds > 1:
        raise IosReferenceAppGateError("reference receipt poll interval is outside its bound")
    deadline = clock() + timeout_seconds
    while True:
        final_metadata = _receipt_metadata(receipt_path, "reference receipt file")
        staging_metadata = _receipt_metadata(
            staging_path, "reference receipt staging file"
        )
        if final_metadata is not None:
            if staging_metadata is not None:
                raise IosReferenceAppGateError(
                    "reference receipt staging file survived atomic publication"
                )
            return _read_receipt_file(
                receipt_path, archive, challenge, process_id
            )
        now = clock()
        if now >= deadline:
            state = " with an incomplete staging file" if staging_metadata is not None else ""
            raise IosReferenceAppGateError(
                f"reference receipt file timed out{state}"
            )
        sleeper(min(poll_interval_seconds, deadline - now))


def _launch_simulator_application(
    identity: SimulatorIdentity, challenge: str
) -> int:
    output = _run(
        (
            "/usr/bin/xcrun",
            "simctl",
            "launch",
            identity.udid,
            APPLICATION_BUNDLE_IDENTIFIER,
        ),
        environment=_reference_environment(challenge),
        operation="simulator reference-app launch",
        timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    if output.stderr:
        raise IosReferenceAppGateError("simulator reference-app launch emitted stderr")
    stdout = output.stdout
    if stdout.endswith("\n"):
        stdout = stdout[:-1]
    if "\n" in stdout or "\r" in stdout:
        raise IosReferenceAppGateError("simulator launch returned an ambiguous PID")
    match = re.fullmatch(
        rf"{re.escape(APPLICATION_BUNDLE_IDENTIFIER)}: ([1-9][0-9]{{0,9}})",
        stdout,
    )
    if match is None:
        raise IosReferenceAppGateError("simulator launch did not return the exact PID")
    pid = int(match.group(1))
    if pid > 2_147_483_647:
        raise IosReferenceAppGateError("simulator launch PID is outside its bound")
    return pid


def _simulator_process_is_live(
    identity: SimulatorIdentity,
    pid: int,
) -> bool:
    pid = _process_id(pid)
    command = (
        "/usr/bin/xcrun",
        "simctl",
        "spawn",
        identity.udid,
        "/bin/ps",
        "-p",
        str(pid),
        "-o",
        "pid=",
    )
    status = _run_status(
        command,
        operation="simulator process settlement probe",
        timeout_seconds=PROCESS_PROBE_TIMEOUT_SECONDS,
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    if status.return_code == 1 and not status.stdout and not status.stderr:
        return False
    if (
        status.return_code == 0
        and not status.stderr
        and status.stdout.strip() == str(pid)
    ):
        return True
    raise IosReferenceAppGateError(
        "simulator process settlement probe returned an unexpected result"
    )


def _wait_simulator_process_settlement(
    identity: SimulatorIdentity,
    pid: int,
    *,
    timeout_seconds: float = PROCESS_SETTLEMENT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = REFERENCE_POLL_INTERVAL_SECONDS,
    clock: Any = time.monotonic,
    sleeper: Any = time.sleep,
) -> None:
    if timeout_seconds <= 0 or timeout_seconds > PROCESS_SETTLEMENT_TIMEOUT_SECONDS:
        raise IosReferenceAppGateError("process settlement timeout is outside its bound")
    deadline = clock() + timeout_seconds
    while True:
        if not _simulator_process_is_live(identity, pid):
            return
        now = clock()
        if now >= deadline:
            raise IosReferenceAppGateError(
                "simulator reference-app process did not terminate"
            )
        sleeper(min(poll_interval_seconds, deadline - now))


def _terminate_simulator_application(identity: SimulatorIdentity, pid: int) -> None:
    if not _simulator_process_is_live(identity, pid):
        return
    try:
        _run(
            (
                "/usr/bin/xcrun",
                "simctl",
                "terminate",
                identity.udid,
                APPLICATION_BUNDLE_IDENTIFIER,
            ),
            operation="simulator reference-app termination",
            timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        )
    except IosReferenceAppGateError as termination_error:
        try:
            if not _simulator_process_is_live(identity, pid):
                return
        except BaseException as probe_error:
            raise termination_error from probe_error
        raise
    _wait_simulator_process_settlement(identity, pid)


def _cleanup_simulator(
    identity: SimulatorIdentity,
    *,
    device_root: Path,
    shutdown_if_started: bool,
    owned_pid: int | None,
    install_succeeded: bool,
    installed_expectation: InstalledApplicationExpectation | None,
    installed_ownership: InstalledApplicationOwnership | None,
) -> None:
    errors: list[BaseException] = []
    if install_succeeded and installed_ownership is None:
        if installed_expectation is None:
            errors.append(
                IosReferenceAppGateError(
                    "cannot bind cleanup ownership: pre-install identity was not retained"
                )
            )
        else:
            try:
                installed_ownership = _bind_installed_application(
                    identity,
                    installed_expectation,
                    device_root,
                )
            except BaseException as error:
                errors.append(error)
    if owned_pid is not None:
        if installed_ownership is None:
            errors.append(
                IosReferenceAppGateError(
                    "cannot terminate: installed application ownership was not established"
                )
            )
        else:
            try:
                _revalidate_installed_ownership(
                    identity, installed_ownership, device_root
                )
                _terminate_simulator_application(identity, owned_pid)
                owned_pid = None
            except BaseException as error:
                errors.append(error)
    if installed_ownership is not None and owned_pid is None:
        try:
            _revalidate_installed_ownership(
                identity, installed_ownership, device_root
            )
            _run(
                (
                    "/usr/bin/xcrun",
                    "simctl",
                    "uninstall",
                    identity.udid,
                    APPLICATION_BUNDLE_IDENTIFIER,
                ),
                operation="simulator reference-app uninstall",
                timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
                maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
            )
            _require_package_absent(identity)
        except BaseException as error:
            errors.append(error)
    if shutdown_if_started:
        try:
            _run(
                ("/usr/bin/xcrun", "simctl", "shutdown", identity.udid),
                operation="simulator shutdown",
                timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
                maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
            )
        except BaseException as error:
            errors.append(error)
    if errors:
        raise IosReferenceCleanupError(errors) from errors[0]


def _run_owned_lifecycle(
    body: Callable[[], _T], cleanup: Callable[[], None]
) -> _T:
    primary_error: BaseException | None = None
    result: _T | None = None
    try:
        result = body()
    except BaseException as error:
        primary_error = error
    cleanup_error: BaseException | None = None
    try:
        cleanup()
    except BaseException as error:
        cleanup_error = error
    if primary_error is not None and cleanup_error is not None:
        raise IosReferenceLifecycleError(
            primary_error, cleanup_error
        ) from cleanup_error
    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    return result  # type: ignore[return-value]


def _native_inventory_evidence(audit: dict[str, Any]) -> dict[str, object]:
    inventory = audit["nativeInventory"]
    frameworks = inventory["frameworks"]
    records = inventory["machOBinaries"]
    exports = inventory["shimExports"]
    linked = inventory["linkedRuntimeIdentity"]
    hook_metadata = linked["hookInvocationMetadata"]
    accounted_transformations = linked["accountedTransformations"]
    public_linked_identity = {
        "runtimeMode": linked["runtimeMode"],
        "packagedShim": linked["packagedShim"],
        "hookInvocationMetadata": {
            "invocationId": hook_metadata["invocationId"],
            "status": hook_metadata["status"],
        },
        "normalizedRuntimeFields": linked["normalizedRuntimeFields"],
        "normalizedRuntimeFieldsSha256": linked[
            "normalizedRuntimeFieldsSha256"
        ],
        "comparisonScope": linked["comparisonScope"],
        "accountedTransformations": accounted_transformations,
        "embeddedBuildIdentity": linked["embeddedBuildIdentity"],
        "nlistAndDyldExports": linked["nlistAndDyldExports"],
        "separatelyPackagedOrtMachOs": linked["separatelyPackagedOrtMachOs"],
        "auditedOrtLoadCommandDependencies": linked[
            "auditedOrtLoadCommandDependencies"
        ],
        "runtimeDlopenBehavior": linked["runtimeDlopenBehavior"],
        "otherMachOStaticOrtCopies": linked["otherMachOStaticOrtCopies"],
        "staticArchiveMultiplicity": linked["staticArchiveMultiplicity"],
        "claimBoundary": linked["claimBoundary"],
    }
    return {
        "profile": inventory["profile"],
        "frameworks": {
            "count": len(frameworks),
            "sha256": _canonical_json_sha256(frameworks),
        },
        "machOBinaries": {
            "count": len(records),
            "paths": [record["path"] for record in records],
            "recordFields": sorted(_IOS_MACHO_RECORD_KEYS),
            "dylibDependencyCount": sum(
                len(record["dylibDependencies"]) for record in records
            ),
            "sha256": _canonical_json_sha256(records),
        },
        "shimExports": {
            "allowlist": exports["allowlist"],
            "symbolCount": exports["symbolCount"],
            "allowlistSha256": exports["allowlistSha256"],
            "nlistSymbolSetSha256": exports["nlistSymbolSetSha256"],
            "dyldExportSetSha256": exports["dyldExportSetSha256"],
            "sha256": _canonical_json_sha256(exports),
        },
        "linkedRuntimeIdentity": {
            "runtimeMode": public_linked_identity["runtimeMode"],
            "hookInvocationMetadata": public_linked_identity[
                "hookInvocationMetadata"
            ],
            "normalizedRuntimeFields": linked["normalizedRuntimeFields"],
            "normalizedRuntimeFieldsSha256": linked[
                "normalizedRuntimeFieldsSha256"
            ],
            "comparisonScope": linked["comparisonScope"],
            "accountedTransformations": {
                "count": len(accounted_transformations),
                "sha256": _canonical_json_sha256(accounted_transformations),
            },
            "embeddedBuildIdentity": linked["embeddedBuildIdentity"],
            "nlistAndDyldExports": linked["nlistAndDyldExports"],
            "separatelyPackagedOrtMachOs": linked[
                "separatelyPackagedOrtMachOs"
            ],
            "auditedOrtLoadCommandDependencies": linked[
                "auditedOrtLoadCommandDependencies"
            ],
            "runtimeDlopenBehavior": linked["runtimeDlopenBehavior"],
            "otherMachOStaticOrtCopies": linked[
                "otherMachOStaticOrtCopies"
            ],
            "staticArchiveMultiplicity": linked[
                "staticArchiveMultiplicity"
            ],
            "claimBoundary": linked["claimBoundary"],
            "sha256": _canonical_json_sha256(public_linked_identity),
        },
    }


def _contains_private_launch_value(
    value: object, *, challenge: str, process_id: int
) -> bool:
    if isinstance(value, dict):
        if any(key in {"challenge", "processId"} for key in value):
            return True
        return any(
            _contains_private_launch_value(
                item, challenge=challenge, process_id=process_id
            )
            for item in value.values()
        )
    if isinstance(value, list):
        return any(
            _contains_private_launch_value(
                item, challenge=challenge, process_id=process_id
            )
            for item in value
        )
    return value == challenge or value == str(process_id)


def run_gate(
    *,
    repository: Path,
    flutter: Path,
    artifact_cache: Path,
    simulator_udid: str,
    work_directory: Path,
) -> dict[str, object]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise IosReferenceAppGateError("the iOS reference gate requires an arm64 Mac")
    repository = _directory(repository.resolve(strict=True), "Fonix repository")
    flutter = _regular_file(flutter.resolve(strict=True), "Flutter executable")
    if not os.access(flutter, os.X_OK):
        raise IosReferenceAppGateError("Flutter executable is not executable")
    artifact_cache = _directory(
        artifact_cache.resolve(strict=True), "supplied artifact cache"
    )
    if not work_directory.is_absolute():
        raise IosReferenceAppGateError("--work-dir must be absolute")
    work_parent = _directory(
        work_directory.parent.resolve(strict=True), "reference work parent"
    )
    work_directory = work_parent / work_directory.name
    if work_directory.exists() or work_directory.is_symlink():
        raise IosReferenceAppGateError("--work-dir must not already exist")

    if _UDID.fullmatch(simulator_udid) is None:
        raise IosReferenceAppGateError("--simulator-udid must be a canonical UUID")
    flutter_version, apple = _verify_apple_environment(flutter)
    dart = _regular_file(
        flutter.parent / "cache/dart-sdk/bin/dart",
        "Flutter-bundled Dart executable",
    )
    if not os.access(dart, os.X_OK):
        raise IosReferenceAppGateError("Flutter-bundled Dart is not executable")
    work_directory.mkdir(mode=0o700)
    source_epoch, source_epoch_evidence = _snapshot_source_epoch(
        repository, work_directory / "source_epoch"
    )
    frozen_source_identity = _tree_identity(
        source_epoch, "frozen Fonix source epoch"
    )
    archives = _load_pinned_archives(source_epoch / "native/versions.lock.yaml")
    host_archive = _COMMON._load_pinned_archive(
        source_epoch / "native/versions.lock.yaml"
    )
    source_example = _directory(source_epoch / "example", "frozen committed example")

    device_work = work_directory / "device"
    simulator_work = work_directory / "simulator"
    device_copy, device_manifest = _prepare_variant(
        repository=source_epoch,
        source_example=source_example,
        destination=device_work,
        flutter=flutter,
        dart=dart,
        artifact_cache=artifact_cache,
        archive=archives["device"],
        host_archive=host_archive,
        run_source_checks=True,
    )
    simulator_copy, simulator_manifest = _prepare_variant(
        repository=source_epoch,
        source_example=source_example,
        destination=simulator_work,
        flutter=flutter,
        dart=dart,
        artifact_cache=artifact_cache,
        archive=archives["simulator"],
        host_archive=host_archive,
        run_source_checks=False,
    )
    if device_manifest == simulator_manifest:
        raise IosReferenceAppGateError(
            "device and simulator preparation produced the same native manifest"
        )
    if _tree_identity(source_epoch, "frozen Fonix source epoch") != frozen_source_identity:
        raise IosReferenceAppGateError(
            "frozen Fonix source epoch changed while deriving build variants"
        )

    _run(
        (
            str(flutter),
            "build",
            "ios",
            "--release",
            "--no-codesign",
            "--no-pub",
        ),
        cwd=device_work,
        operation="unsigned iOS arm64 device Release build",
        timeout_seconds=APPLICATION_BUILD_TIMEOUT_SECONDS,
        maximum_output=MAX_COMMAND_OUTPUT_BYTES,
    )
    device_application = _directory(
        device_work / "build/ios/iphoneos" / APPLICATION_BUNDLE_NAME,
        "final unsigned iOS device application",
    )
    device_reference_shim = _resolve_reference_shim(
        work_directory=device_work,
        repository=source_epoch,
        archive=archives["device"],
    )
    device_audit, device_application_identity = _audit_stable_application(
        repository=source_epoch,
        application=device_application,
        archive=archives["device"],
        reference_shim=device_reference_shim,
    )

    _run(
        (
            str(flutter),
            "build",
            "ios",
            "--simulator",
            "--debug",
            "--no-pub",
        ),
        cwd=simulator_work,
        operation="iOS arm64 simulator Debug build",
        timeout_seconds=APPLICATION_BUILD_TIMEOUT_SECONDS,
        maximum_output=MAX_COMMAND_OUTPUT_BYTES,
    )
    _run(
        (
            str(flutter),
            "build",
            "ios",
            "--simulator",
            "--debug",
            "--no-pub",
        ),
        cwd=simulator_work,
        operation="incremental iOS arm64 simulator Debug rebuild",
        timeout_seconds=APPLICATION_BUILD_TIMEOUT_SECONDS,
        maximum_output=MAX_COMMAND_OUTPUT_BYTES,
    )
    simulator_application = _directory(
        simulator_work / "build/ios/iphonesimulator" / APPLICATION_BUNDLE_NAME,
        "final iOS simulator application",
    )
    simulator_reference_shim = _resolve_reference_shim(
        work_directory=simulator_work,
        repository=source_epoch,
        archive=archives["simulator"],
    )
    simulator_audit, simulator_application_identity = _audit_stable_application(
        repository=source_epoch,
        application=simulator_application,
        archive=archives["simulator"],
        reference_shim=simulator_reference_shim,
    )
    if _tree_identity(source_epoch, "frozen Fonix source epoch") != frozen_source_identity:
        raise IosReferenceAppGateError(
            "frozen Fonix source epoch changed during build or audit"
        )

    simulator: SimulatorIdentity | None = None
    device_root: Path | None = None
    booted_by_gate = False
    install_succeeded = False
    installed_expectation: InstalledApplicationExpectation | None = None
    installed_ownership: InstalledApplicationOwnership | None = None
    owned_pid: int | None = None
    launch_challenge: str | None = None

    def exercise_simulator() -> tuple[
        SimulatorIdentity,
        dict[str, object],
        dict[str, object],
        dict[str, object],
        str,
        int,
    ]:
        nonlocal simulator
        nonlocal device_root
        nonlocal booted_by_gate
        nonlocal install_succeeded
        nonlocal installed_expectation
        nonlocal installed_ownership
        nonlocal owned_pid
        nonlocal launch_challenge

        device_root = _canonical_simulator_device_root(
            SimulatorIdentity(
                udid=simulator_udid,
                udid_sha256=hashlib.sha256(simulator_udid.encode("ascii")).hexdigest(),
                name="pending",
                runtime=VALIDATED_SIMULATOR_RUNTIME,
                initial_state="Shutdown",
            )
        )
        # This state read is intentionally adjacent to the boot decision. A
        # state observed before the builds is not ownership evidence.
        simulator = _simulator_identity(simulator_udid)
        if booted_by_gate:
            raise IosReferenceAppGateError(
                "simulator boot ownership was unexpectedly already set"
            )
        if simulator.initial_state == "Shutdown":
            _run(
                ("/usr/bin/xcrun", "simctl", "boot", simulator.udid),
                operation="simulator boot",
                timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
                maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
            )
            # Ownership begins only after simctl confirms that this invocation
            # successfully performed the boot transition.
            booted_by_gate = True
            _run(
                (
                    "/usr/bin/xcrun",
                    "simctl",
                    "bootstatus",
                    simulator.udid,
                    "-b",
                ),
                operation="simulator boot settlement",
                timeout_seconds=SIMULATOR_BOOT_TIMEOUT_SECONDS,
                maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
            )
        arm64_probe = _verify_simulator_arm64(simulator)
        _require_package_absent(simulator)
        if (
            _tree_identity(
                simulator_application, "pre-install audited simulator application"
            )
            != simulator_application_identity
        ):
            raise IosReferenceAppGateError(
                "simulator application changed after audit and before install"
            )
        expected_installed_tree = _install_transport_identity(
            simulator_application,
            "pre-install simulator install-transport identity",
            installed=False,
        )
        _run(
            (
                "/usr/bin/xcrun",
                "simctl",
                "install",
                simulator.udid,
                str(simulator_application),
            ),
            operation="simulator reference-app install",
            timeout_seconds=SIMULATOR_COMMAND_TIMEOUT_SECONDS,
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        )
        install_succeeded = True
        installed_expectation = InstalledApplicationExpectation(
            container=_installed_application_path(simulator, device_root),
            tree=expected_installed_tree,
        )
        _require_package_present(simulator)
        installed_ownership = _bind_installed_application(
            simulator, installed_expectation, device_root
        )
        data_container = _simulator_data_container(simulator, device_root)
        launch_challenge = _validate_challenge(secrets.token_hex(32))
        receipt_path, staging_path = _receipt_paths(
            data_container, launch_challenge
        )
        _require_receipt_paths_absent(receipt_path, staging_path)
        owned_pid = _launch_simulator_application(simulator, launch_challenge)
        receipt, receipt_file_evidence = _poll_reference_receipt(
            receipt_path,
            staging_path,
            archives["simulator"],
            launch_challenge,
            owned_pid,
        )
        launched_pid = owned_pid
        _terminate_simulator_application(simulator, owned_pid)
        owned_pid = None
        _revalidate_installed_ownership(
            simulator, installed_ownership, device_root
        )
        public_receipt = {
            key: value
            for key, value in receipt.items()
            if key not in {"challenge", "processId"}
        }
        return (
            simulator,
            arm64_probe,
            public_receipt,
            receipt_file_evidence,
            launch_challenge,
            launched_pid,
        )

    def cleanup_simulator() -> None:
        if simulator is not None and device_root is not None:
            _cleanup_simulator(
                simulator,
                device_root=device_root,
                shutdown_if_started=booted_by_gate,
                owned_pid=owned_pid,
                install_succeeded=install_succeeded,
                installed_expectation=installed_expectation,
                installed_ownership=installed_ownership,
            )

    (
        simulator,
        arm64_probe,
        receipt,
        receipt_file_evidence,
        launch_challenge,
        launched_pid,
    ) = _run_owned_lifecycle(exercise_simulator, cleanup_simulator)
    if installed_ownership is None:
        raise IosReferenceAppGateError(
            "installed simulator application ownership was not retained"
        )

    launch_binding = {
        "challengeSha256": hashlib.sha256(
            launch_challenge.encode("ascii")
        ).hexdigest(),
        "processIdSha256": hashlib.sha256(
            str(launched_pid).encode("ascii")
        ).hexdigest(),
        "receiptBinding": "passed",
    }

    report: dict[str, object] = {
        "schemaVersion": 1,
        "result": "passed",
        "flutterRevision": flutter_version["frameworkRevision"],
        "flutterVersion": flutter_version["frameworkVersion"],
        "appleEnvironment": {
            "xcodeVersion": apple.xcode_version,
            "xcodeBuild": apple.xcode_build,
            "deviceSdk": apple.device_sdk,
            "simulatorSdk": apple.simulator_sdk,
            "macosVersion": apple.macos_version,
            "macosBuild": apple.macos_build,
        },
        "applicationMinimumOs": APPLICATION_MINIMUM_OS,
        "sourceEpoch": source_epoch_evidence,
        "sourceCopies": {
            "device": device_copy,
            "simulator": simulator_copy,
        },
        "device": {
            "artifactId": archives["device"].artifact_id,
            "archiveSha256": archives["device"].sha256,
            "assetManifestSha256": device_manifest,
            "configuration": "release",
            "codeSigning": "disabled",
            "signaturePolicy": device_audit["signaturePolicy"],
            "signatureStatus": device_audit["signatureStatus"],
            "claimStatus": "static-only",
            "applicationIdentity": _tree_evidence(device_application_identity),
            "referenceShim": {
                "sha256": device_reference_shim.sha256,
                "invocationHash": device_reference_shim.invocation_hash,
            },
            "nativeInventory": _native_inventory_evidence(device_audit),
            "build": "passed",
            "audit": "passed",
            "execution": "not-run-no-physical-device",
        },
        "simulator": {
            "artifactId": archives["simulator"].artifact_id,
            "archiveSha256": archives["simulator"].sha256,
            "assetManifestSha256": simulator_manifest,
            "configuration": "debug",
            "runtime": simulator.runtime,
            "name": simulator.name,
            "udidSha256": simulator.udid_sha256,
            "simulatorArm64CapabilityProbe": arm64_probe,
            "signaturePolicy": simulator_audit["signaturePolicy"],
            "claimStatus": "exact-simulator-functional-only",
            "applicationIdentity": _tree_evidence(simulator_application_identity),
            "referenceShim": {
                "sha256": simulator_reference_shim.sha256,
                "invocationHash": simulator_reference_shim.invocation_hash,
            },
            "nativeInventory": _native_inventory_evidence(simulator_audit),
            "run": "passed",
            "incrementalBuild": "passed",
            "audit": "passed",
            "referenceReceipt": receipt,
            "receiptFileEvidence": receipt_file_evidence,
            "launchBinding": launch_binding,
            "installedApplicationBinding": _installed_transport_evidence(
                installed_ownership.tree,
                simulator_application_identity,
            ),
            "processSettlement": "passed",
            "uninstall": "passed",
        },
        "analysis": "passed",
        "tests": "passed",
        "claimBoundary": {
            "proves": (
                "the exact closed arm64 device and simulator Mach-O inventories, "
                "absence of a separately packaged raw ONNX Runtime Mach-O or "
                "audited ONNX Runtime load-command dependency, packaged-shim "
                "binding to each validated native-assets hook output, and the "
                "exact audited simulator transport-normalized installed-tree "
                "CPU/full-assignment receipt"
            ),
            "doesNotProve": [
                "physical-device execution",
                "absence of runtime dlopen behavior",
                "absence of an extra static ONNX Runtime copy in another Mach-O",
                "that the selected static archive was linked exactly once",
                "release signing, provisioning, IPA, App Store, or distribution",
                "CoreML, XNNPACK, GPU, or Neural Engine assignment",
                "performance, memory, thermal, or sustained behavior",
                "simulator Release execution or transferability to another tuple",
            ],
        },
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if launch_challenge in encoded or _contains_private_launch_value(
        report, challenge=launch_challenge, process_id=launched_pid
    ):
        raise IosReferenceAppGateError(
            "iOS reference gate report exposed private launch binding values"
        )
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise IosReferenceAppGateError("iOS reference gate report exceeds its bound")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--flutter", required=True, type=Path)
    parser.add_argument("--artifact-cache", required=True, type=Path)
    parser.add_argument("--simulator-udid", required=True)
    parser.add_argument("--work-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = run_gate(
            repository=arguments.repository,
            flutter=arguments.flutter,
            artifact_cache=arguments.artifact_cache,
            simulator_udid=arguments.simulator_udid,
            work_directory=arguments.work_dir,
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        IosReferenceAppGateError,
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ) as error:
        print(f"run_ios_reference_app_gate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
