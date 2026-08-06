import 'dart:io';
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  final String? runtimePath = Platform.environment['FONIX_TEST_REAL_ORT_PATH'];

  test(
    'deterministic XNNPACK MatMul fixture has exact CPU reference output',
    () {
      final String normalizedRuntimePath = p.normalize(
        p.absolute(runtimePath!),
      );
      final String modelPath = p.normalize(
        p.absolute('example', 'assets', 'models', 'xnnpack_matmul.onnx'),
      );
      final OrtRuntime runtime = OrtRuntime.open(
        source: OrtRuntimeSource.file(
          absolutePath: normalizedRuntimePath,
          allowedRoot: p.dirname(normalizedRuntimePath),
        ),
        logId: 'fonix-xnnpack-fixture-test',
      );
      OrtSession? session;
      OrtTensor? input;
      OrtRunResult? result;
      try {
        session = OrtSession.fromFile(
          runtime: runtime,
          modelPath: modelPath,
          allowedRoot: p.dirname(modelPath),
          modelId:
              'xnnpack-matmul-sha256-'
              'c75aaa93b0e1ae09e0bb12ddee5786c2fda803dfa5f565234e2b65e238623482',
        );
        expect(session.inputs.single.name, 'input');
        expect(session.outputs.single.name, 'output');
        expect(
          session.inputs.single.type.dimensions.map(
            (OrtDimension dimension) => dimension.value,
          ),
          <int?>[3, 2],
        );
        expect(
          session.outputs.single.type.dimensions.map(
            (OrtDimension dimension) => dimension.value,
          ),
          <int?>[3, 2],
        );

        input = OrtTensor.fromFloat32List(
          runtime: runtime,
          values: Float32List.fromList(<double>[1, 2, 3, 4, 5, 6]),
          shape: const <int>[3, 2],
        );
        result = session.run(inputs: <String, OrtValue>{'input': input});
        expect(result.tensor('output').copyFloat32Data(), <double>[
          7,
          10,
          15,
          22,
          23,
          34,
        ]);
      } finally {
        result?.dispose();
        input?.dispose();
        session?.dispose();
        runtime.dispose();
        runtime.dispose();
      }
    },
    skip: runtimePath == null
        ? 'Set FONIX_TEST_REAL_ORT_PATH to the exact ORT runtime.'
        : false,
  );
}
