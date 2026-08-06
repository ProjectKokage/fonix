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
