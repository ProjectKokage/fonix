part of 'runtime.dart';

/// A ref-counted native host buffer for copy-controlled tensor reuse.
///
/// Exposes bounded read/write methods rather than a raw pointer or an
/// untracked Dart typed-data view.
final class OrtNativeBuffer implements Disposable, Finalizable {
  factory OrtNativeBuffer.allocate({
    required OrtRuntime runtime,
    required int byteLength,
    int alignment = 64,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    runtime._ensureOpen();
    if (byteLength < 0 || byteLength > limits.maxTensorBytes) {
      throw RangeError.range(
        byteLength,
        0,
        limits.maxTensorBytes,
        'byteLength',
      );
    }
    final int minimumAlignment = sizeOf<Pointer<Void>>();
    if (alignment < minimumAlignment ||
        alignment > 4096 ||
        (alignment & (alignment - 1)) != 0) {
      throw RangeError.value(
        alignment,
        'alignment',
        'must be a power of two from $minimumAlignment through 4096',
      );
    }

    try {
      final Pointer<Void> handle = runtime._nativeApi.allocateBuffer(
        runtime: runtime._nativeHandle,
        byteLength: byteLength,
        alignment: alignment,
      );
      try {
        final int actualLength = runtime._nativeApi.bufferByteLength(handle);
        if (actualLength != byteLength) {
          throw StateError(
            'The native buffer length does not match the requested length.',
          );
        }
        return OrtNativeBuffer._(
          runtime: runtime,
          handle: handle,
          byteLength: byteLength,
          alignment: alignment,
        );
      } catch (_) {
        runtime._nativeApi.releaseBuffer(handle);
        rethrow;
      }
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    } on StateError catch (cause) {
      throw OrtNativePackagingException(
        operation: 'buffer_allocate_validate',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid buffer allocation.',
        cause: cause,
      );
    }
  }

  OrtNativeBuffer._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required int byteLength,
    required int alignment,
  }) : _runtime = runtime,
       _byteLength = byteLength,
       _alignment = alignment,
       _allocation = _NativeBufferAllocation(
         nativeApi: runtime._nativeApi,
         handle: handle,
         releaseAddress: runtime._nativeApi.bufferReleaseAddress,
         externalSize: byteLength == 0 ? null : byteLength,
       );

  final OrtRuntime _runtime;
  final int _byteLength;
  final int _alignment;
  _NativeBufferAllocation? _allocation;

  FonixNativeApi get _nativeApi => _runtime._nativeApi;

  Pointer<Void> get _nativeHandle {
    _ensureOpen();
    return _allocation!.handle;
  }

  _ExternalMemoryAccount get _memoryAccount {
    _ensureOpen();
    return _allocation!;
  }

  @override
  bool get isDisposed => _allocation == null;

  @override
  void dispose() {
    final _NativeBufferAllocation? allocation = _allocation;
    if (allocation == null) return;
    _allocation = null;
    allocation.release();
  }

  void _ensureOpen() {
    if (isDisposed) {
      throw OrtDisposedException('OrtNativeBuffer');
    }
  }

  int get byteLength {
    _ensureOpen();
    return _byteLength;
  }

  int get alignment {
    _ensureOpen();
    return _alignment;
  }

  void write(Uint8List bytes, {int offset = 0}) {
    _ensureOpen();
    _checkRange(offset, bytes.length);
    if (bytes.isEmpty) return;
    try {
      _nativeApi.writeBuffer(_nativeHandle, offset, bytes);
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
  }

  Uint8List read({int offset = 0, int? byteLength}) {
    _ensureOpen();
    final int effectiveLength = byteLength ?? _byteLength - offset;
    _checkRange(offset, effectiveLength);
    if (effectiveLength == 0) return Uint8List(0);
    try {
      return _nativeApi.readBuffer(_nativeHandle, offset, effectiveLength);
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
  }

  /// Returns a mutable native-backed byte view of the complete allocation.
  ///
  /// The view owns a native data lease and remains valid after this buffer or
  /// its runtime is disposed. Its lease is released when the view is garbage
  /// collected. Use [read] when a copied snapshot is preferable.
  Uint8List viewBytes() {
    _ensureOpen();
    final _ExternalMemoryAccount account = _memoryAccount;
    account.retain();
    FonixNativeDataLease? lease;
    try {
      lease = _nativeApi.acquireBufferData(_nativeHandle);
      validateOrtDataLeaseLengthForTesting(lease.byteLength, _byteLength);
      if (_byteLength == 0) {
        _nativeApi.releaseDataLease(lease.lease);
        account.release();
        return Uint8List(0);
      }
      final Uint8List view = lease.data.cast<Uint8>().asTypedList(_byteLength);
      return _attachDataLease(view, _nativeApi, lease.lease, account);
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
        operation: 'buffer_data_lease',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid buffer data lease.',
      );
    } on StateError {
      if (lease != null) _nativeApi.releaseDataLease(lease.lease);
      account.release();
      throw OrtNativePackagingException(
        operation: 'buffer_data_lease',
        code: _nativeErrorPlatform,
        message: 'The native shim returned an invalid buffer data lease.',
      );
    } catch (_) {
      if (lease != null) _nativeApi.releaseDataLease(lease.lease);
      account.release();
      rethrow;
    }
  }

  void _checkRange(int offset, int length) {
    if (offset < 0 || length < 0 || offset > _byteLength) {
      throw RangeError('Native-buffer range is outside the allocation.');
    }
    if (length > _byteLength - offset) {
      throw RangeError('Native-buffer range is outside the allocation.');
    }
  }
}

