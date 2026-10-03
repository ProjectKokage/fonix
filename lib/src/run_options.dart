part of 'runtime.dart';

/// Ref-counted native run state, including cooperative termination.
final class OrtRunOptions extends _NativeOwner {
  factory OrtRunOptions({required OrtRuntime runtime}) {
    runtime._ensureOpen();
    try {
      final Pointer<Void> handle = runtime._nativeApi.createRunOptions(
        runtime._nativeHandle,
      );
      try {
        return OrtRunOptions._(runtime: runtime, handle: handle);
      } catch (_) {
        runtime._nativeApi.releaseRunOptions(handle);
        rethrow;
      }
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
  }

  OrtRunOptions._({required OrtRuntime runtime, required Pointer<Void> handle})
    : _runtime = runtime,
      super(
        nativeApi: runtime._nativeApi,
        nativeHandle: handle,
        releaseAddress: runtime._nativeApi.runOptionsReleaseAddress,
        release: runtime._nativeApi.releaseRunOptions,
        debugName: 'OrtRunOptions',
      );

  final OrtRuntime _runtime;
  bool _terminationRequested = false;

  bool get isTerminationRequested {
    _ensureOpen();
    return _terminationRequested;
  }

  /// Requests cooperative termination of a run using this object.
  void setTerminate() {
    _ensureOpen();
    if (_terminationRequested) return;
    try {
      _nativeApi.setRunTermination(_nativeHandle);
      _terminationRequested = true;
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
  }

  /// Clears termination only after the prior run has returned.
  void unsetTerminate() {
    _ensureOpen();
    if (!_terminationRequested) return;
    try {
      _nativeApi.unsetRunTermination(_nativeHandle);
      _terminationRequested = false;
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
      );
    }
  }
}

/// Registers [options] in the shim's cancel-token registry and returns the
/// token another isolate may use to request cancellation.
int registerOrtRunCancelToken(OrtRunOptions options) =>
    options._nativeApi.registerCancelToken(options._nativeHandle);

/// Retires [token] and returns whether termination was requested through it.
bool finishOrtRunCancelToken(OrtRunOptions options, int token) =>
    options._nativeApi.finishCancelToken(token);
