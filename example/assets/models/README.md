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