abstract interface class _ExternalMemoryAccount {
  void retain();

  void release();
}

final Finalizer<_DataLeaseRetention> _dataLeaseFinalizer =
    Finalizer<_DataLeaseRetention>((_DataLeaseRetention retention) {
      retention.release();
    });

final class _DataLeaseRetention {
  _DataLeaseRetention(this._nativeApi, this._lease, this._account);

  final FonixNativeApi _nativeApi;
  Pointer<Void> _lease;
  _ExternalMemoryAccount? _account;

  void release() {
    final Pointer<Void> lease = _lease;
    if (lease == nullptr) return;
    _lease = nullptr;
    final _ExternalMemoryAccount? account = _account;
    _account = null;
    _nativeApi.releaseDataLease(lease);
    account?.release();
  }
}

T _attachDataLease<T extends TypedData>(
  T view,
  FonixNativeApi nativeApi,
  Pointer<Void> lease,
  _ExternalMemoryAccount account,
) {
  final _DataLeaseRetention retention = _DataLeaseRetention(
    nativeApi,
    lease,
    account,
  );
  _dataLeaseFinalizer.attach(view, retention);
  return view;
}

/// Exercises native lease-length validation before creating a TypedData view.
///
/// This entry point is intentionally omitted from `package:fonix/fonix.dart`.
void validateOrtDataLeaseLengthForTesting(int actual, int expected) {
  if (actual < 0 || expected < 0 || actual != expected) {
    throw const FormatException('Native data lease length is invalid.');
  }
}

/// Owns the buffer reference that carries the VM's single external-size charge.
///
/// Buffer-backed tensors retain this Dart token as well as the native tensor's
/// own C-side buffer reference. The external allocation therefore remains
/// accounted after the buffer facade is disposed without charging every alias.
final class _NativeBufferAllocation
    implements _ExternalMemoryAccount, Finalizable {
  _NativeBufferAllocation({
    required FonixNativeApi nativeApi,
    required Pointer<Void> handle,
    required Pointer<NativeFinalizerFunction> releaseAddress,
    required int? externalSize,
  }) : _nativeApi = nativeApi,
       _handle = handle,
       _finalizer = NativeFinalizer(releaseAddress) {
    _finalizer.attach(
      this,
      handle,
      detach: _detachToken,
      externalSize: externalSize,
    );
  }

  final FonixNativeApi _nativeApi;
  final NativeFinalizer _finalizer;
  final Object _detachToken = Object();
  Pointer<Void> _handle;
  int _references = 1;

  Pointer<Void> get handle {
    if (_handle == nullptr) {
      throw StateError('The native buffer allocation has been released.');
    }
    return _handle;
  }

  @override
  void retain() {
    if (_handle == nullptr || _references <= 0) {
      throw StateError('The native buffer allocation has been released.');
    }
    _references += 1;
  }

  @override
  void release() {
    if (_references <= 0) return;
    _references -= 1;
    if (_references != 0) return;
    final Pointer<Void> handle = _handle;
    _handle = nullptr;
    _finalizer.detach(_detachToken);
    _nativeApi.releaseBuffer(handle);
  }
}
