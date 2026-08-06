import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:fonix/fonix.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  final String? runtimePath = Platform.environment['FONIX_TEST_REAL_ORT_PATH'];

  test(
    'real ORT run profile produces closed CPU assignment evidence',
    () {
      final Directory temporary = Directory.systemTemp.createTempSync(
        'fonix-provider-profile-',
      );
      OrtRuntime? runtime;
      OrtSession? session;
      OrtTensor? input;
      OrtRunResult? result;
      try {
        runtime = OrtRuntime.open(
          source: OrtRuntimeSource.file(
            absolutePath: p.normalize(p.absolute(runtimePath!)),
            allowedRoot: p.dirname(p.normalize(p.absolute(runtimePath))),
          ),
        );
        session = OrtSession.fromFile(
          runtime: runtime,
          modelPath: p.normalize(p.absolute('test', 'fixtures', 'mul_1.onnx')),
          allowedRoot: p.normalize(p.absolute('test', 'fixtures')),
          options: OrtSessionOptions(
            artifactRoot: temporary.path,
            providers: <OrtExecutionProvider>[
              OrtExecutionProvider.cpu(
                requirement: OrtProviderRequirement.requireFullAssignment,
              ),
            ],
            fallbackPolicy: OrtFallbackPolicy.rejectAny,
          ),
        );
        input = OrtTensor.fromFloat32List(
          runtime: runtime,
          values: Float32List.fromList(<double>[1, 2, 3, 4, 5, 6]),
          shape: const <int>[3, 2],
        );
        result = session.run(inputs: <String, OrtValue>{'X': input});
        expect(result.tensor('Y').copyFloat32Data(), <double>[
          1,
          4,
          9,
          16,
          25,
          36,
        ]);
        final OrtProviderRunEvidence evidence = result.providerEvidence!;
        expect(evidence.nodeExecutionCount, 1);
        expect(evidence.isFullyAssignedTo('cpu'), isTrue);
        expect(
          result.diagnostics.providers
              .singleWhere(
                (OrtProviderDiagnostics value) => value.wrapperId == 'cpu',
              )
              .active,
          isTrue,
        );
        expect(
          result.providerDiagnostics
              .singleWhere(
                (OrtProviderDiagnostics value) => value.wrapperId == 'cpu',
              )
              .active,
          isTrue,
        );
        result.dispose();
        result = null;
        session.dispose();
        session = null;

        expect(temporary.listSync(followLinks: false), isEmpty);
      } finally {
        result?.dispose();
        input?.dispose();
        session?.dispose();
        runtime?.dispose();
        temporary.deleteSync(recursive: true);
      }
    },
    skip: runtimePath == null
        ? 'Set FONIX_TEST_REAL_ORT_PATH to the exact ORT runtime.'
        : false,
  );

  test(
    'real CoreML run produces full-assignment evidence and CPU parity',
    () {
      final Directory temporary = Directory.systemTemp.createTempSync(
        'fonix-coreml-profile-',
      );
      OrtRuntime? runtime;
      OrtSession? session;
      OrtTensor? input;
      OrtRunResult? result;
      try {
        final File model = File(
          p.normalize(p.absolute('test', 'fixtures', 'mul_1.onnx')),
        );
        final OrtExecutionProvider typedCoreMl = OrtExecutionProvider.coreMl(
          computeUnits: OrtCoreMlComputeUnits.cpuOnly,
          requireStaticInputShapes: true,
          cache: OrtCoreMlCacheConfiguration(
            rootDirectory: temporary.path,
            modelSha256: sha256.convert(model.readAsBytesSync()).toString(),
            applicationSchema: 'provider-profile-test-v1',
          ),
          requirement: OrtProviderRequirement.requireFullAssignment,
        );
        runtime = OrtRuntime.open(
          source: OrtRuntimeSource.file(
            absolutePath: p.normalize(p.absolute(runtimePath!)),
            allowedRoot: p.dirname(p.normalize(p.absolute(runtimePath))),
          ),
        );
        session = OrtSession.fromFile(
          runtime: runtime,
          modelPath: model.path,
          allowedRoot: p.normalize(p.absolute('test', 'fixtures')),
          options: OrtSessionOptions(
            fallbackPolicy: OrtFallbackPolicy.rejectAny,
            artifactRoot: temporary.path,
            providers: <OrtExecutionProvider>[
              typedCoreMl,
              OrtExecutionProvider.cpu(),
            ],
          ),
        );
        input = OrtTensor.fromFloat32List(
          runtime: runtime,
          values: Float32List.fromList(<double>[1, 2, 3, 4, 5, 6]),
          shape: const <int>[3, 2],
        );
        result = session.run(inputs: <String, OrtValue>{'X': input});
        expect(result.tensor('Y').copyFloat32Data(), <double>[
          1,
          4,
          9,
          16,
          25,
          36,
        ]);
        final OrtProviderRunEvidence evidence = result.providerEvidence!;
        expect(evidence.nodeExecutionsByProvider, <String, int>{'coreml': 1});
        final OrtProviderDiagnostics coreMl = result.providerDiagnostics
            .singleWhere(
              (OrtProviderDiagnostics value) => value.wrapperId == 'coreml',
            );
        expect(coreMl.active, isTrue);
        final String cacheIdentity = coreMl.options['fonix_cache_identity']!;
        expect(cacheIdentity, matches(RegExp(r'^[0-9a-f]{64}$')));
        expect(
          Directory(p.join(temporary.path, cacheIdentity)).existsSync(),
          isTrue,
        );
        result.dispose();
        result = null;
        session.dispose();
        session = null;

        expect(
          temporary
              .listSync(followLinks: false)
              .where(
                (FileSystemEntity entry) =>
                    p.basename(entry.path).startsWith('fonix-run-profile-'),
              ),
          isEmpty,
        );
      } finally {
        result?.dispose();
        input?.dispose();
        session?.dispose();
        runtime?.dispose();
        temporary.deleteSync(recursive: true);
      }
    },
    skip: runtimePath == null || !Platform.isMacOS
        ? 'Set FONIX_TEST_REAL_ORT_PATH to a CoreML-enabled macOS runtime.'
        : false,
  );

  test(
    'real worker preserves typed CoreML cache and assignment policy',
    () async {
      final Directory temporary = Directory.systemTemp.createTempSync(
        'fonix-coreml-worker-profile-',
      );
      OrtIsolateSession? worker;
      try {
        final File model = File(
          p.normalize(p.absolute('test', 'fixtures', 'mul_1.onnx')),
        );
        worker = await OrtIsolateSession.spawn(
          runtimeSource: OrtRuntimeSource.file(
            absolutePath: p.normalize(p.absolute(runtimePath!)),
            allowedRoot: p.dirname(p.normalize(p.absolute(runtimePath))),
          ),
          model: OrtModelSource.file(
            absolutePath: model.path,
            allowedRoot: model.parent.path,
            modelId: 'coreml-worker-cache-contract',
          ),
          options: OrtSessionOptions(
            fallbackPolicy: OrtFallbackPolicy.rejectAny,
            artifactRoot: temporary.path,
            providers: <OrtExecutionProvider>[
              OrtExecutionProvider.coreMl(
                computeUnits: OrtCoreMlComputeUnits.cpuOnly,
                requireStaticInputShapes: true,
                cache: OrtCoreMlCacheConfiguration(
                  rootDirectory: temporary.path,
                  modelSha256: sha256
                      .convert(model.readAsBytesSync())
                      .toString(),
                  applicationSchema: 'provider-worker-test-v1',
                ),
                requirement: OrtProviderRequirement.requireFullAssignment,
              ),
              OrtExecutionProvider.cpu(),
            ],
          ),
        );
        expect(worker.diagnostics.runtimeVersion, '1.27.1');
        expect(worker.diagnostics.providers.first.active, isNull);
        final OrtIsolateRunResult result = await worker.run(
          inputs: <String, OrtIsolateValue>{
            'X': OrtIsolateTensor.fromFloat32List(
              values: Float32List.fromList(<double>[1, 2, 3, 4, 5, 6]),
              shape: const <int>[3, 2],
            ),
          },
        );
        expect(result.tensor('Y').copyFloat32Data(), <double>[
          1,
          4,
          9,
          16,
          25,
          36,
        ]);
        expect(result.providerEvidence?.isFullyAssignedTo('coreml'), isTrue);
        expect(
          result.diagnostics.providers
              .singleWhere(
                (OrtProviderDiagnostics value) => value.wrapperId == 'coreml',
              )
              .active,
          isTrue,
        );
        final OrtProviderDiagnostics coreMl = result.providerDiagnostics
            .singleWhere(
              (OrtProviderDiagnostics value) => value.wrapperId == 'coreml',
            );
        final String cacheIdentity = coreMl.options['fonix_cache_identity']!;
        expect(cacheIdentity, matches(RegExp(r'^[0-9a-f]{64}$')));
        expect(
          Directory(p.join(temporary.path, cacheIdentity)).existsSync(),
          isTrue,
        );
      } finally {
        await worker?.close();
        temporary.deleteSync(recursive: true);
      }
    },
    skip: runtimePath == null || !Platform.isMacOS
        ? 'Set FONIX_TEST_REAL_ORT_PATH to a CoreML-enabled macOS runtime.'
        : false,
  );
}
