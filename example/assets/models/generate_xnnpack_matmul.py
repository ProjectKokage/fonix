#!/usr/bin/env python3
"""Generate and verify the deterministic Android XNNPACK MatMul fixture.

The ONNX message is encoded directly from the field numbers in onnx-ml.proto.
Only Python's standard library is required, which keeps the committed model
reproducible without depending on an onnx, protobuf, or NumPy wheel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parent
MODEL_FILENAME = "xnnpack_matmul.onnx"
METADATA_FILENAME = "xnnpack_matmul.json"
MODEL_ASSET_PATH = f"assets/models/{MODEL_FILENAME}"
GENERATOR_ASSET_PATH = "assets/models/generate_xnnpack_matmul.py"

FLOAT32 = 1  # TensorProto.DataType.FLOAT
IR_VERSION = 8
OPSET_VERSION = 17
GENERATOR_VERSION = "xnnpack-matmul-v1"

INPUT_SHAPE = (3, 2)
WEIGHT_SHAPE = (2, 2)
OUTPUT_SHAPE = (3, 2)
INPUT_VALUES = (1, 2, 3, 4, 5, 6)
WEIGHT_VALUES = (1, 2, 3, 4)
OUTPUT_VALUES = (7, 10, 15, 22, 23, 34)


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varints in this fixture must be non-negative")
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _key(field_number: int, wire_type: int) -> bytes:
    if field_number <= 0 or wire_type not in (0, 2):
        raise ValueError("invalid protobuf field key")
    return _varint((field_number << 3) | wire_type)


def _integer(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _varint(value)


def _bytes(field_number: int, value: bytes) -> bytes:
    return _key(field_number, 2) + _varint(len(value)) + value


def _string(field_number: int, value: str) -> bytes:
    return _bytes(field_number, value.encode("utf-8"))


def _message(field_number: int, value: bytes) -> bytes:
    return _bytes(field_number, value)


def _tensor_type(shape: Iterable[int]) -> bytes:
    # TypeProto.tensor_type -> TypeProto.Tensor -> TensorShapeProto.Dimension.
    dimensions = b"".join(
        _message(1, _integer(1, dimension)) for dimension in shape
    )
    tensor = _integer(1, FLOAT32) + _message(2, dimensions)
    return _message(1, tensor)


def _value_info(name: str, shape: Iterable[int]) -> bytes:
    return _string(1, name) + _message(2, _tensor_type(shape))


def _raw_float_tensor(
    name: str,
    shape: Iterable[int],
    values: Sequence[int],
) -> bytes:
    dimensions = tuple(shape)
    element_count = 1
    for dimension in dimensions:
        if dimension <= 0:
            raise AssertionError("fixture initializer dimensions must be positive")
        element_count *= dimension
    if element_count != len(values):
        raise AssertionError("initializer shape does not match its values")
    value = b"".join(_integer(1, dimension) for dimension in dimensions)
    value += _integer(2, FLOAT32)
    value += _string(8, name)
    value += _bytes(9, struct.pack(f"<{len(values)}f", *values))
    return value


def _reference_output() -> tuple[int, ...]:
    rows = INPUT_SHAPE[0]
    reduction = INPUT_SHAPE[1]
    columns = WEIGHT_SHAPE[1]
    values: list[int] = []
    for row in range(rows):
        for column in range(columns):
            total = 0
            for inner in range(reduction):
                total += (
                    INPUT_VALUES[row * reduction + inner]
                    * WEIGHT_VALUES[inner * columns + column]
                )
            values.append(total)
    return tuple(values)


def _validate_contract() -> None:
    if _reference_output() != OUTPUT_VALUES:
        raise AssertionError("independent MatMul reference output changed")
    if INPUT_SHAPE[1] != WEIGHT_SHAPE[0]:
        raise AssertionError("MatMul reduction dimensions are inconsistent")
    expected_output_shape = (INPUT_SHAPE[0], WEIGHT_SHAPE[1])
    if expected_output_shape != OUTPUT_SHAPE:
        raise AssertionError("MatMul output shape is inconsistent")
    for value in INPUT_VALUES + WEIGHT_VALUES + OUTPUT_VALUES:
        encoded = struct.pack("<f", value)
        if struct.unpack("<f", encoded)[0] != value:
            raise AssertionError("fixture value is not exactly representable as float32")


def _model_bytes() -> bytes:
    _validate_contract()

    # NodeProto: input = 1, output = 2, name = 3, op_type = 4.
    node = b"".join(_string(1, name) for name in ("input", "weight"))
    node += _string(2, "output")
    node += _string(3, "static_float_matmul")
    node += _string(4, "MatMul")

    # GraphProto: node = 1, name = 2, initializer = 5, input/output = 11/12.
    graph = _message(1, node)
    graph += _string(2, "xnnpack_static_matmul")
    graph += _message(
        5,
        _raw_float_tensor("weight", WEIGHT_SHAPE, WEIGHT_VALUES),
    )
    graph += _message(11, _value_info("input", INPUT_SHAPE))
    graph += _message(12, _value_info("output", OUTPUT_SHAPE))

    # ModelProto and OperatorSetIdProto. An omitted opset domain is the ONNX
    # default domain. IR v8 and opset 17 are supported by locked ORT 1.27.1.
    model = _integer(1, IR_VERSION)
    model += _string(2, "fonix-reference-fixtures")
    model += _string(3, GENERATOR_VERSION)
    model += _string(4, "dev.fonix.reference")
    model += _integer(5, 1)
    model += _string(
        6,
        "Deterministic static-weight floating-point XNNPACK assignment fixture.",
    )
    model += _message(7, graph)
    model += _message(8, _integer(2, OPSET_VERSION))
    return model


def _metadata_bytes(model: bytes) -> bytes:
    model_sha256 = hashlib.sha256(model).hexdigest()
    value = {
        "schemaVersion": 1,
        "id": f"xnnpack-matmul-sha256-{model_sha256}",
        "path": MODEL_ASSET_PATH,
        "generator": GENERATOR_ASSET_PATH,
        "generatorVersion": GENERATOR_VERSION,
        "sha256": model_sha256,
        "sizeBytes": len(model),
        "onnxIrVersion": IR_VERSION,
        "opset": OPSET_VERSION,
        "operator": "MatMul",
        "input": {
            "name": "input",
            "elementType": "float32",
            "shape": list(INPUT_SHAPE),
            "values": list(INPUT_VALUES),
        },
        "initializer": {
            "name": "weight",
            "elementType": "float32",
            "shape": list(WEIGHT_SHAPE),
            "values": list(WEIGHT_VALUES),
        },
        "matrixMultiplication": {
            "leftShape": list(INPUT_SHAPE),
            "rightShape": list(WEIGHT_SHAPE),
        },
        "output": {
            "name": "output",
            "elementType": "float32",
            "shape": list(OUTPUT_SHAPE),
            "values": list(OUTPUT_VALUES),
        },
        "referencePolicy": "exact-float32",
        "claimBoundary": (
            "Functional provider-assignment and CPU-parity fixture only; not a "
            "performance, thermal, physical-device, or provider-qualification "
            "workload."
        ),
    }
    return (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def _expected_outputs() -> tuple[bytes, bytes]:
    model = _model_bytes()
    return model, _metadata_bytes(model)


def _write_exact(path: Path, contents: bytes) -> None:
    try:
        existing = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(existing.st_mode):
            raise RuntimeError(f"refusing to replace non-regular file: {path.name}")

    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(temporary, flags, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_exact_regular_file(path: Path, expected_size: int) -> tuple[bytes, str]:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return b"", f"missing: {path.name}"
    except OSError as error:
        return b"", f"could not inspect {path.name}: {error}"
    if not stat.S_ISREG(before.st_mode):
        return b"", f"not a regular non-link file: {path.name}"
    if before.st_size != expected_size:
        return b"", f"size mismatch: {path.name}"

    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size != expected_size
            ):
                return b"", f"file changed while opening: {path.name}"
            contents = stream.read(expected_size + 1)
            after = os.fstat(stream.fileno())
    except OSError as error:
        return b"", f"could not read {path.name}: {error}"
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        or len(contents) != expected_size
    ):
        return b"", f"file changed while reading: {path.name}"
    return contents, ""


def _write(*, root: Path = ROOT) -> None:
    model, metadata = _expected_outputs()
    _write_exact(root / MODEL_FILENAME, model)
    _write_exact(root / METADATA_FILENAME, metadata)


def _check(*, root: Path = ROOT) -> int:
    model, metadata = _expected_outputs()
    failures: list[str] = []
    for filename, expected in (
        (MODEL_FILENAME, model),
        (METADATA_FILENAME, metadata),
    ):
        actual, failure = _read_exact_regular_file(root / filename, len(expected))
        if failure:
            failures.append(failure)
        elif actual != expected:
            failures.append(
                f"byte mismatch: {filename} differs from deterministic output"
            )
    if failures:
        print("XNNPACK MatMul fixture verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(
        "verified deterministic XNNPACK MatMul fixture "
        f"({len(model)} model bytes, sha256={hashlib.sha256(model).hexdigest()})"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="compare committed bytes")
    mode.add_argument("--write", action="store_true", help="regenerate committed bytes")
    arguments = parser.parse_args(argv)

    if arguments.write:
        _write()
        return 0
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
