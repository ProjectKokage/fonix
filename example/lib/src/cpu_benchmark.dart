import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:fonix/fonix.dart';

const String cpuBenchmarkActivationKey = 'FONIX_CPU_BENCHMARK';
const String cpuBenchmarkResultPrefix = 'FONIX_CPU_BENCHMARK_RESULT=';

const String _modelAsset = 'assets/models/cpu_benchmark_matmul.onnx';
const String _inputAsset = 'assets/models/cpu_benchmark_matmul.input.f32le';
const String _outputAsset = 'assets/models/cpu_benchmark_matmul.output.f32le';
const String _inputName = 'input';
const String _outputName = 'output';
const List<int> _inputShape = <int>[2048, 1024];
const List<int> _outputShape = <int>[2048, 1024];
const int _elementCount = 2048 * 1024;
const int cpuBenchmarkWarmupRuns = 3;
const int cpuBenchmarkMeasuredRuns = 10;

bool desktopCpuBenchmarkEnabled({
  required bool isMacOS,
  required bool isLinux,
  required Map<String, String> environment,
}) => isMacOS != isLinux && environment[cpuBenchmarkActivationKey] == '1';

final class CpuBenchmarkFailure implements Exception {
  const CpuBenchmarkFailure(this.summary);

  final String summary;

  @override
  String toString() => 'CpuBenchmarkFailure: $summary';
}

final class CpuBenchmarkAssets {
  CpuBenchmarkAssets._({
    required this.model,
    required this.inputValues,
    required this.referenceOutput,
  });

  final Uint8List model;
  final Float32List inputValues;
  final Float32List referenceOutput;

  static Future<CpuBenchmarkAssets> load({AssetBundle? assets}) async {
    final AssetBundle bundle = assets ?? rootBundle;
    final List<ByteData> loaded = await Future.wait(<Future<ByteData>>[
      bundle.load(_modelAsset),
      bundle.load(_inputAsset),
      bundle.load(_outputAsset),
    ]);
    final Uint8List model = _ownedBytes(loaded[0]);
    if (model.isEmpty) {
      throw const CpuBenchmarkFailure('The CPU benchmark model is empty.');
    }
    final Float32List input = _decodeFloat32(_ownedBytes(loaded[1]));
    final Float32List output = _decodeFloat32(_ownedBytes(loaded[2]));
    if (input.length != _elementCount || output.length != _elementCount) {
      throw const CpuBenchmarkFailure(
        'A CPU benchmark tensor fixture has an unexpected length.',
      );
    }
    return CpuBenchmarkAssets._(
      model: model,
      inputValues: input,
      referenceOutput: output,
    );
  }

  static Uint8List _ownedBytes(ByteData data) => Uint8List.fromList(
    data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
  );

  static Float32List _decodeFloat32(Uint8List bytes) {
    if (bytes.lengthInBytes % Float32List.bytesPerElement != 0) {
      throw const CpuBenchmarkFailure(
        'A CPU benchmark tensor fixture has an invalid byte count.',
      );
    }
    final ByteData data = ByteData.sublistView(bytes);
    final Float32List values = Float32List(
      bytes.lengthInBytes ~/ Float32List.bytesPerElement,
    );
    for (var index = 0; index < values.length; index += 1) {
      values[index] = data.getFloat32(
        index * Float32List.bytesPerElement,
        Endian.little,
      );
    }
    return values;
  }
}

abstract interface class CpuBenchmarkProbe {
  int runOnce();

  void dispose();
}

typedef CpuBenchmarkProbeFactory =
    CpuBenchmarkProbe Function(CpuBenchmarkAssets assets);

final class CpuBenchmarkResult {
  CpuBenchmarkResult._(List<int> samples)
    : samplesMicroseconds = List<int>.unmodifiable(samples) {
    if (samples.length != cpuBenchmarkMeasuredRuns ||
        samples.any((int value) => value <= 0)) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark returned invalid timing samples.',
      );
    }
  }

  final List<int> samplesMicroseconds;

  Map<String, Object> toMap() {
    final List<int> sorted = List<int>.of(samplesMicroseconds)..sort();
    final int sum = samplesMicroseconds.fold<int>(0, (int a, int b) => a + b);
    final int middle = sorted.length ~/ 2;
    return <String, Object>{
      'schemaVersion': 1,
      'result': 'passed',
      'warmupRuns': cpuBenchmarkWarmupRuns,
      'measuredRuns': samplesMicroseconds.length,
      'minMicroseconds': sorted.first,
      'medianMicroseconds': (sorted[middle - 1] + sorted[middle]) ~/ 2,
      'averageMicroseconds': sum ~/ samplesMicroseconds.length,
      'maxMicroseconds': sorted.last,
      'samplesMicroseconds': samplesMicroseconds,
    };
  }

  String toJsonString() => jsonEncode(toMap());
}

