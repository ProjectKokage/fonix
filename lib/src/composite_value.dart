part of 'runtime.dart';

const int _maximumCompositeChildren = 1024;
const int _maximumStringTensorElements = 1024 * 1024;
const int _maximumStringElementBytes = 1024 * 1024;
const int _maximumStringTensorBytes = 64 * 1024 * 1024;

/// An owned dense UTF-8 string tensor.
final class OrtStringTensor extends _NativeOwner implements OrtValue {
  factory OrtStringTensor.fromStrings({
    required OrtRuntime runtime,
    required Iterable<String> values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    runtime._ensureOpen();
    final OrtShape checkedShape = OrtShape(shape, limits: limits);
    if (checkedShape.elementCount > _maximumStringTensorElements) {
      throw RangeError('String tensor exceeds the native element limit.');
    }
    final List<String> copied = <String>[];
    final Iterator<String> iterator = values.iterator;
    var totalBytes = 0;
    for (var index = 0; index < checkedShape.elementCount; index += 1) {
      if (!iterator.moveNext()) {
        throw ArgumentError('String count does not match the tensor shape.');
      }
      final String value = iterator.current;
      if (!hasWellFormedUtf16(value) || value.contains('\u0000')) {
        throw ArgumentError(
          'String tensors require well-formed UTF-16 without embedded NUL.',
        );
      }
      final int byteLength = utf8.encode(value).length;
      if (byteLength > _maximumStringElementBytes ||
          byteLength > limits.maxTensorBytes - totalBytes ||
          totalBytes + byteLength > _maximumStringTensorBytes) {
        throw RangeError('String tensor exceeds its configured byte limit.');
      }
      totalBytes += byteLength;
      copied.add(value);
    }
    if (iterator.moveNext()) {
      throw ArgumentError('String count does not match the tensor shape.');
    }
    try {
      final Pointer<Void> handle = runtime._nativeApi.createStringTensorCopy(
        runtime: runtime._nativeHandle,
        values: copied,
        dimensions: checkedShape.dimensions,
      );
      return _stringTensorFromNative(
        runtime: runtime,
        handle: handle,
        limits: limits,
        expectedShape: checkedShape,
      );
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
  }

  OrtStringTensor._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required OrtTensorInfo info,
    required int maximumElements,
    required int maximumCopyBytes,
    required _ExternalMemoryAccount memoryAccount,
  }) : _runtime = runtime,
       _info = info,
       _type = _concreteTensorType(info),
       _maximumElements = maximumElements,
       _maximumCopyBytes = maximumCopyBytes,
       _memoryAccount = memoryAccount,
       super(
         nativeApi: runtime._nativeApi,
         nativeHandle: handle,
         releaseAddress: runtime._nativeApi.valueReleaseAddress,
         release: runtime._nativeApi.releaseValue,
         debugName: 'OrtStringTensor',
       );

  @override
  final OrtRuntime _runtime;
  final OrtTensorInfo _info;
  final OrtTypeInfo _type;
  final int _maximumElements;
  final int _maximumCopyBytes;
  _ExternalMemoryAccount? _memoryAccount;

  _ExternalMemoryAccount _retainMemoryAccount() {
    _ensureOpen();
    final _ExternalMemoryAccount account = _memoryAccount!;
    account.retain();
    return account;
  }

  @override
  void _disposeDependents() {
    final _ExternalMemoryAccount? account = _memoryAccount;
    _memoryAccount = null;
    account?.release();
  }

  @override
  OrtValueKind get kind {
    _ensureOpen();
    return OrtValueKind.tensor;
  }

  @override
  OrtTypeInfo get type {
    _ensureOpen();
    return _type;
  }

  @override
  OrtStringTensor retain() => _retainBorrowedValue(this) as OrtStringTensor;

  OrtTensorInfo get info {
    _ensureOpen();
    return _info;
  }

  OrtShape get shape {
    _ensureOpen();
    return _info.shape;
  }

