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
  'assets/models/cpu_benchmark_matmul.json',
];

void main() {
  late Map<String, Uint8List> fixtureAssets;

  setUpAll(() {
    fixtureAssets = <String, Uint8List>{
      for (final String name in _assetNames) name: File(name).readAsBytesSync(),
    };
  });

  test('desktop benchmark activation is closed to macOS and Linux', () {
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
      const <String, String>{cpuBenchmarkActivationKey: ''},
      const <String, String>{cpuBenchmarkActivationKey: '0'},
      const <String, String>{cpuBenchmarkActivationKey: 'true'},
      const <String, String>{cpuBenchmarkActivationKey: ' 1'},
      const <String, String>{cpuBenchmarkActivationKey: '1\n'},
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
    for (final ({bool isMacOS, bool isLinux}) platform
        in <({bool isMacOS, bool isLinux})>[
          (isMacOS: false, isLinux: false),
          (isMacOS: true, isLinux: true),
        ]) {
      expect(
        desktopCpuBenchmarkEnabled(
          isMacOS: platform.isMacOS,
          isLinux: platform.isLinux,
          environment: const <String, String>{cpuBenchmarkActivationKey: '1'},
        ),
        isFalse,
      );
    }
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

  test(
    'emits one bounded fragment after fixed stabilization and windows',
    () async {
      final _FakeClock clock = _FakeClock();
      late _FakeProbe probe;
      final CpuBenchmarkFragment fragment = await runDesktopCpuBenchmark(
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets assets) {
          expect(assets.model, hasLength(cpuBenchmarkModelBytes));
          expect(assets.inputValues, hasLength(2048 * 1024));
          expect(assets.referenceOutput, hasLength(2048 * 1024));
          return probe = _FakeProbe(onRun: clock.advance);
        },
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      );

      final Map<String, Object?> value = fragment.toMap();
      expect(value.keys.toList(), <String>[
        'schemaVersion',
        'result',
        'purpose',
        'freshProcessRequired',
        'executionSurface',
        'model',
        'runtime',
        'session',
        'stabilization',
        'measurements',
        'providerAssignment',
        'resources',
        'lifecycle',
        'claimBoundary',
      ]);
      expect(value['purpose'], 'measurement-only-target-fragment');
      expect(value['freshProcessRequired'], isTrue);
      expect(value['executionSurface'], 'synchronous-public-api');

      final Map<String, Object?> stabilization =
          value['stabilization']! as Map<String, Object?>;
      expect(stabilization['actualRuns'], 20);
      expect(stabilization['batchMedianMicroseconds'], <int>[
        1000,
        1000,
        1000,
        1000,
      ]);
      final Map<String, Object?> measurements =
          value['measurements']! as Map<String, Object?>;
      expect(measurements['warmRunMicroseconds'], hasLength(100));
      expect(
        measurements['warmOutputMaterializationMicroseconds'],
        hasLength(100),
      );
      expect(measurements['throughput'], <Map<String, Object?>>[
        <String, Object?>{'completedRuns': 4, 'durationMicroseconds': 1000000},
        <String, Object?>{'completedRuns': 4, 'durationMicroseconds': 1000000},
        <String, Object?>{'completedRuns': 4, 'durationMicroseconds': 1000000},
      ]);
      expect(probe.runCalls, 133);
      expect(probe.assignmentCalls, 1);
      expect(probe.disposeCalls, 2);
      final Map<String, Object?> resources =
          value['resources']! as Map<String, Object?>;
      expect(resources['rssScope'], 'total-process');
      expect(resources['rssSamples'], hasLength(8));

      final String encoded = fragment.toJsonString();
      expect(
        utf8.encode(encoded).length,
        lessThan(maximumCpuBenchmarkFragmentBytes),
      );
      expect(encoded, isNot(contains('\n')));
      expect(encoded, isNot(contains('\r')));
      expect(encoded, isNot(contains(Directory.systemTemp.path)));
    },
  );

  test(
    'rejects one tampered asset before constructing a native probe',
    () async {
      final Map<String, Uint8List> tampered = <String, Uint8List>{
        ...fixtureAssets,
      };
      final Uint8List metadata = Uint8List.fromList(
        tampered[_assetNames.last]!,
      );
      metadata[metadata.length - 2] ^= 1;
      tampered[_assetNames.last] = metadata;
      var factoryCalls = 0;

      await expectLater(
        runDesktopCpuBenchmark(
          assets: _MemoryAssetBundle(tampered),
          createProbe: (CpuBenchmarkAssets _) {
            factoryCalls += 1;
            return _FakeProbe(onRun: () {});
          },
        ),
        throwsA(isA<CpuBenchmarkFailure>()),
      );
      expect(factoryCalls, 0);
    },
  );

  test('fails closed when warm inference never stabilizes', () async {
    final _FakeClock clock = _FakeClock();
    late _FakeProbe probe;
    await expectLater(
      runDesktopCpuBenchmark(
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) =>
            probe = _FakeProbe(onRun: clock.advance, neverStabilizes: true),
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      ),
      throwsA(
        isA<CpuBenchmarkFailure>().having(
          (CpuBenchmarkFailure value) => value.summary,
          'summary',
          contains('did not stabilize'),
        ),
      ),
    );
    expect(probe.runCalls, 101);
    expect(probe.assignmentCalls, 0);
    expect(probe.disposeCalls, 1);
  });

  test('settles the probe when one timed run fails', () async {
    late _FakeProbe probe;
    await expectLater(
      runDesktopCpuBenchmark(
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) =>
            probe = _FakeProbe(onRun: () {}, failRun: true),
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
      ),
      throwsStateError,
    );
    expect(probe.runCalls, 1);
    expect(probe.disposeCalls, 1);
  });
}

