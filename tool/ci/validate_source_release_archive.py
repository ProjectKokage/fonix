#!/usr/bin/env python3
"""Validate one closed Git source archive against the current Fonix source.

The archive is inspected in place and is never extracted.  The emitted record
is offline source-closure evidence only: the Git revision declared by archive
metadata is not an authenticated statement about who created the archive.
"""

from __future__ import annotations

import argparse
import binascii
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import stat
import struct
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Any, Iterable, Mapping
import zipfile
import zlib


sys.dont_write_bytecode = True

SOURCE_HELPER_PATH = "tool/ci/source_checksum_manifest.py"
SOURCE_MANIFEST_PATH = "MANIFEST.sha256"
VALIDATOR_PATH = "tool/ci/validate_source_release_archive.py"
VALIDATION_SCHEMA_PATH = (
    "templates/ci/source_release_archive_validation_v1.schema.json"
)
VALIDATION_SCHEMA_ID = (
    "https://fonix.invalid/schemas/source-release-archive-validation-v1.json"
)
EXPECTED_SOURCE_HELPER_SHA256 = (
    "9ef720e3bae376a01b4b61c2b2a4214c31760075c23bd64dca4fb887641dd5b6"
)
EXPECTED_VALIDATION_SCHEMA_SHA256 = (
    "eeec8ed94b3438509936c9f1b629663a6699ac7b9c300f82299add5d1cd69ed9"
)

MEDIA_TYPE_ZIP = "application/zip"
MEDIA_TYPE_GZIP = "application/gzip"
_FORMAT_BY_MEDIA_TYPE = {
    MEDIA_TYPE_ZIP: ("git-archive-zip-v1", "zip-comment"),
    MEDIA_TYPE_GZIP: ("git-archive-tar-gzip-v1", "pax-global-comment"),
}

MAXIMUM_PATH_BYTES = 4096
MAXIMUM_ARCHIVE_BYTES = 576 * 1024 * 1024
MAXIMUM_MEMBER_COUNT = 65_536
MAXIMUM_REGULAR_FILE_COUNT = 16_385
MAXIMUM_DIRECTORY_COUNT = MAXIMUM_MEMBER_COUNT - 2
MAXIMUM_TOOL_BYTES = 4 * 1024 * 1024
MAXIMUM_SCHEMA_BYTES = 4 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 4 * 1024 * 1024
MAXIMUM_TAR_PADDING_BYTES = 10 * 1024
_IO_CHUNK_BYTES = 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_ZIP_LOCAL = struct.Struct("<IHHHHHIIIHH")
_ZIP_CENTRAL = struct.Struct("<IHHHHHHIIIHHHHHII")
_ZIP_EOCD = struct.Struct("<IHHHHIIH")
_ZIP_LOCAL_SIGNATURE = 0x04034B50
_ZIP_CENTRAL_SIGNATURE = 0x02014B50
_ZIP_EOCD_SIGNATURE = 0x06054B50
_ZIP_TIMESTAMP_EXTRA = 0x5455
_ZIP_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})

_CLAIM_BOUNDARY = (
    "Offline source-closure validation only. It binds one archive's declared "
    "Git revision, closed member inventory, contents, manifest, and executable "
    "semantics to the current repository baseline; it does not authenticate "
    "archive origin, prove build reproducibility or target behavior, establish "
    "licensing, signing, or readiness, or authorize publication or distribution."
)


class SourceReleaseArchiveError(RuntimeError):
    """The archive, source baseline, or publication boundary failed closed."""


@dataclass(frozen=True)
class _ExpectedFile:
    path: str
    size_bytes: int
    sha256: str
    git_blob_id: str
    executable: bool


@dataclass(frozen=True)
class _ExpectedMember:
    path: str
    kind: str
    file: _ExpectedFile | None = None

    @property
    def archive_name(self) -> str:
        return f"{self.path}/" if self.kind == "directory" else self.path

    def inventory_value(self) -> dict[str, Any]:
        if self.kind == "directory":
            return {"kind": "directory", "path": self.path}
        assert self.file is not None
        return {
            "kind": "file",
            "path": self.path,
            "sizeBytes": self.file.size_bytes,
            "sha256": self.file.sha256,
            "executable": self.file.executable,
        }


@dataclass(frozen=True)
class _SourceSnapshot:
    revision: str
    manifest_sha256: str
    manifest_size_bytes: int
    manifest_entry_count: int
    members: tuple[_ExpectedMember, ...]
    expanded_bytes: int
    executable_file_count: int


@dataclass(frozen=True)
class _ZipCentralEntry:
    name: str
    name_bytes: bytes
    extra: bytes
    version_made: int
    version_needed: int
    flags: int
    compression: int
    modified_time: int
    modified_date: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    internal_attributes: int
    external_attributes: int
    local_offset: int


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise SourceReleaseArchiveError("value is not canonical JSON data") from error


def _identity(contents: bytes) -> dict[str, Any]:
    return {
        "sizeBytes": len(contents),
        "sha256": hashlib.sha256(contents).hexdigest(),
    }


def _same_metadata(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
    )


