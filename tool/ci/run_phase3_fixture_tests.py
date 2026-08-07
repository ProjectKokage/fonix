#!/usr/bin/env python3
"""Verify Phase-3 fixture bytes and run the standalone real-ORT harness."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Sequence

from bounded_process import BoundedProcessError, CommandOutput, run_bounded


BUILD_CONFIGURATION = "RelWithDebInfo"
PINNED_ORT_VERSION = "1.27.1"
BYTE_CHECK_TIMEOUT_SECONDS = 2 * 60
CONFIGURE_TIMEOUT_SECONDS = 5 * 60
BUILD_TIMEOUT_SECONDS = 20 * 60
INVENTORY_TIMEOUT_SECONDS = 2 * 60
CTEST_SUITE_TIMEOUT_SECONDS = 30 * 60
CTEST_TEST_TIMEOUT_SECONDS = 5 * 60
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_INVENTORY_OUTPUT_BYTES = 4 * 1024 * 1024
REQUIRED_REAL_RUNTIME_TESTS = frozenset(
    {
        "phase3_fixture_bytes",
        "phase3_fixture_real_ort",
    }
)


class Phase3FixtureError(RuntimeError):
    """The deterministic corpus or its standalone harness is invalid."""


def parse_ctest_inventory(source: str) -> frozenset[str]:
    try:
        value = json.loads(source)
    except json.JSONDecodeError as error:
        raise Phase3FixtureError("CTest inventory was not valid JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("tests"), list):
        raise Phase3FixtureError("CTest JSON inventory has no tests array")

    names: list[str] = []
    for index, test in enumerate(value["tests"]):
        if not isinstance(test, dict) or not isinstance(test.get("name"), str):
            raise Phase3FixtureError(f"CTest entry {index} has no string name")
        name = test["name"]
        if not name or any(ord(character) < 0x20 for character in name):
            raise Phase3FixtureError(f"CTest entry {index} has an invalid name")
        names.append(name)
    if len(names) != len(set(names)):
        raise Phase3FixtureError("CTest inventory contains duplicate test names")
    return frozenset(names)


def validate_real_runtime_tests(discovered: frozenset[str]) -> None:
    if not discovered:
        raise Phase3FixtureError(
            "the Phase-3 harness configured zero tests; "
            "an empty CTest pass is forbidden"
        )
    missing = REQUIRED_REAL_RUNTIME_TESTS - discovered
    if missing:
        raise Phase3FixtureError(
            "the Phase-3 harness is missing required tests: "
            + ", ".join(sorted(missing))
        )


def validate_expected_ort_version(version: str) -> None:
    if version != PINNED_ORT_VERSION:
        raise Phase3FixtureError(
            f"the fixture harness must use pinned ORT {PINNED_ORT_VERSION}, "
            f"got {version}"
        )


def _ctest_run_command(ctest: str, build_directory: Path) -> list[str]:
    return [
        ctest,
        "--test-dir",
        str(build_directory),
        "--build-config",
        BUILD_CONFIGURATION,
        "--output-on-failure",
        "--no-tests=error",
        "--timeout",
        str(CTEST_TEST_TIMEOUT_SECONDS),
    ]


def _emit_output(output: CommandOutput) -> None:
    if output.stdout:
        print(output.stdout, end="" if output.stdout.endswith("\n") else "\n")
    if output.stderr:
        print(
            output.stderr,
            end="" if output.stderr.endswith("\n") else "\n",
            file=sys.stderr,
        )


def _run(
    command: Sequence[str],
    *,
    operation: str,
    cwd: Path,
    timeout_seconds: int,
) -> None:
    print("+ " + " ".join(command), flush=True)
    if os.name == "posix":
        try:
            output = run_bounded(
                command,
                operation=operation,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                maximum_stdout_bytes=MAX_COMMAND_OUTPUT_BYTES,
                maximum_stderr_bytes=MAX_COMMAND_OUTPUT_BYTES,
            )
        except BoundedProcessError as error:
            raise Phase3FixtureError(str(error)) from error
        _emit_output(output)
        return

    # Keep the cross-platform deterministic byte check available on Windows,
    # whose inherited-subprocess boundary remains deferred and unclaimed.
    try:
        subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise Phase3FixtureError(
            f"{operation} exceeded its {timeout_seconds}-second direct-child "
            "deadline on the deferred Windows lane"
        ) from error
    except subprocess.CalledProcessError as error:
        raise Phase3FixtureError(
            f"{operation} failed with exit code {error.returncode}: {command[0]}"
        ) from error


def _inventory(ctest: str, build_directory: Path, *, cwd: Path) -> frozenset[str]:
    command = [
        ctest,
        "--test-dir",
        str(build_directory),
        "--build-config",
        BUILD_CONFIGURATION,
        "--show-only=json-v1",
    ]
    print("+ " + " ".join(command), flush=True)
    if os.name == "posix":
        try:
            result = run_bounded(
                command,
                operation="Phase-3 CTest inventory",
                cwd=cwd,
                timeout_seconds=INVENTORY_TIMEOUT_SECONDS,
                maximum_stdout_bytes=MAX_INVENTORY_OUTPUT_BYTES,
                maximum_stderr_bytes=MAX_INVENTORY_OUTPUT_BYTES,
            )
        except BoundedProcessError as error:
            raise Phase3FixtureError(str(error)) from error
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return parse_ctest_inventory(result.stdout)

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=INVENTORY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise Phase3FixtureError(
            "Phase-3 CTest inventory exceeded its direct-child deadline on "
            "the deferred Windows lane"
        ) from error
    except subprocess.CalledProcessError as error:
        if error.stdout:
            diagnostic = error.stdout[:4096].decode("utf-8", errors="replace")
            print(diagnostic, file=sys.stderr)
        if error.stderr:
            diagnostic = error.stderr[:4096].decode("utf-8", errors="replace")
            print(diagnostic, file=sys.stderr)
        raise Phase3FixtureError(
            "could not enumerate the standalone Phase-3 CTests"
        ) from error
    if (
        len(result.stdout) > MAX_INVENTORY_OUTPUT_BYTES
        or len(result.stderr) > MAX_INVENTORY_OUTPUT_BYTES
    ):
        raise Phase3FixtureError(
            "Phase-3 CTest inventory output exceeds its deferred Windows bound"
        )
    try:
        stdout = result.stdout.decode("utf-8")
        stderr = result.stderr.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Phase3FixtureError(
            "Phase-3 CTest inventory output is not UTF-8"
        ) from error
    if stderr:
        print(stderr, file=sys.stderr, end="")
    return parse_ctest_inventory(stdout)


def _regular_build_directory(path: Path) -> Path:
    if path.is_symlink():
        raise Phase3FixtureError(f"build directory must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise Phase3FixtureError(f"build path is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


def _regular_input(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise Phase3FixtureError(f"{label} is missing: {path}") from error
    if not stat.S_ISREG(mode):
        raise Phase3FixtureError(f"{label} must be a regular file: {path}")
    return path.resolve(strict=True)


def run_byte_check(repository: Path) -> None:
    repository = repository.resolve(strict=True)
    generator = _regular_input(
        repository / "test/fixtures/generate_phase3_fixtures.py",
        "Phase-3 fixture generator",
    )
    _run(
        [sys.executable, "-B", str(generator), "--check"],
        operation="Phase-3 fixture byte check",
        cwd=repository,
        timeout_seconds=BYTE_CHECK_TIMEOUT_SECONDS,
    )


def run_real_runtime_harness(
    *,
    repository: Path,
    build_directory: Path,
    real_ort: Path,
    expected_ort_version: str,
) -> None:
    validate_expected_ort_version(expected_ort_version)
    repository = repository.resolve(strict=True)
    source_directory = repository / "test/fixtures"
    if not source_directory.is_dir():
        raise Phase3FixtureError(
            f"standalone Phase-3 fixture source is missing: {source_directory}"
        )
    resolved_build_directory = _regular_build_directory(build_directory)
    resolved_ort = _regular_input(real_ort, "real ORT library")
    cmake = shutil.which("cmake")
    ctest = shutil.which("ctest")
    if cmake is None or ctest is None:
        raise Phase3FixtureError("both cmake and ctest must be available on PATH")

    _run(
        [
            cmake,
            "-S",
            str(source_directory),
            "-B",
            str(resolved_build_directory),
            f"-DCMAKE_BUILD_TYPE={BUILD_CONFIGURATION}",
            f"-DFONIX_REAL_ORT_LIBRARY={resolved_ort}",
            f"-DFONIX_EXPECTED_ORT_VERSION={expected_ort_version}",
        ],
        operation="Phase-3 CMake configure",
        cwd=repository,
        timeout_seconds=CONFIGURE_TIMEOUT_SECONDS,
    )
    _run(
        [
            cmake,
            "--build",
            str(resolved_build_directory),
            "--config",
            BUILD_CONFIGURATION,
            "--parallel",
        ],
        operation="Phase-3 CMake build",
        cwd=repository,
        timeout_seconds=BUILD_TIMEOUT_SECONDS,
    )

    discovered = _inventory(
        ctest,
        resolved_build_directory,
        cwd=repository,
    )
    validate_real_runtime_tests(discovered)
    _run(
        _ctest_run_command(ctest, resolved_build_directory),
        operation="Phase-3 CTest suite",
        cwd=repository,
        timeout_seconds=CTEST_SUITE_TIMEOUT_SECONDS,
    )
    print(
        f"Ran {len(discovered)} non-empty standalone Phase-3 CTests "
        f"with ONNX Runtime {expected_ort_version}.",
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="compare every committed generated byte")
    real = commands.add_parser(
        "real-runtime",
        help="build and run the standalone real-ORT CMake harness",
    )
    real.add_argument("--build-dir", type=Path, required=True)
    real.add_argument("--real-ort", type=Path, required=True)
    real.add_argument("--expected-ort-version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "check":
            run_byte_check(arguments.repository)
        else:
            run_real_runtime_harness(
                repository=arguments.repository,
                build_directory=arguments.build_dir,
                real_ort=arguments.real_ort,
                expected_ort_version=arguments.expected_ort_version,
            )
    except (Phase3FixtureError, OSError) as error:
        print(f"Phase-3 fixture verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
