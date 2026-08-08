#!/usr/bin/env python3
"""Configure, build, enumerate, and run the honest host native test suite."""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Mapping, Sequence

from bounded_process import BoundedProcessError, CommandOutput, run_bounded


BUILD_CONFIGURATION = "RelWithDebInfo"
CONFIGURE_TIMEOUT_SECONDS = 5 * 60
BUILD_TIMEOUT_SECONDS = 20 * 60
INVENTORY_TIMEOUT_SECONDS = 2 * 60
CTEST_SUITE_TIMEOUT_SECONDS = 30 * 60
CTEST_TEST_TIMEOUT_SECONDS = 5 * 60
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_INVENTORY_OUTPUT_BYTES = 4 * 1024 * 1024
REQUIRED_TESTS: Mapping[str, frozenset[str]] = {
    "posix": frozenset(
        {
            "header-compiles-as-c",
            "header-compiles-as-cpp",
            "owned-string-and-status-limits",
            "runtime-loader-and-abi",
            "phase2-native-guards",
            "phase2-shared-value-thread-safety",
            "phase5-cancel-registry",
            "phase5-cancel-during-run",
            "shim-allocation-fault-injection",
            "exported-symbol-allowlist",
            "external-shim-has-no-ort-link",
        }
    ),
    "windows-contract": frozenset(
        {
            "windows-shim-contract",
            "windows-binary-contract",
            "windows-path-security",
            "windows-profile-security",
        }
    ),
    "bundled": frozenset({"bundled-runtime-adjacent"}),
}


class NativeTestError(RuntimeError):
    """The selected native suite is unsupported, empty, or failed."""


def validate_suite_for_host(suite: str, system: str) -> None:
    if suite == "posix" and system not in {"Darwin", "Linux"}:
        raise NativeTestError(
            f"the full POSIX loader suite is unsupported on {system}; "
            "use windows-contract on Windows"
        )
    if suite == "windows-contract" and system != "Windows":
        raise NativeTestError(
            f"the Windows shim contract suite cannot run on {system}"
        )
    if suite == "bundled" and system not in {"Darwin", "Linux", "Windows"}:
        raise NativeTestError(
            f"the adjacent bundled-runtime suite is unsupported on {system}"
        )