def _repository_directory(path: Path) -> Path:
    raw = Path(path)
    absolute = Path(os.path.abspath(raw))
    try:
        resolved = raw.resolve(strict=True)
        metadata = raw.lstat()
    except OSError as error:
        raise SourceReleaseArchiveError("repository cannot be resolved") from error
    if (
        absolute != resolved
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise SourceReleaseArchiveError(
            "repository must be one canonical non-link directory"
        )
    return resolved


def _bounded_absolute_path(path: Path, label: str) -> Path:
    value = Path(path)
    encoded = os.fsencode(str(value))
    if (
        not value.is_absolute()
        or not encoded
        or len(encoded) > MAXIMUM_PATH_BYTES
        or b"\x00" in encoded
        or any(byte < 0x20 for byte in encoded)
    ):
        raise SourceReleaseArchiveError(f"{label} must be a bounded absolute path")
    return value


def _canonical_existing_file(path: Path, label: str) -> tuple[Path, os.stat_result]:
    value = _bounded_absolute_path(path, label)
    try:
        resolved = value.resolve(strict=True)
        metadata = value.lstat()
    except OSError as error:
        raise SourceReleaseArchiveError(f"{label} cannot be resolved") from error
    if (
        value != resolved
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise SourceReleaseArchiveError(
            f"{label} must be one canonical regular non-link file"
        )
    return resolved, metadata


def _canonical_new_file(path: Path, label: str) -> tuple[Path, os.stat_result]:
    value = _bounded_absolute_path(path, label)
    if value.name in {"", ".", ".."}:
        raise SourceReleaseArchiveError(f"{label} leaf name is invalid")
    try:
        parent = value.parent.resolve(strict=True)
        parent_metadata = value.parent.lstat()
    except OSError as error:
        raise SourceReleaseArchiveError(f"{label} parent cannot be resolved") from error
    if (
        value.parent != parent
        or stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
    ):
        raise SourceReleaseArchiveError(
            f"{label} parent must be one canonical non-link directory"
        )
    candidate = parent / value.name
    try:
        candidate.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SourceReleaseArchiveError(f"{label} cannot be inspected") from error
    else:
        raise SourceReleaseArchiveError(f"{label} must not already exist")
    return candidate, parent_metadata


def _is_descendant(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _repository_file(repository: Path, relative: str) -> Path:
    candidate = repository.joinpath(*relative.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise SourceReleaseArchiveError(
            f"repository file {relative} cannot be resolved"
        ) from error
    if not _is_descendant(resolved, repository) or resolved != candidate:
        raise SourceReleaseArchiveError(
            f"repository file {relative} escapes the repository"
        )
    return candidate


def _read_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as error:
        raise SourceReleaseArchiveError(f"{label} cannot be inspected") from error
    minimum = 0 if allow_empty else 1
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or not minimum <= before.st_size <= maximum_bytes
    ):
        raise SourceReleaseArchiveError(f"{label} is not one bounded regular file")
    if not hasattr(os, "O_NOFOLLOW"):
        raise SourceReleaseArchiveError(
            "this host cannot enforce no-follow source reads"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SourceReleaseArchiveError(f"{label} cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if not _same_metadata(before, opened):
            raise SourceReleaseArchiveError(f"{label} changed before reading")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(_IO_CHUNK_BYTES, maximum_bytes + 1 - consumed))
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum_bytes:
                raise SourceReleaseArchiveError(f"{label} exceeds its byte bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if consumed != before.st_size or not _same_metadata(before, after):
            raise SourceReleaseArchiveError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
    except OSError as error:
        raise SourceReleaseArchiveError(f"{label} disappeared after reading") from error
    if not _same_metadata(before, final):
        raise SourceReleaseArchiveError(f"{label} changed after reading")
    return b"".join(chunks), before


def _strict_json(contents: bytes, label: str) -> Any:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise SourceReleaseArchiveError(f"{label} has duplicate JSON keys")
            value[key] = item
        return value

    try:
        text = contents.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=lambda token: (_ for _ in ()).throw(
                SourceReleaseArchiveError(
                    f"{label} contains non-finite JSON value {token}"
                )
            ),
        )
    except SourceReleaseArchiveError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceReleaseArchiveError(f"{label} is not strict JSON") from error


def _load_source_helper(repository: Path, supplied: Any | None) -> Any:
    helper_path = _repository_file(repository, SOURCE_HELPER_PATH)
    helper_bytes, _ = _read_regular_file(
        helper_path,
        label="source manifest validator",
        maximum_bytes=MAXIMUM_TOOL_BYTES,
    )
    if hashlib.sha256(helper_bytes).hexdigest() != EXPECTED_SOURCE_HELPER_SHA256:
        raise SourceReleaseArchiveError(
            "source manifest validator does not match the pinned bytes"
        )
    if supplied is not None:
        module_path = getattr(supplied, "__file__", None)
        try:
            resolved_module_path = Path(module_path).resolve(strict=True)
        except (OSError, TypeError) as error:
            raise SourceReleaseArchiveError(
                "supplied source manifest validator has no trusted file identity"
            ) from error
        if resolved_module_path != helper_path:
            raise SourceReleaseArchiveError(
                "supplied source manifest validator is not the repository helper"
            )
        helper = supplied
    else:
        module_name = (
            "_fonix_source_checksum_manifest_for_release_archive_"
            f"{secrets.token_hex(16)}"
        )
        helper = ModuleType(module_name)
        helper.__file__ = str(helper_path)
        helper.__package__ = ""
        sys.modules[module_name] = helper
        try:
            code = compile(
                helper_bytes,
                str(helper_path),
                "exec",
                dont_inherit=True,
            )
            exec(code, helper.__dict__)
        except Exception as error:
            raise SourceReleaseArchiveError(
                "source manifest validator cannot be imported"
            ) from error
        finally:
            sys.modules.pop(module_name, None)
    for name in (
        "MAX_ENTRY_COUNT",
        "MAX_FILE_BYTES",
        "MAX_TOTAL_BYTES",
        "MAX_MANIFEST_BYTES",
        "SourceManifestError",
        "check_manifest",
        "parse_manifest",
    ):
        if not hasattr(helper, name):
            raise SourceReleaseArchiveError(
                "source manifest validator API is incomplete"
            )
    imported_bytes, _ = _read_regular_file(
        helper_path,
        label="source manifest validator",
        maximum_bytes=MAXIMUM_TOOL_BYTES,
    )
    if imported_bytes != helper_bytes:
        raise SourceReleaseArchiveError(
            "source manifest validator changed during import"
        )
    return helper


def _schema_identity(repository: Path) -> dict[str, Any]:
    schema_path = _repository_file(repository, VALIDATION_SCHEMA_PATH)
    contents, _ = _read_regular_file(
        schema_path,
        label="source archive validation schema",
        maximum_bytes=MAXIMUM_SCHEMA_BYTES,
    )
    identity = _identity(contents)
    if identity["sha256"] != EXPECTED_VALIDATION_SCHEMA_SHA256:
        raise SourceReleaseArchiveError(
            "source archive validation schema does not match the pinned bytes"
        )
    value = _strict_json(contents, "source archive validation schema")
    if type(value) is not dict or value.get("$id") != VALIDATION_SCHEMA_ID:
        raise SourceReleaseArchiveError(
            "source archive validation schema identity is invalid"
        )
    return identity


def _tool_identities(repository: Path) -> dict[str, dict[str, Any]]:
    validator, _ = _read_regular_file(
        _repository_file(repository, VALIDATOR_PATH),
        label="source archive validator",
        maximum_bytes=MAXIMUM_TOOL_BYTES,
    )
    helper, _ = _read_regular_file(
        _repository_file(repository, SOURCE_HELPER_PATH),
        label="source manifest validator",
        maximum_bytes=MAXIMUM_TOOL_BYTES,
    )
    if hashlib.sha256(helper).hexdigest() != EXPECTED_SOURCE_HELPER_SHA256:
        raise SourceReleaseArchiveError(
            "source manifest validator does not match the pinned bytes"
        )
    return {
        "validator": _identity(validator),
        "sourceManifestValidator": _identity(helper),
    }


def _git_output(
    repository: Path,
    arguments: Iterable[str],
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    git = Path("/usr/bin/git")
    try:
        metadata = git.lstat()
    except OSError as error:
        raise SourceReleaseArchiveError(
            "trusted Git executable is unavailable"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SourceReleaseArchiveError("trusted Git executable is invalid")
    environment = {
        "GIT_NO_REPLACE_OBJECTS": "1",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                str(git),
                "-C",
                str(repository),
                *arguments,
            ],
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        if process.stdout is None:
            raise SourceReleaseArchiveError(f"{label} cannot be captured")
        deadline = time.monotonic() + 10
        chunks: list[bytes] = []
        consumed = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceReleaseArchiveError(f"{label} timed out")
            try:
                readable, _, _ = select.select([process.stdout], [], [], remaining)
            except InterruptedError:
                continue
            if not readable:
                raise SourceReleaseArchiveError(f"{label} timed out")
            chunk = os.read(
                process.stdout.fileno(),
                min(_IO_CHUNK_BYTES, maximum_bytes + 1 - consumed),
            )
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum_bytes:
                raise SourceReleaseArchiveError(
                    f"{label} exceeds the accepted output bound"
                )
            chunks.append(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceReleaseArchiveError(f"{label} timed out")
        if process.wait(timeout=remaining) != 0:
            raise SourceReleaseArchiveError(f"{label} failed")
        return b"".join(chunks)
    except SourceReleaseArchiveError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise SourceReleaseArchiveError(
            f"{label} cannot be resolved"
        ) from error
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            if process.stdout is not None:
                process.stdout.close()
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                pass


def _git_revision(repository: Path) -> str:
    output = _git_output(
        repository,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        label="repository source revision",
        maximum_bytes=128,
    )
    if len(output) > 128:
        raise SourceReleaseArchiveError(
            "repository source revision cannot be resolved"
        )
    try:
        revision = output.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise SourceReleaseArchiveError(
            "repository source revision is not ASCII"
        ) from error
    if _REVISION.fullmatch(revision) is None:
        raise SourceReleaseArchiveError("repository HEAD is not one Git commit")
    return revision


def _git_blob_id(contents: bytes) -> str:
    header = f"blob {len(contents)}\0".encode("ascii")
    return hashlib.sha1(header + contents, usedforsecurity=False).hexdigest()


def _verify_git_tree(
    repository: Path,
    *,
    revision: str,
    files: Iterable[_ExpectedFile],
    maximum_entries: int,
) -> None:
    maximum_output = min(
        MAXIMUM_ARCHIVE_BYTES,
        (maximum_entries + 1) * (MAXIMUM_PATH_BYTES + 128),
    )
    output = _git_output(
        repository,
        ("ls-tree", "-r", "-z", "--full-tree", revision),
        label="repository source tree",
        maximum_bytes=maximum_output,
    )
    if not output.endswith(b"\0"):
        raise SourceReleaseArchiveError(
            "repository source tree output is not canonical"
        )
    raw_entries = output[:-1].split(b"\0")
    if not 1 <= len(raw_entries) <= maximum_entries:
        raise SourceReleaseArchiveError(
            "repository source tree entry count is outside the accepted bound"
        )
    actual: list[tuple[str, str, str]] = []
    for raw_entry in raw_entries:
        try:
            raw_identity, raw_path = raw_entry.split(b"\t", 1)
            raw_mode, raw_kind, raw_object = raw_identity.split(b" ")
            path = raw_path.decode("ascii")
            mode = raw_mode.decode("ascii")
            kind = raw_kind.decode("ascii")
            object_id = raw_object.decode("ascii")
        except (UnicodeDecodeError, ValueError) as error:
            raise SourceReleaseArchiveError(
                "repository source tree entry is malformed"
            ) from error
        _safe_member_name(path, directory=False)
        if (
            mode not in {"100644", "100755"}
            or kind != "blob"
            or _REVISION.fullmatch(object_id) is None
        ):
            raise SourceReleaseArchiveError(
                "repository source tree contains an unsupported entry"
            )
        actual.append((path, mode, object_id))
    expected = sorted(
        (
            source_file.path,
            "100755" if source_file.executable else "100644",
            source_file.git_blob_id,
        )
        for source_file in files
    )
    if actual != expected:
        raise SourceReleaseArchiveError(
            "repository worktree source does not exactly match the declared revision"
        )


def _safe_member_name(value: str, *, directory: bool) -> str:
    encoded = value.encode("ascii", errors="strict")
    if (
        not encoded
        or len(encoded) > MAXIMUM_PATH_BYTES
        or b"\\" in encoded
        or b"\x00" in encoded
        or any(byte < 0x20 or byte == 0x7F for byte in encoded)
        or value.startswith("/")
        or "//" in value
    ):
        raise SourceReleaseArchiveError("archive member path is unsafe")
    if directory:
        if not value.endswith("/"):
            raise SourceReleaseArchiveError(
                "archive directory path is not canonical"
            )
        logical = value[:-1]
    else:
        if value.endswith("/"):
            raise SourceReleaseArchiveError("archive file path is not canonical")
        logical = value
    path = PurePosixPath(logical)
    if (
        not logical
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != logical
    ):
        raise SourceReleaseArchiveError("archive member path is unsafe")
    return logical


def _capture_source(
    repository: Path,
    helper: Any,
    expected_source_revision: str | None,
) -> _SourceSnapshot:
    revision = _git_revision(repository)
    if expected_source_revision is not None:
        if (
            type(expected_source_revision) is not str
            or _REVISION.fullmatch(expected_source_revision) is None
            or revision != expected_source_revision
        ):
            raise SourceReleaseArchiveError(
                "repository revision does not match the expected source baseline"
            )
    manifest_path = _repository_file(repository, SOURCE_MANIFEST_PATH)
    try:
        checked_digest = helper.check_manifest(repository, manifest_path)
    except helper.SourceManifestError as error:
        raise SourceReleaseArchiveError(
            f"repository source manifest is invalid: {error}"
        ) from error
    manifest, manifest_metadata = _read_regular_file(
        manifest_path,
        label="source checksum manifest",
        maximum_bytes=helper.MAX_MANIFEST_BYTES,
    )
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    if manifest_digest != checked_digest:
        raise SourceReleaseArchiveError(
            "source checksum manifest changed during validation"
        )
    try:
        parsed = helper.parse_manifest(manifest)
    except helper.SourceManifestError as error:
        raise SourceReleaseArchiveError(
            f"repository source manifest is invalid: {error}"
        ) from error

    files: list[_ExpectedFile] = []
    total_source_bytes = 0
    for relative, digest in parsed:
        contents, metadata = _read_regular_file(
            _repository_file(repository, relative),
            label=f"source path {relative}",
            maximum_bytes=helper.MAX_FILE_BYTES,
            allow_empty=True,
        )
        actual_digest = hashlib.sha256(contents).hexdigest()
        if actual_digest != digest:
            raise SourceReleaseArchiveError(
                f"source path {relative} does not match the source manifest"
            )
        executable_bits = stat.S_IMODE(metadata.st_mode) & 0o111
        if executable_bits not in {0, 0o111}:
            raise SourceReleaseArchiveError(
                f"source path {relative} has non-canonical executable semantics"
            )
        total_source_bytes += len(contents)
        if total_source_bytes > helper.MAX_TOTAL_BYTES:
            raise SourceReleaseArchiveError(
                "source tree exceeds the total byte bound"
            )
        files.append(
            _ExpectedFile(
                path=relative,
                size_bytes=len(contents),
                sha256=actual_digest,
                git_blob_id=_git_blob_id(contents),
                executable=executable_bits == 0o111,
            )
        )
    manifest_executable = stat.S_IMODE(manifest_metadata.st_mode) & 0o111
    if manifest_executable:
        raise SourceReleaseArchiveError(
            "source checksum manifest must not be executable"
        )
    files.append(
        _ExpectedFile(
            path=SOURCE_MANIFEST_PATH,
            size_bytes=len(manifest),
            sha256=manifest_digest,
            git_blob_id=_git_blob_id(manifest),
            executable=False,
        )
    )

    _verify_git_tree(
        repository,
        revision=revision,
        files=files,
        maximum_entries=helper.MAX_ENTRY_COUNT + 1,
    )

    directories: set[str] = set()
    for source_file in files:
        parts = source_file.path.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
    members = [
        _ExpectedMember(path=directory, kind="directory")
        for directory in directories
    ]
    members.extend(
        _ExpectedMember(path=source_file.path, kind="file", file=source_file)
        for source_file in files
    )
    members.sort(key=lambda member: member.archive_name)
    if not 2 <= len(members) <= MAXIMUM_MEMBER_COUNT:
        raise SourceReleaseArchiveError(
            "closed source archive member count is outside the accepted bound"
        )
    archive_names = [member.archive_name for member in members]
    for member in members:
        _safe_member_name(
            member.archive_name,
            directory=member.kind == "directory",
        )
    if len(archive_names) != len(set(archive_names)) or len(archive_names) != len(
        {name.casefold() for name in archive_names}
    ):
        raise SourceReleaseArchiveError(
            "closed source archive inventory has path collisions"
        )
    expanded_bytes = total_source_bytes + len(manifest)
    if expanded_bytes > helper.MAX_TOTAL_BYTES + helper.MAX_MANIFEST_BYTES:
        raise SourceReleaseArchiveError(
            "closed source archive expanded bytes exceed the accepted bound"
        )
    return _SourceSnapshot(
        revision=revision,
        manifest_sha256=manifest_digest,
        manifest_size_bytes=len(manifest),
        manifest_entry_count=len(parsed),
        members=tuple(members),
        expanded_bytes=expanded_bytes,
        executable_file_count=sum(source_file.executable for source_file in files),
    )


def _descriptor_digest(
    descriptor: int,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
) -> os.stat_result:
    if type(descriptor) is not int or descriptor < 0:
        raise SourceReleaseArchiveError("archive descriptor is invalid")
    if (
        type(expected_size_bytes) is not int
        or not 1 <= expected_size_bytes <= MAXIMUM_ARCHIVE_BYTES
        or type(expected_sha256) is not str
        or _SHA256.fullmatch(expected_sha256) is None
    ):
        raise SourceReleaseArchiveError("archive identity is outside the closed contract")
    try:
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as error:
        raise SourceReleaseArchiveError(
            "archive descriptor must be one seekable regular file"
        ) from error
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size != expected_size_bytes
        or before.st_nlink < 1
    ):
        raise SourceReleaseArchiveError(
            "archive descriptor identity does not match the expected regular file"
        )
    digest = hashlib.sha256()
    consumed = 0
    while True:
        try:
            chunk = os.read(
                descriptor,
                min(_IO_CHUNK_BYTES, MAXIMUM_ARCHIVE_BYTES + 1 - consumed),
            )
        except OSError as error:
            raise SourceReleaseArchiveError("archive cannot be read") from error
        if not chunk:
            break
        consumed += len(chunk)
        if consumed > MAXIMUM_ARCHIVE_BYTES:
            raise SourceReleaseArchiveError("archive exceeds its byte bound")
        digest.update(chunk)
    try:
        after = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as error:
        raise SourceReleaseArchiveError("archive cannot be rewound") from error
    if (
        consumed != expected_size_bytes
        or digest.hexdigest() != expected_sha256
        or not _same_metadata(before, after)
    ):
        raise SourceReleaseArchiveError(
            "archive bytes or identity do not match the expected reference"
        )
    return before


def _read_at(stream: Any, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or size > MAXIMUM_ARCHIVE_BYTES:
        raise SourceReleaseArchiveError(f"{label} range is invalid")
    try:
        stream.seek(offset, os.SEEK_SET)
        value = stream.read(size)
    except (OSError, ValueError) as error:
        raise SourceReleaseArchiveError(f"{label} cannot be read") from error
    if len(value) != size:
        raise SourceReleaseArchiveError(f"{label} is truncated")
    return value


def _zip_extra_timestamp(extra: bytes) -> bytes:
    if len(extra) != 9:
        raise SourceReleaseArchiveError(
            "ZIP member extra fields are outside the canonical Git contract"
        )
    identifier, size = struct.unpack_from("<HH", extra)
    payload = extra[4:]
    if (
        identifier != _ZIP_TIMESTAMP_EXTRA
        or size != 5
        or len(payload) != 5
        or payload[0] != 1
    ):
        raise SourceReleaseArchiveError(
            "ZIP member timestamp metadata is outside the canonical Git contract"
        )
    return payload[1:]


def _decode_zip_name(raw: bytes, *, directory: bool) -> str:
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise SourceReleaseArchiveError(
            "ZIP member name is not canonical ASCII"
        ) from error
    _safe_member_name(value, directory=directory)
    return value


def _parse_zip_central(
    stream: Any,
    *,
    archive_size: int,
    revision: str,
) -> tuple[list[_ZipCentralEntry], int]:
    comment = revision.encode("ascii")
    eocd_offset = archive_size - _ZIP_EOCD.size - len(comment)
    if eocd_offset < 0:
        raise SourceReleaseArchiveError("ZIP archive is truncated")
    eocd = _read_at(stream, eocd_offset, _ZIP_EOCD.size, "ZIP end record")
    (
        signature,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_size,
    ) = _ZIP_EOCD.unpack(eocd)
    if (
        signature != _ZIP_EOCD_SIGNATURE
        or disk_number != 0
        or central_disk != 0
        or disk_entries != total_entries
        or not 1 <= total_entries <= MAXIMUM_MEMBER_COUNT
        or total_entries == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
        or comment_size != len(comment)
        or central_offset + central_size != eocd_offset
        or _read_at(
            stream,
            eocd_offset + _ZIP_EOCD.size,
            comment_size,
            "ZIP revision comment",
        )
        != comment
    ):
        raise SourceReleaseArchiveError(
            "ZIP end record or revision binding is invalid"
        )

    entries: list[_ZipCentralEntry] = []
    offset = central_offset
    timestamp: bytes | None = None
    for _ in range(total_entries):
        raw_header = _read_at(stream, offset, _ZIP_CENTRAL.size, "ZIP central entry")
        fields = _ZIP_CENTRAL.unpack(raw_header)
        (
            entry_signature,
            version_made,
            version_needed,
            flags,
            compression,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_size,
            extra_size,
            member_comment_size,
            disk_start,
            internal_attributes,
            external_attributes,
            local_offset,
        ) = fields
        variable_size = name_size + extra_size + member_comment_size
        if (
            entry_signature != _ZIP_CENTRAL_SIGNATURE
            or not 1 <= name_size <= MAXIMUM_PATH_BYTES
            or variable_size > MAXIMUM_PATH_BYTES + 1024
            or member_comment_size != 0
            or disk_start != 0
            or flags != 0
            or version_needed > 20
            or compression not in _ZIP_ALLOWED_COMPRESSION
            or compressed_size == 0xFFFFFFFF
            or uncompressed_size == 0xFFFFFFFF
            or local_offset == 0xFFFFFFFF
        ):
            raise SourceReleaseArchiveError(
                "ZIP central entry is outside the canonical Git contract"
            )
        variable = _read_at(
            stream,
            offset + _ZIP_CENTRAL.size,
            variable_size,
            "ZIP central entry metadata",
        )
        name_bytes = variable[:name_size]
        extra = variable[name_size : name_size + extra_size]
        is_directory = name_bytes.endswith(b"/")
        name = _decode_zip_name(name_bytes, directory=is_directory)
        current_timestamp = _zip_extra_timestamp(extra)
        if timestamp is None:
            timestamp = current_timestamp
        elif current_timestamp != timestamp:
            raise SourceReleaseArchiveError(
                "ZIP member timestamps do not share one source epoch"
            )
        entries.append(
            _ZipCentralEntry(
                name=name,
                name_bytes=name_bytes,
                extra=extra,
                version_made=version_made,
                version_needed=version_needed,
                flags=flags,
                compression=compression,
                modified_time=modified_time,
                modified_date=modified_date,
                crc32=crc32,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                internal_attributes=internal_attributes,
                external_attributes=external_attributes,
                local_offset=local_offset,
            )
        )
        offset += _ZIP_CENTRAL.size + variable_size
    if offset != eocd_offset or offset - central_offset != central_size:
        raise SourceReleaseArchiveError("ZIP central directory length is invalid")
    return entries, central_offset


def _validate_zip_local_layout(
    stream: Any,
    entries: list[_ZipCentralEntry],
    central_offset: int,
) -> list[int]:
    cursor = 0
    source_time: tuple[int, int] | None = None
    data_offsets: list[int] = []
    for entry in entries:
        if entry.local_offset != cursor:
            raise SourceReleaseArchiveError(
                "ZIP local entries are reordered, overlapping, or contain gaps"
            )
        raw_header = _read_at(stream, cursor, _ZIP_LOCAL.size, "ZIP local entry")
        (
            signature,
            version_needed,
            flags,
            compression,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_size,
            extra_size,
        ) = _ZIP_LOCAL.unpack(raw_header)
        variable_size = name_size + extra_size
        variable = _read_at(
            stream,
            cursor + _ZIP_LOCAL.size,
            variable_size,
            "ZIP local entry metadata",
        )
        name_bytes = variable[:name_size]
        extra = variable[name_size:]
        if (
            signature != _ZIP_LOCAL_SIGNATURE
            or version_needed != entry.version_needed
            or flags != entry.flags
            or compression != entry.compression
            or modified_time != entry.modified_time
            or modified_date != entry.modified_date
            or crc32 != entry.crc32
            or compressed_size != entry.compressed_size
            or uncompressed_size != entry.uncompressed_size
            or name_bytes != entry.name_bytes
            or extra != entry.extra
        ):
            raise SourceReleaseArchiveError(
                "ZIP local and central metadata do not match"
            )
        current_time = (modified_date, modified_time)
        if source_time is None:
            source_time = current_time
        elif current_time != source_time:
            raise SourceReleaseArchiveError(
                "ZIP member timestamps do not share one source epoch"
            )
        data_offset = cursor + _ZIP_LOCAL.size + variable_size
        data_offsets.append(data_offset)
        cursor = data_offset + compressed_size
        if cursor > central_offset:
            raise SourceReleaseArchiveError(
                "ZIP member data overlaps the central directory"
            )
    if cursor != central_offset:
        raise SourceReleaseArchiveError(
            "ZIP local data does not end at the central directory"
        )
    return data_offsets


def _hash_raw_zip_member(
    stream: Any,
    entry: _ZipCentralEntry,
    data_offset: int,
    expected_size: int,
) -> str:
    stream.seek(data_offset, os.SEEK_SET)
    digest = hashlib.sha256()
    crc32 = 0
    produced = 0
    remaining = entry.compressed_size
    decompressor = (
        zlib.decompressobj(-zlib.MAX_WBITS)
        if entry.compression == zipfile.ZIP_DEFLATED
        else None
    )
    while remaining:
        chunk = stream.read(min(_IO_CHUNK_BYTES, remaining))
        if not chunk:
            raise SourceReleaseArchiveError(
                f"ZIP member {entry.name} compressed data is truncated"
            )
        remaining -= len(chunk)
        if decompressor is None:
            output = chunk
            pending = b""
        else:
            pending = chunk
            output = b""
        while True:
            if decompressor is not None:
                try:
                    output = decompressor.decompress(pending, _IO_CHUNK_BYTES)
                except zlib.error as error:
                    raise SourceReleaseArchiveError(
                        f"ZIP member {entry.name} DEFLATE stream is invalid"
                    ) from error
                if decompressor.unused_data:
                    raise SourceReleaseArchiveError(
                        f"ZIP member {entry.name} DEFLATE stream has unused data"
                    )
                next_pending = decompressor.unconsumed_tail
                if next_pending == pending and not output:
                    raise SourceReleaseArchiveError(
                        f"ZIP member {entry.name} DEFLATE stream made no progress"
                    )
                pending = next_pending
            produced += len(output)
            if produced > expected_size:
                raise SourceReleaseArchiveError(
                    f"ZIP member {entry.name} exceeds its expanded-size bound"
                )
            digest.update(output)
            crc32 = binascii.crc32(output, crc32)
            if not pending:
                break
            output = b""
        if decompressor is None:
            continue
    if decompressor is not None:
        if (
            not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
        ):
            raise SourceReleaseArchiveError(
                f"ZIP member {entry.name} DEFLATE stream is not exactly exhausted"
            )
        try:
            final_output = decompressor.flush()
        except zlib.error as error:
            raise SourceReleaseArchiveError(
                f"ZIP member {entry.name} DEFLATE stream cannot be finalized"
            ) from error
        produced += len(final_output)
        if produced > expected_size:
            raise SourceReleaseArchiveError(
                f"ZIP member {entry.name} exceeds its expanded-size bound"
            )
        digest.update(final_output)
        crc32 = binascii.crc32(final_output, crc32)
    if (
        produced != expected_size
        or crc32 & 0xFFFFFFFF != entry.crc32
        or (decompressor is None and entry.compressed_size != expected_size)
    ):
        raise SourceReleaseArchiveError(
            f"ZIP member {entry.name} failed size or CRC validation"
        )
    return digest.hexdigest()


def _inspect_zip(
    stream: Any,
    *,
    archive_size: int,
    source: _SourceSnapshot,
) -> None:
    entries, central_offset = _parse_zip_central(
        stream, archive_size=archive_size, revision=source.revision
    )
    expected_names = [member.archive_name for member in source.members]
    actual_names = [entry.name for entry in entries]
    if actual_names != expected_names:
        raise SourceReleaseArchiveError(
            "ZIP member order or closed source inventory does not match"
        )
    if len(actual_names) != len(set(actual_names)) or len(actual_names) != len(
        {name.casefold() for name in actual_names}
    ):
        raise SourceReleaseArchiveError("ZIP member names collide")
    data_offsets = _validate_zip_local_layout(stream, entries, central_offset)

    try:
        archive = zipfile.ZipFile(stream, mode="r", allowZip64=False)
    except (OSError, zipfile.BadZipFile) as error:
        raise SourceReleaseArchiveError("ZIP archive cannot be opened") from error
    try:
        infos = archive.infolist()
        if len(infos) != len(entries):
            raise SourceReleaseArchiveError("ZIP member inventory is inconsistent")
        for expected, central, info, data_offset in zip(
            source.members, entries, infos, data_offsets, strict=True
        ):
            if (
                info.filename != central.name
                or info.orig_filename != central.name
                or info.header_offset != central.local_offset
                or info.flag_bits != 0
                or info.extra != central.extra
                or info.comment != b""
                or info.compress_type != central.compression
                or info.file_size != central.uncompressed_size
                or info.compress_size != central.compressed_size
                or info.CRC != central.crc32
            ):
                raise SourceReleaseArchiveError(
                    "ZIP decoded metadata does not match its structural inventory"
                )
            create_system = central.version_made >> 8
            if expected.kind == "directory":
                if (
                    central.compression != zipfile.ZIP_STORED
                    or central.uncompressed_size != 0
                    or central.compressed_size != 0
                    or central.crc32 != 0
                    or create_system != 0
                    or central.external_attributes != 0x10
                    or not info.is_dir()
                ):
                    raise SourceReleaseArchiveError(
                        f"ZIP directory {central.name} is not canonical"
                    )
                continue
            assert expected.file is not None
            if info.is_dir() or central.uncompressed_size != expected.file.size_bytes:
                raise SourceReleaseArchiveError(
                    f"ZIP source file {central.name} has an unexpected type or size"
                )
            if expected.file.executable:
                valid_mode = (
                    create_system == 3
                    and central.external_attributes == (0o100755 << 16)
                )
            else:
                valid_mode = create_system == 0 and central.external_attributes == 0
            if not valid_mode:
                raise SourceReleaseArchiveError(
                    f"ZIP source file {central.name} has incorrect executable semantics"
                )
            if (
                _hash_raw_zip_member(
                    stream, central, data_offset, expected.file.size_bytes
                )
                != expected.file.sha256
            ):
                raise SourceReleaseArchiveError(
                    f"ZIP source file {central.name} does not match the source manifest"
                )
    finally:
        archive.close()


def _write_bounded(destination: Any, contents: bytes, *, maximum: int, used: int) -> int:
    total = used + len(contents)
    if total > maximum:
        raise SourceReleaseArchiveError("gzip expansion exceeds the tar byte bound")
    destination.write(contents)
    return total


def _decompress_one_gzip(stream: Any, archive_size: int, maximum: int) -> Any:
    header = _read_at(stream, 0, 10, "gzip header")
    if header != b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03":
        raise SourceReleaseArchiveError(
            "gzip header is outside the canonical Git archive contract"
        )
    temporary = tempfile.TemporaryFile(mode="w+b")
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    produced = 0
    try:
        stream.seek(0, os.SEEK_SET)
        consumed = 0
        while consumed < archive_size:
            chunk = stream.read(min(_IO_CHUNK_BYTES, archive_size - consumed))
            if not chunk:
                raise SourceReleaseArchiveError("gzip archive is truncated")
            consumed += len(chunk)
            pending = chunk
            while pending:
                try:
                    output = decompressor.decompress(pending, _IO_CHUNK_BYTES)
                except zlib.error as error:
                    raise SourceReleaseArchiveError("gzip stream is invalid") from error
                produced = _write_bounded(
                    temporary, output, maximum=maximum, used=produced
                )
                if decompressor.unused_data:
                    raise SourceReleaseArchiveError(
                        "gzip archive contains trailing or concatenated data"
                    )
                next_pending = decompressor.unconsumed_tail
                if next_pending == pending and not output:
                    raise SourceReleaseArchiveError("gzip decompression made no progress")
                pending = next_pending
            if decompressor.eof and consumed != archive_size:
                raise SourceReleaseArchiveError(
                    "gzip archive contains trailing or concatenated data"
                )
        if not decompressor.eof or decompressor.unused_data:
            raise SourceReleaseArchiveError("gzip archive is incomplete")
        try:
            remainder = decompressor.flush()
        except zlib.error as error:
            raise SourceReleaseArchiveError("gzip stream cannot be finalized") from error
        produced = _write_bounded(
            temporary, remainder, maximum=maximum, used=produced
        )
        temporary.flush()
        temporary.seek(0, os.SEEK_SET)
        return temporary
    except BaseException:
        temporary.close()
        raise


def _tar_c_string(field: bytes, label: str) -> bytes:
    nul = field.find(b"\x00")
    if nul < 0 or any(field[nul:]):
        raise SourceReleaseArchiveError(f"tar {label} is not canonical")
    return field[:nul]


def _tar_octal(field: bytes, label: str) -> int:
    if len(field) < 2 or field[-1:] != b"\x00" or not all(
        0x30 <= byte <= 0x37 for byte in field[:-1]
    ):
        raise SourceReleaseArchiveError(f"tar {label} is not canonical octal")
    return int(field[:-1], 8)


def _tar_header_values(header: bytes) -> dict[str, Any]:
    if len(header) != 512:
        raise SourceReleaseArchiveError("tar header is truncated")
    stored_checksum = _tar_octal(header[148:156], "checksum")
    calculated_checksum = sum(header[:148]) + 8 * 0x20 + sum(header[156:])
    if stored_checksum != calculated_checksum:
        raise SourceReleaseArchiveError("tar header checksum is invalid")
    name = _tar_c_string(header[0:100], "name")
    prefix = _tar_c_string(header[345:500], "prefix")
    if prefix:
        raw_name = prefix + b"/" + name
    else:
        raw_name = name
    if (
        header[257:263] != b"ustar\x00"
        or header[263:265] != b"00"
        or _tar_c_string(header[157:257], "link name") != b""
        or _tar_c_string(header[265:297], "owner name") != b"root"
        or _tar_c_string(header[297:329], "group name") != b"root"
        or _tar_octal(header[108:116], "user id") != 0
        or _tar_octal(header[116:124], "group id") != 0
        or _tar_octal(header[329:337], "device major") != 0
        or _tar_octal(header[337:345], "device minor") != 0
        or any(header[500:512])
    ):
        raise SourceReleaseArchiveError(
            "tar header metadata is outside the canonical Git contract"
        )
    return {
        "rawName": raw_name,
        "mode": _tar_octal(header[100:108], "mode"),
        "size": _tar_octal(header[124:136], "size"),
        "mtime": _tar_octal(header[136:148], "modification time"),
        "type": header[156:157],
    }


def _tar_region_digest(stream: Any, offset: int, size: int, label: str) -> str:
    stream.seek(offset, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = stream.read(min(_IO_CHUNK_BYTES, remaining))
        if not chunk:
            raise SourceReleaseArchiveError(f"tar source file {label} is truncated")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _require_zero_region(stream: Any, offset: int, size: int, label: str) -> None:
    stream.seek(offset, os.SEEK_SET)
    remaining = size
    while remaining:
        chunk = stream.read(min(_IO_CHUNK_BYTES, remaining))
        if len(chunk) == 0 or any(chunk):
            raise SourceReleaseArchiveError(f"{label} is not canonical zero padding")
        remaining -= len(chunk)


def _inspect_tar(stream: Any, *, tar_size: int, source: _SourceSnapshot) -> None:
    if tar_size % 512 != 0 or tar_size < 2048:
        raise SourceReleaseArchiveError("tar byte length is invalid")
    global_header = _tar_header_values(_read_at(stream, 0, 512, "tar global header"))
    expected_pax = f"52 comment={source.revision}\n".encode("ascii")
    if (
        global_header["rawName"] != b"pax_global_header"
        or global_header["type"] != b"g"
        or global_header["mode"] != 0o666
        or global_header["size"] != len(expected_pax)
        or _read_at(stream, 512, len(expected_pax), "tar global PAX record")
        != expected_pax
    ):
        raise SourceReleaseArchiveError(
            "tar global PAX revision binding is invalid"
        )
    pax_padded_size = ((len(expected_pax) + 511) // 512) * 512
    _require_zero_region(
        stream,
        512 + len(expected_pax),
        pax_padded_size - len(expected_pax),
        "tar global PAX padding",
    )
    offset = 512 + pax_padded_size
    source_mtime = global_header["mtime"]
    actual_names: list[str] = []
    for expected in source.members:
        if offset + 512 > tar_size:
            raise SourceReleaseArchiveError("tar source inventory is truncated")
        header_bytes = _read_at(stream, offset, 512, "tar member header")
        if not any(header_bytes):
            raise SourceReleaseArchiveError("tar source inventory ended early")
        header = _tar_header_values(header_bytes)
        is_directory = header["type"] == b"5"
        try:
            name = header["rawName"].decode("ascii")
        except UnicodeDecodeError as error:
            raise SourceReleaseArchiveError(
                "tar member name is not canonical ASCII"
            ) from error
        _safe_member_name(name, directory=is_directory)
        actual_names.append(name)
        if name != expected.archive_name or header["mtime"] != source_mtime:
            raise SourceReleaseArchiveError(
                "tar member order, inventory, or source epoch does not match"
            )
        data_offset = offset + 512
        if expected.kind == "directory":
            if (
                header["type"] != b"5"
                or header["mode"] != 0o755
                or header["size"] != 0
            ):
                raise SourceReleaseArchiveError(
                    f"tar directory {name} is not canonical"
                )
        else:
            assert expected.file is not None
            expected_mode = 0o755 if expected.file.executable else 0o644
            if (
                header["type"] != b"0"
                or header["mode"] != expected_mode
                or header["size"] != expected.file.size_bytes
            ):
                raise SourceReleaseArchiveError(
                    f"tar source file {name} has incorrect type, size, or mode"
                )
            if (
                _tar_region_digest(stream, data_offset, header["size"], name)
                != expected.file.sha256
            ):
                raise SourceReleaseArchiveError(
                    f"tar source file {name} does not match the source manifest"
                )
        padded_size = ((header["size"] + 511) // 512) * 512
        if data_offset + padded_size > tar_size:
            raise SourceReleaseArchiveError(f"tar member {name} is truncated")
        _require_zero_region(
            stream,
            data_offset + header["size"],
            padded_size - header["size"],
            f"tar member {name} padding",
        )
        offset = data_offset + padded_size

    expected_names = [member.archive_name for member in source.members]
    if actual_names != expected_names or len(actual_names) != len(
        {name.casefold() for name in actual_names}
    ):
        raise SourceReleaseArchiveError("tar source inventory has path collisions")
    remaining = tar_size - offset
    if (
        remaining < 1024
        or remaining > MAXIMUM_TAR_PADDING_BYTES
        or remaining % 512 != 0
    ):
        raise SourceReleaseArchiveError("tar end padding is outside the accepted bound")
    _require_zero_region(stream, offset, remaining, "tar end padding")


def _inspect_tar_gzip(
    stream: Any,
    *,
    archive_size: int,
    source: _SourceSnapshot,
    helper: Any,
) -> None:
    maximum_tar_bytes = (
        helper.MAX_TOTAL_BYTES
        + helper.MAX_MANIFEST_BYTES
        + MAXIMUM_MEMBER_COUNT * 1024
        + MAXIMUM_TAR_PADDING_BYTES
    )
    tar = _decompress_one_gzip(stream, archive_size, maximum_tar_bytes)
    try:
        tar.seek(0, os.SEEK_END)
        tar_size = tar.tell()
        tar.seek(0, os.SEEK_SET)
        _inspect_tar(tar, tar_size=tar_size, source=source)
    finally:
        tar.close()


def _source_record(source: _SourceSnapshot) -> dict[str, Any]:
    inventory = [member.inventory_value() for member in source.members]
    regular_file_count = sum(member.kind == "file" for member in source.members)
    directory_count = len(source.members) - regular_file_count
    return {
        "revision": source.revision,
        "manifestPath": SOURCE_MANIFEST_PATH,
        "manifestSha256": source.manifest_sha256,
        "manifestSizeBytes": source.manifest_size_bytes,
        "manifestEntryCount": source.manifest_entry_count,
        "memberCount": len(source.members),
        "regularFileCount": regular_file_count,
        "directoryCount": directory_count,
        "executableFileCount": source.executable_file_count,
        "expandedBytes": source.expanded_bytes,
        "inventorySha256": hashlib.sha256(_canonical_bytes(inventory)).hexdigest(),
    }


def _validate_record_shape(record: Mapping[str, Any]) -> None:
    expected_top = {
        "schemaVersion",
        "result",
        "claimStatus",
        "purpose",
        "validationScope",
        "archive",
        "source",
        "tools",
        "schemas",
        "claimBoundary",
    }
    if set(record) != expected_top:
        raise SourceReleaseArchiveError("validation record shape is invalid")
    if (
        record["schemaVersion"] != 1
        or record["result"] != "validated"
        or record["claimStatus"] != "source-closure-only"
        or record["purpose"] != "source-release-archive-closure-validation"
        or record["validationScope"] != "offline-consistency-only"
        or record["claimBoundary"] != _CLAIM_BOUNDARY
    ):
        raise SourceReleaseArchiveError("validation record constants are invalid")
    archive = record["archive"]
    source = record["source"]
    tools = record["tools"]
    schemas = record["schemas"]
    if (
        type(archive) is not dict
        or set(archive) != {"format", "mediaType", "sha256", "sizeBytes"}
        or type(source) is not dict
        or set(source)
        != {
            "revision",
            "revisionBinding",
            "manifestPath",
            "manifestSha256",
            "manifestSizeBytes",
            "manifestEntryCount",
            "memberCount",
            "regularFileCount",
            "directoryCount",
            "executableFileCount",
            "expandedBytes",
            "inventorySha256",
        }
        or type(tools) is not dict
        or set(tools) != {"validator", "sourceManifestValidator"}
        or type(schemas) is not dict
        or set(schemas) != {"validation"}
    ):
        raise SourceReleaseArchiveError("validation record nested shape is invalid")
    _canonical_bytes(record)


def _validate_archive_descriptor_impl(
    repository: Path,
    archive_descriptor: int,
    *,
    media_type: str,
    expected_sha256: str,
    expected_size_bytes: int,
    expected_source_revision: str | None = None,
    source_helper: Any | None = None,
) -> dict[str, Any]:
    repository = _repository_directory(repository)
    if media_type not in _FORMAT_BY_MEDIA_TYPE:
        raise SourceReleaseArchiveError("archive media type is outside the closed set")
    helper = _load_source_helper(repository, source_helper)
    tools_before = _tool_identities(repository)
    schema_before = _schema_identity(repository)
    source_before = _capture_source(
        repository, helper, expected_source_revision
    )
    initial_metadata = _descriptor_digest(
        archive_descriptor,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
    )
    try:
        duplicate = os.dup(archive_descriptor)
    except OSError as error:
        raise SourceReleaseArchiveError("archive descriptor cannot be retained") from error
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as stream:
            if media_type == MEDIA_TYPE_ZIP:
                if _read_at(stream, 0, 4, "ZIP signature") != b"PK\x03\x04":
                    raise SourceReleaseArchiveError(
                        "archive bytes do not match the declared ZIP media type"
                    )
                _inspect_zip(
                    stream,
                    archive_size=expected_size_bytes,
                    source=source_before,
                )
            else:
                if _read_at(stream, 0, 2, "gzip signature") != b"\x1f\x8b":
                    raise SourceReleaseArchiveError(
                        "archive bytes do not match the declared gzip media type"
                    )
                _inspect_tar_gzip(
                    stream,
                    archive_size=expected_size_bytes,
                    source=source_before,
                    helper=helper,
                )
    finally:
        try:
            os.lseek(archive_descriptor, 0, os.SEEK_SET)
        except OSError:
            pass

    final_metadata = _descriptor_digest(
        archive_descriptor,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
    )
    source_after = _capture_source(repository, helper, expected_source_revision)
    tools_after = _tool_identities(repository)
    schema_after = _schema_identity(repository)
    if (
        not _same_metadata(initial_metadata, final_metadata)
        or source_before != source_after
        or tools_before != tools_after
        or schema_before != schema_after
    ):
        raise SourceReleaseArchiveError(
            "archive, source, tool, or schema identity changed during validation"
        )

    archive_format, revision_binding = _FORMAT_BY_MEDIA_TYPE[media_type]
    source_record = _source_record(source_before)
    source_record["revisionBinding"] = revision_binding
    record: dict[str, Any] = {
        "schemaVersion": 1,
        "result": "validated",
        "claimStatus": "source-closure-only",
        "purpose": "source-release-archive-closure-validation",
        "validationScope": "offline-consistency-only",
        "archive": {
            "format": archive_format,
            "mediaType": media_type,
            "sha256": expected_sha256,
            "sizeBytes": expected_size_bytes,
        },
        "source": source_record,
        "tools": tools_before,
        "schemas": {"validation": schema_before},
        "claimBoundary": _CLAIM_BOUNDARY,
    }
    _validate_record_shape(record)
    return record


def validate_archive_descriptor(
    repository: Path,
    archive_descriptor: int,
    *,
    media_type: str,
    expected_sha256: str,
    expected_size_bytes: int,
    expected_source_revision: str | None = None,
    source_helper: Any | None = None,
) -> dict[str, Any]:
    """Validate an already retained archive descriptor and return its record.

    The caller retains ownership of ``archive_descriptor``.  The descriptor is
    left open and rewound to offset zero on both success and ordinary failure.
    """

    try:
        return _validate_archive_descriptor_impl(
            repository,
            archive_descriptor,
            media_type=media_type,
            expected_sha256=expected_sha256,
            expected_size_bytes=expected_size_bytes,
            expected_source_revision=expected_source_revision,
            source_helper=source_helper,
        )
    except SourceReleaseArchiveError:
        raise
    except Exception as error:
        raise SourceReleaseArchiveError(
            "source archive validation failed unexpectedly"
        ) from error
    finally:
        if type(archive_descriptor) is int and archive_descriptor >= 0:
            try:
                os.lseek(archive_descriptor, 0, os.SEEK_SET)
            except OSError:
                pass


def _named_metadata(directory_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SourceReleaseArchiveError(
            "output entry cannot be inspected safely"
        ) from error


def _verify_parent_identity(
    parent: Path,
    descriptor: int,
    expected: os.stat_result,
) -> None:
    try:
        current = parent.lstat()
        opened = os.fstat(descriptor)
        resolved = parent.resolve(strict=True)
    except OSError as error:
        raise SourceReleaseArchiveError(
            "validation output parent cannot be reverified"
        ) from error
    if (
        resolved != parent
        or stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_metadata(current, opened)
        or not _same_inode(opened, expected)
    ):
        raise SourceReleaseArchiveError(
            "validation output parent identity changed"
        )


def _publish_no_replace(
    path: Path,
    record: Mapping[str, Any],
    *,
    expected_parent: os.stat_result,
    repository: Path,
) -> None:
    encoded = (
        json.dumps(
            record,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    if not 1 <= len(encoded) <= MAXIMUM_OUTPUT_BYTES:
        raise SourceReleaseArchiveError("validation output exceeds its byte bound")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SourceReleaseArchiveError(
            "this host cannot publish without following links"
        )
    parent_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    output_flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    parent_descriptor: int | None = None
    descriptor: int | None = None
    reserved = False
    content_complete = False
    try:
        try:
            parent_descriptor = os.open(path.parent, parent_flags)
        except OSError as error:
            raise SourceReleaseArchiveError(
                "validation output parent cannot be opened safely"
            ) from error
        _verify_parent_identity(path.parent, parent_descriptor, expected_parent)
        if _is_descendant(path, repository):
            raise SourceReleaseArchiveError(
                "validation output must remain outside the repository"
            )
        try:
            descriptor = os.open(path.name, output_flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError as error:
            raise SourceReleaseArchiveError(
                "validation output already exists"
            ) from error
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise SourceReleaseArchiveError(
                    "validation output already exists"
                ) from error
            raise SourceReleaseArchiveError(
                "validation output cannot be reserved safely"
            ) from error
        reserved = True
        os.fchmod(descriptor, 0o600)
        initial = os.fstat(descriptor)
        named = _named_metadata(parent_descriptor, path.name)
        if (
            not stat.S_ISREG(initial.st_mode)
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_size != 0
            or named is None
            or not _same_metadata(initial, named)
        ):
            raise SourceReleaseArchiveError(
                "reserved validation output identity changed"
            )
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise SourceReleaseArchiveError("validation output write made no progress")
            remaining = remaining[written:]
        written_metadata = os.fstat(descriptor)
        named = _named_metadata(parent_descriptor, path.name)
        if (
            written_metadata.st_size != len(encoded)
            or named is None
            or not _same_metadata(written_metadata, named)
        ):
            raise SourceReleaseArchiveError(
                "validation output identity changed while writing"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        verified = bytearray()
        while len(verified) <= len(encoded):
            chunk = os.read(descriptor, min(64 * 1024, len(encoded) + 1 - len(verified)))
            if not chunk:
                break
            verified.extend(chunk)
        verified_metadata = os.fstat(descriptor)
        named = _named_metadata(parent_descriptor, path.name)
        if (
            bytes(verified) != encoded
            or not _same_metadata(written_metadata, verified_metadata)
            or named is None
            or not _same_metadata(verified_metadata, named)
        ):
            raise SourceReleaseArchiveError(
                "validation output failed retained-descriptor verification"
            )
        content_complete = True
        try:
            os.fsync(descriptor)
            os.fsync(parent_descriptor)
        except OSError as error:
            raise SourceReleaseArchiveError(
                "validation output is complete but durability sync failed; "
                "the output was preserved"
            ) from error
        _verify_parent_identity(path.parent, parent_descriptor, expected_parent)
        final = os.fstat(descriptor)
        named = _named_metadata(parent_descriptor, path.name)
        try:
            absolute = path.lstat()
        except OSError as error:
            raise SourceReleaseArchiveError(
                "validation output left its requested path"
            ) from error
        if (
            not _same_metadata(verified_metadata, final)
            or named is None
            or not _same_metadata(final, named)
            or not _same_metadata(final, absolute)
            or _is_descendant(path, repository)
        ):
            raise SourceReleaseArchiveError(
                "validation output identity changed after durability sync"
            )
    except Exception as error:
        if reserved and not content_complete:
            raise SourceReleaseArchiveError(
                "reserved validation output is incomplete; it was preserved and "
                "must be inspected or removed before retry"
            ) from error
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def validate_archive_path(
    *,
    repository: Path,
    archive: Path,
    media_type: str,
    expected_sha256: str,
    output: Path,
) -> dict[str, Any]:
    """Validate one external archive path and publish a new record."""

    repository = _repository_directory(repository)
    archive, archive_metadata = _canonical_existing_file(archive, "source archive")
    output, output_parent = _canonical_new_file(output, "validation output")
    if _is_descendant(archive, repository):
        raise SourceReleaseArchiveError("source archive must be outside the repository")
    if _is_descendant(output, repository):
        raise SourceReleaseArchiveError(
            "validation output must be outside the repository"
        )
    if archive == output:
        raise SourceReleaseArchiveError("source archive and output must be disjoint")
    if not hasattr(os, "O_NOFOLLOW"):
        raise SourceReleaseArchiveError(
            "this host cannot enforce no-follow archive reads"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(archive, flags)
    except OSError as error:
        raise SourceReleaseArchiveError("source archive cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if not _same_metadata(archive_metadata, opened):
            raise SourceReleaseArchiveError(
                "source archive changed before validation"
            )
        record = validate_archive_descriptor(
            repository,
            descriptor,
            media_type=media_type,
            expected_sha256=expected_sha256,
            expected_size_bytes=archive_metadata.st_size,
        )
        retained = os.fstat(descriptor)
        try:
            named = archive.lstat()
        except OSError as error:
            raise SourceReleaseArchiveError(
                "source archive disappeared after validation"
            ) from error
        if (
            not _same_metadata(archive_metadata, retained)
            or not _same_metadata(retained, named)
        ):
            raise SourceReleaseArchiveError(
                "source archive changed during validation"
            )
        _publish_no_replace(
            output,
            record,
            expected_parent=output_parent,
            repository=repository,
        )
        retained_after = os.fstat(descriptor)
        named_after = archive.lstat()
        if (
            not _same_metadata(retained, retained_after)
            or not _same_metadata(retained_after, named_after)
        ):
            raise SourceReleaseArchiveError(
                "source archive changed during output publication"
            )
        return record
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--media-type",
        choices=tuple(_FORMAT_BY_MEDIA_TYPE),
        required=True,
    )
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        record = validate_archive_path(
            repository=arguments.repository,
            archive=arguments.archive,
            media_type=arguments.media_type,
            expected_sha256=arguments.archive_sha256,
            output=arguments.output,
        )
    except (
        SourceReleaseArchiveError,
        FileNotFoundError,
        OSError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
        zlib.error,
    ) as error:
        print(f"source release archive error: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "result": record["result"],
                "revision": record["source"]["revision"],
                "memberCount": record["source"]["memberCount"],
                "inventorySha256": record["source"]["inventorySha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
