import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  final _RealOrtFixture? fixture = _RealOrtFixture.fromEnvironment();
  if (fixture == null) {
    test(
      'official ONNX Runtime CPU fixture is provisioned',
      () {},
      skip:
          'Set FONIX_TEST_REAL_ORT_PATH to an absolute ONNX Runtime v1.27.1 '
          'library path.',
    );
    return;
  }

  group('official ONNX Runtime CPU inference', () {
    test('runs copied model bytes with strict named metadata and outputs', () {
      final OrtRuntime runtime = fixture.openRuntime();
      OrtSession? session;
      OrtTensor? input;
      OrtRunResult? result;
      OrtTensor? retainedOutput;
      try {
        final Uint8List modelBytes = fixture.model.readAsBytesSync();
        session = OrtSession.fromBytes(
          runtime: runtime,
          modelBytes: modelBytes,
          modelId: 'ort-v1.27.1-mul-1',
        );
        final OrtDiagnostics runtimeDiagnostics = runtime.diagnostics;
        expect(runtimeDiagnostics.runtimeMode, OrtRuntimeMode.file);
        expect(runtimeDiagnostics.runtimeOwner, OrtRuntimeOwner.application);
        expect(runtimeDiagnostics.artifactFlavor, 'external');
        expect(runtimeDiagnostics.artifactSha256, isNull);
        expect(
          jsonEncode(runtimeDiagnostics.toJson()),
          isNot(contains(fixture.runtimePath)),
        );
        final OrtDiagnostics sessionDiagnostics = session.diagnostics;
        expect(sessionDiagnostics.modelId, 'ort-v1.27.1-mul-1');
        expect(sessionDiagnostics.session?.executionMode, 'sequential');
        final OrtProviderDiagnostics cpuDiagnostics = sessionDiagnostics
            .providers
            .firstWhere(
              (OrtProviderDiagnostics provider) => provider.wrapperId == 'cpu',
            );
        expect(cpuDiagnostics.registered, isTrue);
        expect(cpuDiagnostics.active, isNull);
        modelBytes.fillRange(0, modelBytes.length, 0);

        expect(session.inputs.map((OrtValueInfo value) => value.name), <String>[
          'X',
        ]);
        expect(
          session.outputs.map((OrtValueInfo value) => value.name),
          <String>['Y'],
        );
        expect(
          session.inputs.single.type.tensorElementType,
          OrtTensorElementType.float32,
        );
        expect(
          session.inputs.single.type.dimensions.map(
            (OrtDimension dimension) => dimension.value,
          ),
          <int?>[3, 2],
        );

        final Float32List callerValues = Float32List.fromList(<double>[
          1,
          2,
          3,
          4,
          5,
          6,
        ]);
        input = OrtTensor.fromFloat32List(
          runtime: runtime,
          values: callerValues,
          shape: const <int>[3, 2],
        );
        callerValues.fillRange(0, callerValues.length, 99);

        final OrtRunResult currentResult = session.run(
          inputs: <String, OrtTensor>{'X': input},
          outputNames: const <String>['Y'],
        );
        result = currentResult;
        final OrtDiagnostics runDiagnostics = currentResult.diagnostics;
        expect(runDiagnostics.modelId, 'ort-v1.27.1-mul-1');
        expect(runDiagnostics.session?.fallbackPolicy, 'report');
        expect(currentResult.outputNames, <String>['Y']);
        expect(currentResult.outputs.keys, <String>['Y']);
        expect(currentResult.tensor('Y').copyFloat32Data(), <double>[
          1,
          4,
          9,
          16,
          25,
          36,
        ]);
        expect(
          () => currentResult.outputs['other'] = input!,
          throwsUnsupportedError,
        );

        retainedOutput = currentResult.retainTensor('Y');
        session.dispose();
        expect(currentResult.tensor('Y').copyFloat32Data(), <double>[
          1,
          4,
          9,
          16,
          25,
          36,
        ]);
        currentResult.dispose();
        expect(runDiagnostics.runtimeVersion, '1.27.1');
        expect(() => result!.tensor('Y'), throwsA(isA<OrtDisposedException>()));
        runtime.dispose();
        expect(retainedOutput.copyFloat32Data(), <double>[1, 4, 9, 16, 25, 36]);
      } finally {
        retainedOutput?.dispose();
        retainedOutput?.dispose();
        result?.dispose();
        input?.dispose();
        session?.dispose();
        runtime.dispose();
      }
    });

    test(
      'children remain valid after runtime disposal and dispose either way',
      () {
        final OrtRuntime runtime = fixture.openRuntime();
        final OrtSession session = OrtSession.fromFile(
          runtime: runtime,
          modelPath: fixture.model.path,
          allowedRoot: fixture.model.parent.path,
        );
        final OrtTensor input = OrtTensor.fromFloat32List(
          runtime: runtime,
          values: Float32List.fromList(<double>[1, 1, 1, 1, 1, 1]),
          shape: const <int>[3, 2],
        );
        final OrtRunOptions runOptions = OrtRunOptions(runtime: runtime);
        OrtRunResult? result;
        try {
          runtime.dispose();
          runOptions.setTerminate();
          expect(runOptions.isTerminationRequested, isTrue);
          runOptions.unsetTerminate();
          expect(runOptions.isTerminationRequested, isFalse);

          result = session.run(
            inputs: <String, OrtTensor>{'X': input},
            runOptions: runOptions,
          );
          final OrtTensor borrowed = result.tensor('Y');
          expect(borrowed.copyFloat32Data(), <double>[1, 2, 3, 4, 5, 6]);

          borrowed.dispose();
          expect(
            () => borrowed.copyFloat32Data(),
            throwsA(isA<OrtDisposedException>()),
          );
          result.dispose();
          result.dispose();
        } finally {
          result?.dispose();
          runOptions.dispose();
          runOptions.dispose();
          input.dispose();
          session.dispose();
          session.dispose();
          runtime.dispose();
        }
      },
    );

    test('native buffers are bounded, copied, and retained by tensors', () {
      final OrtRuntime runtime = fixture.openRuntime();
      OrtNativeBuffer? buffer;
      OrtNativeBuffer? emptyBuffer;
      OrtTensor? tensor;
      OrtTensor? tensorAlias;
      OrtTensor? emptyTensor;
      try {
        final Float32List values = Float32List.fromList(<double>[
          1,
          2,
          3,
          4,
          5,
          6,
        ]);
        final Uint8List bytes = Uint8List.fromList(
          values.buffer.asUint8List(values.offsetInBytes, values.lengthInBytes),
        );
        buffer = OrtNativeBuffer.allocate(
          runtime: runtime,
          byteLength: bytes.length,
        );
        expect(
          () => OrtNativeBuffer.allocate(
            runtime: runtime,
            byteLength: 1,
            alignment: 3,
          ),
          throwsRangeError,
        );
        buffer.write(bytes);
        expect(buffer.read(), bytes);
        tensor = OrtTensor.fromNativeBuffer(
          buffer: buffer,
          shape: const <int>[3, 2],
          elementType: OrtTensorElementType.float32,
        );
        expect(
          () => OrtTensor.fromNativeBuffer(
            buffer: buffer!,
            shape: const <int>[1],
            elementType: OrtTensorElementType.float32,
            byteOffset: 1,
          ),
          throwsRangeError,
        );
        tensorAlias = OrtTensor.fromNativeBuffer(
          buffer: buffer,
          shape: const <int>[3, 2],
          elementType: OrtTensorElementType.float32,
        );
        buffer.dispose();
        expect(tensor.copyFloat32Data(), values);
        tensor.dispose();
        expect(tensorAlias.copyFloat32Data(), values);

        emptyBuffer = OrtNativeBuffer.allocate(runtime: runtime, byteLength: 0);
        expect(emptyBuffer.read(), isEmpty);
        emptyTensor = OrtTensor.fromNativeBuffer(
          buffer: emptyBuffer,
          shape: const <int>[0],
          elementType: OrtTensorElementType.float32,
        );
        emptyBuffer.dispose();
        runtime.dispose();
        expect(emptyTensor.copyFloat32Data(), isEmpty);
      } finally {
        emptyTensor?.dispose();
        tensorAlias?.dispose();
        tensor?.dispose();
        emptyBuffer?.dispose();
        buffer?.dispose();
        runtime.dispose();
      }
    });

    test('float16 and bfloat16 tensors preserve every raw storage bit', () {
      final OrtRuntime runtime = fixture.openRuntime();
      final Uint16List float16CallerBits = Uint16List.fromList(<int>[
        0x0000,
        0x8000,
        0x0001,
        0x3c00,
        0x7bff,
        0x7c00,
        0x7e55,
      ]);
      final Uint16List bfloat16CallerBits = Uint16List.fromList(<int>[
        0x0000,
        0x8000,
        0x0001,
        0x3f80,
        0x7f7f,
        0x7f80,
        0x7fc5,
      ]);
      final List<int> expectedFloat16 = List<int>.of(float16CallerBits);
      final List<int> expectedBFloat16 = List<int>.of(bfloat16CallerBits);
      final OrtTensor float16 = OrtTensor.fromFloat16Bits(
        runtime: runtime,
        bits: float16CallerBits,
        shape: <int>[float16CallerBits.length],
      );
      final OrtTensor bfloat16 = OrtTensor.fromBFloat16Bits(
        runtime: runtime,
        bits: bfloat16CallerBits,
        shape: <int>[bfloat16CallerBits.length],
      );
      try {
        float16CallerBits.fillRange(0, float16CallerBits.length, 0xffff);
        bfloat16CallerBits.fillRange(0, bfloat16CallerBits.length, 0xffff);
        expect(float16.copyFloat16Bits(), expectedFloat16);
        expect(bfloat16.copyBFloat16Bits(), expectedBFloat16);
        expect(float16.kind, OrtValueKind.tensor);
        expect(float16.type.tensorElementType, OrtTensorElementType.float16);
        expect(bfloat16.type.tensorElementType, OrtTensorElementType.bfloat16);
        expect(() => float16.copyUint16Data(), throwsStateError);

        runtime.dispose();
        expect(float16.copyFloat16Bits(), expectedFloat16);
        expect(bfloat16.copyBFloat16Bits(), expectedBFloat16);
      } finally {
        bfloat16.dispose();
        float16.dispose();
        runtime.dispose();
      }
    });

    test('invalid external-data byte models fail with a typed load error', () {
      final OrtRuntime runtime = fixture.openRuntime();
      try {
        final OrtModelSource source = OrtModelSource.bytes(
          Uint8List.fromList(<int>[1]),
          externalData: <String, Uint8List>{
            'weights.bin': Uint8List.fromList(<int>[2]),
          },
        );
        expect(
          () => OrtSession.fromModel(runtime: runtime, model: source),
          throwsA(isA<OrtModelLoadException>()),
        );
      } finally {
        runtime.dispose();
      }
    });

    test('unknown-rank metadata accepts multiple concrete input ranks', () {
      final OrtRuntime runtime = fixture.openRuntime();
      final OrtSession session = OrtSession.fromFile(
        runtime: runtime,
        modelPath: fixture.unknownRankModel.path,
        allowedRoot: fixture.unknownRankModel.parent.path,
      );
      try {
        expect(session.inputs.single.name, 'unknown_input');
        expect(session.outputs.single.name, 'unknown_output');
        expect(session.inputs.single.type.hasShape, isFalse);
        expect(session.inputs.single.type.dimensions, isEmpty);

        for (final (List<int> shape, List<double> values)
            in <(List<int>, List<double>)>[
              (<int>[3], <double>[1, 2, 3]),
              (<int>[2, 2], <double>[4, 5, 6, 7]),
            ]) {
          final OrtTensor input = OrtTensor.fromFloat32List(
            runtime: runtime,
            values: Float32List.fromList(values),
            shape: shape,
          );
          OrtRunResult? result;
          try {
            result = session.run(
              inputs: <String, OrtTensor>{'unknown_input': input},
            );
            expect(result.tensor('unknown_output').shape.dimensions, shape);
            expect(result.tensor('unknown_output').copyFloat32Data(), values);
          } finally {
            result?.dispose();
            input.dispose();
          }
        }
      } finally {
        session.dispose();
        runtime.dispose();
      }
    });

    test('policies and invalid models fail closed with typed errors', () {
      final OrtRuntime runtime = fixture.openRuntime();
      final Uint8List modelBytes = fixture.model.readAsBytesSync();
      try {
        expect(
          () => OrtSessionOptions(
            providers: <OrtExecutionProvider>[
              OrtExecutionProvider.cpu(
                requirement: OrtProviderRequirement.requireActive,
              ),
            ],
          ),
          throwsArgumentError,
        );
        expect(
          () => OrtSession.fromBytes(
            runtime: runtime,
            modelBytes: modelBytes,
            options: OrtSessionOptions(
              fallbackPolicy: OrtFallbackPolicy.allow,
              providers: <OrtExecutionProvider>[OrtExecutionProvider.cuda()],
            ),
          ),
          throwsA(isA<OrtProviderUnavailableException>()),
        );
        expect(
          () => OrtSessionOptions(fallbackPolicy: OrtFallbackPolicy.rejectAny),
          throwsArgumentError,
        );
        expect(
          () => OrtSessionOptions(
            providers: <OrtExecutionProvider>[OrtExecutionProvider.cuda()],
          ),
          throwsArgumentError,
        );
        expect(
          () => OrtSession.fromBytes(
            runtime: runtime,
            modelBytes: Uint8List.fromList(<int>[1, 2, 3]),
          ),
          throwsA(isA<OrtModelLoadException>()),
        );

        final String privateMissingModel = p.join(
          fixture.model.parent.path,
          'private-person-missing-model.onnx',
        );
        expect(
          () => OrtSession.fromFile(
            runtime: runtime,
            modelPath: privateMissingModel,
            allowedRoot: fixture.model.parent.path,
          ),
          throwsA(
            isA<OrtModelLoadException>().having(
              (OrtModelLoadException error) => error.message,
              'message',
              isNot(contains(privateMissingModel)),
            ),
          ),
        );
      } finally {
        runtime.dispose();
      }
    });

    test('named run validation rejects mismatches before native execution', () {
      final OrtRuntime runtime = fixture.openRuntime();
      final OrtSession session = OrtSession.fromFile(
        runtime: runtime,
        modelPath: fixture.model.path,
      );
      final OrtTensor valid = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List(6),
        shape: const <int>[3, 2],
      );
      final OrtTensor wrongShape = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: Float32List(6),
        shape: const <int>[6],
      );
      try {
        expect(
          () => session.run(inputs: const <String, OrtTensor>{}),
          throwsArgumentError,
        );
        expect(
          () => session.run(inputs: <String, OrtTensor>{'X': wrongShape}),
          throwsArgumentError,
        );
        expect(
          () => session.run(
            inputs: <String, OrtTensor>{'X': valid},
            outputNames: const <String>['private-unknown-output'],
          ),
          throwsArgumentError,
        );
      } finally {
        wrongShape.dispose();
        valid.dispose();
        session.dispose();
        runtime.dispose();
      }
    });
  });
}

