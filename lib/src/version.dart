/// The Fonix package version embedded into diagnostics.
const String fonixPackageVersion = '0.1.0-dev.1';

/// The project-owned native shim ABI expected by this Dart package.
const int fonixShimAbiVersion = 1;

/// ONNX Runtime C API versions understood by this shim ABI.
enum OrtApiVersion {
  /// ONNX Runtime C API 27.
  v27(27);

  const OrtApiVersion(this.value);

  /// The integer passed to `OrtApiBase.GetApi`.
  final int value;
}

/// Log severities accepted by ONNX Runtime environments and sessions.
enum OrtLogSeverity {
  verbose(0),
  info(1),
  warning(2),
  error(3),
  fatal(4);

  const OrtLogSeverity(this.nativeValue);

  final int nativeValue;
}
