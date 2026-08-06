#!/usr/bin/env python3
"""Validate Android Fonix/Sherpa load-order and lifecycle evidence.

The target receipt is not trusted by itself. This validator rehashes every
named input, checks a host-normalized target/logcat binding, inspects the final
APK with the repository native-library verifier, and validates exact reference
bytes plus bounded VAD invariants. It writes no record unless every binding and
lifecycle claim is internally consistent.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import types
from typing import Any, Iterable, Iterator
import zipfile


sys.dont_write_bytecode = True
REPOSITORY = Path(__file__).resolve().parents[2]
RECEIPT_SCHEMA_PATH = REPOSITORY / "templates/android/load_order_receipt.schema.json"
VERIFIER_PATH = REPOSITORY / "templates/android/verify_native_libs.py"
MAX_TOOL_SOURCE_BYTES = 16 * 1024 * 1024


class LoadOrderReceiptError(RuntimeError):
    """The submitted evidence cannot support a load-order validation record."""


def _read_only_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    return flags


def _read_stable_source(
    path: Path, label: str
) -> tuple[bytes, dict[str, Any]]:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise LoadOrderReceiptError(f"missing {label}: {path}") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > MAX_TOOL_SOURCE_BYTES
    ):
        raise LoadOrderReceiptError(f"{label} is not a bounded regular file")
    descriptor = os.open(path, _read_only_flags())
    try:
        opened = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if before_identity != opened_identity:
            raise LoadOrderReceiptError(f"{label} changed while it was being opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_TOOL_SOURCE_BYTES:
                raise LoadOrderReceiptError(f"{label} exceeds its source bound")
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
        opened_after_identity = (
            opened_after.st_dev,
            opened_after.st_ino,
            opened_after.st_mode,
            opened_after.st_size,
            opened_after.st_mtime_ns,
            opened_after.st_ctime_ns,
        )
        if before_identity != opened_after_identity:
            raise LoadOrderReceiptError(f"{label} changed while it was being read")
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except FileNotFoundError as error:
        raise LoadOrderReceiptError(f"{label} disappeared while it was being read") from error
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise LoadOrderReceiptError(f"{label} changed while it was being read")
    raw = b"".join(chunks)
    if len(raw) != before.st_size:
        raise LoadOrderReceiptError(f"{label} changed while it was being read")
    return raw, {"sizeBytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


_VALIDATOR_SOURCE, VALIDATOR_IDENTITY = _read_stable_source(
    Path(__file__), "load-order receipt validator"
)
_VERIFIER_SOURCE, VERIFIER_IDENTITY = _read_stable_source(
    VERIFIER_PATH, "Android native-library verifier"
)
VERIFIER = types.ModuleType("fonix_android_load_order_native_verifier")
VERIFIER.__file__ = str(VERIFIER_PATH)
exec(compile(_VERIFIER_SOURCE, str(VERIFIER_PATH), "exec"), VERIFIER.__dict__)


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SMALL_EVIDENCE_BYTES = 128 * 1024 * 1024
MAX_APK_BYTES = 4 * 1024 * 1024 * 1024
MAX_MODEL_BYTES = 4 * 1024 * 1024 * 1024
MAX_FIXTURE_BYTES = 128 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_CHALLENGE_BYTES = 1024
MAX_CYCLES = 64
MAX_SEGMENTS = 32
MAX_COUNT = 1_000_000

SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
REVISION_PREFIX = re.compile(r"^[0-9a-f]{7,40}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
APPLICATION_ID = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$"
)

ABIS = frozenset({"armeabi-v7a", "arm64-v8a", "x86", "x86_64"})
LOAD_ORDERS = frozenset({"dart-first", "sherpa-first"})
BUILD_TYPES = frozenset({"debug", "release-minified"})
ANDROID_PLATFORM_PACKAGES = {
    "armeabi-v7a": "sherpa_onnx_android_armeabi",
    "arm64-v8a": "sherpa_onnx_android_arm64",
    "x86": "sherpa_onnx_android_x86",
    "x86_64": "sherpa_onnx_android_x86_64",
}
SHERPA_HOSTED_SHA256 = {
    "1.13.4": {
        "sherpa_onnx": "889c03cf7a8788795e3a6d35bf9f20b66d1862ea7a73a1b6aaf6e450715c870a",
        "sherpa_onnx_android_armeabi": (
            "9e96729d99567c3f64fc6f4c232ef04b57ae4ef3bf1b21364ee6e461103cb7cf"
        ),
        "sherpa_onnx_android_arm64": (
            "0337650bc2357f39b751f1b9ced37770de3b60026effe66473b557346b4e3f97"
        ),
        "sherpa_onnx_android_x86": (
            "6eaa462a24bf881c8ee2b3ab5b9fad3d310b714fc9d3ac4b9f650d0a90c9d0ab"
        ),
        "sherpa_onnx_android_x86_64": (
            "181aa0f0968adf2cf2dcb369c879ea372653864538b82772877e22748a80254f"
        ),
    }
}
SHERPA_SOURCE_REVISIONS = {
    "1.13.4": "142807252687d81b40d6315f23470a1512a00de3",
}

EXPECTED_PROFILE = {
    "provider": "cpu",
    "sampleRateHz": 16000,
    "windowSamples": 512,
    "numThreads": 1,
    "thresholdMillionths": 500000,
    "minimumSpeechMilliseconds": 250,
    "minimumSilenceMilliseconds": 800,
    "maximumSpeechMilliseconds": 30000,
    "bufferMilliseconds": 60000,
}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LoadOrderReceiptError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _exact_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise LoadOrderReceiptError(f"{label} has an unexpected field set")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LoadOrderReceiptError(f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise LoadOrderReceiptError(f"{label} must be an array")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise LoadOrderReceiptError(f"{label} must be an integer in range")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise LoadOrderReceiptError(f"{label} must be a lowercase SHA-256")
    return value


def _token(value: Any, label: str) -> str:
    if not isinstance(value, str) or TOKEN.fullmatch(value) is None:
        raise LoadOrderReceiptError(f"{label} must be a bounded token")
    return value


def _semver(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or SEMVER.fullmatch(value) is None
    ):
        raise LoadOrderReceiptError(f"{label} must be strict semantic versioning")
    return value


def _is_true(value: Any, label: str) -> None:
    if value is not True:
        raise LoadOrderReceiptError(f"{label} must be true")


def _is_false(value: Any, label: str) -> None:
    if value is not False:
        raise LoadOrderReceiptError(f"{label} must be false")


def _is_null(value: Any, label: str) -> None:
    if value is not None:
        raise LoadOrderReceiptError(f"{label} must be null")


def _regular_file(path: Path, label: str, maximum: int) -> os.stat_result:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise LoadOrderReceiptError(f"missing {label}: {path}") from error
    if not stat.S_ISREG(status.st_mode):
        raise LoadOrderReceiptError(f"{label} is not a regular file: {path}")
    if status.st_size <= 0 or status.st_size > maximum:
        raise LoadOrderReceiptError(f"{label} size is outside its bound")
    return status


def _status_identity(status: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _opened_file_matches(
    before: os.stat_result, opened: os.stat_result, label: str
) -> None:
    if (
        not stat.S_ISREG(opened.st_mode)
        or before.st_dev != opened.st_dev
        or before.st_ino != opened.st_ino
        or before.st_mode != opened.st_mode
        or before.st_size != opened.st_size
        or before.st_mtime_ns != opened.st_mtime_ns
        or before.st_ctime_ns != opened.st_ctime_ns
    ):
        raise LoadOrderReceiptError(f"{label} changed while it was being opened")


def _unchanged_file(path: Path, before: os.stat_result, label: str) -> None:
    try:
        after = path.lstat()
    except FileNotFoundError as error:
        raise LoadOrderReceiptError(f"{label} disappeared while being read") from error
    if _status_identity(before) != _status_identity(after):
        raise LoadOrderReceiptError(f"{label} changed while it was being read")


def _stable_identity(path: Path, label: str, maximum: int) -> dict[str, Any]:
    before = _regular_file(path, label, maximum)
    digest = hashlib.sha256()
    descriptor = os.open(path, _read_only_flags())
    try:
        _opened_file_matches(before, os.fstat(descriptor), label)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        if _status_identity(before) != _status_identity(os.fstat(descriptor)):
            raise LoadOrderReceiptError(f"{label} changed while it was being read")
    finally:
        os.close(descriptor)
    _unchanged_file(path, before, label)
    return {"sizeBytes": before.st_size, "sha256": digest.hexdigest()}


def _read_stable_bytes(
    path: Path, label: str, maximum: int
) -> tuple[bytes, dict[str, Any]]:
    before = _regular_file(path, label, maximum)
    descriptor = os.open(path, _read_only_flags())
    try:
        _opened_file_matches(before, os.fstat(descriptor), label)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if _status_identity(before) != _status_identity(os.fstat(descriptor)):
            raise LoadOrderReceiptError(f"{label} changed while it was being read")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    _unchanged_file(path, before, label)
    if len(raw) != before.st_size:
        raise LoadOrderReceiptError(f"{label} changed while it was being read")
    return raw, {
        "sizeBytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


@contextmanager
def _private_snapshot(
    path: Path, label: str, maximum: int, *, suffix: str
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield a private, read-only copy of one exact regular-file identity."""

    before = _regular_file(path, label, maximum)
    try:
        source_descriptor = os.open(path, _read_only_flags())
    except OSError as error:
        raise LoadOrderReceiptError(f"could not open {label}: {error}") from error
    try:
        _opened_file_matches(before, os.fstat(source_descriptor), label)
        with tempfile.TemporaryDirectory(prefix="fonix-load-order-snapshot-") as root:
            snapshot = Path(root) / f"artifact{suffix}"
            destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                destination_flags |= os.O_NOFOLLOW
            destination_descriptor = os.open(snapshot, destination_flags, 0o600)
            digest = hashlib.sha256()
            copied = 0
            try:
                while True:
                    chunk = os.read(source_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > maximum:
                        raise LoadOrderReceiptError(f"{label} exceeds its snapshot bound")
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_descriptor, view)
                        if written <= 0:
                            raise LoadOrderReceiptError(f"could not snapshot {label}")
                        view = view[written:]
                os.fsync(destination_descriptor)
            finally:
                os.close(destination_descriptor)
            if copied != before.st_size:
                raise LoadOrderReceiptError(f"{label} changed while it was being snapshotted")
            if _status_identity(before) != _status_identity(os.fstat(source_descriptor)):
                raise LoadOrderReceiptError(f"{label} changed while it was being snapshotted")
            _unchanged_file(path, before, label)
            identity = {"sizeBytes": copied, "sha256": digest.hexdigest()}
            os.chmod(snapshot, 0o400)
            snapshot_before = _stable_identity(snapshot, f"{label} snapshot", maximum)
            if snapshot_before != identity:
                raise LoadOrderReceiptError(f"{label} snapshot does not match copied bytes")
            try:
                yield snapshot, identity
            finally:
                snapshot_after = _stable_identity(snapshot, f"{label} snapshot", maximum)
                if snapshot_after != snapshot_before:
                    raise LoadOrderReceiptError(
                        f"{label} snapshot changed while it was being inspected"
                    )
    finally:
        os.close(source_descriptor)


