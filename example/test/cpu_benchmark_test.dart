import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/cpu_benchmark.dart';

const List<String> _assetNames = <String>[
  'assets/models/cpu_benchmark_matmul.onnx',
  'assets/models/cpu_benchmark_matmul.input.f32le',
  'assets/models/cpu_benchmark_matmul.output.f32le',
  'assets/models/cpu_benchmark_matmul.json',
];
const String _challenge =
    '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const int _processId = 4242;

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
          environment: const <String, String>{
            cpuBenchmarkActivationKey: '1',
            cpuBenchmarkChallengeKey: _challenge,
          },
        ),
        isTrue,
      );
    }
    for (final Map<String, String> environment in <Map<String, String>>[
      const <String, String>{},
      const <String, String>{cpuBenchmarkChallengeKey: _challenge},
      const <String, String>{
        cpuBenchmarkActivationKey: '',
        cpuBenchmarkChallengeKey: _challenge,
      },
      const <String, String>{
        cpuBenchmarkActivationKey: '0',
        cpuBenchmarkChallengeKey: _challenge,
      },
      const <String, String>{
        cpuBenchmarkActivationKey: 'true',
        cpuBenchmarkChallengeKey: _challenge,
      },
      const <String, String>{
        cpuBenchmarkActivationKey: ' 1',
        cpuBenchmarkChallengeKey: _challenge,
      },
      const <String, String>{
        cpuBenchmarkActivationKey: '1\n',
        cpuBenchmarkChallengeKey: _challenge,
      },
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
          environment: const <String, String>{
            cpuBenchmarkActivationKey: '1',
            cpuBenchmarkChallengeKey: _challenge,
          },
        ),
        isFalse,
      );
    }
  });

  test('desktop benchmark activation requires one exact launch challenge', () {
    for (final String? challenge in <String?>[
      null,
      '',
      _challenge.substring(1),
      '${_challenge}0',
      _challenge.toUpperCase(),
      List<String>.filled(64, 'g').join(),
      ' $_challenge',
      '$_challenge\n',
      '$_challenge\r',
      '${_challenge.substring(1)}\n',
      '${_challenge.substring(1)}\r',
    ]) {
      expect(
        desktopCpuBenchmarkEnabled(
          isMacOS: true,
          isLinux: false,
          environment: <String, String>{
            cpuBenchmarkActivationKey: '1',
            cpuBenchmarkChallengeKey: ?challenge,
          },
        ),
        isFalse,
        reason: '$challenge',
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

  test('protocol and model identity constants match committed bytes', () {
    final File packageConfigFile = File(
      '.dart_tool/package_config.json',
    ).absolute;
    final Map<String, Object?> packageConfig =
        jsonDecode(packageConfigFile.readAsStringSync())!
            as Map<String, Object?>;
    final List<Object?> packages = packageConfig['packages']! as List<Object?>;
    final Map<String, Object?> fonixPackage = packages
        .cast<Map<String, Object?>>()
        .singleWhere((Map<String, Object?> value) => value['name'] == 'fonix');
    final Uri configuredRoot = Uri.parse(fonixPackage['rootUri']! as String);
    final Uri packageRootUri = configuredRoot.isAbsolute
        ? configuredRoot
        : packageConfigFile.parent.uri.resolveUri(configuredRoot);
    final Directory packageRoot = Directory.fromUri(packageRootUri);
    final File descriptorFile = File(
      '${packageRoot.path}/templates/ci/cpu_benchmark_protocol_v1.json',
    );
    final File schemaFile = File(
      '${packageRoot.path}/templates/ci/'
      'cpu_benchmark_target_fragment_v1.schema.json',
    );
    final File generatorFile = File(
      '${packageRoot.path}/example/assets/models/'
      'generate_cpu_benchmark_matmul.py',
    );
    final File metadataFile = File(
      '${packageRoot.path}/example/assets/models/'
      'cpu_benchmark_matmul.json',
    );

    final List<int> descriptorBytes = descriptorFile.readAsBytesSync();
    final List<int> schemaBytes = schemaFile.readAsBytesSync();
    final List<int> generatorBytes = generatorFile.readAsBytesSync();
    final List<int> metadataBytes = metadataFile.readAsBytesSync();
    expect(
      sha256.convert(descriptorBytes).toString(),
      cpuBenchmarkProtocolDescriptorSha256,
    );
    expect(
      sha256.convert(schemaBytes).toString(),
      cpuBenchmarkTargetFragmentSchemaSha256,
    );
    expect(generatorBytes, hasLength(cpuBenchmarkGeneratorBytes));
    expect(
      sha256.convert(generatorBytes).toString(),
      cpuBenchmarkGeneratorSha256,
    );
    expect(metadataBytes, hasLength(cpuBenchmarkMetadataBytes));
    expect(
      sha256.convert(metadataBytes).toString(),
      cpuBenchmarkMetadataSha256,
    );

    final Map<String, Object?> descriptor =
        jsonDecode(utf8.decode(descriptorBytes))! as Map<String, Object?>;
    expect(descriptor['schemaVersion'], 1);
    expect(descriptor['protocolId'], cpuBenchmarkProtocolId);
    expect(descriptor['protocolVersion'], cpuBenchmarkProtocolVersion);
    expect(descriptor['targetFragmentSchema'], <String, Object?>{
      'id':
          'https://fonix.invalid/schemas/'
          'cpu-benchmark-target-fragment-v1.json',
      'path': 'templates/ci/cpu_benchmark_target_fragment_v1.schema.json',
      'sizeBytes': schemaBytes.length,
      'sha256': cpuBenchmarkTargetFragmentSchemaSha256,
    });
  });

  test('rejects invalid launch identity before loading assets', () async {
    for (final String challenge in <String>[
      '',
      _challenge.substring(1),
      '${_challenge}0',
      _challenge.toUpperCase(),
      List<String>.filled(64, 'g').join(),
      ' $_challenge',
      '$_challenge\n',
      '$_challenge\r',
      '${_challenge.substring(1)}\n',
      '${_challenge.substring(1)}\r',
    ]) {
      var factoryCalls = 0;
      await expectLater(
        runDesktopCpuBenchmark(
          launchChallenge: challenge,
          processId: _processId,
          assets: _ForbiddenAssetBundle(),
          createProbe: (CpuBenchmarkAssets _) {
            factoryCalls += 1;
            throw StateError('A probe must not be constructed.');
          },
        ),
        throwsA(
          isA<CpuBenchmarkFailure>().having(
            (CpuBenchmarkFailure value) => value.summary,
            'summary',
            contains('challenge'),
          ),
        ),
      );
      expect(factoryCalls, 0);
    }
    for (final int invalidProcessId in <int>[0, -1, 0x80000000]) {
      var factoryCalls = 0;
      await expectLater(
        runDesktopCpuBenchmark(
          launchChallenge: _challenge,
          processId: invalidProcessId,
          assets: _ForbiddenAssetBundle(),
          createProbe: (CpuBenchmarkAssets _) {
            factoryCalls += 1;
            throw StateError('A probe must not be constructed.');
          },
        ),
        throwsA(
          isA<CpuBenchmarkFailure>().having(
            (CpuBenchmarkFailure value) => value.summary,
            'summary',
            contains('process identity'),
          ),
        ),
      );
      expect(factoryCalls, 0);
    }
  });

  test('strict pool settlement preserves a second-admission failure', () async {
    final StateError admissionFailure = StateError(
      'synthetic second admission failure',
    );
    final Completer<int> firstResult = Completer<int>();
    var startCalls = 0;
    var outstanding = 0;
    var settled = 0;
    final Future<List<int>> pending =
        settleCpuBenchmarkStrictPoolRuns<int, int>(
          start: () {
            startCalls += 1;
            if (startCalls == 2) throw admissionFailure;
            outstanding += 1;
            return 0;
          },
          result: (int _) async {
            try {
              return await firstResult.future;
            } finally {
              outstanding -= 1;
              settled += 1;
            }
          },
          hasExpectedOccupancy: () => false,
          isDrained: () => outstanding == 0,
        );
    expect(startCalls, 2);
    expect(outstanding, 1);
    firstResult.complete(7);
    await expectLater(pending, throwsA(same(admissionFailure)));
    expect(settled, 1);
    expect(outstanding, 0);
  });

  test(
    'strict pool settlement drains both runs before occupancy failure',
    () async {
      final List<Completer<int>> results = <Completer<int>>[
        Completer<int>(),
        Completer<int>(),
      ];
      var startCalls = 0;
      var outstanding = 0;
      var settled = 0;
      final Future<List<int>> pending =
          settleCpuBenchmarkStrictPoolRuns<int, int>(
            start: () {
              final int handle = startCalls;
              startCalls += 1;
              outstanding += 1;
              return handle;
            },
            result: (int handle) async {
              try {
                return await results[handle].future;
              } finally {
                outstanding -= 1;
                settled += 1;
              }
            },
            hasExpectedOccupancy: () => false,
            isDrained: () => outstanding == 0,
          );
      expect(startCalls, 2);
      expect(outstanding, 2);
      results[0].complete(7);
      results[1].complete(11);
      await expectLater(
        pending,
        throwsA(
          isA<CpuBenchmarkFailure>().having(
            (CpuBenchmarkFailure value) => value.summary,
            'summary',
            contains('occupy both workers'),
          ),
        ),
      );
      expect(settled, 2);
      expect(outstanding, 0);
    },
  );

  test(
    'emits one bounded fragment after fixed stabilization and windows',
    () async {
      final _FakeClock clock = _FakeClock();
      late _FakeProbe probe;
      late _FakePoolProbe poolProbe;
      final CpuBenchmarkFragment fragment = await runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets assets) {
          expect(assets.model, hasLength(cpuBenchmarkModelBytes));
          expect(assets.inputValues, hasLength(2048 * 1024));
          expect(assets.referenceOutput, hasLength(2048 * 1024));
          return probe = _FakeProbe(onRun: clock.advance);
        },
        createPoolProbe:
            (
              CpuBenchmarkAssets assets,
              CpuBenchmarkTargetIdentity identity,
              void Function() onInputPrepared,
            ) async {
              expect(assets.model, hasLength(cpuBenchmarkModelBytes));
              expect(identity.platform, 'macos');
              onInputPrepared();
              return poolProbe = _FakePoolProbe(clock: clock);
            },
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      );

      final Map<String, Object?> value = fragment.toMap();
      expect(value.keys.toList(), <String>[
        'schemaVersion',
        'result',
        'purpose',
        'protocol',
        'launchChallenge',
        'processId',
        'freshProcessRequired',
        'executionSurface',
        'model',
        'runtime',
        'session',
        'timing',
        'stabilization',
        'measurements',
        'providerAssignment',
        'resources',
        'lifecycle',
        'poolEvidence',
        'claimBoundary',
      ]);
      expect(value['schemaVersion'], 1);
      expect(value['purpose'], 'measurement-only-target-fragment');
      expect(value['protocol'], <String, Object?>{
        'id': cpuBenchmarkProtocolId,
        'version': cpuBenchmarkProtocolVersion,
        'descriptorSha256': cpuBenchmarkProtocolDescriptorSha256,
        'targetFragmentSchemaSha256': cpuBenchmarkTargetFragmentSchemaSha256,
      });
      expect(value['launchChallenge'], _challenge);
      expect(value['processId'], _processId);
      expect(value['freshProcessRequired'], isTrue);
      expect(
        value['executionSurface'],
        'synchronous-and-isolate-pool-public-api',
      );

      final Map<String, Object?> model =
          value['model']! as Map<String, Object?>;
      expect(model['metadataSha256'], cpuBenchmarkMetadataSha256);
      expect(model['metadataSizeBytes'], cpuBenchmarkMetadataBytes);
      expect(model['generatorId'], cpuBenchmarkGeneratorId);
      expect(model['generatorSha256'], cpuBenchmarkGeneratorSha256);
      expect(model['generatorSizeBytes'], cpuBenchmarkGeneratorBytes);
      expect(model['referencePolicy'], <String, Object?>{
        'comparison': 'exact-ieee754-binary32-bits',
        'absoluteTolerance': 0,
        'relativeTolerance': 0,
        'nanPolicy': 'forbid',
        'infinityPolicy': 'forbid',
      });
      final Map<String, Object?> runtime =
          value['runtime']! as Map<String, Object?>;
      expect(runtime['runtimeLibraryIdentity'], 'onnxruntime.1');
      expect(runtime['shimNativeIdentity'], 'fonix_shim');
      expect(value['timing'], <String, Object?>{
        'durationUnit': 'microseconds',
        'clockScope': 'process-local-monotonic-stopwatch',
        'runtimeLoadScope': 'ort-runtime-open',
        'sessionCreateScope': 'verified-model-bytes-to-session',
        'dataPreparationScope': 'dart-float32-to-native-tensor-copy',
        'inferenceScope': 'synchronous-session-run-only',
        'outputMaterializationScope': 'native-output-to-dart-float32-copy-only',
        'throughputWindowTargetMicroseconds': 1000000,
        'throughputWindowStopRule':
            'complete-runs-until-monotonic-elapsed-gte-target',
        'assignmentScope': 'separate-profiled-session-after-all-timing',
      });

      final Map<String, Object?> stabilization =
          value['stabilization']! as Map<String, Object?>;
      expect(stabilization['actualRuns'], 20);
      expect(stabilization['inferenceMicroseconds'], <int>[
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
        1000,
      ]);
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
      final Map<String, Object?> poolEvidence =
          value['poolEvidence']! as Map<String, Object?>;
      expect(poolEvidence.keys.toList(), <String>[
        'executionSurface',
        'configuration',
        'timing',
        'stabilization',
        'measurements',
        'providerAssignments',
        'resources',
        'lifecycle',
      ]);
      expect(poolEvidence['executionSurface'], 'public-ort-session-pool');
      expect(poolEvidence['configuration'], <String, Object?>{
        'poolSize': 2,
        'concurrency': 2,
        'workerProtocolVersion': 4,
        'maxPendingRunsPerWorker': 1,
        'maxMessageBytes': 33554432,
        'maxOutstandingInputBytesPerWorker': 16777216,
        'inputReservationBytesPerRun': 8388629,
        'graphOptimization': 'all',
        'executionMode': 'sequential',
        'intraOpThreads': 1,
        'interOpThreads': 1,
        'cpuMemoryArena': true,
        'memoryPattern': true,
        'deterministicCompute': true,
        'timedProviderPolicy': 'cpu-required-report-fallback',
        'throughputCycle':
            'controller-input-copy-worker-decode-native-tensor-inference-'
            'worker-output-copy-transfer-controller-decode-dart-output-copy-'
            'bit-validation',
        'copyBoundaries': <String>[
          'input-fixture-to-immutable-isolate-tensor',
          'isolate-tensor-to-transferable-input',
          'worker-transfer-to-native-tensor',
          'native-output-to-worker-transfer',
          'worker-transfer-to-isolate-output',
          'isolate-output-to-dart-float32',
        ],
      });
      final Map<String, Object?> poolStabilization =
          poolEvidence['stabilization']! as Map<String, Object?>;
      expect(poolStabilization['actualRounds'], 20);
      expect(poolStabilization['roundDurationMicroseconds'], <int>[
        for (var index = 0; index < 20; index += 1) 250000,
      ]);
      expect(poolStabilization['batchMedianMicroseconds'], <int>[
        250000,
        250000,
        250000,
        250000,
      ]);
      final Map<String, Object?> poolMeasurements =
          poolEvidence['measurements']! as Map<String, Object?>;
      expect(poolMeasurements['inputPreparationMicroseconds'], <int>[3000]);
      expect(poolMeasurements['poolStartupMicroseconds'], <int>[40000]);
      expect(poolMeasurements['firstConcurrentRound'], <String, Object?>{
        'completedRunsByLane': <int>[1, 1],
        'totalCompletedRuns': 2,
        'durationMicroseconds': 250000,
        'maximumObservedInFlightRuns': 2,
      });
      expect(poolMeasurements['throughput'], <Map<String, Object?>>[
        for (var index = 0; index < 3; index += 1)
          <String, Object?>{
            'completedRunsByLane': <int>[4, 4],
            'totalCompletedRuns': 8,
            'durationMicroseconds': 1000000,
            'maximumObservedInFlightRuns': 2,
          },
      ]);
      expect(poolEvidence['providerAssignments'], hasLength(2));
      final Map<String, Object?> poolResources =
          poolEvidence['resources']! as Map<String, Object?>;
      expect(
        (poolResources['rssSamples']! as List<Object?>)
            .cast<Map<String, Object?>>()
            .map((Map<String, Object?> sample) => sample['phase'])
            .toList(),
        cpuBenchmarkPoolRssPhases,
      );
      expect(poolProbe.runCalls, 66);
      expect(poolProbe.assignmentCalls, 1);
      expect(poolProbe.closeCalls, 3);
      expect(poolProbe.outstandingRuns, 0);
      expect(poolProbe.outstandingInputBytes, 0);

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
          launchChallenge: _challenge,
          processId: _processId,
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

  test('rejects a pool peak below the serial lifetime RSS peak', () async {
    final _FakeClock clock = _FakeClock();
    var rssCalls = 0;
    await expectLater(
      runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) => _FakeProbe(onRun: clock.advance),
        createPoolProbe:
            (
              CpuBenchmarkAssets _,
              CpuBenchmarkTargetIdentity _,
              void Function() onInputPrepared,
            ) async {
              onInputPrepared();
              return _FakePoolProbe(clock: clock);
            },
        readRss: () {
          rssCalls += 1;
          return (
            currentBytes: 1000000,
            peakBytes: rssCalls <= 8 ? 3000000 : 2000000,
          );
        },
        readMonotonicMicroseconds: clock.read,
      ),
      throwsA(
        isA<CpuBenchmarkFailure>().having(
          (CpuBenchmarkFailure value) => value.summary,
          'summary',
          contains('pool returned an invalid process RSS observation'),
        ),
      ),
    );
    expect(rssCalls, 9);
  });

  test('fails closed when warm inference never stabilizes', () async {
    final _FakeClock clock = _FakeClock();
    late _FakeProbe probe;
    await expectLater(
      runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
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
        launchChallenge: _challenge,
        processId: _processId,
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

  test('closes the pool when concurrent stabilization never settles', () async {
    final _FakeClock clock = _FakeClock();
    late _FakePoolProbe pool;
    await expectLater(
      runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) => _FakeProbe(onRun: clock.advance),
        createPoolProbe:
            (
              CpuBenchmarkAssets _,
              CpuBenchmarkTargetIdentity _,
              void Function() onInputPrepared,
            ) async {
              onInputPrepared();
              return pool = _FakePoolProbe(clock: clock, neverStabilizes: true);
            },
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      ),
      throwsA(
        isA<CpuBenchmarkFailure>().having(
          (CpuBenchmarkFailure value) => value.summary,
          'summary',
          contains('Concurrent inference did not stabilize'),
        ),
      ),
    );
    expect(pool.closeCalls, 1);
    expect(pool.outstandingRuns, 0);
    expect(pool.outstandingInputBytes, 0);
  });

  test('drains the peer run and closes after one pool run fails', () async {
    final _FakeClock clock = _FakeClock();
    late _FakePoolProbe pool;
    await expectLater(
      runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) => _FakeProbe(onRun: clock.advance),
        createPoolProbe:
            (
              CpuBenchmarkAssets _,
              CpuBenchmarkTargetIdentity _,
              void Function() onInputPrepared,
            ) async {
              onInputPrepared();
              return pool = _FakePoolProbe(clock: clock, failRun: 1);
            },
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      ),
      throwsStateError,
    );
    expect(pool.runCalls, 2);
    expect(pool.closeCalls, 1);
    expect(pool.outstandingRuns, 0);
    expect(pool.outstandingInputBytes, 0);
  });

  test('rejects a nonadvancing pool clock after draining both runs', () async {
    final _FakeClock clock = _FakeClock();
    late _FakePoolProbe pool;
    await expectLater(
      runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) => _FakeProbe(onRun: clock.advance),
        createPoolProbe:
            (
              CpuBenchmarkAssets _,
              CpuBenchmarkTargetIdentity _,
              void Function() onInputPrepared,
            ) async {
              onInputPrepared();
              return pool = _FakePoolProbe(clock: clock, advanceClock: false);
            },
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      ),
      throwsA(isA<CpuBenchmarkFailure>()),
    );
    expect(pool.runCalls, 2);
    expect(pool.closeCalls, 1);
    expect(pool.outstandingRuns, 0);
    expect(pool.outstandingInputBytes, 0);
  });

  test(
    'rejects output without replacing an idempotent close failure',
    () async {
      final _FakeClock clock = _FakeClock();
      final StateError closeError = StateError('synthetic pool close failure');
      late _FakePoolProbe pool;
      await expectLater(
        runDesktopCpuBenchmark(
          launchChallenge: _challenge,
          processId: _processId,
          assets: _MemoryAssetBundle(fixtureAssets),
          createProbe: (CpuBenchmarkAssets _) =>
              _FakeProbe(onRun: clock.advance),
          createPoolProbe:
              (
                CpuBenchmarkAssets _,
                CpuBenchmarkTargetIdentity _,
                void Function() onInputPrepared,
              ) async {
                onInputPrepared();
                return pool = _FakePoolProbe(
                  clock: clock,
                  closeFailure: closeError,
                );
              },
          readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
          readMonotonicMicroseconds: clock.read,
        ),
        throwsA(same(closeError)),
      );
      expect(pool.closeCalls, 3);
      expect(pool.closeFutures, hasLength(3));
      expect(
        pool.closeFutures.every(
          (Future<void> future) => identical(future, pool.closeFutures.first),
        ),
        isTrue,
      );
      expect(pool.assignmentCalls, 0);
      expect(pool.outstandingRuns, 0);
      expect(pool.outstandingInputBytes, 0);
    },
  );

  test('rejects a pool assignment identity mismatch after cleanup', () async {
    final _FakeClock clock = _FakeClock();
    late _FakePoolProbe pool;
    await expectLater(
      runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) => _FakeProbe(onRun: clock.advance),
        createPoolProbe:
            (
              CpuBenchmarkAssets _,
              CpuBenchmarkTargetIdentity _,
              void Function() onInputPrepared,
            ) async {
              onInputPrepared();
              return pool = _FakePoolProbe(
                clock: clock,
                assignmentReportedName: 'UnexpectedCPUProvider',
              );
            },
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      ),
      throwsA(
        isA<CpuBenchmarkFailure>().having(
          (CpuBenchmarkFailure value) => value.summary,
          'summary',
          contains('assignment receipts'),
        ),
      ),
    );
    expect(pool.assignmentCalls, 1);
    expect(pool.closeCalls, 3);
  });

  test('rejects a pool assignment node-count mismatch after cleanup', () async {
    final _FakeClock clock = _FakeClock();
    late _FakePoolProbe pool;
    await expectLater(
      runDesktopCpuBenchmark(
        launchChallenge: _challenge,
        processId: _processId,
        assets: _MemoryAssetBundle(fixtureAssets),
        createProbe: (CpuBenchmarkAssets _) => _FakeProbe(onRun: clock.advance),
        createPoolProbe:
            (
              CpuBenchmarkAssets _,
              CpuBenchmarkTargetIdentity _,
              void Function() onInputPrepared,
            ) async {
              onInputPrepared();
              return pool = _FakePoolProbe(
                clock: clock,
                assignmentNodeExecutionCount: 2,
              );
            },
        readRss: () => (currentBytes: 1000000, peakBytes: 2000000),
        readMonotonicMicroseconds: clock.read,
      ),
      throwsA(
        isA<CpuBenchmarkFailure>().having(
          (CpuBenchmarkFailure value) => value.summary,
          'summary',
          contains('assignment receipts'),
        ),
      ),
    );
    expect(pool.assignmentCalls, 1);
    expect(pool.closeCalls, 3);
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

final class _ForbiddenAssetBundle extends CachingAssetBundle {
  @override
  Future<ByteData> load(String key) =>
      throw StateError('Launch validation must precede asset loading.');
}

final class _FakeClock {
  var _microseconds = 0;

  int read() => _microseconds;

  void advance() => advanceBy(250000);

  void advanceBy(int microseconds) => _microseconds += microseconds;
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
    runtimeLibraryIdentity: 'onnxruntime.1',
    runtimeSource: 'bundled',
    runtimeOwner: 'wrapper',
    artifactFlavor: 'default',
    artifactId: 'onnxruntime-macos-arm64-default',
    artifactSourceSha256:
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    platform: 'macos',
    architecture: 'arm64',
    shimNativeIdentity: 'fonix_shim',
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

final class _FakePoolProbe implements CpuBenchmarkPoolProbe {
  _FakePoolProbe({
    required this.clock,
    this.neverStabilizes = false,
    this.advanceClock = true,
    this.failRun,
    this.closeFailure,
    this.assignmentReportedName = 'CPUExecutionProvider',
    this.assignmentNodeExecutionCount = 1,
  });

  final _FakeClock clock;
  final bool neverStabilizes;
  final bool advanceClock;
  final int? failRun;
  final Object? closeFailure;
  final String assignmentReportedName;
  final int assignmentNodeExecutionCount;
  final List<({int run, Completer<void> completer})> _round =
      <({int run, Completer<void> completer})>[];
  Future<void>? _closeFuture;
  var _closed = false;
  var _rounds = 0;
  var runCalls = 0;
  var closeCalls = 0;
  var assignmentCalls = 0;
  final List<Future<void>> closeFutures = <Future<void>>[];

  @override
  int get inputPreparationMicroseconds => 3000;

  @override
  int get poolStartupMicroseconds => 40000;

  @override
  var outstandingRuns = 0;

  @override
  var outstandingInputBytes = 0;

  @override
  Future<void> startRun() {
    if (_closed) throw StateError('synthetic pool already closed');
    runCalls += 1;
    outstandingRuns += 1;
    outstandingInputBytes += cpuBenchmarkPoolInputReservationBytesPerRun;
    final Completer<void> completer = Completer<void>();
    _round.add((run: runCalls, completer: completer));
    if (_round.length == cpuBenchmarkPoolConcurrency) {
      final List<({int run, Completer<void> completer})> settling =
          List<({int run, Completer<void> completer})>.of(_round);
      _round.clear();
      scheduleMicrotask(() {
        if (advanceClock) {
          final int duration = neverStabilizes
              ? ((_rounds ~/ 5).isEven ? 250000 : 500000)
              : 250000;
          clock.advanceBy(duration);
        }
        _rounds += 1;
        for (final (:int run, :Completer<void> completer) in settling) {
          outstandingRuns -= 1;
          outstandingInputBytes -= cpuBenchmarkPoolInputReservationBytesPerRun;
          if (run == failRun) {
            completer.completeError(StateError('synthetic pool run failure'));
          } else {
            completer.complete();
          }
        }
      });
    }
    return completer.future;
  }

  @override
  Future<void> close() {
    closeCalls += 1;
    final Future<void> result = _closeFuture ??= Future<void>.sync(() {
      _closed = true;
      if (_round.isNotEmpty ||
          outstandingRuns != 0 ||
          outstandingInputBytes != 0) {
        throw StateError('synthetic pool close before drain');
      }
      final Object? failure = closeFailure;
      if (failure != null) throw failure;
    });
    closeFutures.add(result);
    return result;
  }

  @override
  Future<List<CpuBenchmarkAssignment>> captureAssignments() async {
    assignmentCalls += 1;
    if (!_closed || assignmentCalls != 1) {
      throw StateError('synthetic assignment phase out of order');
    }
    return <CpuBenchmarkAssignment>[
      for (var index = 0; index < cpuBenchmarkPoolSize; index += 1)
        CpuBenchmarkAssignment(
          providerId: 'cpu',
          reportedName: assignmentReportedName,
          nodeExecutionCount: assignmentNodeExecutionCount,
          nodeExecutionsByProvider: <String, int>{
            'cpu': assignmentNodeExecutionCount,
          },
        ),
    ];
  }
}
