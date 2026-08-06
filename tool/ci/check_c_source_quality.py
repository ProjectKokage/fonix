#!/usr/bin/env python3
"""Apply Fonix's deterministic, offline C source hygiene contract."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
from typing import Sequence


SOURCE_ROOTS = ("src", "test/native", "tool/ci")
SOURCE_SUFFIXES = frozenset({".c", ".h"})
MAX_SOURCE_FILES = 256
MAX_SOURCE_FILE_BYTES = 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 512
MAX_REPORTED_PROBLEMS = 64

_BYPASS_MARKERS = (
    b"clang-format off",
    b"nolint",
    b"#pragma gcc diagnostic ignored",
    b"#pragma clang diagnostic ignored",
    b"#pragma warning(disable",
)
_CONFLICT_MARKERS = (b"<<<<<<<", b"=======", b">>>>>>>")


class CSourceQualityError(RuntimeError):
    """Repository-owned C source violates the closed quality contract."""


def _relative_posix(path: Path, repository: Path) -> str:
    try:
        return path.relative_to(repository).as_posix()
    except ValueError as error:
        raise CSourceQualityError(
            f"C source escaped the repository root: {path}"
        ) from error


def discover_sources(repository: Path) -> tuple[Path, ...]:
    """Return the exact, sorted repository-owned C/H source inventory."""
    repository = repository.resolve(strict=True)
    discovered: list[Path] = []
    for relative_root in SOURCE_ROOTS:
        root = repository / relative_root
        try:
            root_mode = root.lstat().st_mode
        except FileNotFoundError as error:
            raise CSourceQualityError(
                f"required C source root is missing: {relative_root}"
            ) from error
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise CSourceQualityError(
                f"required C source root is not a regular directory: {relative_root}"
            )

        for current, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            directory_names.sort()
            file_names.sort()
            current_path = Path(current)
            for directory_name in tuple(directory_names):
                directory = current_path / directory_name
                if directory.is_symlink():
                    relative = _relative_posix(directory, repository)
                    raise CSourceQualityError(
                        "C source tree must not contain symlink directories: "
                        f"{relative}"
                    )
            for file_name in file_names:
                candidate = current_path / file_name
                if candidate.suffix.lower() not in SOURCE_SUFFIXES:
                    continue
                mode = candidate.lstat().st_mode
                relative = _relative_posix(candidate, repository)
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise CSourceQualityError(
                        f"C source must be a regular non-symlink file: {relative}"
                    )
                discovered.append(candidate)

    discovered.sort(key=lambda path: _relative_posix(path, repository))
    if not discovered:
        raise CSourceQualityError("the repository contains no owned C/H sources")
    if len(discovered) > MAX_SOURCE_FILES:
        raise CSourceQualityError(
            f"C source inventory exceeds {MAX_SOURCE_FILES} files"
        )
    relative_paths = [_relative_posix(path, repository) for path in discovered]
    if len(relative_paths) != len(set(relative_paths)):
        raise CSourceQualityError("C source inventory contains duplicate paths")
    return tuple(discovered)


def _line_problem(path: str, line_number: int, message: str) -> str:
    return f"{path}:{line_number}: {message}"


def audit_source_bytes(path: str, source: bytes) -> tuple[str, ...]:
    """Return deterministic problems for one bounded C/H source payload."""
    problems: list[str] = []
    if not source:
        return (f"{path}: source file is empty",)
    if len(source) > MAX_SOURCE_FILE_BYTES:
        return (
            f"{path}: source file exceeds {MAX_SOURCE_FILE_BYTES} bytes",
        )
    if source.startswith(b"\xef\xbb\xbf"):
        problems.append(f"{path}: UTF-8 BOM is forbidden")
    try:
        source.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        problems.append(f"{path}: source is not strict UTF-8")

    if not source.endswith(b"\n"):
        problems.append(f"{path}: source must end in one LF")
    elif source.endswith(b"\n\n"):
        problems.append(f"{path}: source must not have a blank line at EOF")

    lines = source.splitlines(keepends=True)
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line[:-1] if raw_line.endswith(b"\n") else raw_line
        if line.endswith(b"\r"):
            problems.append(
                _line_problem(path, line_number, "CRLF/CR line endings are forbidden")
            )
            line = line[:-1]
        if len(line) > MAX_LINE_BYTES:
            problems.append(
                _line_problem(
                    path,
                    line_number,
                    f"line exceeds {MAX_LINE_BYTES} bytes",
                )
            )
        if b"\t" in line:
            problems.append(_line_problem(path, line_number, "tabs are forbidden"))
        if line.endswith((b" ", b"\t")):
            problems.append(
                _line_problem(path, line_number, "trailing whitespace is forbidden")
            )
        if line.lstrip(b" ").startswith(b"#") and line.startswith(b" "):
            problems.append(
                _line_problem(
                    path,
                    line_number,
                    "preprocessor directives must start in column one",
                )
            )
        lowered = line.lower()
        for marker in _BYPASS_MARKERS:
            if marker in lowered:
                problems.append(
                    _line_problem(
                        path,
                        line_number,
                        "format/lint suppression markers are forbidden",
                    )
                )
                break
        stripped = line.lstrip(b" ")
        if any(stripped.startswith(marker) for marker in _CONFLICT_MARKERS):
            problems.append(
                _line_problem(path, line_number, "merge conflict marker is forbidden")
            )
        for byte in line:
            if byte < 0x20 or byte == 0x7F:
                if byte != 0x09:
                    problems.append(
                        _line_problem(
                            path,
                            line_number,
                            "ASCII control bytes are forbidden",
                        )
                    )
                break
        if len(problems) >= MAX_REPORTED_PROBLEMS:
            break
    return tuple(problems)


def audit_repository(repository: Path) -> tuple[int, int]:
    """Audit all owned sources and return (file count, total bytes)."""
    repository = repository.resolve(strict=True)
    sources = discover_sources(repository)
    total_bytes = 0
    problems: list[str] = []
    for source_path in sources:
        relative = _relative_posix(source_path, repository)
        payload = source_path.read_bytes()
        total_bytes += len(payload)
        if total_bytes > MAX_TOTAL_SOURCE_BYTES:
            raise CSourceQualityError(
                f"C source inventory exceeds {MAX_TOTAL_SOURCE_BYTES} total bytes"
            )
        problems.extend(audit_source_bytes(relative, payload))
        if len(problems) >= MAX_REPORTED_PROBLEMS:
            break
    if problems:
        rendered = "\n".join(problems[:MAX_REPORTED_PROBLEMS])
        if len(problems) >= MAX_REPORTED_PROBLEMS:
            rendered += "\nadditional C source problems were suppressed"
        raise CSourceQualityError(rendered)
    return len(sources), total_bytes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check repository-owned C source format/lint hygiene offline."
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="Fonix repository root (default: current directory).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        file_count, total_bytes = audit_repository(arguments.repository)
    except (CSourceQualityError, OSError) as error:
        print(f"C source quality gate failed: {error}", file=sys.stderr)
        return 1
    print(
        f"C source quality gate passed for {file_count} files "
        f"({total_bytes} bytes)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