def _parse_json(raw: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise LoadOrderReceiptError(f"invalid {label}: non-finite JSON value {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LoadOrderReceiptError(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise LoadOrderReceiptError(f"{label} must be a JSON object")
    return value


def _read_json(
    path: Path, label: str, maximum: int = MAX_JSON_BYTES
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw, identity = _read_stable_bytes(path, label, maximum)
    return _parse_json(raw, label), identity


def _json_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "$defs",
        "$ref",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "unevaluatedProperties",
        "items",
        "allOf",
        "oneOf",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)
_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "null"})


def _resolve_schema_reference(root: dict[str, Any], reference: Any) -> dict[str, Any]:
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise LoadOrderReceiptError("receipt schema may use only local JSON pointers")
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise LoadOrderReceiptError(f"receipt schema has unresolved reference {reference!r}")
        current = current[part]
    if not isinstance(current, dict):
        raise LoadOrderReceiptError(f"receipt schema reference {reference!r} is not an object")
    return current


def _schema_values_are_unique(values: list[Any]) -> bool:
    serialized = [json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values]
    return len(serialized) == len(set(serialized))


def _validate_schema_node(
    node: Any, root: dict[str, Any], label: str, *, depth: int = 0
) -> None:
    if depth > 64 or not isinstance(node, dict):
        raise LoadOrderReceiptError(f"{label} is not a bounded schema object")
    unsupported = set(node) - _SUPPORTED_SCHEMA_KEYS
    if unsupported:
        raise LoadOrderReceiptError(
            f"{label} uses unsupported schema keywords: {', '.join(sorted(unsupported))}"
        )
    if "$ref" in node:
        _resolve_schema_reference(root, node["$ref"])
    if "type" in node and node["type"] not in _SCHEMA_TYPES:
        raise LoadOrderReceiptError(f"{label}.type is unsupported")
    for keyword in ("$schema", "$id", "title", "pattern"):
        if keyword in node and not isinstance(node[keyword], str):
            raise LoadOrderReceiptError(f"{label}.{keyword} must be a string")
    if "pattern" in node:
        try:
            re.compile(node["pattern"])
        except re.error as error:
            raise LoadOrderReceiptError(f"{label}.pattern is invalid: {error}") from error
    for keyword in (
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    ):
        if keyword in node:
            _integer(node[keyword], f"{label}.{keyword}", 0, 2**63 - 1)
    for lower, upper in (
        ("minLength", "maxLength"),
        ("minimum", "maximum"),
        ("minItems", "maxItems"),
    ):
        if lower in node and upper in node and node[lower] > node[upper]:
            raise LoadOrderReceiptError(f"{label}.{lower} exceeds {upper}")
    for keyword in ("additionalProperties", "unevaluatedProperties", "uniqueItems"):
        if keyword in node and type(node[keyword]) is not bool:
            raise LoadOrderReceiptError(f"{label}.{keyword} must be boolean")
    if "required" in node:
        required = _array(node["required"], f"{label}.required")
        if any(not isinstance(item, str) for item in required) or len(required) != len(
            set(required)
        ):
            raise LoadOrderReceiptError(
                f"{label}.required must contain unique string property names"
            )
    if "enum" in node:
        values = _array(node["enum"], f"{label}.enum")
        if not values or not _schema_values_are_unique(values):
            raise LoadOrderReceiptError(f"{label}.enum must contain unique values")
    if node.get("type") == "integer":
        if "const" in node and type(node["const"]) is not int:
            raise LoadOrderReceiptError(f"{label}.const must be a strict integer")
        if "enum" in node and any(type(item) is not int for item in node["enum"]):
            raise LoadOrderReceiptError(f"{label}.enum must contain strict integers")
    properties = node.get("properties")
    if properties is not None:
        properties = _object(properties, f"{label}.properties")
        for name, child in properties.items():
            if not isinstance(name, str):
                raise LoadOrderReceiptError(f"{label}.properties has a non-string name")
            _validate_schema_node(child, root, f"{label}.properties[{name!r}]", depth=depth + 1)
        required = node.get("required", [])
        if any(name not in properties for name in required):
            raise LoadOrderReceiptError(
                f"{label}.required names a property absent from properties"
            )
    definitions = node.get("$defs")
    if definitions is not None:
        definitions = _object(definitions, f"{label}.$defs")
        for name, child in definitions.items():
            _validate_schema_node(child, root, f"{label}.$defs[{name!r}]", depth=depth + 1)
    if "items" in node:
        _validate_schema_node(node["items"], root, f"{label}.items", depth=depth + 1)
    for keyword in ("allOf", "oneOf"):
        if keyword in node:
            children = _array(node[keyword], f"{label}.{keyword}")
            if not children:
                raise LoadOrderReceiptError(f"{label}.{keyword} must not be empty")
            for index, child in enumerate(children):
                _validate_schema_node(
                    child, root, f"{label}.{keyword}[{index}]", depth=depth + 1
                )


def _validate_schema_document(schema: dict[str, Any]) -> None:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise LoadOrderReceiptError("receipt schema must declare JSON Schema 2020-12")
    if schema.get("$id") != "https://fonix.invalid/schemas/android-load-order-receipt-v2.json":
        raise LoadOrderReceiptError("receipt schema has the wrong closed identifier")
    _validate_schema_node(schema, schema, "receipt schema")


def _schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "null": value is None,
    }[expected]


def _evaluated_schema_properties(
    node: dict[str, Any], root: dict[str, Any], *, depth: int = 0
) -> set[str]:
    if depth > 64:
        raise LoadOrderReceiptError("receipt schema reference depth is unbounded")
    names = set(node.get("properties", {}))
    if "$ref" in node:
        names.update(
            _evaluated_schema_properties(
                _resolve_schema_reference(root, node["$ref"]), root, depth=depth + 1
            )
        )
    for child in node.get("allOf", []):
        names.update(_evaluated_schema_properties(child, root, depth=depth + 1))
    return names


def _validate_schema_instance(
    value: Any,
    node: dict[str, Any],
    root: dict[str, Any],
    label: str,
    *,
    depth: int = 0,
) -> None:
    if depth > 128:
        raise LoadOrderReceiptError("receipt schema validation depth is unbounded")
    if "$ref" in node:
        _validate_schema_instance(
            value,
            _resolve_schema_reference(root, node["$ref"]),
            root,
            label,
            depth=depth + 1,
        )
    if "allOf" in node:
        for child in node["allOf"]:
            _validate_schema_instance(value, child, root, label, depth=depth + 1)
    if "oneOf" in node:
        matches = 0
        for child in node["oneOf"]:
            try:
                _validate_schema_instance(value, child, root, label, depth=depth + 1)
            except LoadOrderReceiptError:
                continue
            matches += 1
        if matches != 1:
            raise LoadOrderReceiptError(f"{label} must match exactly one schema alternative")
    expected_type = node.get("type")
    if expected_type is not None and not _schema_type_matches(value, expected_type):
        raise LoadOrderReceiptError(f"{label} must have schema type {expected_type}")
    if "const" in node and not _json_equal(value, node["const"]):
        raise LoadOrderReceiptError(f"{label} does not match its schema constant")
    if "enum" in node and not any(_json_equal(value, item) for item in node["enum"]):
        raise LoadOrderReceiptError(f"{label} is outside its schema enum")
    if isinstance(value, str):
        if "minLength" in node and len(value) < node["minLength"]:
            raise LoadOrderReceiptError(f"{label} is shorter than its schema bound")
        if "maxLength" in node and len(value) > node["maxLength"]:
            raise LoadOrderReceiptError(f"{label} exceeds its schema bound")
        if "pattern" in node and re.search(node["pattern"], value) is None:
            raise LoadOrderReceiptError(f"{label} does not match its schema pattern")
    if type(value) is int:
        if "minimum" in node and value < node["minimum"]:
            raise LoadOrderReceiptError(f"{label} is below its schema minimum")
        if "maximum" in node and value > node["maximum"]:
            raise LoadOrderReceiptError(f"{label} exceeds its schema maximum")
    if isinstance(value, list):
        if "minItems" in node and len(value) < node["minItems"]:
            raise LoadOrderReceiptError(f"{label} has too few schema items")
        if "maxItems" in node and len(value) > node["maxItems"]:
            raise LoadOrderReceiptError(f"{label} has too many schema items")
        if node.get("uniqueItems") and not _schema_values_are_unique(value):
            raise LoadOrderReceiptError(f"{label} schema items must be unique")
        if "items" in node:
            for index, item in enumerate(value):
                _validate_schema_instance(
                    item, node["items"], root, f"{label}[{index}]", depth=depth + 1
                )
    if isinstance(value, dict):
        required = node.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise LoadOrderReceiptError(f"{label} is missing schema-required fields")
        properties = node.get("properties", {})
        for name, child in properties.items():
            if name in value:
                _validate_schema_instance(
                    value[name], child, root, f"{label}.{name}", depth=depth + 1
                )
        if node.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise LoadOrderReceiptError(f"{label} has schema-forbidden fields")
        if node.get("unevaluatedProperties") is False:
            extra = set(value) - _evaluated_schema_properties(node, root)
            if extra:
                raise LoadOrderReceiptError(f"{label} has unevaluated schema fields")


