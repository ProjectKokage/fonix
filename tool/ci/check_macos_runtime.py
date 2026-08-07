#!/usr/bin/env python3
"""Audit the exact lock-pinned macOS arm64 ONNX Runtime dylib."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile

from bounded_process import BoundedProcessError, CommandOutput, run_bounded
import fetch_pinned_macos_ort


MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
INSPECTION_TIMEOUT_SECONDS = 2 * 60
COMPILE_TIMEOUT_SECONDS = 5 * 60
PROBE_TIMEOUT_SECONDS = 2 * 60
COPY_CHUNK_BYTES = 1024 * 1024
PINNED_INSTALL_NAME = "@rpath/libonnxruntime.1.dylib"
PINNED_INSTALL_BASENAME = PurePosixPath(PINNED_INSTALL_NAME).name
MACOS_COMMAND_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
}
TRUSTED_SYSTEM_TOOLS = {
    "cc": Path("/usr/bin/cc"),
    "codesign": Path("/usr/bin/codesign"),
    "nm": Path("/usr/bin/nm"),
    "otool": Path("/usr/bin/otool"),
}
PINNED_EXPORTS = frozenset(
    {
        "_OrtGetApiBase",
        "_OrtSessionOptionsAppendExecutionProvider_CPU",
        "_OrtSessionOptionsAppendExecutionProvider_CoreML",
    }
)

PINNED_AVAILABLE_PROVIDERS = frozenset(
    {
        "CPUExecutionProvider",
        "CoreMLExecutionProvider",
        "WebGpuExecutionProvider",
    }
)

PINNED_DEPENDENCIES = frozenset(
    {
        PINNED_INSTALL_NAME,
        "/System/Library/Frameworks/CoreML.framework/Versions/A/CoreML",
        "/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa",
        "/System/Library/Frameworks/IOKit.framework/Versions/A/IOKit",
        "/System/Library/Frameworks/QuartzCore.framework/Versions/A/QuartzCore",
        "/System/Library/Frameworks/Metal.framework/Versions/A/Metal",
        "/System/Library/Frameworks/IOSurface.framework/Versions/A/IOSurface",
        "/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation",
        "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation",
        "/usr/lib/libiconv.2.dylib",
        "/usr/lib/libc++.1.dylib",
        "/usr/lib/libSystem.B.dylib",
        "/usr/lib/libobjc.A.dylib",
    }
)

LOAD_PATH = re.compile(r"^\s*(\S+)\s+\(compatibility version\s+[^)]+\)\s*$")
RPATH = re.compile(
    r"(?ms)^\s*cmd LC_RPATH\s*$.*?^\s*path (\S+) \(offset \d+\)\s*$"
)
BUILD_VERSION = re.compile(
    r"(?ms)^\s*cmd LC_BUILD_VERSION\s*$"
    r".*?^\s*platform\s+(\d+)\s*$"
    r".*?^\s*minos\s+(\S+)\s*$"
    r".*?^\s*sdk\s+(\S+)\s*$"
)


class MacOsRuntimeError(RuntimeError):
    """The dylib does not match the checked-in macOS runtime contract."""


def _macos_command_environment(
    temporary_directory: Path | None = None,
) -> dict[str, str]:
    environment = dict(MACOS_COMMAND_ENVIRONMENT)
    if temporary_directory is not None:
        metadata = temporary_directory.lstat()
        if (
            not temporary_directory.is_absolute()
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise MacOsRuntimeError("command temporary directory must be private")
        environment["TMPDIR"] = str(temporary_directory)
    return environment


def _trusted_system_tool(name: str) -> str:
    path = TRUSTED_SYSTEM_TOOLS.get(name)
    if path is None:
        raise MacOsRuntimeError(f"unsupported macOS system tool: {name}")
    if not path.is_absolute():
        raise MacOsRuntimeError(f"macOS system tool path is not absolute: {name}")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MacOsRuntimeError(f"macOS system tool is unavailable: {path}") from error
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise MacOsRuntimeError(
            f"macOS system tool is not a regular executable: {path}"
        )
    return str(path)


def parse_otool_paths(source: str) -> frozenset[str]:
    paths = [
        match.group(1)
        for line in source.splitlines()
        if (match := LOAD_PATH.fullmatch(line)) is not None
    ]
    if not paths or len(paths) != len(set(paths)):
        raise MacOsRuntimeError("otool dependency paths were empty or duplicated")
    return frozenset(paths)


def parse_nm_exports(source: str) -> frozenset[str]:
    exports = [line.strip() for line in source.splitlines() if line.strip()]
    if not exports or len(exports) != len(set(exports)):
        raise MacOsRuntimeError("nm exports were empty or duplicated")
    if any(re.fullmatch(r"_[A-Za-z][A-Za-z0-9_]*", value) is None for value in exports):
        raise MacOsRuntimeError("nm returned an unsupported export spelling")
    return frozenset(exports)


def validate_provider_probe_output(source: str) -> None:
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if not lines or lines[0] != "runtimeVersion=1.27.1":
        raise MacOsRuntimeError("provider probe did not report runtime 1.27.1")
    providers = lines[1:]
    if len(providers) != len(set(providers)):
        raise MacOsRuntimeError("provider probe returned duplicate provider names")
    actual = frozenset(providers)
    if actual != PINNED_AVAILABLE_PROVIDERS:
        missing = PINNED_AVAILABLE_PROVIDERS - actual
        unexpected = actual - PINNED_AVAILABLE_PROVIDERS
        details: list[str] = []
        if missing:
            details.append("missing providers: " + ", ".join(sorted(missing)))
        if unexpected:
            details.append("unexpected providers: " + ", ".join(sorted(unexpected)))
        raise MacOsRuntimeError("; ".join(details))


def validate_reports(
    *,
    headers_report: str,
    install_name_report: str,
    dependencies_report: str,
    load_commands_report: str,
    exports_report: str,
) -> None:
    if headers_report.count("Mach header") != 1:
        raise MacOsRuntimeError("runtime must be one thin Mach-O image")
    header_lines = [
        line
        for line in headers_report.splitlines()
        if "ARM64" in line or "X86_64" in line
    ]
    if len(header_lines) != 1 or "ARM64" not in header_lines[0]:
        raise MacOsRuntimeError("runtime must be a thin arm64 Mach-O image")
    if re.search(r"\bDYLIB\b", header_lines[0]) is None:
        raise MacOsRuntimeError("runtime Mach-O image must have DYLIB file type")

    install_names = [
        line.strip()
        for line in install_name_report.splitlines()
        if line.strip() and not line.rstrip().endswith(":")
    ]
    if install_names != [PINNED_INSTALL_NAME]:
        raise MacOsRuntimeError("runtime install name is not the pinned @rpath name")

    dependencies = parse_otool_paths(dependencies_report)
    if dependencies != PINNED_DEPENDENCIES:
        missing = PINNED_DEPENDENCIES - dependencies
        unexpected = dependencies - PINNED_DEPENDENCIES
        details: list[str] = []
        if missing:
            details.append("missing dependencies: " + ", ".join(sorted(missing)))
        if unexpected:
            details.append(
                "unexpected dependencies: " + ", ".join(sorted(unexpected))
            )
        raise MacOsRuntimeError("; ".join(details))
    if any(
        path.startswith(("/usr/local/", "/opt/", "/Library/"))
        for path in dependencies
    ):
        raise MacOsRuntimeError("runtime depends on a non-system global path")

    build_versions = BUILD_VERSION.findall(load_commands_report)
    if build_versions != [("1", "14.0", "26.2")]:
        raise MacOsRuntimeError(
            "runtime must declare exactly macOS platform 1, minOS 14.0, SDK 26.2"
        )
    rpaths = RPATH.findall(load_commands_report)
    if rpaths != ["@loader_path"]:
        raise MacOsRuntimeError("runtime must declare only @loader_path as LC_RPATH")

    exports = parse_nm_exports(exports_report)
    if exports != PINNED_EXPORTS:
        missing = PINNED_EXPORTS - exports
        unexpected = exports - PINNED_EXPORTS
        details = []
        if missing:
            details.append("missing exports: " + ", ".join(sorted(missing)))
        if unexpected:
            details.append("unexpected exports: " + ", ".join(sorted(unexpected)))
        raise MacOsRuntimeError("; ".join(details))


def _open_regular_file_for_read(path: Path, label: str) -> int:
    try:
        before = path.lstat()
    except OSError as error:
        raise MacOsRuntimeError(
            f"{label} is missing, inaccessible, or a symlink: {path}"
        ) from error
    if not stat.S_ISREG(before.st_mode):
        raise MacOsRuntimeError(f"{label} must be a regular, non-symlink file")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise MacOsRuntimeError(
            f"{label} is missing, inaccessible, or a symlink: {path}"
        ) from error
    try:
        opened = os.fstat(descriptor)
        after = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(after.st_mode)
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after)
        ):
            raise MacOsRuntimeError(
                f"{label} changed identity or became non-regular while opening"
            )
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _copy_and_hash_descriptor(
    source_descriptor: int,
    *,
    maximum_bytes: int,
    destination_descriptor: int | None = None,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(source_descriptor, COPY_CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > maximum_bytes:
            raise MacOsRuntimeError("runtime exceeds its locked byte size")
        digest.update(chunk)
        if destination_descriptor is not None:
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(destination_descriptor, remaining)
                if written <= 0:
                    raise MacOsRuntimeError("could not copy the locked runtime")
                remaining = remaining[written:]
    return size, digest.hexdigest()


def _verify_locked_regular_file(
    path: Path,
    expected: fetch_pinned_macos_ort.ExpectedFile,
    *,
    label: str,
    require_single_link: bool = False,
) -> None:
    descriptor = _open_regular_file_for_read(path, label)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_size != expected.size_bytes:
            raise MacOsRuntimeError(f"{label} size differs from the native lock")
        if require_single_link and metadata.st_nlink != 1:
            raise MacOsRuntimeError(f"{label} must have exactly one hard link")
        size, digest = _copy_and_hash_descriptor(
            descriptor,
            maximum_bytes=expected.size_bytes,
        )
    finally:
        os.close(descriptor)
    if size != expected.size_bytes or digest != expected.sha256:
        raise MacOsRuntimeError(
            f"{label} size or SHA-256 differs from the native lock"
        )


def _load_pinned_runtime(lock: Path) -> fetch_pinned_macos_ort.PinnedRuntime:
    try:
        return fetch_pinned_macos_ort.load_pinned_runtime(lock)
    except fetch_pinned_macos_ort.ArtifactVerificationError as error:
        raise MacOsRuntimeError(f"native runtime lock is invalid: {error}") from error


def _stage_locked_runtime_snapshot(
    runtime: Path,
    snapshot_directory: Path,
    expected: fetch_pinned_macos_ort.ExpectedFile,
) -> Path:
    directory_metadata = snapshot_directory.lstat()
    if (
        not stat.S_ISDIR(directory_metadata.st_mode)
        or stat.S_IMODE(directory_metadata.st_mode) & 0o077
    ):
        raise MacOsRuntimeError("runtime snapshot directory must be private")
    if PINNED_INSTALL_BASENAME != "libonnxruntime.1.dylib":
        raise MacOsRuntimeError("pinned runtime install-name basename changed")

    destination = snapshot_directory / PINNED_INSTALL_BASENAME
    source_descriptor = _open_regular_file_for_read(runtime, "runtime")
    destination_descriptor = -1
    try:
        source_metadata = os.fstat(source_descriptor)
        if source_metadata.st_size != expected.size_bytes:
            raise MacOsRuntimeError("runtime size differs from the native lock")
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        destination_flags |= getattr(os, "O_CLOEXEC", 0)
        destination_flags |= getattr(os, "O_NOFOLLOW", 0)
        destination_descriptor = os.open(
            destination,
            destination_flags,
            0o600,
        )
        size, digest = _copy_and_hash_descriptor(
            source_descriptor,
            maximum_bytes=expected.size_bytes,
            destination_descriptor=destination_descriptor,
        )
        if size != expected.size_bytes or digest != expected.sha256:
            raise MacOsRuntimeError(
                "runtime size or SHA-256 differs from the native lock"
            )
        os.fsync(destination_descriptor)
        os.fchmod(destination_descriptor, 0o500)
        destination_metadata = os.fstat(destination_descriptor)
        if (
            not stat.S_ISREG(destination_metadata.st_mode)
            or destination_metadata.st_nlink != 1
            or destination_metadata.st_size != expected.size_bytes
        ):
            raise MacOsRuntimeError(
                "private runtime snapshot is not one exact regular file"
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        os.close(source_descriptor)

    try:
        _verify_locked_regular_file(
            destination,
            expected,
            label="private runtime snapshot",
            require_single_link=True,
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def probe_available_providers(
    runtime: Path,
    repository: Path,
) -> None:
    source = repository / "tool" / "ci" / "macos" / "probe_ort_providers.c"
    header_directory = repository / "third_party" / "onnxruntime" / "include"
    if not source.is_file() or not header_directory.is_dir():
        raise MacOsRuntimeError("provider probe source or pinned headers are missing")
    if runtime.name != PINNED_INSTALL_BASENAME:
        raise MacOsRuntimeError("provider probe runtime lacks the pinned LC_ID name")
    with tempfile.TemporaryDirectory(prefix="fonix-provider-probe-") as temporary:
        probe_directory = Path(temporary)
        executable = probe_directory / "probe_ort_providers"
        environment = _macos_command_environment(probe_directory)
        _run(
            "cc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Werror",
            f"-I{header_directory}",
            str(source),
            str(runtime),
            f"-Wl,-rpath,{runtime.parent}",
            "-o",
            str(executable),
            operation="macOS provider probe compilation",
            timeout_seconds=COMPILE_TIMEOUT_SECONDS,
            environment=environment,
        )
        output = _run_command(
            [str(executable)],
            operation="macOS provider probe execution",
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
            cwd=probe_directory,
            environment=environment,
        )
        validate_provider_probe_output(output)


def _run_command(
    command: list[str],
    *,
    operation: str,
    timeout_seconds: int,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    try:
        result = run_bounded(
            command,
            operation=operation,
            cwd=cwd,
            environment=(
                _macos_command_environment()
                if environment is None
                else environment
            ),
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=MAX_COMMAND_OUTPUT_BYTES,
            maximum_stderr_bytes=MAX_COMMAND_OUTPUT_BYTES,
        )
    except BoundedProcessError as error:
        raise MacOsRuntimeError(f"bounded command failed: {error}") from error
    return result.stdout


def _run(
    tool: str,
    *arguments: str,
    operation: str | None = None,
    timeout_seconds: int = INSPECTION_TIMEOUT_SECONDS,
    environment: dict[str, str] | None = None,
) -> str:
    executable = _trusted_system_tool(tool)
    return _run_command(
        [executable, *arguments],
        operation=operation or f"{tool} {' '.join(arguments[:-1])}".strip(),
        timeout_seconds=timeout_seconds,
        environment=environment,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        pinned = _load_pinned_runtime(arguments.lock)
        repository = arguments.repository.resolve(strict=True)
        with tempfile.TemporaryDirectory(
            prefix="fonix-runtime-snapshot-"
        ) as temporary:
            snapshot = _stage_locked_runtime_snapshot(
                arguments.runtime,
                Path(temporary),
                pinned.runtime,
            )
            runtime = str(snapshot)
            validate_reports(
                headers_report=_run("otool", "-hv", runtime),
                install_name_report=_run("otool", "-D", runtime),
                dependencies_report=_run("otool", "-L", runtime),
                load_commands_report=_run("otool", "-l", runtime),
                exports_report=_run("nm", "-gjU", runtime),
            )
            _run("codesign", "--verify", "--strict", runtime)
            probe_available_providers(snapshot, repository)
    except (OSError, MacOsRuntimeError) as error:
        print(f"macOS runtime verification failed: {error}", file=sys.stderr)
        return 1
    print(
        "Pinned macOS arm64 ORT dylib matches its bytes, Mach-O layout, "
        "dependencies, exports, provider inventory, and embedded signature."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
