#!/usr/bin/env python3
"""Build sherpa-onnx against one exact application-owned Android ORT.

This driver never downloads source, ONNX Runtime, an NDK, or a QNN SDK.  It
archives an exact commit from a caller-provided local git checkout, applies an
exact ordered patch set, invokes ABI-specific pinned scripts with only external
ORT paths, inspects the resulting JNI ELF files, and writes a deterministic
receipt.  A successful receipt is evidence about those exact local inputs; it
is not target-device or provider-qualification evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import Any, Iterable


sys.dont_write_bytecode = True
REPOSITORY = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY / "templates/android/verify_native_libs.py"
_VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "fonix_android_native_verifier", VERIFIER_PATH
)
if _VERIFIER_SPEC is None or _VERIFIER_SPEC.loader is None:
    raise RuntimeError("could not load the Android native-library verifier")
VERIFIER = importlib.util.module_from_spec(_VERIFIER_SPEC)
_VERIFIER_SPEC.loader.exec_module(VERIFIER)


MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_SOURCE_FILES = 200_000
MAX_SOURCE_BYTES = 8 * 1024 * 1024 * 1024
MAX_PATCH_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 32 * 1024 * 1024
MAX_QNN_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_BUILD_OUTPUT_BYTES = 512 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
ABIS = frozenset(VERIFIER.ANDROID_ABIS)


class AlignedBuildError(RuntimeError):
    """The inputs cannot support a reproducible aligned sherpa build."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AlignedBuildError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _exact_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise AlignedBuildError(f"{label} has an unexpected field set")


