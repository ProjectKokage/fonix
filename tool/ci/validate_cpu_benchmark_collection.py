#!/usr/bin/env python3
"""Revalidate one exact raw Fonix CPU benchmark collection bundle offline.

The emitted record proves only closed-schema and byte/hash consistency for the
supplied bundle and the exact current inputs.  It deliberately carries no
threshold, pass/fail performance judgment, provider qualification, platform
support decision, or release approval.  Any later evaluator must reopen the
raw bundle; this validation record is not a substitute for those samples.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable, Mapping, Sequence


sys.dont_write_bytecode = True

_DIRECTORY = Path(__file__).resolve().parent
_TRUSTED_REPOSITORY = _DIRECTORY.parents[1]
sys.path.insert(0, str(_DIRECTORY))

import collect_cpu_benchmark  # noqa: E402
import cpu_benchmark_collection  # noqa: E402


LAUNCH_COUNT = 5
COLLECTION_FILENAME = "cpu-benchmark-collection.json"
HOST_OBSERVATIONS_FILENAME = "host-observations.json"
FRAGMENT_FILENAME = "fragment-{index:02d}.json"
COLLECTION_SCHEMA_RELATIVE = "templates/ci/cpu_benchmark_collection_v2.schema.json"
VALIDATION_SCHEMA_RELATIVE = "templates/ci/cpu_benchmark_validation_v1.schema.json"
TARGET_SCHEMA_RELATIVE = (
    "templates/ci/cpu_benchmark_target_fragment_v2.schema.json"
)
COLLECTION_SCHEMA_ID = (
    "https://fonix.invalid/schemas/cpu-benchmark-collection-v2.json"
)
VALIDATION_SCHEMA_ID = (
    "https://fonix.invalid/schemas/cpu-benchmark-validation-v1.json"
)
TARGET_SCHEMA_ID = (
    "https://fonix.invalid/schemas/cpu-benchmark-target-fragment-v2.json"
)
_EXPECTED_SCHEMA_IDENTITIES = {
    TARGET_SCHEMA_ID: {
        "sizeBytes": 16843,
        "sha256": "58c02fb47c71f95030792476dc96ea0614879b9cd88680d2f13443656051060d",
    },
    COLLECTION_SCHEMA_ID: {
        "sizeBytes": 28617,
        "sha256": "d37872a9723913bcf1fc2047a869c39df0294272185d1351520d5ead65bc8472",
    },
    VALIDATION_SCHEMA_ID: {
        "sizeBytes": 8263,
        "sha256": "29531074402957518a46548d8200cb4e45559a99ea2f93648b83586885b7f429",
    },
}
MAXIMUM_PATH_BYTES = 4096
MAXIMUM_COLLECTION_BYTES = 8 * 1024 * 1024
MAXIMUM_FRAGMENT_BYTES = 128 * 1024
MAXIMUM_HOST_OBSERVATION_BYTES = 1024 * 1024
MAXIMUM_SCHEMA_BYTES = 4 * 1024 * 1024
MAXIMUM_TOOL_BYTES = 4 * 1024 * 1024
MAXIMUM_OUTPUT_BYTES = 16 * 1024 * 1024
MAXIMUM_SCHEMA_DEPTH = 96
MAXIMUM_INSTANCE_DEPTH = 192
MAXIMUM_JSON_INTEGER = 0x7FFF_FFFF_FFFF_FFFF

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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
        "items",
        "prefixItems",
        "oneOf",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)
_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "null"})
_CLAIM_BOUNDARY = (
    "Offline consistency validation of one exact raw CPU measurement bundle "
    "only. The raw bundle must be retained and independently reopened for "
    "every later evaluation. Raw fragments and recorded host observations are "
    "checked for internal consistency but are not independently authenticated; "
    "process freshness is not independently attested. This record is not a "
    "performance baseline, regression threshold, provider qualification, "
    "platform support claim, release approval, or cross-target evidence."
)


class CpuBenchmarkValidationError(RuntimeError):
    """The raw collection bundle or its current artifact bindings failed closed."""


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
        raise CpuBenchmarkValidationError(
            "value is not canonical JSON data"
        ) from error


def _json_equal(left: Any, right: Any) -> bool:
    return _canonical_bytes(left) == _canonical_bytes(right)


def _identity(data: bytes) -> dict[str, Any]:
    return {"sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


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


def _absolute_path(path: Path, label: str) -> Path:
    path = Path(path)
    raw = os.fsencode(str(path))
    if (
        not path.is_absolute()
        or not raw
        or len(raw) > MAXIMUM_PATH_BYTES
        or b"\x00" in raw
        or any(byte < 0x20 for byte in raw)
    ):
        raise CpuBenchmarkValidationError(
            f"{label} must be a bounded absolute path"
        )
    return path


def _resolved_leaf(path: Path, label: str) -> Path:
    path = _absolute_path(path, label)
    if path.name in {"", ".", ".."}:
        raise CpuBenchmarkValidationError(f"{label} leaf name is invalid")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise CpuBenchmarkValidationError(f"missing {label} parent") from error
    try:
        metadata = parent.lstat()
    except OSError as error:
        raise CpuBenchmarkValidationError(f"missing {label} parent") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CpuBenchmarkValidationError(
            f"{label} parent must be a non-link directory"
        )
    return parent / path.name


def _directory(path: Path, label: str) -> Path:
    path = _resolved_leaf(path, label)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CpuBenchmarkValidationError(f"missing {label}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CpuBenchmarkValidationError(
            f"{label} must be a non-link directory"
        )
    return path


def _regular_file(path: Path, label: str, *, executable: bool = False) -> Path:
    path = _resolved_leaf(path, label)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CpuBenchmarkValidationError(f"missing {label}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CpuBenchmarkValidationError(
            f"{label} must be a regular non-link file"
        )
    if executable and not os.access(path, os.X_OK):
        raise CpuBenchmarkValidationError(f"{label} must be executable")
    return path


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
    )


def _same_or_descendant(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _directory_descends_from(directory: Path, root: Path) -> bool:
    root_metadata = root.stat()
    current = directory
    while True:
        if _same_inode(current.stat(), root_metadata):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _reject_output_in_input_tree(
    output: Path,
    *,
    collection_directory: Path,
    application_root: Path,
    repository: Path,
) -> None:
    output = output.parent.resolve(strict=True) / output.name
    protected = (
        (collection_directory.resolve(strict=True), "raw collection bundle"),
        (application_root.resolve(strict=True), "measured application tree"),
        (repository.resolve(strict=True), "supplied repository"),
    )
    for root, label in protected:
        if _same_or_descendant(output, root) or _directory_descends_from(
            output.parent, root
        ):
            raise CpuBenchmarkValidationError(
                f"validation output must be outside the {label}"
            )


def _read_file_at(
    directory_descriptor: int,
    name: str,
    before: os.stat_result,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or not 1 <= before.st_size <= maximum_bytes
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise CpuBenchmarkValidationError(
            f"{label} must be one bounded regular file"
        )
    if not hasattr(os, "O_NOFOLLOW"):
        raise CpuBenchmarkValidationError(
            "this host cannot enforce no-follow bundle reads"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise CpuBenchmarkValidationError(
            f"{label} cannot be opened safely"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_metadata(before, opened):
            raise CpuBenchmarkValidationError(f"{label} changed before reading")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            requested = min(1024 * 1024, maximum_bytes + 1 - consumed)
            chunk = os.read(descriptor, requested)
            if not chunk:
                break
            consumed += len(chunk)
            if consumed > maximum_bytes:
                raise CpuBenchmarkValidationError(f"{label} exceeds its byte bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if consumed != before.st_size or not _same_metadata(before, after):
            raise CpuBenchmarkValidationError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
    try:
        final = os.stat(
            name, dir_fd=directory_descriptor, follow_symlinks=False
        )
    except OSError as error:
        raise CpuBenchmarkValidationError(
            f"{label} disappeared after reading"
        ) from error
    if not _same_metadata(before, final):
        raise CpuBenchmarkValidationError(f"{label} changed after reading")
    return b"".join(chunks)


def _read_exact_bundle(directory: Path) -> dict[str, bytes]:
    expected_names = {
        COLLECTION_FILENAME,
        HOST_OBSERVATIONS_FILENAME,
        *(
            FRAGMENT_FILENAME.format(index=index)
            for index in range(LAUNCH_COUNT)
        ),
    }
    try:
        before = directory.lstat()
    except OSError as error:
        raise CpuBenchmarkValidationError(
            "collection directory cannot be inspected"
        ) from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise CpuBenchmarkValidationError(
            "collection directory must be a non-link directory"
        )
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise CpuBenchmarkValidationError(
            "this host cannot enforce no-follow bundle traversal"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as error:
        raise CpuBenchmarkValidationError(
            "collection directory cannot be opened safely"
        ) from error
    result: dict[str, bytes] = {}
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_metadata(before, opened):
            raise CpuBenchmarkValidationError(
                "collection directory changed before inspection"
            )
        try:
            with os.scandir(descriptor) as iterator:
                names = sorted(entry.name for entry in iterator)
        except OSError as error:
            raise CpuBenchmarkValidationError(
                "collection directory cannot be inventoried"
            ) from error
        if set(names) != expected_names or len(names) != len(expected_names):
            raise CpuBenchmarkValidationError(
                "collection directory inventory is not the exact seven-file bundle"
            )
        for name in names:
            try:
                metadata = os.stat(
                    name, dir_fd=descriptor, follow_symlinks=False
                )
            except OSError as error:
                raise CpuBenchmarkValidationError(
                    "collection bundle entry cannot be inspected"
                ) from error
            maximum = (
                MAXIMUM_COLLECTION_BYTES
                if name == COLLECTION_FILENAME
                else MAXIMUM_HOST_OBSERVATION_BYTES
                if name == HOST_OBSERVATIONS_FILENAME
                else MAXIMUM_FRAGMENT_BYTES
            )
            result[name] = _read_file_at(
                descriptor,
                name,
                metadata,
                label=f"collection bundle {name}",
                maximum_bytes=maximum,
            )
        after = os.fstat(descriptor)
        if not _same_metadata(before, after):
            raise CpuBenchmarkValidationError(
                "collection directory changed during inspection"
            )
    finally:
        os.close(descriptor)
    try:
        final = directory.lstat()
    except OSError as error:
        raise CpuBenchmarkValidationError(
            "collection directory disappeared after inspection"
        ) from error
    if not _same_metadata(before, final):
        raise CpuBenchmarkValidationError(
            "collection directory changed after inspection"
        )
    return result


def _bundle_record(payloads: Mapping[str, bytes]) -> dict[str, Any]:
    entries = [
        {"name": name, **_identity(payloads[name])}
        for name in sorted(payloads)
    ]
    return {
        "format": "fonix-cpu-benchmark-raw-bundle-v1",
        "fileCount": len(entries),
        "byteCount": sum(entry["sizeBytes"] for entry in entries),
        "sha256": hashlib.sha256(_canonical_bytes(entries)).hexdigest(),
        "files": entries,
    }


def _schema_integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAXIMUM_JSON_INTEGER:
        raise CpuBenchmarkValidationError(f"{label} is not a bounded integer")
    return value


def _schema_array(value: Any, label: str) -> list[Any]:
    if type(value) is not list or len(value) > 1_000_000:
        raise CpuBenchmarkValidationError(f"{label} is not a bounded array")
    return value


def _schema_object(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or len(value) > 100_000:
        raise CpuBenchmarkValidationError(f"{label} is not a bounded object")
    return value


def _resolve_schema_reference(
    reference: Any,
    current_root: dict[str, Any],
    registry: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(reference) is not str or not reference:
        raise CpuBenchmarkValidationError("schema reference must be a string")
    if reference.startswith("#"):
        document = current_root
        pointer = reference[1:]
    else:
        identifier, marker, suffix = reference.partition("#")
        if identifier not in registry:
            raise CpuBenchmarkValidationError(
                f"schema has unresolved external reference {reference!r}"
            )
        document = registry[identifier]
        pointer = suffix if marker else ""
    current: Any = document
    if pointer:
        if not pointer.startswith("/"):
            raise CpuBenchmarkValidationError(
                f"schema reference {reference!r} is not a JSON pointer"
            )
        for raw_part in pointer[1:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if type(current) is not dict or part not in current:
                raise CpuBenchmarkValidationError(
                    f"schema has unresolved reference {reference!r}"
                )
            current = current[part]
    if type(current) is not dict:
        raise CpuBenchmarkValidationError(
            f"schema reference {reference!r} is not an object"
        )
    return current, document


def _schema_values_are_unique(values: Sequence[Any]) -> bool:
    encoded = [_canonical_bytes(value) for value in values]
    return len(encoded) == len(set(encoded))


def _validate_schema_node(
    node: Any,
    current_root: dict[str, Any],
    registry: Mapping[str, dict[str, Any]],
    label: str,
    *,
    depth: int = 0,
) -> None:
    if depth > MAXIMUM_SCHEMA_DEPTH or type(node) is not dict:
        raise CpuBenchmarkValidationError(f"{label} is not a bounded schema object")
    unsupported = set(node) - _SUPPORTED_SCHEMA_KEYS
    if unsupported:
        raise CpuBenchmarkValidationError(
            f"{label} uses unsupported schema keywords: "
            + ", ".join(sorted(unsupported))
        )
    if "$ref" in node:
        _resolve_schema_reference(node["$ref"], current_root, registry)
    if "type" in node and node["type"] not in _SCHEMA_TYPES:
        raise CpuBenchmarkValidationError(f"{label}.type is unsupported")
    for keyword in ("$schema", "$id", "title", "pattern"):
        if keyword in node and type(node[keyword]) is not str:
            raise CpuBenchmarkValidationError(f"{label}.{keyword} must be a string")
    if "pattern" in node:
        try:
            re.compile(node["pattern"])
        except re.error as error:
            raise CpuBenchmarkValidationError(
                f"{label}.pattern is invalid"
            ) from error
    for keyword in (
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    ):
        if keyword in node:
            _schema_integer(node[keyword], f"{label}.{keyword}")
    if "multipleOf" in node:
        _schema_integer(node["multipleOf"], f"{label}.multipleOf", minimum=1)
    for lower, upper in (
        ("minLength", "maxLength"),
        ("minimum", "maximum"),
        ("minItems", "maxItems"),
    ):
        if lower in node and upper in node and node[lower] > node[upper]:
            raise CpuBenchmarkValidationError(f"{label}.{lower} exceeds {upper}")
    for keyword in ("additionalProperties", "uniqueItems"):
        if keyword in node and type(node[keyword]) is not bool:
            raise CpuBenchmarkValidationError(f"{label}.{keyword} must be boolean")
    if "required" in node:
        required = _schema_array(node["required"], f"{label}.required")
        if (
            any(type(item) is not str for item in required)
            or len(required) != len(set(required))
        ):
            raise CpuBenchmarkValidationError(
                f"{label}.required must contain unique strings"
            )
    if "enum" in node:
        values = _schema_array(node["enum"], f"{label}.enum")
        if not values or not _schema_values_are_unique(values):
            raise CpuBenchmarkValidationError(
                f"{label}.enum must contain unique values"
            )
    if node.get("type") == "integer":
        if "const" in node and type(node["const"]) is not int:
            raise CpuBenchmarkValidationError(
                f"{label}.const must be a strict integer"
            )
        if "enum" in node and any(type(item) is not int for item in node["enum"]):
            raise CpuBenchmarkValidationError(
                f"{label}.enum must contain strict integers"
            )
    properties = node.get("properties")
    if properties is not None:
        properties = _schema_object(properties, f"{label}.properties")
        for name, child in properties.items():
            if type(name) is not str:
                raise CpuBenchmarkValidationError(
                    f"{label}.properties contains a non-string name"
                )
            _validate_schema_node(
                child,
                current_root,
                registry,
                f"{label}.properties[{name!r}]",
                depth=depth + 1,
            )
        if any(name not in properties for name in node.get("required", [])):
            raise CpuBenchmarkValidationError(
                f"{label}.required names an absent property"
            )
    definitions = node.get("$defs")
    if definitions is not None:
        definitions = _schema_object(definitions, f"{label}.$defs")
        for name, child in definitions.items():
            _validate_schema_node(
                child,
                current_root,
                registry,
                f"{label}.$defs[{name!r}]",
                depth=depth + 1,
            )
    if "items" in node:
        _validate_schema_node(
            node["items"],
            current_root,
            registry,
            f"{label}.items",
            depth=depth + 1,
        )
    if "prefixItems" in node:
        prefix = _schema_array(node["prefixItems"], f"{label}.prefixItems")
        for index, child in enumerate(prefix):
            _validate_schema_node(
                child,
                current_root,
                registry,
                f"{label}.prefixItems[{index}]",
                depth=depth + 1,
            )
    if "oneOf" in node:
        alternatives = _schema_array(node["oneOf"], f"{label}.oneOf")
        if not alternatives:
            raise CpuBenchmarkValidationError(f"{label}.oneOf must not be empty")
        for index, child in enumerate(alternatives):
            _validate_schema_node(
                child,
                current_root,
                registry,
                f"{label}.oneOf[{index}]",
                depth=depth + 1,
            )


def _validate_schema_documents(
    registry: Mapping[str, dict[str, Any]],
) -> None:
    expected = {COLLECTION_SCHEMA_ID, VALIDATION_SCHEMA_ID, TARGET_SCHEMA_ID}
    if set(registry) != expected:
        raise CpuBenchmarkValidationError("schema registry field set changed")
    for identifier, schema in registry.items():
        if schema.get("$schema") != (
            "https://json-schema.org/draft/2020-12/schema"
        ):
            raise CpuBenchmarkValidationError(
                "CPU benchmark schema must declare JSON Schema 2020-12"
            )
        if schema.get("$id") != identifier:
            raise CpuBenchmarkValidationError(
                "CPU benchmark schema has the wrong closed identifier"
            )
        _validate_schema_node(schema, schema, registry, identifier)


def _schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": type(value) is dict,
        "array": type(value) is list,
        "string": type(value) is str,
        "integer": type(value) is int,
        "null": value is None,
    }[expected]


def _validate_schema_instance(
    value: Any,
    node: dict[str, Any],
    current_root: dict[str, Any],
    registry: Mapping[str, dict[str, Any]],
    label: str,
    *,
    depth: int = 0,
) -> None:
    if depth > MAXIMUM_INSTANCE_DEPTH:
        raise CpuBenchmarkValidationError(
            "CPU benchmark schema validation depth is unbounded"
        )
    if "$ref" in node:
        target, target_root = _resolve_schema_reference(
            node["$ref"], current_root, registry
        )
        _validate_schema_instance(
            value,
            target,
            target_root,
            registry,
            label,
            depth=depth + 1,
        )
    if "oneOf" in node:
        matches = 0
        for child in node["oneOf"]:
            try:
                _validate_schema_instance(
                    value,
                    child,
                    current_root,
                    registry,
                    label,
                    depth=depth + 1,
                )
            except CpuBenchmarkValidationError:
                continue
            matches += 1
        if matches != 1:
            raise CpuBenchmarkValidationError(
                f"{label} must match exactly one schema alternative"
            )
    expected_type = node.get("type")
    if expected_type is not None and not _schema_type_matches(value, expected_type):
        raise CpuBenchmarkValidationError(
            f"{label} must have schema type {expected_type}"
        )
    if "const" in node and not _json_equal(value, node["const"]):
        raise CpuBenchmarkValidationError(
            f"{label} does not match its schema constant"
        )
    if "enum" in node and not any(
        _json_equal(value, entry) for entry in node["enum"]
    ):
        raise CpuBenchmarkValidationError(f"{label} is outside its schema enum")
    if type(value) is str:
        if "minLength" in node and len(value) < node["minLength"]:
            raise CpuBenchmarkValidationError(
                f"{label} is shorter than its schema bound"
            )
        if "maxLength" in node and len(value) > node["maxLength"]:
            raise CpuBenchmarkValidationError(
                f"{label} exceeds its schema bound"
            )
        if "pattern" in node and re.search(node["pattern"], value) is None:
            raise CpuBenchmarkValidationError(
                f"{label} does not match its schema pattern"
            )
    if type(value) is int:
        if "minimum" in node and value < node["minimum"]:
            raise CpuBenchmarkValidationError(
                f"{label} is below its schema minimum"
            )
        if "maximum" in node and value > node["maximum"]:
            raise CpuBenchmarkValidationError(
                f"{label} exceeds its schema maximum"
            )
        if "multipleOf" in node and value % node["multipleOf"] != 0:
            raise CpuBenchmarkValidationError(
                f"{label} is not a schema multiple"
            )
    if type(value) is list:
        if "minItems" in node and len(value) < node["minItems"]:
            raise CpuBenchmarkValidationError(
                f"{label} has too few schema items"
            )
        if "maxItems" in node and len(value) > node["maxItems"]:
            raise CpuBenchmarkValidationError(
                f"{label} has too many schema items"
            )
        if node.get("uniqueItems") and not _schema_values_are_unique(value):
            raise CpuBenchmarkValidationError(
                f"{label} schema items must be unique"
            )
        prefix = node.get("prefixItems", [])
        for index, child in enumerate(prefix):
            if index >= len(value):
                break
            _validate_schema_instance(
                value[index],
                child,
                current_root,
                registry,
                f"{label}[{index}]",
                depth=depth + 1,
            )
        if "items" in node:
            for index, item in enumerate(value[len(prefix) :], start=len(prefix)):
                _validate_schema_instance(
                    item,
                    node["items"],
                    current_root,
                    registry,
                    f"{label}[{index}]",
                    depth=depth + 1,
                )
    if type(value) is dict:
        missing = [name for name in node.get("required", []) if name not in value]
        if missing:
            raise CpuBenchmarkValidationError(
                f"{label} is missing schema-required fields"
            )
        properties = node.get("properties", {})
        for name, child in properties.items():
            if name in value:
                _validate_schema_instance(
                    value[name],
                    child,
                    current_root,
                    registry,
                    f"{label}.{name}",
                    depth=depth + 1,
                )
        if node.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise CpuBenchmarkValidationError(
                    f"{label} has schema-forbidden fields"
                )


def _read_source_file(
    core: Any, path: Path, *, label: str, maximum_bytes: int
) -> tuple[bytes, dict[str, Any]]:
    reader = getattr(core, "_read_regular_file", None)
    if not callable(reader):
        raise CpuBenchmarkValidationError(
            "CPU collection core does not expose stable source reads"
        )
    try:
        return reader(path, label=label, maximum_bytes=maximum_bytes)
    except Exception as error:
        raise CpuBenchmarkValidationError(f"could not read {label}") from error


def _load_schemas(
    repository: Path, core: Any
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    specifications = (
        (TARGET_SCHEMA_ID, TARGET_SCHEMA_RELATIVE, "target-fragment schema"),
        (COLLECTION_SCHEMA_ID, COLLECTION_SCHEMA_RELATIVE, "collection schema"),
        (VALIDATION_SCHEMA_ID, VALIDATION_SCHEMA_RELATIVE, "validation schema"),
    )
    registry: dict[str, dict[str, Any]] = {}
    identities: dict[str, dict[str, Any]] = {}
    for identifier, relative, label in specifications:
        trusted_raw, trusted_identity = _read_source_file(
            core,
            _TRUSTED_REPOSITORY.joinpath(*relative.split("/")),
            label=f"trusted {label}",
            maximum_bytes=MAXIMUM_SCHEMA_BYTES,
        )
        if trusted_identity != _EXPECTED_SCHEMA_IDENTITIES[identifier]:
            raise CpuBenchmarkValidationError(
                f"trusted {label} identity does not match the validator pin"
            )
        supplied_raw, supplied_identity = _read_source_file(
            core,
            repository.joinpath(*relative.split("/")),
            label=f"supplied repository {label}",
            maximum_bytes=MAXIMUM_SCHEMA_BYTES,
        )
        if supplied_identity != trusted_identity or supplied_raw != trusted_raw:
            raise CpuBenchmarkValidationError(
                f"supplied repository {label} does not match the trusted validator"
            )
        try:
            value = core.strict_json_loads(
                trusted_raw,
                label=f"trusted {label}",
                maximum_bytes=MAXIMUM_SCHEMA_BYTES,
            )
        except Exception as error:
            raise CpuBenchmarkValidationError(f"invalid {label}") from error
        if type(value) is not dict:
            raise CpuBenchmarkValidationError(f"{label} must be an object")
        registry[identifier] = value
        identities[
            {
                TARGET_SCHEMA_ID: "targetFragment",
                COLLECTION_SCHEMA_ID: "collection",
                VALIDATION_SCHEMA_ID: "validation",
            }[identifier]
        ] = trusted_identity
    _validate_schema_documents(registry)
    return registry, identities


def _tool_identities(repository: Path, core: Any) -> dict[str, dict[str, Any]]:
    specifications = {
        "collector": (
            Path(collect_cpu_benchmark.__file__).resolve(strict=True),
            "tool/ci/collect_cpu_benchmark.py",
        ),
        "collectionCore": (
            Path(cpu_benchmark_collection.__file__).resolve(strict=True),
            "tool/ci/cpu_benchmark_collection.py",
        ),
        "validator": (
            Path(__file__).resolve(strict=True),
            "tool/ci/validate_cpu_benchmark_collection.py",
        ),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, (trusted_path, relative) in specifications.items():
        trusted_raw, trusted_identity = _read_source_file(
            core,
            trusted_path,
            label=f"trusted CPU benchmark {name}",
            maximum_bytes=MAXIMUM_TOOL_BYTES,
        )
        supplied_raw, supplied_identity = _read_source_file(
            core,
            repository.joinpath(*relative.split("/")),
            label=f"supplied repository CPU benchmark {name}",
            maximum_bytes=MAXIMUM_TOOL_BYTES,
        )
        if supplied_identity != trusted_identity or supplied_raw != trusted_raw:
            raise CpuBenchmarkValidationError(
                f"supplied repository CPU benchmark {name} does not match the "
                "trusted validator"
            )
        result[name] = trusted_identity
    return result


def _artifact_snapshot(arguments: argparse.Namespace, core: Any, collector: Any) -> Any:
    try:
        return collector._artifact_snapshot(
            repository=arguments.repository,
            application_root=arguments.application_root,
            executable=arguments.executable,
            shim_artifact=arguments.shim_artifact,
            runtime_artifact=arguments.runtime_artifact,
            resolver_manifest=arguments.resolver_manifest,
            provider_dependencies=arguments.provider_dependency,
            core=core,
        )
    except Exception as error:
        raise CpuBenchmarkValidationError(
            "could not rederive current artifact bindings"
        ) from error


def _normalize_arguments(arguments: argparse.Namespace) -> argparse.Namespace:
    repository = _directory(arguments.repository, "repository")
    application_root = _directory(arguments.application_root, "application root")
    collection_directory = _directory(
        arguments.collection_directory, "collection directory"
    )
    executable = _regular_file(
        arguments.executable, "application executable", executable=True
    )
    try:
        executable.relative_to(application_root)
    except ValueError as error:
        raise CpuBenchmarkValidationError(
            "application executable must be inside the application root"
        ) from error
    output = _resolved_leaf(arguments.output, "validation output")
    _reject_output_in_input_tree(
        output,
        collection_directory=collection_directory,
        application_root=application_root,
        repository=repository,
    )
    try:
        output_parent_metadata = output.parent.lstat()
    except OSError as error:
        raise CpuBenchmarkValidationError(
            "validation output parent cannot be retained"
        ) from error
    if not stat.S_ISDIR(output_parent_metadata.st_mode):
        raise CpuBenchmarkValidationError(
            "validation output parent must remain a directory"
        )
    normalized = argparse.Namespace(
        collection_directory=collection_directory,
        repository=repository,
        application_root=application_root,
        executable=executable,
        shim_artifact=_regular_file(arguments.shim_artifact, "shim artifact"),
        runtime_artifact=_regular_file(
            arguments.runtime_artifact, "runtime artifact"
        ),
        resolver_manifest=_regular_file(
            arguments.resolver_manifest, "resolver manifest"
        ),
        provider_dependency=tuple(
            _regular_file(path, "provider dependency")
            for path in arguments.provider_dependency
        ),
        output=output,
        output_parent_metadata=output_parent_metadata,
    )
    if normalized.output.exists() or normalized.output.is_symlink():
        raise CpuBenchmarkValidationError(
            "validation output must not already exist"
        )
    if normalized.output.parent != normalized.output.parent.resolve(strict=True):
        raise CpuBenchmarkValidationError(
            "validation output parent must be canonical"
        )
    return normalized


def _named_metadata(directory_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CpuBenchmarkValidationError(
            "publication entry cannot be inspected safely"
        ) from error


def _verify_parent_path_identity(
    parent: Path,
    parent_descriptor: int,
    expected: os.stat_result,
) -> None:
    try:
        canonical = parent.resolve(strict=True)
        current = parent.lstat()
        opened = os.fstat(parent_descriptor)
    except OSError as error:
        raise CpuBenchmarkValidationError(
            "validation output parent path cannot be reverified"
        ) from error
    if (
        canonical != parent
        or stat.S_ISLNK(current.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_metadata(current, opened)
        or not _same_inode(opened, expected)
    ):
        raise CpuBenchmarkValidationError(
            "validation output parent path changed; retained output residue was "
            "preserved"
        )


def _publish_no_replace(
    path: Path,
    value: Mapping[str, Any],
    *,
    expected_parent_metadata: os.stat_result | None = None,
    collection_directory: Path | None = None,
    application_root: Path | None = None,
    repository: Path | None = None,
) -> None:
    encoded = (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    if not 1 <= len(encoded) <= MAXIMUM_OUTPUT_BYTES:
        raise CpuBenchmarkValidationError(
            "validation output is outside its byte bound"
        )
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise CpuBenchmarkValidationError(
            "this host cannot reserve validation output without following links"
        )
    parent = path.parent
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    directory_flags |= getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    parent_descriptor: int | None = None
    descriptor: int | None = None
    retained_metadata: os.stat_result | None = None
    reserved = False
    content_complete = False
    try:
        try:
            parent_before = parent.lstat()
            parent_descriptor = os.open(parent, directory_flags)
            parent_opened = os.fstat(parent_descriptor)
        except OSError as error:
            raise CpuBenchmarkValidationError(
                "validation output parent cannot be opened safely"
            ) from error
        if expected_parent_metadata is None:
            expected_parent_metadata = parent_before
        if (
            stat.S_ISLNK(parent_before.st_mode)
            or not stat.S_ISDIR(parent_before.st_mode)
            or not _same_metadata(parent_before, parent_opened)
            or not _same_inode(parent_opened, expected_parent_metadata)
        ):
            raise CpuBenchmarkValidationError(
                "validation output parent changed before publication"
            )
        protected = (
            collection_directory,
            application_root,
            repository,
        )
        if any(root is None for root in protected) and not all(
            root is None for root in protected
        ):
            raise CpuBenchmarkValidationError(
                "validation output confinement inputs are incomplete"
            )
        _verify_parent_path_identity(
            parent,
            parent_descriptor,
            expected_parent_metadata,
        )
        if all(root is not None for root in protected):
            _reject_output_in_input_tree(
                path,
                collection_directory=collection_directory,
                application_root=application_root,
                repository=repository,
            )
        try:
            descriptor = os.open(path.name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError as error:
            raise CpuBenchmarkValidationError(
                "validation output already exists"
            ) from error
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise CpuBenchmarkValidationError(
                    "validation output already exists"
                ) from error
            raise CpuBenchmarkValidationError(
                "validation output cannot be reserved safely"
            ) from error
        reserved = True
        os.fchmod(descriptor, 0o600)
        retained_metadata = os.fstat(descriptor)
        named_initial = _named_metadata(parent_descriptor, path.name)
        if (
            not stat.S_ISREG(retained_metadata.st_mode)
            or stat.S_IMODE(retained_metadata.st_mode) != 0o600
            or retained_metadata.st_size != 0
            or named_initial is None
            or not _same_metadata(retained_metadata, named_initial)
        ):
            raise CpuBenchmarkValidationError(
                "reserved validation output identity changed"
            )
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise CpuBenchmarkValidationError(
                    "could not write validation output"
                )
            remaining = remaining[written:]
        written_metadata = os.fstat(descriptor)
        named_written = _named_metadata(parent_descriptor, path.name)
        if (
            not stat.S_ISREG(written_metadata.st_mode)
            or stat.S_IMODE(written_metadata.st_mode) != 0o600
            or written_metadata.st_size != len(encoded)
            or named_written is None
            or not _same_metadata(written_metadata, named_written)
        ):
            raise CpuBenchmarkValidationError(
                "validation output identity changed while writing"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        read_remaining = len(encoded) + 1
        while read_remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, read_remaining))
            if not chunk:
                break
            chunks.append(chunk)
            read_remaining -= len(chunk)
        verified_metadata = os.fstat(descriptor)
        named_verified = _named_metadata(parent_descriptor, path.name)
        if (
            b"".join(chunks) != encoded
            or not _same_metadata(written_metadata, verified_metadata)
            or named_verified is None
            or not _same_metadata(verified_metadata, named_verified)
        ):
            raise CpuBenchmarkValidationError(
                "validation output content or identity changed during verification"
            )
        content_complete = True
        try:
            os.fsync(descriptor)
            os.fsync(parent_descriptor)
        except OSError as error:
            raise CpuBenchmarkValidationError(
                "validation output content was complete but durability sync failed; "
                "the retained output was preserved"
            ) from error
        final_metadata = os.fstat(descriptor)
        named_final = _named_metadata(parent_descriptor, path.name)
        if (
            not _same_metadata(verified_metadata, final_metadata)
            or named_final is None
            or not _same_metadata(final_metadata, named_final)
        ):
            raise CpuBenchmarkValidationError(
                "validation output changed after durability sync; the current "
                "output name was preserved"
            )
        _verify_parent_path_identity(
            parent,
            parent_descriptor,
            expected_parent_metadata,
        )
        if all(root is not None for root in protected):
            try:
                _reject_output_in_input_tree(
                    path,
                    collection_directory=collection_directory,
                    application_root=application_root,
                    repository=repository,
                )
            except CpuBenchmarkValidationError as error:
                raise CpuBenchmarkValidationError(
                    "validation output entered a protected input tree; retained "
                    "output residue was preserved"
                ) from error
        try:
            absolute_output = path.lstat()
        except OSError as error:
            raise CpuBenchmarkValidationError(
                "validation output is absent from its requested canonical path; "
                "retained output residue was preserved"
            ) from error
        if not _same_metadata(final_metadata, absolute_output):
            raise CpuBenchmarkValidationError(
                "validation output at the requested path changed; retained output "
                "residue was preserved"
            )
        _verify_parent_path_identity(
            parent,
            parent_descriptor,
            expected_parent_metadata,
        )
    except Exception as error:
        if reserved:
            if content_complete and isinstance(
                error, CpuBenchmarkValidationError
            ):
                raise
            state = "content was complete" if content_complete else "was incomplete"
            raise CpuBenchmarkValidationError(
                f"reserved validation output {state}; the current output name "
                "was preserved and must be inspected or removed before retry"
            ) from error
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def validate(
    arguments: argparse.Namespace,
    *,
    core: Any = cpu_benchmark_collection,
    collector: Any = collect_cpu_benchmark,
) -> dict[str, Any]:
    arguments = _normalize_arguments(arguments)
    payloads = _read_exact_bundle(arguments.collection_directory)
    bundle = _bundle_record(payloads)
    registry, schema_identities = _load_schemas(arguments.repository, core)
    tools_before = _tool_identities(arguments.repository, core)

    try:
        collection_value = core.strict_json_loads(
            payloads[COLLECTION_FILENAME],
            label="CPU benchmark collection",
            maximum_bytes=MAXIMUM_COLLECTION_BYTES,
        )
    except Exception as error:
        raise CpuBenchmarkValidationError(
            "CPU benchmark collection is not strict JSON"
        ) from error
    if type(collection_value) is not dict:
        raise CpuBenchmarkValidationError(
            "CPU benchmark collection must be an object"
        )
    _validate_schema_instance(
        collection_value,
        registry[COLLECTION_SCHEMA_ID],
        registry[COLLECTION_SCHEMA_ID],
        registry,
        "CPU benchmark collection",
    )
    if collection_value.get("launchCount") != LAUNCH_COUNT:
        raise CpuBenchmarkValidationError(
            "CPU benchmark collection must contain exactly five launches"
        )

    host_payload = payloads[HOST_OBSERVATIONS_FILENAME]
    try:
        host_record = core.strict_json_loads(
            host_payload,
            label="CPU benchmark host observations",
            maximum_bytes=MAXIMUM_HOST_OBSERVATION_BYTES,
        )
    except Exception as error:
        raise CpuBenchmarkValidationError(
            "CPU benchmark host observations are not strict JSON"
        ) from error
    raw_host = collection_value.get("rawHostObservation")
    if (
        type(host_record) is not dict
        or type(raw_host) is not dict
        or set(raw_host) != {"sha256", "record"}
        or raw_host.get("sha256") != hashlib.sha256(host_payload).hexdigest()
        or not _json_equal(raw_host.get("record"), host_record)
    ):
        raise CpuBenchmarkValidationError(
            "host-observation sidecar does not match the recorded raw evidence"
        )

    expected_launches: list[tuple[str, int]] = []
    for index in range(LAUNCH_COUNT):
        name = FRAGMENT_FILENAME.format(index=index)
        raw = payloads[name]
        try:
            value = core.strict_json_loads(
                raw,
                label=f"CPU benchmark fragment {index}",
                maximum_bytes=MAXIMUM_FRAGMENT_BYTES,
            )
        except Exception as error:
            raise CpuBenchmarkValidationError(
                f"CPU benchmark fragment {index} is not strict JSON"
            ) from error
        if type(value) is not dict:
            raise CpuBenchmarkValidationError(
                f"CPU benchmark fragment {index} must be an object"
            )
        _validate_schema_instance(
            value,
            registry[TARGET_SCHEMA_ID],
            registry[TARGET_SCHEMA_ID],
            registry,
            f"CPU benchmark fragment {index}",
        )
        challenge = value.get("launchChallenge")
        process_id = value.get("processId")
        if type(challenge) is not str or _SHA256.fullmatch(challenge) is None:
            raise CpuBenchmarkValidationError(
                f"CPU benchmark fragment {index} challenge is invalid"
            )
        if type(process_id) is not int or not 1 <= process_id <= 0x7FFF_FFFF:
            raise CpuBenchmarkValidationError(
                f"CPU benchmark fragment {index} process id is invalid"
            )
        expected_launches.append((challenge, process_id))

    artifacts_before, private_before = _artifact_snapshot(
        arguments, core, collector
    )
    environment = collection_value.get("environment")
    if type(environment) is not dict:
        raise CpuBenchmarkValidationError(
            "CPU benchmark collection environment must be an object"
        )
    try:
        expected_collection = core.derive_collection(
            fragment_payloads=[
                payloads[FRAGMENT_FILENAME.format(index=index)]
                for index in range(LAUNCH_COUNT)
            ],
            host_observation_payload=host_payload,
            expected_launches=expected_launches,
            artifacts=artifacts_before,
            collector_sha256=tools_before["collector"]["sha256"],
        )
    except Exception as error:
        raise CpuBenchmarkValidationError(
            "raw bundle cannot rederive a valid CPU benchmark collection"
        ) from error
    if not _json_equal(collection_value, expected_collection):
        raise CpuBenchmarkValidationError(
            "recorded collection does not exactly match raw-fragment rederivation"
        )

    artifacts_after, private_after = _artifact_snapshot(arguments, core, collector)
    tools_after = _tool_identities(arguments.repository, core)
    registry_after, schema_identities_after = _load_schemas(
        arguments.repository, core
    )
    payloads_after = _read_exact_bundle(arguments.collection_directory)
    if (
        not _json_equal(artifacts_before, artifacts_after)
        or not _json_equal(private_before, private_after)
        or not _json_equal(tools_before, tools_after)
        or not _json_equal(schema_identities, schema_identities_after)
        or not _json_equal(registry, registry_after)
        or payloads != payloads_after
    ):
        raise CpuBenchmarkValidationError(
            "bundle, source, tool, schema, or artifact identity changed during "
            "validation"
        )

    collection_identity = _identity(payloads[COLLECTION_FILENAME])
    validation = {
        "schemaVersion": 1,
        "result": "validated",
        "claimStatus": "measurement-only",
        "purpose": "cpu-benchmark-raw-bundle-offline-validation",
        "validationScope": "offline-consistency-only",
        "rawEvidenceRequiredForEvaluation": True,
        "bundle": bundle,
        "collection": {
            **collection_identity,
            "launchCount": LAUNCH_COUNT,
            "collectorSha256": tools_before["collector"]["sha256"],
        },
        "tools": tools_before,
        "schemas": schema_identities,
        "source": artifacts_before["repositoryEvidence"],
        "artifacts": artifacts_before,
        "recordedTarget": {
            "evidenceSource": "checked-host-observation-sidecar",
            "authenticationStatus": "not-independently-authenticated",
            "hostObservationSha256": raw_host["sha256"],
            "platform": environment["platform"],
            "architecture": environment["architecture"],
            "deviceIdentitySha256": environment["deviceIdentitySha256"],
            "osVersion": environment["osVersion"],
            "osBuild": environment["osBuild"],
            "driverIdentity": environment["driverIdentity"],
            "firmwareIdentity": environment["firmwareIdentity"],
        },
        "claimBoundary": _CLAIM_BOUNDARY,
    }
    _validate_schema_instance(
        validation,
        registry[VALIDATION_SCHEMA_ID],
        registry[VALIDATION_SCHEMA_ID],
        registry,
        "CPU benchmark validation record",
    )
    _reject_output_in_input_tree(
        arguments.output,
        collection_directory=arguments.collection_directory,
        application_root=arguments.application_root,
        repository=arguments.repository,
    )
    _publish_no_replace(
        arguments.output,
        validation,
        expected_parent_metadata=arguments.output_parent_metadata,
        collection_directory=arguments.collection_directory,
        application_root=arguments.application_root,
        repository=arguments.repository,
    )
    return validation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-directory", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--application-root", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--shim-artifact", type=Path, required=True)
    parser.add_argument("--runtime-artifact", type=Path, required=True)
    parser.add_argument("--resolver-manifest", type=Path, required=True)
    parser.add_argument(
        "--provider-dependency", type=Path, action="append", default=[]
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        record = validate(arguments)
    except (
        CpuBenchmarkValidationError,
        cpu_benchmark_collection.CpuBenchmarkCollectionError,
        collect_cpu_benchmark.CpuBenchmarkCollectorError,
        FileNotFoundError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"validate_cpu_benchmark_collection: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "result": record["result"],
                "claimStatus": record["claimStatus"],
                "launchCount": record["collection"]["launchCount"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
