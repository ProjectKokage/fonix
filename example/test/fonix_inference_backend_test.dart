import 'package:flutter_test/flutter_test.dart';
import 'package:fonix/fonix.dart';
import 'package:fonix_reference/src/fonix_inference_backend.dart';
import 'package:fonix_reference/src/inference_backend.dart';

void main() {
  test('selects linked ORT only for iOS', () {
    expect(
      referenceRuntimeSourceForPlatform(isIOS: true).kind,
      OrtRuntimeSourceKind.linked,
    );
    expect(
      referenceRuntimeSourceForPlatform(isIOS: false).kind,
      OrtRuntimeSourceKind.bundled,
    );
  });

  test('close is idempotent before startup and retires the backend', () async {
    final FonixInferenceBackend backend = FonixInferenceBackend(
      runtimeSource: const OrtRuntimeSource.linked(),
    );

    final Future<void> firstClose = backend.close();
    final Future<void> secondClose = backend.close();

    expect(identical(firstClose, secondClose), isTrue);
    await firstClose;
    await expectLater(
      backend.start(),
      throwsA(
        isA<InferenceBackendFailure>()
            .having(
              (InferenceBackendFailure error) => error.backendUnusable,
              'backendUnusable',
              isTrue,
            )
            .having(
              (InferenceBackendFailure error) => error.summary,
              'summary',
              'The inference backend is already closed.',
            ),
      ),
    );
  });
}
