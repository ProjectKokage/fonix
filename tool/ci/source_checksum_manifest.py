#!/usr/bin/env python3
"""Generate or verify Fonix's deterministic source checksum manifest.

Only the closed source tree declared below is eligible. Generated state, Git
metadata, the checksum manifest itself, and release outputs are never hashed.
Unknown top-level entries fail closed so a release audit cannot silently omit a
new source root or an accidental binary.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from typing import Iterable


MAX_ENTRY_COUNT = 16_384
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024

SOURCE_DIRECTORIES = frozenset(
    {
        ".github",
        "bin",
        "docs",
        "hook",
        "lib",
        "native",
        "src",
        "templates",
        "test",
        "third_party",
        "tool",
    }
)
SOURCE_ROOT_FILES = frozenset(
    {
        ".gitignore",
        "AGENTS.md",
        "CHANGELOG.md",
        "LICENSE",
        "README.md",
        "VALIDATION.md",
        "analysis_options.yaml",
        "ffigen.native_assets.yaml",
        "ffigen.yaml",
        "pubspec.lock",
        "pubspec.yaml",
    }
)
IGNORED_ROOT_DIRECTORIES = frozenset(
    {".dart_tool", ".git", ".idea", ".vscode", "build"}
)
FORBIDDEN_GENERATED_DIRECTORIES = frozenset({"__pycache__"})
FORBIDDEN_GENERATED_FILES = frozenset({".DS_Store"})
FORBIDDEN_GENERATED_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp", "~")
MANIFEST_NAME = "MANIFEST.sha256"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  \./(.+)$")


class SourceManifestError(RuntimeError):
    """The source tree or checksum manifest violates its closed contract."""


def _safe_relative_path(value: str, *, label: str) -> str:
    if (
        not value
        or len(value.encode("utf-8")) > 4096
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise SourceManifestError(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceManifestError(f"{label} is not a safe relative path")
    normalized = path.as_posix()
    if normalized != value or normalized == MANIFEST_NAME:
        raise SourceManifestError(f"{label} is not a canonical source path")
    return normalized


def _regular_file_bytes(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise SourceManifestError(f"{label} cannot be inspected") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise SourceManifestError(f"{label} must be a regular file, not a link")
    if before.st_size < 0 or before.st_size > maximum:
        raise SourceManifestError(f"{label} size is outside the accepted bound")

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SourceManifestError(f"{label} cannot be opened safely") from error
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            raise SourceManifestError(f"{label} changed type while being read")
        if (
            before.st_size != after.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
        ):
            raise SourceManifestError(f"{label} changed while being read")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum:
                raise SourceManifestError(f"{label} exceeds the accepted size bound")
            chunks.append(chunk)
        if consumed != before.st_size:
            raise SourceManifestError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _walk_source_directory(repository: Path, directory_name: str) -> list[str]:
    root = repository / directory_name
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise SourceManifestError(
            f"source root {directory_name} must be a directory, not a link"
        )

    result: list[str] = []
    entry_count = 0
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        directory_names.sort()
        file_names.sort()
        entry_count += len(directory_names) + len(file_names)
        if entry_count > MAX_ENTRY_COUNT:
            raise SourceManifestError(
                f"source root {directory_name} exceeds the entry bound"
            )
        for name in directory_names:
            candidate = current / name
            if name in FORBIDDEN_GENERATED_DIRECTORIES:
                relative = candidate.relative_to(repository).as_posix()
                raise SourceManifestError(
                    f"source tree contains generated directory {relative}"
                )
            if candidate.is_symlink():
                relative = candidate.relative_to(repository).as_posix()
                raise SourceManifestError(
                    f"source path {relative} must not be a symbolic link"
                )
        for name in file_names:
            candidate = current / name
            relative = _safe_relative_path(
                candidate.relative_to(repository).as_posix(), label="source path"
            )
            if name in FORBIDDEN_GENERATED_FILES or name.endswith(
                FORBIDDEN_GENERATED_SUFFIXES
            ):
                raise SourceManifestError(
                    f"source tree contains generated file {relative}"
                )
            try:
                mode = candidate.lstat().st_mode
            except OSError as error:
                raise SourceManifestError(
                    f"source path {relative} cannot be inspected"
                ) from error
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise SourceManifestError(
                    f"source path {relative} must be a regular file, not a link"
                )
            result.append(relative)
    return result


def source_paths(repository: Path) -> tuple[str, ...]:
    """Return the closed, sorted source file set for ``repository``."""

    if repository.is_symlink() or not repository.is_dir():
        raise SourceManifestError("repository must be a directory, not a link")

    paths: list[str] = []
    unknown: list[str] = []
    try:
        root_entries = sorted(repository.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        raise SourceManifestError("repository cannot be inspected") from error
    for entry in root_entries:
        name = entry.name
        if entry.is_symlink():
            raise SourceManifestError(f"top-level path {name} must not be a link")
        if name == MANIFEST_NAME:
            continue
        if name in IGNORED_ROOT_DIRECTORIES:
            if not entry.is_dir():
                raise SourceManifestError(
                    f"ignored path {name} must be a directory, not a file"
                )
            continue
        if name in SOURCE_ROOT_FILES:
            if not entry.is_file():
                raise SourceManifestError(f"source path {name} must be a regular file")
            paths.append(_safe_relative_path(name, label="source path"))
            continue
        if name in SOURCE_DIRECTORIES:
            if not entry.is_dir():
                raise SourceManifestError(f"source root {name} must be a directory")
            paths.extend(_walk_source_directory(repository, name))
            continue
        unknown.append(name)
    if unknown:
        joined = ", ".join(unknown[:8])
        suffix = "" if len(unknown) <= 8 else ", ..."
        raise SourceManifestError(
            f"repository has unknown top-level entries: {joined}{suffix}"
        )
    if not paths:
        raise SourceManifestError("repository contains no source files")
    if len(paths) > MAX_ENTRY_COUNT:
        raise SourceManifestError("source tree exceeds the entry bound")
    if len(paths) != len(set(paths)):
        raise SourceManifestError("source tree contains duplicate canonical paths")
    if len(paths) != len({path.casefold() for path in paths}):
        raise SourceManifestError("source tree contains case-folding path collisions")
    return tuple(sorted(paths))


def build_manifest(repository: Path) -> bytes:
    """Build canonical checksum-manifest bytes without writing them."""

    lines: list[str] = []
    total_bytes = 0
    for relative in source_paths(repository):
        contents = _regular_file_bytes(
            repository.joinpath(*relative.split("/")),
            label=f"source path {relative}",
            maximum=MAX_FILE_BYTES,
        )
        total_bytes += len(contents)
        if total_bytes > MAX_TOTAL_BYTES:
            raise SourceManifestError("source tree exceeds the total byte bound")
        lines.append(f"{hashlib.sha256(contents).hexdigest()}  ./{relative}\n")
    encoded = "".join(lines).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise SourceManifestError("generated manifest exceeds the size bound")
    return encoded


def parse_manifest(contents: bytes) -> tuple[tuple[str, str], ...]:
    """Parse canonical manifest bytes, rejecting duplicates and unsafe paths."""

    if not contents or len(contents) > MAX_MANIFEST_BYTES:
        raise SourceManifestError("checksum manifest size is outside the accepted bound")
    if b"\r" in contents or not contents.endswith(b"\n"):
        raise SourceManifestError("checksum manifest must use canonical LF lines")
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceManifestError("checksum manifest must be UTF-8") from error

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    seen_casefolded: set[str] = set()
    for index, line in enumerate(text.splitlines(), start=1):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise SourceManifestError(f"checksum manifest line {index} is malformed")
        digest, raw_path = match.groups()
        if _DIGEST.fullmatch(digest) is None:
            raise SourceManifestError(f"checksum manifest line {index} has invalid digest")
        relative = _safe_relative_path(raw_path, label=f"manifest line {index} path")
        if relative in seen:
            raise SourceManifestError(f"checksum manifest duplicates path {relative}")
        if relative.casefold() in seen_casefolded:
            raise SourceManifestError(
                f"checksum manifest has a case-folding collision at {relative}"
            )
        seen.add(relative)
        seen_casefolded.add(relative.casefold())
        entries.append((relative, digest))
    if entries != sorted(entries):
        raise SourceManifestError("checksum manifest paths are not sorted")
    return tuple(entries)


def check_manifest(repository: Path, manifest_path: Path) -> str:
    """Verify exact manifest bytes and return the manifest SHA-256."""

    actual = _regular_file_bytes(
        manifest_path, label="checksum manifest", maximum=MAX_MANIFEST_BYTES
    )
    parse_manifest(actual)
    expected = build_manifest(repository)
    if actual != expected:
        raise SourceManifestError(
            "checksum manifest does not exactly match the closed source tree"
        )
    return hashlib.sha256(actual).hexdigest()


def _atomic_write(path: Path, contents: bytes) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise SourceManifestError("manifest output parent must be a directory")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise SourceManifestError("manifest output must be a regular file")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def generate_manifest(repository: Path, output: Path) -> str:
    contents = build_manifest(repository)
    _atomic_write(output, contents)
    return hashlib.sha256(contents).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "check"):
        command = subparsers.add_parser(name)
        command.add_argument("--repository", type=Path, default=Path("."))
        if name == "generate":
            command.add_argument("--output", type=Path, default=Path(MANIFEST_NAME))
        else:
            command.add_argument(
                "--manifest", type=Path, default=Path(MANIFEST_NAME)
            )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "generate":
            digest = generate_manifest(arguments.repository, arguments.output)
            print(f"generated source checksum manifest sha256={digest}")
        else:
            digest = check_manifest(arguments.repository, arguments.manifest)
            print(f"verified source checksum manifest sha256={digest}")
        return 0
    except SourceManifestError as error:
        print(f"source checksum manifest error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
