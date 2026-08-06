#!/usr/bin/env python3
"""Audit the exact lock-pinned macOS arm64 ONNX Runtime dylib."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

import fetch_pinned_macos_ort


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
        "@rpath/libonnxruntime.1.dylib",
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
    if install_names != ["@rpath/libonnxruntime.1.dylib"]:
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


def validate_locked_bytes(runtime: Path, lock: Path) -> None:
    try:
        descriptor = os.open(
            runtime,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise MacOsRuntimeError(
            f"runtime is missing, inaccessible, or a symlink: {runtime}"
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MacOsRuntimeError("runtime must be a regular, non-symlink file")
        pinned = fetch_pinned_macos_ort.load_pinned_runtime(lock)
        digest = hashlib.sha256()
        size = 0
        source = os.fdopen(descriptor, "rb")
        descriptor = -1
        with source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > pinned.runtime.size_bytes:
                    raise MacOsRuntimeError("runtime exceeds its locked byte size")
                digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if size != pinned.runtime.size_bytes or digest.hexdigest() != pinned.runtime.sha256:
        raise MacOsRuntimeError("runtime size or SHA-256 differs from the native lock")


def probe_available_providers(runtime: Path, repository: Path) -> None:
    source = repository / "tool" / "ci" / "macos" / "probe_ort_providers.c"
    header_directory = repository / "third_party" / "onnxruntime" / "include"
    if not source.is_file() or not header_directory.is_dir():
        raise MacOsRuntimeError("provider probe source or pinned headers are missing")
    with tempfile.TemporaryDirectory(prefix="fonix-provider-probe-") as temporary:
        executable = Path(temporary) / "probe_ort_providers"
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
        )
        environment = os.environ.copy()
        environment.pop("DYLD_LIBRARY_PATH", None)
        environment.pop("DYLD_FALLBACK_LIBRARY_PATH", None)
        try:
            result = subprocess.run(
                [str(executable)],
                check=True,
                capture_output=True,
                text=True,
                cwd=temporary,
                env=environment,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise MacOsRuntimeError("provider probe execution failed") from error
        validate_provider_probe_output(result.stdout)


def _run(tool: str, *arguments: str) -> str:
    try:
        result = subprocess.run(
            [tool, *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise MacOsRuntimeError(f"{tool} {' '.join(arguments[:-1])} failed") from error
    return result.stdout


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
        validate_locked_bytes(arguments.runtime, arguments.lock)
        runtime = str(arguments.runtime)
        validate_reports(
            headers_report=_run("otool", "-hv", runtime),
            install_name_report=_run("otool", "-D", runtime),
            dependencies_report=_run("otool", "-L", runtime),
            load_commands_report=_run("otool", "-l", runtime),
            exports_report=_run("nm", "-gjU", runtime),
        )
        _run("codesign", "--verify", "--strict", runtime)
        probe_available_providers(
            arguments.runtime.resolve(strict=True),
            arguments.repository.resolve(strict=True),
        )
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
