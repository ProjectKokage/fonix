import 'package:fonix/fonix.dart';
import 'package:fonix/src/isolate_protocol.dart'
    show decodeWorkerOrtError, encodeWorkerOrtError;
import 'package:test/test.dart';

void main() {
  final Map<String, Object?> context = <String, Object?>{'requestId': 7};
  final List<OrtException> errors = <OrtException>[
    OrtRuntimeNotFoundException(
      operation: 'runtime_open',
      code: 7,
      message: 'missing runtime',
      context: context,
    ),
    OrtApiIncompatibleException(
      operation: 'runtime_open',
      code: 3,
      message: 'api floor',
      context: context,
    ),
    OrtProviderUnavailableException(
      operation: 'session_create',
      code: 20,
      message: 'provider',
      ortCode: 9,
      context: context,
    ),
    OrtProviderEvidenceException(message: 'evidence', context: context),
    OrtModelLoadException(
      operation: 'session_create',
      code: 15,
      message: 'model',
      ortCode: 2,
      context: context,
    ),
    OrtInvalidArgumentException(
      operation: 'session_run',
      code: 1,
      message: 'argument',
      context: context,
    ),
    OrtRunException(
      operation: 'session_run',
      code: 17,
      message: 'run',
      ortCode: 1,
      context: context,
    ),
    OrtUnsupportedValueException(
      operation: 'value_kind',
      code: 22,
      message: 'value',
      context: context,
    ),
    OrtDataAccessException(
      operation: 'data_lease',
      code: 24,
      message: 'lease',
      context: context,
    ),
    OrtNativePackagingException(
      operation: 'status',
      code: 13,
      message: 'packaging',
      context: context,
    ),
    OrtWorkerException(
      operation: 'worker_custom',
      code: 2999,
      message: 'worker',
      context: context,
    ),
    OrtWorkerStartupException(message: 'startup', context: context),
    OrtWorkerCrashedException(message: 'crash', context: context),
    OrtWorkerProtocolException(message: 'protocol', context: context),
    OrtWorkerClosedException(message: 'closed', context: context),
    OrtWorkerQueueFullException(message: 'full', context: context),
    OrtRunCancelledException(message: 'cancelled', context: context),
    OrtWorkerMessageTooLargeException(message: 'large', context: context),
    OrtException(
      operation: 'other',
      domain: OrtErrorDomain.allocation,
      code: 10,
      message: 'allocation',
      context: context,
    ),
  ];

  for (final OrtException error in errors) {
    test('${error.runtimeType} keeps its type across the worker boundary', () {
      final OrtException decoded = decodeWorkerOrtError(
        encodeWorkerOrtError(error),
      );

      expect(decoded.runtimeType, error.runtimeType);
      expect(decoded.operation, error.operation);
      expect(decoded.domain, error.domain);
      expect(decoded.code, error.code);
      expect(decoded.ortCode, error.ortCode);
      expect(decoded.message, error.message);
      expect(decoded.context, error.context);
    });
  }

  test('an error whose fields contradict its kind is rejected', () {
    final Map<String, Object?> encoded = encodeWorkerOrtError(
      OrtWorkerMessageTooLargeException(message: 'large'),
    );

    expect(
      () => decodeWorkerOrtError(<String, Object?>{...encoded, 'code': 1}),
      throwsFormatException,
    );
    expect(
      () => decodeWorkerOrtError(<String, Object?>{
        ...encoded,
        'domain': OrtErrorDomain.shim.name,
      }),
      throwsFormatException,
    );
    expect(
      () => decodeWorkerOrtError(<String, Object?>{
        ...encoded,
        'kind': 'unknownKind',
      }),
      throwsFormatException,
    );
  });
}
