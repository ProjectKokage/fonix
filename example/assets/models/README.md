# Reference model

`mul_1.onnx` is the 130-byte element-wise multiplication model from the
ONNX Runtime `v1.27.1` test corpus. It accepts one float32 tensor named `X`
with shape `[3, 2]`, multiplies it by the initializer
`[1, 2, 3, 4, 5, 6]`, and returns `Y` with the same shape.

The committed bytes are identical to `test/fixtures/mul_1.onnx`:

- source: `onnxruntime/test/testdata/mul_1.onnx`
- source revision: `v1.27.1`
- size: 130 bytes
- SHA-256: `71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10`
- license: MIT; see `../../../../third_party/onnxruntime/LICENSE`

This is an API and packaging smoke fixture. It is not a representative model,
a performance workload, or evidence for an untested execution provider.

## CPU benchmark MatMul fixture

`cpu_benchmark_matmul.onnx` is a repository-authored, one-node, static-weight
float32 `MatMul` workload intended for deterministic CPU timing measurements on
mobile and desktop targets. It uses ONNX IR 8 and default-domain opset 17. The
closed graph contract is:

- input `input`: float32 `[2048, 1024]`;
- initializer `weight`: float32 `[1024, 1024]`, embedded as ONNX raw data; and
- output `output`: float32 `[2048, 1024]`.

One inference performs 2,147,483,648 multiply-accumulates, or 4,294,967,296
floating-point operations under the documented two-operations-per-MAC
convention. These dimensions are only a practical calibration starting point.
They make no latency, throughput, device-class, real-time, or portability
claim.

The runtime input and independently computed reference output are committed as
little-endian IEEE-754 binary32 files:

| File | Size | SHA-256 |
| --- | ---: | --- |
| `cpu_benchmark_matmul.onnx` | 4,194,629 bytes | `19bc0466ef8627df9764b40d947ff2c7cfa978c7daa6952ca9553c700a6dbcf0` |
| `cpu_benchmark_matmul.input.f32le` | 8,388,608 bytes | `2025466d19e8aa6a9820266d0622d7b154059b61a1051b9edf1d020bf127c36a` |
| `cpu_benchmark_matmul.output.f32le` | 8,388,608 bytes | `c79ff7588eadd3d82ba4a5028955ed02b98a72b11da828131b33075c781a40fb` |
| `cpu_benchmark_matmul.json` | 3,481 bytes | `7c089a5a6c6cd444eb802bb0066a2fae054e54a1b924cffac7c53b80bd9c7c8a` |

The standard-library-only generator uses closed 64-value integer patterns for
the input and initializer. It computes the complete fundamental output tile by
integer MatMul over expanded values, then independently verifies every tile
value using periodic IEEE-754 `math.fsum` correlation before publishing the
reference bytes. Every operand, product, partial sum, and output is an exact
integer with magnitude below `2^24`, so the reference policy is exact float32
bit equality with zero absolute and relative tolerance; NaN and infinity are
forbidden. The exact algorithms, seeds, strides, shapes, file identities,
generator identity, and claim boundary are frozen in
`cpu_benchmark_matmul.json`.

The generator itself is 20,969 bytes with SHA-256
`5730c2193acc8bd9fb3fd52fb9c973d46a512e2bd56c4d815caca49fe4e16ec2`.

```sh
python3 -B example/assets/models/generate_cpu_benchmark_matmul.py --check
python3 -B -m unittest tool.tests.test_cpu_benchmark_fixture_generator -v
```

Use `--write` only after reviewing an intended workload-contract change, then
run `--check`. This fixture can support measurement receipt generation only.
It is not by itself a performance baseline, regression threshold, support or
provider-qualification claim, or release approval.

## XNNPACK static MatMul fixture

`xnnpack_matmul.onnx` is a repository-authored, one-node floating-point
`MatMul` model for strict Android XNNPACK assignment and CPU-parity checks. It
uses ONNX IR 8 and the default-domain opset 17. The graph multiplies one
runtime input with shape `[3, 2]` by one constant initializer with shape
`[2, 2]` and returns an output with shape `[3, 2]`:

- input: `[1, 2, 3, 4, 5, 6]`
- weights: `[1, 2, 3, 4]`
- exact float32 output: `[7, 10, 15, 22, 23, 34]`
- model size: 311 bytes
- model SHA-256:
  `c75aaa93b0e1ae09e0bb12ddee5786c2fda803dfa5f565234e2b65e238623482`

Every input, weight, accumulation result, and output is an integer exactly
representable as float32. The generator independently computes the matrix
product before emitting either file.

The exact ORT 1.27.1 XNNPACK implementation accepts a rank-two `MatMul` with a
constant right-hand initializer directly in the ONNX domain. A boundary
`Conv` is deliberately not used for this full-assignment checkpoint: ORT
converts it to the internal NHWC domain and inserts CPU `Transpose` nodes
before and after it. The target diagnostic observed the resulting exact
`{xnnpack: 1, cpu: 2}` map, which correctly fails Fonix's strict no-fallback
policy.

`xnnpack_matmul.json` is the closed canonical contract for the graph, tensor
names, types, shapes, values, model identity, and claim boundary:

- metadata size: 1,298 bytes
- metadata SHA-256:
  `76eb202b02211f34ee64ca6a88a2a24d9f58f334136fa7f1b7fcfe2e763f30ab`

Both files are reproduced using only Python's standard library. The generator
itself has SHA-256
`7b6139d13491d91b43430c9628e22fbb0085996ac8c003156d45e2fb19756029`.

```sh
python3 -B example/assets/models/generate_xnnpack_matmul.py --check
python3 -B -m unittest tool.tests.test_xnnpack_fixture_generator -v
```

Use `--write` only after reviewing an intended model-contract change, then run
`--check`. The verifier requires exact regular non-link bytes. This fixture can
support a functional assignment/parity receipt, but it is not a representative
convolutional workload, benchmark, thermal test, physical-device result,
provider qualification, platform qualification, or release approval.
