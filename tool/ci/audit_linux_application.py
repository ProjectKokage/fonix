#!/usr/bin/env python3
"""Fail-closed audit of the final Fonix Linux x86_64 application tree.

This auditor intentionally accepts the installed Flutter bundle rather than an
intermediate native-assets directory.  It binds the final bytes back to one
native-assets hook invocation, verifies the closed application inventory, and
audits every ELF in that inventory with GNU readelf.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import threading
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
import zlib


ARTIFACT_ID = "onnxruntime-1.27.1-linux-x86_64-cpu"
ARTIFACT_SHA256 = "25b1ef1fea1acd210d63f8f24dc870ad6e077795ce1f54876252c6d3803c15af"
ARTIFACT_SIZE_BYTES = 8_828_892
ARTIFACT_SOURCE_REVISION = "df2ba1cf8108aa63627cf4cdf8f807880b938616"
ARTIFACT_URL = (
    "https://github.com/microsoft/onnxruntime/releases/download/v1.27.1/"
    "onnxruntime-linux-x64-1.27.1.tgz"
)
RUNTIME_SHA256 = "50e214fa23276cf2f3fb96d90d8e7ca9fc5ba29f42e298eccf3b23f7d8a81ae2"
RUNTIME_SIZE_BYTES = 23_658_512
PROVIDER_SHA256 = "086ec1d5388f64153d9c63470d126693db9a182c8ce236d3a1119068471b8a0d"
PROVIDER_SIZE_BYTES = 14_632
NOTICE_SHA256 = "0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2"
NOTICE_SIZE_BYTES = 325_054
FLUTTER_NOTICES_SHA256 = "1de586c69a9f3847ca9aa50a26912f54a928044327989d1e17eac5e64023495e"
FLUTTER_NOTICES_SIZE_BYTES = 99_081
FLUTTER_ENGINE_SHA256 = "d8d3b60530b06ba43cfc34a9e7a1688d53b743d5133702a3ca2f269e071c9afe"
FLUTTER_ENGINE_SIZE_BYTES = 17_212_792
FLUTTER_ICU_SHA256 = "325a86063d26334c2eabe1743cea073b612540fbf3d8fc2ef0b5708e3763a8c7"
FLUTTER_ICU_SIZE_BYTES = 864_880
MODEL_SHA256 = "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10"
MODEL_SIZE_BYTES = 130
XNNPACK_MODEL_SHA256 = "c75aaa93b0e1ae09e0bb12ddee5786c2fda803dfa5f565234e2b65e238623482"
XNNPACK_MODEL_SIZE_BYTES = 311

APPLICATION_EXECUTABLE = "fonix_reference"
SHIM_NAME = "libfonix_shim.so"
RUNTIME_NAME = "libonnxruntime.so.1"
PROVIDER_NAME = "libonnxruntime_providers_shared.so"

MAX_PATH_BYTES = 4096
MAX_TREE_ENTRIES = 128
MAX_TREE_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_READELF_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024
MAX_NOTICES_UNCOMPRESSED = 32 * 1024 * 1024
READELF_TIMEOUT_SECONDS = 30
COPY_CHUNK_BYTES = 1024 * 1024

SHA256 = re.compile(r"^[0-9a-f]{64}$")
HOOK_INVOCATION = re.compile(r"^[0-9a-f]{10}$")
HOOK_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.000$")
BUILD_ID = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{40})$")
VERSIONED_DORT = re.compile(r"^(dort_[A-Za-z0-9_]+)@@FONIX_DORT_1\.0$")
VERSION_TOKEN = re.compile(r"\b(GLIBCXX|GLIBC|CXXABI)_([A-Za-z0-9_.]+)\b")
NEEDED_VALUE = re.compile(r"^Shared library: \[([^\]]+)\]$")
SONAME_VALUE = re.compile(r"^Library soname: \[([^\]]+)\]$")
RUNPATH_VALUE = re.compile(r"^Library runpath: \[([^\]]*)\]$")
ADDRESS_VALUE = re.compile(r"^(0x[0-9a-fA-F]+)$")
DYNAMIC_SYMBOL = re.compile(
    r"^\s*\d+:\s+[0-9a-fA-F]+\s+\d+\s+(?P<type>\S+)\s+"
    r"(?P<binding>\S+)\s+(?P<visibility>\S+)\s+"
    r"(?P<section>\S+)\s+(?P<name>\S+)"
    r"(?:\s+\((?P<version_index>[0-9]+)\))?\s*$"
)
DYNAMIC_NULL_SYMBOL = re.compile(
    r"^\s*0:\s+0+(?:\s+0+)\s+NOTYPE\s+LOCAL\s+DEFAULT\s+UND\s*$"
)
DYNAMIC_SYMBOL_HEADER = re.compile(
    r"^Symbol table '\.dynsym' contains ([0-9]+) entries:$"
)
PROGRAM_HEADER = re.compile(
    r"^\s*(?P<type>[A-Z0-9_]+)\s+"
    r"(?P<offset>0x[0-9a-fA-F]+)\s+"
    r"(?P<vaddr>0x[0-9a-fA-F]+)\s+"
    r"(?P<paddr>0x[0-9a-fA-F]+)\s+"
    r"(?P<filesz>0x[0-9a-fA-F]+)\s+"
    r"(?P<memsz>0x[0-9a-fA-F]+)\s+"
    r"(?P<flags>[RWE ]+?)\s+0x[0-9a-fA-F]+\s*$"
)
DYNAMIC_ENTRY = re.compile(
    r"^\s*0x[0-9a-fA-F]+\s+\((?P<tag>[A-Z0-9_]+)\)\s+(?P<value>[^\r\n]*)$"
)
INTERPRETER = re.compile(
    r"^\s*\[Requesting program interpreter: ([^\]]+)\]\s*$", re.MULTILINE
)
DYNAMIC_SECTION_HEADER = re.compile(
    r"^Dynamic section at offset 0x[0-9a-fA-F]+ contains ([0-9]+) entries:$"
)
VERSION_NEEDS_HEADER = re.compile(
    r"^Version needs section '[^']+' contains ([0-9]+) entries:$"
)
VERSION_NEED_FILE = re.compile(
    r"^\s*(?:0x)?[0-9a-fA-F]+:\s+Version:\s+\d+\s+File:\s+(?P<file>\S+)\s+Cnt:\s+(?P<count>\d+)\s*$"
)
VERSION_NEED_AUX = re.compile(
    r"^\s*(?:0x)?[0-9a-fA-F]+:\s+Name:\s+(?P<name>\S+)\s+"
    r"Flags:\s+(?P<flags>\S+)\s+Version:\s+(?P<index>\d+)\s*$"
)
RELRO_SECTION = re.compile(
    r"^\s*\[\s*\d+\]\s+(?P<name>\.dynamic|\.got(?:\.plt)?)\s+(?P<type>\S+)\s+"
    r"(?P<address>[0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+"
    r"(?P<size>[0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+(?P<flags>\S+)\s+.*$"
)
FORBIDDEN_DYNAMIC_TAGS = frozenset(
    {"AUDIT", "DEPAUDIT", "FILTER", "AUXILIARY", "TEXTREL"}
)

EXPECTED_FILES = frozenset(
    {
        APPLICATION_EXECUTABLE,
        "data/icudtl.dat",
        "data/flutter_assets/AssetManifest.bin",
        "data/flutter_assets/FontManifest.json",
        "data/flutter_assets/NativeAssetsManifest.json",
        "data/flutter_assets/NOTICES.Z",
        "data/flutter_assets/version.json",
        "data/flutter_assets/fonts/MaterialIcons-Regular.otf",
        "data/flutter_assets/shaders/ink_sparkle.frag",
        "data/flutter_assets/shaders/stretch_effect.frag",
        "data/flutter_assets/assets/models/mul_1.onnx",
        "data/flutter_assets/assets/models/model.json",
        "data/flutter_assets/assets/models/xnnpack_matmul.onnx",
        "data/flutter_assets/assets/models/xnnpack_matmul.json",
        "data/flutter_assets/assets/fonix/fonix-native-artifact-manifest.json",
        "data/flutter_assets/assets/fonix/ThirdPartyNotices.txt",
        "lib/libapp.so",
        "lib/libflutter_linux_gtk.so",
        f"lib/{SHIM_NAME}",
        f"lib/{RUNTIME_NAME}",
        f"lib/{PROVIDER_NAME}",
    }
)

ELF_PATHS = (
    APPLICATION_EXECUTABLE,
    "lib/libapp.so",
    "lib/libflutter_linux_gtk.so",
    f"lib/{SHIM_NAME}",
    f"lib/{RUNTIME_NAME}",
    f"lib/{PROVIDER_NAME}",
)

EXPECTED_SONAMES: Mapping[str, str | None] = {
    APPLICATION_EXECUTABLE: None,
    "lib/libapp.so": "libapp.so",
    "lib/libflutter_linux_gtk.so": "libflutter_linux_gtk.so",
    f"lib/{SHIM_NAME}": SHIM_NAME,
    f"lib/{RUNTIME_NAME}": RUNTIME_NAME,
    f"lib/{PROVIDER_NAME}": PROVIDER_NAME,
}

EXPECTED_RUNPATHS: Mapping[str, tuple[str, ...]] = {
    APPLICATION_EXECUTABLE: ("$ORIGIN/lib",),
    "lib/libapp.so": (),
    "lib/libflutter_linux_gtk.so": ("$ORIGIN",),
    f"lib/{SHIM_NAME}": ("$ORIGIN",),
    f"lib/{RUNTIME_NAME}": ("$ORIGIN",),
    f"lib/{PROVIDER_NAME}": (),
}

SHIM_NEEDED = ("libpthread.so.0", "libdl.so.2", "libc.so.6")
RUNTIME_NEEDED = (
    "libdl.so.2",
    "librt.so.1",
    "libpthread.so.0",
    "libstdc++.so.6",
    "libm.so.6",
    "libgcc_s.so.1",
    "libc.so.6",
    "ld-linux-x86-64.so.2",
)
PROVIDER_NEEDED = (
    "libstdc++.so.6",
    "libm.so.6",
    "libgcc_s.so.1",
    "libc.so.6",
)

# Exact dependency profiles for the pinned Flutter 3.47.0-0.1.pre Linux x64
# engine, its Release AOT snapshot, and the committed GTK runner.  These are
# deliberately not inferred from an allowlist: a missing edge is as material as
# an added one for the closed final-package contract.
RUNNER_NEEDED = (
    "libflutter_linux_gtk.so",
    "libgtk-3.so.0",
    "libgdk-3.so.0",
    "libgio-2.0.so.0",
    "libgobject-2.0.so.0",
    "libglib-2.0.so.0",
    "libstdc++.so.6",
    "libgcc_s.so.1",
    "libc.so.6",
)
APP_NEEDED = (
    "libdl.so.2",
    "libpthread.so.0",
    "libm.so.6",
    "libc.so.6",
)
FLUTTER_NEEDED = (
    "libdl.so.2",
    "libgtk-3.so.0",
    "libgdk-3.so.0",
    "libpangocairo-1.0.so.0",
    "libpango-1.0.so.0",
    "libatk-1.0.so.0",
    "libcairo.so.2",
    "libgio-2.0.so.0",
    "libgobject-2.0.so.0",
    "libglib-2.0.so.0",
    "libepoxy.so.0",
    "libfontconfig.so.1",
    "libpthread.so.0",
    "libm.so.6",
    "libc.so.6",
    "ld-linux-x86-64.so.2",
)

EXPECTED_NEEDED: Mapping[str, tuple[str, ...]] = {
    APPLICATION_EXECUTABLE: RUNNER_NEEDED,
    "lib/libapp.so": APP_NEEDED,
    "lib/libflutter_linux_gtk.so": FLUTTER_NEEDED,
    f"lib/{SHIM_NAME}": SHIM_NEEDED,
    f"lib/{RUNTIME_NAME}": RUNTIME_NEEDED,
    f"lib/{PROVIDER_NAME}": PROVIDER_NEEDED,
}

VERSION_MAXIMA = {
    "GLIBC": (2, 27),
    "GLIBCXX": (3, 4, 24),
    "CXXABI": (1, 3, 11),
}


class LinuxApplicationAuditError(RuntimeError):
    """The final Linux application violates its closed contract."""


def _require_isolated_python_invocation() -> None:
    if (
        sys.flags.isolated != 1
        or sys.flags.ignore_environment != 1
        or sys.flags.no_user_site != 1
        or sys.flags.no_site != 1
        or not sys.dont_write_bytecode
    ):
        raise LinuxApplicationAuditError(
            "run the Linux application auditor with Python -I -S -B"
        )


@dataclass(frozen=True)
class FileIdentity:
    size_bytes: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class TreeIdentity:
    files: Mapping[str, FileIdentity]
    file_count: int
    byte_count: int
    tree_sha256: str


@dataclass(frozen=True)
class ElfRecord:
    path: str
    sha256: str
    size_bytes: int
    needed: tuple[str, ...]
    soname: str | None
    runpath: tuple[str, ...]
    build_id: str
    version_maxima: Mapping[str, str | None]
    defined_symbols: tuple[str, ...]
    undefined_symbols: tuple[str, ...]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LinuxApplicationAuditError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise LinuxApplicationAuditError(f"non-finite JSON number {value!r}")


def _strict_json(data: bytes | str, label: str, *, maximum: int = MAX_JSON_BYTES) -> Any:
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    if not raw or len(raw) > maximum:
        raise LinuxApplicationAuditError(f"{label} size is outside its bound")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except LinuxApplicationAuditError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LinuxApplicationAuditError(f"{label} is not strict UTF-8 JSON") from error


def _bounded_path(path: Path, label: str) -> None:
    raw = os.fsencode(str(path))
    if not raw or len(raw) > MAX_PATH_BYTES or b"\x00" in raw:
        raise LinuxApplicationAuditError(f"{label} is outside the path bound")
    if any(byte < 0x20 for byte in raw):
        raise LinuxApplicationAuditError(f"{label} contains a control character")


def _regular_file(path: Path, label: str, *, maximum: int = MAX_FILE_BYTES) -> Path:
    _bounded_path(path, label)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise LinuxApplicationAuditError(f"missing or unreadable {label}") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > maximum:
        raise LinuxApplicationAuditError(f"{label} is not a bounded regular file")
    return path


def _directory(path: Path, label: str) -> Path:
    _bounded_path(path, label)
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise LinuxApplicationAuditError(f"missing or unreadable {label}") from error
    if not stat.S_ISDIR(mode):
        raise LinuxApplicationAuditError(f"{label} is not a non-link directory")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str, label: str) -> str:
    if (
        not value
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise LinuxApplicationAuditError(f"{label} is not a safe relative path")
    parsed = PurePosixPath(value)
    if parsed.as_posix() != value or any(part in {"", ".", ".."} for part in parsed.parts):
        raise LinuxApplicationAuditError(f"{label} is not canonical")
    return value


def _tree_identity(root: Path) -> TreeIdentity:
    root = _directory(root, "final application root")
    root_mode = stat.S_IMODE(root.lstat().st_mode)
    if root_mode != 0o755:
        raise LinuxApplicationAuditError("application root mode changed")
    result: dict[str, FileIdentity] = {}
    directories: set[str] = set()
    entries = 0
    byte_count = 0
    for current_raw, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_raw)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            candidate = current / name
            relative = candidate.relative_to(root).as_posix()
            _safe_relative_path(relative, "application directory")
            entries += 1
            directory_mode = candidate.lstat().st_mode
            if entries > MAX_TREE_ENTRIES or not stat.S_ISDIR(directory_mode):
                raise LinuxApplicationAuditError("application directory inventory is invalid")
            if stat.S_IMODE(directory_mode) != 0o755:
                raise LinuxApplicationAuditError("application directory mode changed")
            directories.add(relative)
        for name in file_names:
            candidate = current / name
            relative = _safe_relative_path(
                candidate.relative_to(root).as_posix(), "application file"
            )
            entries += 1
            if entries > MAX_TREE_ENTRIES:
                raise LinuxApplicationAuditError("application exceeds its entry bound")
            file_path = _regular_file(candidate, f"application file {relative}")
            metadata = file_path.lstat()
            byte_count += metadata.st_size
            if byte_count > MAX_TREE_BYTES:
                raise LinuxApplicationAuditError("application exceeds its byte bound")
            result[relative] = FileIdentity(
                size_bytes=metadata.st_size,
                sha256=_sha256(file_path),
                mode=stat.S_IMODE(metadata.st_mode),
            )
    if frozenset(result) != EXPECTED_FILES:
        missing = sorted(EXPECTED_FILES - frozenset(result))
        extra = sorted(frozenset(result) - EXPECTED_FILES)
        raise LinuxApplicationAuditError(
            f"application file inventory changed (missing={missing}, extra={extra})"
        )
    expected_directories = {
        str(parent)
        for relative in EXPECTED_FILES
        for parent in PurePosixPath(relative).parents
        if str(parent) != "."
    }
    if directories != expected_directories:
        raise LinuxApplicationAuditError("application directory inventory changed")
    expected_modes = {
        path: (0o755 if path == APPLICATION_EXECUTABLE else 0o644)
        for path in EXPECTED_FILES
    }
    for path, identity in result.items():
        if identity.mode != expected_modes[path] or identity.mode & 0o022:
            raise LinuxApplicationAuditError(f"application file mode changed: {path}")
    canonical: list[dict[str, object]] = [
        {"path": ".", "type": "directory", "mode": "0755"},
        *[
            {"path": path, "type": "directory", "mode": "0755"}
            for path in sorted(directories)
        ],
        *[
            {
                "path": path,
                "type": "file",
                "sizeBytes": identity.size_bytes,
                "sha256": identity.sha256,
                "mode": format(identity.mode, "04o"),
            }
            for path, identity in sorted(result.items())
        ],
    ]
    canonical.sort(key=lambda entry: (str(entry["path"]), str(entry["type"])))
    tree_sha256 = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return TreeIdentity(result, len(result), byte_count, tree_sha256)


def _require_identity(path: Path, expected_sha: str, expected_size: int, label: str) -> None:
    path = _regular_file(path, label)
    if path.stat().st_size != expected_size or _sha256(path) != expected_sha:
        raise LinuxApplicationAuditError(f"{label} identity changed")


def _require_equal(left: Path, right: Path, label: str) -> None:
    left = _regular_file(left, f"{label} source")
    right = _regular_file(right, f"{label} packaged copy")
    if left.stat().st_size != right.stat().st_size or _sha256(left) != _sha256(right):
        raise LinuxApplicationAuditError(f"{label} packaged bytes changed")


def _validate_repository_assets(repository: Path, application: Path) -> dict[str, object]:
    source = repository / "example/assets"
    packaged = application / "data/flutter_assets/assets"
    for relative in (
        "models/mul_1.onnx",
        "models/model.json",
        "models/xnnpack_matmul.onnx",
        "models/xnnpack_matmul.json",
    ):
        _require_equal(source / relative, packaged / relative, f"reference asset {relative}")
    _require_identity(packaged / "models/mul_1.onnx", MODEL_SHA256, MODEL_SIZE_BYTES, "mul_1 model")
    _require_identity(
        packaged / "models/xnnpack_matmul.onnx",
        XNNPACK_MODEL_SHA256,
        XNNPACK_MODEL_SIZE_BYTES,
        "XNNPACK model",
    )
    version = _strict_json(
        (application / "data/flutter_assets/version.json").read_bytes(),
        "Flutter version asset",
    )
    if version != {
        "app_name": "fonix_reference",
        "version": "0.1.0",
        "build_number": "1",
        "package_name": "fonix_reference",
    }:
        raise LinuxApplicationAuditError("Flutter version asset changed")
    fonts = _strict_json(
        (application / "data/flutter_assets/FontManifest.json").read_bytes(),
        "Flutter font manifest",
    )
    expected_fonts = [
        {
            "family": "MaterialIcons",
            "fonts": [{"asset": "fonts/MaterialIcons-Regular.otf"}],
        }
    ]
    if fonts != expected_fonts:
        raise LinuxApplicationAuditError("Flutter font manifest changed")
    notices = _regular_file(
        application / "data/flutter_assets/NOTICES.Z", "compressed Flutter notices"
    ).read_bytes()
    _require_identity(
        application / "data/flutter_assets/NOTICES.Z",
        FLUTTER_NOTICES_SHA256,
        FLUTTER_NOTICES_SIZE_BYTES,
        "pinned Flutter notices",
    )
    try:
        decompressor = zlib.decompressobj(31)
        expanded = decompressor.decompress(notices, MAX_NOTICES_UNCOMPRESSED + 1)
        remaining = max(1, MAX_NOTICES_UNCOMPRESSED + 1 - len(expanded))
        expanded += decompressor.flush(remaining)
    except zlib.error as error:
        raise LinuxApplicationAuditError("Flutter notices are not valid zlib data") from error
    if (
        len(expanded) > MAX_NOTICES_UNCOMPRESSED
        or not decompressor.eof
        or decompressor.unused_data
        or not expanded.startswith(b"Copyright 2013 The Flutter Authors\n")
        or b"\nfonix\n" not in expanded
        or b"\ncrypto\n" not in expanded
        or expanded.count(b"\n--------------------------------------------------------------------------------\n") < 10
    ):
        raise LinuxApplicationAuditError("Flutter notices violate their closed bound")
    native_manifest = _strict_json(
        (application / "data/flutter_assets/NativeAssetsManifest.json").read_bytes(),
        "Flutter native-assets manifest",
    )
    expected_native_manifest = {
        "format-version": [1, 0, 0],
        "native-assets": {
            "linux_x64": {
                "package:fonix/fonix_shim": ["absolute", SHIM_NAME],
                "package:fonix/onnxruntime": ["absolute", RUNTIME_NAME],
                "package:fonix/onnxruntime_providers_shared": [
                    "absolute",
                    PROVIDER_NAME,
                ],
            }
        },
    }
    if native_manifest != expected_native_manifest:
        raise LinuxApplicationAuditError(
            "Flutter native-assets manifest schema or exact app-relative bindings changed"
        )
    return {
        "modelSha256": MODEL_SHA256,
        "xnnpackModelSha256": XNNPACK_MODEL_SHA256,
        "nativeAssetsManifestSha256": _sha256(
            application / "data/flutter_assets/NativeAssetsManifest.json"
        ),
        "noticesSha256": _sha256(application / "data/flutter_assets/NOTICES.Z"),
    }


def _load_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise LinuxApplicationAuditError(f"could not load trusted helper {path.name}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _validate_resolver_manifest(repository: Path, staging: Path) -> dict[str, Any]:
    helper = _load_module(
        "_fonix_linux_desktop_audit", repository / "tool/ci/audit_desktop_bundle.py"
    )
    manifest_path = staging / "fonix-native-artifact-manifest.json"
    manifest = helper._read_json(
        manifest_path, label="resolver manifest", maximum_bytes=helper.MAX_MANIFEST_BYTES
    )
    lock_path = repository / "native/versions.lock.yaml"
    lock = helper._read_json(
        lock_path, label="trusted lock", maximum_bytes=helper.MAX_LOCK_BYTES
    )
    try:
        helper._validate_manifest(
            manifest,
            staging,
            helper.POLICIES["linux-x64"],
            trusted_lock=lock,
            trusted_lock_sha256=_sha256(lock_path),
        )
    except helper.DesktopBundleAuditError as error:
        raise LinuxApplicationAuditError("resolver manifest failed its trusted-lock audit") from error
    return manifest


def _extract_build_manifest(shim: Path) -> dict[str, Any]:
    payload = _regular_file(shim, "reference hook shim").read_bytes()
    prefix = b'{"schemaVersion":3,"nativeIdentity":"fonix_shim"'
    offsets = [index for index in range(len(payload)) if payload.startswith(prefix, index)]
    if len(offsets) != 1:
        raise LinuxApplicationAuditError("shim must embed exactly one schema-3 build manifest")
    tail = payload[offsets[0] : offsets[0] + 16 * 1024]
    nul = tail.find(b"\x00")
    if nul < 0:
        raise LinuxApplicationAuditError("shim build manifest is not NUL terminated")
    manifest = _strict_json(tail[:nul], "embedded shim build manifest", maximum=16 * 1024)
    expected = {
        "schemaVersion": 3,
        "nativeIdentity": "fonix_shim",
        "shimAbiVersion": 1,
        "requiredOrtApiVersion": 27,
        "runtimeProfile": "bundled",
        "androidRuntimeOwner": None,
        "allowedRuntimeSources": ["bundled"],
        "buildId": ARTIFACT_ID,
        "artifact": {
            "id": ARTIFACT_ID,
            "sourceSha256": ARTIFACT_SHA256,
            "targetOs": "linux",
            "targetArchitecture": "x86_64",
            "variant": "default",
            "minimumOs": "glibc-2.27",
            "flavor": "cpu",
            "runtimeMode": "bundled",
            "noticesSha256": NOTICE_SHA256,
            "providers": [{"wrapperId": "cpu", "reportedName": "CPUExecutionProvider"}],
        },
    }
    if not isinstance(manifest, dict) or set(manifest) != set(expected):
        raise LinuxApplicationAuditError("embedded shim build manifest field set changed")
    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != set(expected["artifact"]) | {"lockSha256"}:
        raise LinuxApplicationAuditError("embedded shim artifact field set changed")
    lock_sha = artifact.get("lockSha256")
    artifact_without_lock = {key: value for key, value in artifact.items() if key != "lockSha256"}
    manifest_without_artifact = {key: value for key, value in manifest.items() if key != "artifact"}
    expected_without_artifact = {key: value for key, value in expected.items() if key != "artifact"}
    if (
        not isinstance(lock_sha, str)
        or SHA256.fullmatch(lock_sha) is None
        or manifest_without_artifact != expected_without_artifact
        or artifact_without_lock != expected["artifact"]
    ):
        raise LinuxApplicationAuditError("embedded shim build manifest identity changed")
    return manifest


def _object_value(value: Any, label: str, expected_keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise LinuxApplicationAuditError(f"{label} field set changed")
    return value


def _string_value(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
        or any(ord(character) < 0x20 for character in value)
    ):
        raise LinuxApplicationAuditError(f"{label} is not a bounded string")
    return value


def _validate_hook_input(
    hook_input: Path,
    repository: Path,
    reference_shim: Path,
    reference_runtime: Path,
    reference_provider: Path,
    expected_cc: Path,
    expected_ar: Path,
    expected_ld: Path,
) -> dict[str, object]:
    hook_input = _regular_file(
        hook_input.resolve(strict=True),
        "native-assets hook input",
        maximum=MAX_JSON_BYTES,
    )
    invocation = hook_input.parent.name
    if HOOK_INVOCATION.fullmatch(invocation) is None or hook_input.name != "input.json":
        raise LinuxApplicationAuditError("hook input is not in a canonical invocation directory")
    value = _strict_json(hook_input.read_bytes(), "native-assets hook input")
    value = _object_value(value, "native-assets hook input", {
        "assets", "config", "out_dir_shared", "out_file", "package_name", "package_root", "user_defines"
    })
    if value["assets"] != {} or value["package_name"] != "fonix":
        raise LinuxApplicationAuditError("native-assets hook package identity changed")
    package_root = Path(
        _string_value(value["package_root"], "native-assets hook package root")
    ).resolve(strict=True)
    if package_root != repository:
        raise LinuxApplicationAuditError("native-assets hook package root is not the frozen repository")
    application_root = hook_input.parents[4].resolve(strict=True)
    expected_input = (
        application_root
        / ".dart_tool/hooks_runner/fonix"
        / invocation
        / "input.json"
    )
    if hook_input != expected_input:
        raise LinuxApplicationAuditError("hook input escaped the external application")
    out_file = Path(
        _string_value(value["out_file"], "native-assets hook output path")
    ).resolve(strict=True)
    if out_file != (hook_input.parent / "output.json").resolve(strict=True):
        raise LinuxApplicationAuditError("native-assets hook output binding changed")
    shared = Path(
        _string_value(value["out_dir_shared"], "native-assets hook shared output")
    ).resolve(strict=True)
    expected_shared = (
        application_root / ".dart_tool/hooks_runner/shared/fonix/build"
    ).resolve(strict=True)
    if shared != expected_shared:
        raise LinuxApplicationAuditError("native-assets hook shared output identity changed")
    try:
        reference_shim.resolve(strict=True).relative_to(shared)
    except (OSError, ValueError) as error:
        raise LinuxApplicationAuditError("reference shim is outside the hook shared output") from error
    config = _object_value(
        value["config"],
        "native-assets hook config",
        {"build_asset_types", "extensions", "linking_enabled"},
    )
    if config["build_asset_types"] != ["code_assets/code"] or config["linking_enabled"] is not True:
        raise LinuxApplicationAuditError("native-assets hook build mode changed")
    extensions = _object_value(
        config["extensions"], "native-assets hook extensions", {"code_assets"}
    )
    code = _object_value(
        extensions["code_assets"],
        "native-assets hook code-assets config",
        {
            "c_compiler",
            "link_mode_preference",
            "target_architecture",
            "target_os",
        },
    )
    if code["target_os"] != "linux" or code["target_architecture"] != "x64":
        raise LinuxApplicationAuditError("native-assets hook target is not Linux x86_64")
    if code["link_mode_preference"] != "dynamic":
        raise LinuxApplicationAuditError("native-assets hook link mode changed")
    compiler = _object_value(
        code["c_compiler"],
        "native-assets hook compiler config",
        {"ar", "cc", "ld"},
    )
    expected_compilers = {
        "ar": expected_ar.resolve(strict=True),
        "cc": expected_cc.resolve(strict=True),
        "ld": expected_ld.resolve(strict=True),
    }
    compiler_evidence: dict[str, str] = {}
    for key in ("ar", "cc", "ld"):
        compiler_path = Path(_string_value(compiler[key], f"hook compiler {key}"))
        if not compiler_path.is_absolute():
            raise LinuxApplicationAuditError("native-assets hook compiler path is not absolute")
        compiler_path = _regular_file(
            compiler_path.resolve(strict=True),
            f"hook compiler {key}",
            maximum=256 * 1024 * 1024,
        )
        if compiler_path != expected_compilers[key]:
            raise LinuxApplicationAuditError("native-assets hook compiler identity changed")
        if not os.access(compiler_path, os.X_OK):
            raise LinuxApplicationAuditError("native-assets hook compiler is not executable")
        compiler_evidence[key] = _sha256(compiler_path)
    defines_root = _object_value(
        value["user_defines"], "native-assets hook user-defines", {"workspace_pubspec"}
    )
    workspace = _object_value(
        defines_root["workspace_pubspec"],
        "native-assets hook workspace defines",
        {"base_path", "defines"},
    )
    base_path = Path(
        _string_value(workspace["base_path"], "native-assets hook defines base path")
    ).resolve(strict=True)
    if base_path != (application_root / "pubspec.yaml").resolve(strict=True):
        raise LinuxApplicationAuditError("native-assets hook defines base path changed")
    defines = workspace["defines"]
    if defines != {
        "runtime_mode": "bundled",
        "artifact_cache": ".fonix-artifact-cache",
        "application_minimum_os": "14.0",
    }:
        raise LinuxApplicationAuditError("native-assets hook user defines changed")
    output_path = hook_input.parent / "output.json"
    output = _object_value(
        _strict_json(
            _regular_file(
                output_path, "native-assets hook output", maximum=MAX_JSON_BYTES
            ).read_bytes(),
            "native-assets hook output",
        ),
        "native-assets hook output",
        {
            "assets",
            "assets_for_linking",
            "dependencies",
            "status",
            "timestamp",
        },
    )
    if output["assets_for_linking"] != {} or output["status"] != "success":
        raise LinuxApplicationAuditError("native-assets hook output success/link state changed")
    assets = output["assets"]
    if not isinstance(assets, list) or len(assets) != 3:
        raise LinuxApplicationAuditError("native-assets hook output asset count changed")
    expected_assets = {
        "package:fonix/fonix_shim": reference_shim,
        "package:fonix/onnxruntime": reference_runtime,
        "package:fonix/onnxruntime_providers_shared": reference_provider,
    }
    seen: set[str] = set()
    for index, raw_asset in enumerate(assets):
        asset = _object_value(
            raw_asset,
            f"native-assets hook output asset {index}",
            {"encoding", "type"},
        )
        if asset["type"] != "code_assets/code":
            raise LinuxApplicationAuditError("native-assets hook output asset type changed")
        encoding = _object_value(
            asset["encoding"],
            f"native-assets hook output asset {index} encoding",
            {"file", "id", "link_mode"},
        )
        identifier = _string_value(
            encoding["id"], f"native-assets hook output asset {index} ID"
        )
        if identifier in seen or identifier not in expected_assets:
            raise LinuxApplicationAuditError("native-assets hook output asset ID changed")
        seen.add(identifier)
        if encoding["link_mode"] != {"type": "dynamic_loading_bundle"}:
            raise LinuxApplicationAuditError("native-assets hook output link mode changed")
        asset_path = Path(
            _string_value(
                encoding["file"], f"native-assets hook output asset {index} file"
            )
        ).resolve(strict=True)
        expected_path = expected_assets[identifier].resolve(strict=True)
        if asset_path != expected_path:
            raise LinuxApplicationAuditError("native-assets hook output file binding changed")
        try:
            asset_path.relative_to(shared)
        except ValueError as error:
            raise LinuxApplicationAuditError("native-assets hook asset escaped shared output") from error
    if seen != set(expected_assets):
        raise LinuxApplicationAuditError("native-assets hook output asset set changed")
    dependencies = output["dependencies"]
    if not isinstance(dependencies, list) or not 1 <= len(dependencies) <= 256:
        raise LinuxApplicationAuditError("native-assets hook dependency list is invalid")
    dependency_paths: list[Path] = []
    artifact_cache = (application_root / ".fonix-artifact-cache").resolve(strict=True)
    expected_dependency_paths = {
        repository / "native/versions.lock.yaml",
        repository / "src/CMakeLists.txt",
        repository / "src/fonix_exports.map",
        repository / "src/fonix_exports.apple",
        repository / "src/fonix_shim.def",
        repository / "third_party/onnxruntime/include/onnxruntime_c_api.h",
        repository / "third_party/onnxruntime/include/onnxruntime_ep_c_api.h",
        artifact_cache,
        artifact_cache / "onnxruntime-linux-x64-1.27.1.tgz",
        *sorted((repository / "src").glob("*.c")),
        *sorted((repository / "src").glob("*.h")),
    }
    allowed_dependencies: set[Path] = set()
    for expected_dependency in expected_dependency_paths:
        if expected_dependency == artifact_cache:
            _directory(
                expected_dependency,
                "expected native-assets hook artifact-cache dependency",
            )
        else:
            _regular_file(
                expected_dependency,
                "expected native-assets hook dependency",
            )
        allowed_dependencies.add(expected_dependency.resolve(strict=True))
    for index, raw_dependency in enumerate(dependencies):
        dependency = Path(
            _string_value(raw_dependency, f"native-assets hook dependency {index}")
        )
        if not dependency.is_absolute():
            raise LinuxApplicationAuditError(
                "native-assets hook dependency path is not absolute"
            )
        dependency = dependency.resolve(strict=True)
        if dependency == artifact_cache:
            _directory(dependency, "native-assets hook artifact-cache dependency")
        else:
            _regular_file(dependency, f"native-assets hook dependency {index}")
        dependency_paths.append(dependency)
        if not any(
            _is_relative_to(dependency, allowed)
            for allowed in (repository, application_root, shared)
        ):
            raise LinuxApplicationAuditError("native-assets hook dependency escaped trusted roots")
    normalized_dependencies = set(dependency_paths)
    if normalized_dependencies != allowed_dependencies:
        raise LinuxApplicationAuditError(
            "native-assets hook dependency set changed"
        )
    timestamp = _string_value(output["timestamp"], "native-assets hook timestamp")
    if HOOK_TIMESTAMP.fullmatch(timestamp) is None:
        raise LinuxApplicationAuditError("native-assets hook timestamp format changed")
    try:
        datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.000")
    except ValueError as error:
        raise LinuxApplicationAuditError("native-assets hook timestamp is invalid") from error
    return {
        "invocation": invocation,
        "inputSha256": _sha256(hook_input),
        "outputSha256": _sha256(output_path),
        "compilerSha256": compiler_evidence,
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _decode_utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LinuxApplicationAuditError(f"{label} output is not UTF-8") from error


def _execute_bounded(command: Sequence[str]) -> str:
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as error:
        raise LinuxApplicationAuditError("could not execute readelf") from error
    assert process.stdout is not None and process.stderr is not None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow: list[str] = []
    lock = threading.Lock()

    def drain(name: str, stream: Any) -> None:
        while chunk := stream.read(8192):
            with lock:
                remaining = MAX_READELF_BYTES - len(buffers[name])
                buffers[name].extend(chunk[: max(0, remaining)])
                if len(chunk) > remaining and not overflow:
                    overflow.append(name)
                    process.kill()
        stream.close()

    threads = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        return_code = process.wait(timeout=READELF_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise LinuxApplicationAuditError("readelf timed out") from error
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads) or overflow:
        raise LinuxApplicationAuditError("readelf output exceeded its bound")
    stdout = _decode_utf8(bytes(buffers["stdout"]), "readelf")
    stderr = _decode_utf8(bytes(buffers["stderr"]), "readelf stderr")
    if return_code != 0:
        raise LinuxApplicationAuditError(f"readelf failed with exit code {return_code}: {stderr[:512]}")
    if stderr:
        raise LinuxApplicationAuditError("readelf emitted unexpected stderr")
    return stdout


def _run_readelf(readelf: Path, binary: Path) -> str:
    return _execute_bounded(
        (
            str(readelf), "--wide", "--file-header", "--program-headers", "--dynamic",
            "--section-headers", "--dyn-syms", "--version-info", "--notes", str(binary),
        )
    )


def _parse_symbols(
    output: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, int], ...]]:
    lines = output.splitlines()
    headers = [
        (index, DYNAMIC_SYMBOL_HEADER.fullmatch(line))
        for index, line in enumerate(lines)
        if DYNAMIC_SYMBOL_HEADER.fullmatch(line) is not None
    ]
    if len(headers) != 1 or headers[0][1] is None:
        raise LinuxApplicationAuditError("ELF lacks one canonical dynamic-symbol table")
    header_index, header = headers[0]
    expected_count = int(header.group(1))
    if expected_count <= 0 or expected_count > 1_000_000:
        raise LinuxApplicationAuditError("ELF dynamic-symbol count is outside its bound")
    cursor = header_index + 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    if cursor >= len(lines) or not re.fullmatch(
        r"\s*Num:\s+Value\s+Size\s+Type\s+Bind\s+Vis\s+Ndx\s+Name\s*",
        lines[cursor],
    ):
        raise LinuxApplicationAuditError("ELF dynamic-symbol heading changed")
    cursor += 1
    rows: list[re.Match[str] | None] = []
    while cursor < len(lines) and lines[cursor].strip():
        line = lines[cursor]
        if not rows and DYNAMIC_NULL_SYMBOL.fullmatch(line) is not None:
            rows.append(None)
        else:
            match = DYNAMIC_SYMBOL.fullmatch(line)
            if match is None:
                raise LinuxApplicationAuditError(
                    "ELF contains an unparsed dynamic-symbol row"
                )
            rows.append(match)
        cursor += 1
    if len(rows) != expected_count or not rows or rows[0] is not None:
        raise LinuxApplicationAuditError("ELF dynamic-symbol row count changed")
    defined: list[str] = []
    undefined: list[str] = []
    undefined_versions: list[tuple[str, int]] = []
    for match in rows[1:]:
        assert match is not None
        name = match.group("name")
        if match.group("section") == "UND":
            undefined.append(name)
            raw_index = match.group("version_index")
            if "@" in name:
                if raw_index is None:
                    raise LinuxApplicationAuditError(
                        "versioned undefined symbol lacks its version index"
                    )
                undefined_versions.append((name, int(raw_index)))
            elif raw_index is not None:
                raise LinuxApplicationAuditError(
                    "unversioned undefined symbol has a version index"
                )
        elif match.group("binding") != "LOCAL":
            defined.append(name)
    return tuple(defined), tuple(undefined), tuple(undefined_versions)


def _parse_versions(
    output: str,
    path: str,
    needed: Sequence[str],
    undefined_version_rows: Sequence[tuple[str, int]],
) -> dict[str, str | None]:
    lines = output.splitlines()
    headers = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := VERSION_NEEDS_HEADER.fullmatch(line)) is not None
    ]
    if len(headers) != 1:
        raise LinuxApplicationAuditError(f"{path} version-needs structure changed")
    header_index, header = headers[0]
    declared_files = int(header.group(1))
    if declared_files <= 0 or declared_files > len(needed):
        raise LinuxApplicationAuditError(f"{path} version-needs count changed")
    cursor = header_index + 1
    while cursor < len(lines) and (
        not lines[cursor].strip() or lines[cursor].lstrip().startswith("Addr:")
    ):
        cursor += 1
    needs: dict[str, tuple[tuple[str, int], ...]] = {}
    version_indices: dict[int, tuple[str, str]] = {}
    while cursor < len(lines) and len(needs) < declared_files:
        file_match = VERSION_NEED_FILE.fullmatch(lines[cursor])
        if file_match is None:
            raise LinuxApplicationAuditError(
                f"{path} contains an unparsed version-needs file row"
            )
        filename = file_match.group("file")
        count = int(file_match.group("count"))
        if filename in needs or filename not in needed or count <= 0 or count > 4096:
            raise LinuxApplicationAuditError(f"{path} version-needs file changed")
        cursor += 1
        names: list[tuple[str, int]] = []
        for _ in range(count):
            if cursor >= len(lines):
                raise LinuxApplicationAuditError(
                    f"{path} version-needs auxiliary rows are truncated"
                )
            auxiliary = VERSION_NEED_AUX.fullmatch(lines[cursor])
            if auxiliary is None or auxiliary.group("flags") != "none":
                raise LinuxApplicationAuditError(
                    f"{path} version-needs auxiliary row changed"
                )
            name = auxiliary.group("name")
            index = int(auxiliary.group("index"))
            if (
                index <= 1
                or index > 65535
                or any(existing_name == name for existing_name, _ in names)
                or index in version_indices
            ):
                raise LinuxApplicationAuditError(
                    f"{path} duplicates a version-needs name or index"
                )
            names.append((name, index))
            version_indices[index] = (filename, name)
            cursor += 1
        needs[filename] = tuple(names)
    if len(needs) != declared_files or "libc.so.6" not in needs:
        raise LinuxApplicationAuditError(f"{path} version-needs structure changed")
    required_names = {
        name for names in needs.values() for name, _ in names
    }
    referenced_indices: set[int] = set()
    for symbol, index in undefined_version_rows:
        requirement = version_indices.get(index)
        if requirement is None or symbol.rsplit("@", 1)[1] != requirement[1]:
            raise LinuxApplicationAuditError(
                f"{path} undefined-import version index changed"
            )
        referenced_indices.add(index)
    if referenced_indices != set(version_indices):
        raise LinuxApplicationAuditError(
            f"{path} version-needs and undefined-import versions differ"
        )
    found: dict[str, set[tuple[int, ...]]] = {key: set() for key in VERSION_MAXIMA}
    for name in sorted(required_names):
        match = VERSION_TOKEN.fullmatch(name)
        if match is None:
            if name.startswith(("GLIBC_", "GLIBCXX_", "CXXABI_")):
                raise LinuxApplicationAuditError(
                    f"ELF contains unsupported version namespace {name}"
                )
            continue
        family, raw = match.groups()
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", raw) is None:
            raise LinuxApplicationAuditError(
                f"ELF contains unsupported {family}_{raw} version namespace"
            )
        found[family].add(tuple(int(part) for part in raw.split(".")))
    if not found["GLIBC"]:
        raise LinuxApplicationAuditError("ELF lacks versioned GLIBC dependency evidence")
    result: dict[str, str | None] = {}
    for family, versions in found.items():
        if versions and max(versions) > VERSION_MAXIMA[family]:
            rendered = ".".join(str(part) for part in max(versions))
            raise LinuxApplicationAuditError(f"ELF requires unsupported {family}_{rendered}")
        result[family] = (
            ".".join(str(part) for part in max(versions)) if versions else None
        )
    return result


def _require_elf_header(output: str, path: str) -> None:
    fields: dict[str, str] = {}
    for name in ("Class", "Data", "Type", "Machine"):
        values = re.findall(
            rf"^\s*{re.escape(name)}:\s*(.*?)\s*$", output, re.MULTILINE
        )
        if len(values) != 1:
            raise LinuxApplicationAuditError(
                f"{path} does not contain one canonical ELF {name} field"
            )
        fields[name] = values[0]
    if (
        fields["Class"] != "ELF64"
        or fields["Data"] != "2's complement, little endian"
        or fields["Machine"] != "Advanced Micro Devices X86-64"
    ):
        raise LinuxApplicationAuditError(f"{path} is not the required x86_64 ELF")
    if not fields["Type"].startswith("DYN "):
        raise LinuxApplicationAuditError(f"{path} is not an ELF DYN object")


def _dynamic_entries(output: str, path: str) -> dict[str, list[str]]:
    headers = [
        match.group(1)
        for line in output.splitlines()
        if (match := DYNAMIC_SECTION_HEADER.fullmatch(line)) is not None
    ]
    ordered: list[tuple[str, str]] = []
    if len(headers) != 1 or int(headers[0]) <= 0:
        raise LinuxApplicationAuditError(f"{path} lacks one canonical dynamic section")
    entries: dict[str, list[str]] = {}
    for line in output.splitlines():
        match = DYNAMIC_ENTRY.fullmatch(line)
        if match is not None:
            tag = match.group("tag")
            value = match.group("value").strip()
            ordered.append((tag, value))
            entries.setdefault(tag, []).append(value)
    if len(ordered) != int(headers[0]):
        raise LinuxApplicationAuditError(f"{path} dynamic-entry count changed")
    if ordered[-1] != ("NULL", "0x0") or sum(tag == "NULL" for tag, _ in ordered) != 1:
        raise LinuxApplicationAuditError(f"{path} dynamic section lacks one terminal DT_NULL")
    return entries


def _dynamic_values(
    entries: Mapping[str, Sequence[str]],
    tag: str,
    pattern: re.Pattern[str],
    path: str,
) -> list[str]:
    values: list[str] = []
    for raw in entries.get(tag, ()):
        match = pattern.fullmatch(raw)
        if match is None:
            raise LinuxApplicationAuditError(f"{path} has malformed DT_{tag}")
        values.append(match.group(1))
    return values


def _require_hardening(
    output: str,
    path: str,
    dynamic_entries: Mapping[str, Sequence[str]],
) -> str:
    program_headers: dict[str, list[tuple[str, int, int, int]]] = {}
    for line in output.splitlines():
        match = PROGRAM_HEADER.match(line)
        if match is not None:
            flags = "".join(match.group("flags").split())
            program_headers.setdefault(match.group("type"), []).append(
                (
                    flags,
                    int(match.group("vaddr"), 16),
                    int(match.group("filesz"), 16),
                    int(match.group("memsz"), 16),
                )
            )
    loads = program_headers.get("LOAD", [])
    if not loads or any("W" in item[0] and "E" in item[0] for item in loads):
        raise LinuxApplicationAuditError(f"{path} has missing or W+X PT_LOAD segments")
    relro = program_headers.get("GNU_RELRO", [])
    if len(relro) != 1 or relro[0][0] != "R" or relro[0][2] <= 0 or relro[0][3] <= 0:
        raise LinuxApplicationAuditError(f"{path} lacks one read-only GNU RELRO segment")
    dynamic = program_headers.get("DYNAMIC", [])
    if len(dynamic) != 1 or dynamic[0][0] != "RW" or dynamic[0][2] <= 0 or dynamic[0][3] <= 0:
        raise LinuxApplicationAuditError(f"{path} lacks one nonempty PT_DYNAMIC segment")
    _, dynamic_start, _, dynamic_memory = dynamic[0]
    _, relro_start, _, relro_memory = relro[0]
    relro_end = relro_start + relro_memory
    if dynamic_start < relro_start or dynamic_start + dynamic_memory > relro_end:
        raise LinuxApplicationAuditError(f"{path} PT_DYNAMIC is outside GNU RELRO")
    relro_sections: dict[str, tuple[int, int, str, str]] = {}
    for line in output.splitlines():
        match = RELRO_SECTION.fullmatch(line)
        if match is None:
            continue
        name = match.group("name")
        if name in relro_sections:
            raise LinuxApplicationAuditError(f"{path} duplicates a RELRO section")
        relro_sections[name] = (
            int(match.group("address"), 16),
            int(match.group("size"), 16),
            match.group("flags"),
            match.group("type"),
        )
    if ".dynamic" not in relro_sections or ".got" not in relro_sections:
        raise LinuxApplicationAuditError(f"{path} lacks closed dynamic/GOT sections")
    for name, (address, size, flags_value, section_type) in relro_sections.items():
        expected_type = "DYNAMIC" if name == ".dynamic" else "PROGBITS"
        if (
            size <= 0
            or flags_value != "WA"
            or section_type != expected_type
            or address < relro_start
            or address + size > relro_end
        ):
            raise LinuxApplicationAuditError(
                f"{path} writable {name} is outside GNU RELRO"
            )
    dynamic_section = relro_sections[".dynamic"]
    if dynamic_section[0] != dynamic_start or dynamic_section[1] != dynamic_memory:
        raise LinuxApplicationAuditError(
            f"{path} PT_DYNAMIC and .dynamic ranges differ"
        )
    pltgot_values = _dynamic_values(
        dynamic_entries, "PLTGOT", ADDRESS_VALUE, path
    )
    if len(pltgot_values) != 1:
        raise LinuxApplicationAuditError(f"{path} lacks one DT_PLTGOT")
    pltgot = int(pltgot_values[0], 16)
    got_ranges = [
        (address, address + size)
        for name, (address, size, _, _) in relro_sections.items()
        if name in {".got", ".got.plt"}
    ]
    if not any(start <= pltgot < end for start, end in got_ranges):
        raise LinuxApplicationAuditError(f"{path} DT_PLTGOT is outside GNU RELRO")
    stacks = program_headers.get("GNU_STACK", [])
    if len(stacks) != 1 or stacks[0][0] != "RW":
        raise LinuxApplicationAuditError(f"{path} has an executable or ambiguous GNU stack")
    interpreters = INTERPRETER.findall(output)
    if path == APPLICATION_EXECUTABLE:
        interprets = program_headers.get("INTERP", [])
        if len(interprets) != 1 or interprets[0][0] != "R" or interpreters != [
            "/lib64/ld-linux-x86-64.so.2"
        ]:
            raise LinuxApplicationAuditError("application runner PT_INTERP changed")
    elif program_headers.get("INTERP") or interpreters:
        raise LinuxApplicationAuditError(f"{path} unexpectedly contains PT_INTERP")
    forbidden = FORBIDDEN_DYNAMIC_TAGS.intersection(dynamic_entries)
    if forbidden:
        raise LinuxApplicationAuditError(
            f"{path} contains a forbidden dynamic tag: {sorted(forbidden)[0]}"
        )
    flags_values = dynamic_entries.get("FLAGS", [])
    flags_one_values = dynamic_entries.get("FLAGS_1", [])
    if flags_values != ["BIND_NOW"]:
        if any("TEXTREL" in value.split() for value in flags_values):
            raise LinuxApplicationAuditError(f"{path} contains forbidden DF_TEXTREL")
        raise LinuxApplicationAuditError(f"{path} dynamic FLAGS changed")
    expected_flags_one = [
        "Flags: NOW PIE" if path == APPLICATION_EXECUTABLE else "Flags: NOW"
    ]
    if flags_one_values != expected_flags_one:
        if path == APPLICATION_EXECUTABLE:
            raise LinuxApplicationAuditError("application runner lacks exact DF_1_PIE/NOW")
        raise LinuxApplicationAuditError(f"{path} dynamic FLAGS_1 changed")
    if "TEXTREL" in flags_values[0].split():
        raise LinuxApplicationAuditError(f"{path} contains forbidden DF_TEXTREL")
    ids = re.findall(r"^\s*Build ID: ([0-9a-fA-F]+)\s*$", output, re.MULTILINE)
    if len(ids) != 1 or BUILD_ID.fullmatch(ids[0].lower()) is None:
        raise LinuxApplicationAuditError(f"{path} lacks one closed-length GNU build ID")
    if path == APPLICATION_EXECUTABLE:
        if len(ids[0]) != 40:
            raise LinuxApplicationAuditError("application runner build ID is not SHA-1")
        if flags_one_values != ["Flags: NOW PIE"]:
            raise LinuxApplicationAuditError("application runner lacks DF_1_PIE")
    return ids[0].lower()


def _parse_elf(path: str, binary: Path, output: str) -> ElfRecord:
    _require_elf_header(output, path)
    dynamic_entries = _dynamic_entries(output, path)
    needed = tuple(_dynamic_values(dynamic_entries, "NEEDED", NEEDED_VALUE, path))
    if len(needed) != len(set(needed)):
        raise LinuxApplicationAuditError(f"{path} duplicates a DT_NEEDED entry")
    sonames = _dynamic_values(dynamic_entries, "SONAME", SONAME_VALUE, path)
    if len(sonames) > 1 or (sonames[0] if sonames else None) != EXPECTED_SONAMES[path]:
        raise LinuxApplicationAuditError(f"{path} SONAME changed")
    if dynamic_entries.get("RPATH"):
        raise LinuxApplicationAuditError(f"{path} contains forbidden DT_RPATH")
    runpaths = _dynamic_values(dynamic_entries, "RUNPATH", RUNPATH_VALUE, path)
    if tuple(runpaths) != EXPECTED_RUNPATHS[path]:
        raise LinuxApplicationAuditError(f"{path} RUNPATH changed")
    for value in runpaths:
        if value.startswith("/") or ":" in value or ".." in value or value not in {"$ORIGIN", "$ORIGIN/lib"}:
            raise LinuxApplicationAuditError(f"{path} has an unsafe RUNPATH")
    expected_needed = EXPECTED_NEEDED[path]
    if needed != expected_needed:
        raise LinuxApplicationAuditError(f"{path} DT_NEEDED order or set changed")
    if path == f"lib/{SHIM_NAME}" and any("onnxruntime" in item.lower() for item in needed):
        raise LinuxApplicationAuditError("shim must not link ONNX Runtime")
    defined, undefined, undefined_version_rows = _parse_symbols(output)
    if path == f"lib/{SHIM_NAME}":
        for line in output.splitlines():
            symbol = DYNAMIC_SYMBOL.match(line)
            if (
                symbol is None
                or symbol.group("section") == "UND"
                or symbol.group("binding") == "LOCAL"
            ):
                continue
            if symbol.group("binding") != "GLOBAL":
                raise LinuxApplicationAuditError(
                    "shim contains a defined non-GLOBAL dynamic symbol"
                )
            if symbol.group("name") == "FONIX_DORT_1.0":
                if (
                    symbol.group("type") != "OBJECT"
                    or symbol.group("visibility") != "DEFAULT"
                    or symbol.group("section") != "ABS"
                ):
                    raise LinuxApplicationAuditError(
                        "shim export-version node metadata changed"
                    )
                continue
            if (
                symbol.group("type") != "FUNC"
                or symbol.group("visibility") != "DEFAULT"
                or not symbol.group("section").isdigit()
            ):
                raise LinuxApplicationAuditError(
                    "shim exports must be FUNC GLOBAL DEFAULT definitions"
                )
        if any(name.split("@", 1)[0].startswith("Ort") for name in undefined):
            raise LinuxApplicationAuditError(
                "shim contains a forbidden undefined ONNX Runtime symbol"
            )
    record = ElfRecord(
        path=path,
        sha256=_sha256(binary),
        size_bytes=binary.stat().st_size,
        needed=needed,
        soname=sonames[0] if sonames else None,
        runpath=tuple(runpaths),
        build_id=_require_hardening(output, path, dynamic_entries),
        version_maxima=_parse_versions(
            output, path, needed, undefined_version_rows
        ),
        defined_symbols=defined,
        undefined_symbols=undefined,
    )
    if path == APPLICATION_EXECUTABLE and not any(
        name.split("@", 1)[0] == "__stack_chk_fail" for name in undefined
    ):
        raise LinuxApplicationAuditError("application runner lacks stack-protector evidence")
    return record


def _expected_dort_exports(repository: Path) -> frozenset[str]:
    source = _regular_file(repository / "src/fonix_exports.map", "Linux export map", maximum=64 * 1024).read_text(encoding="utf-8")
    exports = frozenset(re.findall(r"^\s+(dort_[A-Za-z0-9_]+);\s*$", source, re.MULTILINE))
    if len(exports) != 67 or source.count("FONIX_DORT_1.0") != 1 or "local:\n    *;" not in source:
        raise LinuxApplicationAuditError("trusted Linux export map changed")
    return exports


def _validate_exports(records: Sequence[ElfRecord], expected_dort: frozenset[str]) -> None:
    shims = [record for record in records if record.path == f"lib/{SHIM_NAME}"]
    if len(shims) != 1:
        raise LinuxApplicationAuditError("ELF records lack one exact shim")
    shim = shims[0]
    if shim.defined_symbols.count("FONIX_DORT_1.0") != 1:
        raise LinuxApplicationAuditError("shim export-version node is missing")
    exported_dort: list[str] = []
    other: list[str] = []
    for name in shim.defined_symbols:
        base = name.split("@", 1)[0]
        if base == "FONIX_DORT_1.0":
            continue
        match = VERSIONED_DORT.fullmatch(name)
        if match is None:
            other.append(name)
        else:
            exported_dort.append(match.group(1))
    if (
        len(exported_dort) != len(expected_dort)
        or frozenset(exported_dort) != expected_dort
        or other
    ):
        raise LinuxApplicationAuditError("shim exported-symbol contract changed")
    providers = [
        record.path
        for record in records
        for name in record.defined_symbols
        if name.split("@", 1)[0] == "OrtGetApiBase"
    ]
    if providers != [f"lib/{RUNTIME_NAME}"]:
        raise LinuxApplicationAuditError("application contains zero or duplicate ONNX Runtime exports")


def audit_application(
    *,
    repository: Path,
    application: Path,
    hook_input: Path,
    reference_shim: Path,
    reference_runtime: Path,
    reference_provider: Path,
    staging_directory: Path,
    readelf: Path,
    expected_cc: Path,
    expected_ar: Path,
    expected_ld: Path,
) -> dict[str, object]:
    repository = _directory(repository.resolve(strict=True), "Fonix repository")
    application = _directory(application, "provided final application").resolve(
        strict=True
    )
    application = _directory(application, "final application")
    readelf = _regular_file(readelf.resolve(strict=True), "GNU readelf", maximum=16 * 1024 * 1024)
    if not os.access(readelf, os.X_OK):
        raise LinuxApplicationAuditError("GNU readelf is not executable")
    expected_cc = _regular_file(expected_cc.resolve(strict=True), "expected hook compiler")
    expected_ar = _regular_file(expected_ar.resolve(strict=True), "expected hook archiver")
    expected_ld = _regular_file(expected_ld.resolve(strict=True), "expected hook linker")
    tree = _tree_identity(application)
    assets = _validate_repository_assets(repository, application)
    _require_identity(
        application / "lib/libflutter_linux_gtk.so",
        FLUTTER_ENGINE_SHA256,
        FLUTTER_ENGINE_SIZE_BYTES,
        "pinned Flutter Linux engine",
    )
    _require_identity(
        application / "data/icudtl.dat",
        FLUTTER_ICU_SHA256,
        FLUTTER_ICU_SIZE_BYTES,
        "pinned Flutter ICU data",
    )
    staging_directory = _directory(staging_directory.resolve(strict=True), "hook staging directory")
    resolver_manifest = _validate_resolver_manifest(repository, staging_directory)
    packaged_fonix = application / "data/flutter_assets/assets/fonix"
    _require_equal(
        staging_directory / "fonix-native-artifact-manifest.json",
        packaged_fonix / "fonix-native-artifact-manifest.json",
        "resolver manifest asset",
    )
    _require_equal(
        staging_directory / "notices/ThirdPartyNotices.txt",
        packaged_fonix / "ThirdPartyNotices.txt",
        "third-party notice asset",
    )
    _require_identity(packaged_fonix / "ThirdPartyNotices.txt", NOTICE_SHA256, NOTICE_SIZE_BYTES, "packaged third-party notices")
    reference_shim = _regular_file(reference_shim.resolve(strict=True), "reference hook shim")
    reference_runtime = _regular_file(reference_runtime.resolve(strict=True), "reference staged runtime")
    reference_provider = _regular_file(reference_provider.resolve(strict=True), "reference staged provider")
    _require_identity(reference_runtime, RUNTIME_SHA256, RUNTIME_SIZE_BYTES, "reference staged runtime")
    _require_identity(reference_provider, PROVIDER_SHA256, PROVIDER_SIZE_BYTES, "reference staged provider")
    _require_equal(reference_shim, application / f"lib/{SHIM_NAME}", "hook shim")
    _require_equal(reference_runtime, application / f"lib/{RUNTIME_NAME}", "ONNX Runtime")
    _require_equal(reference_provider, application / f"lib/{PROVIDER_NAME}", "provider-shared library")
    hook = _validate_hook_input(
        hook_input.resolve(strict=True),
        repository,
        reference_shim,
        reference_runtime,
        reference_provider,
        expected_cc,
        expected_ar,
        expected_ld,
    )
    embedded = _extract_build_manifest(reference_shim)
    lock_sha = _sha256(repository / "native/versions.lock.yaml")
    if embedded["artifact"]["lockSha256"] != lock_sha:
        raise LinuxApplicationAuditError("shim build manifest lock binding changed")
    records = [
        _parse_elf(relative, application / relative, _run_readelf(readelf, application / relative))
        for relative in ELF_PATHS
    ]
    _validate_exports(records, _expected_dort_exports(repository))
    report: dict[str, object] = {
        "schemaVersion": 1,
        "result": "passed",
        "target": {"os": "linux", "architecture": "x86_64", "minimumOs": "glibc-2.27"},
        "artifact": {
            "id": ARTIFACT_ID,
            "sourceSha256": ARTIFACT_SHA256,
            "resolverManifestSha256": _sha256(
                staging_directory / "fonix-native-artifact-manifest.json"
            ),
        },
        "applicationTree": {
            "fileCount": tree.file_count,
            "byteCount": tree.byte_count,
            "sha256": tree.tree_sha256,
            "identityFormat": "canonical-root-directory-file-size-sha256-mode-v2",
        },
        "assets": assets,
        "flutterRuntime": {
            "engineSha256": FLUTTER_ENGINE_SHA256,
            "icuSha256": FLUTTER_ICU_SHA256,
        },
        "hook": hook,
        "shimBuildManifestSha256": hashlib.sha256(
            json.dumps(embedded, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "elf": [
            {
                "path": record.path,
                "sha256": record.sha256,
                "sizeBytes": record.size_bytes,
                "soname": record.soname,
                "needed": list(record.needed),
                "runpath": list(record.runpath),
                "buildId": record.build_id,
                "versionMaxima": dict(record.version_maxima),
            }
            for record in records
        ],
        "dortExportCount": 67,
        "hardening": {
            "relro": "full",
            "bindNow": True,
            "nxStack": True,
            "buildIds": "present-closed-per-elf-runner-sha1",
        },
        "claimBoundary": (
            "Closed installed bytes, hook provenance, ELF metadata, symbol/version floors, "
            "and hardening only; target-host execution is required separately."
        ),
    }
    if resolver_manifest.get("artifactId") != ARTIFACT_ID:
        raise LinuxApplicationAuditError("resolver manifest selected the wrong artifact")
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_REPORT_BYTES:
        raise LinuxApplicationAuditError("Linux application audit report exceeds its bound")
    if str(repository) in encoded or str(application) in encoded:
        raise LinuxApplicationAuditError("Linux application audit report leaked a path")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--hook-input", type=Path, required=True)
    parser.add_argument("--reference-shim", type=Path, required=True)
    parser.add_argument("--reference-runtime", type=Path, required=True)
    parser.add_argument("--reference-provider", type=Path, required=True)
    parser.add_argument("--staging-directory", type=Path, required=True)
    parser.add_argument("--readelf", type=Path, default=Path("/usr/bin/readelf"))
    parser.add_argument("--expected-cc", type=Path, required=True)
    parser.add_argument("--expected-ar", type=Path, required=True)
    parser.add_argument("--expected-ld", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        _require_isolated_python_invocation()
        arguments = _parser().parse_args(argv)
        report = audit_application(
            repository=arguments.repository,
            application=arguments.application,
            hook_input=arguments.hook_input,
            reference_shim=arguments.reference_shim,
            reference_runtime=arguments.reference_runtime,
            reference_provider=arguments.reference_provider,
            staging_directory=arguments.staging_directory,
            readelf=arguments.readelf,
            expected_cc=arguments.expected_cc,
            expected_ar=arguments.expected_ar,
            expected_ld=arguments.expected_ld,
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (LinuxApplicationAuditError, OSError, ValueError) as error:
        print(f"Linux application audit error: {error}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, IndexError):
        print("Linux application audit error: malformed closed-contract input", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
