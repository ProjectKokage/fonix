/// Stable error domains returned by the project-owned native shim.
enum OrtErrorDomain {
  shim,
  loader,
  ortApi,
  ortStatus,
  provider,
  allocation,
  unsupported,
  packaging,
  worker,
}

/// Base class for failures reported by Fonix or ONNX Runtime.
class OrtException implements Exception {
  OrtException({
    required this.operation,
    required this.domain,
    required this.code,
    required this.message,
    this.ortCode,
    Map<String, Object?> context = const <String, Object?>{},
    this.cause,
  }) : context = Map<String, Object?>.unmodifiable(context);

  final String operation;
  final OrtErrorDomain domain;
  final int code;
  final int? ortCode;
  final String message;

  /// Bounded, redacted context suitable for diagnostics.
  final Map<String, Object?> context;
  final Object? cause;

  @override
  String toString() {
    final ort = ortCode == null ? '' : ', ortCode: $ortCode';
    return '$runtimeType($operation, ${domain.name}:$code$ort): $message';
  }
}

class OrtRuntimeNotFoundException extends OrtException {
  OrtRuntimeNotFoundException({
    required super.operation,
    required super.code,
    required super.message,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.loader);
}

class OrtApiIncompatibleException extends OrtException {
  OrtApiIncompatibleException({
    required super.operation,
    required super.code,
    required super.message,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.ortApi);
}

class OrtProviderUnavailableException extends OrtException {
  OrtProviderUnavailableException({
    required super.operation,
    required super.code,
    required super.message,
    super.ortCode,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.provider);
}

/// A strict post-run provider assignment or fallback policy was not met.
class OrtProviderEvidenceException extends OrtException {
  OrtProviderEvidenceException({
    required super.message,
    super.context,
    super.cause,
  }) : super(
         operation: 'provider_evidence_validate',
         domain: OrtErrorDomain.provider,
         code: 1001,
       );
}

class OrtModelLoadException extends OrtException {
  OrtModelLoadException({
    required super.operation,
    required super.code,
    required super.message,
    super.ortCode,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.ortStatus);
}

class OrtInvalidArgumentException extends OrtException {
  OrtInvalidArgumentException({
    required super.operation,
    required super.code,
    required super.message,
    super.ortCode,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.shim);
}

class OrtRunException extends OrtException {
  OrtRunException({
    required super.operation,
    required super.code,
    required super.message,
    super.ortCode,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.ortStatus);
}

/// A valid ONNX value kind that the closed Fonix ABI does not expose.
class OrtUnsupportedValueException extends OrtException {
  OrtUnsupportedValueException({
    required super.operation,
    required super.code,
    required super.message,
    super.ortCode,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.unsupported);
}

/// A tensor or buffer cannot provide a host-accessible leased data view.
class OrtDataAccessException extends OrtException {
  OrtDataAccessException({
    required super.operation,
    required super.code,
    required super.message,
    super.ortCode,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.unsupported);
}

class OrtNativePackagingException extends OrtException {
  OrtNativePackagingException({
    required super.operation,
    required super.code,
    required super.message,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.packaging);
}

/// Base class for long-lived isolate/session orchestration failures.
class OrtWorkerException extends OrtException {
  OrtWorkerException({
    required super.operation,
    required super.code,
    required super.message,
    super.context,
    super.cause,
  }) : super(domain: OrtErrorDomain.worker);
}

class OrtWorkerStartupException extends OrtWorkerException {
  OrtWorkerStartupException({
    required super.message,
    super.context,
    super.cause,
  }) : super(operation: 'worker_startup', code: 2001);
}

class OrtWorkerCrashedException extends OrtWorkerException {
  OrtWorkerCrashedException({
    required super.message,
    super.context,
    super.cause,
  }) : super(operation: 'worker_crash', code: 2002);
}

class OrtWorkerProtocolException extends OrtWorkerException {
  OrtWorkerProtocolException({
    required super.message,
    super.context,
    super.cause,
  }) : super(operation: 'worker_protocol', code: 2003);
}

class OrtWorkerClosedException extends OrtWorkerException {
  OrtWorkerClosedException({
    super.message = 'The isolate session is closed or closing.',
    super.context,
    super.cause,
  }) : super(operation: 'worker_closed', code: 2004);
}

class OrtWorkerQueueFullException extends OrtWorkerException {
  OrtWorkerQueueFullException({required super.message, super.context})
    : super(operation: 'worker_backpressure', code: 2005);
}

class OrtRunCancelledException extends OrtWorkerException {
  OrtRunCancelledException({
    super.message = 'The queued or active worker run was cancelled.',
    super.context,
    super.cause,
  }) : super(operation: 'worker_run_cancel', code: 2006);
}

class OrtWorkerMessageTooLargeException extends OrtWorkerException {
  OrtWorkerMessageTooLargeException({required super.message, super.context})
    : super(operation: 'worker_message_limit', code: 2007);
}

/// A local use-after-dispose error detected before entering FFI.
class OrtDisposedException extends StateError {
  OrtDisposedException(String objectType)
    : super('$objectType has already been disposed.');
}
