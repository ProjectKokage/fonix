import 'dart:convert';
import 'dart:typed_data';

import 'metadata.dart';
import 'resource_limits.dart';
import 'tensor_type.dart';

const int _isolateMaximumCompositeChildren = 1024;
const int _isolateMaximumStringElementBytes = 1024 * 1024;

/// A closed, pointer-free ONNX value that can cross a Dart isolate boundary.
///
/// Every constructor copies caller-owned collections. These values contain no
/// native handles and may be reused for multiple worker runs.
sealed class OrtIsolateValue {
  const OrtIsolateValue();

  OrtValueKind get kind;

  OrtTypeInfo get type;
}

/// A copied dense tensor used by [OrtIsolateSession].
final class OrtIsolateTensor extends OrtIsolateValue {
  factory OrtIsolateTensor.fromBytes({
    required OrtTensorElementType elementType,
    required Uint8List bytes,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    if (!elementType.supportsDenseCreation ||
        elementType.fixedStorageBytes == null) {
      throw UnsupportedError(
        'Isolate tensor bytes do not support ${elementType.name}.',
      );
    }
    final OrtShape checkedShape = OrtShape(shape, limits: limits);
    final int requiredBytes = checkedShape.requiredBytes(
      elementType.fixedStorageBytes!,
    );
    if (bytes.length != requiredBytes) {
      throw ArgumentError(
        'Tensor byte count does not match its element type and shape.',
      );
    }
    if (elementType == OrtTensorElementType.boolean &&
        bytes.any((int value) => value > 1)) {
      throw ArgumentError('Boolean tensor bytes must be 0 or 1.');
    }
    return OrtIsolateTensor._(
      elementType: elementType,
      shape: checkedShape,
      bytes: Uint8List.fromList(bytes),
      strings: null,
      type: _isolateTensorType(elementType, checkedShape),
    );
  }

  factory OrtIsolateTensor.fromFloat32List({
    required Float32List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.float32,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromFloat64List({
    required Float64List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.float64,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromInt8List({
    required Int8List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.int8,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromUint8List({
    required Uint8List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.uint8,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromInt16List({
    required Int16List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.int16,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromUint16List({
    required Uint16List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.uint16,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromInt32List({
    required Int32List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.int32,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromUint32List({
    required Uint32List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.uint32,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromInt64List({
    required Int64List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.int64,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromUint64List({
    required Uint64List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.uint64,
    bytes: _copyTypedBytes(values),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromFloat16Bits({
    required Uint16List bits,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.float16,
    bytes: _copyTypedBytes(bits),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromBFloat16Bits({
    required Uint16List bits,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => OrtIsolateTensor.fromBytes(
    elementType: OrtTensorElementType.bfloat16,
    bytes: _copyTypedBytes(bits),
    shape: shape,
    limits: limits,
  );

  factory OrtIsolateTensor.fromBoolList({
    required Iterable<bool> values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    final OrtShape checkedShape = OrtShape(shape, limits: limits);
    final Uint8List bytes = Uint8List(checkedShape.elementCount);
    final Iterator<bool> iterator = values.iterator;
    for (var index = 0; index < bytes.length; index += 1) {
      if (!iterator.moveNext()) {
        throw ArgumentError('Boolean count does not match the tensor shape.');
      }
      bytes[index] = iterator.current ? 1 : 0;
    }
    if (iterator.moveNext()) {
      throw ArgumentError('Boolean count does not match the tensor shape.');
    }
    return OrtIsolateTensor.fromBytes(
      elementType: OrtTensorElementType.boolean,
      bytes: bytes,
      shape: checkedShape.dimensions,
      limits: limits,
    );
  }

  factory OrtIsolateTensor.fromStrings({
    required Iterable<String> values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    final OrtShape checkedShape = OrtShape(shape, limits: limits);
    final List<String> copied = <String>[];
    final Iterator<String> iterator = values.iterator;
    var totalBytes = 0;
    for (var index = 0; index < checkedShape.elementCount; index += 1) {
      if (!iterator.moveNext()) {
        throw ArgumentError('String count does not match the tensor shape.');
      }
      final String value = iterator.current;
      if (!_wellFormedUtf16(value) || value.contains('\u0000')) {
        throw ArgumentError(
          'String tensors require well-formed UTF-16 without embedded NUL.',
        );
      }
      final int byteLength = utf8.encode(value).length;
      if (byteLength > _isolateMaximumStringElementBytes ||
          byteLength > limits.maxTensorBytes - totalBytes) {
        throw RangeError('String tensor exceeds its configured byte limit.');
      }
      totalBytes += byteLength;
      copied.add(value);
    }
    if (iterator.moveNext()) {
      throw ArgumentError('String count does not match the tensor shape.');
    }
    return OrtIsolateTensor._(
      elementType: OrtTensorElementType.string,
      shape: checkedShape,
      bytes: null,
      strings: List<String>.unmodifiable(copied),
      type: _isolateTensorType(OrtTensorElementType.string, checkedShape),
    );
  }

  const OrtIsolateTensor._({
    required this.elementType,
    required this.shape,
    required Uint8List? bytes,
    required List<String>? strings,
    required this.type,
  }) : _bytes = bytes,
       _strings = strings;

  final OrtTensorElementType elementType;
  final OrtShape shape;
  final Uint8List? _bytes;
  final List<String>? _strings;

  @override
  final OrtTypeInfo type;

  @override
  OrtValueKind get kind => OrtValueKind.tensor;

  bool get isString => elementType == OrtTensorElementType.string;

  int get byteLength => _bytes?.length ?? _stringByteLength(_strings!);

  Uint8List copyBytes() {
    final Uint8List? bytes = _bytes;
    if (bytes == null) {
      throw StateError('A string tensor does not have fixed-width bytes.');
    }
    return Uint8List.fromList(bytes);
  }

  List<String> copyStrings() {
    final List<String>? strings = _strings;
    if (strings == null) {
      throw StateError('A numeric tensor does not contain strings.');
    }
    return List<String>.unmodifiable(strings);
  }

  Float32List copyFloat32Data() =>
      Float32List.view(_typedBuffer(OrtTensorElementType.float32));
  Float64List copyFloat64Data() =>
      Float64List.view(_typedBuffer(OrtTensorElementType.float64));
  Int8List copyInt8Data() =>
      Int8List.view(_typedBuffer(OrtTensorElementType.int8));
  Uint8List copyUint8Data() => copyBytesFor(OrtTensorElementType.uint8);
  Int16List copyInt16Data() =>
      Int16List.view(_typedBuffer(OrtTensorElementType.int16));
  Uint16List copyUint16Data() =>
      Uint16List.view(_typedBuffer(OrtTensorElementType.uint16));
  Int32List copyInt32Data() =>
      Int32List.view(_typedBuffer(OrtTensorElementType.int32));
  Uint32List copyUint32Data() =>
      Uint32List.view(_typedBuffer(OrtTensorElementType.uint32));
  Int64List copyInt64Data() =>
      Int64List.view(_typedBuffer(OrtTensorElementType.int64));
  Uint64List copyUint64Data() =>
      Uint64List.view(_typedBuffer(OrtTensorElementType.uint64));
  Uint16List copyFloat16Bits() =>
      Uint16List.view(_typedBuffer(OrtTensorElementType.float16));
  Uint16List copyBFloat16Bits() =>
      Uint16List.view(_typedBuffer(OrtTensorElementType.bfloat16));

  List<bool> copyBoolData() => List<bool>.unmodifiable(
    copyBytesFor(OrtTensorElementType.boolean).map((int value) => value == 1),
  );

  Uint8List copyBytesFor(OrtTensorElementType expected) {
    if (elementType != expected) {
      throw StateError('Tensor element type is ${elementType.name}.');
    }
    return copyBytes();
  }

  ByteBuffer _typedBuffer(OrtTensorElementType expected) {
    final Uint8List bytes = copyBytesFor(expected);
    return bytes.buffer;
  }
}

/// A copied, bounded ONNX sequence.
final class OrtIsolateSequence extends OrtIsolateValue {
  factory OrtIsolateSequence(Iterable<OrtIsolateValue> elements) {
    final List<OrtIsolateValue> copied = <OrtIsolateValue>[];
    for (final OrtIsolateValue element in elements) {
      if (copied.length == _isolateMaximumCompositeChildren) {
        throw RangeError('An isolate sequence must contain 1..1024 elements.');
      }
      copied.add(element);
    }
    if (copied.isEmpty) {
      throw RangeError('An isolate sequence must contain 1..1024 elements.');
    }
    final OrtTypeInfo elementType = copied.first.type;
    if (copied.any(
      (OrtIsolateValue value) =>
          value is OrtIsolateOptional || value.type != elementType,
    )) {
      throw ArgumentError(
        'An isolate sequence requires one homogeneous non-optional type.',
      );
    }
    return OrtIsolateSequence._(
      List<OrtIsolateValue>.unmodifiable(copied),
      OrtTypeInfo.sequence(elementType),
    );
  }

  const OrtIsolateSequence._(this.elements, this.type);

  final List<OrtIsolateValue> elements;

  @override
  final OrtTypeInfo type;

  @override
  OrtValueKind get kind => OrtValueKind.sequence;
}

/// A copied ONNX-ML map represented by key and value tensors.
final class OrtIsolateMap extends OrtIsolateValue {
  factory OrtIsolateMap({
    required OrtIsolateTensor keys,
    required OrtIsolateTensor values,
  }) {
    if (keys.elementType != OrtTensorElementType.string &&
        keys.elementType != OrtTensorElementType.int64) {
      throw ArgumentError('Map keys must be string or int64 tensors.');
    }
    if (keys.shape.rank != 1 ||
        values.shape.rank == 0 ||
        keys.shape.elementCount != values.shape.dimensions.first) {
      throw ArgumentError('Map key/value leading cardinality must match.');
    }
    return OrtIsolateMap._(
      keys,
      values,
      OrtTypeInfo.map(keyElementType: keys.elementType, value: values.type),
    );
  }

  const OrtIsolateMap._(this.keys, this.values, this.type);

  final OrtIsolateTensor keys;
  final OrtIsolateTensor values;

  @override
  final OrtTypeInfo type;

  @override
  OrtValueKind get kind => OrtValueKind.map;
}

/// A copied optional value. [value] is null for Optional None.
final class OrtIsolateOptional extends OrtIsolateValue {
  OrtIsolateOptional.none({required OrtTypeInfo elementType})
    : value = null,
      elementType = elementType,
      type = OrtTypeInfo.optional(elementType);

  OrtIsolateOptional.some(OrtIsolateValue value)
    : value = value,
      elementType = value.type,
      type = OrtTypeInfo.optional(value.type);

  final OrtIsolateValue? value;
  final OrtTypeInfo elementType;

  @override
  final OrtTypeInfo type;

  @override
  OrtValueKind get kind => OrtValueKind.optional;
}

Uint8List _copyTypedBytes(TypedData values) => Uint8List.fromList(
  values.buffer.asUint8List(values.offsetInBytes, values.lengthInBytes),
);

int _stringByteLength(List<String> values) {
  var result = 0;
  for (final String value in values) {
    result += utf8.encode(value).length;
  }
  return result;
}

bool _wellFormedUtf16(String value) {
  final List<int> units = value.codeUnits;
  for (var index = 0; index < units.length; index += 1) {
    final int unit = units[index];
    if (unit >= 0xd800 && unit <= 0xdbff) {
      if (index + 1 >= units.length) return false;
      final int low = units[++index];
      if (low < 0xdc00 || low > 0xdfff) return false;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

OrtTypeInfo _isolateTensorType(
  OrtTensorElementType elementType,
  OrtShape shape,
) => OrtTypeInfo.tensor(
  elementType: elementType,
  dimensions: <OrtDimension>[
    for (final int dimension in shape.dimensions) OrtDimension.fixed(dimension),
  ],
);
