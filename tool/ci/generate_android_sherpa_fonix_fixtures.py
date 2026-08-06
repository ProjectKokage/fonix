#!/usr/bin/env python3
"""Generate the closed deterministic fixtures for Android sherpa coexistence.

The ONNX model is encoded directly from the public protobuf field numbers and
uses only the Python standard library. One dynamic square-matrix session serves
both the small exact-reference runs and the bounded cancellation run. Sixty-four
MatMul nodes provide native termination checkpoints without creating a second
session or relying on a timing sleep. The VAD audio is produced by an
integer-only source/filter synthesizer, so it has no recording provenance or
platform-libm reproducibility dependency.
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


SCHEMA_VERSION = 1
GENERATOR_VERSION = "android-sherpa-fonix-v1"
MODEL_FILENAME = "fonix_dynamic_matmul_chain.onnx"
REFERENCE_INPUT_FILENAME = "fonix_reference_input.bin"
REFERENCE_OUTPUT_FILENAME = "fonix_reference_output.bin"
CANCELLATION_INPUT_FILENAME = "fonix_cancellation_input.bin"
MANIFEST_FILENAME = "fonix_fixture_manifest.json"
SHERPA_AUDIO_FILENAME = "sherpa_synthetic_speech.wav"
SHERPA_REFERENCE_FILENAME = "sherpa_vad_reference.json"

MODEL_INPUT_NAME = "input.matrix"
MODEL_WEIGHT_NAME = "weight.matrix"
MODEL_OUTPUT_NAME = "output.matrix"
MATMUL_NODE_COUNT = 64
REFERENCE_DIMENSION = 2
CANCELLATION_DIMENSION = 512
MAX_OUTPUT_DIRECTORY_ENTRIES = 16
MAX_GENERATED_FILE_BYTES = 16 * 1024 * 1024

FLOAT32 = 1
IR_VERSION = 8
OPSET_VERSION = 17
MATRIX_INPUT_MAGIC = b"FONIXM1\0"
MATRIX_INPUT_HEADER = struct.Struct("<8sIIII")

SHERPA_PACKAGE_VERSION = "1.13.4"
SHERPA_NATIVE_REVISION = "142807252687d81b40d6315f23470a1512a00de3"
SHERPA_MODEL_FILENAME = "silero_vad.int8.onnx"
SHERPA_MODEL_SIZE_BYTES = 212_860
SHERPA_MODEL_SHA256 = (
    "c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20"
)
SHERPA_PROFILE_ID = "silero-vad-load-order-v1"
SHERPA_AUDIO_GENERATOR_ID = "android-sherpa-synthetic-vad-v1"
SHERPA_SAMPLE_RATE = 16_000
SHERPA_AUDIO_SAMPLES = 128_000
SHERPA_OBSERVED_START_SAMPLE = 11_872
SHERPA_OBSERVED_SAMPLE_COUNT = 96_160
SHERPA_INVARIANT_MINIMUM_SAMPLES = 95_232
SHERPA_INVARIANT_MAXIMUM_SAMPLES = 97_280

_SHERPA_SYLLABLES = (
    (12_000, 21_600, 136, ((30_880, -15_815), (29_060, -15_569), (18_187, -15_266))),
    (24_000, 34_400, 121, ((31_499, -15_815), (23_960, -15_569), (17_779, -15_266))),
    (36_800, 48_800, 152, ((31_391, -15_815), (30_220, -15_569), (18_491, -15_266))),
    (51_200, 62_400, 110, ((32_013, -15_815), (19_874, -15_569), (11_989, -15_266))),
    (64_800, 77_600, 129, ((31_971, -15_815), (30_096, -15_569), (20_162, -15_266))),
    (80_000, 92_000, 117, ((31_119, -15_815), (24_929, -15_569), (18_491, -15_266))),
    (94_400, 106_400, 144, ((31_798, -15_815), (22_587, -15_569), (17_053, -15_266))),
)


class FixtureGenerationError(RuntimeError):
    """The requested fixture output cannot be generated safely."""


def _varint(value: int) -> bytes:
    if value < 0:
        raise AssertionError("fixture varints must be non-negative")
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _key(field_number: int, wire_type: int) -> bytes:
    if field_number <= 0 or wire_type not in (0, 2):
        raise AssertionError("invalid protobuf field key")
    return _varint((field_number << 3) | wire_type)


def _integer(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _varint(value)


def _bytes(field_number: int, value: bytes) -> bytes:
    return _key(field_number, 2) + _varint(len(value)) + value


def _string(field_number: int, value: str) -> bytes:
    return _bytes(field_number, value.encode("utf-8"))


def _message(field_number: int, value: bytes) -> bytes:
    return _bytes(field_number, value)


def _dimension(symbol: str) -> bytes:
    return _string(2, symbol)


def _tensor_type(symbols: Iterable[str]) -> bytes:
    dimensions = b"".join(_message(1, _dimension(symbol)) for symbol in symbols)
    tensor = _integer(1, FLOAT32) + _message(2, dimensions)
    return _message(1, tensor)


def _value_info(name: str) -> bytes:
    return _string(1, name) + _message(2, _tensor_type(("size", "size")))


def model_bytes() -> bytes:
    nodes = bytearray()
    previous = MODEL_INPUT_NAME
    for index in range(MATMUL_NODE_COUNT):
        output = (
            MODEL_OUTPUT_NAME
            if index == MATMUL_NODE_COUNT - 1
            else f"stage.{index + 1:02d}"
        )
        node = _string(1, previous)
        node += _string(1, MODEL_WEIGHT_NAME)
        node += _string(2, output)
        node += _string(3, f"bounded_matmul_{index + 1:02d}")
        node += _string(4, "MatMul")
        nodes += _message(1, node)
        previous = output

    graph = bytes(nodes)
    graph += _string(2, "android_sherpa_fonix_dynamic_matmul_chain")
    graph += _message(11, _value_info(MODEL_INPUT_NAME))
    graph += _message(11, _value_info(MODEL_WEIGHT_NAME))
    graph += _message(12, _value_info(MODEL_OUTPUT_NAME))

    model = _integer(1, IR_VERSION)
    model += _string(2, "fonix-reference-fixtures")
    model += _string(3, GENERATOR_VERSION)
    model += _string(4, "dev.fonix.android.sherpa")
    model += _integer(5, 1)
    model += _string(
        6,
        "Dynamic MatMul chain for exact CPU output and active cancellation settlement.",
    )
    model += _message(7, graph)
    model += _message(8, _integer(2, OPSET_VERSION))
    return model


def _identity_values(dimension: int) -> Iterable[float]:
    for row in range(dimension):
        for column in range(dimension):
            yield 1.0 if row == column else 0.0


def _cancellation_input_values(dimension: int) -> Iterable[float]:
    # Runtime input prevents constant folding. Bounded signed values avoid NaN,
    # infinity, and unbounded growth across the repeated identity products.
    for row in range(dimension):
        for column in range(dimension):
            yield float(((row * 17 + column * 31) % 23) - 11) / 16.0


def matrix_input_bytes(
    dimension: int,
    left_values: Iterable[float],
    right_values: Iterable[float],
) -> bytes:
    if dimension <= 0 or dimension > CANCELLATION_DIMENSION:
        raise AssertionError("matrix fixture dimension is outside the closed bound")
    element_count = dimension * dimension
    left = tuple(left_values)
    right = tuple(right_values)
    if len(left) != element_count or len(right) != element_count:
        raise AssertionError("matrix fixture value count changed")
    header = MATRIX_INPUT_HEADER.pack(
        MATRIX_INPUT_MAGIC,
        SCHEMA_VERSION,
        dimension,
        dimension,
        dimension,
    )
    return header + struct.pack(f"<{element_count * 2}f", *left, *right)


def reference_input_bytes() -> bytes:
    return matrix_input_bytes(
        REFERENCE_DIMENSION,
        (1.0, 2.0, 3.0, 4.0),
        _identity_values(REFERENCE_DIMENSION),
    )


def reference_output_bytes() -> bytes:
    # Every one of the 64 products uses the exact 2x2 identity matrix.
    return struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)


def cancellation_input_bytes() -> bytes:
    return matrix_input_bytes(
        CANCELLATION_DIMENSION,
        _cancellation_input_values(CANCELLATION_DIMENSION),
        _identity_values(CANCELLATION_DIMENSION),
    )


def sherpa_audio_bytes() -> bytes:
    """Return a canonical PCM16 WAV using cross-platform integer arithmetic."""
    raw = [0] * SHERPA_AUDIO_SAMPLES
    noise = 0xF017A55A
    for start, end, pitch_period, coefficients in _SHERPA_SYLLABLES:
        states = [[0, 0] for _ in coefficients]
        phase = 0
        for index in range(start, end):
            local = index - start
            remaining = end - index
            ramp = min(1024, local * 1024 // 640, remaining * 1024 // 960)
            if phase == 0:
                excitation = 180_000
            elif phase < pitch_period // 3:
                excitation = -18_000
            else:
                excitation = 0
            phase += 1
            if phase >= pitch_period:
                phase = 0
            noise = (1_664_525 * noise + 1_013_904_223) & 0xFFFFFFFF
            breath = ((noise >> 16) - 32_768) * (1 if local < 960 else 0)
            excitation += breath // 10
            value = 0
            for state, (coefficient_1, coefficient_2) in zip(
                states,
                coefficients,
            ):
                current = excitation + (
                    (coefficient_1 * state[0] + coefficient_2 * state[1]) >> 14
                )
                state[1] = state[0]
                state[0] = current
                value += current
            raw[index] = value * ramp // 1024
    peak = max(abs(value) for value in raw)
    if peak <= 0:
        raise AssertionError("synthetic VAD fixture is silent")
    pcm = b"".join(
        struct.pack(
            "<h",
            max(-32_768, min(32_767, value * 23_592 // peak)),
        )
        for value in raw
    )
    data_bytes = len(pcm)
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + data_bytes),
            b"WAVE",
            b"fmt ",
            struct.pack(
                "<IHHIIHH",
                16,
                1,
                1,
                SHERPA_SAMPLE_RATE,
                SHERPA_SAMPLE_RATE * 2,
                2,
                16,
            ),
            b"data",
            struct.pack("<I", data_bytes),
            pcm,
        )
    )


def sherpa_reference_bytes(audio: bytes) -> bytes:
    audio_identity = _identity(audio)
    reference = {
        "schemaVersion": SCHEMA_VERSION,
        "sherpa": {
            "packageVersion": SHERPA_PACKAGE_VERSION,
            "nativeRevision": SHERPA_NATIVE_REVISION,
        },
        "profile": {
            "id": SHERPA_PROFILE_ID,
            "provider": "cpu",
            "sampleRateHz": SHERPA_SAMPLE_RATE,
            "windowSamples": 512,
            "numThreads": 1,
            "thresholdMillionths": 500_000,
            "minimumSpeechMilliseconds": 250,
            "minimumSilenceMilliseconds": 800,
            "maximumSpeechMilliseconds": 30_000,
            "bufferMilliseconds": 60_000,
        },
        "model": {
            "file": SHERPA_MODEL_FILENAME,
            "sizeBytes": SHERPA_MODEL_SIZE_BYTES,
            "sha256": SHERPA_MODEL_SHA256,
        },
        "audio": {
            "file": SHERPA_AUDIO_FILENAME,
            "encoding": "wav-pcm-s16le-mono",
            "generatorId": SHERPA_AUDIO_GENERATOR_ID,
            "sampleCount": SHERPA_AUDIO_SAMPLES,
            **audio_identity,
        },
        "observedSegments": [
            {
                "startSample": SHERPA_OBSERVED_START_SAMPLE,
                "sampleCount": SHERPA_OBSERVED_SAMPLE_COUNT,
            }
        ],
        "invariant": {
            "minimumSegments": 1,
            "maximumSegments": 1,
            "minimumTotalSegmentSamples": SHERPA_INVARIANT_MINIMUM_SAMPLES,
            "maximumTotalSegmentSamples": SHERPA_INVARIANT_MAXIMUM_SAMPLES,
            "maximumSegmentSamples": SHERPA_INVARIANT_MAXIMUM_SAMPLES,
        },
        "claimBoundary": (
            "The observed segment was generated on macOS arm64 with the pinned "
            "sherpa package and is only an invariant source. Android target "
            "execution remains required."
        ),
    }
    return (
        json.dumps(reference, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def generated_files() -> dict[str, bytes]:
    model = model_bytes()
    reference_input = reference_input_bytes()
    reference_output = reference_output_bytes()
    cancellation_input = cancellation_input_bytes()
    sherpa_audio = sherpa_audio_bytes()
    sherpa_reference = sherpa_reference_bytes(sherpa_audio)
    identities = {
        MODEL_FILENAME: _identity(model),
        REFERENCE_INPUT_FILENAME: _identity(reference_input),
        REFERENCE_OUTPUT_FILENAME: _identity(reference_output),
        CANCELLATION_INPUT_FILENAME: _identity(cancellation_input),
        SHERPA_AUDIO_FILENAME: _identity(sherpa_audio),
        SHERPA_REFERENCE_FILENAME: _identity(sherpa_reference),
    }
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "generatorVersion": GENERATOR_VERSION,
        "model": {
            "file": MODEL_FILENAME,
            "inputs": [MODEL_INPUT_NAME, MODEL_WEIGHT_NAME],
            "output": MODEL_OUTPUT_NAME,
            "matMulNodeCount": MATMUL_NODE_COUNT,
            **identities[MODEL_FILENAME],
        },
        "reference": {
            "dimension": REFERENCE_DIMENSION,
            "input": {
                "file": REFERENCE_INPUT_FILENAME,
                **identities[REFERENCE_INPUT_FILENAME],
            },
            "output": {
                "encoding": "float32-le",
                "file": REFERENCE_OUTPUT_FILENAME,
                **identities[REFERENCE_OUTPUT_FILENAME],
            },
        },
        "cancellation": {
            "dimension": CANCELLATION_DIMENSION,
            "modelFile": MODEL_FILENAME,
            "input": {
                "file": CANCELLATION_INPUT_FILENAME,
                **identities[CANCELLATION_INPUT_FILENAME],
            },
        },
        "inputFormat": {
            "magicAscii": "FONIXM1\\0",
            "schemaVersion": SCHEMA_VERSION,
            "byteOrder": "little-endian",
            "header": ["magic[8]", "schema", "rows", "inner", "columns"],
            "payload": ["leftFloat32", "rightFloat32"],
        },
        "sherpa": {
            "model": {
                "file": SHERPA_MODEL_FILENAME,
                "source": (
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                    "asr-models/silero_vad.int8.onnx"
                ),
                "license": "MIT",
                "sizeBytes": SHERPA_MODEL_SIZE_BYTES,
                "sha256": SHERPA_MODEL_SHA256,
            },
            "audio": {
                "file": SHERPA_AUDIO_FILENAME,
                "generatorId": SHERPA_AUDIO_GENERATOR_ID,
                **identities[SHERPA_AUDIO_FILENAME],
            },
            "reference": {
                "file": SHERPA_REFERENCE_FILENAME,
                **identities[SHERPA_REFERENCE_FILENAME],
            },
        },
        "claimBoundary": (
            "Deterministic CPU reference and active-termination fixture only; "
            "not a performance, thermal, or provider-qualification workload."
        ),
    }
    files = {
        MODEL_FILENAME: model,
        REFERENCE_INPUT_FILENAME: reference_input,
        REFERENCE_OUTPUT_FILENAME: reference_output,
        CANCELLATION_INPUT_FILENAME: cancellation_input,
        SHERPA_AUDIO_FILENAME: sherpa_audio,
        SHERPA_REFERENCE_FILENAME: sherpa_reference,
        MANIFEST_FILENAME: (
            json.dumps(
                manifest,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii"),
    }
    for name, contents in files.items():
        if not contents or len(contents) > MAX_GENERATED_FILE_BYTES:
            raise AssertionError(f"generated fixture {name} is outside its byte bound")
    return files


def _identity(contents: bytes) -> dict[str, int | str]:
    return {
        "sizeBytes": len(contents),
        "sha256": hashlib.sha256(contents).hexdigest(),
    }


def _validate_output_directory(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise FixtureGenerationError("output directory does not exist") from error
    if not stat.S_ISDIR(status.st_mode) or path.is_symlink():
        raise FixtureGenerationError("output path must be a regular directory")
    entries = list(path.iterdir())
    if len(entries) > MAX_OUTPUT_DIRECTORY_ENTRIES:
        raise FixtureGenerationError("output directory contains too many entries")
    allowed = set(generated_files())
    unexpected = sorted(entry.name for entry in entries if entry.name not in allowed)
    if unexpected:
        raise FixtureGenerationError("output directory contains unexpected entries")
    for entry in entries:
        status = entry.lstat()
        if not stat.S_ISREG(status.st_mode):
            raise FixtureGenerationError(
                "output directory contains a non-regular entry"
            )


def _write_exact(path: Path, contents: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_fixtures(output: Path) -> dict[str, dict[str, int | str]]:
    try:
        requested_status = output.lstat()
    except FileNotFoundError as error:
        raise FixtureGenerationError("output directory does not exist") from error
    if not stat.S_ISDIR(requested_status.st_mode) or output.is_symlink():
        raise FixtureGenerationError("output path must be a regular directory")
    output = output.resolve(strict=True)
    _validate_output_directory(output)
    files = generated_files()
    for name in sorted(files):
        _write_exact(output / name, files[name])
    return {name: _identity(files[name]) for name in sorted(files)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        identities = write_fixtures(arguments.output)
    except (FixtureGenerationError, OSError, ValueError) as error:
        print(
            f"Android sherpa Fonix fixture generation failed: {error}",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            identities,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
