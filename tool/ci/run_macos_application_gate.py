#!/usr/bin/env python3
"""Build and audit a fresh arm64 Flutter macOS application using Fonix."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from typing import Sequence


VALIDATED_FLUTTER_REVISION = "bd1e75d918605c91b411e8789fb911e6c9a84534"
APPLICATION_NAME = "fonix_macos_application_gate"
APPLICATION_MINIMUM_OS = "14.0"


class MacOsApplicationGateError(RuntimeError):
    """The reproducible Flutter macOS gate could not be completed."""


def _regular_file(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise MacOsApplicationGateError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(mode):
        raise MacOsApplicationGateError(f"{label} is not a regular file: {path}")
    return path


def _directory(path: Path, label: str) -> Path:
    if not path.is_dir() or path.is_symlink():
        raise MacOsApplicationGateError(
            f"{label} is not a non-symlink directory: {path}"
        )
    return path


def _run(command: Sequence[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "DART_SUPPRESS_ANALYTICS": "true"},
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = ""
        if isinstance(error, subprocess.CalledProcessError):
            detail = f"\nstdout:\n{error.stdout}\nstderr:\n{error.stderr}"
        raise MacOsApplicationGateError(
            f"command failed: {' '.join(command)}{detail}"
        ) from error
    return result.stdout


def _pubspec(repository: Path, artifact_cache: Path) -> str:
    repository_scalar = json.dumps(repository.as_posix())
    cache_scalar = json.dumps(artifact_cache.as_posix())
    return f"""name: {APPLICATION_NAME}
description: Reproducible final-application gate for Fonix.
publish_to: none
version: 1.0.0+1

environment:
  sdk: ^3.11.5

dependencies:
  flutter:
    sdk: flutter
  fonix:
    path: {repository_scalar}

flutter:
  uses-material-design: true
  assets:
    - assets/fonix/fonix-native-artifact-manifest.json
    - assets/fonix/ThirdPartyNotices.txt

hooks:
  user_defines:
    fonix:
      runtime_mode: bundled
      artifact_cache: {cache_scalar}
      application_minimum_os: '{APPLICATION_MINIMUM_OS}'
"""


def configure_xcode_project(source: str) -> str:
    floor_pattern = re.compile(r"MACOSX_DEPLOYMENT_TARGET = [^;]+;")
    source, floor_count = floor_pattern.subn(
        f"MACOSX_DEPLOYMENT_TARGET = {APPLICATION_MINIMUM_OS};", source
    )
    if floor_count != 3:
        raise MacOsApplicationGateError(
            f"expected three generated macOS deployment targets; found {floor_count}"
        )
    marker = "\t\t\t\tENABLE_USER_SCRIPT_SANDBOXING = NO;"
    if source.count(marker) != 3:
        raise MacOsApplicationGateError(
            "generated Xcode project did not contain three sandbox settings"
        )
    return source.replace(marker, f"{marker}\n\t\t\t\tEXCLUDED_ARCHS = x86_64;")


def _verify_flutter(flutter: Path) -> dict[str, object]:
    value = json.loads(_run((str(flutter), "--version", "--machine")))
    if not isinstance(value, dict):
        raise MacOsApplicationGateError("Flutter --version --machine was not an object")
    if value.get("frameworkRevision") != VALIDATED_FLUTTER_REVISION:
        raise MacOsApplicationGateError(
            "Flutter revision differs from the final-app gate revision: "
            f"{value.get('frameworkRevision')!r}"
        )
    return value


def run_gate(
    *,
    repository: Path,
    flutter: Path,
    artifact_cache: Path,
    reference_runtime: Path,
    work_directory: Path,
) -> dict[str, object]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise MacOsApplicationGateError("the macOS application gate requires an arm64 Mac")
    repository = _directory(repository.resolve(strict=True), "repository")
    flutter = _regular_file(flutter.resolve(strict=True), "Flutter executable")
    artifact_cache = _directory(
        artifact_cache.resolve(strict=True), "offline artifact cache"
    )
    reference_runtime = _regular_file(
        reference_runtime.resolve(strict=True), "lock-verified reference runtime"
    )
    if not work_directory.is_absolute():
        raise MacOsApplicationGateError("--work-dir must be absolute")
    if work_directory.exists() or work_directory.is_symlink():
        raise MacOsApplicationGateError("--work-dir must not already exist")

    flutter_version = _verify_flutter(flutter)
    dart = flutter.parent / "cache/dart-sdk/bin/dart"
    _regular_file(dart, "Flutter-bundled Dart executable")
    _run(
        (
            str(flutter),
            "create",
            "--no-pub",
            "--platforms=macos",
            "--org=dev.fonix.gate",
            f"--project-name={APPLICATION_NAME}",
            str(work_directory),
        )
    )

    pubspec = _regular_file(work_directory / "pubspec.yaml", "generated pubspec")
    pubspec.write_text(_pubspec(repository, artifact_cache), encoding="utf-8")
    project = _regular_file(
        work_directory / "macos/Runner.xcodeproj/project.pbxproj",
        "generated Xcode project",
    )
    project.write_text(
        configure_xcode_project(project.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    _run((str(flutter), "pub", "get", "--offline"), cwd=work_directory)
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
            str(artifact_cache),
            "--output",
            str(asset_output),
        ),
        cwd=work_directory,
    )
    _run((str(flutter), "build", "macos", "--release", "--no-pub"), cwd=work_directory)

    application = (
        work_directory
        / f"build/macos/Build/Products/Release/{APPLICATION_NAME}.app"
    )
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
            str(repository / "test/fixtures/mul_1.onnx"),
        )
    )
    audit = json.loads(audit_output)
    if not isinstance(audit, dict) or audit.get("cpuInference") != "passed":
        raise MacOsApplicationGateError("final application audit did not pass")
    return {
        "application": str(application),
        "artifactId": audit.get("artifactId"),
        "cpuInference": audit.get("cpuInference"),
        "flutterRevision": flutter_version.get("frameworkRevision"),
        "flutterVersion": flutter_version.get("frameworkVersion"),
        "applicationMinimumOs": APPLICATION_MINIMUM_OS,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
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
        print(json.dumps(report, sort_keys=True))
        return 0
    except (MacOsApplicationGateError, FileNotFoundError, json.JSONDecodeError) as error:
        print(f"run_macos_application_gate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
