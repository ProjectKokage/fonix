#!/usr/bin/env python3
"""Build, exercise, and audit the committed Fonix macOS reference app."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import stat
import subprocess
import sys
import threading
from typing import Any, Mapping, NamedTuple, Sequence
import urllib.parse


VALIDATED_FLUTTER_REVISION = "bd1e75d918605c91b411e8789fb911e6c9a84534"
APPLICATION_MINIMUM_OS = "14.0"
APPLICATION_BUNDLE_NAME = "Fonix Reference.app"
APPLICATION_EXECUTABLE_NAME = "Fonix Reference"
ARTIFACT_ID = "onnxruntime-1.27.1-macos-arm64-cpu"
ARTIFACT_VERSION = "1.27.1"
MODEL_SHA256 = "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10"

COMMAND_TIMEOUT_SECONDS = 30 * 60
REFERENCE_TIMEOUT_SECONDS = 60
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_OUTPUT_BYTES = 64 * 1024
MAX_REPORT_BYTES = 16 * 1024
MAX_LOCK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_COPY_ENTRIES = 100_000
MAX_COPY_FILES = 10_000
MAX_COPY_BYTES = 64 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024

ASSET_NAMES = frozenset(
    {"fonix-native-artifact-manifest.json", "ThirdPartyNotices.txt"}
)
ANDROID_SIDECAR_DIRECTORY = "android-arm64-v8a"

# These are exact paths relative to example/. A prefix is excluded only when it
# is this path or a child of it; similarly named source paths remain included.
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

EXPECTED_REFERENCE_RECEIPT: dict[str, object] = {
    "schemaVersion": 1,
    "status": "passed",
    "runtimeVersion": "1.27.1",
    "runtimeSource": "bundled",
    "runtimeOwner": "wrapper",
    "artifactFlavor": "cpu",
    "platform": "macos",
    "architecture": "arm64",
    "shimBuildId": ARTIFACT_ID,
    "artifactSha256": (
        "e42b77a7281cc6e55141bf44fcfbac2c782b823a491bbb6ac33c781dd991f8a6"
    ),
    "modelSha256": MODEL_SHA256,
    "outputValues": [1, 4, 9, 16, 25, 36],
    "activeProviders": ["cpu"],
    "fullCpuAssignment": True,
    "doubleClose": "passed",
}
REFERENCE_RECEIPT_PREFIX = "FONIX_REFERENCE_RECEIPT="

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODESIGN_FLAGS = re.compile(
    r"^[ \t]*CodeDirectory\b[^\r\n]*[ \t]flags=0x(?P<bits>[0-9a-fA-F]+)"
    r"\((?P<names>[^()\r\n]*)\)(?:[ \t]|$)[^\r\n]*$",
    re.MULTILINE,
)


class MacOsReferenceAppGateError(RuntimeError):
    """The committed reference-application gate failed closed."""


class PinnedArchive(NamedTuple):
    artifact_id: str
    basename: str
    sha256: str
    size_bytes: int


class CopySummary(NamedTuple):
    file_count: int
    byte_count: int


class IncludedFile(NamedTuple):
    path: Path
    size_bytes: int
    sha256: str
    mode: int


class CommandOutput(NamedTuple):
    stdout: str
    stderr: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MacOsReferenceAppGateError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise MacOsReferenceAppGateError(f"non-finite JSON number {value!r}")


def _strict_json(data: bytes | str, label: str, *, maximum: int) -> Any:
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    if len(raw) > maximum:
        raise MacOsReferenceAppGateError(f"{label} exceeds {maximum} bytes")
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except MacOsReferenceAppGateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MacOsReferenceAppGateError(f"{label} is not strict UTF-8 JSON") from error


def _bounded_path(path: Path, label: str) -> None:
    raw = os.fsencode(str(path))
    if len(raw) == 0 or len(raw) > MAX_PATH_BYTES or b"\x00" in raw:
        raise MacOsReferenceAppGateError(f"{label} is outside the path bound")
    if any(byte < 0x20 for byte in raw):
        raise MacOsReferenceAppGateError(f"{label} contains a control character")


def _regular_file(path: Path, label: str, *, maximum: int | None = None) -> Path:
    _bounded_path(path, label)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise MacOsReferenceAppGateError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise MacOsReferenceAppGateError(f"{label} is not a regular file: {path}")
    if maximum is not None and metadata.st_size > maximum:
        raise MacOsReferenceAppGateError(f"{label} exceeds {maximum} bytes")
    return path


def _directory(path: Path, label: str) -> Path:
    _bounded_path(path, label)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise MacOsReferenceAppGateError(f"missing {label}: {path}") from error
    if not stat.S_ISDIR(mode):
        raise MacOsReferenceAppGateError(
            f"{label} is not a non-symlink directory: {path}"
        )
    return path


def _is_excluded(relative_path: Path) -> bool:
    value = relative_path.as_posix()
    return any(
        value == exclusion or value.startswith(f"{exclusion}/")
        for exclusion in GENERATED_EXCLUSIONS
    )


def _copy_example(source: Path, destination: Path) -> CopySummary:
    """Copy regular source bytes while omitting only known generated paths."""

    source = _directory(source, "committed example source")
    if not destination.is_absolute():
        raise MacOsReferenceAppGateError("reference work directory must be absolute")
    _bounded_path(destination, "reference work directory")
    if destination.exists() or destination.is_symlink():
        raise MacOsReferenceAppGateError(
            "reference work directory must not already exist"
        )
    destination_parent = _directory(
        destination.parent.resolve(strict=True), "reference work parent"
    )
    destination = destination_parent / destination.name
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise MacOsReferenceAppGateError(
            "reference work directory must not be inside the source example"
        )

    included_directories: list[Path] = []
    included_files: list[IncludedFile] = []
    entry_count = 0
    byte_count = 0
    for root, directory_names, file_names in os.walk(
        source, topdown=True, followlinks=False
    ):
        directory_names.sort()
        file_names.sort()
        root_path = Path(root)
        for name in (*directory_names, *file_names):
            entry_count += 1
            if entry_count > MAX_COPY_ENTRIES:
                raise MacOsReferenceAppGateError(
                    "example source exceeds the copy-entry bound"
                )
            entry = root_path / name
            try:
                metadata = entry.lstat()
            except FileNotFoundError as error:
                raise MacOsReferenceAppGateError(
                    f"example source changed during scan: {entry}"
                ) from error
            relative = entry.relative_to(source)
            if stat.S_ISLNK(metadata.st_mode):
                raise MacOsReferenceAppGateError(
                    f"example source contains a symbolic link: {relative.as_posix()}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                if not _is_excluded(relative):
                    included_directories.append(relative)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise MacOsReferenceAppGateError(
                    f"example source contains a special file: {relative.as_posix()}"
                )
            if _is_excluded(relative):
                continue
            if len(included_files) >= MAX_COPY_FILES:
                raise MacOsReferenceAppGateError(
                    "example source exceeds the copied-file bound"
                )
            if metadata.st_size > MAX_COPY_BYTES - byte_count:
                raise MacOsReferenceAppGateError(
                    "example source exceeds the copied-byte bound"
                )
            with entry.open("rb") as stream:
                scanned_size, scanned_digest = _copy_and_hash(
                    stream, None, metadata.st_size
                )
            if scanned_size != metadata.st_size:
                raise MacOsReferenceAppGateError(
                    f"example source changed during scan: {relative.as_posix()}"
                )
            byte_count += metadata.st_size
            included_files.append(
                IncludedFile(
                    path=relative,
                    size_bytes=metadata.st_size,
                    sha256=scanned_digest,
                    mode=stat.S_IMODE(metadata.st_mode),
                )
            )

    destination.mkdir(mode=0o755)
    for relative in sorted(included_directories, key=lambda item: item.as_posix()):
        (destination / relative).mkdir(mode=0o755, parents=True, exist_ok=True)
    for expected in sorted(included_files, key=lambda item: item.path.as_posix()):
        source_file = _regular_file(source / expected.path, "example source file")
        destination_file = destination / expected.path
        destination_file.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(source_file, flags)
            with os.fdopen(descriptor, "rb") as input_stream:
                descriptor = -1
                with destination_file.open("xb") as output_stream:
                    copied_size, copied_digest = _copy_and_hash(
                        input_stream, output_stream, expected.size_bytes
                    )
                    output_stream.flush()
            if (
                copied_size != expected.size_bytes
                or copied_digest != expected.sha256
            ):
                raise MacOsReferenceAppGateError(
                    "example source changed between scan and copy: "
                    f"{expected.path.as_posix()}"
                )
            destination_file.chmod(expected.mode & 0o777)
            _regular_file(destination_file, "copied example file")
        except Exception:
            destination_file.unlink(missing_ok=True)
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return CopySummary(file_count=len(included_files), byte_count=byte_count)


def _patch_fonix_path_dependency(pubspec: Path, repository: Path) -> bool:
    """Replace only the committed `fonix: path: ..` scalar when necessary."""

    pubspec = _regular_file(pubspec, "reference pubspec", maximum=1024 * 1024)
    repository = _directory(repository, "Fonix repository")
    repository_scalar = json.dumps(repository.as_posix(), ensure_ascii=True)
    source = pubspec.read_text(encoding="utf-8")
    if "\r" in source:
        raise MacOsReferenceAppGateError("reference pubspec must use LF newlines")
    relative_block = "  fonix:\n    path: ..\n"
    absolute_block = f"  fonix:\n    path: {repository_scalar}\n"
    relative_count = source.count(relative_block)
    absolute_count = source.count(absolute_block)
    if relative_count == 1 and absolute_count == 0:
        updated = source.replace(relative_block, absolute_block)
        if updated.replace(absolute_block, relative_block) != source:
            raise MacOsReferenceAppGateError(
                "reference pubspec path patch changed unexpected bytes"
            )
        pubspec.write_text(updated, encoding="utf-8", newline="")
        return True
    if relative_count == 0 and absolute_count == 1:
        return False
    raise MacOsReferenceAppGateError(
        "reference pubspec must contain exactly one Fonix path dependency "
        "equal to '..' or the exact repository path"
    )


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MacOsReferenceAppGateError(f"{label} must be a non-empty string")
    if len(value.encode("utf-8")) > MAX_PATH_BYTES or any(
        ord(character) < 0x20 for character in value
    ):
        raise MacOsReferenceAppGateError(f"{label} is outside its text bound")
    return value


def _positive_integer(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MacOsReferenceAppGateError(f"{label} must be an integer")
    if value <= 0 or value > maximum:
        raise MacOsReferenceAppGateError(f"{label} is outside its bound")
    return value


def _load_pinned_archive(lock_path: Path) -> PinnedArchive:
    lock_path = _regular_file(lock_path, "native version lock", maximum=MAX_LOCK_BYTES)
    value = _strict_json(lock_path.read_bytes(), "native version lock", maximum=MAX_LOCK_BYTES)
    if not isinstance(value, dict):
        raise MacOsReferenceAppGateError("native version lock must be an object")
    raw_artifacts = value.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise MacOsReferenceAppGateError("native version lock artifacts must be an array")
    candidates: list[dict[str, Any]] = []
    for raw_artifact in raw_artifacts:
        if not isinstance(raw_artifact, dict):
            raise MacOsReferenceAppGateError("native artifact entry must be an object")
        target = raw_artifact.get("target")
        if not isinstance(target, dict):
            raise MacOsReferenceAppGateError("native artifact target must be an object")
        if (
            target.get("os") == "macos"
            and target.get("architecture") == "arm64"
            and target.get("variant") == "default"
            and raw_artifact.get("flavor") == "cpu"
        ):
            candidates.append(raw_artifact)
    if len(candidates) != 1:
        raise MacOsReferenceAppGateError(
            "native lock must select exactly one macOS arm64 default CPU artifact"
        )
    artifact = candidates[0]
    if artifact.get("id") != ARTIFACT_ID or artifact.get("runtime_mode") != "bundled":
        raise MacOsReferenceAppGateError(
            "selected macOS artifact identity or runtime mode changed"
        )
    target = artifact["target"]
    if target.get("min_os") != APPLICATION_MINIMUM_OS:
        raise MacOsReferenceAppGateError("selected macOS artifact minimum OS changed")
    source = artifact.get("source")
    if not isinstance(source, dict) or source.get("archive") != "tgz":
        raise MacOsReferenceAppGateError("selected macOS artifact source is invalid")
    url = _string(source.get("url"), "selected artifact URL")
    expected_url = (
        "https://github.com/microsoft/onnxruntime/releases/download/"
        f"v{ARTIFACT_VERSION}/onnxruntime-osx-arm64-{ARTIFACT_VERSION}.tgz"
    )
    parsed = urllib.parse.urlsplit(url)
    if (
        url != expected_url
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MacOsReferenceAppGateError("selected macOS artifact URL changed")
    basename = PurePosixPath(parsed.path).name
    if basename != f"onnxruntime-osx-arm64-{ARTIFACT_VERSION}.tgz":
        raise MacOsReferenceAppGateError("selected macOS archive basename changed")
    digest = _string(source.get("sha256"), "selected archive SHA-256")
    if _SHA256.fullmatch(digest) is None:
        raise MacOsReferenceAppGateError(
            "selected archive SHA-256 is not lowercase hexadecimal"
        )
    size = _positive_integer(
        source.get("size_bytes"), "selected archive size", MAX_ARCHIVE_BYTES
    )
    return PinnedArchive(
        artifact_id=ARTIFACT_ID,
        basename=basename,
        sha256=digest,
        size_bytes=size,
    )


def _copy_and_hash(source: Any, destination: Any | None, maximum: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(COPY_CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > maximum:
            raise MacOsReferenceAppGateError("file exceeds its locked size")
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return size, digest.hexdigest()


def _populate_relative_artifact_cache(
    source_cache: Path, work_directory: Path, archive: PinnedArchive
) -> Path:
    source_cache = _directory(source_cache, "supplied artifact cache")
    source = _regular_file(
        source_cache / archive.basename,
        "selected macOS archive",
        maximum=archive.size_bytes,
    )
    if source.stat().st_size != archive.size_bytes:
        raise MacOsReferenceAppGateError("selected macOS archive size differs from lock")
    destination_directory = work_directory / ".fonix-artifact-cache"
    if destination_directory.exists() or destination_directory.is_symlink():
        raise MacOsReferenceAppGateError(
            "relative Fonix artifact cache survived the generated-path exclusion"
        )
    destination_directory.mkdir(mode=0o700)
    destination = destination_directory / archive.basename
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            size, digest = _copy_and_hash(
                input_stream, output_stream, archive.size_bytes
            )
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if size != archive.size_bytes or digest != archive.sha256:
            raise MacOsReferenceAppGateError(
                "selected macOS archive size or SHA-256 differs from lock"
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return _regular_file(destination, "relative cached macOS archive")


def _tool_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment.pop("FONIX_REFERENCE_SMOKE", None)
    environment["DART_SUPPRESS_ANALYTICS"] = "true"
    environment["FLUTTER_SUPPRESS_ANALYTICS"] = "true"
    return environment


def _reference_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = _tool_environment(base)
    for key in tuple(environment):
        if key.startswith("DYLD_"):
            environment.pop(key)
    environment["FONIX_REFERENCE_SMOKE"] = "1"
    return environment


def _bounded_output(value: str, label: str, maximum: int) -> None:
    if len(value.encode("utf-8")) > maximum:
        raise MacOsReferenceAppGateError(f"{label} exceeds {maximum} bytes")


def _diagnostic(value: str, maximum: int = 4096) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="replace") + "\n<truncated>"


def _execute_process(
    command: Sequence[str],
    *,
    cwd: Path | None,
    environment: Mapping[str, str],
    timeout_seconds: int,
    maximum_output: int,
) -> tuple[int, bytes, bytes]:
    """Run one child while bounding each captured stream during execution."""

    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment),
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow: list[str] = []
    reader_errors: list[str] = []
    overflow_lock = threading.Lock()

    def drain(label: str, stream: Any) -> None:
        try:
            while chunk := stream.read(8192):
                buffer = buffers[label]
                remaining = maximum_output - len(buffer)
                if remaining > 0:
                    buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    with overflow_lock:
                        if not overflow:
                            overflow.append(label)
                    try:
                        process.kill()
                    except OSError:
                        pass
        except OSError:
            with overflow_lock:
                reader_errors.append(label)
            try:
                process.kill()
            except OSError:
                pass
        finally:
            stream.close()

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(
            target=drain,
            args=("stdout", process.stdout),
            name="fonix-gate-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=("stderr", process.stderr),
            name="fonix-gate-stderr",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired as error:
            raise MacOsReferenceAppGateError(
                "child process did not exit after termination"
            ) from error
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        raise MacOsReferenceAppGateError(
            "child output stream did not close after process exit"
        )
    if timed_out:
        raise subprocess.TimeoutExpired(list(command), timeout_seconds)
    if reader_errors:
        raise MacOsReferenceAppGateError(
            f"could not read child {reader_errors[0]}"
        )
    if overflow:
        raise MacOsReferenceAppGateError(
            f"child {overflow[0]} exceeds {maximum_output} bytes"
        )
    return return_code, bytes(buffers["stdout"]), bytes(buffers["stderr"])


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
    maximum_output: int = MAX_COMMAND_OUTPUT_BYTES,
    operation: str = "command",
) -> CommandOutput:
    if timeout_seconds <= 0 or timeout_seconds > COMMAND_TIMEOUT_SECONDS:
        raise MacOsReferenceAppGateError(f"{operation} timeout is outside its bound")
    if maximum_output <= 0 or maximum_output > MAX_COMMAND_OUTPUT_BYTES:
        raise MacOsReferenceAppGateError(
            f"{operation} output bound is outside its allowed range"
        )
    try:
        return_code, stdout_bytes, stderr_bytes = _execute_process(
            command,
            cwd=cwd,
            environment=dict(environment or _tool_environment()),
            timeout_seconds=timeout_seconds,
            maximum_output=maximum_output,
        )
    except subprocess.TimeoutExpired as error:
        raise MacOsReferenceAppGateError(
            f"{operation} timed out after {timeout_seconds} seconds"
        ) from error
    except OSError as error:
        raise MacOsReferenceAppGateError(f"could not execute {operation}") from error
    try:
        stdout = stdout_bytes.decode("utf-8")
        stderr = stderr_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MacOsReferenceAppGateError(
            f"{operation} output is not valid UTF-8"
        ) from error
    _bounded_output(stdout, f"{operation} stdout", maximum_output)
    _bounded_output(stderr, f"{operation} stderr", maximum_output)
    if return_code != 0:
        raise MacOsReferenceAppGateError(
            f"{operation} failed with exit code {return_code}"
            f"\nstdout:\n{_diagnostic(stdout)}"
            f"\nstderr:\n{_diagnostic(stderr)}"
        )
    return CommandOutput(stdout=stdout, stderr=stderr)


def _verify_flutter(flutter: Path) -> dict[str, object]:
    output = _run(
        (str(flutter), "--version", "--machine"),
        operation="Flutter version check",
    )
    value = _strict_json(
        output.stdout, "Flutter version output", maximum=MAX_COMMAND_OUTPUT_BYTES
    )
    if not isinstance(value, dict):
        raise MacOsReferenceAppGateError("Flutter version output must be an object")
    if value.get("frameworkRevision") != VALIDATED_FLUTTER_REVISION:
        raise MacOsReferenceAppGateError(
            "Flutter revision differs from the reference-app gate revision"
        )
    framework_version = value.get("frameworkVersion")
    if not isinstance(framework_version, str) or not framework_version:
        raise MacOsReferenceAppGateError("Flutter framework version is missing")
    if len(framework_version.encode("utf-8")) > 128:
        raise MacOsReferenceAppGateError("Flutter framework version is too long")
    return value


def _asset_pair(directory: Path, label: str) -> dict[str, bytes]:
    directory = _directory(directory, label)
    entries = sorted(directory.iterdir(), key=lambda path: path.name)
    expected_entries = set(ASSET_NAMES) | {ANDROID_SIDECAR_DIRECTORY}
    if {entry.name for entry in entries} != expected_entries:
        raise MacOsReferenceAppGateError(
            f"{label} must contain exactly the committed Fonix manifest, notice, "
            "and Android sidecar"
        )
    result: dict[str, bytes] = {}
    for entry in entries:
        if entry.name in ASSET_NAMES:
            result[entry.name] = _regular_file(
                entry, f"{label} {entry.name}", maximum=MAX_ASSET_BYTES
            ).read_bytes()
            continue
        sidecar = _directory(entry, f"{label} Android sidecar")
        sidecar_entries = sorted(sidecar.iterdir(), key=lambda path: path.name)
        if {candidate.name for candidate in sidecar_entries} != ASSET_NAMES:
            raise MacOsReferenceAppGateError(
                f"{label} Android sidecar inventory changed"
            )
        for candidate in sidecar_entries:
            relative = f"{ANDROID_SIDECAR_DIRECTORY}/{candidate.name}"
            result[relative] = _regular_file(
                candidate,
                f"{label} {relative}",
                maximum=MAX_ASSET_BYTES,
            ).read_bytes()
    return result


def _require_asset_equality(expected: Mapping[str, bytes], directory: Path) -> None:
    actual = _asset_pair(directory, "regenerated Fonix asset directory")
    if dict(expected) != actual:
        raise MacOsReferenceAppGateError(
            "regenerated Fonix manifest/notices differ from committed source bytes"
        )


def _validate_reference_receipt(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MacOsReferenceAppGateError("reference receipt must be an object")
    if set(value) != set(EXPECTED_REFERENCE_RECEIPT):
        raise MacOsReferenceAppGateError("reference receipt has an unexpected key set")
    for key, expected in EXPECTED_REFERENCE_RECEIPT.items():
        actual = value[key]
        if type(actual) is not type(expected):
            raise MacOsReferenceAppGateError(
                f"reference receipt field {key!r} has the wrong type"
            )
        if isinstance(expected, list):
            if len(actual) != len(expected):
                raise MacOsReferenceAppGateError(
                    f"reference receipt field {key!r} has the wrong length"
                )
            for index, (actual_item, expected_item) in enumerate(
                zip(actual, expected, strict=True)
            ):
                if type(actual_item) is not type(expected_item) or actual_item != expected_item:
                    raise MacOsReferenceAppGateError(
                        f"reference receipt field {key!r}[{index}] is unexpected"
                    )
        elif actual != expected:
            raise MacOsReferenceAppGateError(
                f"reference receipt field {key!r} is unexpected"
            )
    return {key: value[key] for key in EXPECTED_REFERENCE_RECEIPT}


def _parse_reference_receipt(stdout: str) -> dict[str, object]:
    _bounded_output(stdout, "reference application stdout", MAX_REFERENCE_OUTPUT_BYTES)
    receipts = [
        line[len(REFERENCE_RECEIPT_PREFIX) :]
        for line in stdout.splitlines()
        if line.startswith(REFERENCE_RECEIPT_PREFIX)
    ]
    if len(receipts) != 1:
        raise MacOsReferenceAppGateError(
            "reference application must emit exactly one receipt line"
        )
    value = _strict_json(
        receipts[0], "reference application receipt", maximum=16 * 1024
    )
    return _validate_reference_receipt(value)


def _run_reference_application(
    executable: Path, *, timeout_seconds: int = REFERENCE_TIMEOUT_SECONDS
) -> dict[str, object]:
    executable = _regular_file(executable, "reference application executable")
    if not os.access(executable, os.X_OK):
        raise MacOsReferenceAppGateError(
            "reference application executable is not executable"
        )
    output = _run(
        (str(executable),),
        cwd=executable.parent,
        environment=_reference_environment(),
        timeout_seconds=timeout_seconds,
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        operation="reference application",
    )
    _bounded_output(
        output.stderr, "reference application stderr", MAX_REFERENCE_OUTPUT_BYTES
    )
    if any(
        line.startswith(REFERENCE_RECEIPT_PREFIX)
        for line in output.stderr.splitlines()
    ):
        raise MacOsReferenceAppGateError(
            "reference application emitted a receipt on stderr"
        )
    return _parse_reference_receipt(output.stdout)


def _expected_macho_paths(application: Path, executable: Path) -> list[Path]:
    return [
        executable,
        application
        / "Contents/Frameworks/fonix_shim.framework/Versions/A/fonix_shim",
        application
        / "Contents/Frameworks/onnxruntime.1.framework/Versions/A/onnxruntime.1",
    ]


def _application_executable(application: Path) -> Path:
    plist_path = _regular_file(
        application / "Contents/Info.plist",
        "final reference application Info.plist",
        maximum=1024 * 1024,
    )
    try:
        value = plistlib.loads(plist_path.read_bytes())
    except (plistlib.InvalidFileException, ValueError) as error:
        raise MacOsReferenceAppGateError(
            "final reference application Info.plist is invalid"
        ) from error
    if not isinstance(value, dict):
        raise MacOsReferenceAppGateError(
            "final reference application Info.plist must be a dictionary"
        )
    executable_name = value.get("CFBundleExecutable")
    if executable_name != APPLICATION_EXECUTABLE_NAME:
        raise MacOsReferenceAppGateError(
            "final reference application CFBundleExecutable changed"
        )
    return _regular_file(
        application / "Contents/MacOS" / executable_name,
        "final reference application executable",
    )


def _require_hardened_runtime_codesign(output: str) -> None:
    matches = list(_CODESIGN_FLAGS.finditer(output))
    if len(matches) != 1:
        raise MacOsReferenceAppGateError(
            "codesign output must contain exactly one parenthesized flags field"
        )
    match = matches[0]
    tokens = {
        token
        for token in re.split(r"[\s,]+", match.group("names").strip())
        if token
    }
    bits = int(match.group("bits"), 16)
    if "runtime" not in tokens or bits & 0x10000 == 0:
        raise MacOsReferenceAppGateError(
            "final application executable lacks the hardened-runtime code-sign flag"
        )


def _require_reference_entitlements(output: str) -> None:
    if output.count("<plist") != 1 or output.count("</plist>") != 1:
        raise MacOsReferenceAppGateError(
            "codesign entitlement output must contain exactly one property list"
        )
    xml_count = output.count("<?xml")
    if xml_count > 1:
        raise MacOsReferenceAppGateError(
            "codesign entitlement output has ambiguous XML declarations"
        )
    start = output.find("<?xml") if xml_count == 1 else output.find("<plist")
    closing = "</plist>"
    end = output.find(closing, start)
    if end < 0 or output.find(closing, end + len(closing)) >= 0:
        raise MacOsReferenceAppGateError(
            "codesign entitlement output has an ambiguous property list"
        )
    payload = output[start : end + len(closing)]
    try:
        value = plistlib.loads(payload.encode("utf-8"))
    except (plistlib.InvalidFileException, ValueError) as error:
        raise MacOsReferenceAppGateError(
            "codesign entitlement output is not a valid property list"
        ) from error
    if not isinstance(value, dict):
        raise MacOsReferenceAppGateError(
            "signed reference entitlements must be a dictionary"
        )
    required = (
        "com.apple.security.app-sandbox",
        "com.apple.security.cs.disable-library-validation",
    )
    if any(type(value.get(key)) is not bool or value[key] is not True for key in required):
        raise MacOsReferenceAppGateError(
            "signed reference app must retain sandbox and the explicit local "
            "ad-hoc library-validation exception"
        )


def _audit_hardened_runtime(application: Path, executable: Path) -> None:
    for binary in _expected_macho_paths(application, executable):
        _regular_file(binary, "final signed Mach-O")
        _run(
            ("/usr/bin/codesign", "--verify", "--strict", str(binary)),
            operation="pre-execution code-signature verification",
            maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
        )
    _run(
        ("/usr/bin/codesign", "--verify", "--strict", str(application)),
        operation="pre-execution application-signature verification",
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    output = _run(
        ("/usr/bin/codesign", "--display", "--verbose=4", str(executable)),
        operation="codesign hardened-runtime inspection",
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    _require_hardened_runtime_codesign(f"{output.stdout}\n{output.stderr}")
    entitlements = _run(
        (
            "/usr/bin/codesign",
            "--display",
            "--entitlements",
            ":-",
            str(executable),
        ),
        operation="signed reference entitlement inspection",
        maximum_output=MAX_REFERENCE_OUTPUT_BYTES,
    )
    _require_reference_entitlements(
        f"{entitlements.stdout}\n{entitlements.stderr}"
    )


def _validate_audit_binding(
    value: Any,
    *,
    application: Path,
    executable: Path,
    archive: PinnedArchive,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MacOsReferenceAppGateError(
            "final Apple application audit report must be an object"
        )
    expected_machos = [
        str(path) for path in _expected_macho_paths(application, executable)
    ]
    if (
        value.get("platform") != "macos"
        or value.get("application") != str(application)
        or value.get("artifactId") != archive.artifact_id
        or value.get("cpuInference") != "passed"
        or value.get("machOBinaries") != expected_machos
    ):
        raise MacOsReferenceAppGateError(
            "final Apple application audit did not bind the selected CPU app"
        )
    return value


def _sha256_file(path: Path, label: str) -> str:
    path = _regular_file(path, label, maximum=MAX_ASSET_BYTES)
    with path.open("rb") as stream:
        _, digest = _copy_and_hash(stream, None, MAX_ASSET_BYTES)
    return digest


def run_gate(
    *,
    repository: Path,
    flutter: Path,
    artifact_cache: Path,
    reference_runtime: Path,
    work_directory: Path,
) -> dict[str, object]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise MacOsReferenceAppGateError(
            "the macOS reference-app gate requires an arm64 Mac"
        )
    repository = _directory(repository.resolve(strict=True), "Fonix repository")
    flutter = _regular_file(flutter.resolve(strict=True), "Flutter executable")
    if not os.access(flutter, os.X_OK):
        raise MacOsReferenceAppGateError("Flutter executable is not executable")
    artifact_cache = _directory(
        artifact_cache.resolve(strict=True), "supplied artifact cache"
    )
    reference_runtime = _regular_file(
        reference_runtime.resolve(strict=True), "lock-verified reference runtime"
    )
    if not work_directory.is_absolute():
        raise MacOsReferenceAppGateError("--work-dir must be absolute")
    work_parent = _directory(
        work_directory.parent.resolve(strict=True), "reference work parent"
    )
    work_directory = work_parent / work_directory.name
    if work_directory.exists() or work_directory.is_symlink():
        raise MacOsReferenceAppGateError("--work-dir must not already exist")

    flutter_version = _verify_flutter(flutter)
    dart = _regular_file(
        flutter.parent / "cache/dart-sdk/bin/dart",
        "Flutter-bundled Dart executable",
    )
    if not os.access(dart, os.X_OK):
        raise MacOsReferenceAppGateError("Flutter-bundled Dart is not executable")

    source_example = _directory(repository / "example", "committed example")
    committed_assets = _asset_pair(
        source_example / "assets/fonix", "committed Fonix asset directory"
    )
    copy_summary = _copy_example(source_example, work_directory)
    if copy_summary.file_count == 0:
        raise MacOsReferenceAppGateError("committed example copy was empty")
    _patch_fonix_path_dependency(work_directory / "pubspec.yaml", repository)
    _require_asset_equality(committed_assets, work_directory / "assets/fonix")

    archive = _load_pinned_archive(repository / "native/versions.lock.yaml")
    relative_archive = _populate_relative_artifact_cache(
        artifact_cache, work_directory, archive
    )
    if relative_archive.parent != work_directory / ".fonix-artifact-cache":
        raise MacOsReferenceAppGateError("relative artifact cache escaped the example")

    _run(
        (str(flutter), "pub", "get", "--offline"),
        cwd=work_directory,
        operation="offline Flutter pub get",
    )
    asset_output = work_directory / "assets/fonix"
    _run(
        (
            str(dart),
            "run",
            "fonix:fonix_prepare_flutter_assets",
            "--target-os",
            "macos",
            "--architecture",
            "arm64",
            "--variant",
            "default",
            "--package-root",
            str(repository),
            "--cache",
            str(relative_archive.parent),
            "--output",
            str(asset_output),
        ),
        cwd=work_directory,
        operation="Fonix Flutter asset regeneration",
    )
    _require_asset_equality(committed_assets, asset_output)

    _run(
        (str(flutter), "analyze", "--no-pub"),
        cwd=work_directory,
        operation="reference Flutter analysis",
    )
    _run(
        (str(flutter), "test", "--no-pub"),
        cwd=work_directory,
        operation="reference Flutter tests",
    )
    _run(
        (str(flutter), "build", "macos", "--release", "--no-pub"),
        cwd=work_directory,
        operation="reference Flutter Release build",
    )

    application = _directory(
        work_directory
        / "build/macos/Build/Products/Release"
        / APPLICATION_BUNDLE_NAME,
        "final reference application",
    )
    executable = _application_executable(application)
    _audit_hardened_runtime(application, executable)

    model = work_directory / "assets/models/mul_1.onnx"
    if _sha256_file(model, "reference mul_1 model") != MODEL_SHA256:
        raise MacOsReferenceAppGateError("reference mul_1 model digest changed")
    audit_output = _run(
        (
            sys.executable,
            "-B",
            str(repository / "tool/ci/audit_apple_application.py"),
            "--repository",
            str(repository),
            "--app",
            str(application),
            "--platform",
            "macos",
            "--application-minimum-os",
            APPLICATION_MINIMUM_OS,
            "--reference-runtime",
            str(reference_runtime),
            "--run-cpu-probe",
            str(model),
        ),
        operation="final Apple application audit",
    )
    audit = _strict_json(
        audit_output.stdout,
        "final Apple application audit report",
        maximum=MAX_COMMAND_OUTPUT_BYTES,
    )
    _validate_audit_binding(
        audit,
        application=application,
        executable=executable,
        archive=archive,
    )
    receipt = _run_reference_application(executable)

    report: dict[str, object] = {
        "schemaVersion": 1,
        "result": "passed",
        "application": str(application),
        "applicationMinimumOs": APPLICATION_MINIMUM_OS,
        "artifactId": archive.artifact_id,
        "archiveSha256": archive.sha256,
        "flutterRevision": flutter_version["frameworkRevision"],
        "flutterVersion": flutter_version["frameworkVersion"],
        "assetsReproduced": True,
        "analysis": "passed",
        "tests": "passed",
        "releaseBuild": "passed",
        "hardenedRuntime": "passed",
        "appSandbox": "enabled",
        "libraryValidation": "disabled-local-adhoc-development",
        "referenceReceipt": receipt,
        "finalApplicationAudit": "passed",
        "cpuInference": "passed",
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise MacOsReferenceAppGateError("reference-app gate report exceeds its bound")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--flutter", required=True, type=Path)
    parser.add_argument("--artifact-cache", required=True, type=Path)
    parser.add_argument("--reference-runtime", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = run_gate(
            repository=arguments.repository,
            flutter=arguments.flutter,
            artifact_cache=arguments.artifact_cache,
            reference_runtime=arguments.reference_runtime,
            work_directory=arguments.work_dir,
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        MacOsReferenceAppGateError,
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ) as error:
        print(f"run_macos_reference_app_gate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