Future<CpuBenchmarkResult> runDesktopCpuBenchmark({
  AssetBundle? assets,
  CpuBenchmarkProbeFactory? createProbe,
}) async {
  final CpuBenchmarkAssets loaded = await CpuBenchmarkAssets.load(
    assets: assets,
  );
  final CpuBenchmarkProbe probe = (createProbe ?? _OrtCpuBenchmarkProbe.open)(
    loaded,
  );
  try {
    for (var index = 0; index < cpuBenchmarkWarmupRuns; index += 1) {
      probe.runOnce();
    }
    return CpuBenchmarkResult._(<int>[
      for (var index = 0; index < cpuBenchmarkMeasuredRuns; index += 1)
        probe.runOnce(),
    ]);
  } finally {
    probe.dispose();
  }
}

final class _OrtCpuBenchmarkProbe implements CpuBenchmarkProbe {
  _OrtCpuBenchmarkProbe._({
    required OrtRuntime runtime,
    required OrtSession session,
    required OrtTensor input,
    required Float32List referenceOutput,
  }) : _runtime = runtime,
       _session = session,
       _input = input,
       _referenceOutput = referenceOutput;

  static _OrtCpuBenchmarkProbe open(CpuBenchmarkAssets assets) {
    OrtRuntime? runtime;
    OrtSession? session;
    OrtTensor? input;
    try {
      runtime = OrtRuntime.open(source: const OrtRuntimeSource.bundled());
      session = OrtSession.fromBytes(
        runtime: runtime,
        modelBytes: assets.model,
        modelId: 'cpu-benchmark-matmul',
        options: OrtSessionOptions(
          graphOptimization: OrtGraphOptimization.all,
          executionMode: OrtExecutionMode.sequential,
          intraOpThreads: 1,
          interOpThreads: 1,
          deterministicCompute: true,
          providers: <OrtExecutionProvider>[OrtExecutionProvider.cpu()],
          fallbackPolicy: OrtFallbackPolicy.report,
          sessionLogId: 'fonix-cpu-benchmark',
        ),
      );
      input = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: assets.inputValues,
        shape: _inputShape,
      );
      return _OrtCpuBenchmarkProbe._(
        runtime: runtime,
        session: session,
        input: input,
        referenceOutput: assets.referenceOutput,
      );
    } catch (_) {
      input?.dispose();
      session?.dispose();
      runtime?.dispose();
      rethrow;
    }
  }

  OrtRuntime? _runtime;
  OrtSession? _session;
  OrtTensor? _input;
  final Float32List _referenceOutput;

  @override
  int runOnce() {
    final OrtSession? session = _session;
    final OrtTensor? input = _input;
    if (session == null || input == null) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark probe is already disposed.',
      );
    }
    OrtRunResult? result;
    final Stopwatch stopwatch = Stopwatch()..start();
    try {
      result = session.run(
        inputs: <String, OrtValue>{_inputName: input},
        outputNames: const <String>[_outputName],
      );
      final OrtTensor output = result.tensor(_outputName);
      if (!_sameShape(output.shape.dimensions, _outputShape) ||
          output.elementType != OrtTensorElementType.float32 ||
          !cpuBenchmarkFloat32BitsMatch(
            output.copyFloat32Data(),
            _referenceOutput,
          )) {
        throw const CpuBenchmarkFailure(
          'The CPU benchmark returned an unexpected output.',
        );
      }
      stopwatch.stop();
      if (stopwatch.elapsedMicroseconds <= 0) {
        throw const CpuBenchmarkFailure(
          'The CPU benchmark clock returned an invalid duration.',
        );
      }
      return stopwatch.elapsedMicroseconds;
    } finally {
      result?.dispose();
    }
  }

  @override
  void dispose() {
    final OrtTensor? input = _input;
    final OrtSession? session = _session;
    final OrtRuntime? runtime = _runtime;
    _input = null;
    _session = null;
    _runtime = null;
    input?.dispose();
    session?.dispose();
    runtime?.dispose();
  }
}

bool cpuBenchmarkFloat32BitsMatch(Float32List actual, Float32List expected) {
  if (actual.length != expected.length) return false;
  final Uint32List actualBits = Uint32List.view(
    actual.buffer,
    actual.offsetInBytes,
    actual.length,
  );
  final Uint32List expectedBits = Uint32List.view(
    expected.buffer,
    expected.offsetInBytes,
    expected.length,
  );
  for (var index = 0; index < actual.length; index += 1) {
    if (!actual[index].isFinite || actualBits[index] != expectedBits[index]) {
      return false;
    }
  }
  return true;
}

bool _sameShape(List<int> actual, List<int> expected) {
  if (actual.length != expected.length) return false;
  for (var index = 0; index < actual.length; index += 1) {
    if (actual[index] != expected[index]) return false;
  }
  return true;
}