def _load_and_validate_receipt_schema() -> tuple[dict[str, Any], dict[str, Any]]:
    raw, identity = _read_stable_source(
        RECEIPT_SCHEMA_PATH, "load-order receipt schema"
    )
    schema = _parse_json(raw, "load-order receipt schema")
    _validate_schema_document(schema)
    return schema, identity


def _bind_file(
    path: Path, expected: str, label: str, maximum: int
) -> dict[str, Any]:
    identity = _stable_identity(path, label, maximum)
    _expect_hash(identity, expected, label)
    return identity


def _expect_hash(identity: dict[str, Any], expected: str, label: str) -> None:
    if identity["sha256"] != expected:
        raise LoadOrderReceiptError(f"{label} hash does not match the receipt")


def _validate_matrix(receipt: dict[str, Any]) -> dict[str, Any]:
    matrix = _object(receipt["matrix"], "matrix")
    _exact_keys(matrix, {"abi", "loadOrder", "buildType", "pageSizeBytes"}, "matrix")
    if matrix["abi"] not in ABIS:
        raise LoadOrderReceiptError("matrix.abi is outside the closed set")
    if matrix["loadOrder"] not in LOAD_ORDERS:
        raise LoadOrderReceiptError("matrix.loadOrder is outside the closed set")
    if matrix["buildType"] not in BUILD_TYPES:
        raise LoadOrderReceiptError("matrix.buildType is outside the closed set")
    page_size = _integer(matrix["pageSizeBytes"], "matrix.pageSizeBytes", 4096, 16384)
    if page_size not in {4096, 16384}:
        raise LoadOrderReceiptError("matrix.pageSizeBytes is outside the closed set")
    return matrix


def _validate_build(receipt: dict[str, Any]) -> dict[str, Any]:
    build = _object(receipt["build"], "build")
    _exact_keys(
        build,
        {
            "applicationId",
            "finalApkSha256",
            "harnessContractSha256",
            "pubspecLockSha256",
            "targetEvidenceSha256",
            "logcatEvidenceSha256",
        },
        "build",
    )
    application_id = build["applicationId"]
    if (
        not isinstance(application_id, str)
        or len(application_id) > 255
        or APPLICATION_ID.fullmatch(application_id) is None
    ):
        raise LoadOrderReceiptError("build.applicationId is invalid")
    for key in build:
        if key != "applicationId":
            _digest(build[key], f"build.{key}")
    return build


def _validate_device(receipt: dict[str, Any]) -> dict[str, Any]:
    device = _object(receipt["device"], "device")
    _exact_keys(device, {"kind", "modelToken", "androidApi", "fingerprintSha256"}, "device")
    if device["kind"] not in {"physical", "emulator"}:
        raise LoadOrderReceiptError("device.kind is outside the closed set")
    _token(device["modelToken"], "device.modelToken")
    _integer(device["androidApi"], "device.androidApi", 24, 100)
    _digest(device["fingerprintSha256"], "device.fingerprintSha256")
    return device


def _validate_process(receipt: dict[str, Any]) -> dict[str, Any]:
    process = _object(receipt["process"], "process")
    _exact_keys(process, {"launchChallengeSha256", "uid", "pid"}, "process")
    _digest(process["launchChallengeSha256"], "process.launchChallengeSha256")
    uid = _object(process["uid"], "process.uid")
    _exact_keys(uid, {"packageManager", "logcatFilter", "receiptLine"}, "process.uid")
    uid_values = [
        _integer(uid[key], f"process.uid.{key}", 10000, 2**31 - 1) for key in uid
    ]
    if len(set(uid_values)) != 1:
        raise LoadOrderReceiptError("process UID evidence does not identify one app")
    pid = _object(process["pid"], "process.pid")
    _exact_keys(
        pid,
        {
            "beforeLaunch",
            "observedAfterLaunch",
            "logcatFilter",
            "receiptLine",
            "afterReceipt",
            "afterForceStop",
        },
        "process.pid",
    )
    _is_null(pid["beforeLaunch"], "process.pid.beforeLaunch")
    _is_null(pid["afterForceStop"], "process.pid.afterForceStop")
    live_values = [
        _integer(pid[key], f"process.pid.{key}", 1, 2**31 - 1)
        for key in (
            "observedAfterLaunch",
            "logcatFilter",
            "receiptLine",
            "afterReceipt",
        )
    ]
    if len(set(live_values)) != 1:
        raise LoadOrderReceiptError("process PID evidence does not identify one process")
    return process


def _validate_runtime(receipt: dict[str, Any]) -> dict[str, Any]:
    runtime = _object(receipt["runtime"], "runtime")
    _exact_keys(
        runtime,
        {
            "runtimeOwner",
            "runtimeSource",
            "ortVersion",
            "requiredOrtApi",
            "negotiatedOrtApi",
            "ortSha256",
            "shimAbi",
            "shimBuildId",
        },
        "runtime",
    )
    if runtime["runtimeOwner"] != "sherpa" or runtime["runtimeSource"] != "process":
        raise LoadOrderReceiptError("runtime ownership must be sherpa/process")
    _semver(runtime["ortVersion"], "runtime.ortVersion")
    major, minor, _patch = (int(part) for part in runtime["ortVersion"].split("."))
    if major != 1 or minor < 27:
        raise LoadOrderReceiptError(
            "runtime.ortVersion cannot expose the required ORT C API 27"
        )
    required_api = _integer(runtime["requiredOrtApi"], "runtime.requiredOrtApi", 27, 27)
    negotiated_api = _integer(
        runtime["negotiatedOrtApi"], "runtime.negotiatedOrtApi", 27, 27
    )
    if required_api != 27 or negotiated_api != 27:
        raise LoadOrderReceiptError("runtime must require and negotiate ORT C API 27")
    _digest(runtime["ortSha256"], "runtime.ortSha256")
    if _integer(runtime["shimAbi"], "runtime.shimAbi", 1, 1) != 1:
        raise LoadOrderReceiptError("runtime.shimAbi must be 1")
    if runtime["shimBuildId"] != "android-owner-sherpa-source-process":
        raise LoadOrderReceiptError("runtime.shimBuildId is not the closed Android build ID")
    return runtime


def _validate_profile(value: Any, label: str = "sherpa.profile") -> dict[str, Any]:
    profile = _object(value, label)
    _exact_keys(profile, {"id", *EXPECTED_PROFILE}, label)
    _token(profile["id"], f"{label}.id")
    for key, expected in EXPECTED_PROFILE.items():
        if profile[key] != expected or type(profile[key]) is not type(expected):
            raise LoadOrderReceiptError(f"{label}.{key} does not match the closed profile")
    return profile


def _validate_sherpa(receipt: dict[str, Any], revision: str) -> dict[str, Any]:
    sherpa = _object(receipt["sherpa"], "sherpa")
    _exact_keys(
        sherpa,
        {"packageVersion", "nativeVersion", "sourceRevision", "nativeRevision", "profile"},
        "sherpa",
    )
    package_version = _semver(sherpa["packageVersion"], "sherpa.packageVersion")
    native_version = _semver(sherpa["nativeVersion"], "sherpa.nativeVersion")
    if package_version != native_version:
        raise LoadOrderReceiptError("Sherpa package/native versions disagree")
    expected_revision = SHERPA_SOURCE_REVISIONS.get(package_version)
    if expected_revision is None or revision != expected_revision:
        raise LoadOrderReceiptError(
            "Sherpa package version/source revision is outside the closed provenance set"
        )
    if sherpa["sourceRevision"] != revision:
        raise LoadOrderReceiptError("Sherpa source revision does not match the CLI revision")
    native_revision = sherpa["nativeRevision"]
    if (
        not isinstance(native_revision, str)
        or REVISION_PREFIX.fullmatch(native_revision) is None
        or not revision.startswith(native_revision)
    ):
        raise LoadOrderReceiptError("Sherpa native revision is not a source-revision prefix")
    _validate_profile(sherpa["profile"])
    return sherpa


def _validate_fixtures(receipt: dict[str, Any]) -> dict[str, Any]:
    fixtures = _object(receipt["fixtures"], "fixtures")
    keys = {
        "fonixModelSha256",
        "fonixInputSha256",
        "fonixReferenceOutputSha256",
        "fonixCancellationModelSha256",
        "fonixCancellationInputSha256",
        "sherpaModelSha256",
        "sherpaAudioSha256",
        "sherpaReferenceSha256",
    }
    _exact_keys(fixtures, keys, "fixtures")
    for key in keys:
        _digest(fixtures[key], f"fixtures.{key}")
    return fixtures


