part of 'runtime.dart';

/// Copied, concrete information for a CPU-resident tensor.
final class OrtTensorInfo {
  const OrtTensorInfo._({
    required this.elementType,
    required this.shape,
    required this.byteLength,
  });

  final OrtTensorElementType elementType;
  final OrtShape shape;
  final int byteLength;
}

/// A ref-counted dense CPU tensor.
final class OrtTensor extends _NativeOwner implements OrtValue {
  factory OrtTensor.fromFloat32List({
    required OrtRuntime runtime,
    required Float32List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.float32,
    limits: limits,
  );

  factory OrtTensor.fromFloat64List({
    required OrtRuntime runtime,
    required Float64List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.float64,
    limits: limits,
  );

  factory OrtTensor.fromInt8List({
    required OrtRuntime runtime,
    required Int8List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.int8,
    limits: limits,
  );

  factory OrtTensor.fromUint8List({
    required OrtRuntime runtime,
    required Uint8List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.uint8,
    limits: limits,
  );

  factory OrtTensor.fromInt16List({
    required OrtRuntime runtime,
    required Int16List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.int16,
    limits: limits,
  );

  factory OrtTensor.fromUint16List({
    required OrtRuntime runtime,
    required Uint16List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.uint16,
    limits: limits,
  );

  factory OrtTensor.fromInt32List({
    required OrtRuntime runtime,
    required Int32List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.int32,
    limits: limits,
  );

  factory OrtTensor.fromUint32List({
    required OrtRuntime runtime,
    required Uint32List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.uint32,
    limits: limits,
  );

  factory OrtTensor.fromInt64List({
    required OrtRuntime runtime,
    required Int64List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.int64,
    limits: limits,
  );

  factory OrtTensor.fromUint64List({
    required OrtRuntime runtime,
    required Uint64List values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: values,
    shape: shape,
    elementType: OrtTensorElementType.uint64,
    limits: limits,
  );

  factory OrtTensor.fromBoolList({
    required OrtRuntime runtime,
    required Iterable<bool> values,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    final (OrtShape checkedShape, Uint8List normalized) = _checkedBoolBytes(
      values,
      shape,
      limits,
    );
    return createCopiedOrtTensor(
      runtime: runtime,
      values: normalized,
      shape: checkedShape.dimensions,
      elementType: OrtTensorElementType.boolean,
      limits: limits,
    );
  }

  /// Creates a float16 tensor from copied raw IEEE binary16 words.
  ///
  /// Use `OrtFloat16Codec` when conversion from or to Dart floating-point
  /// values is desired; this constructor never performs implicit rounding.
  factory OrtTensor.fromFloat16Bits({
    required OrtRuntime runtime,
    required Uint16List bits,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: bits,
    shape: shape,
    elementType: OrtTensorElementType.float16,
    limits: limits,
  );

  /// Creates a bfloat16 tensor from copied raw 16-bit words.
  ///
  /// Use `OrtBFloat16Codec` when conversion from or to Dart floating-point
  /// values is desired; this constructor never performs implicit rounding.
  factory OrtTensor.fromBFloat16Bits({
    required OrtRuntime runtime,
    required Uint16List bits,
    required Iterable<int> shape,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) => createCopiedOrtTensor(
    runtime: runtime,
    values: bits,
    shape: shape,
    elementType: OrtTensorElementType.bfloat16,
    limits: limits,
  );

  factory OrtTensor.fromNativeBuffer({
    required OrtNativeBuffer buffer,
    required Iterable<int> shape,
    required OrtTensorElementType elementType,
    int byteOffset = 0,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    buffer._ensureOpen();
    buffer._runtime._ensureOpen();
    if (!elementType.supportsDenseCreation ||
        elementType.fixedStorageBytes == null) {
      throw UnsupportedError(
        'Native-buffer tensors do not support ${elementType.name}.',
      );
    }
    final OrtShape checkedShape = OrtShape(shape, limits: limits);
    final int byteLength = checkedShape.requiredBytes(
      elementType.fixedStorageBytes!,
    );
    if (byteOffset < 0 ||
        byteOffset > buffer.byteLength ||
        byteLength > buffer.byteLength - byteOffset) {
      throw RangeError('Tensor storage is outside the native buffer.');
    }
    if (byteOffset % elementType.fixedStorageBytes! != 0) {
      throw RangeError(
        'Tensor byte offset is not aligned for its element type.',
      );
    }
    if (elementType == OrtTensorElementType.boolean && byteLength != 0) {
      final Uint8List boolBytes = buffer.read(
        offset: byteOffset,
        byteLength: byteLength,
      );
      if (boolBytes.any((int value) => value > 1)) {
        throw ArgumentError('Boolean native-buffer values must be 0 or 1.');
      }
    }

    final OrtRuntime runtime = buffer._runtime;
    try {
      final Pointer<Void> handle = runtime._nativeApi.createTensorWithBuffer(
        runtime: runtime._nativeHandle,
        buffer: buffer._nativeHandle,
        byteOffset: byteOffset,
        byteLength: byteLength,
        dimensions: checkedShape.dimensions,
        elementType: elementType.nativeValue,
      );
      return _tensorFromNative(
        runtime: runtime,
        handle: handle,
        limits: limits,
        expectedType: elementType,
        expectedShape: checkedShape,
        sharedMemoryAccount: buffer._memoryAccount,
      );
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    } on StateError catch (cause) {
      throw OrtNativePackagingException(
        operation: 'tensor_buffer_create_validate',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid buffer-backed tensor.',
        cause: cause,
      );
    }
  }

  OrtTensor._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required OrtTensorInfo info,
    required int maximumCopyBytes,
    required _ExternalMemoryAccount memoryAccount,
  }) : _runtime = runtime,
       _info = info,
       _type = _concreteTensorType(info),
       _maximumCopyBytes = maximumCopyBytes,
       _memoryAccount = memoryAccount,
       super(
         nativeApi: runtime._nativeApi,
         nativeHandle: handle,
         releaseAddress: runtime._nativeApi.valueReleaseAddress,
         release: runtime._nativeApi.releaseValue,
         debugName: 'OrtTensor',
       );

  @override
  final OrtRuntime _runtime;
  final OrtTensorInfo _info;
  final OrtTypeInfo _type;
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
  OrtTensor retain() => _retainBorrowedValue(this) as OrtTensor;

  OrtTensorInfo get info {
    _ensureOpen();
    return _info;
  }

  OrtTensorElementType get elementType {
    _ensureOpen();
    return _info.elementType;
  }

  OrtShape get shape {
    _ensureOpen();
    return _info.shape;
  }

  Float32List copyFloat32Data() =>
      Float32List.view(_copyTypedBytes(OrtTensorElementType.float32).buffer);

  Float64List copyFloat64Data() =>
      Float64List.view(_copyTypedBytes(OrtTensorElementType.float64).buffer);

  Int8List copyInt8Data() =>
      Int8List.view(_copyTypedBytes(OrtTensorElementType.int8).buffer);

  Uint8List copyUint8Data() => _copyTypedBytes(OrtTensorElementType.uint8);

  Int16List copyInt16Data() =>
      Int16List.view(_copyTypedBytes(OrtTensorElementType.int16).buffer);

  Uint16List copyUint16Data() =>
      Uint16List.view(_copyTypedBytes(OrtTensorElementType.uint16).buffer);

  Int32List copyInt32Data() =>
      Int32List.view(_copyTypedBytes(OrtTensorElementType.int32).buffer);

  Uint32List copyUint32Data() =>
      Uint32List.view(_copyTypedBytes(OrtTensorElementType.uint32).buffer);

  Int64List copyInt64Data() =>
      Int64List.view(_copyTypedBytes(OrtTensorElementType.int64).buffer);

  Uint64List copyUint64Data() =>
      Uint64List.view(_copyTypedBytes(OrtTensorElementType.uint64).buffer);

  /// Copies raw IEEE binary16 words without converting their payload bits.
  Uint16List copyFloat16Bits() =>
      Uint16List.view(_copyTypedBytes(OrtTensorElementType.float16).buffer);

  /// Copies raw bfloat16 words without converting their payload bits.
  Uint16List copyBFloat16Bits() =>
      Uint16List.view(_copyTypedBytes(OrtTensorElementType.bfloat16).buffer);

  List<bool> copyBoolData() {
    final Uint8List bytes = _copyTypedBytes(OrtTensorElementType.boolean);
    if (bytes.any((int value) => value > 1)) {
      throw OrtNativePackagingException(
        operation: 'tensor_copy_bool',
        code: _nativeErrorPlatform,
        message: 'The native shim returned a non-normalized boolean tensor.',
      );
    }
    return List<bool>.unmodifiable(bytes.map((int value) => value == 1));
  }

  /// Returns a read-only native-backed float32 view.
  Float32List viewFloat32Data() => _viewTensorData<Float32List>(
    OrtTensorElementType.float32,
    (Pointer<Void> data, int byteLength) {
      final Float32List owner = data.cast<Float>().asTypedList(
        byteLength ~/ sizeOf<Float>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Float32List.new,
  );

  Float64List viewFloat64Data() => _viewTensorData<Float64List>(
    OrtTensorElementType.float64,
    (Pointer<Void> data, int byteLength) {
      final Float64List owner = data.cast<Double>().asTypedList(
        byteLength ~/ sizeOf<Double>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Float64List.new,
  );

  Int8List viewInt8Data() => _viewTensorData<Int8List>(
    OrtTensorElementType.int8,
    (Pointer<Void> data, int byteLength) {
      final Int8List owner = data.cast<Int8>().asTypedList(byteLength);
      return (owner, owner.asUnmodifiableView());
    },
    Int8List.new,
  );

  Uint8List viewUint8Data() => _viewTensorData<Uint8List>(
    OrtTensorElementType.uint8,
    (Pointer<Void> data, int byteLength) {
      final Uint8List owner = data.cast<Uint8>().asTypedList(byteLength);
      return (owner, owner.asUnmodifiableView());
    },
    Uint8List.new,
  );

  Int16List viewInt16Data() => _viewTensorData<Int16List>(
    OrtTensorElementType.int16,
    (Pointer<Void> data, int byteLength) {
      final Int16List owner = data.cast<Int16>().asTypedList(
        byteLength ~/ sizeOf<Int16>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Int16List.new,
  );

  Uint16List viewUint16Data() => _viewTensorData<Uint16List>(
    OrtTensorElementType.uint16,
    (Pointer<Void> data, int byteLength) {
      final Uint16List owner = data.cast<Uint16>().asTypedList(
        byteLength ~/ sizeOf<Uint16>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Uint16List.new,
  );

  Int32List viewInt32Data() => _viewTensorData<Int32List>(
    OrtTensorElementType.int32,
    (Pointer<Void> data, int byteLength) {
      final Int32List owner = data.cast<Int32>().asTypedList(
        byteLength ~/ sizeOf<Int32>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Int32List.new,
  );

  Uint32List viewUint32Data() => _viewTensorData<Uint32List>(
    OrtTensorElementType.uint32,
    (Pointer<Void> data, int byteLength) {
      final Uint32List owner = data.cast<Uint32>().asTypedList(
        byteLength ~/ sizeOf<Uint32>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Uint32List.new,
  );

  Int64List viewInt64Data() => _viewTensorData<Int64List>(
    OrtTensorElementType.int64,
    (Pointer<Void> data, int byteLength) {
      final Int64List owner = data.cast<Int64>().asTypedList(
        byteLength ~/ sizeOf<Int64>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Int64List.new,
  );

  Uint64List viewUint64Data() => _viewTensorData<Uint64List>(
    OrtTensorElementType.uint64,
    (Pointer<Void> data, int byteLength) {
      final Uint64List owner = data.cast<Uint64>().asTypedList(
        byteLength ~/ sizeOf<Uint64>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Uint64List.new,
  );

  Uint16List viewFloat16Bits() => _viewTensorData<Uint16List>(
    OrtTensorElementType.float16,
    (Pointer<Void> data, int byteLength) {
      final Uint16List owner = data.cast<Uint16>().asTypedList(
        byteLength ~/ sizeOf<Uint16>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Uint16List.new,
  );

  Uint16List viewBFloat16Bits() => _viewTensorData<Uint16List>(
    OrtTensorElementType.bfloat16,
    (Pointer<Void> data, int byteLength) {
      final Uint16List owner = data.cast<Uint16>().asTypedList(
        byteLength ~/ sizeOf<Uint16>(),
      );
      return (owner, owner.asUnmodifiableView());
    },
    Uint16List.new,
  );

  /// Returns read-only normalized Boolean storage bytes.
  Uint8List viewBoolBytes() => _viewTensorData<Uint8List>(
    OrtTensorElementType.boolean,
    (Pointer<Void> data, int byteLength) {
      final Uint8List owner = data.cast<Uint8>().asTypedList(byteLength);
      return (owner, owner.asUnmodifiableView());
    },
    Uint8List.new,
    validateOwner: (Uint8List owner) {
      if (owner.any((int value) => value > 1)) {
        throw StateError(
          'The native shim returned a non-normalized Boolean tensor.',
        );
      }
    },
  );

  T _viewTensorData<T extends TypedData>(
    OrtTensorElementType expectedType,
    (T, T) Function(Pointer<Void> data, int byteLength) createViews,
    T Function(int length) createEmpty, {
    void Function(T owner)? validateOwner,
  }) {
    _ensureOpen();
    if (_info.elementType != expectedType) {
      throw StateError(
        'Tensor contains ${_info.elementType.name}, not ${expectedType.name}.',
      );
    }
    final _ExternalMemoryAccount account = _retainMemoryAccount();
    FonixNativeDataLease? lease;
    try {
      lease = _nativeApi.acquireTensorData(_nativeHandle);
      validateOrtDataLeaseLength(lease.byteLength, _info.byteLength);
      if (lease.byteLength == 0) {
        _nativeApi.releaseDataLease(lease.lease);
        account.release();
        return createEmpty(0);
      }
      final (T owner, T exposed) = createViews(lease.data, lease.byteLength);
      validateOwner?.call(owner);
      _attachDataLease(owner, _nativeApi, lease.lease, account);
      return exposed;
    } on FonixNativeFailure catch (failure) {
      if (lease != null) _nativeApi.releaseDataLease(lease.lease);
      account.release();
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    } on FormatException {
      if (lease != null) _nativeApi.releaseDataLease(lease.lease);
      account.release();
      throw OrtNativePackagingException(
        operation: 'tensor_data_lease',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid tensor data lease.',
      );
    } on StateError {
      if (lease != null) _nativeApi.releaseDataLease(lease.lease);
      account.release();
      throw OrtNativePackagingException(
        operation: 'tensor_data_lease',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid tensor data lease.',
      );
    } on ArgumentError {
      if (lease != null) _nativeApi.releaseDataLease(lease.lease);
      account.release();
      throw OrtNativePackagingException(
        operation: 'tensor_data_lease',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid tensor data lease.',
      );
    } catch (_) {
      if (lease != null) _nativeApi.releaseDataLease(lease.lease);
      account.release();
      rethrow;
    }
  }

  Uint8List _copyTypedBytes(OrtTensorElementType expectedType) {
    _ensureOpen();
    if (_info.elementType != expectedType) {
      throw StateError(
        'Tensor contains ${_info.elementType.name}, not ${expectedType.name}.',
      );
    }
    try {
      final Uint8List bytes = _nativeApi.copyTensorData(
        _nativeHandle,
        _maximumCopyBytes,
      );
      if (bytes.length != _info.byteLength) {
        throw StateError('Copied tensor byte length changed unexpectedly.');
      }
      return bytes;
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    } on StateError catch (cause) {
      throw OrtNativePackagingException(
        operation: 'tensor_data_copy',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid tensor data copy.',
        cause: cause,
      );
    }
  }
}

/// Exercises Boolean tensor validation without opening a native runtime.
///
/// This entry point is intentionally omitted from `package:fonix/fonix.dart`.
Uint8List normalizeOrtBoolValuesForTesting(
  Iterable<bool> values, {
  required Iterable<int> shape,
  OrtResourceLimits limits = OrtResourceLimits.defaults,
}) => _checkedBoolBytes(values, shape, limits).$2;

(OrtShape, Uint8List) _checkedBoolBytes(
  Iterable<bool> values,
  Iterable<int> shape,
  OrtResourceLimits limits,
) {
  final OrtShape checkedShape = OrtShape(shape, limits: limits);
  checkedShape.requiredBytes(1);
  final Uint8List normalized = Uint8List(checkedShape.elementCount);
  final Iterator<bool> iterator = values.iterator;
  for (var index = 0; index < normalized.length; index += 1) {
    if (!iterator.moveNext()) {
      throw ArgumentError(
        'Boolean value count does not match the concrete tensor shape.',
      );
    }
    normalized[index] = iterator.current ? 1 : 0;
  }
  if (iterator.moveNext()) {
    throw ArgumentError(
      'Boolean value count does not match the concrete tensor shape.',
    );
  }
  return (checkedShape, normalized);
}

OrtTypeInfo _concreteTensorType(OrtTensorInfo info) => OrtTypeInfo(
  kind: OrtValueKind.tensor,
  tensorElementType: info.elementType,
  dimensions: <OrtDimension>[
    for (final int dimension in info.shape.dimensions)
      OrtDimension.fixed(dimension),
  ],
);

OrtTensor createCopiedOrtTensor({
  required OrtRuntime runtime,
  required TypedData values,
  required Iterable<int> shape,
  required OrtTensorElementType elementType,
  required OrtResourceLimits limits,
}) {
  runtime._ensureOpen();
  final OrtShape checkedShape = OrtShape(shape, limits: limits);
  final int bytesPerElement = elementType.fixedStorageBytes!;
  final int requiredBytes = checkedShape.requiredBytes(bytesPerElement);
  if (values.lengthInBytes != requiredBytes) {
    throw ArgumentError(
      'Typed-data length does not match the concrete tensor shape.',
    );
  }
  final Uint8List copiedBytes = Uint8List.fromList(
    values.buffer.asUint8List(values.offsetInBytes, values.lengthInBytes),
  );
  try {
    final Pointer<Void> handle = runtime._nativeApi.createTensorCopy(
      runtime: runtime._nativeHandle,
      bytes: copiedBytes,
      dimensions: checkedShape.dimensions,
      elementType: elementType.nativeValue,
    );
    return _tensorFromNative(
      runtime: runtime,
      handle: handle,
      limits: limits,
      expectedType: elementType,
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

OrtTensor _tensorFromNative({
  required OrtRuntime runtime,
  required Pointer<Void> handle,
  required OrtResourceLimits limits,
  OrtTensorElementType? expectedType,
  OrtShape? expectedShape,
  _ExternalMemoryAccount? sharedMemoryAccount,
}) {
  _ExternalMemoryAccount? memoryAccount;
  try {
    final OrtTensorInfo info = _parseOrtTensorInfo(
      runtime._nativeApi.getTensorInfoJson(handle),
      limits: limits,
    );
    if ((expectedType != null && info.elementType != expectedType) ||
        (expectedShape != null && info.shape != expectedShape)) {
      throw StateError('Native tensor information differs from its request.');
    }
    if (sharedMemoryAccount == null) {
      memoryAccount = _NativeTensorMemoryAccount(
        nativeApi: runtime._nativeApi,
        handle: handle,
        releaseAddress: runtime._nativeApi.valueReleaseAddress,
        externalSize: info.byteLength == 0 ? null : info.byteLength,
      );
    } else {
      sharedMemoryAccount.retain();
      memoryAccount = sharedMemoryAccount;
    }
    return OrtTensor._(
      runtime: runtime,
      handle: handle,
      info: info,
      maximumCopyBytes: limits.maxTensorBytes,
      memoryAccount: memoryAccount,
    );
  } on FormatException catch (cause) {
    memoryAccount?.release();
    runtime._nativeApi.releaseValue(handle);
    throw OrtNativePackagingException(
      operation: 'tensor_info_decode',
      code: _nativeErrorPlatform,
      message: 'The native shim returned incompatible tensor metadata.',
      cause: cause,
    );
  } on StateError catch (cause) {
    memoryAccount?.release();
    runtime._nativeApi.releaseValue(handle);
    throw OrtNativePackagingException(
      operation: 'tensor_info_validate',
      code: _nativeErrorPlatform,
      message: 'The native shim returned invalid tensor metadata.',
      cause: cause,
    );
  } catch (_) {
    memoryAccount?.release();
    runtime._nativeApi.releaseValue(handle);
    rethrow;
  }
}

/// Holds one additional native value reference carrying one external-size
/// charge shared by every Dart facade for the same tensor allocation.
final class _NativeTensorMemoryAccount
    implements _ExternalMemoryAccount, Finalizable {
  _NativeTensorMemoryAccount({
    required FonixNativeApi nativeApi,
    required Pointer<Void> handle,
    required Pointer<NativeFinalizerFunction> releaseAddress,
    required int? externalSize,
  }) : _nativeApi = nativeApi,
       _handle = handle,
       _finalizer = NativeFinalizer(releaseAddress) {
    _nativeApi.retainValue(handle);
    try {
      _finalizer.attach(
        this,
        handle,
        detach: _detachToken,
        externalSize: externalSize,
      );
    } catch (_) {
      _nativeApi.releaseValue(handle);
      rethrow;
    }
  }

  final FonixNativeApi _nativeApi;
  final NativeFinalizer _finalizer;
  final Object _detachToken = Object();
  final Pointer<Void> _handle;
  int _references = 1;
  bool _released = false;

  @override
  void retain() {
    if (_released || _references <= 0) {
      throw StateError('The native tensor memory account has been released.');
    }
    _references += 1;
  }

  @override
  void release() {
    if (_references <= 0) return;
    _references -= 1;
    if (_references != 0) return;
    _released = true;
    _finalizer.detach(_detachToken);
    _nativeApi.releaseValue(_handle);
  }
}

/// Copies [tensor]'s elements as the raw bytes of [elementType].
Uint8List copyOrtTensorTypedBytes(
  OrtTensor tensor,
  OrtTensorElementType elementType,
) => tensor._copyTypedBytes(elementType);