def parse_ctest_inventory(source: str) -> frozenset[str]:
    try:
        value = json.loads(source)
    except json.JSONDecodeError as error:
        raise NativeTestError("CTest inventory was not valid JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("tests"), list):
        raise NativeTestError("CTest JSON inventory has no tests array")
    names: list[str] = []
    for index, test in enumerate(value["tests"]):
        if not isinstance(test, dict) or not isinstance(test.get("name"), str):
            raise NativeTestError(f"CTest entry {index} has no string name")
        name = test["name"]
        if not name or any(ord(character) < 0x20 for character in name):
            raise NativeTestError(f"CTest entry {index} has an invalid name")
        names.append(name)
    if len(names) != len(set(names)):
        raise NativeTestError("CTest inventory contains duplicate test names")
    return frozenset(names)


def validate_discovered_tests(
    suite: str,
    discovered: frozenset[str],
    *,
    require_real_ort: bool = False,
) -> None:
    if not discovered:
        raise NativeTestError(
            f"{suite} configured zero native tests; an empty CTest pass is forbidden"
        )
    missing = REQUIRED_TESTS[suite] - discovered
    if require_real_ort and "phase2-real-ort-cpu" not in discovered:
        missing = missing | {"phase2-real-ort-cpu"}
    if missing:
        raise NativeTestError(
            f"{suite} is missing required native tests: {', '.join(sorted(missing))}"
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
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> None:
    print("+ " + " ".join(command), flush=True)
    if os.name == "posix":
        try:
            output = run_bounded(
                command,
                operation=operation,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                maximum_stdout_bytes=MAX_COMMAND_OUTPUT_BYTES,
                maximum_stderr_bytes=MAX_COMMAND_OUTPUT_BYTES,
            )
        except BoundedProcessError as error:
            raise NativeTestError(str(error)) from error
        _emit_output(output)
        return

    # Windows target-host hardening is deferred. Keep the required source and
    # loader-security lane working with a direct-child deadline, but do not
    # claim inherited-subprocess ownership until a Windows job-object
    # implementation is exercised on a Windows host.
    try:
        subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise NativeTestError(
            f"{operation} exceeded its {timeout_seconds}-second direct-child "
            "deadline on the deferred Windows lane"
        ) from error
    except subprocess.CalledProcessError as error:
        raise NativeTestError(
            f"{operation} failed with exit code {error.returncode}: {command[0]}"
        ) from error


def _inventory(
    ctest: str,
    build_directory: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> frozenset[str]:
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
                operation="native CTest inventory",
                cwd=cwd,
                environment=environment,
                timeout_seconds=INVENTORY_TIMEOUT_SECONDS,
                maximum_stdout_bytes=MAX_INVENTORY_OUTPUT_BYTES,
                maximum_stderr_bytes=MAX_INVENTORY_OUTPUT_BYTES,
            )
        except BoundedProcessError as error:
            raise NativeTestError(str(error)) from error
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return parse_ctest_inventory(result.stdout)

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=True,
            capture_output=True,
            timeout=INVENTORY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise NativeTestError(
            "native CTest inventory exceeded its direct-child deadline on "
            "the deferred Windows lane"
        ) from error
    except subprocess.CalledProcessError as error:
        if error.stdout:
            diagnostic = error.stdout[:4096].decode("utf-8", errors="replace")
            print(diagnostic, file=sys.stderr)
        if error.stderr:
            diagnostic = error.stderr[:4096].decode("utf-8", errors="replace")
            print(diagnostic, file=sys.stderr)
        raise NativeTestError("could not enumerate configured CTests") from error
    if (
        len(result.stdout) > MAX_INVENTORY_OUTPUT_BYTES
        or len(result.stderr) > MAX_INVENTORY_OUTPUT_BYTES
    ):
        raise NativeTestError(
            "native CTest inventory output exceeds its deferred Windows bound"
        )
    try:
        stdout = result.stdout.decode("utf-8")
        stderr = result.stderr.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NativeTestError("native CTest inventory output is not UTF-8") from error
    if stderr:
        print(stderr, file=sys.stderr, end="")
    return parse_ctest_inventory(stdout)


def _regular_build_directory(path: Path) -> None:
    if path.is_symlink():
        raise NativeTestError(f"build directory must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise NativeTestError(f"build path is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _regular_input(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise NativeTestError(f"{label} is missing: {path}") from error
    if not stat.S_ISREG(mode):
        raise NativeTestError(f"{label} must be a regular file: {path}")
    return resolved


def sanitizer_environment(
    system: str, base: Mapping[str, str]
) -> dict[str, str]:
    environment = dict(base)
    if system == "Linux":
        environment.setdefault("ASAN_OPTIONS", "detect_leaks=1:halt_on_error=1")
    elif system == "Darwin":
        # AppleClang's AddressSanitizer rejects detect_leaks at startup.
        environment.setdefault("ASAN_OPTIONS", "halt_on_error=1")
    else:
        raise NativeTestError(f"sanitizer environment is unsupported on {system}")
    environment.setdefault("UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1")
    return environment


def thread_sanitizer_environment(
    system: str, base: Mapping[str, str]
) -> dict[str, str]:
    if system not in {"Darwin", "Linux"}:
        raise NativeTestError(
            f"ThreadSanitizer environment is unsupported on {system}"
        )
    environment = dict(base)
    environment["TSAN_OPTIONS"] = "halt_on_error=1"
    return environment


def _find_built_shim(build_directory: Path, system: str) -> Path:
    library_name = {
        "Darwin": "libfonix_shim.dylib",
        "Linux": "libfonix_shim.so",
        "Windows": "fonix_shim.dll",
    }.get(system)
    if library_name is None:
        raise NativeTestError(f"cannot identify a shim binary on {system}")
    candidates = []
    for candidate in build_directory.rglob(library_name):
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISREG(mode):
            candidates.append(candidate.resolve(strict=True))
    unique = sorted(set(candidates))
    if len(unique) != 1:
        raise NativeTestError(
            f"expected exactly one built {library_name}; found {len(unique)}"
        )
    return unique[0]


def run_native_tests(
    *,
    repository: Path,
    build_directory: Path,
    suite: str,
    sanitizers: bool,
    thread_sanitizer: bool,
    real_ort: Path | None,
    real_model: Path | None,
    system: str | None = None,
) -> Path:
    host_system = system or platform.system()
    validate_suite_for_host(suite, host_system)
    if sanitizers and thread_sanitizer:
        raise NativeTestError(
            "ThreadSanitizer cannot be combined with AddressSanitizer/UndefinedBehaviorSanitizer"
        )
    if sanitizers and suite != "posix":
        raise NativeTestError("sanitizers are enabled only for the POSIX suite")
    if thread_sanitizer and suite != "posix":
        raise NativeTestError("ThreadSanitizer is enabled only for the POSIX suite")
    if (real_ort is None) != (real_model is None):
        raise NativeTestError("--real-ort and --real-model must be supplied together")
    if real_ort is not None and suite != "posix":
        raise NativeTestError("real ORT CTests are available only in the POSIX suite")

    repository = repository.resolve(strict=True)
    _regular_build_directory(build_directory)
    build_directory = build_directory.resolve(strict=True)
    cmake = shutil.which("cmake")
    ctest = shutil.which("ctest")
    if cmake is None or ctest is None:
        raise NativeTestError("both cmake and ctest must be available on PATH")

    source_directory = {
        "posix": repository / "test/native",
        "windows-contract": repository / "tool/ci/windows",
        "bundled": repository / "test/native/bundled",
    }[suite]
    if not source_directory.is_dir():
        raise NativeTestError(f"native test source is missing: {source_directory}")

    environment = os.environ.copy()
    environment.pop("FONIX_TEST_ALLOCATION_EPOCH", None)
    environment.pop("FONIX_TEST_ALLOCATION_FAIL_AT", None)
    if sanitizers:
        environment = sanitizer_environment(host_system, environment)
        if host_system == "Darwin":
            print(
                "AppleClang LeakSanitizer is unavailable; running ASan and "
                "UBSan without detect_leaks.",
                flush=True,
            )
    elif thread_sanitizer:
        environment = thread_sanitizer_environment(host_system, environment)

    configure_command = [
        cmake,
        "-S",
        str(source_directory),
        "-B",
        str(build_directory),
        f"-DCMAKE_BUILD_TYPE={BUILD_CONFIGURATION}",
    ]
    if suite == "posix":
        configure_command.extend(
            [
                f"-DFONIX_TEST_SANITIZERS={'ON' if sanitizers else 'OFF'}",
                "-DFONIX_TEST_THREAD_SANITIZER="
                f"{'ON' if thread_sanitizer else 'OFF'}",
            ]
        )
    if real_ort is not None and real_model is not None:
        resolved_ort = _regular_input(real_ort, "real ORT library")
        resolved_model = _regular_input(real_model, "real ONNX model")
        configure_command.extend(
            [
                f"-DFONIX_REAL_ORT_LIBRARY={resolved_ort}",
                f"-DFONIX_REAL_ORT_ROOT={resolved_ort.parent}",
                f"-DFONIX_REAL_MODEL={resolved_model}",
                f"-DFONIX_REAL_MODEL_ROOT={resolved_model.parent}",
            ]
        )
    _run(
        configure_command,
        operation="native CMake configure",
        cwd=repository,
        environment=environment,
        timeout_seconds=CONFIGURE_TIMEOUT_SECONDS,
    )
    _run(
        [
            cmake,
            "--build",
            str(build_directory),
            "--config",
            BUILD_CONFIGURATION,
            "--parallel",
        ],
        operation="native CMake build",
        cwd=repository,
        environment=environment,
        timeout_seconds=BUILD_TIMEOUT_SECONDS,
    )

    discovered = _inventory(
        ctest,
        build_directory,
        cwd=repository,
        environment=environment,
    )
    validate_discovered_tests(
        suite, discovered, require_real_ort=real_ort is not None
    )
    _run(
        _ctest_run_command(ctest, build_directory),
        operation="native CTest suite",
        cwd=repository,
        environment=environment,
        timeout_seconds=CTEST_SUITE_TIMEOUT_SECONDS,
    )

    shim = _find_built_shim(build_directory, host_system)
    print(
        f"Ran {len(discovered)} non-empty {suite} CTests; built shim: {shim}",
        flush=True,
    )
    return shim


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--suite", choices=tuple(REQUIRED_TESTS), required=True)
    sanitizer_mode = parser.add_mutually_exclusive_group()
    sanitizer_mode.add_argument("--sanitizers", action="store_true")
    sanitizer_mode.add_argument("--thread-sanitizer", action="store_true")
    parser.add_argument("--real-ort", type=Path)
    parser.add_argument("--real-model", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        run_native_tests(
            repository=arguments.repository,
            build_directory=arguments.build_dir,
            suite=arguments.suite,
            sanitizers=arguments.sanitizers,
            thread_sanitizer=arguments.thread_sanitizer,
            real_ort=arguments.real_ort,
            real_model=arguments.real_model,
        )
    except (NativeTestError, OSError) as error:
        print(f"native verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
