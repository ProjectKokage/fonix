#!/usr/bin/env python3
"""Audit a final Flutter Apple application containing Fonix native assets."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import struct
import subprocess
import tempfile
from typing import Any, Iterator, Sequence


_MANIFEST_NAME = "fonix-native-artifact-manifest.json"
_NOTICE_NAME = "ThirdPartyNotices.txt"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$")
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_APPLE_EXPORT = re.compile(r"^_dort_[a-z0-9_]{1,120}$")
_HOOK_INVOCATION_ID = re.compile(r"^[0-9a-f]{10}$")
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_MACHO_BYTES = 512 * 1024 * 1024
_MAX_APPLICATION_ENTRIES = 32768
_MAX_IOS_MACHO_FILES = 64
_MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 5 * 60
_STRICT_SIGNATURE_POLICY = "strict"
_IOS_DEVICE_UNSIGNED_SIGNATURE_POLICY = "ios-device-unsigned-development"
_SIGNATURE_POLICIES = (
    _STRICT_SIGNATURE_POLICY,
    _IOS_DEVICE_UNSIGNED_SIGNATURE_POLICY,
)

_BOUNDED_PROCESS_HELPER: Any | None = None


def _bounded_process_helper() -> Any:
    global _BOUNDED_PROCESS_HELPER
    if _BOUNDED_PROCESS_HELPER is None:
        path = Path(__file__).resolve().with_name("bounded_process.py")
        specification = importlib.util.spec_from_file_location(
            "_fonix_apple_bounded_process",
            path,
        )
        if specification is None or specification.loader is None:
            raise AppleApplicationAuditError(
                "could not load bounded process helper"
            )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        _BOUNDED_PROCESS_HELPER = module
    return _BOUNDED_PROCESS_HELPER


def _apple_command_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DYLD_")
    }
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    return environment
_MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}
_MACHO_DYLIB_LOAD_COMMANDS = {
    0xC,  # LC_LOAD_DYLIB
    0x20,  # LC_LAZY_LOAD_DYLIB
    0x80000018,  # LC_LOAD_WEAK_DYLIB
    0x8000001F,  # LC_REEXPORT_DYLIB
    0x80000023,  # LC_LOAD_UPWARD_DYLIB
}
_MACHO_DYLIB_LOAD_KINDS = {
    0xC: "load",
    0x20: "lazy",
    0x80000018: "weak",
    0x8000001F: "reexport",
    0x80000023: "upward",
}
_MACHO_CODE_SIGNATURE_COMMAND = 0x1D
_MACHO_RPATH_COMMAND = 0x8000001C
_MACHO_SEGMENT_64_COMMAND = 0x19
_MACHO_DYLIB_ID_COMMAND = 0xD
_MACHO_UUID_COMMAND = 0x1B
_MACHO_SYMTAB_COMMAND = 0x2
_MACHO_DYSYMTAB_COMMAND = 0xB
_MACHO_EXECUTE_FILE_TYPE = 0x2
_MACHO_DYLIB_FILE_TYPE = 0x6
_MACHO_ARM64_ALL_SUBTYPE = 0
_MACHO_FORBIDDEN_IOS_COMMANDS = {
    0x24,  # LC_VERSION_MIN_MACOSX
    0x25,  # LC_VERSION_MIN_IPHONEOS
    0x27,  # LC_DYLD_ENVIRONMENT
    0x2F,  # LC_VERSION_MIN_TVOS
    0x30,  # LC_VERSION_MIN_WATCHOS
}
_MACHO_LINKEDIT_DATA_COMMANDS = {
    0x1E,  # LC_SEGMENT_SPLIT_INFO
    0x26,  # LC_FUNCTION_STARTS
    0x29,  # LC_DATA_IN_CODE
    0x2B,  # LC_DYLIB_CODE_SIGN_DRS
    0x2E,  # LC_LINKER_OPTIMIZATION_HINT
    0x80000033,  # LC_DYLD_EXPORTS_TRIE
    0x80000034,  # LC_DYLD_CHAINED_FIXUPS
}
_MACHO_DYLD_INFO_COMMANDS = {0x22, 0x80000022}
_IOS_FRAMEWORKS = (
    "Frameworks/App.framework",
    "Frameworks/Flutter.framework",
    "Frameworks/fonix_shim.framework",
)
_IOS_SHIM_PATH = "Frameworks/fonix_shim.framework/fonix_shim"
_IOS_APP_FRAMEWORK_DEPENDENCIES = frozenset({"/usr/lib/libSystem.B.dylib"})
_IOS_SHIM_DEPENDENCIES = frozenset(
    {
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        "/System/Library/Frameworks/CoreML.framework/CoreML",
        "/System/Library/Frameworks/Foundation.framework/Foundation",
        "/usr/lib/libSystem.B.dylib",
        "/usr/lib/libc++.1.dylib",
        "/usr/lib/libobjc.A.dylib",
    }
)
_IOS_DEVICE_RUNNER_DEPENDENCIES = frozenset(
    {
        "@rpath/Flutter.framework/Flutter",
        "/System/Library/Frameworks/Foundation.framework/Foundation",
        "/usr/lib/libSystem.B.dylib",
        "/usr/lib/libc++.1.dylib",
        "/usr/lib/libobjc.A.dylib",
        "/usr/lib/swift/libswiftCore.dylib",
        "/usr/lib/swift/libswiftCoreAudio.dylib",
        "/usr/lib/swift/libswiftCoreFoundation.dylib",
        "/usr/lib/swift/libswiftCoreImage.dylib",
        "/usr/lib/swift/libswiftCoreMedia.dylib",
        "/usr/lib/swift/libswiftDarwin.dylib",
        "/usr/lib/swift/libswiftDispatch.dylib",
        "/usr/lib/swift/libswiftFoundation.dylib",
        "/usr/lib/swift/libswiftMetal.dylib",
        "/usr/lib/swift/libswiftOSLog.dylib",
        "/usr/lib/swift/libswiftObjectiveC.dylib",
        "/usr/lib/swift/libswiftQuartzCore.dylib",
        "/usr/lib/swift/libswiftSpatial.dylib",
        "/usr/lib/swift/libswiftUIKit.dylib",
        "/usr/lib/swift/libswiftUniformTypeIdentifiers.dylib",
        "/usr/lib/swift/libswiftXPC.dylib",
        "/usr/lib/swift/libswiftos.dylib",
        "/usr/lib/swift/libswiftsimd.dylib",
    }
)
_IOS_SIMULATOR_RUNNER_DEPENDENCIES = frozenset(
    {
        "@rpath/Flutter.framework/Flutter",
        "/System/Library/Frameworks/Foundation.framework/Foundation",
        "/System/Library/Frameworks/SwiftUI.framework/SwiftUI",
        "/System/Library/Frameworks/UIKit.framework/UIKit",
        "/usr/lib/libSystem.B.dylib",
        "/usr/lib/libc++.1.dylib",
        "/usr/lib/libobjc.A.dylib",
        "/usr/lib/swift/libswiftCore.dylib",
        "/usr/lib/swift/libswiftCoreAudio.dylib",
        "/usr/lib/swift/libswiftCoreFoundation.dylib",
        "/usr/lib/swift/libswiftCoreImage.dylib",
        "/usr/lib/swift/libswiftCoreMedia.dylib",
        "/usr/lib/swift/libswiftDarwin.dylib",
        "/usr/lib/swift/libswiftDispatch.dylib",
        "/usr/lib/swift/libswiftFoundation.dylib",
        "/usr/lib/swift/libswiftMetal.dylib",
        "/usr/lib/swift/libswiftOSLog.dylib",
        "/usr/lib/swift/libswiftObjectiveC.dylib",
        "/usr/lib/swift/libswiftQuartzCore.dylib",
        "/usr/lib/swift/libswiftSpatial.dylib",
        "/usr/lib/swift/libswiftUIKit.dylib",
        "/usr/lib/swift/libswiftUniformTypeIdentifiers.dylib",
        "/usr/lib/swift/libswiftXPC.dylib",
        "/usr/lib/swift/libswiftos.dylib",
        "/usr/lib/swift/libswiftsimd.dylib",
    }
)
_IOS_FLUTTER_COMMON_DEPENDENCIES = frozenset(
    {
        "/System/Library/Frameworks/Accessibility.framework/Accessibility",
        "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox",
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
        "/System/Library/Frameworks/CoreMedia.framework/CoreMedia",
        "/System/Library/Frameworks/CoreServices.framework/CoreServices",
        "/System/Library/Frameworks/CoreText.framework/CoreText",
        "/System/Library/Frameworks/CoreVideo.framework/CoreVideo",
        "/System/Library/Frameworks/Foundation.framework/Foundation",
        "/System/Library/Frameworks/IOSurface.framework/IOSurface",
        "/System/Library/Frameworks/ImageIO.framework/ImageIO",
        "/System/Library/Frameworks/Metal.framework/Metal",
        (
            "/System/Library/Frameworks/MetalPerformanceShaders.framework/"
            "MetalPerformanceShaders"
        ),
        "/System/Library/Frameworks/MobileCoreServices.framework/MobileCoreServices",
        "/System/Library/Frameworks/QuartzCore.framework/QuartzCore",
        "/System/Library/Frameworks/Security.framework/Security",
        "/System/Library/Frameworks/UIKit.framework/UIKit",
        "/System/Library/Frameworks/WebKit.framework/WebKit",
        "/usr/lib/libSystem.B.dylib",
        "/usr/lib/libobjc.A.dylib",
        "/usr/lib/swift/libswiftCore.dylib",
        "/usr/lib/swift/libswiftCoreAudio.dylib",
        "/usr/lib/swift/libswiftCoreFoundation.dylib",
        "/usr/lib/swift/libswiftCoreImage.dylib",
        "/usr/lib/swift/libswiftCoreMedia.dylib",
        "/usr/lib/swift/libswiftDarwin.dylib",
        "/usr/lib/swift/libswiftDispatch.dylib",
        "/usr/lib/swift/libswiftFoundation.dylib",
        "/usr/lib/swift/libswiftMetal.dylib",
        "/usr/lib/swift/libswiftOSLog.dylib",
        "/usr/lib/swift/libswiftObjectiveC.dylib",
        "/usr/lib/swift/libswiftQuartzCore.dylib",
        "/usr/lib/swift/libswiftUIKit.dylib",
        "/usr/lib/swift/libswiftUniformTypeIdentifiers.dylib",
        "/usr/lib/swift/libswiftXPC.dylib",
        "/usr/lib/swift/libswiftos.dylib",
        "/usr/lib/swift/libswiftsimd.dylib",
    }
)
_IOS_DEVICE_FLUTTER_DEPENDENCIES = _IOS_FLUTTER_COMMON_DEPENDENCIES | {
    "/usr/lib/libc++.1.dylib",
    "/usr/lib/libc++abi.dylib",
}
_IOS_SIMULATOR_FLUTTER_DEPENDENCIES = _IOS_FLUTTER_COMMON_DEPENDENCIES | {
    "/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit",
}
_IOS_DEPENDENCY_METADATA_SHA256 = {
    "ios-device": {
        "Runner": "0335743864247414e084fd239c8ec62d1c0574ce3e69feee2f6e4e6a5d52de0f",
        "Frameworks/App.framework/App": (
            "96968214d4ab1e26c6793f53f215969cc37fe93bac1c5cc802ad219ecf558bd2"
        ),
        "Frameworks/Flutter.framework/Flutter": (
            "8f95839e466e3ee3a67d18b6c05cbf67c4753b7f9597c78dfb23a1efb37e6fb7"
        ),
        _IOS_SHIM_PATH: (
            "e9c4efc18cfe527272d6d4fdc4c0cee3518fae23cf4650ab8903a69b274abee1"
        ),
    },
    "ios-simulator": {
        "Runner": "90536c2fecfd78ba5d4fd4847d12568ecfd4032d484ec22e2b5a353d21f95b27",
        "Frameworks/App.framework/App": (
            "b5c5e8995f2e536e28a8e0249958cb3a1470d457ee750539f07a98fd16bde915"
        ),
        "Frameworks/Flutter.framework/Flutter": (
            "d20d61b816c1d0be013a05f65b7521e4c844d506c6e31b05cd37ff8a2bb79358"
        ),
        _IOS_SHIM_PATH: (
            "e9c4efc18cfe527272d6d4fdc4c0cee3518fae23cf4650ab8903a69b274abee1"
        ),
    },
}
_IOS_LOAD_COMMAND_KINDS_SHA256 = {
    "ios-device": {
        "Runner": "ba8bbc9870babc79cfb548ad7f722371160c3fc4a2b9209d14eebe2728fa2c76",
        "Frameworks/App.framework/App": (
            "9c670fa9ae25a35320b4997e97ec978ab93de31e8797c87ed6eab722c3246f9e"
        ),
        "Frameworks/Flutter.framework/Flutter": (
            "b6355b3308a0fcbfc60f4387ca5e8aa13c802c15b3483a540e7e6b2a982a6b3e"
        ),
        _IOS_SHIM_PATH: (
            "39034fe632b0c7a6e73ea11776e939d07549954727db590f71a74cbdd8313821"
        ),
    },
    "ios-simulator": {
        "Runner": "42050efd422e5ba57de2fc2f9a21cb43de8596752b01e1d3dcbed45badee9118",
        "Frameworks/App.framework/App": (
            "be75ee7ebbb09aebdd3764240114667b533820f2953fd00fb7396b95f1f18643"
        ),
        "Frameworks/Flutter.framework/Flutter": (
            "f06604219919018f63ac78d80481b6e110d36a86c7d5e3ea9950fe8e62c3eaca"
        ),
        _IOS_SHIM_PATH: (
            "39034fe632b0c7a6e73ea11776e939d07549954727db590f71a74cbdd8313821"
        ),
    },
}
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


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _asset_root(application: Path, platform: str) -> Path:
    if platform == "macos":
        return (
            application
            / "Contents/Frameworks/App.framework/Versions/A/Resources/flutter_assets/assets/fonix"
        )
    return application / "Frameworks/App.framework/flutter_assets/assets/fonix"


def _walk_application(
    application: Path, *, label: str
) -> Iterator[tuple[Path, list[str], list[str]]]:
    def traversal_failed(error: OSError) -> None:
        raise AppleApplicationAuditError(
            f"{label} could not inspect every application subtree"
        ) from error

    entry_count = 0
    try:
        walk = os.walk(
            application,
            topdown=True,
            onerror=traversal_failed,
            followlinks=False,
        )
        for directory, directories, files in walk:
            directories.sort()
            files.sort()
            entry_count += len(directories) + len(files)
            if entry_count > _MAX_APPLICATION_ENTRIES:
                raise AppleApplicationAuditError(
                    f"{label} exceeds the application traversal bound"
                )
            yield Path(directory), directories, files
    except AppleApplicationAuditError:
        raise
    except OSError as error:
        traversal_failed(error)


def _application_entry_mode(path: Path, *, label: str) -> int:
    try:
        return path.lstat().st_mode
    except OSError as error:
        raise AppleApplicationAuditError(
            f"{label} could not inspect an application entry"
        ) from error


def _application_file_magic(path: Path, *, label: str) -> bytes:
    try:
        with path.open("rb") as source:
            return source.read(4)
    except OSError as error:
        raise AppleApplicationAuditError(
            f"{label} could not read an application entry"
        ) from error


def _all_named_files(root: Path, name: str) -> list[Path]:
    matches: list[Path] = []
    for directory, directories, files in _walk_application(
        root, label="application metadata inventory"
    ):
        directories[:] = [
            entry
            for entry in directories
            if not stat.S_ISLNK(
                _application_entry_mode(
                    directory / entry,
                    label="application metadata inventory",
                )
            )
        ]
        if name in files:
            matches.append(directory / name)
    return matches


def _platform_contract(platform: str) -> dict[str, Any]:
    contract = _PLATFORM_CONTRACTS.get(platform)
    if contract is None:
        raise AppleApplicationAuditError(
            "platform must be macos, ios-device, or ios-simulator"
        )
    return contract


def _signature_policy(platform: str, signature_policy: str) -> str:
    if signature_policy not in _SIGNATURE_POLICIES:
        raise AppleApplicationAuditError(
            "signature policy must be strict or ios-device-unsigned-development"
        )
    if (
        signature_policy == _IOS_DEVICE_UNSIGNED_SIGNATURE_POLICY
        and platform != "ios-device"
    ):
        raise AppleApplicationAuditError(
            "ios-device-unsigned-development is accepted only for ios-device"
        )
    return signature_policy


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


def _run(
    command: Sequence[str],
    *,
    operation: str | None = None,
    timeout_seconds: int = _COMMAND_TIMEOUT_SECONDS,
) -> str:
    helper = _bounded_process_helper()
    try:
        result = helper.run_bounded(
            command,
            operation=(
                operation
                or f"Apple application {Path(command[0]).name} inspection"
            ),
            environment=_apple_command_environment(),
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=_MAX_COMMAND_OUTPUT_BYTES,
            maximum_stderr_bytes=_MAX_COMMAND_OUTPUT_BYTES,
        )
    except helper.BoundedProcessError as error:
        raise AppleApplicationAuditError(
            f"bounded Apple command failed: {error}"
        ) from error
    return result.stdout


def _run_unchecked(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    helper = _bounded_process_helper()
    operation = f"Apple application {Path(command[0]).name} status inspection"
    try:
        result = helper.run_bounded(
            command,
            operation=operation,
            environment=_apple_command_environment(),
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
            maximum_stdout_bytes=_MAX_COMMAND_OUTPUT_BYTES,
            maximum_stderr_bytes=_MAX_COMMAND_OUTPUT_BYTES,
        )
        return subprocess.CompletedProcess(
            args=tuple(command),
            returncode=0,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except helper.BoundedProcessExitError as error:
        if error.residual_group_members or error.cleanup_failures:
            raise AppleApplicationAuditError(
                f"unchecked Apple command did not settle safely: {error}"
            ) from error
        return subprocess.CompletedProcess(
            args=tuple(command),
            returncode=error.return_code,
            stdout=error.stdout or "",
            stderr=error.stderr or "",
        )
    except helper.BoundedProcessError as error:
        raise AppleApplicationAuditError(
            f"bounded unchecked Apple command failed: {error}"
        ) from error


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


def _macho_load_command_text(
    data: bytes,
    *,
    command_offset: int,
    command_size: int,
    value_offset: int,
    minimum_offset: int,
    binary: Path,
    label: str,
) -> str:
    if value_offset < minimum_offset or value_offset >= command_size:
        raise AppleApplicationAuditError(
            f"invalid Mach-O {label} offset: {binary}"
        )
    raw_value = data[command_offset + value_offset : command_offset + command_size]
    terminator = raw_value.find(b"\0")
    if terminator < 0:
        raise AppleApplicationAuditError(
            f"Mach-O {label} is not NUL-terminated: {binary}"
        )
    try:
        value = raw_value[:terminator].decode("utf-8")
    except UnicodeDecodeError as error:
        raise AppleApplicationAuditError(
            f"Mach-O {label} is not UTF-8: {binary}"
        ) from error
    if not value:
        raise AppleApplicationAuditError(f"Mach-O {label} is empty: {binary}")
    return value


def _macho_slice_facts(data: bytes, binary: Path) -> dict[str, Any]:
    (
        _,
        _,
        cpu_subtype,
        file_type,
        command_count,
        command_bytes,
        header_flags,
        _,
    ) = struct.unpack_from(
        "<IiiIIIII", data, 0
    )
    offset = 32
    commands_end = offset + command_bytes
    dependencies: list[str] = []
    dependency_records: list[dict[str, Any]] = []
    rpaths: list[str] = []
    dylib_id: dict[str, Any] | None = None
    load_command_kinds: list[int] = []
    code_signature_count = 0
    for _ in range(command_count):
        if offset + 8 > commands_end:
            raise AppleApplicationAuditError(
                f"truncated Mach-O load command: {binary}"
            )
        command, command_size = struct.unpack_from("<II", data, offset)
        if command_size < 8 or offset + command_size > commands_end:
            raise AppleApplicationAuditError(
                f"invalid Mach-O load command: {binary}"
            )
        if command in _MACHO_FORBIDDEN_IOS_COMMANDS:
            raise AppleApplicationAuditError(
                f"forbidden or conflicting iOS Mach-O load command 0x{command:x}: "
                f"{binary}"
            )
        if command != _MACHO_CODE_SIGNATURE_COMMAND:
            load_command_kinds.append(command)
        if command in _MACHO_DYLIB_LOAD_COMMANDS:
            if command_size < 24:
                raise AppleApplicationAuditError(
                    f"invalid Mach-O dylib load command: {binary}"
                )
            name_offset, _, current_version, compatibility_version = (
                struct.unpack_from("<IIII", data, offset + 8)
            )
            dependency = _macho_load_command_text(
                data,
                command_offset=offset,
                command_size=command_size,
                value_offset=name_offset,
                minimum_offset=24,
                binary=binary,
                label="dylib dependency",
            )
            if dependency in dependencies:
                raise AppleApplicationAuditError(
                    f"Mach-O dylib dependencies are duplicated: {binary}"
                )
            dependencies.append(dependency)
            dependency_records.append(
                {
                    "kind": _MACHO_DYLIB_LOAD_KINDS[command],
                    "path": dependency,
                    "currentVersion": current_version,
                    "compatibilityVersion": compatibility_version,
                }
            )
        elif command == _MACHO_DYLIB_ID_COMMAND:
            if command_size < 24 or dylib_id is not None:
                raise AppleApplicationAuditError(
                    f"invalid or duplicate Mach-O dylib ID: {binary}"
                )
            name_offset, timestamp, current_version, compatibility_version = (
                struct.unpack_from("<IIII", data, offset + 8)
            )
            dylib_id = {
                "path": _macho_load_command_text(
                    data,
                    command_offset=offset,
                    command_size=command_size,
                    value_offset=name_offset,
                    minimum_offset=24,
                    binary=binary,
                    label="dylib ID",
                ),
                "timestamp": timestamp,
                "currentVersion": current_version,
                "compatibilityVersion": compatibility_version,
            }
        elif command == _MACHO_RPATH_COMMAND:
            if command_size < 12:
                raise AppleApplicationAuditError(
                    f"invalid Mach-O RPATH command: {binary}"
                )
            rpath = _macho_load_command_text(
                data,
                command_offset=offset,
                command_size=command_size,
                value_offset=struct.unpack_from("<I", data, offset + 8)[0],
                minimum_offset=12,
                binary=binary,
                label="RPATH",
            )
            if rpath in rpaths:
                raise AppleApplicationAuditError(
                    f"Mach-O RPATHs are duplicated: {binary}"
                )
            rpaths.append(rpath)
        elif command == _MACHO_CODE_SIGNATURE_COMMAND:
            if command_size != 16:
                raise AppleApplicationAuditError(
                    f"invalid LC_CODE_SIGNATURE command: {binary}"
                )
            data_offset, data_size = struct.unpack_from("<II", data, offset + 8)
            if data_size == 0 or data_offset + data_size > len(data):
                raise AppleApplicationAuditError(
                    f"LC_CODE_SIGNATURE data is out of bounds: {binary}"
                )
            code_signature_count += 1
        offset += command_size
    if offset != commands_end or code_signature_count > 1:
        raise AppleApplicationAuditError(
            f"Mach-O load-command inventory is inconsistent: {binary}"
        )
    return {
        "cpuSubtype": cpu_subtype & 0xFFFFFFFF,
        "fileType": file_type,
        "headerFlags": header_flags,
        "codeSignatureLoadCommands": code_signature_count,
        "dynamicDependencies": dependencies,
        "dylibDependencies": dependency_records,
        "dependencyMetadataSha256": _canonical_json_sha256(dependency_records),
        "loadCommandKindsSha256": _canonical_json_sha256(load_command_kinds),
        "dylibId": dylib_id,
        "rpaths": rpaths,
    }


def _ios_framework_owner(relative_path: str) -> str:
    for framework in _IOS_FRAMEWORKS:
        if relative_path == framework or relative_path.startswith(f"{framework}/"):
            return framework
    return "application"


def _ios_native_profile(
    application: Path, platform: str, executable: str
) -> tuple[str, dict[str, dict[str, Any]], dict[str, str]]:
    if executable != "Runner":
        raise AppleApplicationAuditError(
            "iOS native inventory accepts only the exact Runner reference app"
        )
    common = {
        "Frameworks/App.framework/App": {
            "dependencies": _IOS_APP_FRAMEWORK_DEPENDENCIES,
            "minimumOs": (15, 0, 0),
            "cpuSubtype": _MACHO_ARM64_ALL_SUBTYPE,
            "fileType": _MACHO_DYLIB_FILE_TYPE,
            "headerFlags": (
                0x100085 if platform == "ios-device" else 0x2100085
            ),
            "dylibId": {
                "path": "@rpath/App.framework/App",
                "timestamp": 0 if platform == "ios-device" else 1,
                "currentVersion": 0,
                "compatibilityVersion": 0,
            },
            "rpaths": (
                "@executable_path/Frameworks",
                "@loader_path/Frameworks",
            ),
        },
        "Frameworks/Flutter.framework/Flutter": {
            "dependencies": (
                _IOS_DEVICE_FLUTTER_DEPENDENCIES
                if platform == "ios-device"
                else _IOS_SIMULATOR_FLUTTER_DEPENDENCIES
            ),
            "minimumOs": (15, 0, 0),
            "cpuSubtype": _MACHO_ARM64_ALL_SUBTYPE,
            "fileType": _MACHO_DYLIB_FILE_TYPE,
            "headerFlags": (
                0x2910085 if platform == "ios-device" else 0x2900085
            ),
            "dylibId": {
                "path": "@rpath/Flutter.framework/Flutter",
                "timestamp": 0,
                "currentVersion": 0,
                "compatibilityVersion": 0,
            },
            "rpaths": (),
        },
        _IOS_SHIM_PATH: {
            "dependencies": _IOS_SHIM_DEPENDENCIES,
            "minimumOs": (15, 1, 0),
            "cpuSubtype": _MACHO_ARM64_ALL_SUBTYPE,
            "fileType": _MACHO_DYLIB_FILE_TYPE,
            "headerFlags": 0x910085,
            "dylibId": {
                "path": "@rpath/fonix_shim.framework/fonix_shim",
                "timestamp": 1,
                "currentVersion": 0,
                "compatibilityVersion": 0,
            },
            "rpaths": (),
        },
    }
    if platform == "ios-device":
        binaries = {
            "Runner": {
                "dependencies": _IOS_DEVICE_RUNNER_DEPENDENCIES,
                "minimumOs": (15, 1, 0),
                "cpuSubtype": _MACHO_ARM64_ALL_SUBTYPE,
                "fileType": _MACHO_EXECUTE_FILE_TYPE,
                "headerFlags": 0x210085,
                "dylibId": None,
                "rpaths": (
                    "/usr/lib/swift",
                    "@executable_path/Frameworks",
                ),
            },
            **common,
        }
        for path, facts in binaries.items():
            facts["dependencyMetadataSha256"] = (
                _IOS_DEPENDENCY_METADATA_SHA256[platform][path]
            )
            facts["loadCommandKindsSha256"] = (
                _IOS_LOAD_COMMAND_KINDS_SHA256[platform][path]
            )
        return (
            "ios-device-release",
            binaries,
            {},
        )
    if platform == "ios-simulator":
        binaries = {
            "Runner": {
                "dependencies": _IOS_SIMULATOR_RUNNER_DEPENDENCIES,
                "minimumOs": (15, 1, 0),
                "cpuSubtype": _MACHO_ARM64_ALL_SUBTYPE,
                "fileType": _MACHO_EXECUTE_FILE_TYPE,
                "headerFlags": 0x210085,
                "dylibId": None,
                "rpaths": (
                    "/usr/lib/swift",
                    "@executable_path/Frameworks",
                ),
            },
            **common,
        }
        for path, facts in binaries.items():
            facts["dependencyMetadataSha256"] = (
                _IOS_DEPENDENCY_METADATA_SHA256[platform][path]
            )
            facts["loadCommandKindsSha256"] = (
                _IOS_LOAD_COMMAND_KINDS_SHA256[platform][path]
            )
        return (
            "ios-simulator-debug-no-debug-dylib",
            binaries,
            {},
        )
    raise AppleApplicationAuditError("iOS native inventory received a non-iOS platform")


def _expected_shim_exports(repository: Path) -> tuple[Path, list[str]]:
    export_path = _regular_file(
        repository / "src/fonix_exports.apple", "Apple shim export allowlist"
    )
    data = export_path.read_bytes()
    if not data or len(data) > 128 * 1024:
        raise AppleApplicationAuditError(
            "Apple shim export allowlist is empty or oversized"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AppleApplicationAuditError(
            "Apple shim export allowlist is not UTF-8"
        ) from error
    if not text.endswith("\n"):
        raise AppleApplicationAuditError(
            "Apple shim export allowlist must end with one newline"
        )
    exports = text.splitlines()
    if (
        not exports
        or len(exports) > 256
        or len(exports) != len(set(exports))
        or any(_APPLE_EXPORT.fullmatch(symbol) is None for symbol in exports)
    ):
        raise AppleApplicationAuditError(
            "Apple shim export allowlist is not a closed unique symbol list"
        )
    return export_path, exports


def _symbol_set_sha256(symbols: Sequence[str]) -> str:
    encoded = ("\n".join(sorted(symbols)) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_dyld_exports(output: str, binary: Path) -> list[str]:
    lines = output.splitlines()
    if len(lines) < 4 or len(lines) > 260:
        raise AppleApplicationAuditError(
            f"dyld runtime export inventory is empty or oversized: {binary}"
        )
    if not lines[0].rstrip().endswith(":"):
        raise AppleApplicationAuditError(
            f"dyld runtime export inventory has an invalid heading: {binary}"
        )
    if lines[1].strip() != "-exports:" or lines[2].split() != ["offset", "symbol"]:
        raise AppleApplicationAuditError(
            f"dyld runtime export inventory has an invalid schema: {binary}"
        )
    exports: list[str] = []
    pattern = re.compile(r"^\s*0x[0-9A-Fa-f]+\s+(_dort_[a-z0-9_]{1,120})\s*$")
    for line in lines[3:]:
        match = pattern.fullmatch(line)
        if match is None:
            raise AppleApplicationAuditError(
                f"dyld runtime export inventory has an invalid row: {binary}"
            )
        exports.append(match.group(1))
    if not exports or len(exports) != len(set(exports)):
        raise AppleApplicationAuditError(
            f"dyld runtime export inventory is empty or duplicated: {binary}"
        )
    return exports


def _audit_shim_exports(
    shim: Path, repository: Path, *, nm: str, dyld_info: str
) -> dict[str, Any]:
    export_path, expected_exports = _expected_shim_exports(repository)
    actual_exports = _run((nm, "-gjU", str(shim))).splitlines()
    if (
        not actual_exports
        or len(actual_exports) > 256
        or len(actual_exports) != len(set(actual_exports))
        or any(_APPLE_EXPORT.fullmatch(symbol) is None for symbol in actual_exports)
    ):
        raise AppleApplicationAuditError(
            "packaged iOS shim exports are not a closed unique symbol list"
        )
    if set(actual_exports) != set(expected_exports):
        raise AppleApplicationAuditError(
            "packaged iOS shim exports differ from src/fonix_exports.apple; "
            f"missing={sorted(set(expected_exports) - set(actual_exports))}, "
            f"extra={sorted(set(actual_exports) - set(expected_exports))}"
        )
    runtime_exports = _parse_dyld_exports(
        _run((dyld_info, "-exports", str(shim))), shim
    )
    if set(runtime_exports) != set(expected_exports):
        raise AppleApplicationAuditError(
            "packaged iOS shim dyld exports differ from src/fonix_exports.apple; "
            f"missing={sorted(set(expected_exports) - set(runtime_exports))}, "
            f"extra={sorted(set(runtime_exports) - set(expected_exports))}"
        )
    return {
        "allowlist": "src/fonix_exports.apple",
        "allowlistSha256": _sha256(export_path),
        "symbolCount": len(expected_exports),
        "nlistSymbolSetSha256": _symbol_set_sha256(actual_exports),
        "dyldExportSetSha256": _symbol_set_sha256(runtime_exports),
    }


def _inventory_ios_application(
    application: Path,
    executable: str,
    *,
    platform: str,
    architecture: str,
    platform_number: int,
    maximum_os: tuple[int, int, int],
    repository: Path,
    otool: str,
    nm: str,
    dyld_info: str,
) -> dict[str, Any]:
    profile_name, binary_profile, rpath_aliases = _ios_native_profile(
        application, platform, executable
    )
    framework_paths: set[str] = set()
    macho_paths: list[Path] = []
    for parent, directories, files in _walk_application(
        application, label="iOS native inventory"
    ):
        retained_directories: list[str] = []
        for name in directories:
            path = parent / name
            relative = path.relative_to(application).as_posix()
            if "onnxruntime" in name.casefold():
                raise AppleApplicationAuditError(
                    f"iOS app separately packages ONNX Runtime: {relative}"
                )
            if name.casefold().endswith(".framework"):
                framework_paths.add(relative)
            if stat.S_ISLNK(
                _application_entry_mode(path, label="iOS native inventory")
            ):
                if relative == "Frameworks" or relative.startswith("Frameworks/"):
                    raise AppleApplicationAuditError(
                        f"iOS native inventory contains a symlink: {relative}"
                    )
                continue
            retained_directories.append(name)
        directories[:] = retained_directories
        for name in files:
            path = parent / name
            relative = path.relative_to(application).as_posix()
            if "onnxruntime" in name.casefold():
                raise AppleApplicationAuditError(
                    f"iOS app separately packages ONNX Runtime: {relative}"
                )
            mode = _application_entry_mode(path, label="iOS native inventory")
            if stat.S_ISLNK(mode):
                if relative.startswith("Frameworks/"):
                    raise AppleApplicationAuditError(
                        f"iOS native inventory contains a symlink: {relative}"
                    )
                continue
            if not stat.S_ISREG(mode):
                if relative.startswith("Frameworks/"):
                    raise AppleApplicationAuditError(
                        f"iOS native inventory contains a special file: {relative}"
                    )
                continue
            magic = _application_file_magic(path, label="iOS native inventory")
            if magic in _MACHO_MAGICS:
                macho_paths.append(path)
                if len(macho_paths) > _MAX_IOS_MACHO_FILES:
                    raise AppleApplicationAuditError(
                        "iOS Mach-O inventory exceeds the audit bound"
                    )

    expected_frameworks = set(_IOS_FRAMEWORKS)
    if framework_paths != expected_frameworks:
        raise AppleApplicationAuditError(
            "iOS app framework inventory is not closed; "
            f"expected={sorted(expected_frameworks)}, actual={sorted(framework_paths)}"
        )
    expected_macho_paths = set(binary_profile)
    actual_macho_paths = {
        path.relative_to(application).as_posix() for path in macho_paths
    }
    if actual_macho_paths != expected_macho_paths:
        raise AppleApplicationAuditError(
            "iOS app Mach-O inventory is not closed; "
            f"expected={sorted(expected_macho_paths)}, actual={sorted(actual_macho_paths)}"
        )

    binary_records: list[dict[str, Any]] = []
    for binary in sorted(macho_paths, key=lambda path: path.as_posix()):
        relative = binary.relative_to(application).as_posix()
        slices = _macho_slices(binary)
        if set(slices) != {architecture}:
            raise AppleApplicationAuditError(
                f"iOS Mach-O architectures do not match {architecture}: {relative}"
            )
        build_versions = _parse_macho_build_versions(
            _run((otool, "-l", str(binary))), binary
        )
        if len(build_versions) != 1 or build_versions[0][0] != platform_number:
            raise AppleApplicationAuditError(
                f"iOS Mach-O platform is wrong or ambiguous: {relative}"
            )
        binary_floor = build_versions[0][1]
        if binary_floor > maximum_os:
            raise AppleApplicationAuditError(
                "iOS Mach-O deployment floor exceeds the declared/plist floor: "
                f"{relative}"
            )
        facts = _macho_slice_facts(slices[architecture], binary)
        expected_facts = binary_profile[relative]
        if binary_floor != expected_facts["minimumOs"]:
            raise AppleApplicationAuditError(
                f"iOS Mach-O deployment floor is not exact for {relative}; "
                f"expected={expected_facts['minimumOs']}, actual={binary_floor}"
            )
        for field in (
            "cpuSubtype",
            "fileType",
            "headerFlags",
            "dylibId",
            "dependencyMetadataSha256",
            "loadCommandKindsSha256",
        ):
            if facts.get(field) != expected_facts[field]:
                raise AppleApplicationAuditError(
                    f"iOS Mach-O {field} is not exact for {relative}; "
                    f"expected={expected_facts[field]!r}, actual={facts.get(field)!r}"
                )
        dependencies = set(
            _array(facts.get("dynamicDependencies"), "Mach-O dynamic dependencies")
        )
        expected_dependencies = set(expected_facts["dependencies"])
        if dependencies != expected_dependencies:
            raise AppleApplicationAuditError(
                f"iOS Mach-O dependencies are not closed for {relative}; "
                f"missing={sorted(expected_dependencies - dependencies)}, "
                f"extra={sorted(dependencies - expected_dependencies)}"
            )
        rpaths = _array(facts.get("rpaths"), "Mach-O RPATHs")
        expected_rpaths = list(expected_facts["rpaths"])
        if rpaths != expected_rpaths:
            raise AppleApplicationAuditError(
                f"iOS Mach-O ordered RPATHs are not exact for {relative}; "
                f"expected={expected_rpaths!r}, actual={rpaths!r}"
            )
        facts["dynamicDependencies"] = sorted(dependencies)
        facts["rpaths"] = [rpath_aliases.get(path, path) for path in rpaths]
        binary_records.append(
            {
                "path": relative,
                "owner": _ios_framework_owner(relative),
                "architecture": architecture,
                "machoPlatform": platform_number,
                "minimumOs": ".".join(str(part) for part in binary_floor),
                **facts,
            }
        )

    frameworks = []
    for framework in _IOS_FRAMEWORKS:
        owned_binaries = [
            record["path"]
            for record in binary_records
            if record["owner"] == framework
        ]
        if len(owned_binaries) != 1:
            raise AppleApplicationAuditError(
                f"iOS framework does not own exactly one Mach-O: {framework}"
            )
        frameworks.append({"path": framework, "machOBinaries": owned_binaries})
    return {
        "profile": profile_name,
        "frameworks": frameworks,
        "machOBinaries": binary_records,
        "shimExports": _audit_shim_exports(
            application / _IOS_SHIM_PATH,
            repository,
            nm=nm,
            dyld_info=dyld_info,
        ),
    }


def _named_application_entries(application: Path, name: str) -> list[str]:
    expected = name.casefold()
    matches: list[str] = []
    for parent, directories, files in _walk_application(
        application, label="iOS signature inventory"
    ):
        for entry in (*directories, *files):
            if entry.casefold() == expected:
                matches.append((parent / entry).relative_to(application).as_posix())
        directories[:] = [
            entry
            for entry in directories
            if not stat.S_ISLNK(
                _application_entry_mode(
                    parent / entry,
                    label="iOS signature inventory",
                )
            )
        ]
    return sorted(matches)


def _codesign_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _require_unsigned_code_object(codesign: str, path: Path, label: str) -> None:
    result = _run_unchecked((codesign, "-d", "--verbose=4", str(path)))
    output = _codesign_output(result)
    if result.returncode == 0 or "code object is not signed at all" not in output:
        raise AppleApplicationAuditError(
            f"{label} is signed or its unsigned state could not be established"
        )


def _require_adhoc_code_object(codesign: str, path: Path, label: str) -> None:
    _run((codesign, "--verify", "--strict", str(path)))
    result = _run_unchecked((codesign, "-d", "--verbose=4", str(path)))
    if result.returncode != 0:
        raise AppleApplicationAuditError(f"could not inspect {label} signing identity")
    output = _codesign_output(result)
    lines = [line.strip() for line in output.splitlines()]
    if (
        lines.count("Signature=adhoc") != 1
        or lines.count("TeamIdentifier=not set") != 1
        or not any("flags=0x2(adhoc)" in line for line in lines)
        or any(line.startswith("Authority=") for line in lines)
    ):
        raise AppleApplicationAuditError(
            f"{label} is not an exact teamless ad-hoc signature"
        )


def _audit_unsigned_ios_device_signing(
    application: Path,
    executable: str,
    native_inventory: dict[str, Any],
    *,
    codesign: str,
) -> dict[str, Any]:
    if _named_application_entries(application, "embedded.mobileprovision"):
        raise AppleApplicationAuditError(
            "unsigned iOS development app must not embed a provisioning profile"
        )
    expected_signature_directories = {
        f"{framework}/_CodeSignature" for framework in _IOS_FRAMEWORKS
    }
    actual_signature_directories = set(
        _named_application_entries(application, "_CodeSignature")
    )
    if actual_signature_directories != expected_signature_directories:
        raise AppleApplicationAuditError(
            "unsigned iOS development signature inventory is not closed; "
            f"expected={sorted(expected_signature_directories)}, "
            f"actual={sorted(actual_signature_directories)}"
        )

    records = _array(
        native_inventory.get("machOBinaries"), "native inventory Mach-O binaries"
    )
    by_path = {
        _string(_object(record, "Mach-O record").get("path"), "Mach-O path"): record
        for record in records
    }
    root_record = _object(by_path.get(executable), "root Mach-O record")
    if root_record.get("codeSignatureLoadCommands") != 0:
        raise AppleApplicationAuditError(
            "unsigned iOS root executable contains LC_CODE_SIGNATURE"
        )
    _require_unsigned_code_object(codesign, application / executable, "root executable")
    _require_unsigned_code_object(codesign, application, "root application bundle")

    nested_frameworks: list[dict[str, Any]] = []
    for framework in _IOS_FRAMEWORKS:
        binary_name = Path(framework).stem
        binary_relative = f"{framework}/{binary_name}"
        record = _object(
            by_path.get(binary_relative), f"{framework} Mach-O record"
        )
        if record.get("codeSignatureLoadCommands") != 1:
            raise AppleApplicationAuditError(
                f"expected nested ad-hoc signature is missing: {binary_relative}"
            )
        framework_path = application / framework
        binary_path = application / binary_relative
        _require_adhoc_code_object(codesign, framework_path, framework)
        _require_adhoc_code_object(codesign, binary_path, binary_relative)
        nested_frameworks.append(
            {
                "path": framework,
                "binary": binary_relative,
                "status": "verified-adhoc",
                "teamIdentifier": None,
                "authorities": [],
            }
        )
    return {
        "rootBundle": "unsigned",
        "rootExecutable": "unsigned",
        "provisioningProfile": "absent",
        "nestedFrameworks": nested_frameworks,
    }


def _audit_strict_ios_signing(
    application: Path,
    executable: str,
    native_inventory: dict[str, Any],
    *,
    codesign: str,
) -> dict[str, Any]:
    records = [
        _object(record, "Mach-O record")
        for record in _array(
            native_inventory.get("machOBinaries"),
            "native inventory Mach-O binaries",
        )
    ]
    verified_code_objects: list[str] = []
    for record in records:
        relative = _string(record.get("path"), "Mach-O path")
        if record.get("codeSignatureLoadCommands") != 1:
            raise AppleApplicationAuditError(
                f"strict iOS Mach-O is missing LC_CODE_SIGNATURE: {relative}"
            )
        _run((codesign, "--verify", "--strict", str(application / relative)))
        verified_code_objects.append(relative)
    for framework in _IOS_FRAMEWORKS:
        _run((codesign, "--verify", "--strict", str(application / framework)))
        verified_code_objects.append(framework)
    _run((codesign, "--verify", "--strict", str(application)))
    if executable not in verified_code_objects:
        raise AppleApplicationAuditError(
            "strict iOS signature inventory does not include the root executable"
        )
    return {
        "rootBundle": "verified",
        "rootExecutable": "verified",
        "verifiedCodeObjects": sorted(verified_code_objects),
    }


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
    maximum_os: tuple[int, int, int] | None = None,
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
    if maximum_os is not None and binary_floor > maximum_os:
        raise AppleApplicationAuditError(
            f"Mach-O deployment floor exceeds the declared/plist floor: {binary}"
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


def _macho_ascii_name(raw: bytes, binary: Path, label: str) -> str:
    try:
        return raw.split(b"\0", 1)[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise AppleApplicationAuditError(
            f"Mach-O {label} is not ASCII: {binary}"
        ) from error


def _macho_payload_sha256(
    data: bytes, offset: int, size: int, binary: Path, label: str
) -> str | None:
    if size == 0:
        if offset > len(data):
            raise AppleApplicationAuditError(
                f"Mach-O empty {label} offset is out of bounds: {binary}"
            )
        return None
    if offset == 0 or offset + size > len(data):
        raise AppleApplicationAuditError(
            f"Mach-O {label} payload is out of bounds: {binary}"
        )
    return hashlib.sha256(data[offset : offset + size]).hexdigest()


def _macho_table_payload(
    data: bytes,
    offset: int,
    count: int,
    entry_size: int,
    binary: Path,
    label: str,
) -> bytes:
    size = count * entry_size
    if count == 0:
        if offset > len(data):
            raise AppleApplicationAuditError(
                f"Mach-O empty {label} offset is out of bounds: {binary}"
            )
        return b""
    if offset == 0 or size > len(data) - offset:
        raise AppleApplicationAuditError(
            f"Mach-O {label} payload is out of bounds: {binary}"
        )
    return data[offset : offset + size]


def _macho_string_value(strings: bytes, index: int, binary: Path, label: str) -> bytes:
    if index >= len(strings):
        raise AppleApplicationAuditError(
            f"Mach-O {label} string index is out of bounds: {binary}"
        )
    terminator = strings.find(b"\0", index)
    if terminator < 0:
        raise AppleApplicationAuditError(
            f"Mach-O {label} string is not NUL-terminated: {binary}"
        )
    return strings[index:terminator]


def _macho_nlist_record(
    data: bytes,
    binary: Path,
    symbol_offset: int,
    symbol_count: int,
    strings: bytes,
    index: int,
) -> tuple[int, dict[str, Any]]:
    if index >= symbol_count:
        raise AppleApplicationAuditError(
            f"Mach-O symbol index is out of bounds: {binary}"
        )
    string_index, symbol_type, section, description, value = struct.unpack_from(
        "<IBBHQ", data, symbol_offset + index * 16
    )
    name = _macho_string_value(strings, string_index, binary, "symbol")
    return string_index, {
        "nameSha256": hashlib.sha256(name).hexdigest(),
        "type": symbol_type,
        "section": section,
        "description": description,
        "value": value,
    }


def _macho_retained_symbol_identity(
    data: bytes,
    binary: Path,
    symtab: dict[str, int],
    dysymtab: dict[str, int],
) -> dict[str, str]:
    symbol_offset = _integer(symtab.get("symbolOffset"), "Mach-O symbol offset")
    symbol_count = _integer(symtab.get("symbolCount"), "Mach-O symbol count")
    string_offset = _integer(symtab.get("stringOffset"), "Mach-O string offset")
    string_size = _integer(symtab.get("stringSize"), "Mach-O string size")
    if (
        symbol_offset + symbol_count * 16 > len(data)
        or string_offset + string_size > len(data)
    ):
        raise AppleApplicationAuditError(
            f"Mach-O symbol or string table is out of bounds: {binary}"
        )
    strings = data[string_offset : string_offset + string_size]
    records: list[dict[str, Any]] = []
    string_semantics: list[dict[str, Any]] = []
    string_aliases: dict[int, int] = {}
    for category, index_key, count_key in (
        ("external", "externalIndex", "externalCount"),
        ("undefined", "undefinedIndex", "undefinedCount"),
    ):
        start = _integer(dysymtab.get(index_key), f"Mach-O {category} symbol index")
        count = _integer(dysymtab.get(count_key), f"Mach-O {category} symbol count")
        if count > 65536 or start + count > symbol_count:
            raise AppleApplicationAuditError(
                f"Mach-O {category} symbol range is invalid: {binary}"
            )
        for index in range(start, start + count):
            string_index, record = _macho_nlist_record(
                data,
                binary,
                symbol_offset,
                symbol_count,
                strings,
                index,
            )
            records.append(
                {
                    "category": category,
                    **record,
                }
            )
            alias = string_aliases.setdefault(string_index, len(string_aliases))
            string_semantics.append(
                {
                    "category": category,
                    "ordinal": index - start,
                    "alias": alias,
                    "nameSha256": record["nameSha256"],
                }
            )
    return {
        "symbolsSha256": _canonical_json_sha256(records),
        "stringsSha256": _canonical_json_sha256(string_semantics),
    }


def _macho_dysymtab_payload_identity(
    data: bytes,
    binary: Path,
    symtab: dict[str, int],
    dysymtab: dict[str, int],
) -> dict[str, dict[str, Any]]:
    symbol_offset = _integer(symtab.get("symbolOffset"), "Mach-O symbol offset")
    symbol_count = _integer(symtab.get("symbolCount"), "Mach-O symbol count")
    string_offset = _integer(symtab.get("stringOffset"), "Mach-O string offset")
    string_size = _integer(symtab.get("stringSize"), "Mach-O string size")
    if (
        symbol_count > len(data) // 16
        or symbol_offset + symbol_count * 16 > len(data)
        or string_offset + string_size > len(data)
    ):
        raise AppleApplicationAuditError(
            f"Mach-O symbol or string table is out of bounds: {binary}"
        )
    strings = data[string_offset : string_offset + string_size]
    ranges = {
        category: (
            _integer(dysymtab.get(index_key), f"Mach-O {category} symbol index"),
            _integer(dysymtab.get(count_key), f"Mach-O {category} symbol count"),
        )
        for category, index_key, count_key in (
            ("local", "localIndex", "localCount"),
            ("external", "externalIndex", "externalCount"),
            ("undefined", "undefinedIndex", "undefinedCount"),
        )
    }
    for category, (start, count) in ranges.items():
        if start + count > symbol_count:
            raise AppleApplicationAuditError(
                f"Mach-O {category} symbol range is invalid: {binary}"
            )

    def symbol_key(index: int, label: str) -> dict[str, Any]:
        for category, (start, count) in ranges.items():
            if start <= index < start + count:
                if category != "local":
                    return {"category": category, "ordinal": index - start}
                _, record = _macho_nlist_record(
                    data,
                    binary,
                    symbol_offset,
                    symbol_count,
                    strings,
                    index,
                )
                return {
                    "category": "local",
                    "symbolSha256": _canonical_json_sha256(record),
                }
        raise AppleApplicationAuditError(
            f"Mach-O {label} references an uncategorized symbol: {binary}"
        )

    def table(
        offset_key: str, count_key: str, entry_size: int, label: str
    ) -> tuple[bytes, int]:
        offset = _integer(dysymtab.get(offset_key), f"Mach-O {label} offset")
        count = _integer(dysymtab.get(count_key), f"Mach-O {label} count")
        return (
            _macho_table_payload(
                data, offset, count, entry_size, binary, label
            ),
            count,
        )

    table_of_contents, toc_count = table(
        "tocOffset", "tocCount", 8, "dynamic table of contents"
    )
    module_count = _integer(
        dysymtab.get("moduleTableCount"), "Mach-O module table count"
    )
    toc_records: list[dict[str, Any]] = []
    for index in range(toc_count):
        symbol_index, module_index = struct.unpack_from(
            "<II", table_of_contents, index * 8
        )
        if module_index >= module_count:
            raise AppleApplicationAuditError(
                f"Mach-O table of contents module index is invalid: {binary}"
            )
        toc_records.append(
            {
                "symbol": symbol_key(symbol_index, "table of contents"),
                "moduleIndex": module_index,
            }
        )

    module_table, parsed_module_count = table(
        "moduleTableOffset", "moduleTableCount", 56, "module table"
    )
    external_reference_count = _integer(
        dysymtab.get("externalReferenceCount"),
        "Mach-O external-reference count",
    )
    external_relocation_count = _integer(
        dysymtab.get("externalRelocationCount"),
        "Mach-O external-relocation count",
    )

    def checked_table_range(
        start: int, count: int, maximum: int, label: str
    ) -> None:
        if count and start + count > maximum:
            raise AppleApplicationAuditError(
                f"Mach-O module {label} range is invalid: {binary}"
            )

    module_records: list[dict[str, Any]] = []
    for index in range(parsed_module_count):
        fields = struct.unpack_from("<12IQ", module_table, index * 56)
        (
            module_name,
            external_start,
            external_count,
            reference_start,
            reference_count,
            local_start,
            local_count,
            relocation_start,
            relocation_count,
            init_term_start,
            init_term_count,
            objc_info_size,
            objc_info_address,
        ) = fields
        checked_table_range(
            reference_start,
            reference_count,
            external_reference_count,
            "external-reference",
        )
        checked_table_range(
            relocation_start,
            relocation_count,
            external_relocation_count,
            "external-relocation",
        )
        external_range_start, external_range_count = ranges["external"]
        if external_count and (
            external_start < external_range_start
            or external_start + external_count
            > external_range_start + external_range_count
        ):
            raise AppleApplicationAuditError(
                f"Mach-O module external-symbol range is invalid: {binary}"
            )
        local_range_start, local_range_count = ranges["local"]
        if local_count and (
            local_start < local_range_start
            or local_start + local_count > local_range_start + local_range_count
        ):
            raise AppleApplicationAuditError(
                f"Mach-O module local-symbol range is invalid: {binary}"
            )
        module_records.append(
            {
                "nameSha256": hashlib.sha256(
                    _macho_string_value(strings, module_name, binary, "module")
                ).hexdigest(),
                "externalStart": (
                    external_start - external_range_start
                    if external_count
                    else external_start
                ),
                "externalCount": external_count,
                "referenceStart": reference_start,
                "referenceCount": reference_count,
                "localStart": local_start,
                "localCount": local_count,
                "localSymbols": [
                    symbol_key(symbol_index, "module local-symbol table")
                    for symbol_index in range(
                        local_start, local_start + local_count
                    )
                ],
                "relocationStart": relocation_start,
                "relocationCount": relocation_count,
                "initTermStart": init_term_start,
                "initTermCount": init_term_count,
                "objcInfoSize": objc_info_size,
                "objcInfoAddress": objc_info_address,
            }
        )

    external_references, parsed_reference_count = table(
        "externalReferenceOffset",
        "externalReferenceCount",
        4,
        "external-reference table",
    )
    external_reference_records = []
    for index in range(parsed_reference_count):
        value = struct.unpack_from("<I", external_references, index * 4)[0]
        external_reference_records.append(
            {
                "symbol": symbol_key(
                    value & 0x00FFFFFF, "external-reference table"
                ),
                "flags": value >> 24,
            }
        )

    indirect_symbols, indirect_count = table(
        "indirectSymbolOffset",
        "indirectSymbolCount",
        4,
        "indirect-symbol table",
    )
    indirect_records: list[dict[str, Any]] = []
    for index in range(indirect_count):
        value = struct.unpack_from("<I", indirect_symbols, index * 4)[0]
        if value & 0xC0000000:
            indirect_records.append({"special": value})
        else:
            indirect_records.append(
                {"symbol": symbol_key(value, "indirect-symbol table")}
            )

    def relocation_identity(
        payload: bytes, count: int, label: str
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index in range(count):
            address, attributes = struct.unpack_from("<II", payload, index * 8)
            if address & 0x80000000:
                records.append(
                    {"scatteredAddressAndFlags": address, "value": attributes}
                )
                continue
            is_external = bool((attributes >> 27) & 1)
            symbol_number = attributes & 0x00FFFFFF
            records.append(
                {
                    "address": address,
                    "target": (
                        symbol_key(symbol_number, label)
                        if is_external
                        else {"sectionOrdinal": symbol_number}
                    ),
                    "pcRelative": (attributes >> 24) & 1,
                    "length": (attributes >> 25) & 0x3,
                    "external": is_external,
                    "type": attributes >> 28,
                }
            )
        return records

    external_relocations, parsed_external_relocation_count = table(
        "externalRelocationOffset",
        "externalRelocationCount",
        8,
        "external-relocation table",
    )
    local_relocations, local_relocation_count = table(
        "localRelocationOffset",
        "localRelocationCount",
        8,
        "local-relocation table",
    )

    def identity(records: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "entryCount": len(records),
            "recordsSha256": _canonical_json_sha256(records),
        }

    return {
        "tableOfContents": identity(toc_records),
        "moduleTable": identity(module_records),
        "externalReferences": identity(external_reference_records),
        "indirectSymbols": identity(indirect_records),
        "externalRelocations": identity(
            relocation_identity(
                external_relocations,
                parsed_external_relocation_count,
                "external-relocation table",
            )
        ),
        "localRelocations": identity(
            relocation_identity(
                local_relocations,
                local_relocation_count,
                "local-relocation table",
            )
        ),
    }


def _macho_normalized_runtime_identity(
    data: bytes, binary: Path
) -> dict[str, Any]:
    (
        _,
        cpu_type,
        cpu_subtype,
        file_type,
        command_count,
        command_bytes,
        header_flags,
        reserved,
    ) = struct.unpack_from("<IiiIIIII", data, 0)
    offset = 32
    commands_end = offset + command_bytes
    segments: list[dict[str, Any]] = []
    immutable_commands: list[dict[str, Any]] = []
    dylib_id: dict[str, Any] | None = None
    load_command_uuid: str | None = None
    symtab: dict[str, int] | None = None
    dysymtab: dict[str, int] | None = None
    code_signature: dict[str, int] | None = None
    for _ in range(command_count):
        if offset + 8 > commands_end:
            raise AppleApplicationAuditError(
                f"truncated normalized Mach-O load command: {binary}"
            )
        command, command_size = struct.unpack_from("<II", data, offset)
        if command_size < 8 or offset + command_size > commands_end:
            raise AppleApplicationAuditError(
                f"invalid normalized Mach-O load command: {binary}"
            )
        command_data = data[offset : offset + command_size]
        if command == _MACHO_SEGMENT_64_COMMAND:
            if command_size < 72:
                raise AppleApplicationAuditError(
                    f"invalid normalized LC_SEGMENT_64: {binary}"
                )
            (
                segment_name_bytes,
                vm_address,
                vm_size,
                file_offset,
                file_size,
                maximum_protection,
                initial_protection,
                section_count,
                segment_flags,
            ) = struct.unpack_from("<16sQQQQiiII", data, offset + 8)
            if section_count > 256 or command_size != 72 + section_count * 80:
                raise AppleApplicationAuditError(
                    f"invalid normalized Mach-O section count: {binary}"
                )
            segment_name = _macho_ascii_name(
                segment_name_bytes, binary, "segment name"
            )
            if any(segment["name"] == segment_name for segment in segments):
                raise AppleApplicationAuditError(
                    f"duplicate normalized Mach-O segment: {binary}"
                )
            if file_offset + file_size > len(data):
                raise AppleApplicationAuditError(
                    f"normalized Mach-O segment is out of bounds: {binary}"
                )
            sections: list[dict[str, Any]] = []
            for section_index in range(section_count):
                section_offset = offset + 72 + section_index * 80
                section = struct.unpack_from(
                    "<16s16sQQIIIIIIII", data, section_offset
                )
                section_name = _macho_ascii_name(
                    section[0], binary, "section name"
                )
                declared_segment = _macho_ascii_name(
                    section[1], binary, "section segment name"
                )
                if declared_segment != segment_name:
                    raise AppleApplicationAuditError(
                        f"normalized Mach-O section segment mismatch: {binary}"
                    )
                (
                    section_address,
                    section_size,
                    section_file_offset,
                    alignment,
                    relocation_offset,
                    relocation_count,
                    section_flags,
                    reserved1,
                    reserved2,
                    reserved3,
                ) = section[2:]
                if section_size and (
                    section_address < vm_address
                    or section_address + section_size > vm_address + vm_size
                ):
                    raise AppleApplicationAuditError(
                        f"normalized Mach-O section VM range is invalid: {binary}"
                    )
                section_type = section_flags & 0xFF
                is_zero_fill = section_type in {1, 12, 18}
                if is_zero_fill:
                    section_sha256 = None
                else:
                    if section_file_offset + section_size > len(data):
                        raise AppleApplicationAuditError(
                            f"normalized Mach-O section is out of bounds: {binary}"
                        )
                    section_sha256 = hashlib.sha256(
                        data[
                            section_file_offset : section_file_offset + section_size
                        ]
                    ).hexdigest()
                sections.append(
                    {
                        "name": section_name,
                        "segment": declared_segment,
                        "address": section_address,
                        "size": section_size,
                        "fileOffset": section_file_offset,
                        "alignment": alignment,
                        "relocationOffset": relocation_offset,
                        "relocationCount": relocation_count,
                        "flags": section_flags,
                        "reserved1": reserved1,
                        "reserved2": reserved2,
                        "reserved3": reserved3,
                        "bytesSha256": section_sha256,
                    }
                )
            segments.append(
                {
                    "name": segment_name,
                    "vmAddress": vm_address,
                    "vmSize": vm_size,
                    "fileOffset": file_offset,
                    "fileSize": file_size,
                    "maximumProtection": maximum_protection,
                    "initialProtection": initial_protection,
                    "flags": segment_flags,
                    "sections": sections,
                }
            )
        elif command == _MACHO_DYLIB_ID_COMMAND:
            if command_size < 24 or dylib_id is not None:
                raise AppleApplicationAuditError(
                    f"invalid normalized Mach-O dylib ID: {binary}"
                )
            name_offset, timestamp, current_version, compatibility_version = (
                struct.unpack_from("<IIII", data, offset + 8)
            )
            dylib_id = {
                "path": _macho_load_command_text(
                    data,
                    command_offset=offset,
                    command_size=command_size,
                    value_offset=name_offset,
                    minimum_offset=24,
                    binary=binary,
                    label="normalized dylib ID",
                ),
                "timestamp": timestamp,
                "currentVersion": current_version,
                "compatibilityVersion": compatibility_version,
            }
        elif command == _MACHO_UUID_COMMAND:
            if command_size != 24 or load_command_uuid is not None:
                raise AppleApplicationAuditError(
                    f"invalid or duplicate normalized LC_UUID: {binary}"
                )
            load_command_uuid = command_data[8:24].hex()
        elif command == _MACHO_SYMTAB_COMMAND:
            if command_size != 24 or symtab is not None:
                raise AppleApplicationAuditError(
                    f"invalid normalized LC_SYMTAB: {binary}"
                )
            symbol_offset, symbol_count, string_offset, string_size = (
                struct.unpack_from("<IIII", data, offset + 8)
            )
            symtab = {
                "symbolOffset": symbol_offset,
                "symbolCount": symbol_count,
                "stringOffset": string_offset,
                "stringSize": string_size,
            }
        elif command == _MACHO_DYSYMTAB_COMMAND:
            if command_size != 80 or dysymtab is not None:
                raise AppleApplicationAuditError(
                    f"invalid normalized LC_DYSYMTAB: {binary}"
                )
            fields = struct.unpack_from("<18I", data, offset + 8)
            names = (
                "localIndex",
                "localCount",
                "externalIndex",
                "externalCount",
                "undefinedIndex",
                "undefinedCount",
                "tocOffset",
                "tocCount",
                "moduleTableOffset",
                "moduleTableCount",
                "externalReferenceOffset",
                "externalReferenceCount",
                "indirectSymbolOffset",
                "indirectSymbolCount",
                "externalRelocationOffset",
                "externalRelocationCount",
                "localRelocationOffset",
                "localRelocationCount",
            )
            dysymtab = dict(zip(names, fields, strict=True))
        elif command == _MACHO_CODE_SIGNATURE_COMMAND:
            if command_size != 16 or code_signature is not None:
                raise AppleApplicationAuditError(
                    f"invalid normalized LC_CODE_SIGNATURE: {binary}"
                )
            data_offset, data_size = struct.unpack_from("<II", data, offset + 8)
            _macho_payload_sha256(
                data, data_offset, data_size, binary, "code signature"
            )
            code_signature = {"offset": data_offset, "size": data_size}
        else:
            normalized_command: dict[str, Any] = {
                "command": command,
                "commandSize": command_size,
                "commandSha256": hashlib.sha256(command_data).hexdigest(),
            }
            if command in _MACHO_LINKEDIT_DATA_COMMANDS:
                if command_size != 16:
                    raise AppleApplicationAuditError(
                        f"invalid normalized linkedit-data command: {binary}"
                    )
                data_offset, data_size = struct.unpack_from("<II", data, offset + 8)
                normalized_command["payloadSha256"] = _macho_payload_sha256(
                    data,
                    data_offset,
                    data_size,
                    binary,
                    f"load command 0x{command:x}",
                )
            elif command in _MACHO_DYLD_INFO_COMMANDS:
                if command_size != 48:
                    raise AppleApplicationAuditError(
                        f"invalid normalized dyld-info command: {binary}"
                    )
                values = struct.unpack_from("<10I", data, offset + 8)
                payloads: list[str | None] = []
                for index in range(0, len(values), 2):
                    payloads.append(
                        _macho_payload_sha256(
                            data,
                            values[index],
                            values[index + 1],
                            binary,
                            f"dyld-info payload {index // 2}",
                        )
                    )
                normalized_command["payloadsSha256"] = payloads
            immutable_commands.append(normalized_command)
        offset += command_size
    if offset != commands_end:
        raise AppleApplicationAuditError(
            f"normalized Mach-O load-command extent is inconsistent: {binary}"
        )
    linkedit_segments = [
        segment for segment in segments if segment["name"] == "__LINKEDIT"
    ]
    if len(linkedit_segments) != 1:
        raise AppleApplicationAuditError(
            f"normalized Mach-O must contain one __LINKEDIT segment: {binary}"
        )
    content_offsets = [
        section["fileOffset"]
        for segment in segments
        if segment["name"] != "__LINKEDIT"
        for section in segment["sections"]
        if section["bytesSha256"] is not None and section["fileOffset"] > 0
    ]
    if not content_offsets:
        raise AppleApplicationAuditError(
            f"normalized Mach-O has no file-backed loaded sections: {binary}"
        )
    header_region_end = min(content_offsets)
    normalized_segments: list[dict[str, Any]] = []
    for segment in segments:
        if segment["name"] == "__LINKEDIT":
            continue
        loaded_start = segment["fileOffset"]
        loaded_end = loaded_start + segment["fileSize"]
        if loaded_start < header_region_end:
            loaded_start = min(loaded_end, header_region_end)
        normalized_segments.append(
            {
                **segment,
                "loadedBytesComparedOffset": loaded_start,
                "loadedBytesComparedSize": loaded_end - loaded_start,
                "loadedBytesOutsideMutableHeaderSha256": hashlib.sha256(
                    data[loaded_start:loaded_end]
                ).hexdigest(),
            }
        )
    if (symtab is None) != (dysymtab is None):
        raise AppleApplicationAuditError(
            f"normalized Mach-O symbol commands are incomplete: {binary}"
        )
    retained_symbol_identity = None
    dysymtab_payload_identity = None
    if symtab is not None and dysymtab is not None:
        retained_symbol_identity = _macho_retained_symbol_identity(
            data, binary, symtab, dysymtab
        )
        dysymtab_payload_identity = _macho_dysymtab_payload_identity(
            data, binary, symtab, dysymtab
        )
    return {
        "normalizedIdentity": {
            "header": {
                "cpuType": cpu_type & 0xFFFFFFFF,
                "cpuSubtype": cpu_subtype & 0xFFFFFFFF,
                "fileType": file_type,
                "flags": header_flags,
                "reserved": reserved,
            },
            "headerRegionEnd": header_region_end,
            "loadableSegments": normalized_segments,
            "immutableLoadCommands": immutable_commands,
            "retainedExternalAndUndefinedSymbolsSha256": (
                retained_symbol_identity["symbolsSha256"]
                if retained_symbol_identity is not None
                else None
            ),
            "retainedStringTableSemanticsSha256": (
                retained_symbol_identity["stringsSha256"]
                if retained_symbol_identity is not None
                else None
            ),
            "dynamicSymbolTablePayloads": dysymtab_payload_identity,
        },
        "loadCommandsEnd": commands_end,
        "dylibId": dylib_id,
        "loadCommandUuid": load_command_uuid,
        "linkedit": linkedit_segments[0],
        "symtab": symtab,
        "dysymtab": dysymtab,
        "codeSignature": code_signature,
    }


def _validated_ios_shim_runtime_identity(
    packaged_data: bytes,
    reference_data: bytes,
    packaged: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    packaged_identity = _object(
        packaged.get("normalizedIdentity"), "packaged shim normalized identity"
    )
    reference_identity = _object(
        reference.get("normalizedIdentity"), "reference shim normalized identity"
    )
    if packaged_identity != reference_identity:
        raise AppleApplicationAuditError(
            "packaged iOS shim normalized runtime fields differ from the "
            "validated prepackage hook output"
        )
    packaged_uuid = packaged.get("loadCommandUuid")
    reference_uuid = reference.get("loadCommandUuid")
    if (
        not isinstance(packaged_uuid, str)
        or not packaged_uuid
        or packaged_uuid != reference_uuid
    ):
        raise AppleApplicationAuditError(
            "packaged iOS shim LC_UUID differs from the validated prepackage "
            "hook output"
        )
    header_region_end = _integer(
        packaged_identity.get("headerRegionEnd"),
        "iOS shim mutable header-region end",
    )
    packaged_commands_end = _integer(
        packaged.get("loadCommandsEnd"), "packaged shim load-command end"
    )
    reference_commands_end = _integer(
        reference.get("loadCommandsEnd"), "reference shim load-command end"
    )
    compared_offset = max(packaged_commands_end, reference_commands_end)
    if (
        compared_offset > header_region_end
        or header_region_end > len(packaged_data)
        or header_region_end > len(reference_data)
    ):
        raise AppleApplicationAuditError(
            "iOS shim mutable load-command region overlaps loaded content"
        )
    packaged_padding = packaged_data[compared_offset:header_region_end]
    reference_padding = reference_data[compared_offset:header_region_end]
    if packaged_padding != reference_padding:
        raise AppleApplicationAuditError(
            "packaged iOS shim loaded header padding differs outside the mutable "
            "load-command region"
        )
    return {
        **packaged_identity,
        "loadCommandUuid": "matched",
        "loadedHeaderPaddingOutsideMutableLoadCommands": "matched",
    }


def _validate_ios_shim_linkedit_transformation(
    packaged: dict[str, Any],
    reference: dict[str, Any],
    packaged_size: int,
    reference_size: int,
) -> dict[str, Any]:
    packaged_linkedit = _object(packaged.get("linkedit"), "packaged shim __LINKEDIT")
    reference_linkedit = _object(
        reference.get("linkedit"), "reference shim __LINKEDIT"
    )
    for field in (
        "name",
        "vmAddress",
        "fileOffset",
        "maximumProtection",
        "initialProtection",
        "flags",
        "sections",
    ):
        if packaged_linkedit.get(field) != reference_linkedit.get(field):
            raise AppleApplicationAuditError(
                f"iOS shim __LINKEDIT {field} changed outside the strip/sign boundary"
            )
    packaged_file_offset = _integer(
        packaged_linkedit.get("fileOffset"), "packaged shim __LINKEDIT file offset"
    )
    reference_file_offset = _integer(
        reference_linkedit.get("fileOffset"), "reference shim __LINKEDIT file offset"
    )
    packaged_file_size = _integer(
        packaged_linkedit.get("fileSize"), "packaged shim __LINKEDIT file size"
    )
    reference_file_size = _integer(
        reference_linkedit.get("fileSize"), "reference shim __LINKEDIT file size"
    )
    packaged_vm_size = _integer(
        packaged_linkedit.get("vmSize"), "packaged shim __LINKEDIT VM size"
    )
    reference_vm_size = _integer(
        reference_linkedit.get("vmSize"), "reference shim __LINKEDIT VM size"
    )
    if (
        packaged_file_offset + packaged_file_size != packaged_size
        or reference_file_offset + reference_file_size != reference_size
        or packaged_vm_size != ((packaged_file_size + 0x3FFF) & ~0x3FFF)
        or reference_vm_size != ((reference_file_size + 0x3FFF) & ~0x3FFF)
    ):
        raise AppleApplicationAuditError(
            "iOS shim __LINKEDIT does not match the expected strip/sign transformation"
        )
    packaged_symtab = _object(packaged.get("symtab"), "packaged shim symtab")
    reference_symtab = _object(reference.get("symtab"), "reference shim symtab")
    packaged_dysymtab = _object(
        packaged.get("dysymtab"), "packaged shim dynamic symtab"
    )
    reference_dysymtab = _object(
        reference.get("dysymtab"), "reference shim dynamic symtab"
    )

    symtab_fields = ("symbolOffset", "symbolCount", "stringOffset", "stringSize")
    dysymtab_fields = (
        "localIndex",
        "localCount",
        "externalIndex",
        "externalCount",
        "undefinedIndex",
        "undefinedCount",
        "tocOffset",
        "tocCount",
        "moduleTableOffset",
        "moduleTableCount",
        "externalReferenceOffset",
        "externalReferenceCount",
        "indirectSymbolOffset",
        "indirectSymbolCount",
        "externalRelocationOffset",
        "externalRelocationCount",
        "localRelocationOffset",
        "localRelocationCount",
    )

    def integers(
        value: dict[str, Any], fields: Sequence[str], label: str
    ) -> dict[str, int]:
        return {
            field: _integer(value.get(field), f"{label} {field}")
            for field in fields
        }

    packaged_symbols = integers(packaged_symtab, symtab_fields, "packaged shim")
    reference_symbols = integers(reference_symtab, symtab_fields, "reference shim")
    packaged_dynamic = integers(
        packaged_dysymtab, dysymtab_fields, "packaged shim"
    )
    reference_dynamic = integers(
        reference_dysymtab, dysymtab_fields, "reference shim"
    )

    for symbols, dynamic in (
        (packaged_symbols, packaged_dynamic),
        (reference_symbols, reference_dynamic),
    ):
        if (
            dynamic["localIndex"] != 0
            or dynamic["externalIndex"]
            != dynamic["localIndex"] + dynamic["localCount"]
            or dynamic["undefinedIndex"]
            != dynamic["externalIndex"] + dynamic["externalCount"]
            or symbols["symbolCount"]
            != dynamic["undefinedIndex"] + dynamic["undefinedCount"]
        ):
            raise AppleApplicationAuditError(
                "iOS shim symbol and dynamic-symbol counts are inconsistent"
            )

    removed_local_symbols = (
        reference_dynamic["localCount"] - packaged_dynamic["localCount"]
    )
    if removed_local_symbols < 0:
        raise AppleApplicationAuditError(
            "iOS shim symbol tables do not match the expected local-symbol strip"
        )
    removed_symbol_bytes = removed_local_symbols * 16
    if (
        packaged_symbols["symbolOffset"] != reference_symbols["symbolOffset"]
        or packaged_symbols["symbolCount"]
        != reference_symbols["symbolCount"] - removed_local_symbols
        or packaged_symbols["stringOffset"]
        != reference_symbols["stringOffset"] - removed_symbol_bytes
        or (
            removed_local_symbols == 0
            and packaged_symbols["stringSize"] != reference_symbols["stringSize"]
        )
        or (
            removed_local_symbols > 0
            and packaged_symbols["stringSize"] >= reference_symbols["stringSize"]
        )
    ):
        raise AppleApplicationAuditError(
            "iOS shim symbol tables do not match the expected local-symbol strip"
        )
    for field in (
        "externalCount",
        "undefinedCount",
        "tocCount",
        "moduleTableCount",
        "externalReferenceCount",
        "indirectSymbolCount",
        "externalRelocationCount",
        "localRelocationCount",
    ):
        if packaged_dynamic[field] != reference_dynamic[field]:
            raise AppleApplicationAuditError(
                f"iOS shim dynamic-symbol {field} changed during strip/sign"
            )
    offset_fields = (
        ("tocOffset", "tocCount"),
        ("moduleTableOffset", "moduleTableCount"),
        ("externalReferenceOffset", "externalReferenceCount"),
        ("indirectSymbolOffset", "indirectSymbolCount"),
        ("externalRelocationOffset", "externalRelocationCount"),
        ("localRelocationOffset", "localRelocationCount"),
    )
    reference_symbol_end = (
        reference_symbols["symbolOffset"] + reference_symbols["symbolCount"] * 16
    )
    for offset_field, count_field in offset_fields:
        reference_offset = reference_dynamic[offset_field]
        if (
            reference_dynamic[count_field] == 0
            or reference_offset < reference_symbol_end
        ):
            expected_offset = reference_offset
        else:
            expected_offset = reference_offset - removed_symbol_bytes
        if packaged_dynamic[offset_field] != expected_offset:
            raise AppleApplicationAuditError(
                f"iOS shim dynamic-symbol {offset_field} changed outside the "
                "local-symbol strip"
            )

    def validate_signature_boundary(
        image: dict[str, Any], content_end: int, file_end: int, label: str
    ) -> None:
        raw_signature = image.get("codeSignature")
        if raw_signature is None:
            if content_end != file_end:
                raise AppleApplicationAuditError(
                    f"{label} has unaccounted __LINKEDIT bytes"
                )
            return
        signature = _object(raw_signature, f"{label} code signature")
        signature_offset = _integer(
            signature.get("offset"), f"{label} code-signature offset"
        )
        signature_size = _integer(
            signature.get("size"),
            f"{label} code-signature size",
            positive=True,
        )
        expected_signature_offset = (content_end + 0xF) & ~0xF
        if (
            signature_offset != expected_signature_offset
            or signature_offset + signature_size != file_end
        ):
            raise AppleApplicationAuditError(
                f"{label} code signature does not exactly terminate __LINKEDIT"
            )

    if packaged.get("codeSignature") is None:
        raise AppleApplicationAuditError(
            "packaged iOS shim does not contain LC_CODE_SIGNATURE"
        )
    validate_signature_boundary(
        packaged,
        packaged_symbols["stringOffset"] + packaged_symbols["stringSize"],
        packaged_size,
        "packaged shim",
    )
    validate_signature_boundary(
        reference,
        reference_symbols["stringOffset"] + reference_symbols["stringSize"],
        reference_size,
        "reference shim",
    )
    return {
        "localSymbolsRemoved": removed_local_symbols,
        "referenceCodeSignature": (
            "present" if reference.get("codeSignature") is not None else "absent"
        ),
        "packagedCodeSignature": "present",
        "linkeditSizeDelta": packaged_file_size - reference_file_size,
    }


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


def _read_hook_json(path: Path, label: str) -> dict[str, Any]:
    _regular_file(path, label)
    data = path.read_bytes()
    if not data or len(data) > _MAX_MANIFEST_BYTES:
        raise AppleApplicationAuditError(f"{label} is empty or oversized")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppleApplicationAuditError(
            f"{label} is not strict UTF-8 JSON"
        ) from error
    return _object(value, label)


def _validate_reference_shim_hook_metadata(
    packaged_shim: Path,
    reference_shim: Path,
    application: Path,
    repository: Path,
    platform: str,
) -> dict[str, Any]:
    _regular_file(packaged_shim, "packaged iOS shim")
    reference_shim = _regular_file(reference_shim, "prepackage reference shim")
    if not reference_shim.is_absolute():
        raise AppleApplicationAuditError("--reference-shim must be an absolute path")
    if Path(os.path.normpath(str(reference_shim))) != reference_shim:
        raise AppleApplicationAuditError(
            "--reference-shim must not contain noncanonical path components"
        )
    try:
        if os.path.samefile(packaged_shim, reference_shim):
            raise AppleApplicationAuditError(
                "--reference-shim must not be the packaged shim or a hard link to it"
            )
    except OSError as error:
        raise AppleApplicationAuditError(
            "could not compare packaged and reference shim inodes"
        ) from error
    if reference_shim.is_relative_to(application):
        raise AppleApplicationAuditError("--reference-shim must not be inside the app")
    if _sha256(packaged_shim) == _sha256(reference_shim):
        raise AppleApplicationAuditError(
            "--reference-shim must not be a byte-for-byte copy of the packaged shim"
        )
    if len(reference_shim.parents) < 7:
        raise AppleApplicationAuditError(
            "--reference-shim does not have the native-assets hook path depth"
        )
    invocation_id = reference_shim.parent.name
    if (
        reference_shim.name != "libfonix_shim.dylib"
        or _HOOK_INVOCATION_ID.fullmatch(invocation_id) is None
        or reference_shim.parents[1].name != "build"
        or reference_shim.parents[2].name != "fonix"
        or reference_shim.parents[3].name != "shared"
        or reference_shim.parents[4].name != "hooks_runner"
        or reference_shim.parents[5].name != ".dart_tool"
    ):
        raise AppleApplicationAuditError(
            "--reference-shim does not match the exact Fonix hook-output path"
        )
    for directory in reference_shim.parents[:7]:
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError as error:
            raise AppleApplicationAuditError(
                f"missing reference-shim parent directory: {directory}"
            ) from error
        if not stat.S_ISDIR(mode):
            raise AppleApplicationAuditError(
                f"reference-shim path contains a non-directory or symlink: {directory}"
            )
    copy_root = reference_shim.parents[6]
    runner_root = copy_root / ".dart_tool/hooks_runner/fonix" / invocation_id
    for directory in (runner_root.parent, runner_root):
        try:
            runner_mode = directory.lstat().st_mode
        except FileNotFoundError as error:
            raise AppleApplicationAuditError(
                "missing matching immediate Fonix hook invocation"
            ) from error
        if not stat.S_ISDIR(runner_mode):
            raise AppleApplicationAuditError(
                "matching Fonix hook invocation contains a symlink or non-directory"
            )
    input_path = runner_root / "input.json"
    output_path = runner_root / "output.json"
    hook_input = _read_hook_json(input_path, "Fonix hook input")
    hook_output = _read_hook_json(output_path, "Fonix hook output")
    _require_keys(
        hook_input,
        {
            "assets",
            "config",
            "out_dir_shared",
            "out_file",
            "package_name",
            "package_root",
            "user_defines",
        },
        "Fonix hook input",
    )
    if hook_input.get("assets") != {} or hook_input.get("package_name") != "fonix":
        raise AppleApplicationAuditError("Fonix hook input package contract changed")
    expected_shared = reference_shim.parents[1]
    if Path(_string(hook_input.get("out_dir_shared"), "hook out_dir_shared")) != expected_shared:
        raise AppleApplicationAuditError("Fonix hook shared output directory is wrong")
    if Path(_string(hook_input.get("out_file"), "hook out_file")) != output_path:
        raise AppleApplicationAuditError("Fonix hook output metadata path is wrong")
    if Path(_string(hook_input.get("package_root"), "hook package_root")) != repository:
        raise AppleApplicationAuditError("Fonix hook package root is wrong")
    config = _object(hook_input.get("config"), "Fonix hook config")
    _require_keys(
        config,
        {"build_asset_types", "extensions", "linking_enabled"},
        "Fonix hook config",
    )
    if config.get("build_asset_types") != ["code_assets/code"]:
        raise AppleApplicationAuditError("Fonix hook asset type contract changed")
    expected_linking = platform == "ios-device"
    if config.get("linking_enabled") is not expected_linking:
        raise AppleApplicationAuditError("Fonix hook linking mode is wrong")
    extensions = _object(config.get("extensions"), "Fonix hook extensions")
    _require_keys(extensions, {"code_assets"}, "Fonix hook extensions")
    code_assets = _object(extensions.get("code_assets"), "Fonix hook code_assets")
    _require_keys(
        code_assets,
        {
            "c_compiler",
            "ios",
            "link_mode_preference",
            "target_architecture",
            "target_os",
        },
        "Fonix hook code_assets",
    )
    compiler = _object(code_assets.get("c_compiler"), "Fonix hook compiler")
    _require_keys(compiler, {"ar", "cc", "ld"}, "Fonix hook compiler")
    for key in ("ar", "cc", "ld"):
        _string(compiler.get(key), f"Fonix hook compiler {key}")
    if (
        code_assets.get("target_os") != "ios"
        or code_assets.get("target_architecture") != "arm64"
        or code_assets.get("link_mode_preference") != "dynamic"
    ):
        raise AppleApplicationAuditError("Fonix hook iOS target contract changed")
    ios = _object(code_assets.get("ios"), "Fonix hook iOS extension")
    _require_keys(ios, {"target_sdk", "target_version"}, "Fonix hook iOS extension")
    expected_sdk = "iphoneos" if platform == "ios-device" else "iphonesimulator"
    if ios.get("target_sdk") != expected_sdk or ios.get("target_version") != 13:
        raise AppleApplicationAuditError("Fonix hook iOS SDK contract changed")
    user_defines = _object(hook_input.get("user_defines"), "Fonix hook user defines")
    _require_keys(user_defines, {"workspace_pubspec"}, "Fonix hook user defines")
    workspace = _object(
        user_defines.get("workspace_pubspec"), "Fonix hook workspace pubspec"
    )
    _require_keys(workspace, {"base_path", "defines"}, "Fonix hook workspace pubspec")
    if Path(_string(workspace.get("base_path"), "workspace pubspec base path")) != (
        copy_root / "pubspec.yaml"
    ):
        raise AppleApplicationAuditError("Fonix hook workspace pubspec path is wrong")
    defines = _object(workspace.get("defines"), "Fonix hook workspace defines")
    if defines != {
        "runtime_mode": "linked",
        "artifact_cache": ".fonix-artifact-cache",
        "application_minimum_os": "15.1",
    }:
        raise AppleApplicationAuditError("Fonix hook workspace defines are wrong")
    _require_keys(
        hook_output,
        {"assets", "assets_for_linking", "dependencies", "status", "timestamp"},
        "Fonix hook output",
    )
    if hook_output.get("status") != "success" or hook_output.get("assets_for_linking") != {}:
        raise AppleApplicationAuditError("Fonix hook output status contract changed")
    _string(hook_output.get("timestamp"), "Fonix hook output timestamp")
    dependencies = _array(hook_output.get("dependencies"), "Fonix hook dependencies")
    if not dependencies or len(dependencies) > 4096:
        raise AppleApplicationAuditError("Fonix hook dependency inventory is invalid")
    for index, dependency in enumerate(dependencies):
        _string(dependency, f"Fonix hook dependency[{index}]")
    assets = _array(hook_output.get("assets"), "Fonix hook output assets")
    if len(assets) != 1:
        raise AppleApplicationAuditError("Fonix hook output must contain one asset")
    asset = _object(assets[0], "Fonix hook output asset")
    _require_keys(asset, {"encoding", "type"}, "Fonix hook output asset")
    if asset.get("type") != "code_assets/code":
        raise AppleApplicationAuditError("Fonix hook output asset type is wrong")
    encoding = _object(asset.get("encoding"), "Fonix hook output encoding")
    _require_keys(
        encoding, {"file", "id", "link_mode"}, "Fonix hook output encoding"
    )
    link_mode = _object(encoding.get("link_mode"), "Fonix hook output link mode")
    _require_keys(link_mode, {"type"}, "Fonix hook output link mode")
    if (
        Path(_string(encoding.get("file"), "Fonix hook output asset file"))
        != reference_shim
        or encoding.get("id") != "package:fonix/fonix_shim"
        or link_mode.get("type") != "dynamic_loading_bundle"
    ):
        raise AppleApplicationAuditError("Fonix hook output asset contract changed")
    return {
        "invocationId": invocation_id,
        "input": str(input_path),
        "output": str(output_path),
        "status": "validated",
    }


def _audit_ios_reference_shim(
    packaged_shim: Path,
    reference_shim: Path,
    expected_build_manifest: dict[str, Any],
    *,
    application: Path,
    repository: Path,
    platform: str,
    architecture: str,
    platform_number: int,
    minimum_os: tuple[int, int, int],
    maximum_os: tuple[int, int, int],
    otool: str,
    nm: str,
    dyld_info: str,
) -> dict[str, Any]:
    hook_metadata = _validate_reference_shim_hook_metadata(
        packaged_shim,
        reference_shim,
        application,
        repository,
        platform,
    )
    reference_shim = _regular_file(reference_shim, "prepackage reference shim")
    build_artifact = _object(
        expected_build_manifest.get("artifact"), "expected build manifest artifact"
    )
    build_identity = {
        "schemaVersion": _integer(
            expected_build_manifest.get("schemaVersion"),
            "expected build manifest schemaVersion",
            positive=True,
        ),
        "artifactId": _string(
            build_artifact.get("id"), "expected build manifest artifact ID"
        ),
        "buildId": _string(
            expected_build_manifest.get("buildId"), "expected build manifest build ID"
        ),
        "status": "matched",
    }
    if build_identity["schemaVersion"] != 3:
        raise AppleApplicationAuditError(
            "iOS linked-runtime identity requires build-manifest schema 3"
        )
    _audit_macho(
        reference_shim,
        architecture=architecture,
        platform_number=platform_number,
        minimum_os=minimum_os,
        maximum_os=maximum_os,
        otool=otool,
    )
    _extract_embedded_build_manifest(
        packaged_shim, expected_build_manifest
    )
    _extract_embedded_build_manifest(
        reference_shim, expected_build_manifest
    )
    _audit_shim_exports(
        reference_shim, repository, nm=nm, dyld_info=dyld_info
    )
    packaged_data = _macho_slices(packaged_shim)[architecture]
    reference_data = _macho_slices(reference_shim)[architecture]
    packaged_image = _macho_normalized_runtime_identity(
        packaged_data, packaged_shim
    )
    reference_image = _macho_normalized_runtime_identity(
        reference_data, reference_shim
    )
    normalized_identity = _validated_ios_shim_runtime_identity(
        packaged_data,
        reference_data,
        packaged_image,
        reference_image,
    )
    expected_packaged_id = {
        "path": "@rpath/fonix_shim.framework/fonix_shim",
        "timestamp": 1,
        "currentVersion": 0,
        "compatibilityVersion": 0,
    }
    expected_reference_id = {
        **expected_packaged_id,
        "path": str(reference_shim),
    }
    if (
        packaged_image.get("dylibId") != expected_packaged_id
        or reference_image.get("dylibId") != expected_reference_id
    ):
        raise AppleApplicationAuditError(
            "iOS shim install-name transformation is not exact"
        )
    linkedit_transformation = _validate_ios_shim_linkedit_transformation(
        packaged_image,
        reference_image,
        len(packaged_data),
        len(reference_data),
    )
    accounted_transformations = [
        "absolute-prepackage-id-to-framework-rpath-id",
    ]
    if linkedit_transformation["localSymbolsRemoved"]:
        accounted_transformations.append("local-symbol-strip")
    accounted_transformations.append(
        (
            "adhoc-code-signature-addition"
            if linkedit_transformation["referenceCodeSignature"] == "absent"
            else "adhoc-code-signature-replacement"
        )
    )
    if linkedit_transformation["linkeditSizeDelta"]:
        accounted_transformations.append("linkedit-resize")
    return {
        "runtimeMode": "linked",
        "packagedShim": _IOS_SHIM_PATH,
        "referenceShim": str(reference_shim),
        "hookInvocationMetadata": hook_metadata,
        "normalizedRuntimeFields": "matched",
        "normalizedRuntimeFieldsSha256": _canonical_json_sha256(
            normalized_identity
        ),
        "comparisonScope": (
            "Mach header CPU/subtype/filetype/flags, pair-matched LC_UUID, "
            "non-LINKEDIT loadable segment and section metadata/bytes/padding "
            "outside the mutable load-command region, retained external/undefined "
            "symbols and string semantics, canonical LC_DYSYMTAB table payloads, "
            "immutable load commands, and referenced dyld fixup/export payloads"
        ),
        "accountedTransformations": accounted_transformations,
        "embeddedBuildIdentity": build_identity,
        "nlistAndDyldExports": "matched",
        "separatelyPackagedOrtMachOs": [],
        "auditedOrtLoadCommandDependencies": [],
        "runtimeDlopenBehavior": "not-proved",
        "otherMachOStaticOrtCopies": "not-proved",
        "staticArchiveMultiplicity": "not-provable-from-final-bundle",
        "claimBoundary": _IOS_REFERENCE_SHIM_CLAIM_BOUNDARY,
    }


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
    reference_shim: Path | None = None,
    otool: str = "/usr/bin/otool",
    nm: str = "/usr/bin/nm",
    dyld_info: str = "/usr/bin/dyld_info",
    codesign: str = "/usr/bin/codesign",
    signature_policy: str = _STRICT_SIGNATURE_POLICY,
) -> dict[str, Any]:
    contract = _platform_contract(platform)
    signature_policy = _signature_policy(platform, signature_policy)
    if not application.is_dir() or application.is_symlink():
        raise AppleApplicationAuditError("--app must be a non-symlink directory")
    manifest, manifest_path, notice_path, locked_minimum = audit_packaged_metadata(
        application, platform
    )
    locked_artifact = _assert_locked_manifest(manifest, repository, platform)
    declared_minimum = _version(
        declared_minimum_os, "declared application minimum OS"
    )
    if platform != "macos" and declared_minimum != locked_minimum:
        raise AppleApplicationAuditError(
            "declared iOS application minimum OS must exactly match the selected lock tuple"
        )
    if platform == "macos" and declared_minimum < locked_minimum:
        raise AppleApplicationAuditError(
            "declared application minimum OS is below the selected lock tuple"
        )
    plist, plist_path = _plist(application, platform)
    floor_key = "LSMinimumSystemVersion" if platform == "macos" else "MinimumOSVersion"
    plist_floor_text = _string(plist.get(floor_key), f"Info.plist {floor_key}")
    plist_floor = _version(plist_floor_text, f"Info.plist {floor_key}")
    if platform != "macos" and plist_floor != declared_minimum:
        raise AppleApplicationAuditError(
            "final iOS Info.plist deployment floor must exactly match the declared/locked floor"
        )
    if platform == "macos" and (
        plist_floor < declared_minimum or plist_floor < locked_minimum
    ):
        raise AppleApplicationAuditError(
            "final Info.plist deployment floor is below the declared/locked floor"
        )
    executable = _string(plist.get("CFBundleExecutable"), "CFBundleExecutable")
    binary_paths = _macho_paths(application, platform, executable)
    target = _object(manifest.get("target"), "manifest target")
    architecture = _string(target.get("architecture"), "target architecture")
    ios_maximum_os = min(declared_minimum, plist_floor)
    for binary in binary_paths:
        _audit_macho(
            binary,
            architecture=architecture,
            platform_number=contract["machoPlatform"],
            minimum_os=locked_minimum,
            otool=otool,
            maximum_os=(ios_maximum_os if platform != "macos" else None),
        )
        if platform == "macos" and signature_policy == _STRICT_SIGNATURE_POLICY:
            _run((codesign, "--verify", "--strict", str(binary)))
    native_inventory: dict[str, Any] | None = None
    if platform != "macos":
        native_inventory = _inventory_ios_application(
            application,
            executable,
            platform=platform,
            architecture=architecture,
            platform_number=contract["machoPlatform"],
            maximum_os=ios_maximum_os,
            repository=repository,
            otool=otool,
            nm=nm,
            dyld_info=dyld_info,
        )
    if signature_policy == _STRICT_SIGNATURE_POLICY and platform != "macos":
        native_inventory = _object(native_inventory, "iOS native inventory")
        signature_details = _audit_strict_ios_signing(
            application,
            executable,
            native_inventory,
            codesign=codesign,
        )
        signature_status = "verified"
        claim_status = "signature-verified-package-audit"
    elif signature_policy == _STRICT_SIGNATURE_POLICY:
        _run((codesign, "--verify", "--strict", str(application)))
        signature_status = "verified"
        claim_status = "signature-verified-package-audit"
        signature_details: dict[str, Any] = {
            "rootBundle": "verified",
            "rootExecutable": "verified",
        }
    else:
        native_inventory = _object(native_inventory, "iOS native inventory")
        signature_details = _audit_unsigned_ios_device_signing(
            application,
            executable,
            native_inventory,
            codesign=codesign,
        )
        signature_status = "unsigned"
        claim_status = "static-only"

    identity = _object(manifest.get("_auditIdentity"), "audit identity")
    shim = binary_paths[1]
    expected_build_manifest = _expected_build_manifest(manifest, locked_artifact)
    if platform == "macos":
        if reference_shim is not None:
            raise AppleApplicationAuditError(
                "macOS final-app audit does not accept --reference-shim"
            )
        _extract_embedded_build_manifest(shim, expected_build_manifest)
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
                "final ONNX Runtime section byte/size identity differs from the "
                "selected payload"
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
    else:
        if reference_runtime is not None:
            raise AppleApplicationAuditError(
                "iOS final-app audit does not accept --reference-runtime"
            )
        if reference_shim is None:
            raise AppleApplicationAuditError(
                "iOS final-app audit requires --reference-shim"
            )
        native_inventory = _object(native_inventory, "iOS native inventory")
        native_inventory["linkedRuntimeIdentity"] = _audit_ios_reference_shim(
            shim,
            reference_shim,
            expected_build_manifest,
            application=application,
            repository=repository,
            platform=platform,
            architecture=architecture,
            platform_number=contract["machoPlatform"],
            minimum_os=locked_minimum,
            maximum_os=ios_maximum_os,
            otool=otool,
            nm=nm,
            dyld_info=dyld_info,
        )
    reported_macho_paths = [str(path) for path in binary_paths]
    if native_inventory is not None:
        reported_macho_paths = [
            str(
                application
                / _string(
                    _object(record, "Mach-O record").get("path"),
                    "Mach-O path",
                )
            )
            for record in _array(
                native_inventory.get("machOBinaries"),
                "native inventory Mach-O binaries",
            )
        ]
    report = {
        "platform": platform,
        "application": str(application),
        "artifactId": identity["artifactId"],
        "lockedMinimumOs": identity["minimumOs"],
        "declaredMinimumOs": declared_minimum_os,
        "plistMinimumOs": plist_floor_text,
        "manifest": str(manifest_path),
        "thirdPartyNotices": str(notice_path),
        "plist": str(plist_path),
        "machOBinaries": reported_macho_paths,
        "buildManifest": expected_build_manifest,
        "signaturePolicy": signature_policy,
        "signatureStatus": signature_status,
        "claimStatus": claim_status,
        "signatureDetails": signature_details,
    }
    if native_inventory is not None:
        report["nativeInventory"] = native_inventory
    return report


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


def audit_application_and_optional_probe(
    application: Path,
    platform: str,
    declared_minimum_os: str,
    *,
    repository: Path,
    reference_runtime: Path | None = None,
    reference_shim: Path | None = None,
    cpu_probe_model: Path | None = None,
    otool: str = "/usr/bin/otool",
    nm: str = "/usr/bin/nm",
    dyld_info: str = "/usr/bin/dyld_info",
    clang: str = "/usr/bin/clang",
    codesign: str = "/usr/bin/codesign",
    signature_policy: str = _STRICT_SIGNATURE_POLICY,
) -> dict[str, Any]:
    """Audit one application in-process and optionally run its macOS probe.

    Gates use this entry point instead of nesting the auditor beneath another
    bounded subprocess owner.  Individual native tools can therefore own one
    POSIX process group without escaping an outer auditor process group.
    """

    report = audit_application(
        application,
        platform,
        declared_minimum_os,
        repository=repository,
        reference_runtime=reference_runtime,
        reference_shim=reference_shim,
        otool=otool,
        nm=nm,
        dyld_info=dyld_info,
        codesign=codesign,
        signature_policy=signature_policy,
    )
    if cpu_probe_model is None:
        return report
    if platform != "macos":
        raise AppleApplicationAuditError(
            "the host CPU probe is available only for macOS apps"
        )
    probe_manifest = run_packaged_cpu_probe(
        application,
        repository,
        cpu_probe_model,
        _object(report.get("buildManifest"), "expected build manifest"),
        clang=clang,
    )
    report["cpuInference"] = "passed"
    report["probeBuildManifest"] = probe_manifest
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument(
        "--platform",
        required=True,
        choices=("macos", "ios-device", "ios-simulator"),
    )
    parser.add_argument("--application-minimum-os", required=True)
    parser.add_argument(
        "--signature-policy",
        choices=_SIGNATURE_POLICIES,
        default=_STRICT_SIGNATURE_POLICY,
        help=(
            "strict by default; ios-device-unsigned-development accepts only "
            "an unsigned iOS device root with exact verified teamless ad-hoc "
            "nested frameworks and emits static-only evidence"
        ),
    )
    parser.add_argument("--run-cpu-probe", type=Path)
    parser.add_argument(
        "--reference-runtime",
        type=Path,
        help="exact lock-verified staged ORT dylib (required for macOS)",
    )
    parser.add_argument(
        "--reference-shim",
        type=Path,
        help=(
            "resolver-built prepackage shim with matching native-assets hook "
            "metadata (required for iOS runtime-image binding)"
        ),
    )
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--otool", default="/usr/bin/otool")
    parser.add_argument("--nm", default="/usr/bin/nm")
    parser.add_argument("--dyld-info", default="/usr/bin/dyld_info")
    parser.add_argument("--clang", default="/usr/bin/clang")
    parser.add_argument("--codesign", default="/usr/bin/codesign")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = audit_application_and_optional_probe(
            arguments.app.resolve(strict=True),
            arguments.platform,
            arguments.application_minimum_os,
            repository=arguments.repository.resolve(strict=True),
            reference_runtime=(
                arguments.reference_runtime.resolve(strict=True)
                if arguments.reference_runtime is not None
                else None
            ),
            reference_shim=(
                arguments.reference_shim
                if arguments.reference_shim is not None
                else None
            ),
            cpu_probe_model=(
                arguments.run_cpu_probe.resolve(strict=True)
                if arguments.run_cpu_probe is not None
                else None
            ),
            otool=arguments.otool,
            nm=arguments.nm,
            dyld_info=arguments.dyld_info,
            clang=arguments.clang,
            codesign=arguments.codesign,
            signature_policy=arguments.signature_policy,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    except (AppleApplicationAuditError, FileNotFoundError) as error:
        print(f"audit_apple_application: {error}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