  List<String> copyStrings() {
    _ensureOpen();
    try {
      final List<String> result = _nativeApi.copyTensorStrings(
        _nativeHandle,
        _maximumElements,
        _maximumCopyBytes,
      );
      if (result.length != _info.shape.elementCount) {
        throw OrtNativePackagingException(
          operation: 'tensor_string_count',
          code: _nativeErrorPlatform,
          message: 'The native string count changed unexpectedly.',
        );
      }
      return result;
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    } on StateError {
      throw OrtNativePackagingException(
        operation: 'tensor_string_copy',
        code: _nativeErrorPlatform,
        message: 'The native shim returned invalid string tensor data.',
      );
    } on FormatException {
      throw OrtNativePackagingException(
        operation: 'tensor_string_copy',
        code: _nativeErrorPlatform,
        message: 'The native shim returned invalid string tensor data.',
      );
    }
  }
}

abstract base class _CompositeValue extends _NativeOwner implements OrtValue {
  _CompositeValue({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required OrtTypeInfo type,
    required List<OrtValue> children,
    required List<_ExternalMemoryAccount> memoryAccounts,
    required super.debugName,
  }) : _runtime = runtime,
       _type = type,
       _children = List<OrtValue>.unmodifiable(children),
       _memoryAccounts = memoryAccounts,
       super(
         nativeApi: runtime._nativeApi,
         nativeHandle: handle,
         releaseAddress: runtime._nativeApi.valueReleaseAddress,
         release: runtime._nativeApi.releaseValue,
       );

  @override
  final OrtRuntime _runtime;
  final OrtTypeInfo _type;
  final List<OrtValue> _children;
  List<_ExternalMemoryAccount>? _memoryAccounts;

  @override
  OrtTypeInfo get type {
    _ensureOpen();
    return _type;
  }

  @override
  OrtValue retain() => _retainBorrowedValue(this);

  @override
  void _disposeDependents() {
    for (final OrtValue child in _children) {
      child.dispose();
    }
    final List<_ExternalMemoryAccount>? accounts = _memoryAccounts;
    _memoryAccounts = null;
    if (accounts != null) {
      for (final _ExternalMemoryAccount account in accounts) {
        account.release();
      }
    }
  }
}

List<_ExternalMemoryAccount> _retainCompositeMemoryAccounts(
  List<OrtValue> children,
) {
  final List<_ExternalMemoryAccount> result = <_ExternalMemoryAccount>[];
  try {
    for (final OrtValue child in children) {
      _retainValueMemoryAccounts(child, result);
    }
    return result;
  } catch (_) {
    for (final _ExternalMemoryAccount account in result) {
      account.release();
    }
    rethrow;
  }
}

/// An owned, homogeneous ONNX sequence.
final class OrtSequence extends _CompositeValue {
  factory OrtSequence.fromValues({
    required OrtRuntime runtime,
    required Iterable<OrtValue> values,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    runtime._ensureOpen();
    final List<OrtValue> checked = _boundedValues(values);
    if (checked.isEmpty) {
      throw ArgumentError('A sequence requires at least one value.');
    }
    for (final OrtValue value in checked) {
      _validateValueRuntime(runtime, value);
      if (value is OrtOptional) {
        throw ArgumentError(
          'Sequence elements must have one non-optional concrete type.',
        );
      }
    }
    var elementType = checked.first.type;
    for (var index = 1; index < checked.length; index += 1) {
      final OrtTypeInfo? common = _commonSequenceElementType(
        elementType,
        checked[index].type,
        limits,
      );
      if (common == null) {
        throw ArgumentError(
          'Sequence elements must have one non-optional concrete type.',
        );
      }
      elementType = common;
    }
    final OrtTypeInfo sequenceType = OrtTypeInfo.sequence(
      elementType,
      limits: limits,
    );
    final List<Pointer<Void>> retainedHandles = <Pointer<Void>>[];
    final Pointer<Void> handle;
    try {
      for (final OrtValue value in checked) {
        final Pointer<Void> childHandle = value._nativeHandle;
        runtime._nativeApi.retainValue(childHandle);
        try {
          retainedHandles.add(childHandle);
        } catch (_) {
          runtime._nativeApi.releaseValue(childHandle);
          rethrow;
        }
      }
      handle = runtime._nativeApi.createSequence(
        runtime._nativeHandle,
        retainedHandles,
      );
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    } finally {
      for (final Pointer<Void> childHandle in retainedHandles) {
        runtime._nativeApi.releaseValue(childHandle);
      }
    }

    final List<OrtValue> retained = <OrtValue>[];
    var ownsHandle = true;
    try {
      _validateCreatedComposite(
        runtime: runtime,
        handle: handle,
        kind: OrtValueKind.sequence,
        childCount: checked.length,
      );
      for (final OrtValue value in checked) {
        retained.add(_retainBorrowedValue(value));
      }
      final OrtSequence result = OrtSequence._(
        runtime: runtime,
        handle: handle,
        type: sequenceType,
        elements: retained,
      );
      ownsHandle = false;
      retained.clear();
      return result;
    } finally {
      for (final OrtValue value in retained) {
        value.dispose();
      }
      if (ownsHandle) runtime._nativeApi.releaseValue(handle);
    }
  }

  factory OrtSequence._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required OrtTypeInfo type,
    required List<OrtValue> elements,
  }) {
    final List<OrtValue> copied = List<OrtValue>.unmodifiable(elements);
    return _constructComposite(
      copied,
      (List<_ExternalMemoryAccount> accounts) => OrtSequence._owned(
        runtime: runtime,
        handle: handle,
        type: type,
        elements: copied,
        memoryAccounts: accounts,
      ),
    );
  }

  OrtSequence._owned({
    required super.runtime,
    required super.handle,
    required super.type,
    required List<OrtValue> elements,
    required super.memoryAccounts,
  }) : _elements = elements,
       super(children: elements, debugName: 'OrtSequence');

  final List<OrtValue> _elements;

  @override
  OrtValueKind get kind {
    _ensureOpen();
    return OrtValueKind.sequence;
  }

  /// Borrowed elements disposed with this sequence.
  List<OrtValue> get elements {
    _ensureOpen();
    return _elements;
  }
}

