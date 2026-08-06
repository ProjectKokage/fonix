#!/usr/bin/env python3
"""Download and safely stage the exact lock-pinned macOS arm64 ORT dylib."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import BinaryIO, Mapping
import urllib.parse
import urllib.request


MAX_LOCK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
PINNED_VERSION = "1.27.1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class ArtifactVerificationError(RuntimeError):
    """The lock, download, or staged runtime failed closed validation."""


@dataclass(frozen=True)
class ExpectedFile:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ExpectedSymlink:
    path: str
    target: str


@dataclass(frozen=True)
class PinnedRuntime:
    artifact_id: str
    url: str
    archive_sha256: str
    archive_size_bytes: int
    runtime: ExpectedFile
    expected_files: tuple[ExpectedFile, ...]
    expected_symlinks: tuple[ExpectedSymlink, ...]


def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactVerificationError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ArtifactVerificationError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ArtifactVerificationError(f"{label} must be an array")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArtifactVerificationError(f"{label} must be a non-empty string")
    if any(ord(character) < 0x20 for character in value):
        raise ArtifactVerificationError(f"{label} contains a control character")
    return value


def _positive_integer(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtifactVerificationError(f"{label} must be an integer")
    if value <= 0 or value > maximum:
        raise ArtifactVerificationError(
            f"{label} must be between 1 and {maximum}; found {value}"
        )
    return value


def _sha256(value: object, label: str) -> str:
    digest = _string(value, label)
    if SHA256_RE.fullmatch(digest) is None:
        raise ArtifactVerificationError(f"{label} must be lowercase SHA-256")
    return digest


def _canonical_path(value: object, label: str) -> str:
    path = _string(value, label)
    if "\\" in path or path.startswith("/"):
        raise ArtifactVerificationError(f"{label} must be a relative POSIX path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ArtifactVerificationError(f"{label} is not canonical")
    return "/".join(parts)


def _regular_file(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise ArtifactVerificationError(f"{label} is missing: {path}") from error
    if not stat.S_ISREG(mode):
        raise ArtifactVerificationError(f"{label} must be a regular file: {path}")


def _expected_file(value: object, label: str) -> ExpectedFile:
    item = _mapping(value, label)
    return ExpectedFile(
        path=_canonical_path(item.get("path"), f"{label}.path"),
        sha256=_sha256(item.get("sha256"), f"{label}.sha256"),
        size_bytes=_positive_integer(
            item.get("size_bytes"), f"{label}.size_bytes", MAX_ARCHIVE_BYTES
        ),
    )


def load_pinned_runtime(lock_path: Path) -> PinnedRuntime:
    lock_path = lock_path.resolve(strict=True)
    _regular_file(lock_path, "native version lock")
    contents = lock_path.read_bytes()
    if len(contents) > MAX_LOCK_BYTES:
        raise ArtifactVerificationError("native version lock exceeds 1 MiB")
    try:
        root = json.loads(
            contents.decode("utf-8"), object_pairs_hook=_without_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactVerificationError(
            "native version lock must be strict UTF-8 JSON"
        ) from error
    root_object = _mapping(root, "lock")

    floor = _mapping(
        _mapping(root_object.get("onnxruntime"), "onnxruntime").get(
            "compatibility_floor"
        ),
        "onnxruntime.compatibility_floor",
    )
    header_version = _string(
        _mapping(floor.get("header"), "compatibility header").get("version"),
        "compatibility header version",
    )
    if header_version != PINNED_VERSION:
        raise ArtifactVerificationError(
            f"macOS integration is pinned to ORT {PINNED_VERSION}; lock has "
            f"{header_version}"
        )

    artifacts = _list(root_object.get("artifacts"), "artifacts")
    candidates: list[Mapping[str, object]] = []
    for index, raw_artifact in enumerate(artifacts):
        artifact = _mapping(raw_artifact, f"artifacts[{index}]")
        target = _mapping(artifact.get("target"), f"artifacts[{index}].target")
        if (
            target.get("os") == "macos"
            and target.get("architecture") == "arm64"
            and target.get("variant") == "default"
            and artifact.get("flavor") == "cpu"
        ):
            candidates.append(artifact)
    if len(candidates) != 1:
        raise ArtifactVerificationError(
            "lock must contain exactly one macOS/arm64/default CPU artifact; "
            f"found {len(candidates)}"
        )

    artifact = candidates[0]
    artifact_id = _string(artifact.get("id"), "artifact.id")
    expected_id = f"onnxruntime-{PINNED_VERSION}-macos-arm64-cpu"
    if artifact_id != expected_id:
        raise ArtifactVerificationError(
            f"expected artifact id {expected_id}; found {artifact_id}"
        )
    if artifact.get("runtime_mode") != "bundled":
        raise ArtifactVerificationError(
            "macOS arm64 CPU artifact must declare bundled runtime mode"
        )
    target = _mapping(artifact.get("target"), "artifact.target")
    if target.get("min_os") != "14.0":
        raise ArtifactVerificationError(
            "macOS arm64 ORT integration artifact must retain min_os 14.0"
        )

    source = _mapping(artifact.get("source"), "artifact.source")
    if source.get("archive") != "tgz":
        raise ArtifactVerificationError("macOS ORT archive must be tgz")
    revision = _string(source.get("source_revision"), "source_revision")
    if REVISION_RE.fullmatch(revision) is None:
        raise ArtifactVerificationError("source_revision must be a 40-byte Git SHA")
    url = _string(source.get("url"), "artifact.source.url")
    parsed_url = urllib.parse.urlsplit(url)
    expected_url = (
        "https://github.com/microsoft/onnxruntime/releases/download/"
        f"v{PINNED_VERSION}/onnxruntime-osx-arm64-{PINNED_VERSION}.tgz"
    )
    if url != expected_url or "latest" in url.lower():
        raise ArtifactVerificationError(
            "macOS ORT URL must be the exact versioned official release URL"
        )
    if (
        parsed_url.scheme != "https"
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ArtifactVerificationError("macOS ORT URL is not canonical HTTPS")

    expected_files = tuple(
        _expected_file(value, f"expected_files[{index}]")
        for index, value in enumerate(
            _list(artifact.get("expected_files"), "expected_files")
        )
    )
    expected_paths = [item.path for item in expected_files]
    if len(expected_paths) != len(set(expected_paths)):
        raise ArtifactVerificationError("expected_files contains duplicate paths")
    runtime_path = (
        f"onnxruntime-osx-arm64-{PINNED_VERSION}/lib/"
        f"libonnxruntime.{PINNED_VERSION}.dylib"
    )
    runtime_matches = [item for item in expected_files if item.path == runtime_path]
    if len(runtime_matches) != 1:
        raise ArtifactVerificationError(
            f"expected_files must pin exactly one {runtime_path}"
        )

    expected_symlinks = tuple(
        ExpectedSymlink(
            path=_canonical_path(value_object.get("path"), f"symlink[{index}].path"),
            target=_canonical_path(
                value_object.get("target"), f"symlink[{index}].target"
            ),
        )
        for index, raw_value in enumerate(
            _list(artifact.get("expected_symlinks"), "expected_symlinks")
        )
        for value_object in [_mapping(raw_value, f"expected_symlinks[{index}]")]
    )
    symlink_paths = [item.path for item in expected_symlinks]
    if len(symlink_paths) != len(set(symlink_paths)):
        raise ArtifactVerificationError("expected_symlinks contains duplicate paths")

    return PinnedRuntime(
        artifact_id=artifact_id,
        url=url,
        archive_sha256=_sha256(source.get("sha256"), "artifact.source.sha256"),
        archive_size_bytes=_positive_integer(
            source.get("size_bytes"),
            "artifact.source.size_bytes",
            MAX_ARCHIVE_BYTES,
        ),
        runtime=runtime_matches[0],
        expected_files=expected_files,
        expected_symlinks=expected_symlinks,
    )


def _copy_and_hash(
    source: BinaryIO,
    destination: BinaryIO | None = None,
    *,
    maximum: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size > maximum:
            raise ArtifactVerificationError(
                f"stream exceeded its locked maximum of {maximum} bytes"
            )
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return size, digest.hexdigest()


def download_archive(runtime: PinnedRuntime, destination: Path) -> None:
    request = urllib.request.Request(
        runtime.url,
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "fonix-lock-pinned-ci/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status_code = getattr(response, "status", 200)
            if status_code != 200:
                raise ArtifactVerificationError(
                    f"ORT download returned HTTP {status_code}"
                )
            final_url = urllib.parse.urlsplit(response.geturl())
            if final_url.scheme != "https":
                raise ArtifactVerificationError(
                    "ORT download redirected away from HTTPS"
                )
            content_encoding = response.headers.get("Content-Encoding", "identity")
            if content_encoding.lower() not in {"", "identity"}:
                raise ArtifactVerificationError(
                    f"ORT download used unexpected content encoding {content_encoding}"
                )
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError as error:
                    raise ArtifactVerificationError(
                        "ORT download Content-Length is not an integer"
                    ) from error
                if declared_length != runtime.archive_size_bytes:
                    raise ArtifactVerificationError(
                        "ORT download Content-Length does not match the lock"
                    )
            with destination.open("xb") as output:
                size, digest = _copy_and_hash(
                    response, output, maximum=runtime.archive_size_bytes
                )
                output.flush()
                os.fsync(output.fileno())
    except ArtifactVerificationError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise ArtifactVerificationError("could not download pinned ORT archive") from error

    if size != runtime.archive_size_bytes or digest != runtime.archive_sha256:
        destination.unlink(missing_ok=True)
        raise ArtifactVerificationError(
            "downloaded ORT archive size or SHA-256 does not match the lock"
        )


def retain_archive_in_cache(
    archive_path: Path, runtime: PinnedRuntime, cache_directory: Path
) -> Path:
    """Copy one verified archive into an offline build-hook cache directory."""

    archive_path = archive_path.resolve(strict=True)
    _regular_file(archive_path, "downloaded ORT archive")
    archive_name = PurePosixPath(urllib.parse.urlsplit(runtime.url).path).name
    expected_name = f"onnxruntime-osx-arm64-{PINNED_VERSION}.tgz"
    if archive_name != expected_name:
        raise ArtifactVerificationError(
            f"pinned ORT archive basename must be {expected_name}"
        )

    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_directory = cache_directory.resolve(strict=True)
    destination = cache_directory / archive_name
    if destination.exists() or destination.is_symlink():
        _regular_file(destination, "offline ORT archive cache entry")
        with destination.open("rb") as stream:
            size, digest = _copy_and_hash(
                stream, maximum=runtime.archive_size_bytes
            )
        if size != runtime.archive_size_bytes or digest != runtime.archive_sha256:
            raise ArtifactVerificationError(
                "existing offline ORT archive cache entry does not match the lock"
            )
        return destination.resolve(strict=True)

    temporary = cache_directory / f".{archive_name}.tmp"
    try:
        with archive_path.open("rb") as source, temporary.open("xb") as output:
            size, digest = _copy_and_hash(
                source, output, maximum=runtime.archive_size_bytes
            )
            output.flush()
            os.fsync(output.fileno())
        if size != runtime.archive_size_bytes or digest != runtime.archive_sha256:
            raise ArtifactVerificationError(
                "offline ORT archive cache copy does not match the lock"
            )
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination.resolve(strict=True)


def _member_map(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members = archive.getmembers()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ArtifactVerificationError(
            f"ORT archive exceeds {MAX_ARCHIVE_MEMBERS} members"
        )
    result: dict[str, tarfile.TarInfo] = {}
    total_size = 0
    for member in members:
        canonical = _canonical_path(member.name.rstrip("/"), "archive member")
        if canonical in result:
            raise ArtifactVerificationError(
                f"ORT archive contains duplicate member {canonical}"
            )
        result[canonical] = member
        if member.size < 0 or member.size > MAX_UNCOMPRESSED_BYTES - total_size:
            raise ArtifactVerificationError(
                "ORT archive exceeds the uncompressed size budget"
            )
        total_size += member.size
        if member.isdir() or member.isreg():
            continue
        if member.issym() or member.islnk():
            _canonical_path(member.linkname, f"archive link {canonical}")
            continue
        raise ArtifactVerificationError(
            f"ORT archive contains unsupported member type at {canonical}"
        )
    return result


def _verify_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    expected: ExpectedFile,
) -> None:
    if not member.isreg():
        raise ArtifactVerificationError(f"{expected.path} is not a regular file")
    if member.size != expected.size_bytes:
        raise ArtifactVerificationError(f"{expected.path} size does not match the lock")
    stream = archive.extractfile(member)
    if stream is None:
        raise ArtifactVerificationError(f"could not read {expected.path}")
    with stream:
        size, digest = _copy_and_hash(stream, maximum=expected.size_bytes)
    if size != expected.size_bytes or digest != expected.sha256:
        raise ArtifactVerificationError(
            f"{expected.path} contents do not match the lock"
        )


def stage_runtime(
    archive_path: Path,
    runtime: PinnedRuntime,
    output_directory: Path,
) -> Path:
    archive_path = archive_path.resolve(strict=True)
    _regular_file(archive_path, "downloaded ORT archive")
    archive_size = archive_path.stat().st_size
    if archive_size != runtime.archive_size_bytes:
        raise ArtifactVerificationError("ORT archive size changed before extraction")
    with archive_path.open("rb") as stream:
        _, archive_digest = _copy_and_hash(stream, maximum=runtime.archive_size_bytes)
    if archive_digest != runtime.archive_sha256:
        raise ArtifactVerificationError("ORT archive digest changed before extraction")

    if output_directory.exists() or output_directory.is_symlink():
        raise ArtifactVerificationError(
            f"output directory must not already exist: {output_directory}"
        )
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    output_directory.mkdir(mode=0o700)
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = _member_map(archive)
            for expected in runtime.expected_files:
                member = members.get(expected.path)
                if member is None:
                    raise ArtifactVerificationError(
                        f"ORT archive is missing locked file {expected.path}"
                    )
                _verify_member(archive, member, expected)
            for expected in runtime.expected_symlinks:
                member = members.get(expected.path)
                if member is None or not member.issym():
                    raise ArtifactVerificationError(
                        f"ORT archive is missing locked symlink {expected.path}"
                    )
                if member.linkname != expected.target:
                    raise ArtifactVerificationError(
                        f"ORT archive symlink target changed for {expected.path}"
                    )

            runtime_member = members[runtime.runtime.path]
            source = archive.extractfile(runtime_member)
            if source is None:
                raise ArtifactVerificationError("could not stage locked ORT dylib")
            destination = output_directory / PurePosixPath(runtime.runtime.path).name
            temporary_destination = output_directory / ".runtime.tmp"
            with source, temporary_destination.open("xb") as output:
                size, digest = _copy_and_hash(
                    source, output, maximum=runtime.runtime.size_bytes
                )
                output.flush()
                os.fsync(output.fileno())
            if size != runtime.runtime.size_bytes or digest != runtime.runtime.sha256:
                raise ArtifactVerificationError(
                    "staged ORT dylib size or SHA-256 does not match the lock"
                )
            temporary_destination.chmod(0o755)
            temporary_destination.replace(destination)
    except Exception:
        shutil.rmtree(output_directory, ignore_errors=True)
        raise
    return destination.resolve(strict=True)


def _write_github_output(path: Path, name: str, value: Path) -> None:
    rendered = str(value)
    if any(character in rendered for character in "\r\n"):
        raise ArtifactVerificationError("GitHub output path contains a newline")
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"{name}={rendered}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path("native/versions.lock.yaml"),
        help="strict-JSON native version lock",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new directory in which to stage only the verified dylib",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        help="optional GitHub Actions output file for the ort_path value",
    )
    parser.add_argument(
        "--archive-cache-dir",
        type=Path,
        help=(
            "optional directory in which to retain the verified source archive "
            "for the offline Dart build hook"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    cached_archive: Path | None = None
    try:
        runtime = load_pinned_runtime(arguments.lock)
        with tempfile.TemporaryDirectory(prefix="fonix-ort-download-") as temporary:
            archive_path = Path(temporary) / "onnxruntime.tgz"
            download_archive(runtime, archive_path)
            if arguments.archive_cache_dir is not None:
                cached_archive = retain_archive_in_cache(
                    archive_path, runtime, arguments.archive_cache_dir.resolve()
                )
            staged_path = stage_runtime(
                archive_path, runtime, arguments.output_dir.resolve()
            )
        if arguments.github_output is not None:
            _write_github_output(arguments.github_output, "ort_path", staged_path)
            if cached_archive is not None:
                _write_github_output(
                    arguments.github_output,
                    "ort_archive_dir",
                    cached_archive.parent,
                )
    except (ArtifactVerificationError, OSError, tarfile.TarError) as error:
        print(f"pinned ORT staging failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Staged {runtime.artifact_id} as {staged_path} after exact size/SHA checks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
