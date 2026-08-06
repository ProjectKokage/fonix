part of 'runtime.dart';

/// Closed base type for owned ONNX values.
///
/// Construction is closed to numeric tensors, string tensors, sequences, maps,
/// and optionals whose recursive native ownership contracts are implemented.
/// Unsupported ONNX value kinds continue to fail before reaching application
/// state.
sealed class OrtValue implements Disposable, Finalizable {
  const OrtValue();

  OrtRuntime get _runtime;

  FonixNativeApi get _nativeApi;

  Pointer<Void> get _nativeHandle;

  OrtValueKind get kind;

  /// Fully copied recursive type information for this concrete value.
  OrtTypeInfo get type;

  /// Returns an independently owned alias of this complete value tree.
  ///
  /// The caller must dispose the returned value. It remains valid after this
  /// value, its parent composite, run result, session, or runtime is disposed.
  OrtValue retain();
}
