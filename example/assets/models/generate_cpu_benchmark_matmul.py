#!/usr/bin/env python3
"""Generate and verify the deterministic CPU benchmark MatMul fixture.

The ONNX message is encoded directly from the field numbers in onnx-ml.proto.
Only Python's standard library is required. The input and expected output are
separate little-endian IEEE-754 binary32 files so a future target harness can
bind and load the exact benchmark tuple without embedding large JSON arrays.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import sys
from typing import Iterable, Iterator, Sequence


ROOT = Path(__file__).resolve().parent
MODEL_FILENAME = "cpu_benchmark_matmul.onnx"
INPUT_FILENAME = "cpu_benchmark_matmul.input.f32le"
OUTPUT_FILENAME = "cpu_benchmark_matmul.output.f32le"
METADATA_FILENAME = "cpu_benchmark_matmul.json"
GENERATOR_FILENAME = "generate_cpu_benchmark_matmul.py"

ASSET_ROOT = "assets/models"
MODEL_ASSET_PATH = f"{ASSET_ROOT}/{MODEL_FILENAME}"
INPUT_ASSET_PATH = f"{ASSET_ROOT}/{INPUT_FILENAME}"
OUTPUT_ASSET_PATH = f"{ASSET_ROOT}/{OUTPUT_FILENAME}"
GENERATOR_ASSET_PATH = f"{ASSET_ROOT}/{GENERATOR_FILENAME}"

FLOAT32 = 1  # TensorProto.DataType.FLOAT
IR_VERSION = 8
OPSET_VERSION = 17
GENERATOR_VERSION = "cpu-benchmark-matmul-v1"

INPUT_NAME = "input"
INITIALIZER_NAME = "weight"
OUTPUT_NAME = "output"
INPUT_SHAPE = (2048, 1024)
WEIGHT_SHAPE = (1024, 1024)
OUTPUT_SHAPE = (2048, 1024)

PATTERN_PERIOD = 64
INPUT_ROW_STRIDE = 17
WEIGHT_COLUMN_STRIDE = 29
INPUT_PATTERN_PARAMETERS = {
    "seed": 0x1234ABCD,
    "multiplier": 1_664_525,
    "increment": 1_013_904_223,
    "modulus": 1 << 32,
    "valueModulus": 7,
    "valueOffset": 3,
}
WEIGHT_PATTERN_PARAMETERS = {
    "seed": 0xC001D00D,
    "multiplier": 22_695_477,
    "increment": 1,
    "modulus": 1 << 32,
    "valueModulus": 5,
    "valueOffset": 2,
}

MAX_GENERATOR_BYTES = 1024 * 1024
FLOAT32_EXACT_INTEGER_LIMIT = 1 << 24
PACK_CHUNK_VALUES = 4096

_EXPECTED_OUTPUTS_CACHE: tuple[bytes, bytes, bytes, bytes] | None = None


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
    dimensions = b"".join(
        _message(1, _integer(1, dimension)) for dimension in shape
    )
    tensor = _integer(1, FLOAT32) + _message(2, dimensions)
    return _message(1, tensor)


def _value_info(name: str, shape: Iterable[int]) -> bytes:
    return _string(1, name) + _message(2, _tensor_type(shape))


def _lcg_pattern(parameters: dict[str, int]) -> tuple[int, ...]:
    state = parameters["seed"]
    values: list[int] = []
    for _ in range(PATTERN_PERIOD):
        state = (
            state * parameters["multiplier"] + parameters["increment"]
        ) % parameters["modulus"]
        values.append(
            state % parameters["valueModulus"] - parameters["valueOffset"]
        )
    return tuple(values)


INPUT_PATTERN = _lcg_pattern(INPUT_PATTERN_PARAMETERS)
WEIGHT_PATTERN = _lcg_pattern(WEIGHT_PATTERN_PARAMETERS)


def _input_values() -> tuple[int, ...]:
    rows, reduction = INPUT_SHAPE
    return tuple(
        INPUT_PATTERN[(inner + row * INPUT_ROW_STRIDE) % PATTERN_PERIOD]
        for row in range(rows)
        for inner in range(reduction)
    )


def _weight_values() -> tuple[int, ...]:
    reduction, columns = WEIGHT_SHAPE
    return tuple(
        WEIGHT_PATTERN[(inner + column * WEIGHT_COLUMN_STRIDE) % PATTERN_PERIOD]
        for inner in range(reduction)
        for column in range(columns)
    )


def _reference_tile_from_expanded_values(
    input_values: Sequence[int],
    weight_values: Sequence[int],
) -> tuple[tuple[int, ...], ...]:
    reduction = INPUT_SHAPE[1]
    columns = WEIGHT_SHAPE[1]
    rows: list[tuple[int, ...]] = []
    for row in range(PATTERN_PERIOD):
        input_offset = row * reduction
        output_row: list[int] = []
        for column in range(PATTERN_PERIOD):
            total = 0
            for inner in range(reduction):
                total += (
                    input_values[input_offset + inner]
                    * weight_values[inner * columns + column]
                )
            output_row.append(total)
        rows.append(tuple(output_row))
    return tuple(rows)


def _verify_reference_tile_independently(
    tile: Sequence[Sequence[int]],
) -> None:
    repetitions = INPUT_SHAPE[1] // PATTERN_PERIOD
    for row in range(PATTERN_PERIOD):
        for column in range(PATTERN_PERIOD):
            products = (
                float(INPUT_PATTERN[(phase + row * INPUT_ROW_STRIDE) % PATTERN_PERIOD])
                * float(
                    WEIGHT_PATTERN[
                        (phase + column * WEIGHT_COLUMN_STRIDE) % PATTERN_PERIOD
                    ]
                )
                for phase in range(PATTERN_PERIOD)
            )
            independently_computed = repetitions * math.fsum(products)
            if independently_computed != tile[row][column]:
                raise AssertionError(
                    "independent periodic float reference disagrees with "
                    "expanded integer MatMul"
                )


def _reference_output_values(
    input_values: Sequence[int],
    weight_values: Sequence[int],
) -> tuple[int, ...]:
    tile = _reference_tile_from_expanded_values(input_values, weight_values)
    _verify_reference_tile_independently(tile)
    rows, columns = OUTPUT_SHAPE
    return tuple(
        tile[row % PATTERN_PERIOD][column % PATTERN_PERIOD]
        for row in range(rows)
        for column in range(columns)
    )


def _chunks(values: Sequence[int]) -> Iterator[Sequence[int]]:
    for offset in range(0, len(values), PACK_CHUNK_VALUES):
        yield values[offset : offset + PACK_CHUNK_VALUES]


def _float32_bytes(values: Sequence[int]) -> bytes:
    return b"".join(
        struct.pack(f"<{len(chunk)}f", *chunk) for chunk in _chunks(values)
    )


def _raw_float_tensor(name: str, shape: Iterable[int], raw_data: bytes) -> bytes:
    dimensions = tuple(shape)
    element_count = 1
    for dimension in dimensions:
        if dimension <= 0:
            raise AssertionError("fixture initializer dimensions must be positive")
        element_count *= dimension
    if len(raw_data) != element_count * 4:
        raise AssertionError("initializer shape does not match its raw data")
    value = b"".join(_integer(1, dimension) for dimension in dimensions)
    value += _integer(2, FLOAT32)
    value += _string(8, name)
    value += _bytes(9, raw_data)
    return value


def _validate_contract(
    input_values: Sequence[int],
    weight_values: Sequence[int],
    output_values: Sequence[int],
) -> None:
    if INPUT_SHAPE[1] != WEIGHT_SHAPE[0]:
        raise AssertionError("MatMul reduction dimensions are inconsistent")
    if (INPUT_SHAPE[0], WEIGHT_SHAPE[1]) != OUTPUT_SHAPE:
        raise AssertionError("MatMul output shape is inconsistent")
    if INPUT_SHAPE[1] % PATTERN_PERIOD != 0:
        raise AssertionError("reduction dimension must contain whole patterns")

    expected_lengths = (
        (input_values, math.prod(INPUT_SHAPE), "input"),
        (weight_values, math.prod(WEIGHT_SHAPE), "initializer"),
        (output_values, math.prod(OUTPUT_SHAPE), "output"),
    )
    for values, expected_length, label in expected_lengths:
        if len(values) != expected_length:
            raise AssertionError(f"{label} shape does not match its values")

    maximum_partial_sum = (
        INPUT_SHAPE[1] * max(abs(value) for value in INPUT_PATTERN)
        * max(abs(value) for value in WEIGHT_PATTERN)
    )
    if maximum_partial_sum >= FLOAT32_EXACT_INTEGER_LIMIT:
        raise AssertionError("integer accumulation could exceed exact float32 range")
    for value in (*INPUT_PATTERN, *WEIGHT_PATTERN, *output_values):
        encoded = struct.pack("<f", value)
        if struct.unpack("<f", encoded)[0] != value:
            raise AssertionError("fixture value is not exactly representable as float32")


def _model_bytes(weight_data: bytes) -> bytes:
    node = b"".join(
        _string(1, name) for name in (INPUT_NAME, INITIALIZER_NAME)
    )
    node += _string(2, OUTPUT_NAME)
    node += _string(3, "cpu_benchmark_static_float_matmul")
    node += _string(4, "MatMul")

    graph = _message(1, node)
    graph += _string(2, "cpu_benchmark_static_matmul")
    graph += _message(
        5,
        _raw_float_tensor(INITIALIZER_NAME, WEIGHT_SHAPE, weight_data),
    )
    graph += _message(11, _value_info(INPUT_NAME, INPUT_SHAPE))
    graph += _message(12, _value_info(OUTPUT_NAME, OUTPUT_SHAPE))

    model = _integer(1, IR_VERSION)
    model += _string(2, "fonix-reference-fixtures")
    model += _string(3, GENERATOR_VERSION)
    model += _string(4, "dev.fonix.reference")
    model += _integer(5, 1)
    model += _string(
        6,
        "Deterministic static-weight CPU MatMul measurement workload.",
    )
    model += _message(7, graph)
    model += _message(8, _integer(2, OPSET_VERSION))
    return model


def _generator_identity() -> tuple[int, str]:
    path = Path(os.path.abspath(__file__))
    try:
        path_before = path.lstat()
    except OSError as error:
        raise RuntimeError(f"could not inspect generator: {error}") from error
    if (
        not stat.S_ISREG(path_before.st_mode)
        or path_before.st_size <= 0
        or path_before.st_size > MAX_GENERATOR_BYTES
    ):
        raise RuntimeError("generator must be a bounded regular non-link file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(f"could not open generator safely: {error}") from error
    try:
        before = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(path_before):
            raise RuntimeError("generator changed before reading")
        chunks: list[bytes] = []
        consumed = 0
        while consumed <= MAX_GENERATOR_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_GENERATOR_BYTES + 1 - consumed),
            )
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = path.lstat()
    except OSError as error:
        raise RuntimeError(f"generator changed while reading: {error}") from error
    if (
        consumed != before.st_size
        or consumed > MAX_GENERATOR_BYTES
        or _stat_identity(before) != _stat_identity(after)
        or _stat_identity(before) != _stat_identity(path_after)
    ):
        raise RuntimeError("generator changed while reading")
    contents = b"".join(chunks)
    return consumed, hashlib.sha256(contents).hexdigest()


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _artifact(path: str, contents: bytes, encoding: str) -> dict[str, object]:
    return {
        "path": path,
        "encoding": encoding,
        "sizeBytes": len(contents),
        "sha256": hashlib.sha256(contents).hexdigest(),
    }


def _metadata_bytes(
    model: bytes,
    input_data: bytes,
    output_data: bytes,
    weight_data: bytes,
) -> bytes:
    generator_size, generator_sha256 = _generator_identity()
    multiplication_count = math.prod(INPUT_SHAPE) * WEIGHT_SHAPE[1]
    addition_count = OUTPUT_SHAPE[0] * OUTPUT_SHAPE[1] * (INPUT_SHAPE[1] - 1)
    model_sha256 = hashlib.sha256(model).hexdigest()
    value = {
        "schemaVersion": 1,
        "id": f"cpu-benchmark-matmul-sha256-{model_sha256}",
        "generator": {
            "path": GENERATOR_ASSET_PATH,
            "version": GENERATOR_VERSION,
            "sizeBytes": generator_size,
            "sha256": generator_sha256,
            "dependencies": "python-standard-library-only",
        },
        "model": {
            **_artifact(MODEL_ASSET_PATH, model, "onnx-protobuf"),
            "onnxIrVersion": IR_VERSION,
            "opset": OPSET_VERSION,
            "operator": "MatMul",
        },
        "input": {
            "name": INPUT_NAME,
            "elementType": "float32",
            "shape": list(INPUT_SHAPE),
            "data": _artifact(
                INPUT_ASSET_PATH,
                input_data,
                "ieee754-binary32-little-endian",
            ),
        },
        "initializer": {
            "name": INITIALIZER_NAME,
            "elementType": "float32",
            "shape": list(WEIGHT_SHAPE),
            "storage": "embedded-onnx-raw-data",
            "rawDataSizeBytes": len(weight_data),
            "rawDataSha256": hashlib.sha256(weight_data).hexdigest(),
        },
        "matrixMultiplication": {
            "leftShape": list(INPUT_SHAPE),
            "rightShape": list(WEIGHT_SHAPE),
            "outputShape": list(OUTPUT_SHAPE),
            "scalarMultiplicationCount": multiplication_count,
            "scalarAdditionCount": addition_count,
            "multiplyAccumulateCount": multiplication_count,
            "flopCountConvention": "two-per-multiply-accumulate",
            "flopCount": multiplication_count * 2,
        },
        "output": {
            "name": OUTPUT_NAME,
            "elementType": "float32",
            "shape": list(OUTPUT_SHAPE),
            "referenceData": _artifact(
                OUTPUT_ASSET_PATH,
                output_data,
                "ieee754-binary32-little-endian",
            ),
        },
        "determinism": {
            "byteOrder": "little-endian",
            "patternPeriod": PATTERN_PERIOD,
            "input": {
                "algorithm": "lcg32-periodic-row-rotation",
                **INPUT_PATTERN_PARAMETERS,
                "rowStride": INPUT_ROW_STRIDE,
            },
            "initializer": {
                "algorithm": "lcg32-periodic-column-rotation",
                **WEIGHT_PATTERN_PARAMETERS,
                "columnStride": WEIGHT_COLUMN_STRIDE,
            },
            "referenceComputation": (
                "expanded-integer-matmul-over-complete-64x64-fundamental-tile"
            ),
            "independentVerification": (
                "periodic-ieee754-math-fsum-correlation-for-every-tile-value"
            ),
        },
        "referencePolicy": {
            "comparison": "exact-ieee754-binary32-bits",
            "absoluteTolerance": 0,
            "relativeTolerance": 0,
            "nanPolicy": "forbid",
            "infinityPolicy": "forbid",
            "reason": (
                "All operands, products, partial sums, and outputs are exact "
                "integers with magnitude below 2^24."
            ),
        },
        "claimBoundary": (
            "Representative deterministic CPU timing workload for measurement "
            "receipt generation only. Its dimensions are a calibration starting "
            "point, not a latency, throughput, real-time, support, provider-"
            "qualification, regression-threshold, portability, or release claim."
        ),
    }
    return (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def _expected_outputs() -> tuple[bytes, bytes, bytes, bytes]:
    global _EXPECTED_OUTPUTS_CACHE
    if _EXPECTED_OUTPUTS_CACHE is not None:
        return _EXPECTED_OUTPUTS_CACHE

    input_values = _input_values()
    weight_values = _weight_values()
    output_values = _reference_output_values(input_values, weight_values)
    _validate_contract(input_values, weight_values, output_values)

    input_data = _float32_bytes(input_values)
    weight_data = _float32_bytes(weight_values)
    output_data = _float32_bytes(output_values)
    model = _model_bytes(weight_data)
    metadata = _metadata_bytes(model, input_data, output_data, weight_data)
    _EXPECTED_OUTPUTS_CACHE = (model, input_data, output_data, metadata)
    return _EXPECTED_OUTPUTS_CACHE


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


def _output_files() -> tuple[tuple[str, bytes], ...]:
    model, input_data, output_data, metadata = _expected_outputs()
    return (
        (MODEL_FILENAME, model),
        (INPUT_FILENAME, input_data),
        (OUTPUT_FILENAME, output_data),
        (METADATA_FILENAME, metadata),
    )


def _write(*, root: Path = ROOT) -> None:
    for filename, contents in _output_files():
        _write_exact(root / filename, contents)


def _check(*, root: Path = ROOT) -> int:
    outputs = _output_files()
    failures: list[str] = []
    for filename, expected in outputs:
        actual, failure = _read_exact_regular_file(root / filename, len(expected))
        if failure:
            failures.append(failure)
        elif actual != expected:
            failures.append(
                f"byte mismatch: {filename} differs from deterministic output"
            )
    if failures:
        print("CPU benchmark MatMul fixture verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    model = outputs[0][1]
    print(
        "verified deterministic CPU benchmark MatMul fixture "
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