final class _MemoryAssetBundle extends CachingAssetBundle {
  _MemoryAssetBundle(this._assets);

  final Map<String, Uint8List> _assets;

  @override
  Future<ByteData> load(String key) async {
    final Uint8List? value = _assets[key];
    if (value == null) throw StateError('Unexpected test asset key.');
    return ByteData.sublistView(value);
  }
}

final class _FakeClock {
  var _microseconds = 0;

  int read() => _microseconds;

  void advance() {
    _microseconds += 250000;
  }
}

final class _FakeProbe implements CpuBenchmarkProbe {
  _FakeProbe({
    required this.onRun,
    this.neverStabilizes = false,
    this.failRun = false,
  });

  final void Function() onRun;
  final bool neverStabilizes;
  final bool failRun;
  var runCalls = 0;
  var assignmentCalls = 0;
  var disposeCalls = 0;

  @override
  CpuBenchmarkTargetIdentity get identity => CpuBenchmarkTargetIdentity(
    packageVersion: '0.1.0-dev.1',
    runtimeVersion: '1.27.1',
    runtimeSource: 'bundled',
    runtimeOwner: 'wrapper',
    artifactFlavor: 'default',
    artifactId: 'onnxruntime-macos-arm64-default',
    artifactSourceSha256:
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    platform: 'macos',
    architecture: 'arm64',
    shimAbi: 1,
    shimBuildId: 'fonix-test',
    requiredOrtApi: 27,
    negotiatedOrtApi: 27,
    cpuReportedName: 'CPUExecutionProvider',
    compiledProviders: <Map<String, Object?>>[
      <String, Object?>{
        'wrapperId': 'cpu',
        'reportedName': 'CPUExecutionProvider',
      },
    ],
  );

  @override
  int get runtimeLoadMicroseconds => 10000;

  @override
  int get sessionCreateMicroseconds => 20000;

  @override
  int get dataPreparationMicroseconds => 3000;

  @override
  CpuBenchmarkRunSample runOnce() {
    runCalls += 1;
    if (failRun) throw StateError('synthetic target failure');
    onRun();
    final int inference;
    if (neverStabilizes && runCalls > 1) {
      final int stabilizationIndex = runCalls - 2;
      inference = (stabilizationIndex ~/ 5).isEven ? 1000 : 2000;
    } else {
      inference = 1000;
    }
    return CpuBenchmarkRunSample(
      inferenceMicroseconds: inference,
      outputMaterializationMicroseconds: 200,
    );
  }

  @override
  CpuBenchmarkAssignment captureAssignment() {
    assignmentCalls += 1;
    return CpuBenchmarkAssignment(
      providerId: 'cpu',
      reportedName: 'CPUExecutionProvider',
      nodeExecutionCount: 1,
      nodeExecutionsByProvider: const <String, int>{'cpu': 1},
    );
  }

  @override
  void dispose() {
    disposeCalls += 1;
  }
}