def _decode_reference(value: Any, expected: bytes, expected_hash: str, label: str) -> None:
    if not isinstance(value, str) or len(value) > (MAX_REFERENCE_BYTES * 2):
        raise LoadOrderReceiptError(f"{label} must be bounded base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise LoadOrderReceiptError(f"{label} is invalid base64") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise LoadOrderReceiptError(f"{label} is not canonical base64")
    if decoded != expected or hashlib.sha256(decoded).hexdigest() != expected_hash:
        raise LoadOrderReceiptError(f"{label} does not equal the reference bytes")


def _validate_fonix_observation(
    value: Any, reference_bytes: bytes, reference_hash: str, label: str, *, step: bool
) -> None:
    observation = _object(value, label)
    expected_keys = {"outputEncoding", "outputBytesBase64", "outputSha256"}
    if step:
        expected_keys |= {"ordinal", "engine"}
    _exact_keys(observation, expected_keys, label)
    if observation["outputEncoding"] != "float32-le":
        raise LoadOrderReceiptError(f"{label}.outputEncoding must be float32-le")
    if observation["outputSha256"] != reference_hash:
        raise LoadOrderReceiptError(f"{label}.outputSha256 does not match the fixture")
    _decode_reference(
        observation["outputBytesBase64"], reference_bytes, reference_hash, f"{label}.outputBytesBase64"
    )


def _validate_vad_observation(
    value: Any,
    reference: dict[str, Any],
    source_samples: int,
    label: str,
    *,
    step: bool,
) -> None:
    observation = _object(value, label)
    expected_keys = {
        "sourceSamples",
        "submittedSamples",
        "segments",
        "queueEmptyAfterDrain",
        "detectedAfterDrain",
    }
    if step:
        expected_keys |= {"ordinal", "engine"}
    _exact_keys(observation, expected_keys, label)
    observed_source_samples = _integer(
        observation["sourceSamples"], f"{label}.sourceSamples", 1, 57_600_000
    )
    if observed_source_samples != source_samples:
        raise LoadOrderReceiptError(f"{label}.sourceSamples does not match the WAV")
    window = reference["profile"]["windowSamples"]
    submitted = ((source_samples + window - 1) // window) * window
    observed_submitted_samples = _integer(
        observation["submittedSamples"],
        f"{label}.submittedSamples",
        1,
        57_600_511,
    )
    if observed_submitted_samples != submitted:
        raise LoadOrderReceiptError(f"{label}.submittedSamples is not exact padded input")
    _is_true(observation["queueEmptyAfterDrain"], f"{label}.queueEmptyAfterDrain")
    _is_false(observation["detectedAfterDrain"], f"{label}.detectedAfterDrain")

    segments = _array(observation["segments"], f"{label}.segments")
    invariant = reference["invariant"]
    if not invariant["minimumSegments"] <= len(segments) <= invariant["maximumSegments"]:
        raise LoadOrderReceiptError(f"{label}.segments count violates the reference")
    previous_end = 0
    total_samples = 0
    for index, raw_segment in enumerate(segments):
        segment_label = f"{label}.segments[{index}]"
        segment = _object(raw_segment, segment_label)
        _exact_keys(segment, {"startSample", "sampleCount"}, segment_label)
        start = _integer(segment["startSample"], f"{segment_label}.startSample", 0, source_samples)
        count = _integer(
            segment["sampleCount"],
            f"{segment_label}.sampleCount",
            1,
            invariant["maximumSegmentSamples"],
        )
        if start < previous_end or count > source_samples - start:
            raise LoadOrderReceiptError(f"{segment_label} is overlapping or out of range")
        previous_end = start + count
        total_samples += count
    if not (
        invariant["minimumTotalSegmentSamples"]
        <= total_samples
        <= invariant["maximumTotalSegmentSamples"]
    ):
        raise LoadOrderReceiptError(f"{label} total segment samples violate the reference")


def _validate_initialization(receipt: dict[str, Any], load_order: str) -> dict[str, Any]:
    initialization = _object(receipt["initialization"], "initialization")
    _exact_keys(initialization, {"events", "firstOwnerAliveWhenSecondReady"}, "initialization")
    expected = (
        ["fonix-session-ready", "sherpa-vad-ready"]
        if load_order == "dart-first"
        else ["sherpa-vad-ready", "fonix-session-ready"]
    )
    if not _json_equal(initialization["events"], expected):
        raise LoadOrderReceiptError("initialization.events do not match matrix.loadOrder")
    _is_true(initialization["firstOwnerAliveWhenSecondReady"], "initialization.firstOwnerAliveWhenSecondReady")
    return initialization


def _validate_workload(
    receipt: dict[str, Any], reference_bytes: bytes, vad_reference: dict[str, Any], source_samples: int
) -> dict[str, Any]:
    workload = _object(receipt["workload"], "workload")
    _exact_keys(workload, {"requestedCycles", "completedCycles", "steps"}, "workload")
    cycles = _integer(workload["requestedCycles"], "workload.requestedCycles", 2, MAX_CYCLES)
    completed_cycles = _integer(
        workload["completedCycles"], "workload.completedCycles", 2, MAX_CYCLES
    )
    if completed_cycles != cycles:
        raise LoadOrderReceiptError("workload.completedCycles must equal requestedCycles")
    steps = _array(workload["steps"], "workload.steps")
    if len(steps) != cycles * 2:
        raise LoadOrderReceiptError("workload.steps must contain exactly two steps per cycle")
    reference_hash = hashlib.sha256(reference_bytes).hexdigest()
    for index, raw_step in enumerate(steps):
        label = f"workload.steps[{index}]"
        step = _object(raw_step, label)
        if step.get("ordinal") != index + 1 or type(step.get("ordinal")) is not int:
            raise LoadOrderReceiptError(f"{label}.ordinal is not contiguous")
        expected_engine = "fonix" if index % 2 == 0 else "sherpa"
        if step.get("engine") != expected_engine:
            raise LoadOrderReceiptError("workload steps must strictly alternate from Fonix")
        if expected_engine == "fonix":
            _validate_fonix_observation(step, reference_bytes, reference_hash, label, step=True)
        else:
            _validate_vad_observation(step, vad_reference, source_samples, label, step=True)
    return workload


def _validate_lifecycle(
    receipt: dict[str, Any], reference_bytes: bytes, vad_reference: dict[str, Any], source_samples: int
) -> dict[str, Any]:
    lifecycle = _object(receipt["lifecycle"], "lifecycle")
    _exact_keys(
        lifecycle,
        {"fonixCancellation", "sherpaCancellation", "staleCompletion", "recovery", "disposal"},
        "lifecycle",
    )

    fonix = _object(lifecycle["fonixCancellation"], "lifecycle.fonixCancellation")
    _exact_keys(
        fonix,
        {
            "mode",
            "requestCount",
            "nativeRequestAcceptedCount",
            "settlementCount",
            "cancelledResultCount",
            "publishedOutputCount",
            "outstandingRunsAfterSettlement",
            "settledBeforeRecovery",
        },
        "lifecycle.fonixCancellation",
    )
    expected_fonix = {
        "mode": "active-native-termination",
        "requestCount": 1,
        "nativeRequestAcceptedCount": 1,
        "settlementCount": 1,
        "cancelledResultCount": 1,
        "publishedOutputCount": 0,
        "outstandingRunsAfterSettlement": 0,
        "settledBeforeRecovery": True,
    }
    if not _json_equal(fonix, expected_fonix):
        raise LoadOrderReceiptError("Fonix cancellation does not prove native settlement")

    sherpa = _object(lifecycle["sherpaCancellation"], "lifecycle.sherpaCancellation")
    _exact_keys(
        sherpa,
        {
            "mode",
            "requestCount",
            "framesAcceptedBeforeRequest",
            "framesAcceptedAfterRequest",
            "flushCallsAfterRequest",
            "segmentsPublishedAfterRequest",
            "detectorRetiredBeforeRecovery",
        },
        "lifecycle.sherpaCancellation",
    )
    if sherpa["mode"] != "between-bounded-frames" or _integer(
        sherpa["requestCount"],
        "lifecycle.sherpaCancellation.requestCount",
        1,
        1,
    ) != 1:
        raise LoadOrderReceiptError("Sherpa cancellation boundary is not the honest bounded-frame mode")
    maximum_frames = (source_samples + vad_reference["profile"]["windowSamples"] - 1) // vad_reference[
        "profile"
    ]["windowSamples"]
    frames_before_request = _integer(
        sherpa["framesAcceptedBeforeRequest"],
        "lifecycle.sherpaCancellation.framesAcceptedBeforeRequest",
        1,
        max(1, maximum_frames - 1),
    )
    if frames_before_request >= maximum_frames:
        raise LoadOrderReceiptError(
            "Sherpa cancellation must occur before the final bounded VAD frame"
        )
    for key in ("framesAcceptedAfterRequest", "flushCallsAfterRequest", "segmentsPublishedAfterRequest"):
        if sherpa[key] != 0 or type(sherpa[key]) is not int:
            raise LoadOrderReceiptError(f"lifecycle.sherpaCancellation.{key} must be zero")
    _is_true(
        sherpa["detectorRetiredBeforeRecovery"],
        "lifecycle.sherpaCancellation.detectorRetiredBeforeRecovery",
    )

    stale = _object(lifecycle["staleCompletion"], "lifecycle.staleCompletion")
    _exact_keys(
        stale,
        {
            "inducedCount",
            "observedCount",
            "suppressedCount",
            "publishedOutputCount",
            "retiredGeneration",
            "authoritativeGeneration",
        },
        "lifecycle.staleCompletion",
    )
    for key in ("inducedCount", "observedCount", "suppressedCount"):
        if stale[key] != 1 or type(stale[key]) is not int:
            raise LoadOrderReceiptError(f"lifecycle.staleCompletion.{key} must be one")
    if stale["publishedOutputCount"] != 0 or type(stale["publishedOutputCount"]) is not int:
        raise LoadOrderReceiptError("stale completion must publish no output")
    retired = _integer(stale["retiredGeneration"], "lifecycle.staleCompletion.retiredGeneration", 1, MAX_COUNT)
    authoritative = _integer(
        stale["authoritativeGeneration"],
        "lifecycle.staleCompletion.authoritativeGeneration",
        2,
        MAX_COUNT,
    )
    if authoritative <= retired:
        raise LoadOrderReceiptError("authoritative generation must follow the retired generation")

    recovery = _object(lifecycle["recovery"], "lifecycle.recovery")
    _exact_keys(
        recovery,
        {"fonixOutputEncoding", "fonixOutputBytesBase64", "fonixOutputSha256", "sherpa"},
        "lifecycle.recovery",
    )
    recovery_fonix = {
        "outputEncoding": recovery["fonixOutputEncoding"],
        "outputBytesBase64": recovery["fonixOutputBytesBase64"],
        "outputSha256": recovery["fonixOutputSha256"],
    }
    _validate_fonix_observation(
        recovery_fonix,
        reference_bytes,
        hashlib.sha256(reference_bytes).hexdigest(),
        "lifecycle.recovery.fonix",
        step=False,
    )
    _validate_vad_observation(
        recovery["sherpa"],
        vad_reference,
        source_samples,
        "lifecycle.recovery.sherpa",
        step=False,
    )

    disposal = _object(lifecycle["disposal"], "lifecycle.disposal")
    _exact_keys(
        disposal,
        {
            "ordersTested",
            "fonixSessionsCreated",
            "fonixSessionsClosed",
            "sherpaDetectorsCreated",
            "sherpaDetectorsFreed",
            "fonixDoubleClose",
            "sherpaDoubleFree",
            "pendingFonixRuns",
            "queuedSherpaSegments",
            "temporaryRootsCreated",
            "temporaryRootsRemoved",
            "temporaryRootsRemaining",
        },
        "lifecycle.disposal",
    )
    if not _json_equal(disposal["ordersTested"], ["fonix-then-sherpa", "sherpa-then-fonix"]):
        raise LoadOrderReceiptError("both disposal orders must be tested exactly once")
    fonix_created = _integer(disposal["fonixSessionsCreated"], "lifecycle.disposal.fonixSessionsCreated", 2, MAX_COUNT)
    sherpa_created = _integer(disposal["sherpaDetectorsCreated"], "lifecycle.disposal.sherpaDetectorsCreated", 2, MAX_COUNT)
    fonix_closed = _integer(
        disposal["fonixSessionsClosed"],
        "lifecycle.disposal.fonixSessionsClosed",
        1,
        MAX_COUNT,
    )
    sherpa_freed = _integer(
        disposal["sherpaDetectorsFreed"],
        "lifecycle.disposal.sherpaDetectorsFreed",
        1,
        MAX_COUNT,
    )
    if fonix_closed != fonix_created:
        raise LoadOrderReceiptError("all Fonix sessions must be closed")
    if sherpa_freed != sherpa_created:
        raise LoadOrderReceiptError("all Sherpa detectors must be freed")
    if disposal["fonixDoubleClose"] != "passed" or disposal["sherpaDoubleFree"] != "passed":
        raise LoadOrderReceiptError("double-disposal checks must pass")
    for key in ("pendingFonixRuns", "queuedSherpaSegments", "temporaryRootsRemaining"):
        if disposal[key] != 0 or type(disposal[key]) is not int:
            raise LoadOrderReceiptError(f"lifecycle.disposal.{key} must be zero")
    roots = _integer(disposal["temporaryRootsCreated"], "lifecycle.disposal.temporaryRootsCreated", 1, MAX_COUNT)
    roots_removed = _integer(
        disposal["temporaryRootsRemoved"],
        "lifecycle.disposal.temporaryRootsRemoved",
        1,
        MAX_COUNT,
    )
    if roots_removed != roots:
        raise LoadOrderReceiptError("all temporary roots must be removed")
    return lifecycle


def _validate_receipt_shape(receipt: dict[str, Any], revision: str) -> None:
    _exact_keys(
        receipt,
        {
            "schemaVersion",
            "result",
            "matrix",
            "build",
            "device",
            "process",
            "runtime",
            "sherpa",
            "fixtures",
            "initialization",
            "workload",
            "lifecycle",
        },
        "load-order receipt",
    )
    if (
        _integer(receipt["schemaVersion"], "receipt.schemaVersion", 2, 2) != 2
        or receipt["result"] != "passed"
    ):
        raise LoadOrderReceiptError("receipt must be a passed schemaVersion 2 record")
    _validate_matrix(receipt)
    _validate_build(receipt)
    _validate_device(receipt)
    _validate_process(receipt)
    _validate_runtime(receipt)
    _validate_sherpa(receipt, revision)
    _validate_fixtures(receipt)


def _parse_wav(raw: bytes, sample_rate: int) -> int:
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise LoadOrderReceiptError("Sherpa audio must be a RIFF/WAVE file")
    if struct.unpack_from("<I", raw, 4)[0] != len(raw) - 8:
        raise LoadOrderReceiptError("Sherpa WAV RIFF size is inconsistent")
    offset = 12
    format_chunk: bytes | None = None
    data_chunk: bytes | None = None
    while offset < len(raw):
        if len(raw) - offset < 8:
            raise LoadOrderReceiptError("Sherpa WAV has a truncated chunk header")
        chunk_id = raw[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", raw, offset + 4)[0]
        offset += 8
        if chunk_size > len(raw) - offset:
            raise LoadOrderReceiptError("Sherpa WAV has a truncated chunk")
        chunk = raw[offset : offset + chunk_size]
        offset += chunk_size
        if chunk_size % 2:
            if offset >= len(raw) or raw[offset] != 0:
                raise LoadOrderReceiptError("Sherpa WAV chunk padding is invalid")
            offset += 1
        if chunk_id == b"fmt ":
            if format_chunk is not None:
                raise LoadOrderReceiptError("Sherpa WAV has duplicate fmt chunks")
            format_chunk = chunk
        elif chunk_id == b"data":
            if data_chunk is not None:
                raise LoadOrderReceiptError("Sherpa WAV has duplicate data chunks")
            data_chunk = chunk
    if format_chunk is None or data_chunk is None or len(format_chunk) != 16:
        raise LoadOrderReceiptError("Sherpa WAV must have one canonical PCM fmt and data chunk")
    audio_format, channels, rate, byte_rate, block_align, bits = struct.unpack(
        "<HHIIHH", format_chunk
    )
    if (
        audio_format != 1
        or channels != 1
        or rate != sample_rate
        or byte_rate != rate * 2
        or block_align != 2
        or bits != 16
        or not data_chunk
        or len(data_chunk) % 2
    ):
        raise LoadOrderReceiptError("Sherpa WAV must be mono PCM16 at the closed sample rate")
    sample_count = len(data_chunk) // 2
    if sample_count > 57_600_000:
        raise LoadOrderReceiptError("Sherpa WAV exceeds the one-hour sample bound")
    return sample_count


def _validate_vad_reference(
    value: dict[str, Any], profile: dict[str, Any], source_samples: int
) -> dict[str, Any]:
    _exact_keys(value, {"schemaVersion", "profile", "audio", "invariant"}, "Sherpa reference")
    if _integer(value["schemaVersion"], "Sherpa reference.schemaVersion", 1, 1) != 1:
        raise LoadOrderReceiptError("Sherpa reference schemaVersion must be 1")
    _validate_profile(value["profile"], "Sherpa reference.profile")
    if not _json_equal(value["profile"], profile):
        raise LoadOrderReceiptError("Sherpa reference profile does not match the receipt")
    audio = _object(value["audio"], "Sherpa reference.audio")
    _exact_keys(audio, {"encoding", "sampleCount"}, "Sherpa reference.audio")
    reference_sample_count = _integer(
        audio["sampleCount"], "Sherpa reference.audio.sampleCount", 1, 57_600_000
    )
    if (
        audio["encoding"] != "wav-pcm-s16le-mono"
        or reference_sample_count != source_samples
    ):
        raise LoadOrderReceiptError("Sherpa reference audio identity does not match the WAV")
    invariant = _object(value["invariant"], "Sherpa reference.invariant")
    _exact_keys(
        invariant,
        {
            "minimumSegments",
            "maximumSegments",
            "minimumTotalSegmentSamples",
            "maximumTotalSegmentSamples",
            "maximumSegmentSamples",
        },
        "Sherpa reference.invariant",
    )
    minimum_segments = _integer(invariant["minimumSegments"], "minimumSegments", 1, MAX_SEGMENTS)
    maximum_segments = _integer(invariant["maximumSegments"], "maximumSegments", 1, MAX_SEGMENTS)
    minimum_total = _integer(invariant["minimumTotalSegmentSamples"], "minimumTotalSegmentSamples", 1, source_samples)
    maximum_total = _integer(invariant["maximumTotalSegmentSamples"], "maximumTotalSegmentSamples", 1, source_samples)
    maximum_segment = _integer(invariant["maximumSegmentSamples"], "maximumSegmentSamples", 1, source_samples)
    if minimum_segments > maximum_segments or minimum_total > maximum_total or maximum_segment > maximum_total:
        raise LoadOrderReceiptError("Sherpa reference invariants are contradictory")
    return value


def _lock_scalar(raw: str, label: str) -> str:
    value = raw.strip()
    if not value:
        raise LoadOrderReceiptError(f"{label} is missing a scalar value")
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise LoadOrderReceiptError(f"{label} has invalid quoting") from error
        if not isinstance(decoded, str):
            raise LoadOrderReceiptError(f"{label} must be a string scalar")
        return decoded
    if re.fullmatch(r"[A-Za-z0-9._+-]+", value) is None:
        raise LoadOrderReceiptError(f"{label} must use a bounded lockfile scalar")
    return value


def _pubspec_package_blocks(raw: bytes) -> dict[str, list[str]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise LoadOrderReceiptError("pubspec.lock is not UTF-8") from error
    packages: dict[str, list[str]] = {}
    sdks: dict[str, str] = {}
    current: str | None = None
    packages_roots = 0
    sdks_roots = 0
    section: str | None = None
    for line_number, line in enumerate(lines, start=1):
        if "\t" in line:
            raise LoadOrderReceiptError(
                f"pubspec.lock line {line_number} contains a tab"
            )
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "packages:":
            packages_roots += 1
            if packages_roots > 1 or section is not None:
                raise LoadOrderReceiptError("pubspec.lock has duplicate packages roots")
            section = "packages"
            current = None
            continue
        if line == "sdks:":
            sdks_roots += 1
            if sdks_roots > 1 or section != "packages" or not packages:
                raise LoadOrderReceiptError(
                    "pubspec.lock must place one sdks root after packages"
                )
            section = "sdks"
            current = None
            continue
        if not line.startswith(" "):
            raise LoadOrderReceiptError(
                f"pubspec.lock line {line_number} has an unexpected top-level field"
            )
        if section == "packages":
            match = re.fullmatch(r"  ([A-Za-z0-9_]+):", line)
            if match:
                current = match.group(1)
                if current in packages:
                    raise LoadOrderReceiptError(
                        f"duplicate pubspec.lock package {current}"
                    )
                packages[current] = []
                continue
            if current is not None and line.startswith("    "):
                packages[current].append(line)
                continue
            raise LoadOrderReceiptError(
                "pubspec.lock packages section has invalid indentation"
            )
        if section == "sdks":
            match = re.fullmatch(r"  ([A-Za-z][A-Za-z0-9_]*):(.*)", line)
            if match is None:
                raise LoadOrderReceiptError(
                    "pubspec.lock sdks section has invalid structure"
                )
            name, raw_value = match.groups()
            if name in sdks:
                raise LoadOrderReceiptError(f"pubspec.lock repeats SDK {name}")
            sdks[name] = _lock_scalar(raw_value, f"pubspec.lock sdks.{name}")
            continue
        raise LoadOrderReceiptError("pubspec.lock content precedes its packages root")
    if packages_roots != 1 or not packages:
        raise LoadOrderReceiptError("pubspec.lock must contain one non-empty packages root")
    if sdks_roots != 1 or "dart" not in sdks or "flutter" not in sdks:
        raise LoadOrderReceiptError(
            "pubspec.lock must contain one Dart/Flutter sdks root"
        )
    return packages


def _parse_pubspec_package(
    name: str, lines: list[str]
) -> tuple[dict[str, str], dict[str, str] | str]:
    values: dict[str, str] = {}
    description: dict[str, str] = {}
    scalar_description: str | None = None
    in_description = False
    for line in lines:
        top_match = re.fullmatch(r"    ([A-Za-z][A-Za-z0-9_]*):(.*)", line)
        if top_match:
            key, raw_value = top_match.groups()
            if key in values:
                raise LoadOrderReceiptError(f"pubspec.lock package {name} repeats {key}")
            if key == "description":
                if raw_value.strip():
                    scalar_description = _lock_scalar(
                        raw_value, f"pubspec.lock package {name}.description"
                    )
                    values[key] = "scalar"
                    in_description = False
                else:
                    values[key] = "mapping"
                    in_description = True
            else:
                values[key] = _lock_scalar(
                    raw_value, f"pubspec.lock package {name}.{key}"
                )
                in_description = False
            continue
        description_match = re.fullmatch(
            r"      ([A-Za-z][A-Za-z0-9_-]*):(.*)", line
        )
        if description_match and in_description:
            key, raw_value = description_match.groups()
            if key in description:
                raise LoadOrderReceiptError(
                    f"pubspec.lock package {name} description repeats {key}"
                )
            description[key] = _lock_scalar(
                raw_value, f"pubspec.lock package {name}.description.{key}"
            )
            continue
        raise LoadOrderReceiptError(f"pubspec.lock package {name} has invalid structure")
    if set(values) != {"dependency", "description", "source", "version"}:
        raise LoadOrderReceiptError(
            f"pubspec.lock package {name} has an unexpected field set"
        )
    if values["dependency"] not in {
        "transitive",
        "direct main",
        "direct dev",
        "direct overridden",
    }:
        raise LoadOrderReceiptError(
            f"pubspec.lock package {name} has an invalid dependency mode"
        )
    source = values["source"]
    if source == "hosted":
        if scalar_description is not None or set(description) != {
            "name",
            "sha256",
            "url",
        }:
            raise LoadOrderReceiptError(
                f"pubspec.lock package {name} has an incomplete hosted description"
            )
        if description["name"] != name:
            raise LoadOrderReceiptError(
                f"pubspec.lock package {name} has the wrong hosted name"
            )
        _digest(
            description["sha256"],
            f"pubspec.lock package {name}.description.sha256",
        )
        if not description["url"].startswith("https://"):
            raise LoadOrderReceiptError(
                f"pubspec.lock package {name} has an insecure hosted URL"
            )
        return values, description
    if source == "path":
        if scalar_description is not None or set(description) != {"path", "relative"}:
            raise LoadOrderReceiptError(
                f"pubspec.lock package {name} has an invalid path description"
            )
        if description["relative"] not in {"true", "false"}:
            raise LoadOrderReceiptError(
                f"pubspec.lock package {name} has an invalid relative path flag"
            )
        return values, description
    if source == "sdk":
        if scalar_description is None or description:
            raise LoadOrderReceiptError(
                f"pubspec.lock package {name} has an invalid SDK description"
            )
        return values, scalar_description
    if source == "git":
        if (
            scalar_description is not None
            or not {"url", "resolved-ref"}.issubset(description)
        ):
            raise LoadOrderReceiptError(
                f"pubspec.lock package {name} has an invalid Git description"
            )
        return values, description
    raise LoadOrderReceiptError(
        f"pubspec.lock package {name} has an unsupported source"
    )


def _validate_pubspec_lock(raw: bytes, abi: str, version: str) -> None:
    packages = _pubspec_package_blocks(raw)
    parsed = {
        package: _parse_pubspec_package(package, lines)
        for package, lines in packages.items()
    }
    expected_hashes = SHERPA_HOSTED_SHA256.get(version)
    if expected_hashes is None:
        raise LoadOrderReceiptError(
            f"pubspec.lock Sherpa version {version} is outside the closed hosted set"
        )
    expected = {
        "sherpa_onnx": "direct main",
        ANDROID_PLATFORM_PACKAGES[abi]: "direct overridden",
    }
    for package, dependency in expected.items():
        if package not in packages:
            raise LoadOrderReceiptError(f"pubspec.lock does not bind {package} {version}")
        values, description = parsed[package]
        if not isinstance(description, dict):
            raise LoadOrderReceiptError(
                f"pubspec.lock package {package} must have a hosted description"
            )
        if values["dependency"] != dependency:
            raise LoadOrderReceiptError(
                f"pubspec.lock package {package} has the wrong dependency mode"
            )
        if values["source"] != "hosted":
            raise LoadOrderReceiptError(
                f"pubspec.lock package {package} must use the hosted source"
            )
        if values["version"] != version:
            raise LoadOrderReceiptError(f"pubspec.lock does not bind {package} {version}")
        if description["url"] != "https://pub.dev":
            raise LoadOrderReceiptError(
                f"pubspec.lock package {package} has the wrong hosted URL"
            )
        if description["sha256"] != expected_hashes[package]:
            raise LoadOrderReceiptError(
                f"pubspec.lock package {package} has the wrong hosted SHA-256"
            )


def _validate_target_evidence(
    target: dict[str, Any], receipt: dict[str, Any]
) -> None:
    _exact_keys(
        target,
        {
            "schemaVersion",
            "result",
            "applicationId",
            "finalApkSha256",
            "harnessContractSha256",
            "pubspecLockSha256",
            "launchChallengeSha256",
            "matrix",
            "device",
            "fixtures",
            "process",
            "runtime",
            "sherpa",
            "initialization",
            "workload",
            "lifecycle",
        },
        "target evidence",
    )
    expected = {
        "schemaVersion": 1,
        "result": "passed",
        "applicationId": receipt["build"]["applicationId"],
        "finalApkSha256": receipt["build"]["finalApkSha256"],
        "harnessContractSha256": receipt["build"]["harnessContractSha256"],
        "pubspecLockSha256": receipt["build"]["pubspecLockSha256"],
        "launchChallengeSha256": receipt["process"]["launchChallengeSha256"],
        "matrix": receipt["matrix"],
        "device": receipt["device"],
        "fixtures": receipt["fixtures"],
        "process": {"uid": receipt["process"]["uid"], "pid": receipt["process"]["pid"]},
        "runtime": receipt["runtime"],
        "sherpa": receipt["sherpa"],
        "initialization": receipt["initialization"],
        "workload": receipt["workload"],
        "lifecycle": receipt["lifecycle"],
    }
    if not _json_equal(target, expected):
        raise LoadOrderReceiptError("target evidence does not exactly reproduce the receipt claims")


def _validate_logcat_evidence(
    logcat: dict[str, Any], receipt: dict[str, Any], target_hash: str
) -> None:
    _exact_keys(
        logcat,
        {
            "schemaVersion",
            "result",
            "applicationId",
            "deviceFingerprintSha256",
            "finalApkSha256",
            "harnessContractSha256",
            "fixtures",
            "targetEvidenceSha256",
            "launchChallengeSha256",
            "uid",
            "pid",
        },
        "logcat evidence",
    )
    expected = {
        "schemaVersion": 1,
        "result": "passed",
        "applicationId": receipt["build"]["applicationId"],
        "deviceFingerprintSha256": receipt["device"]["fingerprintSha256"],
        "finalApkSha256": receipt["build"]["finalApkSha256"],
        "harnessContractSha256": receipt["build"]["harnessContractSha256"],
        "fixtures": receipt["fixtures"],
        "targetEvidenceSha256": target_hash,
        "launchChallengeSha256": receipt["process"]["launchChallengeSha256"],
        "uid": receipt["process"]["uid"]["receiptLine"],
        "pid": receipt["process"]["pid"]["receiptLine"],
    }
    if not _json_equal(logcat, expected):
        raise LoadOrderReceiptError("logcat evidence does not bind the target process")


def _validate_harness_contract(contract: dict[str, Any], receipt: dict[str, Any]) -> None:
    _exact_keys(
        contract,
        {
            "schemaVersion",
            "applicationId",
            "finalApkSha256",
            "pubspecLockSha256",
            "profileId",
            "requestedCycles",
            "runtime",
            "fixtures",
            "cancellationModes",
        },
        "harness contract",
    )
    expected = {
        "schemaVersion": 1,
        "applicationId": receipt["build"]["applicationId"],
        "finalApkSha256": receipt["build"]["finalApkSha256"],
        "pubspecLockSha256": receipt["build"]["pubspecLockSha256"],
        "profileId": receipt["sherpa"]["profile"]["id"],
        "requestedCycles": receipt["workload"]["requestedCycles"],
        "runtime": receipt["runtime"],
        "fixtures": receipt["fixtures"],
        "cancellationModes": {
            "fonix": "active-native-termination",
            "sherpa": "between-bounded-frames",
        },
    }
    if not _json_equal(contract, expected):
        raise LoadOrderReceiptError("harness contract does not match the receipt")


def _library_record(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "sizeBytes": entry["size"],
        "sha256": entry["sha256"],
        "soname": entry["elf"]["soname"],
        "needed": entry["elf"]["needed"],
        "pageSize16KiBCompatible": entry["elf"]["pageSize16KiBCompatible"],
    }


def _validate_apk(
    path: Path, abi: str, ort_hash: str, expected_apk_hash: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    with _private_snapshot(
        path, "final APK", MAX_APK_BYTES, suffix=".apk"
    ) as (snapshot, identity):
        if identity["sha256"] != expected_apk_hash:
            raise LoadOrderReceiptError("final APK hash does not match the receipt")
        try:
            report = VERIFIER.inspect(snapshot)
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
            raise LoadOrderReceiptError(f"could not inspect final APK: {error}") from error
    if report["kind"] != "apk":
        raise LoadOrderReceiptError("final artifact must be an APK")
    for key in ("duplicate_paths", "invalid_archive_paths", "invalid_libraries"):
        if report[key]:
            raise LoadOrderReceiptError(f"final APK has {key.replace('_', ' ')}")
    ort_paths = {entry["path"] for entry in report["libraries"] if entry["name"] == VERIFIER.ORT_NAME}
    if set(report["ort_candidates"]) != ort_paths:
        raise LoadOrderReceiptError("final APK has a hidden or invalid ONNX Runtime candidate")

    names = {
        "onnxRuntime": VERIFIER.ORT_NAME,
        "fonixShim": VERIFIER.SHIM_NAME,
        "sherpaCapi": VERIFIER.SHERPA_C_API_NAME,
        "sherpaCxxApi": VERIFIER.SHERPA_CXX_API_NAME,
    }
    closed_sonames = frozenset(names.values())
    abi_entries = [entry for entry in report["libraries"] if entry["abi"] == abi]
    packaged_names = {entry["name"] for entry in abi_entries}
    for entry in abi_entries:
        if not entry["elf"]["pageSize16KiBCompatible"]:
            raise LoadOrderReceiptError(
                f"final APK library {entry['name']} is not 16 KiB page compatible"
            )
        if (
            entry["elf"]["soname"] in closed_sonames
            and entry["name"] != entry["elf"]["soname"]
        ):
            raise LoadOrderReceiptError(
                f"final APK aliases closed SONAME {entry['elf']['soname']}"
            )
        soname = entry["elf"]["soname"]
        flutter_aot_without_soname = (
            entry["name"] == VERIFIER.FLUTTER_AOT_NAME and soname is None
        )
        if soname != entry["name"] and not flutter_aot_without_soname:
            raise LoadOrderReceiptError(
                f"final APK library {entry['name']} has mismatched SONAME {soname!r}"
            )
        unresolved = sorted(
            dependency
            for dependency in entry["elf"]["needed"]
            if dependency not in VERIFIER.ANDROID_SYSTEM_LIBRARIES
            and dependency not in packaged_names
        )
        if unresolved:
            raise LoadOrderReceiptError(
                f"final APK library {entry['name']} has unresolved DT_NEEDED: "
                + ", ".join(unresolved)
            )

    libcxx_owners = [
        entry for entry in abi_entries if entry["name"] == VERIFIER.LIBCXX_NAME
    ]
    libcxx_users = [
        entry
        for entry in abi_entries
        if entry["name"] != VERIFIER.LIBCXX_NAME
        and VERIFIER.LIBCXX_NAME in entry["elf"]["needed"]
    ]
    if len(libcxx_owners) > 1 or (libcxx_users and len(libcxx_owners) != 1):
        raise LoadOrderReceiptError("final APK has invalid libc++_shared ownership")
    if libcxx_owners and not libcxx_users:
        raise LoadOrderReceiptError("final APK has an unconsumed libc++_shared owner")
    selected: dict[str, dict[str, Any]] = {}
    for role, name in names.items():
        matches = [
            entry for entry in report["libraries"] if entry["abi"] == abi and entry["name"] == name
        ]
        if len(matches) != 1:
            raise LoadOrderReceiptError(f"final APK must contain exactly one {name} for {abi}")
        selected[role] = matches[0]
    if any(
        entry["abi"] == abi and entry["name"] == VERIFIER.SHERPA_JNI_NAME
        for entry in report["libraries"]
    ):
        raise LoadOrderReceiptError("final APK must use the Sherpa Flutter C/C++ profile, not JNI")
    for role, entry in selected.items():
        if entry["elf"]["soname"] != entry["name"]:
            raise LoadOrderReceiptError(f"{role} SONAME does not match its closed library name")
    if selected["onnxRuntime"]["sha256"] != ort_hash:
        raise LoadOrderReceiptError("APK ONNX Runtime hash does not match the receipt")
    if selected["onnxRuntime"]["elf"].get("definesOrtApi") is not True:
        raise LoadOrderReceiptError(
            "APK ONNX Runtime does not define the required OrtGetApiBase entry point"
        )
    if selected["fonixShim"]["elf"]["needed"].count(VERIFIER.ORT_NAME) != 0:
        raise LoadOrderReceiptError("Fonix shim must discover process-owned ORT dynamically")
    if selected["sherpaCapi"]["elf"]["needed"].count(VERIFIER.ORT_NAME) != 1:
        raise LoadOrderReceiptError("Sherpa C API must bind exactly one ONNX Runtime")
    cxx_needed = selected["sherpaCxxApi"]["elf"]["needed"]
    if cxx_needed.count(VERIFIER.ORT_NAME) != 1 or cxx_needed.count(VERIFIER.SHERPA_C_API_NAME) != 1:
        raise LoadOrderReceiptError("Sherpa C++ API must bind exactly one C API and ONNX Runtime")
    libraries = {role: _library_record(entry) for role, entry in selected.items()}
    return libraries, identity


def validate(arguments: argparse.Namespace) -> dict[str, Any]:
    revision = arguments.sherpa_revision
    if not isinstance(revision, str) or REVISION.fullmatch(revision) is None:
        raise LoadOrderReceiptError("--sherpa-revision must be a full lowercase 40-hex revision")

    receipt_schema, receipt_schema_identity = _load_and_validate_receipt_schema()
    receipt, receipt_identity = _read_json(arguments.receipt, "load-order receipt")
    _validate_schema_instance(
        receipt, receipt_schema, receipt_schema, "load-order receipt"
    )
    _validate_receipt_shape(receipt, revision)
    matrix = receipt["matrix"]
    build = receipt["build"]
    fixtures = receipt["fixtures"]

    harness, harness_identity = _read_json(
        arguments.harness_contract, "harness contract"
    )
    _expect_hash(
        harness_identity, build["harnessContractSha256"], "harness contract"
    )
    pubspec_raw, pubspec_identity = _read_stable_bytes(
        arguments.pubspec_lock, "pubspec.lock", MAX_JSON_BYTES
    )
    _expect_hash(pubspec_identity, build["pubspecLockSha256"], "pubspec.lock")
    target, target_identity = _read_json(
        arguments.target_evidence, "target evidence", MAX_SMALL_EVIDENCE_BYTES
    )
    _expect_hash(
        target_identity, build["targetEvidenceSha256"], "target evidence"
    )
    logcat, logcat_identity = _read_json(
        arguments.logcat_evidence, "logcat evidence", MAX_SMALL_EVIDENCE_BYTES
    )
    _expect_hash(
        logcat_identity, build["logcatEvidenceSha256"], "logcat evidence"
    )
    reference_bytes, reference_identity = _read_stable_bytes(
        arguments.fonix_reference_output,
        "Fonix reference output",
        MAX_REFERENCE_BYTES,
    )
    _expect_hash(
        reference_identity,
        fixtures["fonixReferenceOutputSha256"],
        "Fonix reference output",
    )
    audio_raw, audio_identity = _read_stable_bytes(
        arguments.sherpa_audio, "Sherpa audio", MAX_FIXTURE_BYTES
    )
    _expect_hash(audio_identity, fixtures["sherpaAudioSha256"], "Sherpa audio")
    vad_reference, vad_reference_identity = _read_json(
        arguments.sherpa_reference, "Sherpa reference"
    )
    _expect_hash(
        vad_reference_identity,
        fixtures["sherpaReferenceSha256"],
        "Sherpa reference",
    )

    bindings = {
        "receipt": receipt_identity,
        "receiptSchema": receipt_schema_identity,
        "nativeVerifier": VERIFIER_IDENTITY,
        "harnessContract": harness_identity,
        "pubspecLock": pubspec_identity,
        "targetEvidence": target_identity,
        "logcatEvidence": logcat_identity,
        "launchChallenge": _bind_file(
            arguments.launch_challenge,
            receipt["process"]["launchChallengeSha256"],
            "launch challenge",
            MAX_CHALLENGE_BYTES,
        ),
        "fonixModel": _bind_file(
            arguments.fonix_model, fixtures["fonixModelSha256"], "Fonix model", MAX_MODEL_BYTES
        ),
        "fonixInput": _bind_file(
            arguments.fonix_input, fixtures["fonixInputSha256"], "Fonix input", MAX_FIXTURE_BYTES
        ),
        "fonixReferenceOutput": reference_identity,
        "fonixCancellationModel": _bind_file(
            arguments.fonix_cancellation_model,
            fixtures["fonixCancellationModelSha256"],
            "Fonix cancellation model",
            MAX_MODEL_BYTES,
        ),
        "fonixCancellationInput": _bind_file(
            arguments.fonix_cancellation_input,
            fixtures["fonixCancellationInputSha256"],
            "Fonix cancellation input",
            MAX_FIXTURE_BYTES,
        ),
        "sherpaModel": _bind_file(
            arguments.sherpa_model, fixtures["sherpaModelSha256"], "Sherpa model", MAX_MODEL_BYTES
        ),
        "sherpaAudio": audio_identity,
        "sherpaReference": vad_reference_identity,
    }

    if len(reference_bytes) % 4:
        raise LoadOrderReceiptError("Fonix reference output is not whole float32-le values")
    source_samples = _parse_wav(
        audio_raw, receipt["sherpa"]["profile"]["sampleRateHz"]
    )
    _validate_vad_reference(vad_reference, receipt["sherpa"]["profile"], source_samples)

    _validate_initialization(receipt, matrix["loadOrder"])
    _validate_workload(receipt, reference_bytes, vad_reference, source_samples)
    _validate_lifecycle(receipt, reference_bytes, vad_reference, source_samples)
    _validate_pubspec_lock(
        pubspec_raw, matrix["abi"], receipt["sherpa"]["packageVersion"]
    )

    _validate_harness_contract(harness, receipt)
    _validate_target_evidence(target, receipt)
    _validate_logcat_evidence(logcat, receipt, target_identity["sha256"])
    native_libraries, final_apk_identity = _validate_apk(
        arguments.final_apk,
        matrix["abi"],
        receipt["runtime"]["ortSha256"],
        build["finalApkSha256"],
    )
    bindings["finalApk"] = final_apk_identity

    return {
        "schemaVersion": 1,
        "result": "passed",
        "claimStatus": "offline-consistency-only",
        "targetEvidenceProvenance": "unverified",
        "validatorSha256": VALIDATOR_IDENTITY["sha256"],
        "receiptSchemaSha256": receipt_schema_identity["sha256"],
        "nativeVerifierSha256": VERIFIER_IDENTITY["sha256"],
        "loadOrderReceiptSha256": receipt_identity["sha256"],
        "matrix": receipt["matrix"],
        "build": receipt["build"],
        "device": receipt["device"],
        "process": receipt["process"],
        "runtime": receipt["runtime"],
        "sherpa": receipt["sherpa"],
        "fixtures": receipt["fixtures"],
        "initialization": receipt["initialization"],
        "workload": receipt["workload"],
        "lifecycle": receipt["lifecycle"],
        "evidenceBindings": bindings,
        "nativeLibraries": native_libraries,
        "claimBoundary": (
            "This record validates exact input bytes and the internal contract of one "
            "Android APK, ABI, load order, claimed process, runtime graph, Sherpa "
            "profile, fixture set, lifecycle run, and evidence tuple. It does not "
            "authenticate target/logcat JSON provenance or by itself prove a real "
            "adb, install, or device run; trusted capture provenance remains required "
            "before a target compatibility claim. Sherpa cancellation is proven only "
            "between bounded VAD frames, not during a native call. It makes no claim "
            "for another build, device, model, runtime, profile, or load order."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--target-evidence", type=Path, required=True)
    parser.add_argument("--logcat-evidence", type=Path, required=True)
    parser.add_argument("--launch-challenge", type=Path, required=True)
    parser.add_argument("--final-apk", type=Path, required=True)
    parser.add_argument("--harness-contract", type=Path, required=True)
    parser.add_argument("--pubspec-lock", type=Path, required=True)
    parser.add_argument("--fonix-model", type=Path, required=True)
    parser.add_argument("--fonix-input", type=Path, required=True)
    parser.add_argument("--fonix-reference-output", type=Path, required=True)
    parser.add_argument("--fonix-cancellation-model", type=Path, required=True)
    parser.add_argument("--fonix-cancellation-input", type=Path, required=True)
    parser.add_argument("--sherpa-model", type=Path, required=True)
    parser.add_argument("--sherpa-audio", type=Path, required=True)
    parser.add_argument("--sherpa-reference", type=Path, required=True)
    parser.add_argument("--sherpa-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _input_paths(arguments: argparse.Namespace) -> tuple[Path, ...]:
    return (
        arguments.receipt,
        arguments.target_evidence,
        arguments.logcat_evidence,
        arguments.launch_challenge,
        arguments.final_apk,
        arguments.harness_contract,
        arguments.pubspec_lock,
        arguments.fonix_model,
        arguments.fonix_input,
        arguments.fonix_reference_output,
        arguments.fonix_cancellation_model,
        arguments.fonix_cancellation_input,
        arguments.sherpa_model,
        arguments.sherpa_audio,
        arguments.sherpa_reference,
    )


def _validate_output_location(path: Path, inputs: Iterable[Path]) -> None:
    if not path.is_absolute():
        raise LoadOrderReceiptError("--output must be absolute")
    output = path.resolve(strict=False)
    temporary = path.with_name(path.name + ".tmp").resolve(strict=False)
    for input_path in inputs:
        resolved_input = input_path.resolve(strict=True)
        for candidate in (output, temporary):
            if candidate == resolved_input or resolved_input in candidate.parents:
                raise LoadOrderReceiptError(
                    "output and temporary output must not overlap an input"
                )


def _write_new(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise LoadOrderReceiptError("--output must not already exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise LoadOrderReceiptError("temporary output already exists")
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor: int | None = None
    owned_temporary: tuple[int, int] | None = None
    publication_attempted = False
    linked_output: tuple[int, int] | None = None
    publication_succeeded = False
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        initial_status = os.fstat(descriptor)
        owned_temporary = (initial_status.st_dev, initial_status.st_ino)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise LoadOrderReceiptError("could not write the validation record")
            view = view[written:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        linked_source = temporary.lstat()
        if (
            not stat.S_ISREG(linked_source.st_mode)
            or opened.st_dev != linked_source.st_dev
            or opened.st_ino != linked_source.st_ino
            or opened.st_mode != linked_source.st_mode
            or opened.st_size != linked_source.st_size
            or opened.st_size != len(encoded)
        ):
            raise LoadOrderReceiptError("temporary output changed before publication")
        publication_attempted = True
        os.link(temporary, path, follow_symlinks=False)
        published = path.lstat()
        linked_output = (published.st_dev, published.st_ino)
        opened_after_link = os.fstat(descriptor)
        if (
            not stat.S_ISREG(published.st_mode)
            or opened_after_link.st_dev != published.st_dev
            or opened_after_link.st_ino != published.st_ino
            or opened_after_link.st_mode != published.st_mode
            or opened_after_link.st_size != published.st_size
            or opened_after_link.st_size != len(encoded)
        ):
            raise LoadOrderReceiptError("published output is not the completed record")
        before_read = _status_identity(opened_after_link)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = len(encoded) + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        observed = b"".join(chunks)
        opened_after_read = os.fstat(descriptor)
        published_after_read = path.lstat()
        if (
            observed != encoded
            or _status_identity(opened_after_read) != before_read
            or _status_identity(published_after_read)
            != _status_identity(opened_after_read)
        ):
            raise LoadOrderReceiptError("published output changed during publication")
        publication_succeeded = True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if (
            publication_attempted
            and not publication_succeeded
            and (linked_output is not None or owned_temporary is not None)
        ):
            try:
                published_status = path.lstat()
            except FileNotFoundError:
                pass
            else:
                cleanup_identity = linked_output or owned_temporary
                if cleanup_identity is not None and (
                    published_status.st_dev,
                    published_status.st_ino,
                ) == cleanup_identity:
                    path.unlink()
        if owned_temporary is not None:
            try:
                temporary_status = temporary.lstat()
            except FileNotFoundError:
                pass
            else:
                if (
                    temporary_status.st_dev,
                    temporary_status.st_ino,
                ) == owned_temporary:
                    temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _validate_output_location(arguments.output, _input_paths(arguments))
        record = validate(arguments)
        _write_new(arguments.output, record)
    except (LoadOrderReceiptError, FileNotFoundError, OSError, UnicodeError) as error:
        print(f"validate_android_load_order_receipt: {error}", file=sys.stderr)
        return 1
    print(f"Wrote Android load-order validation record: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
