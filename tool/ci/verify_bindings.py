#!/usr/bin/env python3
"""Regenerate both ffigen outputs outside the checkout and compare bytes."""

from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


MAX_CONFIG_BYTES = 1024 * 1024
MAX_PUBSPEC_BYTES = 1024 * 1024
DEFAULT_CONFIGS = ("ffigen.yaml", "ffigen.native_assets.yaml")


class BindingVerificationError(RuntimeError):
    """A closed binding-regeneration precondition or comparison failed."""


def _replace_once(source: str, pattern: str, replacement: str, label: str) -> str:
    result, count = re.subn(pattern, replacement, source, flags=re.MULTILINE)
    if count != 1:
        raise BindingVerificationError(
            f"{label}: expected exactly one matching configuration line; found {count}"
        )
    return result


def _yaml_string(value: Path) -> str:
    # A JSON string is also a YAML 1.2 double-quoted scalar. This keeps Windows
    # backslashes and any spaces unambiguous without another YAML dependency.
    return json.dumps(str(value), ensure_ascii=True)


def package_language_version(repository: Path) -> str:
    """Return the Dart language version implied by the exact SDK floor."""

    pubspec = repository / "pubspec.yaml"
    source = pubspec.read_text(encoding="utf-8")
    if len(source.encode("utf-8")) > MAX_PUBSPEC_BYTES:
        raise BindingVerificationError(
            f"pubspec.yaml exceeds the {MAX_PUBSPEC_BYTES}-byte limit"
        )
    matches = re.findall(
        r"^\s{2}sdk:\s+\^([0-9]+)\.([0-9]+)\.[0-9]+\s*$",
        source,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise BindingVerificationError(
            "pubspec.yaml must declare one unquoted ^major.minor.patch SDK floor"
        )
    major, minor = matches[0]
    return f"{int(major)}.{int(minor)}"


def write_isolated_package_config(root: Path, language_version: str) -> None:
    """Give generators beneath [root] the package's formatter language."""

    config = root / ".dart_tool" / "package_config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "configVersion": 2,
                "packages": [
                    {
                        "name": "fonix_binding_check",
                        "rootUri": "../",
                        "packageUri": "lib/",
                        "languageVersion": language_version,
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _committed_output_path(source: str, repository: Path) -> Path:
    output_match = re.search(
        r"^output:\s*([A-Za-z0-9_./-]+)\s*$", source, flags=re.MULTILINE
    )
    if output_match is None:
        raise BindingVerificationError(
            "ffigen output must be one canonical relative checkout path"
        )
    relative_output = output_match.group(1)
    if relative_output.startswith("/") or ".." in Path(relative_output).parts:
        raise BindingVerificationError(
            "ffigen output must stay within the repository checkout"
        )
    committed_output = (repository / relative_output).resolve()
    if repository != committed_output and repository not in committed_output.parents:
        raise BindingVerificationError(
            "ffigen output resolved outside the repository checkout"
        )
    return committed_output


def isolated_config(
    source: str,
    *,
    repository: Path,
    generated_output: Path,
) -> tuple[str, Path]:
    """Return an absolute-input config and its committed output path."""

    encoded_size = len(source.encode("utf-8"))
    if encoded_size > MAX_CONFIG_BYTES:
        raise BindingVerificationError(
            f"ffigen configuration is {encoded_size} bytes; maximum is "
            f"{MAX_CONFIG_BYTES}"
        )

    committed_output = _committed_output_path(source, repository)

    transformed = _replace_once(
        source,
        r"^output:\s*[A-Za-z0-9_./-]+\s*$",
        f"output: {_yaml_string(generated_output)}",
        "output",
    )
    transformed = _replace_once(
        transformed,
        r"^    - src/dort\.h\s*$",
        f"    - {_yaml_string(repository / 'src' / 'dort.h')}",
        "headers.entry-points",
    )
    transformed = _replace_once(
        transformed,
        r"^  - -Isrc\s*$",
        f"  - {_yaml_string(Path('-I' + str(repository / 'src')))}",
        "compiler include for src",
    )
    transformed = _replace_once(
        transformed,
        r"^  - -Ithird_party/onnxruntime/include\s*$",
        "  - "
        + _yaml_string(
            Path("-I" + str(repository / "third_party/onnxruntime/include"))
        ),
        "compiler include for ONNX Runtime",
    )
    return transformed, committed_output


def _diff(expected: Path, actual: Path) -> str:
    try:
        expected_lines = expected.read_text(encoding="utf-8").splitlines()
        actual_lines = actual.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise BindingVerificationError(
            "generated bindings must be valid UTF-8 text"
        ) from error
    lines = list(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile=str(expected),
            tofile=f"regenerated/{expected.name}",
            lineterm="",
        )
    )
    if len(lines) > 400:
        lines = lines[:400] + ["... diff truncated after 400 lines ..."]
    return "\n".join(lines)


def verify_bindings(
    repository: Path,
    config_names: tuple[str, ...] = DEFAULT_CONFIGS,
    *,
    dart: str = "dart",
) -> None:
    repository = repository.resolve(strict=True)
    dart_executable = shutil.which(dart) if not Path(dart).is_absolute() else dart
    if dart_executable is None:
        raise BindingVerificationError(f"Dart executable not found: {dart}")
    language_version = package_language_version(repository)

    mismatches: list[str] = []
    with tempfile.TemporaryDirectory(prefix="fonix-ffigen-check-") as temporary:
        temporary_root = Path(temporary)
        write_isolated_package_config(temporary_root, language_version)
        for index, config_name in enumerate(config_names):
            config_path = (repository / config_name).resolve(strict=True)
            if repository not in config_path.parents:
                raise BindingVerificationError(
                    f"configuration escapes repository: {config_name}"
                )
            source = config_path.read_text(encoding="utf-8")
            committed_from_config = _committed_output_path(source, repository)
            generated_output = temporary_root / committed_from_config.relative_to(
                repository
            )
            transformed, committed_output = isolated_config(
                source,
                repository=repository,
                generated_output=generated_output,
            )
            generated_output.parent.mkdir(parents=True, exist_ok=True)
            if not committed_output.is_file():
                raise BindingVerificationError(
                    f"committed binding output is missing: {committed_output}"
                )
            temporary_config = temporary_root / f"config-{index}.yaml"
            temporary_config.write_text(transformed, encoding="utf-8")

            environment = os.environ.copy()
            # Keep ffigen deterministic and prevent the Dart launcher from
            # attempting to update per-user analytics state in hermetic CI.
            environment.setdefault("CI", "true")
            environment.setdefault("DART_SUPPRESS_ANALYTICS", "true")
            result = subprocess.run(
                [
                    str(dart_executable),
                    "run",
                    "ffigen",
                    "--config",
                    str(temporary_config),
                    "--verbose",
                    "warning",
                ],
                cwd=repository,
                env=environment,
                check=False,
            )
            if result.returncode != 0:
                raise BindingVerificationError(
                    f"ffigen failed for {config_name} with exit code "
                    f"{result.returncode}"
                )
            if not generated_output.is_file():
                raise BindingVerificationError(
                    f"ffigen did not create the isolated output for {config_name}"
                )
            format_result = subprocess.run(
                [
                    str(dart_executable),
                    "format",
                    "--language-version",
                    language_version,
                    str(generated_output),
                ],
                cwd=repository,
                env=environment,
                check=False,
            )
            if format_result.returncode != 0:
                raise BindingVerificationError(
                    f"dart format failed for the isolated {config_name} output"
                )
            if committed_output.read_bytes() != generated_output.read_bytes():
                mismatches.append(_diff(committed_output, generated_output))

    if mismatches:
        raise BindingVerificationError(
            "FFI bindings are stale; regenerate both checked-in outputs.\n"
            + "\n".join(mismatches)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Fonix repository root (defaults to the script's checkout)",
    )
    parser.add_argument(
        "--dart",
        default="dart",
        help="Dart executable name or absolute path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        verify_bindings(arguments.repository, dart=arguments.dart)
    except (BindingVerificationError, OSError) as error:
        print(f"binding verification failed: {error}", file=sys.stderr)
        return 1
    print("Both ffigen outputs reproduce exactly from src/dort.h.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
