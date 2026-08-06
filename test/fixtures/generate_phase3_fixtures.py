#!/usr/bin/env python3
"""Generate and byte-check the tiny deterministic Phase-3 ONNX corpus.

This generator deliberately uses only Python's standard library.  The ONNX
messages below use the field numbers from ONNX's ``onnx-ml.proto``; keeping the
wire encoder here avoids making fixture reproduction depend on a particular
``onnx`` or protobuf wheel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = "phase3_manifest.json"
MODEL_DATA_SUFFIXES = frozenset({".onnx", ".bin"})
SEPARATELY_SOURCED_MODEL_DATA = {
    "mul_1.onnx": (
        130,
        "71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10",
    )
}
MAX_FIXTURE_INVENTORY_ENTRIES = 256
MAX_FIXTURE_INVENTORY_DEPTH = 8
MAX_FIXTURE_RELATIVE_PATH_BYTES = 1024
MAX_MODEL_DATA_FILES = 64

# TensorProto.DataType values used by this corpus.
FLOAT = 1
UINT8 = 2
INT8 = 3
UINT16 = 4
INT16 = 5
INT32 = 6
INT64 = 7
STRING = 8
BOOL = 9
FLOAT16 = 10
FLOAT64 = 11
UINT32 = 12
UINT64 = 13
BFLOAT16 = 16


def _varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _key(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _integer(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _varint(value)


def _bytes(field_number: int, value: bytes) -> bytes:
    return _key(field_number, 2) + _varint(len(value)) + value


def _string(field_number: int, value: str) -> bytes:
    return _bytes(field_number, value.encode("utf-8"))


def _message(field_number: int, value: bytes) -> bytes:
    return _bytes(field_number, value)


def _entry(key: str, value: str) -> bytes:
    # StringStringEntryProto
    return _string(1, key) + _string(2, value)


def _dimension(value: int | str) -> bytes:
    # TensorShapeProto.Dimension: dim_value = 1, dim_param = 2.
    return _integer(1, value) if isinstance(value, int) else _string(2, value)


def _tensor_type(element_type: int, shape: Iterable[int | str] | None) -> bytes:
    # TypeProto.tensor_type -> TypeProto.Tensor.
    tensor = _integer(1, element_type)
    if shape is not None:
        tensor_shape = b"".join(_message(1, _dimension(dim)) for dim in shape)
        tensor += _message(2, tensor_shape)
    return _message(1, tensor)


def _sequence_type(element_type: bytes) -> bytes:
    # TypeProto.sequence_type -> TypeProto.Sequence.elem_type.
    return _message(4, _message(1, element_type))


def _map_type(key_type: int, value_type: bytes) -> bytes:
    # TypeProto.map_type -> TypeProto.Map.
    return _message(5, _integer(1, key_type) + _message(2, value_type))


def _optional_type(element_type: bytes) -> bytes:
    # TypeProto.optional_type -> TypeProto.Optional.elem_type.
    return _message(9, _message(1, element_type))


def _value_info(
    name: str,
    value_type: bytes,
    *,
    doc: str = "",
    metadata: dict[str, str] | None = None,
) -> bytes:
    value = _string(1, name) + _message(2, value_type)
    if doc:
        value += _string(3, doc)
    for key, item in sorted((metadata or {}).items()):
        value += _message(4, _entry(key, item))
    return value


def _attribute_strings(name: str, values: Iterable[str]) -> bytes:
    # AttributeProto: name = 1, strings = 9, type = 20 (STRINGS = 8).
    value = _string(1, name)
    value += b"".join(_bytes(9, item.encode("utf-8")) for item in values)
    value += _integer(20, 8)
    return value


def _attribute_ints(name: str, values: Iterable[int]) -> bytes:
    # AttributeProto: name = 1, ints = 8, type = 20 (INTS = 7).
    value = _string(1, name)
    value += b"".join(_integer(8, item) for item in values)
    value += _integer(20, 7)
    return value


def _attribute_type_proto(name: str, value_type: bytes) -> bytes:
    # AttributeProto: name = 1, tp = 14, type = 20 (TYPE_PROTO = 13).
    return _string(1, name) + _message(14, value_type) + _integer(20, 13)


def _node(
    op_type: str,
    inputs: Iterable[str],
    outputs: Iterable[str],
    *,
    name: str,
    domain: str = "",
    attributes: Iterable[bytes] = (),
    doc: str = "",
) -> bytes:
    value = b"".join(_string(1, item) for item in inputs)
    value += b"".join(_string(2, item) for item in outputs)
    value += _string(3, name)
    value += _string(4, op_type)
    value += b"".join(_message(5, item) for item in attributes)
    if doc:
        value += _string(6, doc)
    if domain:
        value += _string(7, domain)
    return value


def _external_tensor(
    name: str,
    shape: Iterable[int],
    element_type: int,
    *,
    location: str,
    byte_length: int,
    sha1: str,
) -> bytes:
    value = b"".join(_integer(1, dim) for dim in shape)
    value += _integer(2, element_type)
    value += _string(8, name)
    for key, item in (
        ("location", location),
        ("offset", "0"),
        ("length", str(byte_length)),
        ("checksum", sha1),
    ):
        value += _message(13, _entry(key, item))
    value += _integer(14, 1)  # TensorProto.DataLocation.EXTERNAL
    return value


def _graph(
    *,
    name: str,
    nodes: Iterable[bytes],
    inputs: Iterable[bytes],
    outputs: Iterable[bytes],
    initializers: Iterable[bytes] = (),
    doc: str = "",
    metadata: dict[str, str] | None = None,
) -> bytes:
    value = b"".join(_message(1, node) for node in nodes)
    value += _string(2, name)
    value += b"".join(_message(5, tensor) for tensor in initializers)
    if doc:
        value += _string(10, doc)
    value += b"".join(_message(11, item) for item in inputs)
    value += b"".join(_message(12, item) for item in outputs)
    for key, item in sorted((metadata or {}).items()):
        value += _message(16, _entry(key, item))
    return value


def _opset(domain: str, version: int) -> bytes:
    value = _string(1, domain) if domain else b""
    return value + _integer(2, version)


def _model(
    graph: bytes,
    *,
    producer_name: str = "fonix-fixtures",
    producer_version: str = "phase3-v1",
    domain: str = "dev.fonix.fixtures",
    model_version: int = 1,
    doc: str = "Deterministic Fonix Phase-3 fixture.",
    opsets: Iterable[tuple[str, int]] = (("", 17),),
    metadata: dict[str, str] | None = None,
) -> bytes:
    value = _integer(1, 8)  # ONNX IR v8; supported by ORT 1.27.1.
    value += _string(2, producer_name)
    value += _string(3, producer_version)
    value += _string(4, domain)
    value += _integer(5, model_version)
    value += _string(6, doc)
    value += _message(7, graph)
    value += b"".join(_message(8, _opset(*item)) for item in opsets)
    for key, item in sorted((metadata or {}).items()):
        value += _message(14, _entry(key, item))
    return value


def _identity_model(
    *,
    graph_name: str,
    input_name: str,
    output_name: str,
    value_type: bytes,
    node_name: str,
) -> bytes:
    graph = _graph(
        name=graph_name,
        nodes=[
            _node(
                "Identity",
                [input_name],
                [output_name],
                name=node_name,
            )
        ],
        inputs=[_value_info(input_name, value_type)],
        outputs=[_value_info(output_name, value_type)],
    )
    return _model(graph)


def _build_files() -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    files: dict[str, bytes] = {}
    semantics: dict[str, dict[str, Any]] = {}

    files["string_identity.onnx"] = _identity_model(
        graph_name="string_identity",
        input_name="text",
        output_name="echo",
        value_type=_tensor_type(STRING, [4]),
        node_name="copy_strings",
    )
    semantics["string_identity.onnx"] = {
        "kind": "string_tensor_identity",
        "input": {"name": "text", "type": "tensor(string)", "shape": [4]},
        "output": {"name": "echo", "type": "tensor(string)", "shape": [4]},
        "contractCases": [
            "empty string",
            "ASCII",
            "UTF-8: こんにちは🌿",
            "4096-byte ASCII string generated at test time",
        ],
        "embeddedNulPolicy": "wrapper rejects before FFI",
    }

    float16_bits = [0x0000, 0x8000, 0x3C00, 0x0001, 0x0400, 0x7C00, 0x7E00]
    files["float16_identity.onnx"] = _identity_model(
        graph_name="float16_identity",
        input_name="float16_input",
        output_name="float16_output",
        value_type=_tensor_type(FLOAT16, [len(float16_bits)]),
        node_name="copy_float16_bits",
    )
    semantics["float16_identity.onnx"] = {
        "kind": "float16_raw_bits_identity",
        "input": {"name": "float16_input", "type": "tensor(float16)", "shape": [7]},
        "output": {"name": "float16_output", "type": "tensor(float16)", "shape": [7]},
        "rawBitsHex": [f"{value:04x}" for value in float16_bits],
        "cases": [
            "+0",
            "-0",
            "+1",
            "minimum positive subnormal",
            "minimum positive normal",
            "+infinity",
            "quiet NaN",
        ],
        "comparison": "exact uint16 bit equality",
    }

    bfloat16_bits = [0x0000, 0x8000, 0x3F80, 0x0001, 0x0080, 0x7F80, 0x7FC0]
    files["bfloat16_identity.onnx"] = _identity_model(
        graph_name="bfloat16_identity",
        input_name="bfloat16_input",
        output_name="bfloat16_output",
        value_type=_tensor_type(BFLOAT16, [len(bfloat16_bits)]),
        node_name="copy_bfloat16_bits",
    )
    semantics["bfloat16_identity.onnx"] = {
        "kind": "bfloat16_raw_bits_identity",
        "input": {"name": "bfloat16_input", "type": "tensor(bfloat16)", "shape": [7]},
        "output": {"name": "bfloat16_output", "type": "tensor(bfloat16)", "shape": [7]},
        "rawBitsHex": [f"{value:04x}" for value in bfloat16_bits],
        "cases": [
            "+0",
            "-0",
            "+1",
            "minimum positive subnormal",
            "minimum positive normal",
            "+infinity",
            "quiet NaN",
        ],
        "comparison": "exact uint16 bit equality",
    }

    files["scalar_float64_identity.onnx"] = _identity_model(
        graph_name="scalar_float64_identity",
        input_name="scalar.input",
        output_name="scalar/output",
        value_type=_tensor_type(FLOAT64, []),
        node_name="copy_scalar_float64",
    )
    semantics["scalar_float64_identity.onnx"] = {
        "kind": "rank_zero_float64_identity",
        "input": {"name": "scalar.input", "type": "tensor(float64)", "shape": []},
        "output": {"name": "scalar/output", "type": "tensor(float64)", "shape": []},
        "inputValue": -3.25,
        "expectedOutput": -3.25,
        "comparison": "exact binary64 equality",
    }

    dynamic_tensor = _tensor_type(FLOAT, ["rows", "columns"])
    dynamic_add_graph = _graph(
        name="dynamic_add",
        nodes=[
            _node(
                "Add",
                ["lhs.matrix", "rhs/matrix"],
                ["sum.matrix"],
                name="add_dynamic_matrices",
            )
        ],
        inputs=[
            _value_info("lhs.matrix", dynamic_tensor),
            _value_info("rhs/matrix", dynamic_tensor),
        ],
        outputs=[_value_info("sum.matrix", dynamic_tensor)],
    )
    files["dynamic_add.onnx"] = _model(dynamic_add_graph)
    semantics["dynamic_add.onnx"] = {
        "kind": "dynamic_shape_float32_add",
        "inputs": [
            {
                "name": "lhs.matrix",
                "type": "tensor(float32)",
                "shape": [None, None],
                "symbols": ["rows", "columns"],
            },
            {
                "name": "rhs/matrix",
                "type": "tensor(float32)",
                "shape": [None, None],
                "symbols": ["rows", "columns"],
            },
        ],
        "output": {
            "name": "sum.matrix",
            "type": "tensor(float32)",
            "shape": [None, None],
            "symbols": ["rows", "columns"],
        },
        "referenceCases": [
            {
                "shape": [2, 3],
                "lhs": [1.0, -2.0, 3.0, 4.5, -5.5, 6.0],
                "rhs": [10.0, 20.0, -30.0, 0.5, 5.5, -6.0],
                "expectedOutput": [11.0, 18.0, -27.0, 5.0, 0.0, 0.0],
            },
            {
                "shape": [1, 4],
                "lhs": [-1.0, 0.25, 8.0, 100.0],
                "rhs": [1.0, 0.75, -3.0, -25.0],
                "expectedOutput": [0.0, 1.0, 5.0, 75.0],
            },
        ],
        "comparison": "exact values chosen for binary32 addition",
    }

    dynamic_matmul_graph = _graph(
        name="dynamic_matmul",
        nodes=[
            _node(
                "MatMul",
                ["left.matrix", "right/matrix"],
                ["product.matrix"],
                name="multiply_dynamic_matrices",
            )
        ],
        inputs=[
            _value_info(
                "left.matrix",
                _tensor_type(FLOAT, ["rows", "inner"]),
            ),
            _value_info(
                "right/matrix",
                _tensor_type(FLOAT, ["inner", "columns"]),
            ),
        ],
        outputs=[
            _value_info(
                "product.matrix",
                _tensor_type(FLOAT, ["rows", "columns"]),
            )
        ],
    )
    files["dynamic_matmul.onnx"] = _model(dynamic_matmul_graph)
    semantics["dynamic_matmul.onnx"] = {
        "kind": "dynamic_shape_float32_matmul",
        "inputs": [
            {
                "name": "left.matrix",
                "type": "tensor(float32)",
                "shape": [None, None],
                "symbols": ["rows", "inner"],
            },
            {
                "name": "right/matrix",
                "type": "tensor(float32)",
                "shape": [None, None],
                "symbols": ["inner", "columns"],
            },
        ],
        "output": {
            "name": "product.matrix",
            "type": "tensor(float32)",
            "shape": [None, None],
            "symbols": ["rows", "columns"],
        },
        "referenceCases": [
            {
                "leftShape": [2, 3],
                "rightShape": [3, 2],
                "outputShape": [2, 2],
                "left": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "right": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
                "expectedOutput": [58.0, 64.0, 139.0, 154.0],
            },
            {
                "leftShape": [1, 2],
                "rightShape": [2, 3],
                "outputShape": [1, 3],
                "left": [-1.0, 2.0],
                "right": [3.0, 4.0, 5.0, -6.0, 7.0, 8.0],
                "expectedOutput": [-15.0, 10.0, 11.0],
            },
        ],
        "comparison": "exact values chosen for binary32 multiplication and accumulation",
    }

    files["zero_length_float32_identity.onnx"] = _identity_model(
        graph_name="zero_length_float32_identity",
        input_name="empty/input",
        output_name="empty.output",
        value_type=_tensor_type(FLOAT, [0, 3]),
        node_name="copy_zero_length_tensor",
    )
    semantics["zero_length_float32_identity.onnx"] = {
        "kind": "zero_length_float32_identity",
        "input": {
            "name": "empty/input",
            "type": "tensor(float32)",
            "shape": [0, 3],
            "values": [],
        },
        "output": {
            "name": "empty.output",
            "type": "tensor(float32)",
            "shape": [0, 3],
            "expectedValues": [],
        },
        "expectedElementCount": 0,
    }

    files["bool_identity.onnx"] = _identity_model(
        graph_name="bool_identity",
        input_name="flags/input",
        output_name="flags.normalized",
        value_type=_tensor_type(BOOL, [6]),
        node_name="copy_normalized_bools",
    )
    semantics["bool_identity.onnx"] = {
        "kind": "normalized_bool_identity",
        "input": {
            "name": "flags/input",
            "type": "tensor(bool)",
            "shape": [6],
            "values": [False, True, True, False, True, False],
        },
        "output": {
            "name": "flags.normalized",
            "type": "tensor(bool)",
            "shape": [6],
            "expectedValues": [False, True, True, False, True, False],
        },
        "storageContract": "each wrapper byte and returned byte is exactly 0 or 1",
    }

    integer_specs = [
        (
            "int8",
            INT8,
            "signed/int8.input",
            "signed/int8.output",
            [-128, -1, 0, 127],
        ),
        (
            "uint8",
            UINT8,
            "unsigned/uint8.input",
            "unsigned/uint8.output",
            [0, 1, 254, 255],
        ),
        (
            "int16",
            INT16,
            "signed/int16.input",
            "signed/int16.output",
            [-32768, -1, 0, 32767],
        ),
        (
            "uint16",
            UINT16,
            "unsigned/uint16.input",
            "unsigned/uint16.output",
            [0, 1, 65534, 65535],
        ),
        (
            "int32",
            INT32,
            "signed/int32.input",
            "signed/int32.output",
            [-2147483648, -1, 0, 2147483647],
        ),
        (
            "uint32",
            UINT32,
            "unsigned/uint32.input",
            "unsigned/uint32.output",
            [0, 1, 4294967294, 4294967295],
        ),
        (
            "int64",
            INT64,
            "signed/int64.input",
            "signed/int64.output",
            [-9223372036854775808, -1, 0, 9223372036854775807],
        ),
        (
            "uint64",
            UINT64,
            "unsigned/uint64.input",
            "unsigned/uint64.output",
            [0, 1, 4294967296, 9223372036854775807],
        ),
    ]
    integer_graph = _graph(
        name="fixed_width_integer_identities",
        nodes=[
            _node(
                "Identity",
                [input_name],
                [output_name],
                name=f"copy_{type_name}",
            )
            for type_name, _, input_name, output_name, _ in integer_specs
        ],
        inputs=[
            _value_info(input_name, _tensor_type(element_type, [4]))
            for _, element_type, input_name, _, _ in integer_specs
        ],
        outputs=[
            _value_info(output_name, _tensor_type(element_type, [4]))
            for _, element_type, _, output_name, _ in integer_specs
        ],
    )
    files["fixed_width_integer_identities.onnx"] = _model(integer_graph)
    semantics["fixed_width_integer_identities.onnx"] = {
        "kind": "multiple_named_fixed_width_integer_identities",
        "inputs": [
            {
                "name": input_name,
                "type": f"tensor({type_name})",
                "shape": [4],
                "values": list(values),
            }
            for type_name, _, input_name, _, values in integer_specs
        ],
        "outputs": [
            {
                "name": output_name,
                "type": f"tensor({type_name})",
                "shape": [4],
                "expectedValues": list(values),
            }
            for type_name, _, _, output_name, values in integer_specs
        ],
        "runOutputOrder": [
            output_name
            for _, _, _, output_name, _ in reversed(integer_specs)
        ],
        "comparison": "exact fixed-width byte equality",
    }

    metadata_tensor = _tensor_type(FLOAT, ["batch", 2])
    metadata_graph = _graph(
        name="メタデータグラフ",
        nodes=[
            _node(
                "Identity",
                ["入力"],
                ["出力"],
                name="metadata_identity",
                doc="Copies the tensor without changing it.",
            )
        ],
        inputs=[
            _value_info(
                "入力",
                metadata_tensor,
                doc="UTF-8 input; first dimension is symbolic.",
                metadata={"role": "source"},
            )
        ],
        outputs=[
            _value_info(
                "出力",
                metadata_tensor,
                doc="UTF-8 output; copied from 入力.",
                metadata={"role": "result"},
            )
        ],
        doc="Graph description with UTF-8: 木陰.",
        metadata={"graph-key": "graph-value"},
    )
    metadata_values = {
        "empty": "",
        "purpose": "model metadata contract",
        "unicode": "こんにちは🌿",
    }
    files["metadata_identity.onnx"] = _model(
        metadata_graph,
        producer_name="fonix-fixtures",
        producer_version="phase3-v1",
        domain="dev.fonix.fixtures.metadata",
        model_version=42,
        doc="Model description with UTF-8: 木陰 and café.",
        metadata=metadata_values,
    )
    semantics["metadata_identity.onnx"] = {
        "kind": "complete_model_metadata",
        "input": {
            "name": "入力",
            "type": "tensor(float32)",
            "shape": [None, 2],
            "symbols": ["batch", None],
        },
        "output": {
            "name": "出力",
            "type": "tensor(float32)",
            "shape": [None, 2],
            "symbols": ["batch", None],
        },
        "producerName": "fonix-fixtures",
        "producerVersion": "phase3-v1",
        "domain": "dev.fonix.fixtures.metadata",
        "modelVersion": 42,
        "graphName": "メタデータグラフ",
        "modelDescription": "Model description with UTF-8: 木陰 and café.",
        "graphDescription": "Graph description with UTF-8: 木陰.",
        "customMetadata": metadata_values,
    }

    unknown_rank_tensor = _tensor_type(FLOAT, None)
    files["unknown_rank_identity.onnx"] = _identity_model(
        graph_name="unknown_rank_identity",
        input_name="unknown_input",
        output_name="unknown_output",
        value_type=unknown_rank_tensor,
        node_name="copy_unknown_rank",
    )
    semantics["unknown_rank_identity.onnx"] = {
        "kind": "unknown_rank_tensor_identity",
        "input": {
            "name": "unknown_input",
            "type": "tensor(float32)",
            "hasShape": False,
            "shape": None,
        },
        "output": {
            "name": "unknown_output",
            "type": "tensor(float32)",
            "hasShape": False,
            "shape": None,
        },
        "referenceCases": [
            {"shape": [3], "values": [1.0, 2.0, 3.0]},
            {"shape": [2, 2], "values": [4.0, 5.0, 6.0, 7.0]},
        ],
    }

    tensor_2 = _tensor_type(FLOAT, [2])
    sequence_graph = _graph(
        name="sequence_construct",
        nodes=[
            _node(
                "SequenceConstruct",
                ["first", "second"],
                ["values"],
                name="make_sequence",
            )
        ],
        inputs=[
            _value_info("first", tensor_2),
            _value_info("second", tensor_2),
        ],
        outputs=[_value_info("values", _sequence_type(tensor_2))],
    )
    files["sequence_construct.onnx"] = _model(sequence_graph)
    semantics["sequence_construct.onnx"] = {
        "kind": "sequence_of_tensors",
        "inputs": [
            {"name": "first", "type": "tensor(float32)", "shape": [2]},
            {"name": "second", "type": "tensor(float32)", "shape": [2]},
        ],
        "output": {"name": "values", "type": "sequence<tensor(float32,[2])>"},
        "reference": {"first": [1.0, 2.0], "second": [3.0, 4.0]},
    }

    optional_graph = _graph(
        name="optional_tensor",
        nodes=[
            _node(
                "Optional",
                ["value"],
                ["maybe_value"],
                name="wrap_optional",
            )
        ],
        inputs=[_value_info("value", tensor_2)],
        outputs=[_value_info("maybe_value", _optional_type(tensor_2))],
    )
    files["optional_tensor.onnx"] = _model(optional_graph)
    semantics["optional_tensor.onnx"] = {
        "kind": "optional_tensor",
        "input": {"name": "value", "type": "tensor(float32)", "shape": [2]},
        "output": {"name": "maybe_value", "type": "optional<tensor(float32,[2])>"},
        "reference": {"value": [5.0, 6.0], "present": True},
    }

    optional_empty_graph = _graph(
        name="optional_empty_tensor",
        nodes=[
            _node(
                "Optional",
                [],
                ["maybe_value"],
                name="make_empty_optional",
                attributes=[_attribute_type_proto("type", tensor_2)],
            )
        ],
        inputs=[],
        outputs=[_value_info("maybe_value", _optional_type(tensor_2))],
    )
    files["optional_empty_tensor.onnx"] = _model(optional_empty_graph)
    semantics["optional_empty_tensor.onnx"] = {
        "kind": "empty_optional_tensor",
        "inputs": [],
        "output": {"name": "maybe_value", "type": "optional<tensor(float32,[2])>"},
        "reference": {"present": False},
    }

    optional_input_graph = _graph(
        name="optional_has_element",
        nodes=[
            _node(
                "OptionalHasElement",
                ["maybe_value"],
                ["has_value"],
                name="test_optional_presence",
            )
        ],
        inputs=[_value_info("maybe_value", _optional_type(tensor_2))],
        outputs=[_value_info("has_value", _tensor_type(BOOL, []))],
    )
    files["optional_has_element.onnx"] = _model(optional_input_graph)
    semantics["optional_has_element.onnx"] = {
        "kind": "optional_input_presence",
        "input": {"name": "maybe_value", "type": "optional<tensor(float32,[2])>"},
        "output": {"name": "has_value", "type": "tensor(bool)", "shape": []},
        "referenceCases": [
            {"input": "present tensor [5.0,6.0]", "hasValue": True},
            {"input": "omitted optional input", "hasValue": False},
        ],
        "api27PresentRepresentation": "supply the contained tensor OrtValue",
        "api27NoneRepresentation": "omit the optional graph input from Run",
    }

    zipmap_output = _sequence_type(_map_type(STRING, _tensor_type(FLOAT, None)))
    zipmap_graph = _graph(
        name="zipmap_string",
        nodes=[
            _node(
                "ZipMap",
                ["probabilities"],
                ["scores"],
                name="zip_labels",
                domain="ai.onnx.ml",
                attributes=[
                    _attribute_strings("classlabels_strings", ["cat", "dog"])
                ],
            )
        ],
        inputs=[_value_info("probabilities", _tensor_type(FLOAT, ["batch", 2]))],
        outputs=[_value_info("scores", zipmap_output)],
    )
    files["zipmap_string.onnx"] = _model(
        zipmap_graph,
        opsets=(("", 17), ("ai.onnx.ml", 3)),
    )
    semantics["zipmap_string.onnx"] = {
        "kind": "onnx_ml_sequence_map",
        "input": {
            "name": "probabilities",
            "type": "tensor(float32)",
            "shape": [None, 2],
        },
        "output": {
            "name": "scores",
            "type": "sequence<map<string,tensor(float32)>>",
        },
        "labels": ["cat", "dog"],
        "reference": {"probabilities": [[0.25, 0.75]], "scores": [{"cat": 0.25, "dog": 0.75}]},
    }

    zipmap_int64_output = _sequence_type(_map_type(INT64, _tensor_type(FLOAT, None)))
    zipmap_int64_graph = _graph(
        name="zipmap_int64",
        nodes=[
            _node(
                "ZipMap",
                ["probabilities"],
                ["scores"],
                name="zip_labels",
                domain="ai.onnx.ml",
                attributes=[_attribute_ints("classlabels_int64s", [10, 20])],
            )
        ],
        inputs=[_value_info("probabilities", _tensor_type(FLOAT, ["batch", 2]))],
        outputs=[_value_info("scores", zipmap_int64_output)],
    )
    files["zipmap_int64.onnx"] = _model(
        zipmap_int64_graph,
        opsets=(("", 17), ("ai.onnx.ml", 3)),
    )
    semantics["zipmap_int64.onnx"] = {
        "kind": "onnx_ml_sequence_map",
        "input": {
            "name": "probabilities",
            "type": "tensor(float32)",
            "shape": [None, 2],
        },
        "output": {
            "name": "scores",
            "type": "sequence<map<int64,tensor(float32)>>",
        },
        "labels": [10, 20],
        "reference": {
            "probabilities": [[0.125, 0.875]],
            "scores": [{"10": 0.125, "20": 0.875}],
        },
    }

    valid_weights = struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)
    outside_weights = struct.pack("<4f", 9.0, 9.0, 9.0, 9.0)
    files["external_data/valid/weights.bin"] = valid_weights
    files["external_data/outside.bin"] = outside_weights
    semantics["external_data/valid/weights.bin"] = {
        "kind": "external_tensor_bytes",
        "elementType": "float32",
        "shape": [4],
        "values": [1.0, 2.0, 3.0, 4.0],
    }
    semantics["external_data/outside.bin"] = {
        "kind": "path_escape_sentinel",
        "elementType": "float32",
        "shape": [4],
        "values": [9.0, 9.0, 9.0, 9.0],
    }

    def external_model(location: str, payload: bytes) -> bytes:
        tensor = _external_tensor(
            "weight",
            [4],
            FLOAT,
            location=location,
            byte_length=len(payload),
            sha1=hashlib.sha1(payload).hexdigest(),
        )
        graph = _graph(
            name="external_data_mul",
            nodes=[
                _node(
                    "Mul",
                    ["input", "weight"],
                    ["output"],
                    name="multiply_external",
                )
            ],
            inputs=[_value_info("input", _tensor_type(FLOAT, [4]))],
            outputs=[_value_info("output", _tensor_type(FLOAT, [4]))],
            initializers=[tensor],
        )
        return _model(graph)

    files["external_data/valid/model.onnx"] = external_model(
        "weights.bin", valid_weights
    )
    semantics["external_data/valid/model.onnx"] = {
        "kind": "external_data_model",
        "externalLocation": "weights.bin",
        "sandboxPolicy": "accept when allowed root is external_data/valid",
        "input": [10.0, 10.0, 10.0, 10.0],
        "expectedOutput": [10.0, 20.0, 30.0, 40.0],
    }

    files["external_data/escape/model.onnx"] = external_model(
        "../outside.bin", outside_weights
    )
    semantics["external_data/escape/model.onnx"] = {
        "kind": "external_data_path_escape",
        "externalLocation": "../outside.bin",
        "sandboxPolicy": "reject before ORT even though the target exists",
        "allowedRoot": "external_data/escape",
        "escapedTarget": "external_data/outside.bin",
    }

    files["external_data/missing/model.onnx"] = external_model(
        "missing.bin", valid_weights
    )
    semantics["external_data/missing/model.onnx"] = {
        "kind": "external_data_missing_file",
        "externalLocation": "missing.bin",
        "sandboxPolicy": "fail closed without revealing unrelated paths",
    }

    return files, semantics


def _normalized_relative_target(model_directory: str, location: str) -> str:
    parts = [part for part in Path(model_directory).parts if part not in ("", ".")]
    for part in Path(location).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("external-data location escapes the fixture root")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _validate_external_data_contract(files: dict[str, bytes]) -> None:
    valid_target = _normalized_relative_target("external_data/valid", "weights.bin")
    escape_target = _normalized_relative_target("external_data/escape", "../outside.bin")
    missing_target = _normalized_relative_target("external_data/missing", "missing.bin")
    if valid_target != "external_data/valid/weights.bin" or valid_target not in files:
        raise AssertionError("valid external-data target is not contained beside its model")
    if escape_target != "external_data/outside.bin" or escape_target not in files:
        raise AssertionError("escape fixture does not resolve to its outside sentinel")
    if escape_target.startswith("external_data/escape/"):
        raise AssertionError("escape fixture unexpectedly remains inside its allowed root")
    if missing_target in files:
        raise AssertionError("missing external-data fixture unexpectedly has a target")


def _manifest(files: dict[str, bytes], semantics: dict[str, dict[str, Any]]) -> bytes:
    entries = []
    for path, contents in sorted(files.items()):
        entry: dict[str, Any] = {
            "path": path,
            "size": len(contents),
            "sha256": hashlib.sha256(contents).hexdigest(),
        }
        entry.update(semantics[path])
        entries.append(entry)
    manifest = {
        "schema": 1,
        "generator": "generate_phase3_fixtures.py",
        "onnxIrVersion": 8,
        "defaultOpset": 17,
        "fixtures": entries,
    }
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _fixture_inventory_failures(
    root: Path,
    generated_paths: Iterable[str],
) -> list[str]:
    failures: list[str] = []
    generated = set(generated_paths)
    allowed = generated | set(SEPARATELY_SOURCED_MODEL_DATA)
    if len(allowed) > MAX_MODEL_DATA_FILES:
        return [
            "configured model/data inventory exceeds "
            f"{MAX_MODEL_DATA_FILES} files"
        ]
    for relative in sorted(allowed):
        candidate = Path(relative)
        try:
            encoded_length = len(relative.encode("utf-8"))
        except UnicodeEncodeError:
            failures.append(f"configured model/data path is not UTF-8: {relative!r}")
            continue
        if (
            candidate.is_absolute()
            or not candidate.parts
            or any(part in ("", ".", "..") for part in candidate.parts)
            or candidate.suffix.lower() not in MODEL_DATA_SUFFIXES
            or encoded_length > MAX_FIXTURE_RELATIVE_PATH_BYTES
        ):
            failures.append(f"invalid configured model/data path: {relative}")
    if failures:
        return failures

    try:
        root_mode = root.lstat().st_mode
    except OSError as error:
        return [f"could not inspect fixture root: {error}"]
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        return ["fixture root must be a non-symlink directory"]

    discovered: set[str] = set()
    pending: list[tuple[Path, int]] = [(root, 0)]
    entry_count = 0
    while pending:
        directory, depth = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as error:
            failures.append(
                f"could not enumerate {directory.relative_to(root).as_posix() or '.'}: "
                f"{error}"
            )
            continue
        for path in entries:
            entry_count += 1
            if entry_count > MAX_FIXTURE_INVENTORY_ENTRIES:
                failures.append(
                    "fixture inventory exceeds "
                    f"{MAX_FIXTURE_INVENTORY_ENTRIES} entries"
                )
                return failures
            relative = path.relative_to(root).as_posix()
            try:
                relative_length = len(relative.encode("utf-8"))
            except UnicodeEncodeError:
                failures.append(f"fixture path is not UTF-8: {relative!r}")
                continue
            if relative_length > MAX_FIXTURE_RELATIVE_PATH_BYTES:
                failures.append(
                    "fixture path exceeds "
                    f"{MAX_FIXTURE_RELATIVE_PATH_BYTES} UTF-8 bytes: {relative}"
                )
                continue
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                failures.append(f"could not inspect fixture entry {relative}: {error}")
                continue
            is_model_data = path.suffix.lower() in MODEL_DATA_SUFFIXES
            if stat.S_ISLNK(mode):
                failures.append(f"fixture symlink is forbidden: {relative}")
                continue
            if stat.S_ISDIR(mode):
                if is_model_data:
                    failures.append(
                        f"model/data path must be a regular file: {relative}"
                    )
                    continue
                child_depth = depth + 1
                if child_depth > MAX_FIXTURE_INVENTORY_DEPTH:
                    failures.append(
                        "fixture directory depth exceeds "
                        f"{MAX_FIXTURE_INVENTORY_DEPTH}: {relative}"
                    )
                    continue
                pending.append((path, child_depth))
                continue
            if not stat.S_ISREG(mode):
                failures.append(f"fixture entry must be regular: {relative}")
                continue
            if is_model_data:
                discovered.add(relative)
                if len(discovered) > MAX_MODEL_DATA_FILES:
                    failures.append(
                        "fixture model/data inventory exceeds "
                        f"{MAX_MODEL_DATA_FILES} files"
                    )
                    return failures

    for relative in sorted(discovered - allowed):
        failures.append(f"unexpected model/data file: {relative}")
    for relative in sorted(allowed - discovered):
        failures.append(f"missing model/data file: {relative}")
    return failures


def _read_exact_regular_file(
    path: Path,
    *,
    expected_size: int,
    label: str,
) -> tuple[bytes | None, str | None]:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None, f"missing: {label}"
    except OSError as error:
        return None, f"could not inspect {label}: {error}"
    if not stat.S_ISREG(before.st_mode):
        return None, f"not a regular file: {label}"
    if before.st_size != expected_size:
        return None, f"size mismatch: {label}"
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size != expected_size
            ):
                return None, f"file changed while opening: {label}"
            contents = stream.read(expected_size + 1)
            after = os.fstat(stream.fileno())
    except OSError as error:
        return None, f"could not read {label}: {error}"
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        or len(contents) != expected_size
    ):
        return None, f"file changed while reading: {label}"
    return contents, None


def _check(files: dict[str, bytes], manifest: bytes, *, root: Path = ROOT) -> int:
    expected = dict(files)
    expected[MANIFEST_PATH] = manifest
    failures = _fixture_inventory_failures(root, files)
    for relative, contents in sorted(expected.items()):
        actual, failure = _read_exact_regular_file(
            root / relative,
            expected_size=len(contents),
            label=relative,
        )
        if failure is not None:
            failures.append(failure)
        elif actual != contents:
            failures.append(f"byte mismatch: {relative}")
    for relative, (size, sha256) in sorted(SEPARATELY_SOURCED_MODEL_DATA.items()):
        contents, failure = _read_exact_regular_file(
            root / relative,
            expected_size=size,
            label=relative,
        )
        if failure is not None:
            failures.append(failure)
        elif hashlib.sha256(contents).hexdigest() != sha256:
            failures.append(f"SHA-256 mismatch: {relative}")
    if failures:
        print("Phase-3 fixture verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        print(
            "Run generate_phase3_fixtures.py --write after reviewing the change.",
            file=sys.stderr,
        )
        return 1
    print(
        f"verified {len(expected)} deterministic Phase-3 fixture files and "
        f"{len(files) + len(SEPARATELY_SOURCED_MODEL_DATA)} closed model/data paths"
    )
    return 0


def _write(files: dict[str, bytes], manifest: bytes) -> None:
    for relative, contents in sorted(files.items()):
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    (ROOT / MANIFEST_PATH).write_bytes(manifest)
    print(f"wrote {len(files) + 1} deterministic Phase-3 fixture files")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="compare committed bytes")
    mode.add_argument("--write", action="store_true", help="regenerate committed bytes")
    args = parser.parse_args()

    files, semantics = _build_files()
    _validate_external_data_contract(files)
    manifest = _manifest(files, semantics)
    if args.write:
        _write(files, manifest)
        return 0
    return _check(files, manifest)


if __name__ == "__main__":
    raise SystemExit(main())
