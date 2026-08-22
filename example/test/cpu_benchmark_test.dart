import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/cpu_benchmark.dart';

const List<String> _assetNames = <String>[
  'assets/models/cpu_benchmark_matmul.onnx',
  'assets/models/cpu_benchmark_matmul.input.f32le',
  'assets/models/cpu_benchmark_matmul.output.f32le',
];

void main() {
  late Map<String, Uint8List> fixtureAssets;

  setUpAll(() {
    fixtureAssets = <String, Uint8List>{
      for (final String name in _assetNames) name: File(name).readAsBytesSync(),
    };
  });

  test('desktop benchmark is an explicit macOS or Linux opt-in', () {
    for (final ({bool isMacOS, bool isLinux}) platform
        in <({bool isMacOS, bool isLinux})>[
          (isMacOS: true, isLinux: false),
          (isMacOS: false, isLinux: true),
        ]) {
      expect(
        desktopCpuBenchmarkEnabled(
          isMacOS: platform.isMacOS,
          isLinux: platform.isLinux,
          environment: const <String, String>{cpuBenchmarkActivationKey: '1'},
        ),
        isTrue,
      );
    }
    for (final Map<String, String> environment in <Map<String, String>>[
      const <String, String>{},
      const <String, String>{cpuBenchmarkActivationKey: '0'},
      const <String, String>{cpuBenchmarkActivationKey: 'true'},
    ]) {
      expect(
        desktopCpuBenchmarkEnabled(
          isMacOS: true,
          isLinux: false,
          environment: environment,
        ),
        isFalse,
      );
    }
    expect(
      desktopCpuBenchmarkEnabled(
        isMacOS: false,
        isLinux: false,
        environment: const <String, String>{cpuBenchmarkActivationKey: '1'},
      ),
      isFalse,
    );
  });

  test('loads the tracked benchmark fixture by shape', () async {
    final CpuBenchmarkAssets assets = await CpuBenchmarkAssets.load(
      assets: _MemoryAssetBundle(fixtureAssets),
    );

    expect(assets.model, isNotEmpty);
    expect(assets.inputValues, hasLength(2048 * 1024));
    expect(assets.referenceOutput, hasLength(2048 * 1024));
  });

  test('rejects a malformed tensor fixture', () async {
    final Map<String, Uint8List> malformed = Map<String, Uint8List>.of(
      fixtureAssets,
    );
    malformed[_assetNames[1]] = Uint8List(3);

    await expectLater(
      CpuBenchmarkAssets.load(assets: _MemoryAssetBundle(malformed)),
      throwsA(isA<CpuBenchmarkFailure>()),
    );
  });

  test('runs fixed warmups and reports ordinary timing samples', () async {
    final _FakeProbe probe = _FakeProbe();

    final CpuBenchmarkResult result = await runDesktopCpuBenchmark(
      assets: _MemoryAssetBundle(fixtureAssets),
      createProbe: (CpuBenchmarkAssets _) => probe,
    );

    expect(probe.runCalls, cpuBenchmarkWarmupRuns + cpuBenchmarkMeasuredRuns);
    expect(probe.disposeCalls, 1);
    expect(result.samplesMicroseconds, <int>[4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
    expect(result.toMap(), <String, Object>{
      'schemaVersion': 1,
      'result': 'passed',
      'warmupRuns': 3,
      'measuredRuns': 10,
      'minMicroseconds': 4,
      'medianMicroseconds': 8,
      'averageMicroseconds': 8,
      'maxMicroseconds': 13,
      'samplesMicroseconds': <int>[4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
    });
    expect(jsonDecode(result.toJsonString()), result.toMap());
  });

  test('disposes the probe when a measured run fails', () async {
    final _FakeProbe probe = _FakeProbe(failAt: 5);

    await expectLater(
      runDesktopCpuBenchmark(
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) => probe,
      ),
      throwsStateError,
    );
    expect(probe.disposeCalls, 1);
  });

  test('reference validation compares exact finite float32 bits', () {
    expect(
      cpuBenchmarkFloat32BitsMatch(
        Float32List.fromList(<double>[0, 1]),
        Float32List.fromList(<double>[-0.0, 1]),
      ),
      isFalse,
    );
    expect(
      cpuBenchmarkFloat32BitsMatch(
        Float32List.fromList(<double>[double.nan]),
        Float32List.fromList(<double>[double.nan]),
      ),
      isFalse,
    );
    expect(
      cpuBenchmarkFloat32BitsMatch(
        Float32List.fromList(<double>[-0.0, 1]),
        Float32List.fromList(<double>[-0.0, 1]),
      ),
      isTrue,
    );
  });
}

final class _MemoryAssetBundle extends CachingAssetBundle {
  _MemoryAssetBundle(this.assets);

  final Map<String, Uint8List> assets;

  @override
  Future<ByteData> load(String key) async {
    final Uint8List bytes = Uint8List.fromList(assets[key]!);
    return ByteData.view(bytes.buffer);
  }
}

final class _FakeProbe implements CpuBenchmarkProbe {
  _FakeProbe({this.failAt});

  final int? failAt;
  var runCalls = 0;
  var disposeCalls = 0;

  @override
  int runOnce() {
    runCalls += 1;
    if (runCalls == failAt) throw StateError('synthetic run failure');
    return runCalls;
  }

  @override
  void dispose() {
    disposeCalls += 1;
  }
}