def _token(value: Any, label: str) -> str:
    if not isinstance(value, str) or TOKEN.fullmatch(value) is None:
        raise AlignedBuildError(f"{label} must be a bounded token")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise AlignedBuildError(f"{label} must be a lowercase SHA-256")
    return value


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AlignedBuildError(f"{label} must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AlignedBuildError(f"{label} must be a canonical relative path")
    if len(value.encode("utf-8")) > 1024:
        raise AlignedBuildError(f"{label} exceeds the path bound")
    return value


def _regular_file(path: Path, label: str, maximum: int) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise AlignedBuildError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(status.st_mode):
        raise AlignedBuildError(f"{label} is not a regular file: {path}")
    if status.st_size <= 0 or status.st_size > maximum:
        raise AlignedBuildError(f"{label} size is outside its bound")
    return path


def _directory(path: Path, label: str) -> Path:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise AlignedBuildError(f"missing {label}: {path}") from error
    if not stat.S_ISDIR(status.st_mode):
        raise AlignedBuildError(f"{label} is not a directory: {path}")
    return path


def _child_without_symlinks(root: Path, relative: str, label: str) -> Path:
    candidate = root
    for part in PurePosixPath(relative).parts:
        candidate = candidate / part
        try:
            status = candidate.lstat()
        except FileNotFoundError as error:
            raise AlignedBuildError(f"missing {label}: {candidate}") from error
        if stat.S_ISLNK(status.st_mode):
            raise AlignedBuildError(f"{label} traverses a symbolic link")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    return {"sizeBytes": path.stat().st_size, "sha256": _sha256(path)}


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    _regular_file(path, label, MAX_JSON_BYTES)
    raw = path.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlignedBuildError(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise AlignedBuildError(f"{label} must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    label: str,
    capture: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except OSError as error:
        raise AlignedBuildError(f"could not run {label}: {error}") from error
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")[-4096:]
        raise AlignedBuildError(f"{label} failed ({result.returncode}): {stderr}")
    return result


def _parse_keyed(values: list[str], label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, separator, raw_path = value.partition("=")
        if not separator or not key or not raw_path:
            raise AlignedBuildError(f"{label} must use KEY=/absolute/path")
        if key in result:
            raise AlignedBuildError(f"duplicate {label} key {key!r}")
        path = Path(raw_path)
        if not path.is_absolute():
            raise AlignedBuildError(f"{label} paths must be absolute")
        result[key] = path
    return result


def _validate_file_identity(
    root: Path,
    record: dict[str, Any],
    *,
    label: str,
    maximum: int,
) -> tuple[Path, dict[str, Any]]:
    _exact_keys(record, {"path", "sizeBytes", "sha256"}, label)
    relative = _relative_path(record["path"], f"{label}.path")
    size = record["sizeBytes"]
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > maximum:
        raise AlignedBuildError(f"{label}.sizeBytes is outside its bound")
    expected_hash = _digest(record["sha256"], f"{label}.sha256")
    candidate = _child_without_symlinks(root, relative, label)
    _regular_file(candidate, label, maximum)
    if candidate.stat().st_size != size or _sha256(candidate) != expected_hash:
        raise AlignedBuildError(f"{label} identity does not match the plan")
    return candidate, {"path": relative, "sizeBytes": size, "sha256": expected_hash}


def _validate_plan(plan: dict[str, Any]) -> None:
    _exact_keys(
        plan,
        {"schemaVersion", "sherpa", "android", "onnxRuntime", "patches", "qnn"},
        "aligned build plan",
    )
    if plan["schemaVersion"] != 1:
        raise AlignedBuildError("aligned build plan schemaVersion must be 1")

    sherpa = plan["sherpa"]
    if not isinstance(sherpa, dict):
        raise AlignedBuildError("sherpa must be an object")
    _exact_keys(sherpa, {"repository", "revision", "buildsByAbi"}, "sherpa")
    repository = sherpa["repository"]
    if (
        not isinstance(repository, str)
        or not repository.startswith("https://")
        or len(repository) > 2048
        or repository.endswith("/")
    ):
        raise AlignedBuildError("sherpa.repository must be a bounded canonical HTTPS URL")
    if not isinstance(sherpa["revision"], str) or REVISION.fullmatch(sherpa["revision"]) is None:
        raise AlignedBuildError("sherpa.revision must be a full lowercase commit SHA")
    builds = sherpa["buildsByAbi"]
    if not isinstance(builds, dict) or not builds:
        raise AlignedBuildError("sherpa.buildsByAbi must be a non-empty object")
    if any(abi not in ABIS for abi in builds):
        raise AlignedBuildError("sherpa.buildsByAbi contains an unsupported ABI")
    for abi, build in builds.items():
        if not isinstance(build, dict):
            raise AlignedBuildError(f"sherpa build for {abi} must be an object")
        _exact_keys(build, {"script", "output"}, f"sherpa build for {abi}")
        _relative_path(build["script"], f"sherpa build script for {abi}")
        output = _relative_path(build["output"], f"sherpa output for {abi}")
        if PurePosixPath(output).name != VERIFIER.SHERPA_JNI_NAME:
            raise AlignedBuildError(f"sherpa output for {abi} must be {VERIFIER.SHERPA_JNI_NAME}")

    android = plan["android"]
    if not isinstance(android, dict):
        raise AlignedBuildError("android must be an object")
    _exact_keys(android, {"minimumApi", "ndk"}, "android")
    if not isinstance(android["minimumApi"], int) or isinstance(android["minimumApi"], bool) or not 24 <= android["minimumApi"] <= 100:
        raise AlignedBuildError("android.minimumApi must be between 24 and 100")
    ndk = android["ndk"]
    if not isinstance(ndk, dict):
        raise AlignedBuildError("android.ndk must be an object")
    _exact_keys(
        ndk,
        {
            "revision",
            "hostTag",
            "sourcePropertiesSha256",
            "clangPath",
            "clangSha256",
            "cmakeToolchainPath",
            "cmakeToolchainSha256",
        },
        "android.ndk",
    )
    _token(ndk["revision"], "android.ndk.revision")
    _token(ndk["hostTag"], "android.ndk.hostTag")
    _digest(ndk["sourcePropertiesSha256"], "android.ndk.sourcePropertiesSha256")
    _relative_path(ndk["clangPath"], "android.ndk.clangPath")
    _digest(ndk["clangSha256"], "android.ndk.clangSha256")
    _relative_path(ndk["cmakeToolchainPath"], "android.ndk.cmakeToolchainPath")
    _digest(ndk["cmakeToolchainSha256"], "android.ndk.cmakeToolchainSha256")

    ort = plan["onnxRuntime"]
    if not isinstance(ort, dict):
        raise AlignedBuildError("onnxRuntime must be an object")
    _exact_keys(ort, {"version", "requiredApi", "byAbi"}, "onnxRuntime")
    if not isinstance(ort["version"], str) or SEMVER.fullmatch(ort["version"]) is None:
        raise AlignedBuildError("onnxRuntime.version must be strict semver")
    if ort["requiredApi"] != 27:
        raise AlignedBuildError("this source snapshot requires ONNX Runtime C API 27")
    by_abi = ort["byAbi"]
    if not isinstance(by_abi, dict) or set(by_abi) != set(builds):
        raise AlignedBuildError("onnxRuntime.byAbi must exactly match sherpa.buildsByAbi")
    for abi, record in by_abi.items():
        if not isinstance(record, dict):
            raise AlignedBuildError(f"onnxRuntime entry for {abi} must be an object")
        _exact_keys(record, {"artifactId", "includeDir", "headers", "library"}, f"onnxRuntime entry for {abi}")
        _token(record["artifactId"], f"onnxRuntime artifactId for {abi}")
        _relative_path(record["includeDir"], f"onnxRuntime includeDir for {abi}")
        headers = record["headers"]
        if not isinstance(headers, list) or not headers or len(headers) > 256:
            raise AlignedBuildError(f"onnxRuntime headers for {abi} must be a bounded non-empty list")
        seen_headers: set[str] = set()
        for header in headers:
            if not isinstance(header, dict):
                raise AlignedBuildError(f"onnxRuntime header for {abi} must be an object")
            _exact_keys(header, {"path", "sizeBytes", "sha256"}, f"onnxRuntime header for {abi}")
            relative = _relative_path(header["path"], f"onnxRuntime header path for {abi}")
            if relative in seen_headers:
                raise AlignedBuildError(f"duplicate ONNX Runtime header path for {abi}")
            seen_headers.add(relative)
            if not relative.startswith(record["includeDir"] + "/"):
                raise AlignedBuildError(f"ONNX Runtime header for {abi} is outside includeDir")
            if not isinstance(header["sizeBytes"], int) or isinstance(header["sizeBytes"], bool) or not 0 < header["sizeBytes"] <= MAX_HEADER_BYTES:
                raise AlignedBuildError(f"ONNX Runtime header size for {abi} is outside its bound")
            _digest(header["sha256"], f"ONNX Runtime header hash for {abi}")
        if not any(PurePosixPath(path).name == "onnxruntime_c_api.h" for path in seen_headers):
            raise AlignedBuildError(f"ONNX Runtime headers for {abi} omit onnxruntime_c_api.h")
        library = record["library"]
        if not isinstance(library, dict):
            raise AlignedBuildError(f"onnxRuntime library for {abi} must be an object")
        _exact_keys(library, {"path", "sizeBytes", "sha256"}, f"onnxRuntime library for {abi}")
        if PurePosixPath(_relative_path(library["path"], f"onnxRuntime library path for {abi}")).name != VERIFIER.ORT_NAME:
            raise AlignedBuildError(f"onnxRuntime library for {abi} must be {VERIFIER.ORT_NAME}")
        if not isinstance(library["sizeBytes"], int) or isinstance(library["sizeBytes"], bool) or not 0 < library["sizeBytes"] <= VERIFIER.MAX_NATIVE_LIBRARY_BYTES:
            raise AlignedBuildError(f"ONNX Runtime library size for {abi} is outside its bound")
        _digest(library["sha256"], f"ONNX Runtime library hash for {abi}")

    patches = plan["patches"]
    if not isinstance(patches, list) or len(patches) > 64:
        raise AlignedBuildError("patches must be a bounded array")
    patch_ids: set[str] = set()
    for patch in patches:
        if not isinstance(patch, dict):
            raise AlignedBuildError("each patch must be an object")
        _exact_keys(patch, {"id", "fileName", "sizeBytes", "sha256"}, "patch")
        patch_id = _token(patch["id"], "patch.id")
        if patch_id in patch_ids:
            raise AlignedBuildError("patch IDs must be unique")
        patch_ids.add(patch_id)
        file_name = _relative_path(patch["fileName"], "patch.fileName")
        if len(PurePosixPath(file_name).parts) != 1:
            raise AlignedBuildError("patch.fileName must be a basename")
        if not isinstance(patch["sizeBytes"], int) or isinstance(patch["sizeBytes"], bool) or not 0 < patch["sizeBytes"] <= MAX_PATCH_BYTES:
            raise AlignedBuildError("patch.sizeBytes is outside its bound")
        _digest(patch["sha256"], "patch.sha256")

    qnn = plan["qnn"]
    if qnn is not None:
        if not isinstance(qnn, dict):
            raise AlignedBuildError("qnn must be null or an object")
        _exact_keys(qnn, {"manifestSha256", "backendId"}, "qnn")
        _digest(qnn["manifestSha256"], "qnn.manifestSha256")
        _token(qnn["backendId"], "qnn.backendId")


def _validate_git_source(source: Path, plan: dict[str, Any]) -> None:
    _directory(source, "sherpa source checkout")
    source_string = str(source)
    remote = _run(
        ["git", "-C", source_string, "remote", "get-url", "origin"],
        label="git origin lookup",
    ).stdout.decode("utf-8", errors="strict").strip()
    if remote != plan["sherpa"]["repository"]:
        raise AlignedBuildError("sherpa source origin does not match the plan")
    revision = plan["sherpa"]["revision"]
    resolved = _run(
        ["git", "-C", source_string, "rev-parse", "--verify", f"{revision}^{{commit}}"],
        label="sherpa revision lookup",
    ).stdout.decode("ascii", errors="strict").strip()
    if resolved != revision:
        raise AlignedBuildError("sherpa revision did not resolve to the exact planned commit")


def _archive_source(source: Path, revision: str, archive: Path) -> None:
    _run(
        ["git", "-C", str(source), "archive", "--format=tar", "--output", str(archive), revision],
        label="sherpa source archive",
    )
    _regular_file(archive, "sherpa source archive", MAX_SOURCE_ARCHIVE_BYTES)


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    total = 0
    with tarfile.open(archive, "r:") as source:
        members = source.getmembers()
        if len(members) > MAX_SOURCE_FILES:
            raise AlignedBuildError("sherpa source archive exceeds the file-count bound")
        for member in members:
            relative = _relative_path(member.name.rstrip("/"), "sherpa archive member")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise AlignedBuildError(f"unsupported sherpa archive member type: {relative}")
            if not (member.isfile() or member.isdir()):
                raise AlignedBuildError(f"unsupported sherpa archive member type: {relative}")
            if member.isfile():
                if member.size < 0 or member.size > MAX_SOURCE_BYTES - total:
                    raise AlignedBuildError("sherpa source archive exceeds the byte bound")
                total += member.size
        source.extractall(destination, members=members)


def _validate_patch_text(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise AlignedBuildError(f"patch is not UTF-8: {path.name}") from error
    saw_diff = False
    for line in lines:
        candidates: list[str] = []
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) != 4:
                raise AlignedBuildError(f"malformed diff header in {path.name}")
            candidates.extend(parts[2:])
            saw_diff = True
        elif line.startswith("--- ") or line.startswith("+++ "):
            candidate = line[4:].split("\t", 1)[0]
            if candidate != "/dev/null":
                candidates.append(candidate)
        for candidate in candidates:
            stripped = candidate[2:] if candidate.startswith(("a/", "b/")) else candidate
            _relative_path(stripped, f"path in patch {path.name}")
    if not saw_diff:
        raise AlignedBuildError(f"patch {path.name} contains no git diff")


def _apply_patches(source: Path, patches: list[tuple[dict[str, Any], Path]]) -> None:
    for record, patch in patches:
        _validate_patch_text(patch)
        command = ["git", "apply", "--whitespace=error-all"]
        _run(
            command + ["--check", str(patch)],
            cwd=source,
            label=f"patch check {record['id']}",
        )
        _run(command + [str(patch)], cwd=source, label=f"patch apply {record['id']}")


def _find_forbidden_ort(root: Path) -> list[str]:
    matches: list[str] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory_name in list(directory_names):
            candidate = current_path / directory_name
            if candidate.is_symlink():
                if "onnxruntime" in directory_name.lower():
                    matches.append(candidate.relative_to(root).as_posix())
                directory_names.remove(directory_name)
        for file_name in file_names:
            candidate = current_path / file_name
            lower = file_name.lower()
            is_ort_library = lower.startswith("libonnxruntime") and (
                ".so" in lower or lower.endswith(".a")
            )
            is_ort_archive = "onnxruntime" in lower and lower.endswith(
                (".aar", ".zip", ".tgz", ".tar.gz", ".tar.xz", ".tar.zst")
            )
            if is_ort_library or is_ort_archive or (
                candidate.is_symlink() and "onnxruntime" in lower
            ):
                matches.append(candidate.relative_to(root).as_posix())
    return sorted(matches)


def _inspect_elf(path: Path, abi: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    _regular_file(path, label, VERIFIER.MAX_NATIVE_LIBRARY_BYTES)
    with path.open("rb") as stream:
        metadata, error = VERIFIER.inspect_elf(stream, path.stat().st_size, abi)
    if error is not None or metadata is None:
        raise AlignedBuildError(f"invalid {label}: {error}")
    if abi in VERIFIER.ANDROID_16K_ABIS and not metadata["pageSize16KiBCompatible"]:
        raise AlignedBuildError(f"{label} is not 16 KiB page compatible")
    return metadata, _identity(path)


def _validate_ndk(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    _directory(root, "Android NDK")
    source_properties = _regular_file(
        _child_without_symlinks(root, "source.properties", "NDK source.properties"),
        "NDK source.properties",
        MAX_JSON_BYTES,
    )
    clang = _regular_file(
        _child_without_symlinks(root, record["clangPath"], "NDK clang"),
        "NDK clang",
        MAX_BUILD_OUTPUT_BYTES,
    )
    toolchain = _regular_file(
        _child_without_symlinks(
            root, record["cmakeToolchainPath"], "NDK CMake toolchain"
        ),
        "NDK CMake toolchain",
        MAX_JSON_BYTES,
    )
    expected = {
        "source.properties": record["sourcePropertiesSha256"],
        record["clangPath"]: record["clangSha256"],
        record["cmakeToolchainPath"]: record["cmakeToolchainSha256"],
    }
    actual = {
        "source.properties": _sha256(source_properties),
        record["clangPath"]: _sha256(clang),
        record["cmakeToolchainPath"]: _sha256(toolchain),
    }
    if actual != expected:
        raise AlignedBuildError("Android NDK/toolchain identity does not match the plan")
    revision_lines = [
        line.split("=", 1)[1].strip()
        for line in source_properties.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("Pkg.Revision") and "=" in line
    ]
    if revision_lines != [record["revision"]]:
        raise AlignedBuildError("Android NDK revision does not match source.properties")
    expected_clang_prefix = f"toolchains/llvm/prebuilt/{record['hostTag']}/"
    if not record["clangPath"].startswith(expected_clang_prefix):
        raise AlignedBuildError("Android NDK clang path does not match hostTag")
    return {
        "revision": record["revision"],
        "hostTag": record["hostTag"],
        "sourceProperties": {"sha256": actual["source.properties"]},
        "clang": {"path": record["clangPath"], "sha256": actual[record["clangPath"]]},
        "cmakeToolchain": {
            "path": record["cmakeToolchainPath"],
            "sha256": actual[record["cmakeToolchainPath"]],
        },
    }


def _validate_ort_inputs(
    plan: dict[str, Any], roots: dict[str, Path]
) -> tuple[dict[str, Any], dict[str, tuple[Path, Path]]]:
    by_abi = plan["onnxRuntime"]["byAbi"]
    if set(roots) != set(by_abi):
        raise AlignedBuildError("--ort keys must exactly match the planned ABI set")
    receipt: dict[str, Any] = {}
    paths: dict[str, tuple[Path, Path]] = {}
    for abi in sorted(by_abi):
        root = _directory(roots[abi], f"ONNX Runtime root for {abi}")
        record = by_abi[abi]
        include_dir = _directory(
            _child_without_symlinks(
                root,
                record["includeDir"],
                f"ONNX Runtime include directory for {abi}",
            ),
            f"ONNX Runtime include directory for {abi}",
        )
        header_records: list[dict[str, Any]] = []
        for index, header in enumerate(record["headers"]):
            _, identity = _validate_file_identity(
                root,
                header,
                label=f"ONNX Runtime header {index} for {abi}",
                maximum=MAX_HEADER_BYTES,
            )
            header_records.append(identity)
        library, library_identity = _validate_file_identity(
            root,
            record["library"],
            label=f"ONNX Runtime library for {abi}",
            maximum=VERIFIER.MAX_NATIVE_LIBRARY_BYTES,
        )
        elf, actual_identity = _inspect_elf(library, abi, f"ONNX Runtime library for {abi}")
        if library_identity != {"path": record["library"]["path"], **actual_identity}:
            raise AlignedBuildError(f"ONNX Runtime library identity drift for {abi}")
        if elf["soname"] != VERIFIER.ORT_NAME:
            raise AlignedBuildError(f"ONNX Runtime SONAME is wrong for {abi}")
        receipt[abi] = {
            "artifactId": record["artifactId"],
            "includeDir": record["includeDir"],
            "headers": sorted(header_records, key=lambda value: value["path"]),
            "library": {**library_identity, "elf": elf},
        }
        paths[abi] = (include_dir, library.parent)
    return receipt, paths


def _validate_qnn_manifest(
    manifest_path: Path | None,
    sdk_root: Path | None,
    planned: dict[str, Any] | None,
    abis: set[str],
) -> tuple[dict[str, Any] | None, Path | None]:
    if planned is None:
        if manifest_path is not None or sdk_root is not None:
            raise AlignedBuildError("QNN inputs are forbidden when plan.qnn is null")
        return None, None
    if manifest_path is None or sdk_root is None:
        raise AlignedBuildError("QNN-enabled plans require --qnn-manifest and --qnn-root")
    manifest, manifest_hash = _read_json(manifest_path, "QNN SDK manifest")
    if manifest_hash != planned["manifestSha256"]:
        raise AlignedBuildError("QNN SDK manifest hash does not match the plan")
    _exact_keys(
        manifest,
        {
            "schemaVersion",
            "sdkId",
            "sdkVersion",
            "source",
            "backendId",
            "backendVersion",
            "license",
            "artifactsByAbi",
        },
        "QNN SDK manifest",
    )
    if manifest["schemaVersion"] != 1:
        raise AlignedBuildError("QNN SDK manifest schemaVersion must be 1")
    for key in ("sdkId", "sdkVersion", "backendId", "backendVersion"):
        _token(manifest[key], f"QNN SDK manifest {key}")
    if manifest["backendId"] != planned["backendId"]:
        raise AlignedBuildError("QNN backendId does not match the plan")
    source = manifest["source"]
    if not isinstance(source, str) or not source.startswith("https://") or len(source) > 2048:
        raise AlignedBuildError("QNN SDK source must be a bounded HTTPS URL")
    license_record = manifest["license"]
    if not isinstance(license_record, dict):
        raise AlignedBuildError("QNN license must be an object")
    _exact_keys(license_record, {"id", "spdxId", "redistribution", "notice"}, "QNN license")
    _token(license_record["id"], "QNN license.id")
    _token(license_record["spdxId"], "QNN license.spdxId")
    if license_record["redistribution"] not in {"prohibited", "restricted", "permitted"}:
        raise AlignedBuildError("QNN license.redistribution is outside the closed set")
    root = _directory(sdk_root, "QNN SDK root")
    notice = license_record["notice"]
    if not isinstance(notice, dict):
        raise AlignedBuildError("QNN license.notice must be an object")
    _, notice_identity = _validate_file_identity(
        root, notice, label="QNN license notice", maximum=MAX_JSON_BYTES
    )
    artifacts = manifest["artifactsByAbi"]
    if not isinstance(artifacts, dict) or set(artifacts) != abis:
        raise AlignedBuildError("QNN artifactsByAbi must exactly match the planned ABI set")
    recorded: dict[str, Any] = {}
    for abi in sorted(artifacts):
        entries = artifacts[abi]
        if not isinstance(entries, list) or not entries or len(entries) > 128:
            raise AlignedBuildError(f"QNN artifacts for {abi} must be a bounded non-empty list")
        roles: set[str] = set()
        paths: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise AlignedBuildError(f"QNN artifact {index} for {abi} must be an object")
            _exact_keys(entry, {"role", "path", "sizeBytes", "sha256", "licenseId"}, f"QNN artifact {index} for {abi}")
            role = _token(entry["role"], f"QNN artifact role for {abi}")
            if entry["licenseId"] != license_record["id"]:
                raise AlignedBuildError(f"QNN artifact license ID mismatch for {abi}")
            _, identity = _validate_file_identity(
                root,
                {key: entry[key] for key in ("path", "sizeBytes", "sha256")},
                label=f"QNN artifact {index} for {abi}",
                maximum=MAX_QNN_ARTIFACT_BYTES,
            )
            if identity["path"] in paths:
                raise AlignedBuildError(f"duplicate QNN artifact path for {abi}")
            roles.add(role)
            paths.add(identity["path"])
            normalized.append({"role": role, **identity, "licenseId": entry["licenseId"]})
        if "backend" not in roles:
            raise AlignedBuildError(f"QNN artifacts for {abi} omit the backend role")
        recorded[abi] = sorted(normalized, key=lambda value: (value["role"], value["path"]))
    return (
        {
            "manifestSha256": manifest_hash,
            "sdkId": manifest["sdkId"],
            "sdkVersion": manifest["sdkVersion"],
            "source": source,
            "backendId": manifest["backendId"],
            "backendVersion": manifest["backendVersion"],
            "license": {
                "id": license_record["id"],
                "spdxId": license_record["spdxId"],
                "redistribution": license_record["redistribution"],
                "notice": notice_identity,
            },
            "artifactsByAbi": recorded,
        },
        root,
    )


def _prepare_patches(
    records: list[dict[str, Any]], supplied: dict[str, Path]
) -> list[tuple[dict[str, Any], Path]]:
    expected_ids = {record["id"] for record in records}
    if set(supplied) != expected_ids:
        raise AlignedBuildError("--patch keys must exactly match the planned patch set")
    result: list[tuple[dict[str, Any], Path]] = []
    for record in records:
        path = _regular_file(supplied[record["id"]], f"patch {record['id']}", MAX_PATCH_BYTES)
        if path.name != record["fileName"]:
            raise AlignedBuildError(f"patch filename mismatch for {record['id']}")
        if path.stat().st_size != record["sizeBytes"] or _sha256(path) != record["sha256"]:
            raise AlignedBuildError(f"patch identity mismatch for {record['id']}")
        result.append((record, path))
    return result


def _build_abi(
    *,
    abi: str,
    source_archive: Path,
    work_dir: Path,
    plan: dict[str, Any],
    patches: list[tuple[dict[str, Any], Path]],
    ndk_root: Path,
    ort_paths: tuple[Path, Path],
    qnn_root: Path | None,
) -> dict[str, Any]:
    source = work_dir / f"source-{abi}"
    _extract_archive(source_archive, source)
    if forbidden := _find_forbidden_ort(source):
        raise AlignedBuildError(
            f"archived sherpa source contains a default/fallback ORT for {abi}: {forbidden[0]}"
        )
    _apply_patches(source, patches)
    if forbidden := _find_forbidden_ort(source):
        raise AlignedBuildError(
            f"patched sherpa source contains a default/fallback ORT for {abi}: {forbidden[0]}"
        )

    build = plan["sherpa"]["buildsByAbi"][abi]
    script = _regular_file(source / build["script"], f"sherpa build script for {abi}", MAX_JSON_BYTES)
    output = source / build["output"]
    if output.exists():
        raise AlignedBuildError(f"sherpa build output already exists for {abi}")
    include_dir, library_dir = ort_paths
    isolated_home = work_dir / f"home-{abi}"
    isolated_tmp = work_dir / f"tmp-{abi}"
    isolated_home.mkdir()
    isolated_tmp.mkdir()
    blocked_proxy = "http://127.0.0.1:9"
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(isolated_home),
        "TMPDIR": str(isolated_tmp),
        "XDG_CACHE_HOME": str(isolated_home / ".cache"),
        "LC_ALL": "C",
        "LANG": "C",
        "SOURCE_DATE_EPOCH": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "http_proxy": blocked_proxy,
        "https_proxy": blocked_proxy,
        "HTTP_PROXY": blocked_proxy,
        "HTTPS_PROXY": blocked_proxy,
        "ALL_PROXY": blocked_proxy,
        "NO_PROXY": "",
        "ANDROID_ABI": abi,
        "ANDROID_PLATFORM": f"android-{plan['android']['minimumApi']}",
        "ANDROID_NDK": str(ndk_root),
        "ANDROID_NDK_HOME": str(ndk_root),
        "ANDROID_NDK_ROOT": str(ndk_root),
        "CMAKE_TOOLCHAIN_FILE": str(ndk_root / plan["android"]["ndk"]["cmakeToolchainPath"]),
        "BUILD_SHARED_LIBS": "ON",
        "SHERPA_ONNXRUNTIME_INCLUDE_DIR": str(include_dir),
        "SHERPA_ONNXRUNTIME_LIB_DIR": str(library_dir),
        "SHERPA_ONNX_ENABLE_JNI": "ON",
        "SHERPA_ONNX_ENABLE_C_API": "OFF",
        "SHERPA_ONNX_ENABLE_QNN": "ON" if qnn_root is not None else "OFF",
        "SHERPA_ONNX_ENABLE_RKNN": "OFF",
        "SHERPA_ONNX_DOWNLOAD_ONNXRUNTIME": "OFF",
        "SHERPA_ONNX_USE_PRECOMPILED_ONNXRUNTIME_IF_AVAILABLE": "OFF",
        "FETCHCONTENT_FULLY_DISCONNECTED": "ON",
        "FONIX_ALIGNED_BUILD": "1",
        "FONIX_SHERPA_JNI_OUTPUT": str(output),
    }
    if qnn_root is not None:
        environment["QNN_SDK_ROOT"] = str(qnn_root)
        environment["FONIX_QNN_BACKEND_ID"] = plan["qnn"]["backendId"]

    _run(
        ["bash", str(script)],
        cwd=source,
        env=environment,
        label=f"sherpa aligned build for {abi}",
    )
    if forbidden := _find_forbidden_ort(source):
        raise AlignedBuildError(
            f"sherpa build acquired or emitted a forbidden ORT for {abi}: {forbidden[0]}"
        )
    elf, identity = _inspect_elf(output, abi, f"sherpa JNI output for {abi}")
    if elf["soname"] != VERIFIER.SHERPA_JNI_NAME:
        raise AlignedBuildError(f"sherpa JNI SONAME is wrong for {abi}")
    if elf["needed"].count(VERIFIER.ORT_NAME) != 1:
        raise AlignedBuildError(
            f"sherpa JNI for {abi} must have exactly one DT_NEEDED {VERIFIER.ORT_NAME}"
        )
    artifact_dir = work_dir / "artifact" / "jni" / abi
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_output = artifact_dir / VERIFIER.SHERPA_JNI_NAME
    shutil.copyfile(output, artifact_output)
    if _identity(artifact_output) != identity:
        raise AlignedBuildError(f"copied sherpa JNI identity drift for {abi}")
    return {
        "script": {"path": build["script"], **_identity(script)},
        "output": {
            "path": f"jni/{abi}/{VERIFIER.SHERPA_JNI_NAME}",
            **identity,
            "elf": elf,
        },
    }


def build(arguments: argparse.Namespace) -> dict[str, Any]:
    plan_path = arguments.plan.resolve(strict=True)
    plan, plan_hash = _read_json(plan_path, "aligned build plan")
    _validate_plan(plan)
    source = arguments.sherpa_source.resolve(strict=True)
    ndk_root = arguments.ndk_root.resolve(strict=True)
    ort_roots = _parse_keyed(arguments.ort, "--ort")
    patch_paths = _parse_keyed(arguments.patch, "--patch")
    _validate_git_source(source, plan)
    ndk_receipt = _validate_ndk(ndk_root, plan["android"]["ndk"])
    ort_receipt, ort_paths = _validate_ort_inputs(plan, ort_roots)
    patches = _prepare_patches(plan["patches"], patch_paths)
    qnn_receipt, qnn_root = _validate_qnn_manifest(
        arguments.qnn_manifest.resolve(strict=True) if arguments.qnn_manifest else None,
        arguments.qnn_root.resolve(strict=True) if arguments.qnn_root else None,
        plan["qnn"],
        set(plan["sherpa"]["buildsByAbi"]),
    )

    work_dir = arguments.work_dir
    if not work_dir.is_absolute():
        raise AlignedBuildError("--work-dir must be absolute")
    if work_dir.exists() or work_dir.is_symlink():
        raise AlignedBuildError("--work-dir must not already exist")
    work_dir.mkdir(parents=True)
    archive = work_dir / "sherpa-source.tar"
    _archive_source(source, plan["sherpa"]["revision"], archive)
    source_archive_identity = _identity(archive)

    builds: dict[str, Any] = {}
    for abi in sorted(plan["sherpa"]["buildsByAbi"]):
        builds[abi] = _build_abi(
            abi=abi,
            source_archive=archive,
            work_dir=work_dir,
            plan=plan,
            patches=patches,
            ndk_root=ndk_root,
            ort_paths=ort_paths[abi],
            qnn_root=qnn_root,
        )

    return {
        "schemaVersion": 1,
        "result": "passed",
        "planSha256": plan_hash,
        "driverSha256": _sha256(Path(__file__)),
        "sherpa": {
            "repository": plan["sherpa"]["repository"],
            "revision": plan["sherpa"]["revision"],
            "sourceArchive": source_archive_identity,
            "patches": [
                {
                    "id": record["id"],
                    "fileName": record["fileName"],
                    "sizeBytes": record["sizeBytes"],
                    "sha256": record["sha256"],
                }
                for record, _path in patches
            ],
            "buildsByAbi": builds,
        },
        "android": {
            "minimumApi": plan["android"]["minimumApi"],
            "ndk": ndk_receipt,
        },
        "onnxRuntime": {
            "owner": "application",
            "linkage": "external-shared",
            "version": plan["onnxRuntime"]["version"],
            "requiredApi": plan["onnxRuntime"]["requiredApi"],
            "byAbi": ort_receipt,
        },
        "qnn": qnn_receipt,
        "claimBoundary": (
            "This deterministic receipt proves only that exact local inputs produced "
            "sherpa JNI files with one DT_NEEDED libonnxruntime.so and no emitted "
            "sherpa-owned ORT. It does not prove final packaging, target loading, "
            "provider assignment, inference correctness, performance, or licensing approval."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--sherpa-source", type=Path, required=True)
    parser.add_argument("--ndk-root", type=Path, required=True)
    parser.add_argument("--ort", action="append", default=[], metavar="ABI=ROOT")
    parser.add_argument("--patch", action="append", default=[], metavar="ID=FILE")
    parser.add_argument("--qnn-manifest", type=Path)
    parser.add_argument("--qnn-root", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _write_new(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise AlignedBuildError("--output must be absolute")
    if path.exists() or path.is_symlink():
        raise AlignedBuildError("--output must not already exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise AlignedBuildError("temporary output already exists")
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = build(arguments)
        _write_new(arguments.output, receipt)
    except (AlignedBuildError, FileNotFoundError, OSError, UnicodeError, tarfile.TarError) as error:
        print(f"build_sherpa_aligned_android: {error}", file=sys.stderr)
        return 1
    print(f"Wrote deterministic aligned-build receipt: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
