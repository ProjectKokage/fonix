#!/usr/bin/env python3
"""Build, audit, and optionally run the Fonix Android arm64 reference app."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, NamedTuple, Sequence
import urllib.parse
import zipfile

from android_gate_common import (
    AndroidGateCommonError,
    directory,
    regular_file,
    run_bounded,
    sha256_file,
    strict_json,
    tool_environment,
)


VALIDATED_FLUTTER_REVISION = "bd1e75d918605c91b411e8789fb911e6c9a84534"
ANDROID_BUILD_TOOLS_VERSION = "36.0.0"
ANDROID_NDK_VERSION = "28.2.13676358"
ANDROID_COMMAND_LINE_TOOLS_VERSION = "20.0"
ANDROID_API = 35
ABI = "arm64-v8a"
SUPPORTED_JAVA_VERSION = "21.0.12"
BUNDLETOOL_VERSION = "1.18.3"
BUNDLETOOL_SIZE_BYTES = 32_520_401
BUNDLETOOL_SHA256 = (
    "a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29"
)
APPLICATION_ID = "dev.fonix.fonix_reference"
APPLICATION_COMPONENT = f"{APPLICATION_ID}/.MainActivity"
ARTIFACT_ID = "onnxruntime-1.27.1-android-arm64-v8a-cpu"
ARTIFACT_SOURCE_SHA256 = (
    "9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383"
)
ARTIFACT_SOURCE_SIZE = 135_152_698
ARTIFACT_CONTAINER_PATH = "runtimes/android/native/onnxruntime.aar"
ARTIFACT_CONTAINER_SHA256 = (
    "6c4390433cf5ad9c38aa7e515f4e22d405613f3b872c0ed2b7d27cf1a714f32d"
)
ARTIFACT_CONTAINER_SIZE = 44_522_169
ORT_ARCHIVE_PATH = "jni/arm64-v8a/libonnxruntime.so"
ORT_SHA256 = "a7579e85ecc5465840d352c35f355e5b7418d36901670d36afd46555304458c2"
ORT_SIZE_BYTES = 27_983_536
MODEL_SHA256 = "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10"
EXPECTED_SHIM_BUILD_ID = (
    "android-owner-application-source-bundled-artifact-"
    "onnxruntime-1.27.1-android-arm64-v8a-cpu"
)

SMOKE_DART_DEFINE = "FONIX_REFERENCE_SMOKE=true"
LOG_TAG = "FonixReference"
RECEIPT_PREFIX = "FONIX_REFERENCE_RECEIPT="
FAILURE_PREFIX = "FONIX_REFERENCE_FAILURE="
MAX_RECEIPT_BYTES = 16 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_LOCK_BYTES = 1024 * 1024
MAX_PUBSPEC_LOCK_BYTES = 2 * 1024 * 1024
MAX_LOCAL_PROPERTIES_BYTES = 64 * 1024
MAX_COPY_ENTRIES = 100_000
MAX_COPY_FILES = 10_000
MAX_COPY_BYTES = 96 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024
MAX_LOGCAT_BYTES = 64 * 1024
MAX_ZIP_ENTRIES = 100_000
MAX_COMPRESSION_RATIO = 200
MAX_ASSET_BYTES = 8 * 1024 * 1024
AVD_SHUTDOWN_TIMEOUT_SECONDS = 30.0
AVD_SHUTDOWN_POLL_SECONDS = 0.25
ASSET_NAMES = frozenset(
    {"fonix-native-artifact-manifest.json", "ThirdPartyNotices.txt"}
)

GENERATED_EXCLUSIONS = frozenset(
    {
        ".dart_tool",
        ".fonix-artifact-cache",
        ".flutter-plugins-dependencies",
        ".idea",
        ".pub",
        ".pub-cache",
        "android/.gradle",
        "android/.kotlin",
        "android/app/.cxx",
        "android/app/src/main/java/io/flutter/plugins/GeneratedPluginRegistrant.java",
        "android/captures",
        "android/fonix_reference_android.iml",
        "android/local.properties",
        "build",
        "coverage",
        "fonix_reference.iml",
        "macos/Flutter/ephemeral",
    }
)

EXPECTED_RECEIPT: dict[str, object] = {
    "schemaVersion": 1,
    "status": "passed",
    "runtimeVersion": "1.27.1",
    "runtimeSource": "bundled",
    "runtimeOwner": "application",
    "artifactFlavor": "cpu",
    "platform": "android",
    "architecture": ABI,
    "shimBuildId": EXPECTED_SHIM_BUILD_ID,
    "artifactSha256": ARTIFACT_SOURCE_SHA256,
    "modelSha256": MODEL_SHA256,
    "outputValues": [1, 4, 9, 16, 25, 36],
    "activeProviders": ["cpu"],
    "fullCpuAssignment": True,
    "doubleClose": "passed",
}
EXPECTED_RECEIPT_JSON = json.dumps(
    EXPECTED_RECEIPT,
    ensure_ascii=True,
    separators=(",", ":"),
)


class AndroidReferenceAppGateError(RuntimeError):
    """The Android reference-application gate failed closed."""


class AndroidReferenceReceiptPending(AndroidReferenceAppGateError):
    """The bounded log capture does not yet contain a smoke completion."""


class PinnedArchive(NamedTuple):
    artifact_id: str
    basename: str
    sha256: str
    size_bytes: int
    container_path: str
    container_sha256: str
    container_size_bytes: int
    runtime_path: str
    runtime_sha256: str
    runtime_size_bytes: int


class CopySummary(NamedTuple):
    file_count: int
    byte_count: int


class FileIdentity(NamedTuple):
    size_bytes: int
    sha256: str


class LogcatReceiptEvidence(NamedTuple):
    receipt: dict[str, object]
    uid: int
    pid: int
    pid_was_observed: bool


class JavaTools(NamedTuple):
    home: Path
    java: Path
    jarsigner: Path
    version: str
    vendor: str


def _common(error: AndroidGateCommonError) -> AndroidReferenceAppGateError:
    return AndroidReferenceAppGateError(str(error))


def _is_excluded(relative: Path) -> bool:
    value = relative.as_posix()
    return any(
        value == exclusion or value.startswith(f"{exclusion}/")
        for exclusion in GENERATED_EXCLUSIONS
    )


def _copy_example(source: Path, destination: Path) -> CopySummary:
    try:
        source = directory(source, "committed example source")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise AndroidReferenceAppGateError(
            "Android reference work directory must be a new absolute path"
        )
    try:
        parent = directory(destination.parent.resolve(strict=True), "work parent")
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidReferenceAppGateError("Android work parent is invalid") from error
    destination = parent / destination.name
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise AndroidReferenceAppGateError("work directory must be outside example source")

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
            if entry_count > MAX_COPY_ENTRIES:
                raise AndroidReferenceAppGateError("example exceeds copy-entry bound")
            entry = root_path / name
            metadata = entry.lstat()
            relative = entry.relative_to(source)
            if stat.S_ISLNK(metadata.st_mode):
                raise AndroidReferenceAppGateError(
                    f"example contains a symbolic link: {relative.as_posix()}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                if not _is_excluded(relative):
                    directories.append(relative)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise AndroidReferenceAppGateError(
                    f"example contains a special file: {relative.as_posix()}"
                )
            if _is_excluded(relative):
                continue
            if len(files) >= MAX_COPY_FILES or metadata.st_size > MAX_COPY_BYTES - total_bytes:
                raise AndroidReferenceAppGateError("example exceeds copied-source bound")
            data = entry.read_bytes()
            if len(data) != metadata.st_size:
                raise AndroidReferenceAppGateError("example changed during scan")
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
    for relative, size, mode, digest in sorted(files, key=lambda value: value[0].as_posix()):
        source_file = source / relative
        destination_file = destination / relative
        destination_file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source_file, flags)
        try:
            with os.fdopen(descriptor, "rb") as input_stream:
                descriptor = -1
                data = input_stream.read(MAX_COPY_BYTES + 1)
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise AndroidReferenceAppGateError("example changed between scan and copy")
            with destination_file.open("xb") as output:
                output.write(data)
                output.flush()
            destination_file.chmod(mode & 0o777)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return CopySummary(len(files), total_bytes)


def _patch_pubspec(pubspec: Path, repository: Path) -> None:
    try:
        pubspec = regular_file(pubspec, "reference pubspec", maximum=1024 * 1024)
        repository = directory(repository, "Fonix repository")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    source = pubspec.read_text(encoding="utf-8")
    if "\r" in source:
        raise AndroidReferenceAppGateError("reference pubspec must use LF newlines")
    relative_dependency = "  fonix:\n    path: ..\n"
    absolute_dependency = (
        "  fonix:\n"
        f"    path: {json.dumps(repository.as_posix(), ensure_ascii=True)}\n"
    )
    macos_hooks = (
        "hooks:\n"
        "  user_defines:\n"
        "    fonix:\n"
        "      runtime_mode: bundled\n"
        "      artifact_cache: .fonix-artifact-cache\n"
        "      application_minimum_os: '14.0'\n"
    )
    test_hooks = (
        "hooks:\n"
        "  user_defines:\n"
        "    fonix:\n"
        "      runtime_mode: external\n"
    )
    if source.count(relative_dependency) != 1 or source.count(macos_hooks) != 1:
        raise AndroidReferenceAppGateError(
            "reference pubspec no longer matches the exact macOS source template"
        )
    updated = source.replace(relative_dependency, absolute_dependency).replace(
        macos_hooks, test_hooks
    )
    if updated.replace(absolute_dependency, relative_dependency).replace(
        test_hooks, macos_hooks
    ) != source:
        raise AndroidReferenceAppGateError("test pubspec patch changed unexpected bytes")
    pubspec.write_text(updated, encoding="utf-8", newline="")


def _file_identity(path: Path, label: str, *, maximum: int) -> FileIdentity:
    try:
        size, digest = sha256_file(path, label, maximum=maximum)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    return FileIdentity(size, digest)


def _patch_pubspec_lock(lockfile: Path, repository: Path) -> FileIdentity:
    try:
        lockfile = regular_file(
            lockfile,
            "reference pubspec lock",
            maximum=MAX_PUBSPEC_LOCK_BYTES,
        )
        repository = directory(repository, "Fonix repository")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    try:
        source = lockfile.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise AndroidReferenceAppGateError(
            "reference pubspec lock is not UTF-8"
        ) from error
    if "\r" in source:
        raise AndroidReferenceAppGateError("reference pubspec lock must use LF newlines")
    relative_dependency = '      path: ".."\n      relative: true\n'
    absolute_dependency = (
        f"      path: {json.dumps(repository.as_posix(), ensure_ascii=True)}\n"
        "      relative: false\n"
    )
    if source.count(relative_dependency) != 1:
        raise AndroidReferenceAppGateError(
            "reference pubspec lock no longer has one relative Fonix dependency"
        )
    updated = source.replace(relative_dependency, absolute_dependency)
    if updated.replace(absolute_dependency, relative_dependency) != source:
        raise AndroidReferenceAppGateError(
            "reference pubspec lock patch changed unexpected bytes"
        )
    lockfile.write_text(updated, encoding="utf-8", newline="")
    return _file_identity(
        lockfile,
        "patched reference pubspec lock",
        maximum=MAX_PUBSPEC_LOCK_BYTES,
    )


def _require_file_identity(
    path: Path,
    expected: FileIdentity,
    label: str,
    *,
    maximum: int,
) -> None:
    observed = _file_identity(path, label, maximum=maximum)
    if observed != expected:
        raise AndroidReferenceAppGateError(f"{label} identity changed")


def _run_locked_pub_get(
    flutter: Path,
    work_directory: Path,
    environment: Mapping[str, str],
    expected_lock: FileIdentity,
    *,
    operation: str,
) -> None:
    lockfile = work_directory / "pubspec.lock"
    _require_file_identity(
        lockfile,
        expected_lock,
        f"{operation} input lockfile",
        maximum=MAX_PUBSPEC_LOCK_BYTES,
    )
    try:
        _run_command(
            (str(flutter), "pub", "get", "--offline", "--enforce-lockfile"),
            operation=operation,
            cwd=work_directory,
            environment=environment,
        )
    finally:
        _require_file_identity(
            lockfile,
            expected_lock,
            f"{operation} output lockfile",
            maximum=MAX_PUBSPEC_LOCK_BYTES,
        )


def _verify_android_local_properties(path: Path, android_sdk: Path) -> None:
    try:
        path = regular_file(
            path,
            "generated Android local.properties",
            maximum=MAX_LOCAL_PROPERTIES_BYTES,
        )
        android_sdk = directory(android_sdk, "Android SDK")
        source = path.read_text(encoding="utf-8")
    except (AndroidGateCommonError, UnicodeDecodeError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidReferenceAppGateError(
            "generated Android local.properties is not UTF-8"
        ) from error
    if "\r" in source or "\x00" in source:
        raise AndroidReferenceAppGateError(
            "generated Android local.properties has invalid line encoding"
        )
    sdk_lines = [line for line in source.splitlines() if line.startswith("sdk.dir")]
    expected = f"sdk.dir={android_sdk}"
    if sdk_lines != [expected]:
        raise AndroidReferenceAppGateError(
            "generated Android local.properties is not bound to --android-sdk"
        )


def _android_build_environment(java_home: Path, android_sdk: Path) -> dict[str, str]:
    environment = tool_environment()
    environment["JAVA_HOME"] = str(java_home)
    environment["ANDROID_HOME"] = str(android_sdk)
    environment["ANDROID_SDK_ROOT"] = str(android_sdk)
    return environment


def _select_android_hooks(pubspec: Path) -> None:
    try:
        pubspec = regular_file(pubspec, "reference pubspec", maximum=1024 * 1024)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    source = pubspec.read_text(encoding="utf-8")
    if "\r" in source:
        raise AndroidReferenceAppGateError("reference pubspec must use LF newlines")
    test_hooks = (
        "hooks:\n"
        "  user_defines:\n"
        "    fonix:\n"
        "      runtime_mode: external\n"
    )
    android_hooks = (
        "hooks:\n"
        "  user_defines:\n"
        "    fonix:\n"
        "      android_runtime_owner: application\n"
        "      runtime_mode: bundled\n"
        "      artifact_cache: .fonix-artifact-cache\n"
    )
    if source.count(test_hooks) != 1:
        raise AndroidReferenceAppGateError(
            "reference pubspec no longer matches the exact host-test configuration"
        )
    updated = source.replace(test_hooks, android_hooks)
    if updated.replace(android_hooks, test_hooks) != source:
        raise AndroidReferenceAppGateError("Android pubspec patch changed unexpected bytes")
    pubspec.write_text(updated, encoding="utf-8", newline="")


def _load_archive(lock_path: Path) -> PinnedArchive:
    try:
        data = regular_file(lock_path, "native lock", maximum=MAX_LOCK_BYTES).read_bytes()
        value = strict_json(data, "native lock", maximum=MAX_LOCK_BYTES)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
        raise AndroidReferenceAppGateError("native lock is invalid")
    matches: list[dict[str, Any]] = []
    for raw in value["artifacts"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("target"), dict):
            raise AndroidReferenceAppGateError("native lock artifact is invalid")
        target = raw["target"]
        if (
            raw.get("id") == ARTIFACT_ID
            and target.get("os") == "android"
            and target.get("architecture") == ABI
            and target.get("variant") == "default"
            and target.get("min_os") == "24"
            and raw.get("flavor") == "cpu"
            and raw.get("runtime_mode") == "bundled"
        ):
            matches.append(raw)
    if len(matches) != 1:
        raise AndroidReferenceAppGateError("lock must select one Android arm64 artifact")
    source = matches[0].get("source")
    if not isinstance(source, dict) or source.get("archive") != "zip":
        raise AndroidReferenceAppGateError("locked Android source is invalid")
    url = source.get("url")
    if not isinstance(url, str):
        raise AndroidReferenceAppGateError("locked Android source URL is invalid")
    parsed = urllib.parse.urlsplit(url)
    basename = PurePosixPath(parsed.path).name
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.nuget.org"
        or parsed.query
        or parsed.fragment
        or basename != "microsoft.ml.onnxruntime.1.27.1.nupkg"
        or source.get("sha256") != ARTIFACT_SOURCE_SHA256
        or source.get("size_bytes") != ARTIFACT_SOURCE_SIZE
    ):
        raise AndroidReferenceAppGateError("locked Android archive identity changed")
    containers = matches[0].get("containers")
    if containers != [
        {
            "path": ARTIFACT_CONTAINER_PATH,
            "sha256": ARTIFACT_CONTAINER_SHA256,
            "size_bytes": ARTIFACT_CONTAINER_SIZE,
            "archive": "zip",
        }
    ]:
        raise AndroidReferenceAppGateError("locked Android container identity changed")
    expected_files = matches[0].get("expected_files")
    if expected_files != [
        {
            "path": ORT_ARCHIVE_PATH,
            "staged_path": "libonnxruntime.so",
            "sha256": ORT_SHA256,
            "size_bytes": ORT_SIZE_BYTES,
        }
    ]:
        raise AndroidReferenceAppGateError("locked Android runtime identity changed")
    return PinnedArchive(
        ARTIFACT_ID,
        basename,
        ARTIFACT_SOURCE_SHA256,
        ARTIFACT_SOURCE_SIZE,
        ARTIFACT_CONTAINER_PATH,
        ARTIFACT_CONTAINER_SHA256,
        ARTIFACT_CONTAINER_SIZE,
        ORT_ARCHIVE_PATH,
        ORT_SHA256,
        ORT_SIZE_BYTES,
    )


def _populate_cache(source_cache: Path, work: Path, archive: PinnedArchive) -> Path:
    try:
        source_cache = directory(source_cache, "supplied artifact cache")
        source = regular_file(
            source_cache / archive.basename,
            "locked Android archive",
            maximum=archive.size_bytes,
        )
        size, digest = sha256_file(
            source, "locked Android archive", maximum=archive.size_bytes
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if size != archive.size_bytes or digest != archive.sha256:
        raise AndroidReferenceAppGateError("Android archive differs from lock")
    destination_directory = work / ".fonix-artifact-cache"
    if destination_directory.exists() or destination_directory.is_symlink():
        raise AndroidReferenceAppGateError("generated cache survived source copy")
    destination_directory.mkdir(mode=0o700)
    destination = destination_directory / archive.basename
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        digest_builder = hashlib.sha256()
        copied = 0
        while chunk := input_stream.read(1024 * 1024):
            copied += len(chunk)
            if copied > archive.size_bytes:
                raise AndroidReferenceAppGateError("Android archive exceeded lock size")
            digest_builder.update(chunk)
            output_stream.write(chunk)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    if copied != archive.size_bytes or digest_builder.hexdigest() != archive.sha256:
        destination.unlink(missing_ok=True)
        raise AndroidReferenceAppGateError("copied Android archive differs from lock")
    return destination


def _canonical_zip_path(value: str) -> str | None:
    raw = value.rstrip("/")
    if not raw or raw.startswith("/") or "\\" in raw:
        return None
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _read_pinned_zip_member(
    source: Path | bytes,
    *,
    member: str,
    expected_size: int,
    expected_sha256: str,
    label: str,
) -> bytes:
    archive_source: Path | io.BytesIO = (
        source if isinstance(source, Path) else io.BytesIO(source)
    )
    try:
        archive = zipfile.ZipFile(archive_source)
    except (OSError, zipfile.BadZipFile) as error:
        raise AndroidReferenceAppGateError(f"{label} container is not a ZIP") from error
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise AndroidReferenceAppGateError(f"{label} container has too many entries")
        index: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            canonical = _canonical_zip_path(info.filename)
            if canonical is None or canonical in index:
                raise AndroidReferenceAppGateError(
                    f"{label} container has a duplicate or non-canonical path"
                )
            index[canonical] = info
        info = index.get(member)
        if info is None or info.is_dir() or info.flag_bits & 0x1:
            raise AndroidReferenceAppGateError(f"{label} member is missing or encrypted")
        if info.file_size != expected_size or info.compress_size <= 0:
            raise AndroidReferenceAppGateError(f"{label} member size changed")
        if info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
            raise AndroidReferenceAppGateError(
                f"{label} member has a suspicious compression ratio"
            )
        try:
            data = archive.read(info)
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            raise AndroidReferenceAppGateError(f"could not read {label} member") from error
    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise AndroidReferenceAppGateError(f"{label} member identity changed")
    return data


def _extract_reference_runtime(
    cached_archive: Path,
    work: Path,
    archive: PinnedArchive,
) -> Path:
    container = _read_pinned_zip_member(
        cached_archive,
        member=archive.container_path,
        expected_size=archive.container_size_bytes,
        expected_sha256=archive.container_sha256,
        label="locked Android AAR",
    )
    runtime = _read_pinned_zip_member(
        container,
        member=archive.runtime_path,
        expected_size=archive.runtime_size_bytes,
        expected_sha256=archive.runtime_sha256,
        label="locked Android ONNX Runtime",
    )
    root = work / ".android-reference-runtime" / ABI
    if root.parent.exists() or root.parent.is_symlink():
        raise AndroidReferenceAppGateError("reference-runtime output already exists")
    root.mkdir(parents=True, mode=0o700)
    destination = root / "libonnxruntime.so"
    with destination.open("xb") as output:
        output.write(runtime)
        output.flush()
        os.fsync(output.fileno())
    return destination


def _asset_inventory(root: Path, label: str) -> dict[str, bytes]:
    try:
        root = directory(root, label)
        entries = list(root.iterdir())
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidReferenceAppGateError(f"{label} is invalid") from error
    if {entry.name for entry in entries} != set(ASSET_NAMES):
        raise AndroidReferenceAppGateError(f"{label} file inventory changed")
    result: dict[str, bytes] = {}
    for name in sorted(ASSET_NAMES):
        try:
            path = regular_file(root / name, f"{label} {name}", maximum=MAX_ASSET_BYTES)
        except AndroidGateCommonError as error:
            raise _common(error) from error
        data = path.read_bytes()
        if not data:
            raise AndroidReferenceAppGateError(f"{label} {name} is empty")
        result[name] = data
    return result


def _verify_and_publish_android_assets(
    work: Path,
    generated_root: Path,
) -> dict[str, str]:
    generated = _asset_inventory(generated_root, "regenerated Android assets")
    committed = _asset_inventory(
        work / "assets/fonix/android-arm64-v8a",
        "committed Android sidecar assets",
    )
    if generated != committed:
        raise AndroidReferenceAppGateError(
            "regenerated Android assets differ from the committed sidecar"
        )
    generic_root = work / "assets/fonix"
    try:
        directory(generic_root, "generic packaged-asset directory")
    except AndroidGateCommonError as error:
        raise _common(error) from error
    for name, data in generated.items():
        destination = generic_root / name
        try:
            regular_file(destination, f"generic packaged asset {name}", maximum=MAX_ASSET_BYTES)
        except AndroidGateCommonError as error:
            raise _common(error) from error
        destination.write_bytes(data)
    published = {name: (generic_root / name).read_bytes() for name in generated}
    if published != generated:
        raise AndroidReferenceAppGateError("published Android assets changed during copy")
    return {
        name: hashlib.sha256(data).hexdigest() for name, data in sorted(generated.items())
    }


def _verify_flutter(
    flutter: Path,
    environment: Mapping[str, str],
) -> dict[str, object]:
    try:
        flutter = regular_file(flutter, "Flutter executable")
        output = run_bounded(
            (str(flutter), "--version", "--machine"),
            operation="Flutter version check",
            maximum_output=256 * 1024,
            environment=environment,
        )
        value = strict_json(output.stdout, "Flutter version", maximum=256 * 1024)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if not isinstance(value, dict) or value.get("frameworkRevision") != VALIDATED_FLUTTER_REVISION:
        raise AndroidReferenceAppGateError("Flutter revision differs from Android gate pin")
    return value


def _resolve_tool(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        tool = regular_file(resolved, label)
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidReferenceAppGateError(f"could not resolve {label}") from error
    if not os.access(tool, os.X_OK) and tool.suffix != ".jar":
        raise AndroidReferenceAppGateError(f"{label} is not executable")
    return tool


def _validate_java_home(java_home: Path) -> JavaTools:
    try:
        java_home = directory(java_home.resolve(strict=True), "Java home")
    except (OSError, AndroidGateCommonError) as error:
        raise AndroidReferenceAppGateError("could not resolve Java home") from error
    java = _resolve_tool(java_home / "bin/java", "Java executable")
    jarsigner = _resolve_tool(java_home / "bin/jarsigner", "jarsigner")
    environment = tool_environment()
    environment["JAVA_HOME"] = str(java_home)
    try:
        output = run_bounded(
            (str(java), "-XshowSettings:properties", "-version"),
            operation="Java identity check",
            maximum_output=256 * 1024,
            environment=environment,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
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
        raise AndroidReferenceAppGateError(
            f"Android gate requires OpenJDK {SUPPORTED_JAVA_VERSION}"
        )
    observed_home = properties.get("java.home")
    try:
        observed_home_path = Path(observed_home or "").resolve(strict=True)
    except OSError as error:
        raise AndroidReferenceAppGateError("Java reported an invalid home") from error
    if observed_home_path != java_home:
        raise AndroidReferenceAppGateError("Java executable is not bound to --java-home")
    vendor = properties.get("java.vendor")
    if not isinstance(vendor, str) or not vendor or len(vendor.encode("utf-8")) > 256:
        raise AndroidReferenceAppGateError("Java vendor identity is outside its bound")
    return JavaTools(java_home, java, jarsigner, SUPPORTED_JAVA_VERSION, vendor)


def _validate_bundletool(
    bundletool: Path,
    java: Path,
    environment: Mapping[str, str],
) -> dict[str, object]:
    try:
        bundletool = regular_file(
            bundletool.resolve(strict=True),
            "bundletool jar",
            maximum=BUNDLETOOL_SIZE_BYTES,
        )
        size, digest = sha256_file(
            bundletool, "bundletool jar", maximum=BUNDLETOOL_SIZE_BYTES
        )
        version = run_bounded(
            (str(java), "-jar", str(bundletool), "version"),
            operation="bundletool identity check",
            maximum_output=256 * 1024,
            environment=environment,
        ).stdout.strip()
    except (OSError, AndroidGateCommonError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidReferenceAppGateError("could not resolve bundletool") from error
    if (
        size != BUNDLETOOL_SIZE_BYTES
        or digest != BUNDLETOOL_SHA256
        or version != BUNDLETOOL_VERSION
    ):
        raise AndroidReferenceAppGateError("bundletool identity changed")
    return {"version": version, "sizeBytes": size, "sha256": digest}


def _default_tools(
    android_sdk: Path,
    *,
    include_device_tools: bool,
) -> dict[str, Path]:
    try:
        android_sdk = directory(android_sdk, "Android SDK")
    except AndroidGateCommonError as error:
        raise AndroidReferenceAppGateError("could not resolve Android SDK") from error
    build_tools = android_sdk / "build-tools" / ANDROID_BUILD_TOOLS_VERSION
    prebuilt = android_sdk / "ndk" / ANDROID_NDK_VERSION / "toolchains/llvm/prebuilt"
    try:
        host_directories = [entry for entry in prebuilt.iterdir() if entry.is_dir()]
    except OSError as error:
        raise AndroidReferenceAppGateError("pinned Android NDK is missing") from error
    if len(host_directories) != 1:
        raise AndroidReferenceAppGateError("pinned Android NDK host toolchain is ambiguous")
    command_line_tools = (
        android_sdk / "cmdline-tools" / ANDROID_COMMAND_LINE_TOOLS_VERSION
    )
    try:
        source_properties = regular_file(
            command_line_tools / "source.properties",
            "Android command-line tools identity",
            maximum=64 * 1024,
        ).read_text(encoding="utf-8")
    except (AndroidGateCommonError, UnicodeDecodeError) as error:
        raise AndroidReferenceAppGateError(
            "pinned Android command-line tools are missing"
        ) from error
    if source_properties != (
        f"Pkg.Revision={ANDROID_COMMAND_LINE_TOOLS_VERSION}\n"
        f"Pkg.Path=cmdline-tools;{ANDROID_COMMAND_LINE_TOOLS_VERSION}\n"
        "Pkg.Desc=Android SDK Command-line Tools\n"
    ):
        raise AndroidReferenceAppGateError(
            "Android command-line tools identity changed"
        )
    tools = {
        "apkanalyzer": _resolve_tool(
            command_line_tools / "bin/apkanalyzer", "apkanalyzer"
        ),
        "zipalign": _resolve_tool(build_tools / "zipalign", "zipalign"),
        "apksigner": _resolve_tool(build_tools / "apksigner", "apksigner"),
        "readelf": _resolve_tool(
            host_directories[0] / "bin/llvm-readelf", "NDK llvm-readelf"
        ),
    }
    if include_device_tools:
        tools.update(
            {
                "adb": _resolve_tool(android_sdk / "platform-tools/adb", "adb"),
                "emulator": _resolve_tool(
                    android_sdk / "emulator/emulator", "emulator"
                ),
            }
        )
    return tools


def _run_command(
    command: Sequence[str],
    *,
    operation: str,
    cwd: Path | None = None,
    maximum_output: int = 2 * 1024 * 1024,
    timeout_seconds: int = 30 * 60,
    environment: Mapping[str, str] | None = None,
) -> str:
    try:
        return run_bounded(
            command,
            operation=operation,
            cwd=cwd,
            maximum_output=maximum_output,
            timeout_seconds=timeout_seconds,
            environment=environment,
        ).stdout
    except AndroidGateCommonError as error:
        raise _common(error) from error


def _audit_package(
    *,
    repository: Path,
    artifact: Path,
    reference_runtime: Path,
    tools: Mapping[str, Path],
    java: Path,
    jarsigner: Path,
    bundletool: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-B",
        str(repository / "tool/ci/audit_android_application.py"),
        "--repository",
        str(repository),
        "--artifact",
        str(artifact),
        "--reference-runtime",
        str(reference_runtime),
        "--readelf",
        str(tools["readelf"]),
    ]
    if artifact.suffix == ".apk":
        command.extend(
            [
                "--apkanalyzer",
                str(tools["apkanalyzer"]),
                "--zipalign",
                str(tools["zipalign"]),
                "--apksigner",
                str(tools["apksigner"]),
            ]
        )
    else:
        command.extend(
            [
                "--java",
                str(java),
                "--bundletool",
                str(bundletool),
                "--jarsigner",
                str(jarsigner),
            ]
        )
    output = _run_command(
        command,
        operation=f"final {artifact.suffix} audit",
        environment=environment,
    )
    try:
        value = strict_json(output, "Android package audit", maximum=2 * 1024 * 1024)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    if (
        not isinstance(value, dict)
        or value.get("result") != "passed"
        or value.get("artifact") != str(artifact)
        or value.get("abi") != ABI
        or value.get("shimBuildId") != EXPECTED_SHIM_BUILD_ID
    ):
        raise AndroidReferenceAppGateError("Android package audit is not bound to output")
    return value


def _adb(adb: Path, serial: str, arguments: Sequence[str], operation: str) -> str:
    return _run_command(
        (str(adb), "-s", serial, *arguments),
        operation=operation,
        timeout_seconds=120,
        maximum_output=MAX_LOGCAT_BYTES,
    ).strip()


def _adb_device_inventory(adb: Path) -> dict[str, str]:
    output = _run_command(
        (str(adb), "devices"), operation="adb device inventory", maximum_output=64 * 1024
    )
    lines = output.splitlines()
    if not lines or lines[0] != "List of devices attached":
        raise AndroidReferenceAppGateError("adb device inventory header changed")
    result: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        fields = line.split(maxsplit=1)
        if (
            len(fields) != 2
            or re.fullmatch(r"[!-~]{1,256}", fields[0]) is None
            or re.fullmatch(r"[ -~]{1,256}", fields[1]) is None
            or fields[0] in result
        ):
            raise AndroidReferenceAppGateError("adb device inventory is not closed")
        result[fields[0]] = fields[1]
    return result


def _adb_devices(adb: Path) -> set[str]:
    return {
        serial
        for serial, state in _adb_device_inventory(adb).items()
        if state == "device"
    }


def _adb_known_serials(adb: Path) -> set[str]:
    return set(_adb_device_inventory(adb))


def _query_avd_name(adb: Path, serial: str) -> str:
    output = _adb(adb, serial, ("emu", "avd", "name"), "AVD name query")
    lines = output.splitlines()
    if (
        len(lines) not in {1, 2}
        or (len(lines) == 2 and lines[1] != "OK")
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", lines[0]) is None
    ):
        raise AndroidReferenceAppGateError("AVD name response is not exact")
    return lines[0]


def _running_named_avd(adb: Path, name: str) -> str | None:
    matches: list[str] = []
    for serial in sorted(_adb_devices(adb)):
        if not serial.startswith("emulator-"):
            continue
        try:
            observed = _query_avd_name(adb, serial)
        except AndroidReferenceAppGateError:
            continue
        if observed == name:
            matches.append(serial)
    if len(matches) > 1:
        raise AndroidReferenceAppGateError("more than one running emulator has the named AVD")
    return matches[0] if matches else None


def _reap_owned_avd(
    process: subprocess.Popen[bytes],
    *,
    adb: Path | None = None,
    serial: str | None = None,
) -> None:
    if (adb is None) != (serial is None):
        raise AndroidReferenceAppGateError(
            "owned AVD cleanup requires both adb and serial"
        )
    shutdown_error: AndroidReferenceAppGateError | None = None
    if adb is not None and serial is not None:
        try:
            _adb(adb, serial, ("emu", "kill"), "owned AVD shutdown")
        except AndroidReferenceAppGateError as error:
            shutdown_error = error
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired as error:
            raise AndroidReferenceAppGateError(
                "owned AVD process could not be reaped"
            ) from error

    if adb is None or serial is None:
        return
    deadline = time.monotonic() + AVD_SHUTDOWN_TIMEOUT_SECONDS
    while True:
        try:
            present = serial in _adb_known_serials(adb)
        except AndroidReferenceAppGateError as error:
            raise AndroidReferenceAppGateError(
                "owned AVD disappearance could not be verified"
            ) from error
        if not present:
            return
        if time.monotonic() >= deadline:
            message = "owned AVD serial remained after process cleanup"
            if shutdown_error is not None:
                message += f"; emulator shutdown failed: {shutdown_error}"
            raise AndroidReferenceAppGateError(message)
        time.sleep(AVD_SHUTDOWN_POLL_SECONDS)


def _start_named_avd(
    adb: Path,
    emulator: Path,
    name: str,
) -> tuple[str, subprocess.Popen[bytes] | None]:
    existing = _running_named_avd(adb, name)
    if existing is not None:
        return existing, None
    before = _adb_known_serials(adb)
    try:
        process = subprocess.Popen(
            (
                str(emulator),
                "-avd",
                name,
                "-read-only",
                "-no-window",
                "-no-audio",
                "-no-boot-anim",
                "-no-snapshot-save",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=tool_environment(),
        )
    except OSError as error:
        raise AndroidReferenceAppGateError("could not start named AVD") from error
    handed_off = False
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AndroidReferenceAppGateError("named AVD exited before boot")
            candidates = sorted(
                value
                for value in _adb_devices(adb) - before
                if value.startswith("emulator-")
            )
            matching_candidates: list[str] = []
            for candidate in candidates:
                try:
                    if _query_avd_name(adb, candidate) == name:
                        matching_candidates.append(candidate)
                except AndroidReferenceAppGateError:
                    continue
            if len(matching_candidates) > 1:
                raise AndroidReferenceAppGateError(
                    "named AVD produced ambiguous devices"
                )
            if matching_candidates:
                serial = matching_candidates[0]
                if (
                    _adb(
                        adb,
                        serial,
                        ("shell", "getprop", "sys.boot_completed"),
                        "AVD boot query",
                    )
                    == "1"
                ):
                    handed_off = True
                    return serial, process
            time.sleep(1)
        raise AndroidReferenceAppGateError("named AVD did not boot within 300 seconds")
    finally:
        if not handed_off:
            _reap_owned_avd(process)


def _positive_device_int(value: str, label: str, *, maximum: int) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise AndroidReferenceAppGateError(f"{label} is not a positive integer")
    result = int(value)
    if result > maximum:
        raise AndroidReferenceAppGateError(f"{label} exceeds its bound")
    return result


def _validate_receipt(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(EXPECTED_RECEIPT):
        raise AndroidReferenceAppGateError("Android smoke receipt field set changed")
    for key, expected in EXPECTED_RECEIPT.items():
        actual = value[key]
        if type(actual) is not type(expected) or actual != expected:
            raise AndroidReferenceAppGateError(f"Android receipt field {key!r} changed")
    return {key: value[key] for key in EXPECTED_RECEIPT}


def _parse_package_uid(output: str) -> int:
    match = re.fullmatch(
        rf"package:{re.escape(APPLICATION_ID)} uid:([1-9][0-9]{{0,9}})",
        output,
    )
    if match is None:
        raise AndroidReferenceAppGateError(
            "installed package manager UID response is not exact"
        )
    uid = int(match.group(1))
    if uid < 10_000 or uid > 19_999:
        raise AndroidReferenceAppGateError("installed application UID is out of range")
    return uid


def _parse_logcat_uid(value: str) -> int:
    if re.fullmatch(r"[1-9][0-9]{0,9}", value):
        result = int(value)
    else:
        match = re.fullmatch(r"u([0-9]{1,5})_a([0-9]{1,4})", value)
        if match is None:
            raise AndroidReferenceAppGateError(
                "Android logcat receipt has an unsupported UID identity"
            )
        result = int(match.group(1)) * 100_000 + 10_000 + int(match.group(2))
    if result <= 0 or result > 2_147_483_647:
        raise AndroidReferenceAppGateError("Android logcat UID exceeds its bound")
    return result


def _parse_application_pid(output: str) -> int | None:
    if output == "":
        return None
    fields = output.split(" ")
    if (
        len(fields) != 1
        or not re.fullmatch(r"[1-9][0-9]{0,9}", fields[0])
    ):
        raise AndroidReferenceAppGateError(
            "installed application process identity is ambiguous"
        )
    result = int(fields[0])
    if result > 2_147_483_647:
        raise AndroidReferenceAppGateError("installed application PID exceeds its bound")
    return result


def _query_application_pid(adb: Path, serial: str) -> int | None:
    output = _adb(
        adb,
        serial,
        (
            "shell",
            "sh",
            "-c",
            f"pidof {APPLICATION_ID} 2>/dev/null || true",
        ),
        "installed application PID query",
    )
    return _parse_application_pid(output)


def _logcat_arguments(uid: int, pid: int | None) -> tuple[str, ...]:
    if uid <= 0 or uid > 2_147_483_647:
        raise AndroidReferenceAppGateError("logcat UID filter is outside its bound")
    arguments = [
        "logcat",
        "-d",
        "-b",
        "main",
        "-v",
        "threadtime,uid,printable",
        f"--uid={uid}",
    ]
    if pid is not None:
        if pid <= 0 or pid > 2_147_483_647:
            raise AndroidReferenceAppGateError("logcat PID filter is outside its bound")
        arguments.append(f"--pid={pid}")
    arguments.extend(("-s", f"{LOG_TAG}:I", "*:S"))
    return tuple(arguments)


def _parse_logcat_receipt(
    output: str,
    *,
    expected_uid: int,
    observed_pid: int | None,
) -> LogcatReceiptEvidence:
    try:
        encoded = output.encode("ascii")
    except UnicodeEncodeError as error:
        raise AndroidReferenceAppGateError(
            "Android logcat output is not printable ASCII"
        ) from error
    if len(encoded) > MAX_LOGCAT_BYTES:
        raise AndroidReferenceAppGateError("Android logcat output exceeds its bound")
    if "\r" in output or "\x00" in output:
        raise AndroidReferenceAppGateError("Android logcat output has invalid controls")
    if expected_uid <= 0 or expected_uid > 2_147_483_647:
        raise AndroidReferenceAppGateError("expected Android application UID is invalid")
    if observed_pid is not None and (
        observed_pid <= 0 or observed_pid > 2_147_483_647
    ):
        raise AndroidReferenceAppGateError("observed Android application PID is invalid")

    line_pattern = re.compile(
        r"^[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}"
        r"[ ]+([A-Za-z0-9_]{1,32})[ ]+([1-9][0-9]{0,9})"
        r"[ ]+([1-9][0-9]{0,9})[ ]+I[ ]+"
        + re.escape(LOG_TAG)
        + r"[ ]*: ([\x20-\x7e]+)$"
    )
    successes: list[tuple[str, int, int]] = []
    failures: list[tuple[str, int, int]] = []
    for line in output.splitlines():
        if RECEIPT_PREFIX not in line and FAILURE_PREFIX not in line:
            continue
        if len(line.encode("ascii")) > MAX_RECEIPT_BYTES + 512:
            raise AndroidReferenceAppGateError("Android smoke log line exceeds its bound")
        match = line_pattern.fullmatch(line)
        if match is None:
            raise AndroidReferenceAppGateError(
                "Android smoke log line lacks exact UID/PID provenance"
            )
        uid = _parse_logcat_uid(match.group(1))
        pid = int(match.group(2))
        if uid != expected_uid:
            raise AndroidReferenceAppGateError(
                "Android smoke log line came from a different UID"
            )
        if observed_pid is not None and pid != observed_pid:
            raise AndroidReferenceAppGateError(
                "Android smoke log line came from a different process"
            )
        message = match.group(4)
        if message.startswith(RECEIPT_PREFIX):
            successes.append((message[len(RECEIPT_PREFIX) :], uid, pid))
        elif message.startswith(FAILURE_PREFIX):
            failures.append((message[len(FAILURE_PREFIX) :], uid, pid))
        else:
            raise AndroidReferenceAppGateError(
                "Android smoke log line has a non-canonical completion prefix"
            )
    if not successes and not failures:
        raise AndroidReferenceReceiptPending("Android smoke receipt is not present yet")
    if len(successes) != 1 or failures:
        raise AndroidReferenceAppGateError(
            "Android smoke must emit exactly one success and zero failures"
        )
    raw_receipt, uid, pid = successes[0]
    if len(raw_receipt.encode("ascii")) > MAX_RECEIPT_BYTES:
        raise AndroidReferenceAppGateError("Android smoke receipt exceeds 16 KiB")
    if raw_receipt != EXPECTED_RECEIPT_JSON:
        raise AndroidReferenceAppGateError(
            "Android smoke receipt is not the exact canonical JSON"
        )
    try:
        value = strict_json(
            raw_receipt,
            "Android smoke receipt",
            maximum=MAX_RECEIPT_BYTES,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    return LogcatReceiptEvidence(
        _validate_receipt(value),
        uid,
        pid,
        observed_pid is not None,
    )


def _parse_installed_base_apk_path(output: str) -> str:
    match = re.fullmatch(
        rf"package:(/data/app/~~[A-Za-z0-9_+=-]{{1,128}}/"
        rf"{re.escape(APPLICATION_ID)}-[A-Za-z0-9_+=-]{{1,256}}/base\.apk)",
        output,
    )
    if match is None:
        raise AndroidReferenceAppGateError(
            "installed package does not have one closed /data/app base APK"
        )
    return match.group(1)


def _parse_device_sha256(output: str, expected_path: str) -> str:
    match = re.fullmatch(r"([0-9a-f]{64})  (/[\x21-\x7e]{1,1024})", output)
    if match is None or match.group(2) != expected_path:
        raise AndroidReferenceAppGateError(
            "device base APK checksum response is not exact"
        )
    return match.group(1)


def _require_lower_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AndroidReferenceAppGateError(f"{label} is not a lowercase SHA-256")
    return value


def _matching_signer_sha256(
    apk_audit: Mapping[str, object],
    aab_audit: Mapping[str, object],
) -> str:
    values: list[str] = []
    for label, audit in (("APK", apk_audit), ("AAB", aab_audit)):
        signing = audit.get("signing")
        if not isinstance(signing, dict):
            raise AndroidReferenceAppGateError(f"{label} signing evidence is missing")
        values.append(
            _require_lower_sha256(
                signing.get("certificateSha256"),
                f"{label} signing certificate digest",
            )
        )
    if values[0] != values[1]:
        raise AndroidReferenceAppGateError(
            "APK and AAB signing certificate digests differ"
        )
    return values[0]


def _verify_installed_apk(
    adb: Path,
    serial: str,
    apk: Path,
    expected_sha256: str,
) -> str:
    expected_sha256 = _require_lower_sha256(
        expected_sha256,
        "audited APK digest",
    )
    host_identity = _file_identity(
        apk,
        "installed APK input",
        maximum=MAX_ARCHIVE_BYTES,
    )
    if host_identity.sha256 != expected_sha256:
        raise AndroidReferenceAppGateError("installed APK input differs from audit")
    installed_path = _parse_installed_base_apk_path(
        _adb(
            adb,
            serial,
            ("shell", "pm", "path", APPLICATION_ID),
            "installed base APK path query",
        )
    )
    device_sha256 = _parse_device_sha256(
        _adb(
            adb,
            serial,
            ("shell", "sha256sum", installed_path),
            "installed base APK checksum",
        ),
        installed_path,
    )
    if device_sha256 != expected_sha256:
        raise AndroidReferenceAppGateError("installed base APK differs from audited APK")
    _require_file_identity(
        apk,
        host_identity,
        "installed APK input after device checksum",
        maximum=MAX_ARCHIVE_BYTES,
    )
    return device_sha256


def _cleanup_installed_application(adb: Path, serial: str) -> None:
    errors: list[AndroidReferenceAppGateError] = []
    try:
        _adb(
            adb,
            serial,
            ("shell", "am", "force-stop", APPLICATION_ID),
            "app cleanup",
        )
    except AndroidReferenceAppGateError as error:
        errors.append(error)
    try:
        uninstall_output = _adb(
            adb,
            serial,
            ("uninstall", APPLICATION_ID),
            "APK uninstall",
        )
        if uninstall_output != "Success":
            errors.append(
                AndroidReferenceAppGateError(
                    "APK uninstall did not return the exact success receipt"
                )
            )
    except AndroidReferenceAppGateError as error:
        errors.append(error)
    try:
        remaining = _adb(
            adb,
            serial,
            (
                "shell",
                "cmd",
                "package",
                "list",
                "packages",
                APPLICATION_ID,
            ),
            "APK removal verification",
        )
        if remaining:
            errors.append(
                AndroidReferenceAppGateError(
                    "installed application remained after APK uninstall"
                )
            )
    except AndroidReferenceAppGateError as error:
        errors.append(error)
    if errors:
        raise AndroidReferenceAppGateError(
            f"installed application cleanup failed: {errors[0]}"
        ) from errors[0]


def _run_on_avd(
    adb: Path,
    serial: str,
    apk: Path,
    *,
    expected_apk_sha256: str,
) -> dict[str, object]:
    abi = _adb(adb, serial, ("shell", "getprop", "ro.product.cpu.abi"), "device ABI query")
    if abi != ABI:
        raise AndroidReferenceAppGateError(f"named AVD ABI is {abi!r}, expected {ABI}")
    api = _positive_device_int(
        _adb(adb, serial, ("shell", "getprop", "ro.build.version.sdk"), "device API query"),
        "device API",
        maximum=100,
    )
    if api != ANDROID_API:
        raise AndroidReferenceAppGateError(
            f"named AVD API is {api}, expected pinned API {ANDROID_API}"
        )
    page_size = _positive_device_int(
        _adb(adb, serial, ("shell", "getconf", "PAGE_SIZE"), "device page-size query"),
        "device page size",
        maximum=64 * 1024,
    )
    if page_size not in {4096, 16 * 1024}:
        raise AndroidReferenceAppGateError("device page size is outside 4/16 KiB contract")
    if _adb(adb, serial, ("shell", "getprop", "ro.kernel.qemu"), "emulator identity query") != "1":
        raise AndroidReferenceAppGateError("named Android target is not an emulator")
    fingerprint = _adb(
        adb, serial, ("shell", "getprop", "ro.build.fingerprint"), "device fingerprint query"
    )
    if not fingerprint or len(fingerprint.encode("utf-8")) > 1024:
        raise AndroidReferenceAppGateError("device fingerprint is outside its bound")
    try:
        _adb(
            adb,
            serial,
            ("install", "-r", "--no-streaming", str(apk)),
            "APK install",
        )
        installed_apk_sha256 = _verify_installed_apk(
            adb,
            serial,
            apk,
            expected_apk_sha256,
        )
        application_uid = _parse_package_uid(
            _adb(
                adb,
                serial,
                (
                    "shell",
                    "cmd",
                    "package",
                    "list",
                    "packages",
                    "-U",
                    APPLICATION_ID,
                ),
                "installed package UID query",
            )
        )
        _adb(adb, serial, ("logcat", "-c"), "logcat clear")
        _adb(adb, serial, ("shell", "am", "force-stop", APPLICATION_ID), "app force-stop")
        _adb(
            adb,
            serial,
            ("shell", "am", "start", "-n", APPLICATION_COMPONENT),
            "reference app launch",
        )
        deadline = time.monotonic() + 90
        receipt_evidence: LogcatReceiptEvidence | None = None
        observed_pid: int | None = None
        while time.monotonic() < deadline:
            current_pid = _query_application_pid(adb, serial)
            if current_pid is not None:
                if observed_pid is not None and current_pid != observed_pid:
                    raise AndroidReferenceAppGateError(
                        "installed application PID changed during the smoke run"
                    )
                observed_pid = current_pid
            output = _adb(
                adb,
                serial,
                _logcat_arguments(application_uid, observed_pid),
                "reference logcat capture",
            )
            try:
                receipt_evidence = _parse_logcat_receipt(
                    output,
                    expected_uid=application_uid,
                    observed_pid=observed_pid,
                )
                break
            except AndroidReferenceReceiptPending:
                pass
            time.sleep(0.5)
        if receipt_evidence is None:
            raise AndroidReferenceAppGateError("reference receipt was not observed")
    finally:
        _cleanup_installed_application(adb, serial)
    return {
        "kind": "emulator",
        "abi": abi,
        "androidApi": api,
        "pageSizeBytes": page_size,
        "fingerprintSha256": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
        "installedApkSha256": installed_apk_sha256,
        "receipt": receipt_evidence.receipt,
        "receiptProvenance": {
            "applicationUid": application_uid,
            "logcatUidFilter": application_uid,
            "receiptUid": receipt_evidence.uid,
            "receiptPid": receipt_evidence.pid,
            "observedApplicationPid": observed_pid,
            "pidBinding": (
                "matched-observed-process"
                if receipt_evidence.pid_was_observed
                else "uid-filtered-process-exited-before-pid-query"
            ),
        },
        "claimBoundary": (
            "This runtime receipt applies only to the queried emulator ABI, API, "
            "page size, installed APK bytes, and CPU fixture."
        ),
    }


def run_gate(
    *,
    repository: Path,
    flutter: Path,
    artifact_cache: Path,
    work_directory: Path,
    android_sdk: Path,
    java_home: Path,
    bundletool: Path,
    avd_name: str | None,
) -> dict[str, object]:
    try:
        repository = directory(repository.resolve(strict=True), "Fonix repository")
        flutter = _resolve_tool(flutter, "Flutter executable")
        artifact_cache = directory(artifact_cache.resolve(strict=True), "artifact cache")
        android_sdk = directory(android_sdk.resolve(strict=True), "Android SDK")
    except (OSError, AndroidGateCommonError) as error:
        if isinstance(error, AndroidGateCommonError):
            raise _common(error) from error
        raise AndroidReferenceAppGateError("could not resolve Android gate inputs") from error
    if not work_directory.is_absolute():
        raise AndroidReferenceAppGateError("--work-dir must be absolute")
    java_tools = _validate_java_home(java_home)
    build_environment = _android_build_environment(java_tools.home, android_sdk)
    bundletool_identity = _validate_bundletool(
        bundletool,
        java_tools.java,
        build_environment,
    )
    bundletool = Path(bundletool).resolve(strict=True)
    flutter_version = _verify_flutter(flutter, build_environment)
    tools = _default_tools(android_sdk, include_device_tools=avd_name is not None)
    dart = _resolve_tool(flutter.parent / "cache/dart-sdk/bin/dart", "Flutter Dart")
    summary = _copy_example(repository / "example", work_directory)
    if summary.file_count == 0:
        raise AndroidReferenceAppGateError("reference source copy is empty")
    committed_lock_identity = _file_identity(
        repository / "example/pubspec.lock",
        "committed reference pubspec lock",
        maximum=MAX_PUBSPEC_LOCK_BYTES,
    )
    _require_file_identity(
        work_directory / "pubspec.lock",
        committed_lock_identity,
        "copied reference pubspec lock",
        maximum=MAX_PUBSPEC_LOCK_BYTES,
    )
    _patch_pubspec(work_directory / "pubspec.yaml", repository)
    working_lock_identity = _patch_pubspec_lock(
        work_directory / "pubspec.lock",
        repository,
    )
    archive = _load_archive(repository / "native/versions.lock.yaml")
    cached_archive = _populate_cache(artifact_cache, work_directory, archive)
    reference_runtime = _extract_reference_runtime(
        cached_archive,
        work_directory,
        archive,
    )

    _run_locked_pub_get(
        flutter,
        work_directory,
        build_environment,
        working_lock_identity,
        operation="offline Flutter pub get",
    )
    _verify_android_local_properties(
        work_directory / "android/local.properties",
        android_sdk,
    )
    generated_assets = work_directory / ".android-reference-assets"
    if generated_assets.exists() or generated_assets.is_symlink():
        raise AndroidReferenceAppGateError("Android asset reproduction output exists")
    try:
        asset_cli = regular_file(
            repository / "bin/fonix_prepare_flutter_assets.dart",
            "Fonix Flutter asset CLI",
            maximum=1024 * 1024,
        )
        package_config = regular_file(
            work_directory / ".dart_tool/package_config.json",
            "reference package configuration",
            maximum=8 * 1024 * 1024,
        )
    except AndroidGateCommonError as error:
        raise _common(error) from error
    _run_command(
        (
            str(dart),
            f"--packages={package_config}",
            str(asset_cli),
            "--target-os",
            "android",
            "--architecture",
            ABI,
            "--variant",
            "default",
            "--package-root",
            str(repository),
            "--cache",
            str(cached_archive.parent),
            "--output",
            str(generated_assets),
        ),
        operation="Android Flutter asset preparation",
        cwd=work_directory,
        environment=build_environment,
    )
    asset_identities = _verify_and_publish_android_assets(
        work_directory,
        generated_assets,
    )
    _run_command(
        (str(flutter), "analyze", "--no-pub"),
        operation="reference Flutter analysis",
        cwd=work_directory,
        environment=build_environment,
    )
    _run_command(
        (str(flutter), "test", "--no-pub"),
        operation="reference Flutter tests",
        cwd=work_directory,
        environment=build_environment,
    )
    _select_android_hooks(work_directory / "pubspec.yaml")
    _run_locked_pub_get(
        flutter,
        work_directory,
        build_environment,
        working_lock_identity,
        operation="offline Android Flutter pub get",
    )
    _verify_android_local_properties(
        work_directory / "android/local.properties",
        android_sdk,
    )
    build_common = (
        "--release",
        "--no-pub",
        "--target-platform",
        "android-arm64",
        "--dart-define",
        SMOKE_DART_DEFINE,
    )
    _run_command(
        (str(flutter), "build", "apk", *build_common),
        operation="reference Release APK build",
        cwd=work_directory,
        environment=build_environment,
    )
    apk = work_directory / "build/app/outputs/flutter-apk/app-release.apk"
    try:
        apk = regular_file(apk, "Release APK", maximum=MAX_ARCHIVE_BYTES)
    except AndroidGateCommonError as error:
        raise _common(error) from error
    _run_command(
        (str(flutter), "build", "appbundle", *build_common),
        operation="reference Release AAB build",
        cwd=work_directory,
        environment=build_environment,
    )
    aab = work_directory / "build/app/outputs/bundle/release/app-release.aab"
    try:
        aab = regular_file(aab, "Release AAB", maximum=MAX_ARCHIVE_BYTES)
    except AndroidGateCommonError as error:
        raise _common(error) from error

    # Both independent final-package audits intentionally precede any install.
    apk_audit = _audit_package(
        repository=repository,
        artifact=apk,
        reference_runtime=reference_runtime,
        tools=tools,
        java=java_tools.java,
        jarsigner=java_tools.jarsigner,
        bundletool=bundletool,
        environment=build_environment,
    )
    aab_audit = _audit_package(
        repository=repository,
        artifact=aab,
        reference_runtime=reference_runtime,
        tools=tools,
        java=java_tools.java,
        jarsigner=java_tools.jarsigner,
        bundletool=bundletool,
        environment=build_environment,
    )
    if (
        apk_audit.get("ortSha256") != aab_audit.get("ortSha256")
        or apk_audit.get("shimSha256") != aab_audit.get("shimSha256")
        or apk_audit.get("modelSha256") != aab_audit.get("modelSha256")
        or apk_audit.get("ortReferenceSha256") != ORT_SHA256
        or aab_audit.get("ortReferenceSha256") != ORT_SHA256
    ):
        raise AndroidReferenceAppGateError("APK and AAB identities differ")
    apk_sha256 = _require_lower_sha256(
        apk_audit.get("artifactSha256"),
        "audited APK digest",
    )
    signer_sha256 = _matching_signer_sha256(apk_audit, aab_audit)

    device_evidence: dict[str, object] | None = None
    emulator_process: subprocess.Popen[bytes] | None = None
    serial: str | None = None
    if avd_name is not None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", avd_name):
            raise AndroidReferenceAppGateError("--avd-name is not a closed token")
        try:
            serial, emulator_process = _start_named_avd(
                tools["adb"], tools["emulator"], avd_name
            )
            device_evidence = _run_on_avd(
                tools["adb"],
                serial,
                apk,
                expected_apk_sha256=apk_sha256,
            )
        finally:
            if emulator_process is not None:
                _reap_owned_avd(
                    emulator_process,
                    adb=tools["adb"],
                    serial=serial,
                )

    report: dict[str, object] = {
        "schemaVersion": 1,
        "result": "passed",
        "applicationId": APPLICATION_ID,
        "abi": ABI,
        "artifactId": ARTIFACT_ID,
        "archiveSha256": archive.sha256,
        "flutterRevision": flutter_version["frameworkRevision"],
        "flutterVersion": flutter_version.get("frameworkVersion"),
        "androidBuildToolsVersion": ANDROID_BUILD_TOOLS_VERSION,
        "androidNdkVersion": ANDROID_NDK_VERSION,
        "androidCommandLineToolsVersion": ANDROID_COMMAND_LINE_TOOLS_VERSION,
        "java": {
            "version": java_tools.version,
            "vendor": java_tools.vendor,
            "home": str(java_tools.home),
        },
        "bundletool": bundletool_identity,
        "androidAssetSha256": asset_identities,
        "pubspecLockSha256": working_lock_identity.sha256,
        "signingCertificateSha256": signer_sha256,
        "analysis": "passed",
        "tests": "passed",
        "releaseApkAudit": apk_audit,
        "releaseAabAudit": aab_audit,
        "deviceEvidence": device_evidence,
        "claimBoundary": (
            "This is a CI release-mode package gate, not a reproducible or "
            "distribution release build; Gradle dependency verification metadata "
            "is absent. Static package evidence and optional runtime evidence remain "
            "separate. "
            "A missing device receipt, a 4 KiB page-size receipt, or an API-35-only "
            "receipt does not prove another device, 16 KiB runtime, or API-24 execution."
        ),
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise AndroidReferenceAppGateError("Android reference gate report exceeds its bound")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--flutter", type=Path, required=True)
    parser.add_argument("--artifact-cache", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--android-sdk", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--bundletool", type=Path, required=True)
    parser.add_argument("--avd-name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = run_gate(
            repository=arguments.repository,
            flutter=arguments.flutter,
            artifact_cache=arguments.artifact_cache,
            work_directory=arguments.work_dir,
            android_sdk=arguments.android_sdk,
            java_home=arguments.java_home,
            bundletool=arguments.bundletool,
            avd_name=arguments.avd_name,
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (AndroidReferenceAppGateError, AndroidGateCommonError, OSError) as error:
        print(f"run_android_reference_app_gate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
