/// Tensor element types in the ONNX Runtime C API 27 contract.
enum OrtTensorElementType {
  undefined(0, null, false),
  float32(1, 4, true),
  uint8(2, 1, true),
  int8(3, 1, true),
  uint16(4, 2, true),
  int16(5, 2, true),
  int32(6, 4, true),
  int64(7, 8, true),
  string(8, null, false),
  boolean(9, 1, true),
  float16(10, 2, true),
  float64(11, 8, true),
  uint32(12, 4, true),
  uint64(13, 8, true),
  complex64(14, 8, false),
  complex128(15, 16, false),
  bfloat16(16, 2, true),
  float8E4M3FN(17, 1, false),
  float8E4M3FNUZ(18, 1, false),
  float8E5M2(19, 1, false),
  float8E5M2FNUZ(20, 1, false),
  uint4(21, null, false),
  int4(22, null, false),
  float4E2M1(23, null, false),
  uint2(24, null, false),
  int2(25, null, false),
  float8E8M0(26, 1, false);

  const OrtTensorElementType(
    this.nativeValue,
    this.fixedStorageBytes,
    this.supportsDenseCreation,
  );

  final int nativeValue;

  /// Bytes per element, or null for string/sub-byte/undefined storage.
  final int? fixedStorageBytes;

  /// Whether Fonix can create this type through its safe dense API.
  final bool supportsDenseCreation;

  static OrtTensorElementType fromNativeValue(int value) {
    for (final type in values) {
      if (type.nativeValue == value) return type;
    }
    throw ArgumentError.value(value, 'value', 'unknown ORT element type');
  }
}

/// The closed set of ONNX value kinds exposed by type introspection.
enum OrtValueKind {
  unknown,
  tensor,
  sequence,
  map,
  opaque,
  sparseTensor,
  optional,
}