final class _RealOrtFixture {
  const _RealOrtFixture({
    required this.runtimePath,
    required this.model,
    required this.unknownRankModel,
  });

  factory _RealOrtFixture._fromEnvironmentChecked(String runtimePath) {
    if (runtimePath.isEmpty ||
        !p.isAbsolute(runtimePath) ||
        !File(runtimePath).existsSync()) {
      throw const FormatException(
        'FONIX_TEST_REAL_ORT_PATH must name an absolute, existing file.',
      );
    }
    final File model = File(
      p.normalize(p.absolute('test', 'fixtures', 'mul_1.onnx')),
    );
    final File unknownRankModel = File(
      p.normalize(p.absolute('test', 'fixtures', 'unknown_rank_identity.onnx')),
    );
    if (!model.existsSync() || !unknownRankModel.existsSync()) {
      throw const FormatException(
        'A committed CPU inference fixture is missing.',
      );
    }
    return _RealOrtFixture(
      runtimePath: p.normalize(runtimePath),
      model: model,
      unknownRankModel: unknownRankModel,
    );
  }

  static _RealOrtFixture? fromEnvironment() {
    const String key = 'FONIX_TEST_REAL_ORT_PATH';
    if (!Platform.environment.containsKey(key)) {
      return null;
    }
    return _RealOrtFixture._fromEnvironmentChecked(
      Platform.environment[key] ?? '',
    );
  }

  final String runtimePath;
  final File model;
  final File unknownRankModel;

  OrtRuntime openRuntime() => OrtRuntime.open(
    source: OrtRuntimeSource.file(
      absolutePath: runtimePath,
      allowedRoot: p.dirname(runtimePath),
    ),
    logId: 'fonix-cpu-test',
  );
}
