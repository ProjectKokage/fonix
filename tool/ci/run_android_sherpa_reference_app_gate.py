#!/usr/bin/env python3
"""Build and statically audit the sherpa-owned Android reference app.

The committed application is copied to a new directory outside the Fonix
checkout before any Flutter command runs.  This gate deliberately stops after
the closed raw-native and matching APK/AAB audits.  It does not install an APK,
run a target workload, or create a runtime receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Callable, Mapping, NamedTuple, Sequence
from urllib.parse import unquote, urlsplit
import zipfile


sys.dont_write_bytecode = True


from android_gate_common import (
    AndroidGateCommonError,
    CommandOutput,
    directory,
    regular_file,
    run_bounded,
    sha256_file,
    strict_json,
    tool_environment,
)
import generate_android_sherpa_fonix_fixtures as fixture_generator
from validate_android_load_order_receipt import (
    LoadOrderReceiptError,
    _validate_pubspec_lock,
)


VALIDATED_FLUTTER_REVISION = "bd1e75d918605c91b411e8789fb911e6c9a84534"
ANDROID_BUILD_TOOLS_VERSION = "36.0.0"
ANDROID_NDK_VERSION = "28.2.13676358"
ANDROID_COMPILE_API = 36
SUPPORTED_JAVA_VERSION = "21.0.12"
ABI = "arm64-v8a"
FLUTTER_TARGET_PLATFORM = "android-arm64"
BUILD_TYPE = "release-minified"
ORT_API_REQUIRED = 27
SHERPA_SOURCE = "https://github.com/k2-fsa/sherpa-onnx"
SHERPA_REVISION = "142807252687d81b40d6315f23470a1512a00de3"
SHERPA_GENERIC_PACKAGE = "sherpa_onnx"
SHERPA_PACKAGE = "sherpa_onnx_android_arm64"
SHERPA_ANDROID_PACKAGES = (
    SHERPA_PACKAGE,
    "sherpa_onnx_android_armeabi",
    "sherpa_onnx_android_x86",
    "sherpa_onnx_android_x86_64",
)
SHERPA_HOSTED_PACKAGES = (SHERPA_GENERIC_PACKAGE, *SHERPA_ANDROID_PACKAGES)
SHERPA_VERSION = "1.13.4"
SNAPSHOT_DATE = "2026-08-07"

TEMPLATE = Path("templates/android/sherpa_reference_app")
MANIFEST = Path("MANIFEST.sha256")
APK_RELATIVE = Path("build/app/outputs/flutter-apk/app-release.apk")
AAB_RELATIVE = Path("build/app/outputs/bundle/release/app-release.aab")
HOOK_BUILD_RELATIVE = Path(".dart_tool/hooks_runner/shared/fonix/build")
SHIM_NAME = "libfonix_shim.so"
RUNTIME_ASSET_DIRECTORY = Path("assets/qualification")
APK_RUNTIME_ASSET_PREFIX = "assets/flutter_assets/assets/qualification/"
AAB_RUNTIME_ASSET_PREFIX = "base/assets/flutter_assets/assets/qualification/"
SHERPA_MODEL_NAME = "silero_vad.int8.onnx"
SHERPA_MODEL_SIZE_BYTES = 212_860
SHERPA_MODEL_SHA256 = (
    "c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20"
)
GENERATED_RUNTIME_FIXTURE_NAMES = (
    "fonix_cancellation_input.bin",
    "fonix_dynamic_matmul_chain.onnx",
    "fonix_fixture_manifest.json",
    "fonix_reference_input.bin",
    "fonix_reference_output.bin",
    "sherpa_synthetic_speech.wav",
    "sherpa_vad_reference.json",
)
RUNTIME_FIXTURE_NAMES = tuple(
    sorted((*GENERATED_RUNTIME_FIXTURE_NAMES, SHERPA_MODEL_NAME))
)

STATIC_FLUTTER_STANZA = "flutter:\n  uses-material-design: true\n"
RUNTIME_FLUTTER_STANZA = (
    "flutter:\n"
    "  uses-material-design: true\n"
    "  assets:\n"
    "    - assets/qualification/\n"
)

MAX_SOURCE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TEMPLATE_ENTRIES = 100_000
MAX_TEMPLATE_FILES = 10_000
MAX_TEMPLATE_BYTES = 96 * 1024 * 1024
MAX_LOCK_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_CONFIG_BYTES = 8 * 1024 * 1024
MAX_PLUGIN_INVENTORY_BYTES = 8 * 1024 * 1024
MAX_LOCAL_PROPERTIES_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000
MAX_ARCHIVE_MEMBER_PATH_BYTES = 4_096
MAX_TARGET_APK_ZIP_COMPRESSION_RATIO = 200
MAX_RUNTIME_FIXTURE_FILE_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_FIXTURE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_SHIM_BYTES = 16 * 1024 * 1024
MAX_SHERPA_LIBRARY_BYTES = 256 * 1024 * 1024
MAX_HOSTED_PACKAGE_ENTRIES = 4_096
MAX_HOSTED_PACKAGE_FILES = 2_048
MAX_HOSTED_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_AUDIT_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_ESCAPE_ENTRIES = 4_096
MAX_ESCAPE_BYTES = 256 * 1024 * 1024

GENERATED_DIRECTORY_EXCLUSIONS = frozenset(
    {
        ".dart_tool",
        ".idea",
        ".sherpa-static-evidence",
        "android/.gradle",
        "android/.kotlin",
        "android/app/.cxx",
        "android/captures",
        "build",
    }
)
GENERATED_FILE_EXCLUSIONS = frozenset(
    {
        ".flutter-plugins-dependencies",
        "android/app/src/main/java/io/flutter/plugins/GeneratedPluginRegistrant.java",
        "android/local.properties",
    }
)
GENERATED_EXCLUSIONS = GENERATED_DIRECTORY_EXCLUSIONS | GENERATED_FILE_EXCLUSIONS
SHERPA_NATIVE_LIBRARY_NAMES = (
    "libonnxruntime.so",
    "libsherpa-onnx-c-api.so",
    "libsherpa-onnx-cxx-api.so",
)
SHERPA_NATIVE_SHA256 = {
    "libonnxruntime.so": (
        "994848008526a934dfb579ac773b00e5867929234852b061005d45aacaee9533"
    ),
    "libsherpa-onnx-c-api.so": (
        "cb0fe5f4d26e8f66a5466cfc760caafaf50c60128321e491f538d60857324f56"
    ),
    "libsherpa-onnx-cxx-api.so": (
        "f961acd4fc2582ed8bea395c941e8c7b51fd8b41cffb2855779103764d6e7247"
    ),
}
REPOSITORY_ESCAPE_PATHS = (
    Path(".dart_tool"),
    Path("build"),
    Path(".flutter-plugins-dependencies"),
)

RELATIVE_FONIX_DEPENDENCY = "  fonix:\n    path: ../../..\n"
RELATIVE_FONIX_LOCK = '      path: "../../.."\n      relative: true\n'
ANDROID_HOOK = (
    "hooks:\n"
    "  user_defines:\n"
    "    fonix:\n"
    "      android_runtime_owner: sherpa\n"
    "      runtime_mode: external\n"
)
HOST_TEST_HOOK = (
    "hooks:\n"
    "  user_defines:\n"
    "    fonix:\n"
    "      runtime_mode: external\n"
)


class AndroidSherpaReferenceAppGateError(RuntimeError):
    """The sherpa-owned Android reference application gate failed closed."""


class FileIdentity(NamedTuple):
    size_bytes: int
    sha256: str


class ReportDestination(NamedTuple):
    path: Path
    parent: Path
    name: str
    directory_descriptor: int
    parent_identity: tuple[int, int]


class CopySummary(NamedTuple):
    file_count: int
    byte_count: int


class StagedSourceIdentity(NamedTuple):
    entry_count: int
    file_count: int
    byte_count: int
    sha256: str


class HostedPackageTreeIdentity(NamedTuple):
    directory_count: int
    file_count: int
    byte_count: int
    sha256: str


class HostedPackagePin(NamedTuple):
    archive_sha256: str
    tree: HostedPackageTreeIdentity


class HostedPackageGraphIdentity(NamedTuple):
    package_config: FileIdentity
    plugin_inventory: FileIdentity
    android_plugins: tuple[str, ...]


SHERPA_HOSTED_PACKAGE_PINS = {
    SHERPA_GENERIC_PACKAGE: HostedPackagePin(
        "889c03cf7a8788795e3a6d35bf9f20b66d1862ea7a73a1b6aaf6e450715c870a",
        HostedPackageTreeIdentity(
            3,
            30,
            411_017,
            "1c28eee2df0f5db02fd96a312b5889d1385f14786a6ad720157f89743e3b0db4",
        ),
    ),
    SHERPA_PACKAGE: HostedPackagePin(
        "0337650bc2357f39b751f1b9ced37770de3b60026effe66473b557346b4e3f97",
        HostedPackageTreeIdentity(
            6,
            12,
            26_644_755,
            "642c260000395b6552962929b8241ccd938816178b53a84b6059e7075d0652f7",
        ),
    ),
    "sherpa_onnx_android_armeabi": HostedPackagePin(
        "9e96729d99567c3f64fc6f4c232ef04b57ae4ef3bf1b21364ee6e461103cb7cf",
        HostedPackageTreeIdentity(
            6,
            12,
            18_534_341,
            "83492bb191461294d28bb16eed5734a09e008224ebe5a1008403809f6c2aec30",
        ),
    ),
    "sherpa_onnx_android_x86": HostedPackagePin(
        "6eaa462a24bf881c8ee2b3ab5b9fad3d310b714fc9d3ac4b9f650d0a90c9d0ab",
        HostedPackageTreeIdentity(
            6,
            12,
            31_497_589,
            "55aa1a375b0056814d9569157ca985eb6efd9f8d6c5ada3b4a1bf5ea60f03e76",
        ),
    ),
    "sherpa_onnx_android_x86_64": HostedPackagePin(
        "181aa0f0968adf2cf2dcb369c879ea372653864538b82772877e22748a80254f",
        HostedPackageTreeIdentity(
            6,
            12,
            30_442_454,
            "a23b7c43cad70605f9c83043dc28364b3beeafb9d0228c8b3a4bc512eec71896",
        ),
    ),
}


CommandRunner = Callable[..., CommandOutput]


def _common(error: AndroidGateCommonError) -> AndroidSherpaReferenceAppGateError:
    return AndroidSherpaReferenceAppGateError(str(error))


def _run_command(
    command: Sequence[str],
    *,
    operation: str,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int = 30 * 60,
) -> CommandOutput:
    try:
        return run_bounded(
            command,
            operation=operation,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error


def _execute(
    runner: CommandRunner,
    command: Sequence[str],
    *,
    operation: str,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> CommandOutput:
    output = runner(
        tuple(str(value) for value in command),
        operation=operation,
        cwd=cwd,
        environment=environment,
    )
    if not isinstance(output, CommandOutput):
        raise AndroidSherpaReferenceAppGateError(
            f"{operation} runner returned an invalid result"
        )
    return output


def _file_identity(path: Path, label: str, *, maximum: int) -> FileIdentity:
    try:
        size, digest = sha256_file(path, label, maximum=maximum)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    return FileIdentity(size, digest)


def _require_file_identity(
    path: Path,
    expected: FileIdentity,
    label: str,
    *,
    maximum: int,
) -> None:
    if _file_identity(path, label, maximum=maximum) != expected:
        raise AndroidSherpaReferenceAppGateError(f"{label} identity changed")


def _validate_sherpa_model(path: Path) -> tuple[Path, FileIdentity]:
    try:
        regular_file(
            path,
            "external Silero VAD model",
            maximum=SHERPA_MODEL_SIZE_BYTES,
        )
        resolved = path.resolve(strict=True)
    except (OSError, AndroidGateCommonError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaReferenceAppGateError(
            "could not resolve the external Silero VAD model"
        ) from error
    identity = _file_identity(
        resolved,
        "external Silero VAD model",
        maximum=SHERPA_MODEL_SIZE_BYTES,
    )
    expected = FileIdentity(SHERPA_MODEL_SIZE_BYTES, SHERPA_MODEL_SHA256)
    if identity != expected:
        raise AndroidSherpaReferenceAppGateError(
            "external Silero VAD model does not match the exact size and SHA-256"
        )
    return resolved, identity


def _read_exact_file(
    path: Path,
    expected: FileIdentity,
    label: str,
    *,
    maximum: int,
) -> bytes:
    _require_file_identity(path, expected, label, maximum=maximum)
    try:
        with path.open("rb") as stream:
            contents = stream.read(maximum + 1)
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            f"could not read {label}"
        ) from error
    if (
        len(contents) > maximum
        or FileIdentity(len(contents), hashlib.sha256(contents).hexdigest())
        != expected
    ):
        raise AndroidSherpaReferenceAppGateError(f"{label} identity changed")
    _require_file_identity(path, expected, label, maximum=maximum)
    return contents


def _generated_runtime_fixture_bytes() -> dict[str, bytes]:
    try:
        generated = fixture_generator.generated_files()
    except Exception as error:
        raise AndroidSherpaReferenceAppGateError(
            "runtime fixture generator failed"
        ) from error
    if (
        not isinstance(generated, dict)
        or tuple(sorted(generated)) != GENERATED_RUNTIME_FIXTURE_NAMES
    ):
        raise AndroidSherpaReferenceAppGateError(
            "runtime fixture generator did not return the exact seven-file set"
        )
    total = 0
    result: dict[str, bytes] = {}
    for name in GENERATED_RUNTIME_FIXTURE_NAMES:
        contents = generated.get(name)
        if (
            type(contents) is not bytes
            or not contents
            or len(contents) > MAX_RUNTIME_FIXTURE_FILE_BYTES
        ):
            raise AndroidSherpaReferenceAppGateError(
                f"generated runtime fixture {name} is outside its byte bound"
            )
        total += len(contents)
        if total > MAX_RUNTIME_FIXTURE_TOTAL_BYTES:
            raise AndroidSherpaReferenceAppGateError(
                "generated runtime fixtures exceed their aggregate byte bound"
            )
        result[name] = contents
    return result


def _patch_runtime_assets(pubspec: Path) -> None:
    try:
        pubspec = regular_file(
            pubspec,
            "sherpa reference pubspec",
            maximum=1024 * 1024,
        )
        source = pubspec.read_text(encoding="utf-8")
    except (AndroidGateCommonError, UnicodeDecodeError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference pubspec is not UTF-8"
        ) from error
    if (
        "\r" in source
        or source.count(STATIC_FLUTTER_STANZA) != 1
        or "\n  assets:\n" in source
    ):
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference pubspec lost the exact asset-free Flutter stanza"
        )
    updated = source.replace(STATIC_FLUTTER_STANZA, RUNTIME_FLUTTER_STANZA)
    if updated.replace(RUNTIME_FLUTTER_STANZA, STATIC_FLUTTER_STANZA) != source:
        raise AndroidSherpaReferenceAppGateError(
            "runtime qualification asset patch changed unexpected bytes"
        )
    pubspec.write_text(updated, encoding="utf-8", newline="")


def _runtime_fixture_inventory(
    asset_root: Path,
    expected: Mapping[str, FileIdentity],
) -> dict[str, FileIdentity]:
    if tuple(sorted(expected)) != RUNTIME_FIXTURE_NAMES:
        raise AndroidSherpaReferenceAppGateError(
            "runtime fixture expectation is not the exact eight-file set"
        )
    try:
        asset_root = directory(asset_root, "runtime qualification asset directory")
        entries = list(asset_root.iterdir())
    except (OSError, AndroidGateCommonError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaReferenceAppGateError(
            "could not inspect runtime qualification assets"
        ) from error
    if tuple(sorted(entry.name for entry in entries)) != RUNTIME_FIXTURE_NAMES:
        raise AndroidSherpaReferenceAppGateError(
            "runtime qualification assets are not the exact eight-file set"
        )
    result: dict[str, FileIdentity] = {}
    total = 0
    for entry in sorted(entries, key=lambda value: value.name):
        identity = _file_identity(
            entry,
            f"runtime qualification asset {entry.name}",
            maximum=MAX_RUNTIME_FIXTURE_FILE_BYTES,
        )
        if identity != expected[entry.name]:
            raise AndroidSherpaReferenceAppGateError(
                f"runtime qualification asset {entry.name} identity changed"
            )
        total += identity.size_bytes
        if total > MAX_RUNTIME_FIXTURE_TOTAL_BYTES:
            raise AndroidSherpaReferenceAppGateError(
                "runtime qualification assets exceed their aggregate byte bound"
            )
        result[entry.name] = identity
    return result


def _stage_runtime_fixtures(
    work_directory: Path,
    pubspec: Path,
    sherpa_model: Path,
    sherpa_model_identity: FileIdentity,
) -> dict[str, FileIdentity]:
    generated = _generated_runtime_fixture_bytes()
    model = _read_exact_file(
        sherpa_model,
        sherpa_model_identity,
        "external Silero VAD model",
        maximum=SHERPA_MODEL_SIZE_BYTES,
    )
    contents = {**generated, SHERPA_MODEL_NAME: model}
    if tuple(sorted(contents)) != RUNTIME_FIXTURE_NAMES:
        raise AndroidSherpaReferenceAppGateError(
            "runtime qualification fixture composition changed"
        )
    if sum(len(value) for value in contents.values()) > MAX_RUNTIME_FIXTURE_TOTAL_BYTES:
        raise AndroidSherpaReferenceAppGateError(
            "runtime qualification fixtures exceed their aggregate byte bound"
        )

    assets = work_directory / RUNTIME_ASSET_DIRECTORY.parent
    asset_root = work_directory / RUNTIME_ASSET_DIRECTORY
    try:
        if assets.exists() or assets.is_symlink():
            directory(assets, "sherpa reference asset root")
        else:
            assets.mkdir(mode=0o755)
        assets.chmod(0o755)
        if asset_root.exists() or asset_root.is_symlink():
            raise AndroidSherpaReferenceAppGateError(
                "runtime qualification asset directory already exists"
            )
        asset_root.mkdir(mode=0o755)
        for name in RUNTIME_FIXTURE_NAMES:
            output = asset_root / name
            with output.open("xb") as stream:
                stream.write(contents[name])
                stream.flush()
            output.chmod(0o644)
    except AndroidSherpaReferenceAppGateError:
        raise
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidSherpaReferenceAppGateError(
            "could not stage runtime qualification fixtures"
        ) from error

    _patch_runtime_assets(pubspec)
    expected = {
        name: FileIdentity(
            len(contents[name]),
            hashlib.sha256(contents[name]).hexdigest(),
        )
        for name in RUNTIME_FIXTURE_NAMES
    }
    return _runtime_fixture_inventory(asset_root, expected)


def _validate_archive_member_name(name: str, label: str) -> None:
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError as error:
        raise AndroidSherpaReferenceAppGateError(
            f"{label} contains a non-UTF-8 archive path"
        ) from error
    if (
        not encoded
        or len(encoded) > MAX_ARCHIVE_MEMBER_PATH_BYTES
        or any(value < 0x20 for value in encoded)
        or "\\" in name
        or name.startswith("/")
    ):
        raise AndroidSherpaReferenceAppGateError(
            f"{label} contains an unsafe archive path"
        )
    path = name[:-1] if name.endswith("/") else name
    parts = path.split("/")
    if (
        not path
        or any(part in ("", ".", "..") for part in parts)
        or (parts and len(parts[0]) == 2 and parts[0][1] == ":")
    ):
        raise AndroidSherpaReferenceAppGateError(
            f"{label} contains an unsafe archive path"
        )


def _audit_runtime_fixture_archive(
    archive_path: Path,
    expected: Mapping[str, FileIdentity],
    *,
    kind: str,
) -> None:
    if kind == "apk":
        prefix = APK_RUNTIME_ASSET_PREFIX
    elif kind == "aab":
        prefix = AAB_RUNTIME_ASSET_PREFIX
    else:
        raise AndroidSherpaReferenceAppGateError(
            "runtime fixture archive kind is not apk or aab"
        )
    if tuple(sorted(expected)) != RUNTIME_FIXTURE_NAMES:
        raise AndroidSherpaReferenceAppGateError(
            "runtime fixture archive expectation is not the exact eight-file set"
        )
    archive_identity = _file_identity(
        archive_path,
        f"runtime-provisioned Release {kind.upper()}",
        maximum=MAX_ARCHIVE_BYTES,
    )
    expected_paths = {f"{prefix}{name}": name for name in RUNTIME_FIXTURE_NAMES}
    selected: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise AndroidSherpaReferenceAppGateError(
                    f"runtime-provisioned Release {kind.upper()} has too many entries"
                )
            for entry in entries:
                name = entry.filename
                _validate_archive_member_name(
                    name,
                    f"runtime-provisioned Release {kind.upper()}",
                )
                if name in seen:
                    raise AndroidSherpaReferenceAppGateError(
                        f"runtime-provisioned Release {kind.upper()} has a "
                        "duplicate path"
                    )
                seen.add(name)
                base_name = name.rsplit("/", 1)[-1]
                is_qualification_path = "assets/qualification/" in name
                if name in expected_paths:
                    selected[name] = entry
                elif is_qualification_path or base_name in expected:
                    raise AndroidSherpaReferenceAppGateError(
                        f"runtime-provisioned Release {kind.upper()} has an "
                        "unexpected qualification asset path"
                    )
            if set(selected) != set(expected_paths):
                raise AndroidSherpaReferenceAppGateError(
                    f"runtime-provisioned Release {kind.upper()} does not contain "
                    "the exact qualification asset set"
                )
            total = 0
            for member_path, fixture_name in sorted(expected_paths.items()):
                entry = selected[member_path]
                identity = expected[fixture_name]
                mode = entry.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    entry.is_dir()
                    or (file_type not in (0, stat.S_IFREG))
                    or entry.flag_bits & 0x1
                    or entry.file_size != identity.size_bytes
                    or entry.file_size > MAX_RUNTIME_FIXTURE_FILE_BYTES
                    or entry.compress_size > MAX_ARCHIVE_BYTES
                    or (entry.compress_size == 0 and entry.file_size != 0)
                    or (
                        # The trusted target runner reads the APK directly.
                        # Bundle transport may recompress exact, size-bounded
                        # assets before bundletool produces installable splits.
                        kind == "apk"
                        and entry.compress_size > 0
                        and entry.file_size
                        > entry.compress_size
                        * MAX_TARGET_APK_ZIP_COMPRESSION_RATIO
                    )
                ):
                    raise AndroidSherpaReferenceAppGateError(
                        f"runtime-provisioned Release {kind.upper()} qualification "
                        f"asset {fixture_name} has invalid metadata"
                    )
                total += entry.file_size
                if total > MAX_RUNTIME_FIXTURE_TOTAL_BYTES:
                    raise AndroidSherpaReferenceAppGateError(
                        f"runtime-provisioned Release {kind.upper()} qualification "
                        "assets exceed their aggregate byte bound"
                    )
                with archive.open(entry, "r") as stream:
                    contents = stream.read(identity.size_bytes + 1)
                if (
                    len(contents) != identity.size_bytes
                    or hashlib.sha256(contents).hexdigest() != identity.sha256
                ):
                    raise AndroidSherpaReferenceAppGateError(
                        f"runtime-provisioned Release {kind.upper()} qualification "
                        f"asset {fixture_name} identity changed"
                    )
    except AndroidSherpaReferenceAppGateError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise AndroidSherpaReferenceAppGateError(
            f"could not inspect runtime-provisioned Release {kind.upper()}"
        ) from error
    _require_file_identity(
        archive_path,
        archive_identity,
        f"runtime-provisioned Release {kind.upper()}",
        maximum=MAX_ARCHIVE_BYTES,
    )


def _resolve_tool(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        tool = regular_file(resolved, label)
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidSherpaReferenceAppGateError(f"could not resolve {label}") from error
    if not os.access(tool, os.X_OK):
        raise AndroidSherpaReferenceAppGateError(f"{label} is not executable")
    return tool


def _is_generated_directory(relative: Path) -> bool:
    value = relative.as_posix()
    return any(
        value == exclusion or value.startswith(f"{exclusion}/")
        for exclusion in GENERATED_DIRECTORY_EXCLUSIONS
    )


def _is_generated_file(relative: Path) -> bool:
    if relative.as_posix() in GENERATED_FILE_EXCLUSIONS:
        return True
    return relative.suffix == ".iml" and relative.parts[:1] == ("android",)


def _is_excluded(relative: Path) -> bool:
    return _is_generated_directory(relative) or _is_generated_file(relative)


def _source_record(
    digest: Any,
    *,
    kind: str,
    relative: str,
    mode: int,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> None:
    record: list[object] = [kind, relative, mode]
    if size_bytes is not None:
        record.extend((size_bytes, sha256))
    encoded = json.dumps(
        record,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)


def _stable_source_file_identity(
    path: Path,
    relative: Path,
    *,
    maximum: int,
) -> tuple[FileIdentity, int]:
    try:
        before = path.lstat()
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "staged sherpa reference source changed during inspection: "
            f"{relative.as_posix()}"
        ) from error
    if not stat.S_ISREG(before.st_mode):
        kind = "symbolic link" if stat.S_ISLNK(before.st_mode) else "special file"
        raise AndroidSherpaReferenceAppGateError(
            f"staged sherpa reference source contains a {kind}: "
            f"{relative.as_posix()}"
        )
    if before.st_size > maximum:
        raise AndroidSherpaReferenceAppGateError(
            "staged sherpa reference source exceeds its byte bound"
        )

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    content_digest = hashlib.sha256()
    size_bytes = 0
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > maximum:
                    raise AndroidSherpaReferenceAppGateError(
                        "staged sherpa reference source exceeds its byte bound"
                    )
                content_digest.update(chunk)
            after_open = os.fstat(stream.fileno())
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "staged sherpa reference source changed during inspection: "
            f"{relative.as_posix()}"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        after_path = path.lstat()
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "staged sherpa reference source changed during inspection: "
            f"{relative.as_posix()}"
        ) from error
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
    after_open_identity = (
        after_open.st_dev,
        after_open.st_ino,
        after_open.st_mode,
        after_open.st_size,
        after_open.st_mtime_ns,
        after_open.st_ctime_ns,
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
        before_identity != opened_identity
        or before_identity != after_open_identity
        or before_identity != after_path_identity
        or size_bytes != before.st_size
    ):
        raise AndroidSherpaReferenceAppGateError(
            "staged sherpa reference source changed during inspection: "
            f"{relative.as_posix()}"
        )
    return FileIdentity(size_bytes, content_digest.hexdigest()), stat.S_IMODE(
        before.st_mode
    )


def _staged_source_identity(root: Path) -> StagedSourceIdentity:
    try:
        root = directory(root, "staged sherpa reference source")
    except AndroidGateCommonError as error:
        raise _common(error) from error

    manifest_digest = hashlib.sha256(b"fonix-staged-source-manifest-v1\0")
    root_metadata = root.lstat()
    _source_record(
        manifest_digest,
        kind="directory",
        relative=".",
        mode=stat.S_IMODE(root_metadata.st_mode),
    )
    entry_count = 1
    file_count = 0
    byte_count = 0
    for walk_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        walk_root_path = Path(walk_root)
        retained_directories: list[str] = []
        for name in directory_names:
            candidate = walk_root_path / name
            relative = candidate.relative_to(root)
            try:
                metadata = candidate.lstat()
            except OSError as error:
                raise AndroidSherpaReferenceAppGateError(
                    "staged sherpa reference source changed during inspection: "
                    f"{relative.as_posix()}"
                ) from error
            if stat.S_ISLNK(metadata.st_mode):
                raise AndroidSherpaReferenceAppGateError(
                    "staged sherpa reference source contains a symbolic link: "
                    f"{relative.as_posix()}"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise AndroidSherpaReferenceAppGateError(
                    "staged sherpa reference source contains a special entry: "
                    f"{relative.as_posix()}"
                )
            if relative.as_posix() in GENERATED_DIRECTORY_EXCLUSIONS:
                continue
            retained_directories.append(name)
            entry_count += 1
            if entry_count > MAX_TEMPLATE_ENTRIES:
                raise AndroidSherpaReferenceAppGateError(
                    "staged sherpa reference source exceeds its entry bound"
                )
            _source_record(
                manifest_digest,
                kind="directory",
                relative=relative.as_posix(),
                mode=stat.S_IMODE(metadata.st_mode),
            )
        directory_names[:] = retained_directories

        for name in file_names:
            candidate = walk_root_path / name
            relative = candidate.relative_to(root)
            if _is_generated_file(relative):
                try:
                    metadata = candidate.lstat()
                except OSError as error:
                    raise AndroidSherpaReferenceAppGateError(
                        "staged generated file changed during inspection: "
                        f"{relative.as_posix()}"
                    ) from error
                if not stat.S_ISREG(metadata.st_mode):
                    raise AndroidSherpaReferenceAppGateError(
                        "staged generated-file exclusion is not a regular file: "
                        f"{relative.as_posix()}"
                    )
                continue
            entry_count += 1
            file_count += 1
            if (
                entry_count > MAX_TEMPLATE_ENTRIES
                or file_count > MAX_TEMPLATE_FILES
            ):
                raise AndroidSherpaReferenceAppGateError(
                    "staged sherpa reference source exceeds its entry bound"
                )
            identity, mode = _stable_source_file_identity(
                candidate,
                relative,
                maximum=MAX_TEMPLATE_BYTES - byte_count,
            )
            byte_count += identity.size_bytes
            _source_record(
                manifest_digest,
                kind="file",
                relative=relative.as_posix(),
                mode=mode,
                size_bytes=identity.size_bytes,
                sha256=identity.sha256,
            )
    return StagedSourceIdentity(
        entry_count,
        file_count,
        byte_count,
        manifest_digest.hexdigest(),
    )


def _require_staged_source_identity(
    root: Path,
    expected: StagedSourceIdentity,
    label: str,
) -> None:
    if _staged_source_identity(root) != expected:
        raise AndroidSherpaReferenceAppGateError(
            f"{label} staged source identity changed"
        )


def _copy_template(source: Path, destination: Path) -> CopySummary:
    try:
        source = directory(source, "committed sherpa reference source")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise AndroidSherpaReferenceAppGateError(
            "Android sherpa work directory must be a new absolute path"
        )
    try:
        parent = directory(destination.parent.resolve(strict=True), "work parent")
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidSherpaReferenceAppGateError("Android work parent is invalid") from error
    destination = parent / destination.name
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise AndroidSherpaReferenceAppGateError(
            "work directory must be outside the committed template"
        )

    directories: list[Path] = []
    files: list[tuple[Path, int, int, str]] = []
    entry_count = 0
    total_bytes = 0
    for root, directory_names, file_names in os.walk(source, followlinks=False):
        directory_names.sort()
        file_names.sort()
        root_path = Path(root)
        for name in (*directory_names, *file_names):
            entry_count += 1
            if entry_count > MAX_TEMPLATE_ENTRIES:
                raise AndroidSherpaReferenceAppGateError(
                    "sherpa reference template exceeds copy-entry bound"
                )
            entry = root_path / name
            metadata = entry.lstat()
            relative = entry.relative_to(source)
            if stat.S_ISLNK(metadata.st_mode):
                raise AndroidSherpaReferenceAppGateError(
                    "sherpa reference template contains a symbolic link: "
                    f"{relative.as_posix()}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                if not _is_excluded(relative):
                    directories.append(relative)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise AndroidSherpaReferenceAppGateError(
                    "sherpa reference template contains a special file: "
                    f"{relative.as_posix()}"
                )
            if _is_excluded(relative):
                continue
            if (
                len(files) >= MAX_TEMPLATE_FILES
                or metadata.st_size > MAX_TEMPLATE_BYTES - total_bytes
            ):
                raise AndroidSherpaReferenceAppGateError(
                    "sherpa reference template exceeds copied-source bound"
                )
            data = entry.read_bytes()
            if len(data) != metadata.st_size:
                raise AndroidSherpaReferenceAppGateError(
                    "sherpa reference template changed during scan"
                )
            total_bytes += len(data)
            files.append(
                (
                    relative,
                    metadata.st_size,
                    stat.S_IMODE(metadata.st_mode),
                    hashlib.sha256(data).hexdigest(),
                )
            )

    destination.mkdir(mode=0o755)
    for relative in sorted(directories, key=lambda value: value.as_posix()):
        (destination / relative).mkdir(parents=True, exist_ok=True, mode=0o755)
    for relative, size, mode, digest in sorted(
        files, key=lambda value: value[0].as_posix()
    ):
        source_file = source / relative
        destination_file = destination / relative
        destination_file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source_file, flags)
        try:
            with os.fdopen(descriptor, "rb") as input_stream:
                descriptor = -1
                data = input_stream.read(MAX_TEMPLATE_BYTES + 1)
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise AndroidSherpaReferenceAppGateError(
                    "sherpa reference template changed between scan and copy"
                )
            with destination_file.open("xb") as output:
                output.write(data)
                output.flush()
            destination_file.chmod(mode & 0o777)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return CopySummary(len(files), total_bytes)


def _patch_for_host_tests(
    pubspec: Path,
    lockfile: Path,
    repository: Path,
) -> FileIdentity:
    try:
        pubspec = regular_file(pubspec, "sherpa reference pubspec", maximum=1024 * 1024)
        lockfile = regular_file(
            lockfile,
            "sherpa reference pubspec lock",
            maximum=MAX_LOCK_BYTES,
        )
        repository = directory(repository, "Fonix repository")
        pubspec_source = pubspec.read_text(encoding="utf-8")
        lock_source = lockfile.read_text(encoding="utf-8")
    except (AndroidGateCommonError, UnicodeDecodeError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference dependency files are not UTF-8"
        ) from error
    if "\r" in pubspec_source or "\r" in lock_source:
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference dependency files must use LF newlines"
        )
    if (
        pubspec_source.count(RELATIVE_FONIX_DEPENDENCY) != 1
        or pubspec_source.count(ANDROID_HOOK) != 1
        or lock_source.count(RELATIVE_FONIX_LOCK) != 1
    ):
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference dependency files no longer match the exact template"
        )
    try:
        _validate_pubspec_lock(lock_source.encode("utf-8"), ABI, SHERPA_VERSION)
    except LoadOrderReceiptError as error:
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference pubspec lock does not bind the exact hosted "
            f"sherpa-onnx {SHERPA_VERSION} graph"
        ) from error

    absolute_dependency = (
        "  fonix:\n"
        f"    path: {json.dumps(repository.as_posix(), ensure_ascii=True)}\n"
    )
    absolute_lock = (
        f"      path: {json.dumps(repository.as_posix(), ensure_ascii=True)}\n"
        "      relative: false\n"
    )
    updated_pubspec = pubspec_source.replace(
        RELATIVE_FONIX_DEPENDENCY, absolute_dependency
    ).replace(ANDROID_HOOK, HOST_TEST_HOOK)
    updated_lock = lock_source.replace(RELATIVE_FONIX_LOCK, absolute_lock)
    if (
        updated_pubspec.replace(absolute_dependency, RELATIVE_FONIX_DEPENDENCY)
        .replace(HOST_TEST_HOOK, ANDROID_HOOK)
        != pubspec_source
        or updated_lock.replace(absolute_lock, RELATIVE_FONIX_LOCK) != lock_source
    ):
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference path patch changed unexpected bytes"
        )
    pubspec.write_text(updated_pubspec, encoding="utf-8", newline="")
    lockfile.write_text(updated_lock, encoding="utf-8", newline="")
    return _file_identity(
        lockfile,
        "patched sherpa reference pubspec lock",
        maximum=MAX_LOCK_BYTES,
    )


def _select_android_hook(pubspec: Path) -> None:
    try:
        pubspec = regular_file(pubspec, "sherpa reference pubspec", maximum=1024 * 1024)
        source = pubspec.read_text(encoding="utf-8")
    except (AndroidGateCommonError, UnicodeDecodeError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference pubspec is not UTF-8"
        ) from error
    if "\r" in source or source.count(HOST_TEST_HOOK) != 1:
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference pubspec lost the exact host-test hook"
        )
    updated = source.replace(HOST_TEST_HOOK, ANDROID_HOOK)
    if updated.replace(ANDROID_HOOK, HOST_TEST_HOOK) != source:
        raise AndroidSherpaReferenceAppGateError(
            "Android sherpa hook patch changed unexpected bytes"
        )
    pubspec.write_text(updated, encoding="utf-8", newline="")


def _execute_with_staged_source_guard(
    runner: CommandRunner,
    command: Sequence[str],
    *,
    operation: str,
    work_directory: Path,
    environment: Mapping[str, str],
    expected_source: StagedSourceIdentity,
    hosted_package_roots: Mapping[str, Path] | None = None,
    expected_hosted_packages: Mapping[str, HostedPackageTreeIdentity] | None = None,
    expected_hosted_graph: HostedPackageGraphIdentity | None = None,
) -> CommandOutput:
    if (hosted_package_roots is None) != (expected_hosted_packages is None):
        raise AndroidSherpaReferenceAppGateError(
            f"{operation} has an incomplete hosted-package guard"
        )
    _require_staged_source_identity(
        work_directory,
        expected_source,
        f"{operation} input",
    )
    if expected_hosted_graph is not None:
        if hosted_package_roots is None:
            raise AndroidSherpaReferenceAppGateError(
                f"{operation} has an incomplete generated-package guard"
            )
        _require_hosted_package_graph(
            work_directory,
            hosted_package_roots,
            expected_hosted_graph,
            f"{operation} input",
        )
    if hosted_package_roots is not None and expected_hosted_packages is not None:
        _require_hosted_package_inventory(
            hosted_package_roots,
            expected_hosted_packages,
            f"{operation} input",
        )
    try:
        return _execute(
            runner,
            command,
            operation=operation,
            cwd=work_directory,
            environment=environment,
        )
    finally:
        if expected_hosted_graph is not None:
            assert hosted_package_roots is not None
            _require_hosted_package_graph(
                work_directory,
                hosted_package_roots,
                expected_hosted_graph,
                f"{operation} output",
            )
        if hosted_package_roots is not None and expected_hosted_packages is not None:
            _require_hosted_package_inventory(
                hosted_package_roots,
                expected_hosted_packages,
                f"{operation} output",
            )
        _require_staged_source_identity(
            work_directory,
            expected_source,
            f"{operation} output",
        )


def _run_locked_pub_get(
    runner: CommandRunner,
    flutter: Path,
    work_directory: Path,
    environment: Mapping[str, str],
    expected_lock: FileIdentity,
    expected_source: StagedSourceIdentity,
    hosted_package_roots: Mapping[str, Path] | None = None,
    expected_hosted_packages: Mapping[str, HostedPackageTreeIdentity] | None = None,
    expected_input_graph: HostedPackageGraphIdentity | None = None,
    *,
    operation: str,
) -> None:
    lockfile = work_directory / "pubspec.lock"
    _require_file_identity(
        lockfile,
        expected_lock,
        f"{operation} input lockfile",
        maximum=MAX_LOCK_BYTES,
    )
    if expected_input_graph is not None:
        if hosted_package_roots is None:
            raise AndroidSherpaReferenceAppGateError(
                f"{operation} has an incomplete generated-package guard"
            )
        _require_hosted_package_graph(
            work_directory,
            hosted_package_roots,
            expected_input_graph,
            f"{operation} input",
        )
    try:
        _execute_with_staged_source_guard(
            runner,
            (str(flutter), "pub", "get", "--offline", "--enforce-lockfile"),
            operation=operation,
            work_directory=work_directory,
            environment=environment,
            expected_source=expected_source,
            hosted_package_roots=hosted_package_roots,
            expected_hosted_packages=expected_hosted_packages,
        )
    finally:
        _require_file_identity(
            lockfile,
            expected_lock,
            f"{operation} output lockfile",
            maximum=MAX_LOCK_BYTES,
        )


def _verify_local_properties(path: Path, android_sdk: Path) -> None:
    try:
        source = regular_file(
            path,
            "generated Android local.properties",
            maximum=MAX_LOCAL_PROPERTIES_BYTES,
        ).read_text(encoding="utf-8")
    except (AndroidGateCommonError, UnicodeDecodeError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaReferenceAppGateError(
            "generated Android local.properties is not UTF-8"
        ) from error
    if "\r" in source or "\x00" in source:
        raise AndroidSherpaReferenceAppGateError(
            "generated Android local.properties has invalid line encoding"
        )
    expected = f"sdk.dir={android_sdk}"
    sdk_lines = [line for line in source.splitlines() if line.startswith("sdk.dir")]
    if sdk_lines != [expected]:
        raise AndroidSherpaReferenceAppGateError(
            "generated Android local.properties is not bound to --android-sdk"
        )


def _validate_android_sdk(android_sdk: Path) -> Path:
    try:
        android_sdk = directory(android_sdk.resolve(strict=True), "Android SDK")
        ndk_properties = regular_file(
            android_sdk / "ndk" / ANDROID_NDK_VERSION / "source.properties",
            "pinned Android NDK identity",
            maximum=64 * 1024,
        ).read_text(encoding="utf-8")
        directory(
            android_sdk / "build-tools" / ANDROID_BUILD_TOOLS_VERSION,
            "pinned Android build tools",
        )
        regular_file(
            android_sdk / "platforms" / f"android-{ANDROID_COMPILE_API}" / "android.jar",
            "pinned Android platform",
        )
    except (OSError, AndroidGateCommonError, UnicodeDecodeError) as error:
        raise AndroidSherpaReferenceAppGateError(
            "pinned Android SDK/NDK inputs are unavailable"
        ) from error
    revision_lines = [
        line.strip()
        for line in ndk_properties.splitlines()
        if line.strip().startswith("Pkg.Revision")
    ]
    if revision_lines != [f"Pkg.Revision = {ANDROID_NDK_VERSION}"]:
        raise AndroidSherpaReferenceAppGateError("Android NDK identity changed")
    return android_sdk


def _validate_java_home(
    java_home: Path,
    runner: CommandRunner,
) -> tuple[Path, Path, str]:
    try:
        java_home = directory(java_home.resolve(strict=True), "Java home")
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidSherpaReferenceAppGateError("could not resolve Java home") from error
    java = _resolve_tool(java_home / "bin/java", "Java executable")
    environment = tool_environment()
    environment["JAVA_HOME"] = str(java_home)
    output = _execute(
        runner,
        (str(java), "-XshowSettings:properties", "-version"),
        operation="Java identity check",
        environment=environment,
    )
    properties: dict[str, str] = {}
    for line in f"{output.stdout}\n{output.stderr}".splitlines():
        match = re.fullmatch(r"\s*(java\.[A-Za-z.]+) = (.{1,1024})", line)
        if match is not None:
            properties[match.group(1)] = match.group(2)
    if (
        properties.get("java.version") != SUPPORTED_JAVA_VERSION
        or properties.get("java.specification.version") != "21"
        or "OpenJDK" not in properties.get("java.vm.name", "")
    ):
        raise AndroidSherpaReferenceAppGateError(
            f"Android sherpa gate requires OpenJDK {SUPPORTED_JAVA_VERSION}"
        )
    try:
        reported_home = Path(properties.get("java.home", "")).resolve(strict=True)
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "Java reported an invalid home"
        ) from error
    if reported_home != java_home:
        raise AndroidSherpaReferenceAppGateError(
            "Java executable is not bound to --java-home"
        )
    vendor = properties.get("java.vendor", "")
    if not vendor or len(vendor.encode("utf-8")) > 256:
        raise AndroidSherpaReferenceAppGateError(
            "Java vendor identity is outside its bound"
        )
    return java_home, java, vendor


def _verify_flutter(
    flutter: Path,
    runner: CommandRunner,
    environment: Mapping[str, str],
) -> dict[str, object]:
    output = _execute(
        runner,
        (str(flutter), "--version", "--machine"),
        operation="Flutter version check",
        environment=environment,
    )
    try:
        value = strict_json(output.stdout, "Flutter version", maximum=256 * 1024)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if (
        not isinstance(value, dict)
        or value.get("frameworkRevision") != VALIDATED_FLUTTER_REVISION
    ):
        raise AndroidSherpaReferenceAppGateError(
            "Flutter revision differs from Android sherpa gate pin"
        )
    return value


def _verify_source_manifest(
    repository: Path,
    runner: CommandRunner,
    environment: Mapping[str, str],
    *,
    phase: str,
) -> FileIdentity:
    manifest = repository / MANIFEST
    identity = _file_identity(
        manifest,
        "source checksum manifest",
        maximum=MAX_SOURCE_MANIFEST_BYTES,
    )
    _execute(
        runner,
        (
            sys.executable,
            "-B",
            str(repository / "tool/ci/source_checksum_manifest.py"),
            "check",
            "--repository",
            str(repository),
            "--manifest",
            str(manifest),
        ),
        operation=f"source checksum manifest {phase}",
        environment=environment,
    )
    _require_file_identity(
        manifest,
        identity,
        f"source checksum manifest {phase}",
        maximum=MAX_SOURCE_MANIFEST_BYTES,
    )
    return identity


def _fingerprint_escape_path(path: Path, label: str) -> str | None:
    if not path.exists() and not path.is_symlink():
        return None
    digest = hashlib.sha256()
    entry_count = 0
    total_bytes = 0

    def add(relative: str, candidate: Path) -> None:
        nonlocal entry_count, total_bytes
        entry_count += 1
        if entry_count > MAX_ESCAPE_ENTRIES:
            raise AndroidSherpaReferenceAppGateError(
                f"{label} exceeds the generated-state entry bound"
            )
        metadata = candidate.lstat()
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(stat.S_IFMT(metadata.st_mode).to_bytes(4, "big"))
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(candidate).encode("utf-8")
            if len(target) > 4096:
                raise AndroidSherpaReferenceAppGateError(
                    f"{label} contains an oversized symbolic link"
                )
            digest.update(len(target).to_bytes(4, "big"))
            digest.update(target)
            return
        if stat.S_ISDIR(metadata.st_mode):
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise AndroidSherpaReferenceAppGateError(
                f"{label} contains a special generated-state entry"
            )
        if metadata.st_size > MAX_ESCAPE_BYTES - total_bytes:
            raise AndroidSherpaReferenceAppGateError(
                f"{label} exceeds the generated-state byte bound"
            )
        before = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        data = candidate.read_bytes()
        after_metadata = candidate.lstat()
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
        )
        if before != after or len(data) != metadata.st_size:
            raise AndroidSherpaReferenceAppGateError(
                f"{label} changed while its generated state was inspected"
            )
        total_bytes += len(data)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    add(".", path)
    if path.is_dir() and not path.is_symlink():
        for root, directory_names, file_names in os.walk(path, followlinks=False):
            directory_names.sort()
            file_names.sort()
            root_path = Path(root)
            for name in (*directory_names, *file_names):
                candidate = root_path / name
                add(candidate.relative_to(path).as_posix(), candidate)
    return digest.hexdigest()


def _repository_escape_snapshot(repository: Path) -> dict[str, str | None]:
    return {
        relative.as_posix(): _fingerprint_escape_path(
            repository / relative,
            f"repository {relative.as_posix()}",
        )
        for relative in REPOSITORY_ESCAPE_PATHS
    }


def _hosted_tree_record(digest: Any, record: Sequence[object]) -> None:
    encoded = json.dumps(
        list(record),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)


def _canonical_hosted_relative(relative: Path) -> str:
    value = relative.as_posix()
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise AndroidSherpaReferenceAppGateError(
            "hosted sherpa package contains a non-UTF-8 path"
        ) from error
    if (
        not encoded
        or len(encoded) > 4096
        or value.startswith("/")
        or "\\" in value
        or any(character in {"", ".", ".."} for character in relative.parts)
        or any(byte < 0x20 or byte == 0x7F for byte in encoded)
    ):
        raise AndroidSherpaReferenceAppGateError(
            "hosted sherpa package contains a non-canonical path"
        )
    return value


def _hosted_package_tree_identity(
    root: Path,
    package_name: str,
    archive_sha256: str,
) -> HostedPackageTreeIdentity:
    try:
        root = directory(root, f"resolved {package_name} package")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if re.fullmatch(r"[0-9a-f]{64}", archive_sha256) is None:
        raise AndroidSherpaReferenceAppGateError(
            "the hosted sherpa archive digest pin is invalid"
        )

    digest = hashlib.sha256(b"fonix-hosted-package-tree-v1\0")
    _hosted_tree_record(
        digest,
        ("package", package_name, SHERPA_VERSION, archive_sha256),
    )
    records: list[tuple[bytes, tuple[object, ...]]] = []
    seen: set[str] = set()
    seen_casefolded: set[str] = set()
    directory_count = 0
    file_count = 0
    byte_count = 0
    entry_count = 0
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for walk_root, directory_names, file_names in walker:
            directory_names.sort()
            file_names.sort()
            walk_root_path = Path(walk_root)
            for name in directory_names:
                candidate = walk_root_path / name
                relative = candidate.relative_to(root)
                canonical = _canonical_hosted_relative(relative)
                metadata = candidate.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(
                    metadata.st_mode
                ):
                    raise AndroidSherpaReferenceAppGateError(
                        f"resolved {package_name} contains a non-directory entry"
                    )
                records.append(
                    (canonical.encode("utf-8"), ("directory", canonical))
                )
                directory_count += 1
                entry_count += 1
                if entry_count > MAX_HOSTED_PACKAGE_ENTRIES:
                    raise AndroidSherpaReferenceAppGateError(
                        f"resolved {package_name} exceeds its tree-entry bound"
                    )
            for name in file_names:
                candidate = walk_root_path / name
                relative = candidate.relative_to(root)
                canonical = _canonical_hosted_relative(relative)
                entry_count += 1
                file_count += 1
                if (
                    entry_count > MAX_HOSTED_PACKAGE_ENTRIES
                    or file_count > MAX_HOSTED_PACKAGE_FILES
                ):
                    raise AndroidSherpaReferenceAppGateError(
                        f"resolved {package_name} exceeds its tree-entry bound"
                    )
                identity, _mode = _stable_source_file_identity(
                    candidate,
                    relative,
                    maximum=MAX_HOSTED_PACKAGE_BYTES - byte_count,
                )
                byte_count += identity.size_bytes
                records.append(
                    (
                        canonical.encode("utf-8"),
                        (
                            "file",
                            canonical,
                            identity.size_bytes,
                            identity.sha256,
                        ),
                    )
                )
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            f"could not inspect resolved {package_name} package tree"
        ) from error
    if entry_count > MAX_HOSTED_PACKAGE_ENTRIES:
        raise AndroidSherpaReferenceAppGateError(
            f"resolved {package_name} exceeds its tree-entry bound"
        )

    for encoded, record in sorted(records, key=lambda value: value[0]):
        canonical = record[1]
        assert isinstance(canonical, str)
        if canonical in seen or canonical.casefold() in seen_casefolded:
            raise AndroidSherpaReferenceAppGateError(
                f"resolved {package_name} contains a path collision"
            )
        seen.add(canonical)
        seen_casefolded.add(canonical.casefold())
        _hosted_tree_record(digest, record)
    return HostedPackageTreeIdentity(
        directory_count,
        file_count,
        byte_count,
        digest.hexdigest(),
    )


def _hosted_package_inventory(
    roots: Mapping[str, Path],
) -> dict[str, HostedPackageTreeIdentity]:
    if set(SHERPA_HOSTED_PACKAGE_PINS) != set(SHERPA_HOSTED_PACKAGES):
        raise AndroidSherpaReferenceAppGateError(
            "the gate's closed hosted sherpa package pins are invalid"
        )
    if set(roots) != set(SHERPA_HOSTED_PACKAGES):
        raise AndroidSherpaReferenceAppGateError(
            "the resolved hosted sherpa package inventory is incomplete"
        )
    result: dict[str, HostedPackageTreeIdentity] = {}
    for name in sorted(SHERPA_HOSTED_PACKAGES):
        pin = SHERPA_HOSTED_PACKAGE_PINS[name]
        identity = _hosted_package_tree_identity(
            roots[name],
            name,
            pin.archive_sha256,
        )
        if identity != pin.tree:
            raise AndroidSherpaReferenceAppGateError(
                f"resolved {name} does not match the exact selected "
                f"{SHERPA_VERSION} hosted package tree"
            )
        result[name] = identity
    return result


def _require_hosted_package_inventory(
    roots: Mapping[str, Path],
    expected: Mapping[str, HostedPackageTreeIdentity],
    label: str,
) -> None:
    if _hosted_package_inventory(roots) != dict(expected):
        raise AndroidSherpaReferenceAppGateError(
            f"{label} hosted sherpa package inventory identity changed"
        )


def _resolve_sherpa_package_roots(package_config: Path) -> dict[str, Path]:
    try:
        raw = regular_file(
            package_config,
            "sherpa reference package configuration",
            maximum=MAX_PACKAGE_CONFIG_BYTES,
        ).read_bytes()
        value = strict_json(
            raw,
            "sherpa reference package configuration",
            maximum=MAX_PACKAGE_CONFIG_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if not isinstance(value, dict) or value.get("configVersion") != 2:
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference package configuration has the wrong schema"
        )
    packages = value.get("packages")
    if not isinstance(packages, list) or not 1 <= len(packages) <= 4096:
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference package inventory is outside its bound"
        )
    result: dict[str, Path] = {}
    for package_name in SHERPA_HOSTED_PACKAGES:
        matches = [
            entry
            for entry in packages
            if isinstance(entry, dict) and entry.get("name") == package_name
        ]
        if len(matches) != 1:
            raise AndroidSherpaReferenceAppGateError(
                f"package configuration must resolve exactly one {package_name}"
            )
        entry = matches[0]
        root_uri = entry.get("rootUri")
        package_uri = entry.get("packageUri")
        if not isinstance(root_uri, str) or package_uri != "lib/":
            raise AndroidSherpaReferenceAppGateError(
                f"{package_name} package configuration is not canonical"
            )
        parsed = urlsplit(root_uri)
        if parsed.query or parsed.fragment or parsed.netloc:
            raise AndroidSherpaReferenceAppGateError(
                f"{package_name} root URI is not a local path"
            )
        decoded = unquote(parsed.path)
        if (
            not decoded
            or "\x00" in decoded
            or any(ord(character) < 0x20 for character in decoded)
        ):
            raise AndroidSherpaReferenceAppGateError(
                f"{package_name} root URI is not a canonical local path"
            )
        if parsed.scheme == "file":
            package_root = Path(decoded)
        elif not parsed.scheme:
            package_root = package_config.parent / decoded
        else:
            raise AndroidSherpaReferenceAppGateError(
                f"{package_name} root URI is not a file URI"
            )
        try:
            result[package_name] = directory(
                package_root.resolve(strict=True),
                f"resolved {package_name} package",
            )
        except (OSError, ValueError, AndroidGateCommonError) as error:
            raise AndroidSherpaReferenceAppGateError(
                f"could not resolve {package_name} hosted package"
            ) from error
    resolved_roots = list(result.values())
    if len(set(resolved_roots)) != len(resolved_roots) or any(
        left in right.parents or right in left.parents
        for index, left in enumerate(resolved_roots)
        for right in resolved_roots[index + 1 :]
    ):
        raise AndroidSherpaReferenceAppGateError(
            "resolved hosted sherpa package roots overlap"
        )
    return result


def _validate_android_plugin_inventory(
    path: Path,
    package_roots: Mapping[str, Path],
) -> tuple[str, ...]:
    if set(package_roots) != set(SHERPA_HOSTED_PACKAGES):
        raise AndroidSherpaReferenceAppGateError(
            "generated Android plugin validation has an incomplete package graph"
        )
    try:
        raw = regular_file(
            path,
            "generated Flutter plugin inventory",
            maximum=MAX_PLUGIN_INVENTORY_BYTES,
        ).read_bytes()
        value = strict_json(
            raw,
            "generated Flutter plugin inventory",
            maximum=MAX_PLUGIN_INVENTORY_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    plugins = value.get("plugins") if isinstance(value, dict) else None
    android_plugins = plugins.get("android") if isinstance(plugins, dict) else None
    if not isinstance(android_plugins, list) or len(android_plugins) != len(
        SHERPA_ANDROID_PACKAGES
    ):
        raise AndroidSherpaReferenceAppGateError(
            "generated Android plugin inventory is not the exact sherpa set"
        )
    expected_keys = {
        "name",
        "path",
        "native_build",
        "dependencies",
        "dev_dependency",
    }
    observed: dict[str, Path] = {}
    for entry in android_plugins:
        if not isinstance(entry, dict) or set(entry) != expected_keys:
            raise AndroidSherpaReferenceAppGateError(
                "generated Android plugin entry has the wrong closed shape"
            )
        name = entry.get("name")
        raw_path = entry.get("path")
        if (
            name not in SHERPA_ANDROID_PACKAGES
            or not isinstance(raw_path, str)
            or not raw_path
            or "\x00" in raw_path
            or any(ord(character) < 0x20 for character in raw_path)
            or entry.get("native_build") is not True
            or entry.get("dependencies") != []
            or entry.get("dev_dependency") is not False
        ):
            raise AndroidSherpaReferenceAppGateError(
                "generated Android plugin entry is outside the closed sherpa graph"
            )
        try:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                raise ValueError("plugin path is relative")
            resolved = directory(
                candidate.resolve(strict=True),
                f"generated {name} Android plugin package",
            )
        except (OSError, ValueError, AndroidGateCommonError) as error:
            raise AndroidSherpaReferenceAppGateError(
                f"generated {name} Android plugin path is invalid"
            ) from error
        if name in observed or resolved != package_roots[name]:
            raise AndroidSherpaReferenceAppGateError(
                "generated Android plugin paths do not bind the resolved package graph"
            )
        observed[name] = resolved
    if set(observed) != set(SHERPA_ANDROID_PACKAGES):
        raise AndroidSherpaReferenceAppGateError(
            "generated Android plugin inventory is not the exact sherpa set"
        )
    return tuple(sorted(observed))


def _resolved_hosted_package_graph(
    work_directory: Path,
) -> tuple[dict[str, Path], HostedPackageGraphIdentity]:
    package_config = work_directory / ".dart_tool/package_config.json"
    package_config_identity = _file_identity(
        package_config,
        "generated sherpa package configuration",
        maximum=MAX_PACKAGE_CONFIG_BYTES,
    )
    package_roots = _resolve_sherpa_package_roots(package_config)
    _require_file_identity(
        package_config,
        package_config_identity,
        "generated sherpa package configuration inspection",
        maximum=MAX_PACKAGE_CONFIG_BYTES,
    )

    plugin_inventory = work_directory / ".flutter-plugins-dependencies"
    plugin_identity = _file_identity(
        plugin_inventory,
        "generated Flutter plugin inventory",
        maximum=MAX_PLUGIN_INVENTORY_BYTES,
    )
    android_plugins = _validate_android_plugin_inventory(
        plugin_inventory,
        package_roots,
    )
    _require_file_identity(
        plugin_inventory,
        plugin_identity,
        "generated Flutter plugin inventory inspection",
        maximum=MAX_PLUGIN_INVENTORY_BYTES,
    )
    return package_roots, HostedPackageGraphIdentity(
        package_config_identity,
        plugin_identity,
        android_plugins,
    )


def _require_hosted_package_graph(
    work_directory: Path,
    expected_roots: Mapping[str, Path],
    expected: HostedPackageGraphIdentity,
    label: str,
) -> None:
    roots, identity = _resolved_hosted_package_graph(work_directory)
    if roots != dict(expected_roots):
        raise AndroidSherpaReferenceAppGateError(
            f"{label} generated hosted-package roots changed"
        )
    if identity != expected:
        raise AndroidSherpaReferenceAppGateError(
            f"{label} generated hosted-package graph identity changed"
        )


def _resolve_sherpa_jni_root(package_config: Path) -> Path:
    roots = _resolve_sherpa_package_roots(package_config)
    try:
        return directory(
            roots[SHERPA_PACKAGE] / "android/src/main/jniLibs",
            f"resolved {SHERPA_PACKAGE} jniLibs",
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error


def _sherpa_native_inventory(jni_root: Path) -> dict[str, FileIdentity]:
    try:
        jni_root = directory(jni_root, f"{SHERPA_PACKAGE} jniLibs")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if set(SHERPA_NATIVE_SHA256) != set(SHERPA_NATIVE_LIBRARY_NAMES):
        raise AndroidSherpaReferenceAppGateError(
            "the gate's closed sherpa native digest inventory is invalid"
        )

    try:
        root_entries = sorted(jni_root.iterdir(), key=lambda value: value.name)
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "could not inspect the resolved sherpa native input"
        ) from error
    if [entry.name for entry in root_entries] != [ABI]:
        raise AndroidSherpaReferenceAppGateError(
            "resolved sherpa jniLibs must contain exactly the selected ABI"
        )
    try:
        abi_root = directory(root_entries[0], f"resolved sherpa {ABI} native input")
    except AndroidGateCommonError as error:
        raise _common(error) from error

    try:
        entries = sorted(abi_root.iterdir(), key=lambda value: value.name)
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            f"could not inspect the resolved sherpa {ABI} native input"
        ) from error
    expected_names = sorted(SHERPA_NATIVE_LIBRARY_NAMES)
    if [entry.name for entry in entries] != expected_names:
        raise AndroidSherpaReferenceAppGateError(
            f"resolved sherpa {ABI} native inventory is not the exact selected "
            "three-library set"
        )

    result: dict[str, FileIdentity] = {}
    for entry in entries:
        identity = _file_identity(
            entry,
            f"resolved sherpa {ABI} {entry.name}",
            maximum=MAX_SHERPA_LIBRARY_BYTES,
        )
        if identity.sha256 != SHERPA_NATIVE_SHA256[entry.name]:
            raise AndroidSherpaReferenceAppGateError(
                f"resolved sherpa {ABI} {entry.name} does not match the exact "
                f"selected {SHERPA_VERSION} native digest"
            )
        result[entry.name] = identity
    return result


def _require_sherpa_native_inventory(
    jni_root: Path,
    expected: Mapping[str, FileIdentity],
    label: str,
) -> None:
    if _sherpa_native_inventory(jni_root) != dict(expected):
        raise AndroidSherpaReferenceAppGateError(
            f"{label} sherpa native inventory identity changed"
        )


def _stage_wrapper_input(work_directory: Path) -> tuple[Path, FileIdentity]:
    build_root = work_directory / HOOK_BUILD_RELATIVE
    try:
        build_root = directory(build_root, "Fonix native-assets hook output")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    candidates: list[Path] = []
    for child in sorted(build_root.iterdir(), key=lambda value: value.name):
        metadata = child.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AndroidSherpaReferenceAppGateError(
                "Fonix hook build inventory contains a non-directory entry"
            )
        if re.fullmatch(r"[0-9a-f]{10}", child.name) is None:
            raise AndroidSherpaReferenceAppGateError(
                "Fonix hook build inventory contains an unexpected cache key"
            )
        candidate = child / SHIM_NAME
        if candidate.exists() or candidate.is_symlink():
            try:
                candidates.append(
                    regular_file(candidate, "raw external-owner Fonix shim")
                )
            except AndroidGateCommonError as error:
                raise _common(error) from error
    if len(candidates) != 1:
        raise AndroidSherpaReferenceAppGateError(
            "expected exactly one raw Android external-owner Fonix shim; "
            f"found {len(candidates)}"
        )
    source = candidates[0]
    source_identity = _file_identity(
        source,
        "raw external-owner Fonix shim",
        maximum=MAX_SHIM_BYTES,
    )
    output_root = work_directory / ".sherpa-static-evidence/wrapper-input"
    if output_root.exists() or output_root.is_symlink():
        raise AndroidSherpaReferenceAppGateError(
            "wrapper evidence output already exists"
        )
    output = output_root / ABI / SHIM_NAME
    output.parent.mkdir(parents=True, mode=0o755)
    data = source.read_bytes()
    if (
        len(data) != source_identity.size_bytes
        or hashlib.sha256(data).hexdigest() != source_identity.sha256
    ):
        raise AndroidSherpaReferenceAppGateError(
            "raw external-owner Fonix shim changed before evidence staging"
        )
    with output.open("xb") as stream:
        stream.write(data)
        stream.flush()
    output.chmod(0o755)
    _require_file_identity(
        output,
        source_identity,
        "staged external-owner Fonix shim",
        maximum=MAX_SHIM_BYTES,
    )
    _require_file_identity(
        source,
        source_identity,
        "raw external-owner Fonix shim",
        maximum=MAX_SHIM_BYTES,
    )
    return output_root, source_identity


def _require_raw_library_inventory(
    report: object,
    *,
    artifact: Path,
    expected: Mapping[str, FileIdentity],
    ort_candidates: list[str],
) -> None:
    if not isinstance(report, dict) or set(report) != {
        "artifact",
        "kind",
        "duplicate_paths",
        "invalid_archive_paths",
        "ort_candidates",
        "invalid_libraries",
        "libraries",
    }:
        raise AndroidSherpaReferenceAppGateError(
            "raw native audit report has the wrong closed shape"
        )
    libraries = report.get("libraries")
    if (
        report.get("artifact") != str(artifact)
        or report.get("kind") != "directory"
        or report.get("duplicate_paths") != []
        or report.get("invalid_archive_paths") != []
        or report.get("invalid_libraries") != []
        or report.get("ort_candidates") != ort_candidates
        or not isinstance(libraries, list)
        or len(libraries) != len(expected)
    ):
        raise AndroidSherpaReferenceAppGateError(
            "raw native audit report is not bound to the expected artifact"
        )
    observed: dict[str, dict[str, Any]] = {}
    for entry in libraries:
        if not isinstance(entry, dict):
            raise AndroidSherpaReferenceAppGateError(
                "raw native audit contains a malformed library entry"
            )
        name = entry.get("name")
        elf = entry.get("elf")
        if (
            name not in expected
            or name in observed
            or entry.get("abi") != ABI
            or entry.get("path") != f"{ABI}/{name}"
            or entry.get("size") != expected[name].size_bytes
            or entry.get("sha256") != expected[name].sha256
            or not isinstance(elf, dict)
            or elf.get("class") != 64
            or elf.get("machine") != 183
            or elf.get("pageSize16KiBCompatible") is not True
        ):
            raise AndroidSherpaReferenceAppGateError(
                "raw native audit library inventory does not match the selected bytes"
            )
        observed[name] = entry
    if set(observed) != set(expected):
        raise AndroidSherpaReferenceAppGateError(
            "raw native audit library inventory is incomplete"
        )


def _validate_raw_audit(
    path: Path,
    *,
    sherpa_jni_root: Path,
    sherpa_native: Mapping[str, FileIdentity],
    wrapper_input: Path,
    wrapper_identity: FileIdentity,
) -> tuple[dict[str, Any], FileIdentity]:
    identity = _file_identity(path, "raw native audit", maximum=MAX_AUDIT_BYTES)
    try:
        value = strict_json(
            path.read_bytes(),
            "raw native audit",
            maximum=MAX_AUDIT_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema",
            "policy",
            "sherpaLibraryProfile",
            "require16KiBPageAlignment",
            "reports",
            "errors",
        }
        or value.get("schema") != 4
        or value.get("policy") != "sherpa-audit"
        or value.get("sherpaLibraryProfile") != "flutter-ffi"
        or value.get("require16KiBPageAlignment") is not True
        or value.get("errors") != []
        or not isinstance(value.get("reports"), list)
        or len(value["reports"]) != 2
    ):
        raise AndroidSherpaReferenceAppGateError(
            "raw native audit did not pass the exact sherpa-owned profile"
        )
    reports = value["reports"]
    assert isinstance(reports, list)
    _require_raw_library_inventory(
        reports[0],
        artifact=sherpa_jni_root,
        expected=sherpa_native,
        ort_candidates=[f"{ABI}/libonnxruntime.so"],
    )
    _require_raw_library_inventory(
        reports[1],
        artifact=wrapper_input,
        expected={SHIM_NAME: wrapper_identity},
        ort_candidates=[],
    )
    return value, identity


def _require_static_artifact_identity(
    value: object,
    expected: FileIdentity,
    *,
    kind: str,
) -> None:
    if not isinstance(value, dict):
        raise AndroidSherpaReferenceAppGateError(
            f"static {kind.upper()} identity is missing"
        )
    if (
        value.get("kind") != kind
        or value.get("sizeBytes") != expected.size_bytes
        or value.get("sha256") != expected.sha256
        or value.get("digestScope") != "archive-bytes-v1"
    ):
        raise AndroidSherpaReferenceAppGateError(
            f"static {kind.upper()} identity is not bound to the built artifact"
        )


def _validate_static_manifest(
    path: Path,
    *,
    apk_identity: FileIdentity,
    aab_identity: FileIdentity,
) -> tuple[dict[str, Any], FileIdentity]:
    identity = _file_identity(
        path,
        "Android static package manifest",
        maximum=MAX_AUDIT_BYTES,
    )
    try:
        value = strict_json(
            path.read_bytes(),
            "Android static package manifest",
            maximum=MAX_AUDIT_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if not isinstance(value, dict):
        raise AndroidSherpaReferenceAppGateError(
            "Android static package manifest is not an object"
        )
    sherpa = value.get("sherpaOnnx")
    android = value.get("android")
    if (
        value.get("schemaVersion") != 1
        or value.get("result") != "passed"
        or value.get("claimStatus") != "static-package-only"
        or value.get("snapshotDate") != SNAPSHOT_DATE
        or not isinstance(sherpa, dict)
        or sherpa.get("source") != SHERPA_SOURCE
        or sherpa.get("revision") != SHERPA_REVISION
        or sherpa.get("provenanceBinding")
        != "caller-declared; enclosing-gate-required"
        or sherpa.get("libraryProfile") != "flutter-ffi"
        or not isinstance(android, dict)
        or android.get("integrationMode") != "sherpa-owned"
        or android.get("buildType") != BUILD_TYPE
        or android.get("buildTypeBinding")
        != "caller-declared; enclosing-gate-required"
        or android.get("abis") != [ABI]
        or android.get("ortOwner") != "sherpa"
        or android.get("ortApiRequired") != ORT_API_REQUIRED
    ):
        raise AndroidSherpaReferenceAppGateError(
            "Android static package manifest has the wrong closed contract"
        )
    artifacts = android.get("artifacts")
    if not isinstance(artifacts, dict):
        raise AndroidSherpaReferenceAppGateError(
            "Android static package manifest has no artifact identities"
        )
    _require_static_artifact_identity(
        artifacts.get("finalApk"), apk_identity, kind="apk"
    )
    _require_static_artifact_identity(
        artifacts.get("finalAab"), aab_identity, kind="aab"
    )
    return value, identity


def _require_static_audit_inputs(
    *,
    label: str,
    work_directory: Path,
    staged_source: StagedSourceIdentity,
    hosted_package_roots: Mapping[str, Path],
    hosted_packages: Mapping[str, HostedPackageTreeIdentity],
    sherpa_jni_root: Path,
    sherpa_native: Mapping[str, FileIdentity],
    wrapper_input: Path,
    wrapper_identity: FileIdentity,
    apk: Path,
    apk_identity: FileIdentity,
    aab: Path,
    aab_identity: FileIdentity,
) -> None:
    _require_staged_source_identity(
        work_directory,
        staged_source,
        f"{label} staged",
    )
    _require_hosted_package_inventory(
        hosted_package_roots,
        hosted_packages,
        label,
    )
    _require_sherpa_native_inventory(
        sherpa_jni_root,
        sherpa_native,
        label,
    )
    _require_file_identity(
        wrapper_input / ABI / SHIM_NAME,
        wrapper_identity,
        f"{label} staged Fonix shim",
        maximum=MAX_SHIM_BYTES,
    )
    _require_file_identity(
        apk,
        apk_identity,
        f"{label} Release APK",
        maximum=MAX_ARCHIVE_BYTES,
    )
    _require_file_identity(
        aab,
        aab_identity,
        f"{label} Release AAB",
        maximum=MAX_ARCHIVE_BYTES,
    )


def _run_static_audits(
    *,
    runner: CommandRunner,
    repository: Path,
    work_directory: Path,
    hosted_package_roots: Mapping[str, Path],
    hosted_packages: Mapping[str, HostedPackageTreeIdentity],
    sherpa_jni_root: Path,
    sherpa_native: Mapping[str, FileIdentity],
    wrapper_input: Path,
    wrapper_identity: FileIdentity,
    apk: Path,
    apk_identity: FileIdentity,
    aab: Path,
    aab_identity: FileIdentity,
    staged_source: StagedSourceIdentity,
    environment: Mapping[str, str],
) -> tuple[dict[str, Any], FileIdentity, dict[str, Any], FileIdentity]:
    evidence_root = work_directory / ".sherpa-static-evidence"
    raw_output = evidence_root / "raw-native-audit.json"
    static_output = evidence_root / "static-package-manifest.json"
    for path, label in (
        (raw_output, "raw native audit output"),
        (static_output, "static package manifest output"),
    ):
        if path.exists() or path.is_symlink():
            raise AndroidSherpaReferenceAppGateError(f"{label} already exists")

    def require_inputs(label: str) -> None:
        _require_static_audit_inputs(
            label=label,
            work_directory=work_directory,
            staged_source=staged_source,
            hosted_package_roots=hosted_package_roots,
            hosted_packages=hosted_packages,
            sherpa_jni_root=sherpa_jni_root,
            sherpa_native=sherpa_native,
            wrapper_input=wrapper_input,
            wrapper_identity=wrapper_identity,
            apk=apk,
            apk_identity=apk_identity,
            aab=aab,
            aab_identity=aab_identity,
        )

    require_inputs("raw native audit input")
    try:
        _execute(
            runner,
            (
                sys.executable,
                "-B",
                str(repository / "templates/android/verify_native_libs.py"),
                "--artifact",
                str(sherpa_jni_root),
                "--artifact",
                str(wrapper_input),
                "--policy",
                "sherpa-audit",
                "--sherpa-library-profile",
                "flutter-ffi",
                "--require-16k-page-alignment",
                "--require-abi",
                ABI,
                "--reject-multiple-ort-owners",
                "--reject-multiple-libcxx-owners",
                "--forbid-ort-in",
                str(wrapper_input),
                "--json-out",
                str(raw_output),
                "--quiet",
            ),
            operation="raw sherpa/Fonix native-input audit",
            cwd=work_directory,
            environment=environment,
        )
    finally:
        require_inputs("raw native audit output")
    raw_audit, raw_identity = _validate_raw_audit(
        raw_output,
        sherpa_jni_root=sherpa_jni_root,
        sherpa_native=sherpa_native,
        wrapper_input=wrapper_input,
        wrapper_identity=wrapper_identity,
    )

    require_inputs("closed static audit input")
    try:
        _execute(
            runner,
            (
                sys.executable,
                "-B",
                str(repository / "tool/ci/android_static_package_manifest.py"),
                "--sherpa-source",
                SHERPA_SOURCE,
                "--sherpa-revision",
                SHERPA_REVISION,
                "--sherpa-artifact",
                str(sherpa_jni_root),
                "--wrapper-artifact",
                str(wrapper_input),
                "--final-apk",
                str(apk),
                "--final-aab",
                str(aab),
                "--abi",
                ABI,
                "--ort-api-required",
                str(ORT_API_REQUIRED),
                "--build-type",
                BUILD_TYPE,
                "--snapshot-date",
                SNAPSHOT_DATE,
                "--output",
                str(static_output),
            ),
            operation="closed sherpa APK/AAB static audit",
            cwd=work_directory,
            environment=environment,
        )
    finally:
        require_inputs("closed static audit output")
    static_manifest, static_identity = _validate_static_manifest(
        static_output,
        apk_identity=apk_identity,
        aab_identity=aab_identity,
    )
    require_inputs("static audit postflight")
    return raw_audit, raw_identity, static_manifest, static_identity


def _build_environment(java_home: Path, android_sdk: Path) -> dict[str, str]:
    environment = tool_environment()
    environment["JAVA_HOME"] = str(java_home)
    environment["ANDROID_HOME"] = str(android_sdk)
    environment["ANDROID_SDK_ROOT"] = str(android_sdk)
    return environment


def _run_staged_gate(
    *,
    repository: Path,
    flutter: Path,
    work_directory: Path,
    android_sdk: Path,
    java_vendor: str,
    environment: Mapping[str, str],
    flutter_version: dict[str, object],
    source_manifest_identity: FileIdentity,
    runner: CommandRunner,
    sherpa_model: tuple[Path, FileIdentity] | None,
) -> dict[str, object]:
    summary = _copy_template(repository / TEMPLATE, work_directory)
    if summary.file_count == 0:
        raise AndroidSherpaReferenceAppGateError(
            "sherpa reference source copy is empty"
        )
    committed_lock_identity = _file_identity(
        repository / TEMPLATE / "pubspec.lock",
        "committed sherpa reference pubspec lock",
        maximum=MAX_LOCK_BYTES,
    )
    _require_file_identity(
        work_directory / "pubspec.lock",
        committed_lock_identity,
        "copied sherpa reference pubspec lock",
        maximum=MAX_LOCK_BYTES,
    )
    staged_lock_identity = _patch_for_host_tests(
        work_directory / "pubspec.yaml",
        work_directory / "pubspec.lock",
        repository,
    )
    runtime_fixtures: dict[str, FileIdentity] | None = None
    if sherpa_model is not None:
        runtime_fixtures = _stage_runtime_fixtures(
            work_directory,
            work_directory / "pubspec.yaml",
            sherpa_model[0],
            sherpa_model[1],
        )
    host_source_identity = _staged_source_identity(work_directory)

    _run_locked_pub_get(
        runner,
        flutter,
        work_directory,
        environment,
        staged_lock_identity,
        host_source_identity,
        operation="offline sherpa reference Flutter pub get",
    )
    hosted_package_roots, host_package_graph = _resolved_hosted_package_graph(
        work_directory
    )
    hosted_packages = _hosted_package_inventory(hosted_package_roots)
    _verify_local_properties(
        work_directory / "android/local.properties",
        android_sdk,
    )
    _execute_with_staged_source_guard(
        runner,
        (str(flutter), "analyze", "--no-pub"),
        operation="sherpa reference Flutter analysis",
        work_directory=work_directory,
        environment=environment,
        expected_source=host_source_identity,
        hosted_package_roots=hosted_package_roots,
        expected_hosted_packages=hosted_packages,
        expected_hosted_graph=host_package_graph,
    )
    _execute_with_staged_source_guard(
        runner,
        (str(flutter), "test", "--no-pub"),
        operation="sherpa reference Flutter tests",
        work_directory=work_directory,
        environment=environment,
        expected_source=host_source_identity,
        hosted_package_roots=hosted_package_roots,
        expected_hosted_packages=hosted_packages,
        expected_hosted_graph=host_package_graph,
    )

    _require_staged_source_identity(
        work_directory,
        host_source_identity,
        "Android hook selection input",
    )
    _select_android_hook(work_directory / "pubspec.yaml")
    android_source_identity = _staged_source_identity(work_directory)
    _run_locked_pub_get(
        runner,
        flutter,
        work_directory,
        environment,
        staged_lock_identity,
        android_source_identity,
        hosted_package_roots,
        hosted_packages,
        host_package_graph,
        operation="offline Android sherpa Flutter pub get",
    )
    android_package_roots, android_package_graph = _resolved_hosted_package_graph(
        work_directory
    )
    if android_package_roots != hosted_package_roots:
        raise AndroidSherpaReferenceAppGateError(
            "Android pub get changed the resolved hosted sherpa package roots"
        )
    _require_hosted_package_inventory(
        android_package_roots,
        hosted_packages,
        "offline Android sherpa Flutter pub get",
    )
    _verify_local_properties(
        work_directory / "android/local.properties",
        android_sdk,
    )
    try:
        sherpa_jni_root = directory(
            hosted_package_roots[SHERPA_PACKAGE] / "android/src/main/jniLibs",
            f"resolved {SHERPA_PACKAGE} jniLibs",
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    sherpa_native = _sherpa_native_inventory(sherpa_jni_root)

    build_arguments = (
        "--release",
        "--no-pub",
        "--target-platform",
        FLUTTER_TARGET_PLATFORM,
    )
    _require_sherpa_native_inventory(
        sherpa_jni_root,
        sherpa_native,
        "Release APK build input",
    )
    try:
        _execute_with_staged_source_guard(
            runner,
            (str(flutter), "build", "apk", *build_arguments),
            operation="sherpa reference Release APK build",
            work_directory=work_directory,
            environment=environment,
            expected_source=android_source_identity,
            hosted_package_roots=hosted_package_roots,
            expected_hosted_packages=hosted_packages,
            expected_hosted_graph=android_package_graph,
        )
    finally:
        _require_sherpa_native_inventory(
            sherpa_jni_root,
            sherpa_native,
            "Release APK build output",
        )
    apk = work_directory / APK_RELATIVE
    try:
        apk = regular_file(
            apk,
            "sherpa reference Release APK",
            maximum=MAX_ARCHIVE_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    apk_identity = _file_identity(apk, "Release APK", maximum=MAX_ARCHIVE_BYTES)
    _require_sherpa_native_inventory(
        sherpa_jni_root,
        sherpa_native,
        "Release AAB build input",
    )
    try:
        _execute_with_staged_source_guard(
            runner,
            (str(flutter), "build", "appbundle", *build_arguments),
            operation="sherpa reference Release AAB build",
            work_directory=work_directory,
            environment=environment,
            expected_source=android_source_identity,
            hosted_package_roots=hosted_package_roots,
            expected_hosted_packages=hosted_packages,
            expected_hosted_graph=android_package_graph,
        )
    finally:
        _require_sherpa_native_inventory(
            sherpa_jni_root,
            sherpa_native,
            "Release AAB build output",
        )
        _require_file_identity(
            apk,
            apk_identity,
            "Release AAB build input APK",
            maximum=MAX_ARCHIVE_BYTES,
        )
    aab = work_directory / AAB_RELATIVE
    try:
        aab = regular_file(
            aab,
            "sherpa reference Release AAB",
            maximum=MAX_ARCHIVE_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    aab_identity = _file_identity(aab, "Release AAB", maximum=MAX_ARCHIVE_BYTES)

    if runtime_fixtures is not None:
        _runtime_fixture_inventory(
            work_directory / RUNTIME_ASSET_DIRECTORY,
            runtime_fixtures,
        )
        _audit_runtime_fixture_archive(
            apk,
            runtime_fixtures,
            kind="apk",
        )
        _audit_runtime_fixture_archive(
            aab,
            runtime_fixtures,
            kind="aab",
        )

    wrapper_input, raw_shim_identity = _stage_wrapper_input(work_directory)
    raw_audit, raw_audit_identity, static_manifest, static_manifest_identity = (
        _run_static_audits(
            runner=runner,
            repository=repository,
            work_directory=work_directory,
            hosted_package_roots=hosted_package_roots,
            hosted_packages=hosted_packages,
            sherpa_jni_root=sherpa_jni_root,
            sherpa_native=sherpa_native,
            wrapper_input=wrapper_input,
            wrapper_identity=raw_shim_identity,
            apk=apk,
            apk_identity=apk_identity,
            aab=aab,
            aab_identity=aab_identity,
            staged_source=android_source_identity,
            environment=environment,
        )
    )
    _require_file_identity(
        work_directory / "pubspec.lock",
        staged_lock_identity,
        "final staged sherpa reference pubspec lock",
        maximum=MAX_LOCK_BYTES,
    )
    _require_staged_source_identity(
        work_directory,
        android_source_identity,
        "final sherpa reference",
    )
    _require_sherpa_native_inventory(
        sherpa_jni_root,
        sherpa_native,
        "final sherpa reference",
    )
    _require_hosted_package_inventory(
        hosted_package_roots,
        hosted_packages,
        "final sherpa reference",
    )
    _require_hosted_package_graph(
        work_directory,
        hosted_package_roots,
        android_package_graph,
        "final sherpa reference",
    )
    if runtime_fixtures is not None:
        _runtime_fixture_inventory(
            work_directory / RUNTIME_ASSET_DIRECTORY,
            runtime_fixtures,
        )

    report: dict[str, object] = {
        "schemaVersion": 1,
        "result": "passed",
        "mode": (
            "runtime-provisioned"
            if runtime_fixtures is not None
            else "static-template"
        ),
        "abi": ABI,
        "buildType": BUILD_TYPE,
        "sourceManifestSha256": source_manifest_identity.sha256,
        "sourceCopy": {
            "fileCount": summary.file_count,
            "byteCount": summary.byte_count,
        },
        "stagedSource": {
            "manifestSchema": 1,
            "excludedGeneratedPaths": sorted(GENERATED_EXCLUSIONS)
            + ["android/**/*.iml"],
            "hostTest": {
                "entryCount": host_source_identity.entry_count,
                "fileCount": host_source_identity.file_count,
                "byteCount": host_source_identity.byte_count,
                "sha256": host_source_identity.sha256,
            },
            "androidBuild": {
                "entryCount": android_source_identity.entry_count,
                "fileCount": android_source_identity.file_count,
                "byteCount": android_source_identity.byte_count,
                "sha256": android_source_identity.sha256,
            },
        },
        "flutterRevision": flutter_version["frameworkRevision"],
        "flutterVersion": flutter_version.get("frameworkVersion"),
        "android": {
            "compileApi": ANDROID_COMPILE_API,
            "buildToolsVersion": ANDROID_BUILD_TOOLS_VERSION,
            "ndkVersion": ANDROID_NDK_VERSION,
        },
        "java": {
            "version": SUPPORTED_JAVA_VERSION,
            "vendor": java_vendor,
        },
        "pubspecLock": {
            "committedSha256": committed_lock_identity.sha256,
            "stagedSha256": staged_lock_identity.sha256,
        },
        "sherpaOnnx": {
            "source": SHERPA_SOURCE,
            "revision": SHERPA_REVISION,
            "version": SHERPA_VERSION,
            "package": SHERPA_PACKAGE,
            "libraryProfile": "flutter-ffi",
            "hostedPackageGuard": {
                "threatModel": "non-hostile-local-build",
                "treeSchema": 1,
                "androidPluginPackages": list(
                    android_package_graph.android_plugins
                ),
                "packages": [
                    {
                        "name": name,
                        "version": SHERPA_VERSION,
                        "archiveSha256": SHERPA_HOSTED_PACKAGE_PINS[
                            name
                        ].archive_sha256,
                        "directoryCount": hosted_packages[
                            name
                        ].directory_count,
                        "fileCount": hosted_packages[name].file_count,
                        "byteCount": hosted_packages[name].byte_count,
                        "treeSha256": hosted_packages[name].sha256,
                    }
                    for name in sorted(hosted_packages)
                ],
            },
            "nativeInputs": [
                {
                    "abi": ABI,
                    "fileName": name,
                    "sizeBytes": sherpa_native[name].size_bytes,
                    "sha256": sherpa_native[name].sha256,
                }
                for name in sorted(sherpa_native)
            ],
        },
        "rawFonixShim": {
            "sizeBytes": raw_shim_identity.size_bytes,
            "sha256": raw_shim_identity.sha256,
        },
        "releaseApk": {
            "sizeBytes": apk_identity.size_bytes,
            "sha256": apk_identity.sha256,
        },
        "releaseAab": {
            "sizeBytes": aab_identity.size_bytes,
            "sha256": aab_identity.sha256,
        },
        "rawNativeAudit": {
            "schema": raw_audit["schema"],
            "policy": raw_audit["policy"],
            "sizeBytes": raw_audit_identity.size_bytes,
            "sha256": raw_audit_identity.sha256,
        },
        "staticPackageManifest": {
            "sizeBytes": static_manifest_identity.size_bytes,
            "sha256": static_manifest_identity.sha256,
            "record": static_manifest,
        },
        "targetEvidence": None,
        "claimBoundary": (
            "This gate proves the staged source, locked dependency graph, raw "
            "sherpa/Fonix native inputs, and the same selected arm64-v8a native "
            "graph and Flutter platform-library loaded identities in one Release "
            "APK/base-only AAB pair. It does not prove application/version manifest "
            "identity, signing, installation, runtime API/version negotiation, "
            "inference, load order, lifecycle behavior, a delivered AAB split, a "
            "4 KiB or 16 KiB target environment, or target compatibility. "
            "Hosted-package tree guards assume a non-hostile local build and do not "
            "authenticate the host cache or defend against mutate-and-restore races."
        ),
    }
    if runtime_fixtures is not None:
        report["runtimeFixtures"] = [
            {
                "fileName": name,
                "sizeBytes": runtime_fixtures[name].size_bytes,
                "sha256": runtime_fixtures[name].sha256,
            }
            for name in RUNTIME_FIXTURE_NAMES
        ]
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise AndroidSherpaReferenceAppGateError(
            "Android sherpa reference gate report exceeds its bound"
        )
    return report


def run_gate(
    *,
    repository: Path,
    flutter: Path,
    work_directory: Path,
    android_sdk: Path,
    java_home: Path,
    command_runner: CommandRunner | None = None,
    sherpa_model: Path | None = None,
) -> dict[str, object]:
    runner = _run_command if command_runner is None else command_runner
    try:
        repository = directory(repository.resolve(strict=True), "Fonix repository")
        flutter = _resolve_tool(flutter, "Flutter executable")
    except (OSError, AndroidGateCommonError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidSherpaReferenceAppGateError(
            "could not resolve Android sherpa gate inputs"
        ) from error
    if not work_directory.is_absolute():
        raise AndroidSherpaReferenceAppGateError("--work-dir must be absolute")
    try:
        work_parent = directory(
            work_directory.parent.resolve(strict=True),
            "Android sherpa work parent",
        )
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidSherpaReferenceAppGateError(
            "Android sherpa work parent is invalid"
        ) from error
    work_directory = work_parent / work_directory.name
    if work_directory.exists() or work_directory.is_symlink():
        raise AndroidSherpaReferenceAppGateError(
            "--work-dir must name a new path"
        )
    try:
        work_directory.relative_to(repository)
    except ValueError:
        pass
    else:
        raise AndroidSherpaReferenceAppGateError(
            "--work-dir must be outside the Fonix repository"
        )
    try:
        repository.relative_to(work_directory)
    except ValueError:
        pass
    else:
        raise AndroidSherpaReferenceAppGateError(
            "--work-dir must not contain the Fonix repository"
        )

    validated_sherpa_model = (
        None if sherpa_model is None else _validate_sherpa_model(sherpa_model)
    )

    android_sdk = _validate_android_sdk(android_sdk)
    java_home, _java, java_vendor = _validate_java_home(java_home, runner)
    environment = _build_environment(java_home, android_sdk)
    flutter_version = _verify_flutter(flutter, runner, environment)
    source_manifest_identity = _verify_source_manifest(
        repository,
        runner,
        environment,
        phase="preflight",
    )
    escape_snapshot = _repository_escape_snapshot(repository)
    try:
        return _run_staged_gate(
            repository=repository,
            flutter=flutter,
            work_directory=work_directory,
            android_sdk=android_sdk,
            java_vendor=java_vendor,
            environment=environment,
            flutter_version=flutter_version,
            source_manifest_identity=source_manifest_identity,
            runner=runner,
            sherpa_model=validated_sherpa_model,
        )
    finally:
        try:
            final_manifest_identity = _verify_source_manifest(
                repository,
                runner,
                environment,
                phase="postflight",
            )
            final_escape_snapshot = _repository_escape_snapshot(repository)
        except AndroidSherpaReferenceAppGateError as error:
            raise AndroidSherpaReferenceAppGateError(
                "generated state or output escaped staging: "
                "the Fonix source checkout no longer passes its exact manifest/state check"
            ) from error
        if (
            final_manifest_identity != source_manifest_identity
            or final_escape_snapshot != escape_snapshot
        ):
            raise AndroidSherpaReferenceAppGateError(
                "generated state or output escaped staging into the Fonix checkout"
            )


def _require_report_output_capabilities() -> None:
    required_dir_fd_functions = (os.open, os.stat, os.unlink, os.link)
    required_no_follow_functions = (os.stat, os.link)
    if (
        os.name != "posix"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "fchmod")
        or any(
            function not in os.supports_dir_fd
            for function in required_dir_fd_functions
        )
        or any(
            function not in os.supports_follow_symlinks
            for function in required_no_follow_functions
        )
    ):
        raise AndroidSherpaReferenceAppGateError(
            "--report requires POSIX directory-descriptor publication support"
        )


def _status_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _report_entry_status(
    destination: ReportDestination, name: str
) -> os.stat_result | None:
    try:
        return os.stat(
            name,
            dir_fd=destination.directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _verify_report_parent(destination: ReportDestination) -> None:
    try:
        linked = destination.parent.lstat()
        opened = os.fstat(destination.directory_descriptor)
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "--report parent changed during the gate"
        ) from error
    if (
        not stat.S_ISDIR(linked.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or _status_identity(linked) != destination.parent_identity
        or _status_identity(opened) != destination.parent_identity
    ):
        raise AndroidSherpaReferenceAppGateError(
            "--report parent changed during the gate"
        )


def _prepare_report_destination(
    path: Path, repository: Path
) -> ReportDestination:
    _require_report_output_capabilities()
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise AndroidSherpaReferenceAppGateError(
            "--report must be an absolute file path"
        )
    try:
        encoded_name = path.name.encode("utf-8")
    except UnicodeEncodeError as error:
        raise AndroidSherpaReferenceAppGateError(
            "--report file name must be valid UTF-8"
        ) from error
    if len(encoded_name) > 240 or b"\x00" in encoded_name:
        raise AndroidSherpaReferenceAppGateError(
            "--report file name is outside its byte bound"
        )
    try:
        repository_root = repository.resolve(strict=True)
        repository_metadata = repository_root.lstat()
        parent_metadata = path.parent.lstat()
        if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(
            parent_metadata.st_mode
        ):
            raise AndroidSherpaReferenceAppGateError(
                "--report parent must be a non-symlink directory"
            )
        parent = path.parent.resolve(strict=True)
        resolved_metadata = parent.lstat()
    except (OSError, ValueError) as error:
        raise AndroidSherpaReferenceAppGateError(
            "--report repository and parent must already exist"
        ) from error
    if not stat.S_ISDIR(repository_metadata.st_mode):
        raise AndroidSherpaReferenceAppGateError(
            "--repository must resolve to a directory"
        )
    if (
        not stat.S_ISDIR(resolved_metadata.st_mode)
        or _status_identity(parent_metadata) != _status_identity(resolved_metadata)
    ):
        raise AndroidSherpaReferenceAppGateError(
            "--report parent must be a stable non-symlink directory"
        )
    if (
        resolved_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(resolved_metadata.st_mode) & 0o022
    ):
        raise AndroidSherpaReferenceAppGateError(
            "--report parent must be owned by the current user and not "
            "group- or world-writable"
        )
    destination_path = parent / path.name
    if (
        destination_path == repository_root
        or repository_root in destination_path.parents
    ):
        raise AndroidSherpaReferenceAppGateError(
            "--report must be outside the source repository"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor: int | None = None
    try:
        descriptor = os.open(parent, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _status_identity(opened) != _status_identity(resolved_metadata)
        ):
            raise AndroidSherpaReferenceAppGateError(
                "--report parent changed while being opened"
            )
        destination = ReportDestination(
            destination_path,
            parent,
            path.name,
            descriptor,
            _status_identity(opened),
        )
        if _report_entry_status(destination, destination.name) is not None:
            raise AndroidSherpaReferenceAppGateError(
                "--report must name a new path"
            )
        descriptor = None
        return destination
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "could not safely open --report parent"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unlink_owned_report_entry(
    destination: ReportDestination,
    name: str | None,
    identity: tuple[int, int] | None,
) -> None:
    if name is None or identity is None:
        return
    try:
        metadata = _report_entry_status(destination, name)
        if metadata is not None and _status_identity(metadata) == identity:
            os.unlink(name, dir_fd=destination.directory_descriptor)
    except OSError:
        pass


def _create_report_temporary(
    destination: ReportDestination,
) -> tuple[str, int, tuple[int, int]]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    for _attempt in range(16):
        name = f".fonix-sherpa-report-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=destination.directory_descriptor,
            )
        except FileExistsError:
            continue
        try:
            metadata = os.fstat(descriptor)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise AndroidSherpaReferenceAppGateError(
                "--report temporary identity could not be verified before "
                "writing; no report bytes were written and the unverified "
                f"path was left untouched: {destination.parent / name}"
            ) from error
        owned_identity = _status_identity(metadata)
        try:
            if not stat.S_ISREG(metadata.st_mode):
                raise AndroidSherpaReferenceAppGateError(
                    "--report temporary output is not a regular file"
                )
            return name, descriptor, owned_identity
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            _unlink_owned_report_entry(destination, name, owned_identity)
            raise
    raise AndroidSherpaReferenceAppGateError(
        "could not allocate a private --report temporary output"
    )


def _write_new_report(
    destination: ReportDestination, report: Mapping[str, object]
) -> None:
    encoded = (
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise AndroidSherpaReferenceAppGateError(
            "Android sherpa reference gate report exceeds its bound"
        )
    temporary_name: str | None = None
    descriptor: int | None = None
    owned_identity: tuple[int, int] | None = None
    published = False
    succeeded = False
    try:
        _verify_report_parent(destination)
        if _report_entry_status(destination, destination.name) is not None:
            raise AndroidSherpaReferenceAppGateError(
                "--report output already exists"
            )
        temporary_name, descriptor, owned_identity = _create_report_temporary(
            destination
        )
        os.fchmod(descriptor, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AndroidSherpaReferenceAppGateError(
                    "could not write --report output"
                )
            view = view[written:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        temporary = _report_entry_status(destination, temporary_name)
        if (
            temporary is None
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(temporary.st_mode)
            or _status_identity(opened) != owned_identity
            or _status_identity(temporary) != owned_identity
            or opened.st_size != len(encoded)
            or temporary.st_size != len(encoded)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(temporary.st_mode) != 0o600
        ):
            raise AndroidSherpaReferenceAppGateError(
                "--report temporary output changed before publication"
            )
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
        if (
            bytes(observed) != encoded
            or _status_identity(os.fstat(descriptor)) != owned_identity
        ):
            raise AndroidSherpaReferenceAppGateError(
                "--report temporary bytes changed before publication"
            )
        _verify_report_parent(destination)
        os.link(
            temporary_name,
            destination.name,
            src_dir_fd=destination.directory_descriptor,
            dst_dir_fd=destination.directory_descriptor,
            follow_symlinks=False,
        )
        published = True
        final_metadata = _report_entry_status(destination, destination.name)
        if (
            final_metadata is None
            or _status_identity(final_metadata) != owned_identity
            or final_metadata.st_size != len(encoded)
            or stat.S_IMODE(final_metadata.st_mode) != 0o600
        ):
            raise AndroidSherpaReferenceAppGateError(
                "--report publication does not reference the completed output"
            )
        _verify_report_parent(destination)
        _unlink_owned_report_entry(
            destination, temporary_name, owned_identity
        )
        if _report_entry_status(destination, temporary_name) is not None:
            raise AndroidSherpaReferenceAppGateError(
                "--report temporary output could not be retired"
            )
        temporary_name = None
        os.fsync(destination.directory_descriptor)
        _verify_report_parent(destination)
        final_metadata = _report_entry_status(destination, destination.name)
        if (
            final_metadata is None
            or _status_identity(final_metadata) != owned_identity
            or final_metadata.st_size != len(encoded)
        ):
            raise AndroidSherpaReferenceAppGateError(
                "--report output changed during publication"
            )
        closing_descriptor = descriptor
        descriptor = None
        try:
            os.close(closing_descriptor)
        except OSError as error:
            raise AndroidSherpaReferenceAppGateError(
                "could not close the completed --report temporary output"
            ) from error
        succeeded = True
    except OSError as error:
        raise AndroidSherpaReferenceAppGateError(
            "could not publish the new --report output"
        ) from error
    finally:
        if descriptor is not None:
            closing_descriptor = descriptor
            descriptor = None
            try:
                os.close(closing_descriptor)
            except OSError:
                pass
        if not succeeded and published:
            _unlink_owned_report_entry(
                destination, destination.name, owned_identity
            )
        _unlink_owned_report_entry(
            destination, temporary_name, owned_identity
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--flutter", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--android-sdk", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument(
        "--sherpa-model",
        type=Path,
        help=(
            "exact external silero_vad.int8.onnx used to provision the "
            "runtime qualification build"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="new absolute path for the successful gate report",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    report_destination: ReportDestination | None = None
    report: dict[str, object] | None = None
    failure: AndroidSherpaReferenceAppGateError | None = None
    try:
        report_destination = (
            None
            if arguments.report is None
            else _prepare_report_destination(
                arguments.report, arguments.repository
            )
        )
        report = run_gate(
            repository=arguments.repository,
            flutter=arguments.flutter,
            work_directory=arguments.work_dir,
            android_sdk=arguments.android_sdk,
            java_home=arguments.java_home,
            sherpa_model=arguments.sherpa_model,
        )
        if report_destination is not None:
            _write_new_report(report_destination, report)
    except AndroidSherpaReferenceAppGateError as error:
        failure = error
    except BaseException:
        if report_destination is not None:
            try:
                os.close(report_destination.directory_descriptor)
            except OSError:
                pass
        raise
    if report_destination is not None:
        try:
            os.close(report_destination.directory_descriptor)
        except OSError:
            pass
    if failure is not None:
        print(f"android_sherpa_reference_app_gate: {failure}", file=sys.stderr)
        return 1
    if report is None:
        raise AssertionError("successful Android sherpa gate omitted its report")
    if report_destination is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "Wrote Android sherpa reference app gate report: "
            f"{report_destination.path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