/// An owned ONNX map represented by its key and value tensors.
final class OrtMap extends _CompositeValue {
  factory OrtMap.fromValues({
    required OrtRuntime runtime,
    required OrtValue keys,
    required OrtValue values,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    runtime._ensureOpen();
    _validateValueRuntime(runtime, keys);
    _validateValueRuntime(runtime, values);
    final OrtTensorElementType? keyType = keys.type.tensorElementType;
    if (keys.kind != OrtValueKind.tensor ||
        (keyType != OrtTensorElementType.string &&
            keyType != OrtTensorElementType.int64) ||
        values.kind != OrtValueKind.tensor) {
      throw ArgumentError(
        'Map keys must be string or int64 tensors and values must be a tensor.',
      );
    }
    if (!_validMapTensorShapes(keys, values)) {
      throw ArgumentError(
        'Map keys must be rank one and value leading cardinality must match.',
      );
    }
    final OrtTypeInfo mapType = OrtTypeInfo.map(
      keyElementType: keyType!,
      value: values.type,
      limits: limits,
    );
    final Pointer<Void> handle;
    try {
      handle = runtime._nativeApi.createMap(
        runtime._nativeHandle,
        keys._nativeHandle,
        values._nativeHandle,
      );
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }

    final List<OrtValue> retained = <OrtValue>[];
    var ownsHandle = true;
    try {
      _validateCreatedComposite(
        runtime: runtime,
        handle: handle,
        kind: OrtValueKind.map,
        childCount: 2,
      );
      retained
        ..add(_retainBorrowedValue(keys))
        ..add(_retainBorrowedValue(values));
      final OrtMap result = OrtMap._(
        runtime: runtime,
        handle: handle,
        type: mapType,
        keys: retained[0],
        values: retained[1],
      );
      ownsHandle = false;
      retained.clear();
      return result;
    } finally {
      for (final OrtValue value in retained) {
        value.dispose();
      }
      if (ownsHandle) runtime._nativeApi.releaseValue(handle);
    }
  }

  factory OrtMap._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required OrtTypeInfo type,
    required OrtValue keys,
    required OrtValue values,
  }) {
    final List<OrtValue> children = <OrtValue>[keys, values];
    return _constructComposite(
      children,
      (List<_ExternalMemoryAccount> accounts) => OrtMap._owned(
        runtime: runtime,
        handle: handle,
        type: type,
        keys: keys,
        values: values,
        memoryAccounts: accounts,
      ),
    );
  }

  OrtMap._owned({
    required super.runtime,
    required super.handle,
    required super.type,
    required OrtValue keys,
    required OrtValue values,
    required super.memoryAccounts,
  }) : _keys = keys,
       _values = values,
       super(children: <OrtValue>[keys, values], debugName: 'OrtMap');

  /// Borrowed key tensor disposed with this map.
  OrtValue get keys {
    _ensureOpen();
    return _keys;
  }

  final OrtValue _keys;

  /// Borrowed value tensor disposed with this map.
  OrtValue get values {
    _ensureOpen();
    return _values;
  }

  final OrtValue _values;

  @override
  OrtValueKind get kind {
    _ensureOpen();
    return OrtValueKind.map;
  }
}

