import 'dart:io';
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

const String _modelSha256 =
    '19bc0466ef8627df9764b40d947ff2c7cfa978c7daa6952ca9553c700a6dbcf0';

void main() {
  final String? runtimePath = Platform.environment['FONIX_TEST_REAL_ORT_PATH'];

  test(
    'deterministic CPU benchmark MatMul has exact reference output',
    () {
      final String normalizedRuntimePath = p.normalize(
        p.absolute(runtimePath!),
      );
      final String modelPath = p.normalize(
        p.absolute('example', 'assets', 'models', 'cpu_benchmark_matmul.onnx'),
      );
      final Float32List inputValues = _readFloat32LittleEndian(
        p.absolute(
          'example',
          'assets',
          'models',
          'cpu_benchmark_matmul.input.f32le',
        ),
      );
      final Float32List expectedOutput = _readFloat32LittleEndian(
        p.absolute(
          'example',
          'assets',
          'models',
          'cpu_benchmark_matmul.output.f32le',
        ),
      );
      expect(inputValues.length, 2048 * 1024);
      expect(expectedOutput.length, 2048 * 1024);

      final OrtRuntime runtime = OrtRuntime.open(
        source: OrtRuntimeSource.file(
          absolutePath: normalizedRuntimePath,
          allowedRoot: p.dirname(normalizedRuntimePath),
        ),
        logId: 'fonix-cpu-benchmark-fixture-test',
      );
      OrtSession? session;
      OrtTensor? input;
      OrtRunResult? result;
      try {
        session = OrtSession.fromFile(
          runtime: runtime,
          modelPath: modelPath,
          allowedRoot: p.dirname(modelPath),
          modelId: 'cpu-benchmark-matmul-sha256-$_modelSha256',
        );
        expect(session.inputs.single.name, 'input');
        expect(session.outputs.single.name, 'output');
        expect(
          session.inputs.single.type.dimensions.map(
            (OrtDimension dimension) => dimension.value,
          ),
          <int?>[2048, 1024],
        );
        expect(
          session.outputs.single.type.dimensions.map(
            (OrtDimension dimension) => dimension.value,
          ),
          <int?>[2048, 1024],
        );

        input = OrtTensor.fromFloat32List(
          runtime: runtime,
          values: inputValues,
          shape: const <int>[2048, 1024],
        );
        result = session.run(inputs: <String, OrtValue>{'input': input});
        final Float32List actualOutput = result
            .tensor('output')
            .copyFloat32Data();
        expect(actualOutput.length, expectedOutput.length);
        final Uint32List actualBits = Uint32List.view(
          actualOutput.buffer,
          actualOutput.offsetInBytes,
          actualOutput.length,
        );
        final Uint32List expectedBits = Uint32List.view(
          expectedOutput.buffer,
          expectedOutput.offsetInBytes,
          expectedOutput.length,
        );
        for (var index = 0; index < actualOutput.length; index += 1) {
          if (!actualOutput[index].isFinite ||
              actualBits[index] != expectedBits[index]) {
            fail('CPU benchmark output bits differed at index $index.');
          }
        }
      } finally {
        result?.dispose();
        result?.dispose();
        input?.dispose();
        input?.dispose();
        session?.dispose();
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

Float32List _readFloat32LittleEndian(String path) {
  final Uint8List bytes = File(path).readAsBytesSync();
  if (bytes.lengthInBytes % Float32List.bytesPerElement != 0) {
    throw FormatException('Float32 fixture has a partial value: $path');
  }
  final ByteData data = ByteData.sublistView(bytes);
  final Float32List result = Float32List(
    bytes.lengthInBytes ~/ Float32List.bytesPerElement,
  );
  for (var index = 0; index < result.length; index += 1) {
    result[index] = data.getFloat32(
      index * Float32List.bytesPerElement,
      Endian.little,
    );
  }
  return result;
}