/// An owned ONNX optional sentinel or contained value.
final class OrtOptional extends _CompositeValue {
  factory OrtOptional.none({
    required OrtRuntime runtime,
    required OrtTypeInfo elementType,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    runtime._ensureOpen();
    final OrtTypeInfo type = OrtTypeInfo.optional(elementType, limits: limits);
    final Pointer<Void> handle;
    try {
      handle = runtime._nativeApi.createOptionalNone(runtime._nativeHandle);
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
    try {
      _validateCreatedComposite(
        runtime: runtime,
        handle: handle,
        kind: OrtValueKind.optional,
        childCount: 0,
      );
      return OrtOptional._(
        runtime: runtime,
        handle: handle,
        type: type,
        value: null,
      );
    } catch (_) {
      runtime._nativeApi.releaseValue(handle);
      rethrow;
    }
  }

  factory OrtOptional.some({
    required OrtValue value,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    value._runtime._ensureOpen();
    value._ensureValueOpen();
    if (value is OrtOptional) {
      throw ArgumentError('An optional cannot directly contain an optional.');
    }
    final OrtTypeInfo type = OrtTypeInfo.optional(value.type, limits: limits);
    final Pointer<Void> handle;
    try {
      handle = value._nativeApi.createOptionalSome(value._nativeHandle);
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: value._runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
    OrtValue? retained;
    var ownsHandle = true;
    try {
      _validateCreatedComposite(
        runtime: value._runtime,
        handle: handle,
        kind: OrtValueKind.optional,
        childCount: 1,
      );
      retained = _retainBorrowedValue(value);
      final OrtOptional result = OrtOptional._(
        runtime: value._runtime,
        handle: handle,
        type: type,
        value: retained,
      );
      ownsHandle = false;
      retained = null;
      return result;
    } finally {
      retained?.dispose();
      if (ownsHandle) value._nativeApi.releaseValue(handle);
    }
  }

  factory OrtOptional._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required OrtTypeInfo type,
    required OrtValue? value,
  }) {
    final List<OrtValue> children = value == null
        ? const <OrtValue>[]
        : <OrtValue>[value];
    return _constructComposite(
      children,
      (List<_ExternalMemoryAccount> accounts) => OrtOptional._owned(
        runtime: runtime,
        handle: handle,
        type: type,
        value: value,
        memoryAccounts: accounts,
      ),
    );
  }

  OrtOptional._owned({
    required super.runtime,
    required super.handle,
    required super.type,
    required OrtValue? value,
    required super.memoryAccounts,
  }) : _value = value,
       super(
         children: value == null ? const <OrtValue>[] : <OrtValue>[value],
         debugName: 'OrtOptional',
       );

  /// The borrowed contained value, or null for None.
  OrtValue? get value {
    _ensureOpen();
    return _value;
  }

  final OrtValue? _value;

  bool get hasValue {
    _ensureOpen();
    return _value != null;
  }

  @override
  OrtValueKind get kind {
    _ensureOpen();
    return OrtValueKind.optional;
  }
}

T _constructComposite<T extends _CompositeValue>(
  List<OrtValue> children,
  T Function(List<_ExternalMemoryAccount> accounts) construct,
) {
  final List<_ExternalMemoryAccount> accounts = _retainCompositeMemoryAccounts(
    children,
  );
  try {
    return construct(accounts);
  } catch (_) {
    for (final _ExternalMemoryAccount account in accounts) {
      account.release();
    }
    rethrow;
  }
}

OrtTensorInfo? _tensorInfoOfOrNull(OrtValue value) => switch (value) {
  OrtTensor() => value._info,
  OrtStringTensor() => value._info,
  _ => null,
};

bool _validMapTensorShapes(OrtValue keys, OrtValue values) {
  final OrtTensorInfo? keyInfo = _tensorInfoOfOrNull(keys);
  final OrtTensorInfo? valueInfo = _tensorInfoOfOrNull(values);
  return keyInfo != null &&
      valueInfo != null &&
      keyInfo.shape.rank == 1 &&
      valueInfo.shape.rank != 0 &&
      valueInfo.shape.dimensions.first == keyInfo.shape.dimensions.single;
}

void _validateCreatedComposite({
  required OrtRuntime runtime,
  required Pointer<Void> handle,
  required OrtValueKind kind,
  required int childCount,
}) {
  try {
    validateOrtNativeValueKind(runtime._nativeApi.valueKind(handle), kind);
    final int actualChildren = _boundedChildCount(runtime, handle);
    if (actualChildren != childCount) {
      throw const FormatException(
        'Native composite child count differs from its request.',
      );
    }
  } on FonixNativeFailure catch (failure) {
    throw _translateNativeFailure(
      failure,
      runtimeSource: runtime._info.runtimeSource,
      requiredApi: OrtApiVersion.v27,
    );
  } on FormatException catch (cause) {
    throw OrtNativePackagingException(
      operation: 'composite_create_validate',
      code: _nativeErrorPlatform,
      message: 'The native shim returned an incompatible composite value.',
      cause: cause,
    );
  } on StateError catch (cause) {
    throw OrtNativePackagingException(
      operation: 'composite_create_validate',
      code: _nativeErrorPlatform,
      message: 'The native shim returned an invalid composite value.',
      cause: cause,
    );
  }
}

OrtTypeInfo? _commonSequenceElementType(
  OrtTypeInfo left,
  OrtTypeInfo right,
  OrtResourceLimits limits,
) {
  if (left.kind != right.kind) return null;
  switch (left.kind) {
    case OrtValueKind.tensor:
      if (left.tensorElementType != right.tensorElementType) return null;
      if (!left.hasShape ||
          !right.hasShape ||
          left.dimensions.length != right.dimensions.length) {
        return OrtTypeInfo.tensor(
          elementType: left.tensorElementType!,
          hasShape: false,
          limits: limits,
        );
      }
      return OrtTypeInfo.tensor(
        elementType: left.tensorElementType!,
        dimensions: <OrtDimension>[
          for (var index = 0; index < left.dimensions.length; index += 1)
            left.dimensions[index] == right.dimensions[index]
                ? left.dimensions[index]
                : OrtDimension.dynamic(),
        ],
        limits: limits,
      );
    case OrtValueKind.sequence:
      final OrtTypeInfo? element = _commonSequenceElementType(
        left.sequenceElement!,
        right.sequenceElement!,
        limits,
      );
      return element == null
          ? null
          : OrtTypeInfo.sequence(element, limits: limits);
    case OrtValueKind.map:
      if (left.mapKeyType != right.mapKeyType) return null;
      final OrtTypeInfo? value = _commonSequenceElementType(
        left.mapValueType!,
        right.mapValueType!,
        limits,
      );
      return value == null
          ? null
          : OrtTypeInfo.map(
              keyElementType: left.mapKeyType!,
              value: value,
              limits: limits,
            );
    case OrtValueKind.optional:
    case OrtValueKind.unknown:
    case OrtValueKind.opaque:
    case OrtValueKind.sparseTensor:
      return null;
  }
}

List<OrtValue> _boundedValues(Iterable<OrtValue> values) {
  final List<OrtValue> result = <OrtValue>[];
  for (final OrtValue value in values) {
    if (result.length == _maximumCompositeChildren) {
      throw RangeError('Composite value exceeds the native child limit.');
    }
    result.add(value);
  }
  return List<OrtValue>.unmodifiable(result);
}

void _validateValueRuntime(OrtRuntime runtime, OrtValue value) {
  value._ensureValueOpen();
  if (!identical(runtime, value._runtime)) {
    throw ArgumentError('Values belong to different runtime owners.');
  }
}

extension on OrtValue {
  void _ensureValueOpen() {
    if (isDisposed) throw OrtDisposedException(runtimeType.toString());
  }
}

OrtStringTensor _stringTensorFromNative({
  required OrtRuntime runtime,
  required Pointer<Void> handle,
  required OrtResourceLimits limits,
  OrtShape? expectedShape,
}) {
  _ExternalMemoryAccount? memoryAccount;
  try {
    final OrtTensorInfo info = _parseOrtTensorInfo(
      runtime._nativeApi.getTensorInfoJson(handle),
      limits: limits,
    );
    if (info.elementType != OrtTensorElementType.string ||
        (expectedShape != null && info.shape != expectedShape)) {
      throw StateError('Native string tensor metadata differs from its type.');
    }
    memoryAccount = _NativeTensorMemoryAccount(
      nativeApi: runtime._nativeApi,
      handle: handle,
      releaseAddress: runtime._nativeApi.valueReleaseAddress,
      externalSize: info.byteLength == 0 ? null : info.byteLength,
    );
    return OrtStringTensor._(
      runtime: runtime,
      handle: handle,
      info: info,
      maximumElements: info.shape.elementCount,
      maximumCopyBytes: info.byteLength,
      memoryAccount: memoryAccount,
    );
  } on FormatException catch (cause) {
    memoryAccount?.release();
    runtime._nativeApi.releaseValue(handle);
    throw OrtNativePackagingException(
      operation: 'string_tensor_info_decode',
      code: _nativeErrorPlatform,
      message: 'The native shim returned incompatible string tensor metadata.',
      cause: cause,
    );
  } on StateError catch (cause) {
    memoryAccount?.release();
    runtime._nativeApi.releaseValue(handle);
    throw OrtNativePackagingException(
      operation: 'string_tensor_info_validate',
      code: _nativeErrorPlatform,
      message: 'The native shim returned invalid string tensor metadata.',
      cause: cause,
    );
  } catch (_) {
    memoryAccount?.release();
    runtime._nativeApi.releaseValue(handle);
    rethrow;
  }
}

OrtValue _valueFromNative({
  required OrtRuntime runtime,
  required Pointer<Void> handle,
  required OrtTypeInfo expectedType,
  required OrtResourceLimits limits,
  _ValueDecodeBudget? budget,
  int depth = 1,
}) {
  final _ValueDecodeBudget decodeBudget =
      budget ?? _ValueDecodeBudget(limits: limits);
  final List<OrtValue> children = <OrtValue>[];
  var ownsHandle = true;
  try {
    decodeBudget.consume(depth);
    validateOrtNativeValueKind(
      runtime._nativeApi.valueKind(handle),
      expectedType.kind,
    );
    switch (expectedType.kind) {
      case OrtValueKind.tensor:
        if (expectedType.tensorElementType == OrtTensorElementType.string) {
          ownsHandle = false;
          final OrtStringTensor tensor = _stringTensorFromNative(
            runtime: runtime,
            handle: handle,
            limits: limits,
          );
          if (!_tensorMatchesMetadata(expectedType, tensor._info)) {
            tensor.dispose();
            throw const FormatException(
              'String tensor differs from session metadata.',
            );
          }
          return tensor;
        }
        ownsHandle = false;
        final OrtTensor tensor = _tensorFromNative(
          runtime: runtime,
          handle: handle,
          limits: limits,
        );
        if (!_tensorMatchesMetadata(expectedType, tensor._info)) {
          tensor.dispose();
          throw const FormatException('Tensor differs from session metadata.');
        }
        return tensor;
      case OrtValueKind.sequence:
        final int count = _boundedChildCount(runtime, handle);
        decodeBudget.requireAvailable(count);
        for (var index = 0; index < count; index += 1) {
          children.add(
            _valueFromNative(
              runtime: runtime,
              handle: runtime._nativeApi.getValueChild(handle, index),
              expectedType: expectedType.sequenceElement!,
              limits: limits,
              budget: decodeBudget,
              depth: depth + 1,
            ),
          );
        }
        final OrtSequence result = OrtSequence._(
          runtime: runtime,
          handle: handle,
          type: expectedType,
          elements: children,
        );
        ownsHandle = false;
        return result;
      case OrtValueKind.map:
        if (_boundedChildCount(runtime, handle) != 2) {
          throw const FormatException('Native map must have two children.');
        }
        decodeBudget.requireAvailable(2);
        final OrtTypeInfo keyType = OrtTypeInfo.tensor(
          elementType: expectedType.mapKeyType!,
          hasShape: false,
          limits: limits,
        );
        children
          ..add(
            _valueFromNative(
              runtime: runtime,
              handle: runtime._nativeApi.getValueChild(handle, 0),
              expectedType: keyType,
              limits: limits,
              budget: decodeBudget,
              depth: depth + 1,
            ),
          )
          ..add(
            _valueFromNative(
              runtime: runtime,
              handle: runtime._nativeApi.getValueChild(handle, 1),
              expectedType: expectedType.mapValueType!,
              limits: limits,
              budget: decodeBudget,
              depth: depth + 1,
            ),
          );
        if (!_validMapTensorShapes(children[0], children[1])) {
          throw const FormatException(
            'Native map tensors have incompatible cardinality.',
          );
        }
        final OrtMap result = OrtMap._(
          runtime: runtime,
          handle: handle,
          type: expectedType,
          keys: children[0],
          values: children[1],
        );
        ownsHandle = false;
        return result;
      case OrtValueKind.optional:
        final int count = _boundedChildCount(runtime, handle);
        if (count > 1) {
          throw const FormatException('Native optional has too many children.');
        }
        decodeBudget.requireAvailable(count);
        if (count == 1) {
          children.add(
            _valueFromNative(
              runtime: runtime,
              handle: runtime._nativeApi.getValueChild(handle, 0),
              expectedType: expectedType.optionalElement!,
              limits: limits,
              budget: decodeBudget,
              depth: depth + 1,
            ),
          );
        }
        final OrtOptional result = OrtOptional._(
          runtime: runtime,
          handle: handle,
          type: expectedType,
          value: children.isEmpty ? null : children.first,
        );
        ownsHandle = false;
        return result;
      case OrtValueKind.unknown:
      case OrtValueKind.opaque:
      case OrtValueKind.sparseTensor:
        throw const FormatException('Unsupported native value kind.');
    }
  } catch (_) {
    for (final OrtValue child in children) {
      child.dispose();
    }
    if (ownsHandle) runtime._nativeApi.releaseValue(handle);
    rethrow;
  }
}

final class _ValueDecodeBudget {
  _ValueDecodeBudget({required this.limits});

  final OrtResourceLimits limits;
  int _nodes = 0;

  void consume(int depth) {
    if (depth > limits.maxTypeDepth || _nodes == limits.maxTypeNodes) {
      throw const FormatException('Native value tree exceeds its Dart limit.');
    }
    _nodes += 1;
  }

  void requireAvailable(int minimumNodes) {
    if (minimumNodes < 0 || minimumNodes > limits.maxTypeNodes - _nodes) {
      throw const FormatException('Native value tree exceeds its Dart limit.');
    }
  }
}

int _boundedChildCount(OrtRuntime runtime, Pointer<Void> handle) {
  final int count = runtime._nativeApi.valueChildCount(handle);
  return validateOrtValueChildCount(count);
}

/// Exercises native value-kind validation without opening a runtime.
///
/// This entry point is intentionally omitted from `package:fonix/fonix.dart`.
void validateOrtNativeValueKind(int nativeKind, OrtValueKind expectedKind) {
  final int expectedNativeKind = _nativeValueKind(expectedKind);
  if (expectedNativeKind < 0 || nativeKind != expectedNativeKind) {
    throw const FormatException('Native value kind differs from metadata.');
  }
}

/// Exercises native composite-count validation before child allocation.
///
/// This entry point is intentionally omitted from `package:fonix/fonix.dart`.
int validateOrtValueChildCount(int count) {
  if (count < 0 || count > _maximumCompositeChildren) {
    throw const FormatException('Native composite child count is invalid.');
  }
  return count;
}

int _nativeValueKind(OrtValueKind kind) => switch (kind) {
  OrtValueKind.tensor => bindings.dort_value_kind.DORT_VALUE_KIND_TENSOR,
  OrtValueKind.sequence => bindings.dort_value_kind.DORT_VALUE_KIND_SEQUENCE,
  OrtValueKind.map => bindings.dort_value_kind.DORT_VALUE_KIND_MAP,
  OrtValueKind.optional => bindings.dort_value_kind.DORT_VALUE_KIND_OPTIONAL,
  _ => -1,
};
