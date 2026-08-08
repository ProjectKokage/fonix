import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:fonix/fonix.dart';

const int cpuBenchmarkFragmentSchemaVersion = 1;
const String cpuBenchmarkActivationKey = 'FONIX_CPU_BENCHMARK';
const String cpuBenchmarkChallengeKey = 'FONIX_CPU_BENCHMARK_CHALLENGE';
const String cpuBenchmarkFragmentPrefix = 'FONIX_CPU_BENCHMARK_FRAGMENT=';
const int maximumCpuBenchmarkFragmentBytes = 128 * 1024;

const String cpuBenchmarkProtocolId = 'fonix-cpu-benchmark-target-v1';
const int cpuBenchmarkProtocolVersion = 1;
const String cpuBenchmarkProtocolDescriptorSha256 =
    '9951998ec0c23817150ec2491e3225ad746539e989ea8335d01fd22e1e96766e';
const String cpuBenchmarkTargetFragmentSchemaSha256 =
    '8e79e4d637e2c917d654648cf549d3ade782770bbc5a4a0140297812fffa26d8';

const String _modelAsset = 'assets/models/cpu_benchmark_matmul.onnx';
const String _inputAsset = 'assets/models/cpu_benchmark_matmul.input.f32le';
const String _outputAsset = 'assets/models/cpu_benchmark_matmul.output.f32le';
const String _metadataAsset = 'assets/models/cpu_benchmark_matmul.json';

const String cpuBenchmarkModelSha256 =
    '19bc0466ef8627df9764b40d947ff2c7cfa978c7daa6952ca9553c700a6dbcf0';
const String cpuBenchmarkInputSha256 =
    '2025466d19e8aa6a9820266d0622d7b154059b61a1051b9edf1d020bf127c36a';
const String cpuBenchmarkOutputSha256 =
    'c79ff7588eadd3d82ba4a5028955ed02b98a72b11da828131b33075c781a40fb';
const String cpuBenchmarkMetadataSha256 =
    '7c089a5a6c6cd444eb802bb0066a2fae054e54a1b924cffac7c53b80bd9c7c8a';
const int cpuBenchmarkModelBytes = 4194629;
const int cpuBenchmarkInputBytes = 8388608;
const int cpuBenchmarkOutputBytes = 8388608;
const int cpuBenchmarkMetadataBytes = 3481;
const String cpuBenchmarkGeneratorId = 'cpu-benchmark-matmul-v1';
const String cpuBenchmarkGeneratorSha256 =
    '5730c2193acc8bd9fb3fd52fb9c973d46a512e2bd56c4d815caca49fe4e16ec2';
const int cpuBenchmarkGeneratorBytes = 20969;
const String cpuBenchmarkModelId =
    'cpu-benchmark-matmul-sha256-$cpuBenchmarkModelSha256';
const List<int> cpuBenchmarkInputShape = <int>[2048, 1024];
const List<int> cpuBenchmarkOutputShape = <int>[2048, 1024];
const String cpuBenchmarkInputName = 'input';
const String cpuBenchmarkOutputName = 'output';

const int _stabilizationBatchSize = 5;
const int _stabilizationRequiredTransitions = 3;
const int _stabilizationThresholdBasisPoints = 1000;
const int _maximumStabilizationRuns = 100;
const int _measuredWarmRuns = 100;
const int _throughputWindowCount = 3;
const int _throughputWindowMicroseconds = 1000000;
const int cpuBenchmarkPoolSize = 2;
const int cpuBenchmarkPoolConcurrency = 2;
const int cpuBenchmarkPoolWorkerProtocolVersion = 4;
const int cpuBenchmarkPoolMaxPendingRunsPerWorker = 1;
const int cpuBenchmarkPoolMaxMessageBytes = 32 * 1024 * 1024;
const int cpuBenchmarkPoolMaxOutstandingInputBytesPerWorker = 16 * 1024 * 1024;
const int cpuBenchmarkPoolInputReservationBytesPerRun =
    cpuBenchmarkInputBytes + (2 * 8) + 5;
const int _poolStabilizationBatchSize = 5;
const int _poolStabilizationRequiredTransitions = 3;
const int _poolStabilizationThresholdBasisPoints = 1000;
const int _maximumPoolStabilizationRounds = 100;
const List<String> cpuBenchmarkPoolRssPhases = <String>[
  'after-input-preparation',
  'after-pool-startup',
  'after-first-concurrent-round',
  'after-stabilization',
  'after-throughput',
  'after-pool-close',
  'after-assignment-evidence',
];
const int _maximumRunsPerThroughputWindow = 1000000;
const int _maximumDurationMicroseconds = 1000000000000000;
const int _maximumRssBytes = 0x7fffffffffffffff;
const int _maximumProcessId = 0x7fffffff;

/// Whether this process requested the closed desktop CPU benchmark path.
///
/// The target fragment is available only to the macOS and Linux final
/// applications. It is measurement input for a later host-side evidence
/// aggregate, never a performance or support claim by itself.
bool desktopCpuBenchmarkEnabled({
  required bool isMacOS,
  required bool isLinux,
  required Map<String, String> environment,
}) =>
    isMacOS != isLinux &&
    environment[cpuBenchmarkActivationKey] == '1' &&
    _isExactLowercaseHex64(environment[cpuBenchmarkChallengeKey] ?? '');

/// A bounded failure whose text contains no target path or model contents.
final class CpuBenchmarkFailure implements Exception {
  const CpuBenchmarkFailure(this.summary);

  final String summary;

  @override
  String toString() => 'CpuBenchmarkFailure: $summary';
}

/// The exact committed model, input, output, and metadata bytes.
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
      bundle.load(_metadataAsset),
    ]);
    final Uint8List model = _ownedBytes(loaded[0]);
    final Uint8List input = _ownedBytes(loaded[1]);
    final Uint8List output = _ownedBytes(loaded[2]);
    final Uint8List metadata = _ownedBytes(loaded[3]);
    _verifyAsset(
      model,
      expectedBytes: cpuBenchmarkModelBytes,
      expectedSha256: cpuBenchmarkModelSha256,
    );
    _verifyAsset(
      input,
      expectedBytes: cpuBenchmarkInputBytes,
      expectedSha256: cpuBenchmarkInputSha256,
    );
    _verifyAsset(
      output,
      expectedBytes: cpuBenchmarkOutputBytes,
      expectedSha256: cpuBenchmarkOutputSha256,
    );
    _verifyAsset(
      metadata,
      expectedBytes: cpuBenchmarkMetadataBytes,
      expectedSha256: cpuBenchmarkMetadataSha256,
    );
    return CpuBenchmarkAssets._(
      model: model,
      inputValues: _decodeFloat32(input),
      referenceOutput: _decodeFloat32(output),
    );
  }

  static Uint8List _ownedBytes(ByteData data) => Uint8List.fromList(
    data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
  );

  static void _verifyAsset(
    Uint8List value, {
    required int expectedBytes,
    required String expectedSha256,
  }) {
    if (value.lengthInBytes != expectedBytes ||
        sha256.convert(value).toString() != expectedSha256) {
      throw const CpuBenchmarkFailure(
        'A bundled CPU benchmark asset failed its identity check.',
      );
    }
  }

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

/// One inference duration and its separately measured output materialization.
final class CpuBenchmarkRunSample {
  const CpuBenchmarkRunSample({
    required this.inferenceMicroseconds,
    required this.outputMaterializationMicroseconds,
  });

  final int inferenceMicroseconds;
  final int outputMaterializationMicroseconds;
}

/// Safe native/runtime/build identity copied before native owners are closed.
final class CpuBenchmarkTargetIdentity {
  CpuBenchmarkTargetIdentity({
    required this.packageVersion,
    required this.runtimeVersion,
    required this.runtimeLibraryIdentity,
    required this.runtimeSource,
    required this.runtimeOwner,
    required this.artifactFlavor,
    required this.artifactId,
    required this.artifactSourceSha256,
    required this.platform,
    required this.architecture,
    required this.shimNativeIdentity,
    required this.shimAbi,
    required this.shimBuildId,
    required this.requiredOrtApi,
    required this.negotiatedOrtApi,
    required this.cpuReportedName,
    required List<Map<String, Object?>> compiledProviders,
  }) : compiledProviders = List<Map<String, Object?>>.unmodifiable(
         compiledProviders.map(
           (Map<String, Object?> value) =>
               Map<String, Object?>.unmodifiable(value),
         ),
       ) {
    if (!_semanticVersion.hasMatch(packageVersion) ||
        !_label.hasMatch(runtimeVersion) ||
        !_token.hasMatch(runtimeLibraryIdentity) ||
        !const <String>{
          'linked',
          'bundled',
          'process',
          'file',
        }.contains(runtimeSource) ||
        !const <String>{
          'wrapper',
          'sherpa',
          'application',
          'system',
        }.contains(runtimeOwner) ||
        !_token.hasMatch(artifactFlavor) ||
        !_token.hasMatch(artifactId) ||
        !_isExactLowercaseHex64(artifactSourceSha256) ||
        !const <String>{'macos', 'linux'}.contains(platform) ||
        !const <String>{'arm64', 'x86_64'}.contains(architecture) ||
        !_token.hasMatch(shimNativeIdentity) ||
        shimAbi <= 0 ||
        requiredOrtApi <= 0 ||
        negotiatedOrtApi <= 0 ||
        !_token.hasMatch(shimBuildId) ||
        !_token.hasMatch(cpuReportedName) ||
        compiledProviders.isEmpty ||
        compiledProviders.length > 64) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark target identity is outside its closed bounds.',
      );
    }
    final Set<String> providerIds = <String>{};
    for (final Map<String, Object?> provider in compiledProviders) {
      final Object? wrapperId = provider['wrapperId'];
      final Object? reportedName = provider['reportedName'];
      if (provider.length != 2 ||
          !provider.containsKey('wrapperId') ||
          !provider.containsKey('reportedName') ||
          wrapperId is! String ||
          !_token.hasMatch(wrapperId) ||
          !providerIds.add(wrapperId) ||
          (reportedName != null &&
              (reportedName is! String || !_token.hasMatch(reportedName)))) {
        throw const CpuBenchmarkFailure(
          'The CPU benchmark provider inventory is outside its closed bounds.',
        );
      }
    }
  }

  final String packageVersion;
  final String runtimeVersion;
  final String runtimeLibraryIdentity;
  final String runtimeSource;
  final String runtimeOwner;
  final String artifactFlavor;
  final String artifactId;
  final String artifactSourceSha256;
  final String platform;
  final String architecture;
  final String shimNativeIdentity;
  final int shimAbi;
  final String shimBuildId;
  final int requiredOrtApi;
  final int negotiatedOrtApi;
  final String cpuReportedName;
  final List<Map<String, Object?>> compiledProviders;

  Map<String, Object?> toMap() => <String, Object?>{
    'packageVersion': packageVersion,
    'runtimeVersion': runtimeVersion,
    'runtimeLibraryIdentity': runtimeLibraryIdentity,
    'runtimeSource': runtimeSource,
    'runtimeOwner': runtimeOwner,
    'artifactFlavor': artifactFlavor,
    'artifactId': artifactId,
    'artifactSourceSha256': artifactSourceSha256,
    'platform': platform,
    'architecture': architecture,
    'shimNativeIdentity': shimNativeIdentity,
    'shimAbi': shimAbi,
    'shimBuildId': shimBuildId,
    'requiredOrtApi': requiredOrtApi,
    'negotiatedOrtApi': negotiatedOrtApi,
    'compiledProviders': compiledProviders,
  };
}

/// Full-assignment counts captured outside every timed sample.
final class CpuBenchmarkAssignment {
  CpuBenchmarkAssignment({
    required this.providerId,
    required this.reportedName,
    required this.nodeExecutionCount,
    required Map<String, int> nodeExecutionsByProvider,
  }) : nodeExecutionsByProvider = Map<String, int>.unmodifiable(
         nodeExecutionsByProvider,
       ) {
    final int countSum = nodeExecutionsByProvider.values.fold<int>(
      0,
      (int total, int value) => total + value,
    );
    if (providerId != 'cpu' ||
        !_token.hasMatch(reportedName) ||
        nodeExecutionCount <= 0 ||
        nodeExecutionCount > 1000000 ||
        nodeExecutionsByProvider.length != 1 ||
        nodeExecutionsByProvider['cpu'] != nodeExecutionCount ||
        countSum != nodeExecutionCount) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark did not prove full CPU graph assignment.',
      );
    }
  }

  final String providerId;
  final String reportedName;
  final int nodeExecutionCount;
  final Map<String, int> nodeExecutionsByProvider;

  Map<String, Object?> toMap() => <String, Object?>{
    'providerId': providerId,
    'reportedName': reportedName,
    'discoverable': true,
    'registered': true,
    'requirement': 'full',
    'fallbackPolicy': 'reject-any',
    'nodeExecutionCount': nodeExecutionCount,
    'nodeExecutionsByProvider': nodeExecutionsByProvider,
    'fallbackObserved': false,
  };
}

bool _sameCpuBenchmarkAssignment(
  CpuBenchmarkAssignment left,
  CpuBenchmarkAssignment right,
) =>
    left.providerId == right.providerId &&
    left.reportedName == right.reportedName &&
    left.nodeExecutionCount == right.nodeExecutionCount &&
    left.nodeExecutionsByProvider.length ==
        right.nodeExecutionsByProvider.length &&
    left.nodeExecutionsByProvider.entries.every(
      (MapEntry<String, int> entry) =>
          right.nodeExecutionsByProvider[entry.key] == entry.value,
    );

/// The synchronous public-API surface exercised by the target runner.
abstract interface class CpuBenchmarkProbe {
  CpuBenchmarkTargetIdentity get identity;

  int get runtimeLoadMicroseconds;

  int get sessionCreateMicroseconds;

  int get dataPreparationMicroseconds;

  CpuBenchmarkRunSample runOnce();

  CpuBenchmarkAssignment captureAssignment();

  void dispose();
}

typedef CpuBenchmarkProbeFactory =
    CpuBenchmarkProbe Function(CpuBenchmarkAssets assets);

/// The public session-pool surface exercised by the concurrent phase.
abstract interface class CpuBenchmarkPoolProbe {
  int get inputPreparationMicroseconds;

  int get poolStartupMicroseconds;

  int get outstandingRuns;

  int get outstandingInputBytes;

  /// Starts one run synchronously and returns its eventual validated result.
  ///
  /// Implementations must admit the request before returning. This lets the
  /// runner prove two simultaneous reservations without yielding its isolate.
  Future<void> startRun();

  /// Captures one strict full-CPU receipt from each worker outside timing.
  Future<List<CpuBenchmarkAssignment>> captureAssignments();

  Future<void> close();
}

typedef CpuBenchmarkPoolProbeFactory =
    Future<CpuBenchmarkPoolProbe> Function(
      CpuBenchmarkAssets assets,
      CpuBenchmarkTargetIdentity identity,
      void Function() onInputPrepared,
    );

/// Starts and settles the exact strict two-run batch used for assignment proof.
///
/// The first admission, occupancy, or result failure remains authoritative,
/// but every admitted result is observed before that failure is rethrown.
Future<List<TResult>> settleCpuBenchmarkStrictPoolRuns<THandle, TResult>({
  required THandle Function() start,
  required Future<TResult> Function(THandle handle) result,
  required bool Function() hasExpectedOccupancy,
  required bool Function() isDrained,
}) async {
  final List<THandle> handles = <THandle>[];
  final List<TResult?> settledResults = List<TResult?>.filled(
    cpuBenchmarkPoolConcurrency,
    null,
  );
  Object? firstError;
  StackTrace? firstStackTrace;
  void retainFirstError(Object error, StackTrace stackTrace) {
    if (firstError == null) {
      firstError = error;
      firstStackTrace = stackTrace;
    }
  }

  for (var index = 0; index < cpuBenchmarkPoolConcurrency; index += 1) {
    try {
      handles.add(start());
    } on Object catch (error, stackTrace) {
      retainFirstError(error, stackTrace);
      break;
    }
  }
  if (firstError == null && !hasExpectedOccupancy()) {
    retainFirstError(
      const CpuBenchmarkFailure(
        'The strict pool did not occupy both workers concurrently.',
      ),
      StackTrace.current,
    );
  }
  final List<Future<void>> settlements = <Future<void>>[];
  for (var index = 0; index < handles.length; index += 1) {
    settlements.add(
      result(handles[index]).then<void>(
        (TResult value) {
          settledResults[index] = value;
        },
        onError: (Object error, StackTrace stackTrace) {
          retainFirstError(error, stackTrace);
        },
      ),
    );
  }
  await Future.wait<void>(settlements);
  if (!isDrained()) {
    retainFirstError(
      const CpuBenchmarkFailure('The strict pool did not drain before close.'),
      StackTrace.current,
    );
  }
  final Object? retainedError = firstError;
  if (retainedError != null) {
    Error.throwWithStackTrace(retainedError, firstStackTrace!);
  }
  if (handles.length != cpuBenchmarkPoolConcurrency ||
      settledResults.any((TResult? value) => value == null)) {
    throw const CpuBenchmarkFailure(
      'The strict pool result set is incomplete.',
    );
  }
  return List<TResult>.unmodifiable(settledResults.cast<TResult>());
}

/// One drained, two-lane pool round or throughput window.
final class CpuBenchmarkPoolWindow {
  CpuBenchmarkPoolWindow({
    required List<int> completedRunsByLane,
    required this.durationMicroseconds,
    required this.maximumObservedInFlightRuns,
  }) : completedRunsByLane = List<int>.unmodifiable(completedRunsByLane),
       totalCompletedRuns = completedRunsByLane.fold<int>(
         0,
         (int total, int value) => total + value,
       ) {
    if (this.completedRunsByLane.length != cpuBenchmarkPoolConcurrency ||
        this.completedRunsByLane.any((int value) => value <= 0) ||
        totalCompletedRuns < cpuBenchmarkPoolConcurrency ||
        durationMicroseconds <= 0 ||
        durationMicroseconds > _maximumDurationMicroseconds ||
        maximumObservedInFlightRuns != cpuBenchmarkPoolConcurrency) {
      throw const CpuBenchmarkFailure(
        'A concurrent CPU benchmark window violated its closed bounds.',
      );
    }
  }

  final List<int> completedRunsByLane;
  final int totalCompletedRuns;
  final int durationMicroseconds;
  final int maximumObservedInFlightRuns;

  Map<String, Object?> toMap() => <String, Object?>{
    'completedRunsByLane': completedRunsByLane,
    'totalCompletedRuns': totalCompletedRuns,
    'durationMicroseconds': durationMicroseconds,
    'maximumObservedInFlightRuns': maximumObservedInFlightRuns,
  };
}

/// Bounded public-API pool measurements appended to the serial evidence.
final class CpuBenchmarkPoolEvidence {
  CpuBenchmarkPoolEvidence._({
    required this.inputPreparationMicroseconds,
    required this.poolStartupMicroseconds,
    required this.firstConcurrentRound,
    required this.stabilizationRounds,
    required List<int> stabilizationRoundDurationMicroseconds,
    required List<int> stabilizationBatchMedianMicroseconds,
    required List<CpuBenchmarkPoolWindow> throughputWindows,
    required List<CpuBenchmarkAssignment> providerAssignments,
    required List<CpuBenchmarkRssSample> rssSamples,
    required CpuBenchmarkAssignment serialAssignment,
  }) : stabilizationRoundDurationMicroseconds = List<int>.unmodifiable(
         stabilizationRoundDurationMicroseconds,
       ),
       stabilizationBatchMedianMicroseconds = List<int>.unmodifiable(
         stabilizationBatchMedianMicroseconds,
       ),
       throughputWindows = List<CpuBenchmarkPoolWindow>.unmodifiable(
         throughputWindows,
       ),
       providerAssignments = List<CpuBenchmarkAssignment>.unmodifiable(
         providerAssignments,
       ),
       rssSamples = List<CpuBenchmarkRssSample>.unmodifiable(rssSamples) {
    _positiveDuration(inputPreparationMicroseconds);
    _positiveDuration(poolStartupMicroseconds);
    _validatePoolStabilizationEvidence(
      roundCount: stabilizationRounds,
      roundDurationMicroseconds: this.stabilizationRoundDurationMicroseconds,
      batchMedians: this.stabilizationBatchMedianMicroseconds,
    );
    if (this.throughputWindows.length != _throughputWindowCount ||
        this.throughputWindows.any(
          (CpuBenchmarkPoolWindow value) =>
              value.durationMicroseconds < _throughputWindowMicroseconds,
        ) ||
        this.providerAssignments.length != cpuBenchmarkPoolSize ||
        this.providerAssignments.any(
          (CpuBenchmarkAssignment assignment) =>
              !_sameCpuBenchmarkAssignment(assignment, serialAssignment),
        ) ||
        this.rssSamples.length != cpuBenchmarkPoolRssPhases.length) {
      throw const CpuBenchmarkFailure(
        'The concurrent CPU benchmark evidence is incomplete.',
      );
    }
    for (var index = 0; index < this.rssSamples.length; index += 1) {
      if (this.rssSamples[index].phase != cpuBenchmarkPoolRssPhases[index]) {
        throw const CpuBenchmarkFailure(
          'The concurrent CPU benchmark RSS phases are inconsistent.',
        );
      }
    }
  }

  final int inputPreparationMicroseconds;
  final int poolStartupMicroseconds;
  final CpuBenchmarkPoolWindow firstConcurrentRound;
  final int stabilizationRounds;
  final List<int> stabilizationRoundDurationMicroseconds;
  final List<int> stabilizationBatchMedianMicroseconds;
  final List<CpuBenchmarkPoolWindow> throughputWindows;
  final List<CpuBenchmarkAssignment> providerAssignments;
  final List<CpuBenchmarkRssSample> rssSamples;

  Map<String, Object?> toMap() => <String, Object?>{
    'executionSurface': 'public-ort-session-pool',
    'configuration': <String, Object?>{
      'poolSize': cpuBenchmarkPoolSize,
      'concurrency': cpuBenchmarkPoolConcurrency,
      'workerProtocolVersion': cpuBenchmarkPoolWorkerProtocolVersion,
      'maxPendingRunsPerWorker': cpuBenchmarkPoolMaxPendingRunsPerWorker,
      'maxMessageBytes': cpuBenchmarkPoolMaxMessageBytes,
      'maxOutstandingInputBytesPerWorker':
          cpuBenchmarkPoolMaxOutstandingInputBytesPerWorker,
      'inputReservationBytesPerRun':
          cpuBenchmarkPoolInputReservationBytesPerRun,
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
    },
    'timing': <String, Object?>{
      'durationUnit': 'microseconds',
      'clockScope': 'process-local-monotonic-stopwatch',
      'inputPreparationScope': 'input-fixture-to-immutable-isolate-tensor-copy',
      'poolStartupScope': 'two-worker-runtime-session-ready',
      'firstConcurrentRoundScope':
          'two-runs-admitted-before-await-through-output-bit-validation',
      'stabilizationRoundScope':
          'two-concurrent-runs-through-output-bit-validation',
      'throughputWindowTargetMicroseconds': _throughputWindowMicroseconds,
      'throughputWindowStopRule':
          'two-lanes-stop-admission-at-target-then-drain',
      'assignmentScope':
          'separate-strict-two-worker-pool-after-all-pool-timing',
    },
    'stabilization': <String, Object?>{
      'method': 'bounded-batch-median-relative-change',
      'batchSize': _poolStabilizationBatchSize,
      'thresholdBasisPoints': _poolStabilizationThresholdBasisPoints,
      'requiredConsecutiveTransitions': _poolStabilizationRequiredTransitions,
      'maximumRounds': _maximumPoolStabilizationRounds,
      'actualRounds': stabilizationRounds,
      'roundDurationMicroseconds': stabilizationRoundDurationMicroseconds,
      'batchMedianMicroseconds': stabilizationBatchMedianMicroseconds,
      'result': 'stabilized',
    },
    'measurements': <String, Object?>{
      'inputPreparationMicroseconds': <int>[inputPreparationMicroseconds],
      'poolStartupMicroseconds': <int>[poolStartupMicroseconds],
      'firstConcurrentRound': firstConcurrentRound.toMap(),
      'throughput': <Map<String, Object?>>[
        for (final CpuBenchmarkPoolWindow window in throughputWindows)
          window.toMap(),
      ],
    },
    'providerAssignments': <Map<String, Object?>>[
      for (final CpuBenchmarkAssignment assignment in providerAssignments)
        assignment.toMap(),
    ],
    'resources': <String, Object?>{
      'rssScope': 'total-process',
      'rssSamples': <Map<String, Object?>>[
        for (final CpuBenchmarkRssSample sample in rssSamples) sample.toMap(),
      ],
      'nativeRss': <String, Object?>{'status': 'not-exposed-by-target-api'},
      'cpuUtilization': <String, Object?>{
        'status': 'not-exposed-by-target-api',
      },
    },
    'lifecycle': <String, Object?>{
      'initialConcurrentOccupancy': 'passed',
      'zeroOutstandingBeforeClose': 'passed',
      'idempotentClose': 'passed',
      'zeroOutstandingAfterClose': 'passed',
      'strictAssignmentConcurrentOccupancy': 'passed',
      'strictAssignmentPoolClosed': 'passed',
      'temporaryAssignmentArtifacts': 'deleted',
    },
  };
}

/// One total-process RSS observation at a named lifecycle phase.
final class CpuBenchmarkRssSample {
  const CpuBenchmarkRssSample({
    required this.phase,
    required this.currentBytes,
    required this.peakBytes,
  });

  final String phase;
  final int currentBytes;
  final int peakBytes;

  Map<String, Object?> toMap() => <String, Object?>{
    'phase': phase,
    'currentBytes': currentBytes,
    'peakBytes': peakBytes,
  };
}

typedef CpuBenchmarkRssReader = ({int currentBytes, int peakBytes}) Function();
typedef CpuBenchmarkMonotonicReader = int Function();

/// A bounded, path-free fragment emitted by one fresh target process.
final class CpuBenchmarkFragment {
  CpuBenchmarkFragment._({
    required this.launchChallenge,
    required this.processId,
    required this.identity,
    required this.runtimeLoadMicroseconds,
    required this.sessionCreateMicroseconds,
    required this.dataPreparationMicroseconds,
    required this.firstRun,
    required this.stabilizationRuns,
    required this.stabilizationInferenceMicroseconds,
    required this.stabilizationBatchMedians,
    required this.warmRuns,
    required this.throughputWindows,
    required this.assignment,
    required this.rssSamples,
    required this.poolEvidence,
  }) {
    _validateStabilizationEvidence(
      runCount: stabilizationRuns,
      inferenceMicroseconds: stabilizationInferenceMicroseconds,
      batchMedians: stabilizationBatchMedians,
    );
  }

  final String launchChallenge;
  final int processId;
  final CpuBenchmarkTargetIdentity identity;
  final int runtimeLoadMicroseconds;
  final int sessionCreateMicroseconds;
  final int dataPreparationMicroseconds;
  final CpuBenchmarkRunSample firstRun;
  final int stabilizationRuns;
  final List<int> stabilizationInferenceMicroseconds;
  final List<int> stabilizationBatchMedians;
  final List<CpuBenchmarkRunSample> warmRuns;
  final List<({int completedRuns, int durationMicroseconds})> throughputWindows;
  final CpuBenchmarkAssignment assignment;
  final List<CpuBenchmarkRssSample> rssSamples;
  final CpuBenchmarkPoolEvidence poolEvidence;

  Map<String, Object?> toMap() => <String, Object?>{
    'schemaVersion': cpuBenchmarkFragmentSchemaVersion,
    'result': 'measured',
    'purpose': 'measurement-only-target-fragment',
    'protocol': <String, Object?>{
      'id': cpuBenchmarkProtocolId,
      'version': cpuBenchmarkProtocolVersion,
      'descriptorSha256': cpuBenchmarkProtocolDescriptorSha256,
      'targetFragmentSchemaSha256': cpuBenchmarkTargetFragmentSchemaSha256,
    },
    'launchChallenge': launchChallenge,
    'processId': processId,
    'freshProcessRequired': true,
    'executionSurface': 'synchronous-and-isolate-pool-public-api',
    'model': <String, Object?>{
      'id': cpuBenchmarkModelId,
      'onnxSha256': cpuBenchmarkModelSha256,
      'onnxSizeBytes': cpuBenchmarkModelBytes,
      'inputFixtureSha256': cpuBenchmarkInputSha256,
      'inputFixtureSizeBytes': cpuBenchmarkInputBytes,
      'referenceOutputSha256': cpuBenchmarkOutputSha256,
      'referenceOutputSizeBytes': cpuBenchmarkOutputBytes,
      'metadataSha256': cpuBenchmarkMetadataSha256,
      'metadataSizeBytes': cpuBenchmarkMetadataBytes,
      'generatorId': cpuBenchmarkGeneratorId,
      'generatorSha256': cpuBenchmarkGeneratorSha256,
      'generatorSizeBytes': cpuBenchmarkGeneratorBytes,
      'opset': 17,
      'precision': 'float32',
      'inputName': cpuBenchmarkInputName,
      'inputShape': cpuBenchmarkInputShape,
      'outputName': cpuBenchmarkOutputName,
      'outputShape': cpuBenchmarkOutputShape,
      'referencePolicy': <String, Object?>{
        'comparison': 'exact-ieee754-binary32-bits',
        'absoluteTolerance': 0,
        'relativeTolerance': 0,
        'nanPolicy': 'forbid',
        'infinityPolicy': 'forbid',
      },
    },
    'runtime': identity.toMap(),
    'session': <String, Object?>{
      'poolSize': 1,
      'concurrency': 1,
      'graphOptimization': 'all',
      'executionMode': 'sequential',
      'intraOpThreads': 1,
      'interOpThreads': 1,
      'cpuMemoryArena': true,
      'memoryPattern': true,
      'deterministicCompute': true,
      'timedProviderPolicy': 'cpu-required-report-fallback',
      'throughputCycle': 'inference-output-copy-bit-validation-result-disposal',
      'copyBoundaries': <String>[
        'model-bytes-to-session',
        'input-fixture-to-dart-float32',
        'dart-float32-to-native-tensor',
        'native-output-to-dart-float32',
      ],
    },
    'timing': <String, Object?>{
      'durationUnit': 'microseconds',
      'clockScope': 'process-local-monotonic-stopwatch',
      'runtimeLoadScope': 'ort-runtime-open',
      'sessionCreateScope': 'verified-model-bytes-to-session',
      'dataPreparationScope': 'dart-float32-to-native-tensor-copy',
      'inferenceScope': 'synchronous-session-run-only',
      'outputMaterializationScope': 'native-output-to-dart-float32-copy-only',
      'throughputWindowTargetMicroseconds': _throughputWindowMicroseconds,
      'throughputWindowStopRule':
          'complete-runs-until-monotonic-elapsed-gte-target',
      'assignmentScope': 'separate-profiled-session-after-all-timing',
    },
    'stabilization': <String, Object?>{
      'method': 'bounded-batch-median-relative-change',
      'batchSize': _stabilizationBatchSize,
      'thresholdBasisPoints': _stabilizationThresholdBasisPoints,
      'requiredConsecutiveTransitions': _stabilizationRequiredTransitions,
      'maximumRuns': _maximumStabilizationRuns,
      'actualRuns': stabilizationRuns,
      'inferenceMicroseconds': stabilizationInferenceMicroseconds,
      'batchMedianMicroseconds': stabilizationBatchMedians,
      'result': 'stabilized',
    },
    'measurements': <String, Object?>{
      'coldRuntimeLoadMicroseconds': <int>[runtimeLoadMicroseconds],
      'sessionCreateMicroseconds': <int>[sessionCreateMicroseconds],
      'dataPreparationMicroseconds': <int>[dataPreparationMicroseconds],
      'firstRunMicroseconds': <int>[firstRun.inferenceMicroseconds],
      'firstOutputMaterializationMicroseconds': <int>[
        firstRun.outputMaterializationMicroseconds,
      ],
      'warmRunMicroseconds': <int>[
        for (final CpuBenchmarkRunSample sample in warmRuns)
          sample.inferenceMicroseconds,
      ],
      'warmOutputMaterializationMicroseconds': <int>[
        for (final CpuBenchmarkRunSample sample in warmRuns)
          sample.outputMaterializationMicroseconds,
      ],
      'throughput': <Map<String, Object?>>[
        for (final window in throughputWindows)
          <String, Object?>{
            'completedRuns': window.completedRuns,
            'durationMicroseconds': window.durationMicroseconds,
          },
      ],
    },
    'providerAssignment': assignment.toMap(),
    'resources': <String, Object?>{
      'rssScope': 'total-process',
      'rssSamples': <Map<String, Object?>>[
        for (final CpuBenchmarkRssSample sample in rssSamples) sample.toMap(),
      ],
      'nativeRss': <String, Object?>{'status': 'not-exposed-by-target-api'},
      'cpuUtilization': <String, Object?>{
        'status': 'not-exposed-by-target-api',
      },
      'thermalStart': 'host-evidence-required',
      'thermalEnd': 'host-evidence-required',
      'powerMode': 'host-evidence-required',
    },
    'lifecycle': <String, Object?>{
      'doubleDispose': 'passed',
      'zeroPendingWork': 'passed',
      'temporaryAssignmentArtifacts': 'deleted',
    },
    'poolEvidence': poolEvidence.toMap(),
    'claimBoundary':
        'One fresh-process target fragment containing serial and bounded '
        'session-pool measurements only; not a performance baseline, '
        'regression threshold, provider qualification, platform support '
        'claim, release approval, or cross-target evidence.',
  };

  String toJsonString() {
    final String value = jsonEncode(toMap());
    if (value.contains('\n') ||
        value.contains('\r') ||
        utf8.encode(value).length > maximumCpuBenchmarkFragmentBytes) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark fragment exceeds its publication bounds.',
      );
    }
    return value;
  }
}

/// Runs one bounded target fragment after all committed assets are loaded.
Future<CpuBenchmarkFragment> runDesktopCpuBenchmark({
  AssetBundle? assets,
  CpuBenchmarkProbeFactory? createProbe,
  CpuBenchmarkPoolProbeFactory? createPoolProbe,
  CpuBenchmarkRssReader? readRss,
  CpuBenchmarkMonotonicReader? readMonotonicMicroseconds,
  String? launchChallenge,
  int? processId,
}) async {
  final String resolvedLaunchChallenge =
      launchChallenge ?? Platform.environment[cpuBenchmarkChallengeKey] ?? '';
  final int resolvedProcessId = processId ?? pid;
  if (!_isExactLowercaseHex64(resolvedLaunchChallenge)) {
    throw const CpuBenchmarkFailure(
      'The CPU benchmark launch challenge is invalid.',
    );
  }
  if (resolvedProcessId <= 0 || resolvedProcessId > _maximumProcessId) {
    throw const CpuBenchmarkFailure(
      'The CPU benchmark process identity is invalid.',
    );
  }
  final CpuBenchmarkAssets loaded = await CpuBenchmarkAssets.load(
    assets: assets,
  );
  final CpuBenchmarkRssReader rssReader = readRss ?? _readProcessRss;
  final List<CpuBenchmarkRssSample> rssSamples = <CpuBenchmarkRssSample>[];
  void sampleRss(String phase) {
    final ({int currentBytes, int peakBytes}) sample = rssReader();
    if (sample.currentBytes <= 0 ||
        sample.currentBytes > _maximumRssBytes ||
        sample.peakBytes <= 0 ||
        sample.peakBytes > _maximumRssBytes ||
        sample.peakBytes < sample.currentBytes) {
      throw const CpuBenchmarkFailure(
        'The target returned an invalid process RSS observation.',
      );
    }
    rssSamples.add(
      CpuBenchmarkRssSample(
        phase: phase,
        currentBytes: sample.currentBytes,
        peakBytes: sample.peakBytes,
      ),
    );
  }

  final Stopwatch monotonicStopwatch = Stopwatch()..start();
  final CpuBenchmarkMonotonicReader monotonic =
      readMonotonicMicroseconds ?? () => monotonicStopwatch.elapsedMicroseconds;
  CpuBenchmarkProbe? probe;
  var settled = false;
  try {
    sampleRss('after-asset-preparation');
    probe = (createProbe ?? _OrtCpuBenchmarkProbe.open)(loaded);
    final CpuBenchmarkTargetIdentity identity = probe.identity;
    final int runtimeLoadMicroseconds = probe.runtimeLoadMicroseconds;
    final int sessionCreateMicroseconds = probe.sessionCreateMicroseconds;
    final int dataPreparationMicroseconds = probe.dataPreparationMicroseconds;
    _positiveDuration(runtimeLoadMicroseconds);
    _positiveDuration(sessionCreateMicroseconds);
    _positiveDuration(dataPreparationMicroseconds);
    sampleRss('after-session-create');

    final CpuBenchmarkRunSample firstRun = probe.runOnce();
    _validateRunSample(firstRun);
    sampleRss('after-first-run');

    final _StabilizationResult stabilization = _stabilize(probe);
    sampleRss('after-stabilization');

    final List<CpuBenchmarkRunSample> warmRuns = <CpuBenchmarkRunSample>[];
    for (var index = 0; index < _measuredWarmRuns; index += 1) {
      final CpuBenchmarkRunSample sample = probe.runOnce();
      _validateRunSample(sample);
      warmRuns.add(sample);
    }
    sampleRss('after-warm-runs');

    final List<({int completedRuns, int durationMicroseconds})>
    throughputWindows = <({int completedRuns, int durationMicroseconds})>[];
    for (var index = 0; index < _throughputWindowCount; index += 1) {
      final int start = monotonic();
      if (start < 0) {
        throw const CpuBenchmarkFailure(
          'The monotonic benchmark clock returned an invalid value.',
        );
      }
      var completedRuns = 0;
      var elapsed = 0;
      do {
        final CpuBenchmarkRunSample sample = probe.runOnce();
        _validateRunSample(sample);
        completedRuns += 1;
        if (completedRuns > _maximumRunsPerThroughputWindow) {
          throw const CpuBenchmarkFailure(
            'A throughput window exceeded its run bound.',
          );
        }
        final int now = monotonic();
        if (now < start || now - start > _maximumDurationMicroseconds) {
          throw const CpuBenchmarkFailure(
            'The monotonic benchmark clock violated its bounds.',
          );
        }
        elapsed = now - start;
      } while (elapsed < _throughputWindowMicroseconds);
      if (completedRuns <= 0 || elapsed <= 0) {
        throw const CpuBenchmarkFailure(
          'A throughput window produced no bounded measurement.',
        );
      }
      throughputWindows.add((
        completedRuns: completedRuns,
        durationMicroseconds: elapsed,
      ));
    }
    sampleRss('after-throughput');

    final CpuBenchmarkAssignment assignment = probe.captureAssignment();
    if (assignment.reportedName != identity.cpuReportedName) {
      throw const CpuBenchmarkFailure(
        'CPU assignment evidence contradicted the runtime identity.',
      );
    }
    sampleRss('after-assignment-evidence');

    probe.dispose();
    probe.dispose();
    settled = true;
    sampleRss('after-dispose');
    final CpuBenchmarkPoolEvidence poolEvidence = await _runPoolEvidence(
      assets: loaded,
      identity: identity,
      serialAssignment: assignment,
      minimumPeakBytes: rssSamples.last.peakBytes,
      createProbe: createPoolProbe ?? _OrtCpuBenchmarkPoolProbe.open,
      readRss: rssReader,
      readMonotonicMicroseconds: monotonic,
    );
    return CpuBenchmarkFragment._(
      launchChallenge: resolvedLaunchChallenge,
      processId: resolvedProcessId,
      identity: identity,
      runtimeLoadMicroseconds: runtimeLoadMicroseconds,
      sessionCreateMicroseconds: sessionCreateMicroseconds,
      dataPreparationMicroseconds: dataPreparationMicroseconds,
      firstRun: firstRun,
      stabilizationRuns: stabilization.runCount,
      stabilizationInferenceMicroseconds: List<int>.unmodifiable(
        stabilization.inferenceMicroseconds,
      ),
      stabilizationBatchMedians: List<int>.unmodifiable(
        stabilization.batchMedians,
      ),
      warmRuns: List<CpuBenchmarkRunSample>.unmodifiable(warmRuns),
      throughputWindows: List.unmodifiable(throughputWindows),
      assignment: assignment,
      rssSamples: List<CpuBenchmarkRssSample>.unmodifiable(rssSamples),
      poolEvidence: poolEvidence,
    );
  } finally {
    monotonicStopwatch.stop();
    if (!settled) {
      try {
        probe?.dispose();
      } on Object {
        // The authoritative measurement failure remains primary.
      }
    }
  }
}

Future<CpuBenchmarkPoolEvidence> _runPoolEvidence({
  required CpuBenchmarkAssets assets,
  required CpuBenchmarkTargetIdentity identity,
  required CpuBenchmarkAssignment serialAssignment,
  required int minimumPeakBytes,
  required CpuBenchmarkPoolProbeFactory createProbe,
  required CpuBenchmarkRssReader readRss,
  required CpuBenchmarkMonotonicReader readMonotonicMicroseconds,
}) async {
  final List<CpuBenchmarkRssSample> rssSamples = <CpuBenchmarkRssSample>[];
  var previousPeakBytes = minimumPeakBytes;
  void sampleRss(String phase) {
    final ({int currentBytes, int peakBytes}) sample = readRss();
    if (sample.currentBytes <= 0 ||
        sample.currentBytes > _maximumRssBytes ||
        sample.peakBytes <= 0 ||
        sample.peakBytes > _maximumRssBytes ||
        sample.peakBytes < sample.currentBytes ||
        sample.peakBytes < previousPeakBytes) {
      throw const CpuBenchmarkFailure(
        'The pool returned an invalid process RSS observation.',
      );
    }
    previousPeakBytes = sample.peakBytes;
    rssSamples.add(
      CpuBenchmarkRssSample(
        phase: phase,
        currentBytes: sample.currentBytes,
        peakBytes: sample.peakBytes,
      ),
    );
  }

  CpuBenchmarkPoolProbe? probe;
  var inputPrepared = false;
  var authoritativeFailure = false;
  try {
    probe = await createProbe(assets, identity, () {
      if (inputPrepared) {
        throw const CpuBenchmarkFailure(
          'The pool input-preparation checkpoint was repeated.',
        );
      }
      inputPrepared = true;
      sampleRss(cpuBenchmarkPoolRssPhases[0]);
    });
    if (!inputPrepared) {
      throw const CpuBenchmarkFailure(
        'The pool omitted its input-preparation checkpoint.',
      );
    }
    _positiveDuration(probe.inputPreparationMicroseconds);
    _positiveDuration(probe.poolStartupMicroseconds);
    sampleRss(cpuBenchmarkPoolRssPhases[1]);

    final CpuBenchmarkPoolWindow firstConcurrentRound =
        await _runExactPoolRound(probe, readMonotonicMicroseconds);
    sampleRss(cpuBenchmarkPoolRssPhases[2]);

    final _PoolStabilizationResult stabilization = await _stabilizePool(
      probe,
      readMonotonicMicroseconds,
    );
    sampleRss(cpuBenchmarkPoolRssPhases[3]);

    final List<CpuBenchmarkPoolWindow> throughputWindows =
        <CpuBenchmarkPoolWindow>[];
    for (var index = 0; index < _throughputWindowCount; index += 1) {
      throughputWindows.add(
        await _runPoolThroughputWindow(probe, readMonotonicMicroseconds),
      );
    }
    sampleRss(cpuBenchmarkPoolRssPhases[4]);
    _requirePoolDrained(probe, 'before close');

    final Future<void> firstClose = probe.close();
    final Future<void> secondClose = probe.close();
    if (!identical(firstClose, secondClose)) {
      throw const CpuBenchmarkFailure(
        'The pool close operation was not idempotent.',
      );
    }
    await firstClose;
    _requirePoolDrained(probe, 'after close');
    sampleRss(cpuBenchmarkPoolRssPhases[5]);

    final List<CpuBenchmarkAssignment> assignments = await probe
        .captureAssignments();
    if (assignments.length != cpuBenchmarkPoolSize ||
        assignments.any(
          (CpuBenchmarkAssignment assignment) =>
              assignment.reportedName != identity.cpuReportedName ||
              !_sameCpuBenchmarkAssignment(assignment, serialAssignment),
        )) {
      throw const CpuBenchmarkFailure(
        'The strict pool assignment receipts are inconsistent.',
      );
    }
    sampleRss(cpuBenchmarkPoolRssPhases[6]);
    return CpuBenchmarkPoolEvidence._(
      inputPreparationMicroseconds: probe.inputPreparationMicroseconds,
      poolStartupMicroseconds: probe.poolStartupMicroseconds,
      firstConcurrentRound: firstConcurrentRound,
      stabilizationRounds: stabilization.roundCount,
      stabilizationRoundDurationMicroseconds:
          stabilization.roundDurationMicroseconds,
      stabilizationBatchMedianMicroseconds: stabilization.batchMedians,
      throughputWindows: throughputWindows,
      providerAssignments: assignments,
      rssSamples: rssSamples,
      serialAssignment: serialAssignment,
    );
  } on Object {
    authoritativeFailure = true;
    rethrow;
  } finally {
    final CpuBenchmarkPoolProbe? current = probe;
    if (current != null) {
      try {
        await current.close();
      } on Object {
        if (!authoritativeFailure) rethrow;
      }
    }
  }
}

Future<CpuBenchmarkPoolWindow> _runExactPoolRound(
  CpuBenchmarkPoolProbe probe,
  CpuBenchmarkMonotonicReader monotonic,
) async {
  final int start = _readPoolClockStart(monotonic);
  final List<Future<void>> runs = <Future<void>>[];
  Object? firstError;
  StackTrace? firstStackTrace;
  for (var index = 0; index < cpuBenchmarkPoolConcurrency; index += 1) {
    try {
      runs.add(probe.startRun());
    } on Object catch (error, stackTrace) {
      firstError ??= error;
      firstStackTrace ??= stackTrace;
      break;
    }
  }
  if (runs.length == cpuBenchmarkPoolConcurrency) {
    try {
      _requireInitialPoolOccupancy(probe);
    } on Object catch (error, stackTrace) {
      firstError ??= error;
      firstStackTrace ??= stackTrace;
    }
  }
  try {
    await Future.wait<void>(runs);
  } on Object catch (error, stackTrace) {
    firstError ??= error;
    firstStackTrace ??= stackTrace;
  }
  try {
    _requirePoolDrained(probe, 'after a concurrent round');
  } on Object catch (error, stackTrace) {
    firstError ??= error;
    firstStackTrace ??= stackTrace;
  }
  if (firstError case final Object error) {
    Error.throwWithStackTrace(error, firstStackTrace!);
  }
  final int elapsed = _readPoolElapsed(monotonic, start);
  return CpuBenchmarkPoolWindow(
    completedRunsByLane: const <int>[1, 1],
    durationMicroseconds: elapsed,
    maximumObservedInFlightRuns: cpuBenchmarkPoolConcurrency,
  );
}

final class _PoolStabilizationResult {
  const _PoolStabilizationResult({
    required this.roundCount,
    required this.roundDurationMicroseconds,
    required this.batchMedians,
  });

  final int roundCount;
  final List<int> roundDurationMicroseconds;
  final List<int> batchMedians;
}

Future<_PoolStabilizationResult> _stabilizePool(
  CpuBenchmarkPoolProbe probe,
  CpuBenchmarkMonotonicReader monotonic,
) async {
  final List<int> roundDurations = <int>[];
  final List<int> medians = <int>[];
  var stableTransitions = 0;
  var roundCount = 0;
  while (roundCount < _maximumPoolStabilizationRounds) {
    final List<int> batch = <int>[];
    for (var index = 0; index < _poolStabilizationBatchSize; index += 1) {
      final CpuBenchmarkPoolWindow round = await _runExactPoolRound(
        probe,
        monotonic,
      );
      batch.add(round.durationMicroseconds);
      roundDurations.add(round.durationMicroseconds);
      roundCount += 1;
    }
    batch.sort();
    final int median = batch[batch.length ~/ 2];
    if (medians.isNotEmpty &&
        (median - medians.last).abs() * 10000 <=
            medians.last * _poolStabilizationThresholdBasisPoints) {
      stableTransitions += 1;
    } else {
      stableTransitions = 0;
    }
    medians.add(median);
    if (stableTransitions >= _poolStabilizationRequiredTransitions) {
      return _PoolStabilizationResult(
        roundCount: roundCount,
        roundDurationMicroseconds: List<int>.unmodifiable(roundDurations),
        batchMedians: List<int>.unmodifiable(medians),
      );
    }
  }
  throw const CpuBenchmarkFailure(
    'Concurrent inference did not stabilize within the fixed round bound.',
  );
}

Future<CpuBenchmarkPoolWindow> _runPoolThroughputWindow(
  CpuBenchmarkPoolProbe probe,
  CpuBenchmarkMonotonicReader monotonic,
) async {
  final int start = _readPoolClockStart(monotonic);
  var inFlight = 0;
  var maximumInFlight = 0;
  var stopAdmissions = false;
  Object? firstError;
  StackTrace? firstStackTrace;
  void fail(Object error, StackTrace stackTrace) {
    firstError ??= error;
    firstStackTrace ??= stackTrace;
    stopAdmissions = true;
  }

  Future<int> runLane() async {
    var completedRuns = 0;
    while (!stopAdmissions) {
      inFlight += 1;
      if (inFlight > maximumInFlight) maximumInFlight = inFlight;
      if (inFlight > cpuBenchmarkPoolConcurrency) {
        fail(
          const CpuBenchmarkFailure('The pool exceeded its fixed concurrency.'),
          StackTrace.current,
        );
        inFlight -= 1;
        break;
      }
      try {
        await probe.startRun();
      } on Object catch (error, stackTrace) {
        fail(error, stackTrace);
      } finally {
        inFlight -= 1;
      }
      if (firstError != null) break;
      completedRuns += 1;
      if (completedRuns > _maximumRunsPerThroughputWindow) {
        fail(
          const CpuBenchmarkFailure(
            'A concurrent throughput lane exceeded its run bound.',
          ),
          StackTrace.current,
        );
        break;
      }
      try {
        final int elapsed = _readPoolElapsed(monotonic, start);
        if (elapsed >= _throughputWindowMicroseconds) {
          stopAdmissions = true;
        }
      } on Object catch (error, stackTrace) {
        fail(error, stackTrace);
      }
    }
    return completedRuns;
  }

  final Future<int> firstLane = runLane();
  final Future<int> secondLane = runLane();
  if (firstError == null) {
    try {
      _requireInitialPoolOccupancy(probe);
    } on Object catch (error, stackTrace) {
      fail(error, stackTrace);
    }
  }
  final List<int> completedByLane = await Future.wait<int>(<Future<int>>[
    firstLane,
    secondLane,
  ]);
  if (inFlight != 0) {
    fail(
      const CpuBenchmarkFailure(
        'The pool retained controller-side in-flight work after drain.',
      ),
      StackTrace.current,
    );
  }
  try {
    _requirePoolDrained(probe, 'after a throughput window');
  } on Object catch (error, stackTrace) {
    fail(error, stackTrace);
  }
  if (firstError case final Object error) {
    Error.throwWithStackTrace(error, firstStackTrace!);
  }
  final int elapsed = _readPoolElapsed(monotonic, start);
  if (elapsed < _throughputWindowMicroseconds) {
    throw const CpuBenchmarkFailure(
      'A concurrent throughput window stopped before its target.',
    );
  }
  return CpuBenchmarkPoolWindow(
    completedRunsByLane: completedByLane,
    durationMicroseconds: elapsed,
    maximumObservedInFlightRuns: maximumInFlight,
  );
}

void _validatePoolStabilizationEvidence({
  required int roundCount,
  required List<int> roundDurationMicroseconds,
  required List<int> batchMedians,
}) {
  if (roundCount <
          _poolStabilizationBatchSize *
              (_poolStabilizationRequiredTransitions + 1) ||
      roundCount > _maximumPoolStabilizationRounds ||
      roundCount % _poolStabilizationBatchSize != 0 ||
      roundDurationMicroseconds.length != roundCount ||
      batchMedians.length != roundCount ~/ _poolStabilizationBatchSize) {
    throw const CpuBenchmarkFailure(
      'The pool stabilization evidence is inconsistent.',
    );
  }
  var stableTransitions = 0;
  var reachedStability = false;
  for (var batchIndex = 0; batchIndex < batchMedians.length; batchIndex += 1) {
    final int start = batchIndex * _poolStabilizationBatchSize;
    final List<int> batch = roundDurationMicroseconds.sublist(
      start,
      start + _poolStabilizationBatchSize,
    )..sort();
    batch.forEach(_positiveDuration);
    final int median = batch[batch.length ~/ 2];
    if (batchMedians[batchIndex] != median) {
      throw const CpuBenchmarkFailure(
        'The pool stabilization medians are not recomputable.',
      );
    }
    if (batchIndex > 0 &&
        (median - batchMedians[batchIndex - 1]).abs() * 10000 <=
            batchMedians[batchIndex - 1] *
                _poolStabilizationThresholdBasisPoints) {
      stableTransitions += 1;
    } else {
      stableTransitions = 0;
    }
    if (stableTransitions >= _poolStabilizationRequiredTransitions) {
      if (batchIndex != batchMedians.length - 1) {
        throw const CpuBenchmarkFailure(
          'The pool stabilization evidence exceeded its stop rule.',
        );
      }
      reachedStability = true;
    }
  }
  if (!reachedStability) {
    throw const CpuBenchmarkFailure(
      'The pool stabilization evidence did not reach its stop rule.',
    );
  }
}

int _readPoolClockStart(CpuBenchmarkMonotonicReader monotonic) {
  final int start = monotonic();
  if (start < 0 || start > _maximumDurationMicroseconds) {
    throw const CpuBenchmarkFailure(
      'The monotonic pool benchmark clock returned an invalid value.',
    );
  }
  return start;
}

int _readPoolElapsed(CpuBenchmarkMonotonicReader monotonic, int start) {
  final int now = monotonic();
  if (now <= start || now - start > _maximumDurationMicroseconds) {
    throw const CpuBenchmarkFailure(
      'The monotonic pool benchmark clock violated its bounds.',
    );
  }
  return now - start;
}

void _requireInitialPoolOccupancy(CpuBenchmarkPoolProbe probe) {
  if (probe.outstandingRuns != cpuBenchmarkPoolConcurrency ||
      probe.outstandingInputBytes !=
          cpuBenchmarkPoolConcurrency *
              cpuBenchmarkPoolInputReservationBytesPerRun) {
    throw const CpuBenchmarkFailure(
      'The pool did not admit one simultaneous run per worker.',
    );
  }
}

void _requirePoolDrained(CpuBenchmarkPoolProbe probe, String phase) {
  if (probe.outstandingRuns != 0 || probe.outstandingInputBytes != 0) {
    throw CpuBenchmarkFailure('The pool retained bounded work $phase.');
  }
}

final class _StabilizationResult {
  const _StabilizationResult({
    required this.runCount,
    required this.inferenceMicroseconds,
    required this.batchMedians,
  });

  final int runCount;
  final List<int> inferenceMicroseconds;
  final List<int> batchMedians;
}

_StabilizationResult _stabilize(CpuBenchmarkProbe probe) {
  final List<int> inferenceMicroseconds = <int>[];
  final List<int> medians = <int>[];
  var stableTransitions = 0;
  var runCount = 0;
  while (runCount < _maximumStabilizationRuns) {
    final List<int> batch = <int>[];
    for (var index = 0; index < _stabilizationBatchSize; index += 1) {
      final CpuBenchmarkRunSample sample = probe.runOnce();
      _validateRunSample(sample);
      batch.add(sample.inferenceMicroseconds);
      inferenceMicroseconds.add(sample.inferenceMicroseconds);
      runCount += 1;
    }
    batch.sort();
    final int median = batch[batch.length ~/ 2];
    if (medians.isNotEmpty &&
        (median - medians.last).abs() * 10000 <=
            medians.last * _stabilizationThresholdBasisPoints) {
      stableTransitions += 1;
    } else {
      stableTransitions = 0;
    }
    medians.add(median);
    if (stableTransitions >= _stabilizationRequiredTransitions) {
      return _StabilizationResult(
        runCount: runCount,
        inferenceMicroseconds: inferenceMicroseconds,
        batchMedians: medians,
      );
    }
  }
  throw const CpuBenchmarkFailure(
    'Warm inference did not stabilize within the fixed run bound.',
  );
}

void _validateStabilizationEvidence({
  required int runCount,
  required List<int> inferenceMicroseconds,
  required List<int> batchMedians,
}) {
  if (runCount <
          _stabilizationBatchSize * (_stabilizationRequiredTransitions + 1) ||
      runCount > _maximumStabilizationRuns ||
      runCount % _stabilizationBatchSize != 0 ||
      inferenceMicroseconds.length != runCount ||
      batchMedians.length != runCount ~/ _stabilizationBatchSize) {
    throw const CpuBenchmarkFailure(
      'The CPU benchmark stabilization evidence is inconsistent.',
    );
  }

  var stableTransitions = 0;
  var reachedStability = false;
  for (var batchIndex = 0; batchIndex < batchMedians.length; batchIndex += 1) {
    final int start = batchIndex * _stabilizationBatchSize;
    final List<int> batch =
        inferenceMicroseconds.sublist(start, start + _stabilizationBatchSize)
          ..forEach(_positiveDuration)
          ..sort();
    final int median = batch[batch.length ~/ 2];
    if (batchMedians[batchIndex] != median) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark stabilization medians are not recomputable.',
      );
    }
    if (batchIndex > 0 &&
        (median - batchMedians[batchIndex - 1]).abs() * 10000 <=
            batchMedians[batchIndex - 1] * _stabilizationThresholdBasisPoints) {
      stableTransitions += 1;
    } else {
      stableTransitions = 0;
    }
    if (stableTransitions >= _stabilizationRequiredTransitions) {
      if (batchIndex != batchMedians.length - 1) {
        throw const CpuBenchmarkFailure(
          'The CPU benchmark stabilization evidence exceeded its stop rule.',
        );
      }
      reachedStability = true;
    }
  }
  if (!reachedStability) {
    throw const CpuBenchmarkFailure(
      'The CPU benchmark stabilization evidence did not reach its stop rule.',
    );
  }
}

void _validateRunSample(CpuBenchmarkRunSample sample) {
  _positiveDuration(sample.inferenceMicroseconds);
  _positiveDuration(sample.outputMaterializationMicroseconds);
}

void _positiveDuration(int value) {
  if (value <= 0 || value > _maximumDurationMicroseconds) {
    throw const CpuBenchmarkFailure(
      'The target returned an invalid benchmark duration.',
    );
  }
}

({int currentBytes, int peakBytes}) _readProcessRss() =>
    (currentBytes: ProcessInfo.currentRss, peakBytes: ProcessInfo.maxRss);

final RegExp _token = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$');
final RegExp _label = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._+() ,:=+-]{0,255}$');
final RegExp _semanticVersion = RegExp(
  r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
  r'(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$',
);
final RegExp _digest = RegExp(r'^[0-9a-f]{64}$');

bool _isExactLowercaseHex64(String value) =>
    value.length == 64 && _digest.hasMatch(value);

final class _OrtCpuBenchmarkProbe implements CpuBenchmarkProbe {
  _OrtCpuBenchmarkProbe._({
    required OrtRuntime runtime,
    required OrtSession session,
    required OrtTensor input,
    required Uint8List model,
    required Float32List referenceOutput,
    required this.identity,
    required this.runtimeLoadMicroseconds,
    required this.sessionCreateMicroseconds,
    required this.dataPreparationMicroseconds,
  }) : _runtime = runtime,
       _session = session,
       _input = input,
       _model = model,
       _referenceOutput = referenceOutput;

  static _OrtCpuBenchmarkProbe open(CpuBenchmarkAssets assets) {
    OrtRuntime? runtime;
    OrtSession? session;
    OrtTensor? input;
    try {
      final Stopwatch runtimeWatch = Stopwatch()..start();
      runtime = OrtRuntime.open(source: const OrtRuntimeSource.bundled());
      runtimeWatch.stop();
      final int runtimeLoad = runtimeWatch.elapsedMicroseconds;
      _positiveDuration(runtimeLoad);

      final Stopwatch sessionWatch = Stopwatch()..start();
      session = OrtSession.fromBytes(
        runtime: runtime,
        modelBytes: assets.model,
        modelId: cpuBenchmarkModelId,
        options: _sessionOptions(),
      );
      sessionWatch.stop();
      final int sessionCreate = sessionWatch.elapsedMicroseconds;
      _positiveDuration(sessionCreate);

      final Stopwatch preparationWatch = Stopwatch()..start();
      input = OrtTensor.fromFloat32List(
        runtime: runtime,
        values: assets.inputValues,
        shape: cpuBenchmarkInputShape,
      );
      preparationWatch.stop();
      final int preparation = preparationWatch.elapsedMicroseconds;
      _positiveDuration(preparation);

      final CpuBenchmarkTargetIdentity identity = _targetIdentity(
        runtime,
        session,
      );
      return _OrtCpuBenchmarkProbe._(
        runtime: runtime,
        session: session,
        input: input,
        model: assets.model,
        referenceOutput: assets.referenceOutput,
        identity: identity,
        runtimeLoadMicroseconds: runtimeLoad,
        sessionCreateMicroseconds: sessionCreate,
        dataPreparationMicroseconds: preparation,
      );
    } catch (_) {
      input?.dispose();
      input?.dispose();
      session?.dispose();
      session?.dispose();
      runtime?.dispose();
      runtime?.dispose();
      rethrow;
    }
  }

  OrtRuntime? _runtime;
  OrtSession? _session;
  OrtTensor? _input;
  final Uint8List _model;
  final Float32List _referenceOutput;
  var _disposed = false;

  @override
  final CpuBenchmarkTargetIdentity identity;

  @override
  final int runtimeLoadMicroseconds;

  @override
  final int sessionCreateMicroseconds;

  @override
  final int dataPreparationMicroseconds;

  @override
  CpuBenchmarkRunSample runOnce() {
    final OrtSession? session = _session;
    final OrtTensor? input = _input;
    if (_disposed || session == null || input == null) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark probe is already disposed.',
      );
    }
    OrtRunResult? result;
    try {
      final Stopwatch inferenceWatch = Stopwatch()..start();
      result = session.run(
        inputs: <String, OrtValue>{cpuBenchmarkInputName: input},
        outputNames: const <String>[cpuBenchmarkOutputName],
      );
      inferenceWatch.stop();
      final int inference = inferenceWatch.elapsedMicroseconds;
      _positiveDuration(inference);

      final OrtTensor output = result.tensor(cpuBenchmarkOutputName);
      if (!_sameShape(output.shape.dimensions, cpuBenchmarkOutputShape) ||
          output.elementType != OrtTensorElementType.float32) {
        throw const CpuBenchmarkFailure(
          'The CPU benchmark returned an unexpected output tensor.',
        );
      }
      final Stopwatch materializationWatch = Stopwatch()..start();
      final Float32List values = output.copyFloat32Data();
      materializationWatch.stop();
      final int materialization = materializationWatch.elapsedMicroseconds;
      _positiveDuration(materialization);
      _verifyReferenceOutput(values, _referenceOutput);
      return CpuBenchmarkRunSample(
        inferenceMicroseconds: inference,
        outputMaterializationMicroseconds: materialization,
      );
    } finally {
      result?.dispose();
      result?.dispose();
    }
  }

  @override
  CpuBenchmarkAssignment captureAssignment() {
    final OrtRuntime? runtime = _runtime;
    final OrtTensor? input = _input;
    if (_disposed || runtime == null || input == null) {
      throw const CpuBenchmarkFailure(
        'The CPU benchmark probe is already disposed.',
      );
    }
    Directory? artifactRoot;
    OrtSession? evidenceSession;
    OrtRunResult? result;
    var failed = false;
    try {
      artifactRoot = Directory.systemTemp.createTempSync(
        'fonix-cpu-benchmark-assignment-',
      );
      evidenceSession = OrtSession.fromBytes(
        runtime: runtime,
        modelBytes: _model,
        modelId: cpuBenchmarkModelId,
        options: _sessionOptions(
          artifactRoot: artifactRoot.path,
          strictAssignment: true,
        ),
      );
      result = evidenceSession.run(
        inputs: <String, OrtValue>{cpuBenchmarkInputName: input},
        outputNames: const <String>[cpuBenchmarkOutputName],
      );
      _verifyReferenceOutput(
        result.tensor(cpuBenchmarkOutputName).copyFloat32Data(),
        _referenceOutput,
      );
      final OrtProviderRunEvidence? evidence = result.providerEvidence;
      if (evidence == null || !evidence.isFullyAssignedTo('cpu')) {
        throw const CpuBenchmarkFailure(
          'The CPU benchmark returned no full-assignment evidence.',
        );
      }
      return CpuBenchmarkAssignment(
        providerId: 'cpu',
        reportedName: identity.cpuReportedName,
        nodeExecutionCount: evidence.nodeExecutionCount,
        nodeExecutionsByProvider: evidence.nodeExecutionsByProvider,
      );
    } on Object {
      failed = true;
      rethrow;
    } finally {
      result?.dispose();
      result?.dispose();
      evidenceSession?.dispose();
      evidenceSession?.dispose();
      if (artifactRoot != null) {
        try {
          _deleteArtifactRoot(artifactRoot);
        } on Object {
          if (!failed) rethrow;
        }
      }
    }
  }

  @override
  void dispose() {
    if (_disposed) return;
    _disposed = true;
    final OrtTensor? input = _input;
    final OrtSession? session = _session;
    final OrtRuntime? runtime = _runtime;
    _input = null;
    _session = null;
    _runtime = null;
    input?.dispose();
    input?.dispose();
    session?.dispose();
    session?.dispose();
    runtime?.dispose();
    runtime?.dispose();
  }
}

final class _OrtCpuBenchmarkPoolProbe implements CpuBenchmarkPoolProbe {
  _OrtCpuBenchmarkPoolProbe._({
    required OrtSessionPool timedPool,
    required OrtModelSource model,
    required OrtIsolateTensor input,
    required Float32List referenceOutput,
    required CpuBenchmarkTargetIdentity identity,
    required this.inputPreparationMicroseconds,
    required this.poolStartupMicroseconds,
  }) : _timedPool = timedPool,
       _model = model,
       _input = input,
       _referenceOutput = referenceOutput,
       _identity = identity;

  static Future<_OrtCpuBenchmarkPoolProbe> open(
    CpuBenchmarkAssets assets,
    CpuBenchmarkTargetIdentity identity,
    void Function() onInputPrepared,
  ) async {
    final OrtModelSource model = OrtModelSource.bytes(
      assets.model,
      modelId: cpuBenchmarkModelId,
    );
    final Stopwatch preparationWatch = Stopwatch()..start();
    final OrtIsolateTensor input = OrtIsolateTensor.fromFloat32List(
      values: assets.inputValues,
      shape: cpuBenchmarkInputShape,
    );
    preparationWatch.stop();
    final int inputPreparation = preparationWatch.elapsedMicroseconds;
    _positiveDuration(inputPreparation);
    onInputPrepared();

    final Stopwatch startupWatch = Stopwatch()..start();
    OrtSessionPool? pool;
    var authoritativeFailure = false;
    try {
      pool = await OrtSessionPool.spawn(
        size: cpuBenchmarkPoolSize,
        model: model,
        runtimeSource: const OrtRuntimeSource.bundled(),
        options: _sessionOptions(),
        logId: 'fonix-cpu-benchmark-pool-timed',
        maxPendingRunsPerWorker: cpuBenchmarkPoolMaxPendingRunsPerWorker,
        maxMessageBytes: cpuBenchmarkPoolMaxMessageBytes,
        maxOutstandingInputBytesPerWorker:
            cpuBenchmarkPoolMaxOutstandingInputBytesPerWorker,
      );
      startupWatch.stop();
      final int startup = startupWatch.elapsedMicroseconds;
      _positiveDuration(startup);
      final _OrtCpuBenchmarkPoolProbe result = _OrtCpuBenchmarkPoolProbe._(
        timedPool: pool,
        model: model,
        input: input,
        referenceOutput: assets.referenceOutput,
        identity: identity,
        inputPreparationMicroseconds: inputPreparation,
        poolStartupMicroseconds: startup,
      );
      pool = null;
      return result;
    } on Object {
      authoritativeFailure = true;
      rethrow;
    } finally {
      startupWatch.stop();
      final OrtSessionPool? current = pool;
      if (current != null) {
        try {
          await current.close();
        } on Object {
          if (!authoritativeFailure) rethrow;
        }
      }
    }
  }

  final OrtSessionPool _timedPool;
  final OrtModelSource _model;
  final OrtIsolateTensor _input;
  final Float32List _referenceOutput;
  final CpuBenchmarkTargetIdentity _identity;
  Future<void>? _closeFuture;
  var _timedPoolClosed = false;
  var _assignmentCaptured = false;

  @override
  final int inputPreparationMicroseconds;

  @override
  final int poolStartupMicroseconds;

  @override
  int get outstandingRuns => _timedPool.outstandingRuns;

  @override
  int get outstandingInputBytes => _timedPool.outstandingInputBytes;

  @override
  Future<void> startRun() {
    if (_timedPoolClosed) {
      throw const CpuBenchmarkFailure(
        'The timed CPU benchmark pool is already closed.',
      );
    }
    final OrtIsolateRun run = _timedPool.startRun(
      inputs: <String, OrtIsolateValue>{cpuBenchmarkInputName: _input},
      outputNames: const <String>[cpuBenchmarkOutputName],
    );
    return _validateRunResult(run.result, strictAssignment: false).then((_) {});
  }

  @override
  Future<void> close() => _closeFuture ??= _timedPool.close().then((_) {
    _timedPoolClosed = true;
  });

  @override
  Future<List<CpuBenchmarkAssignment>> captureAssignments() async {
    if (!_timedPoolClosed || _assignmentCaptured) {
      throw const CpuBenchmarkFailure(
        'The strict CPU pool assignment phase is out of order.',
      );
    }
    _assignmentCaptured = true;
    final Directory artifactRoot = Directory.systemTemp.createTempSync(
      'fonix-cpu-benchmark-pool-assignment-',
    );
    OrtSessionPool? pool;
    var authoritativeFailure = false;
    try {
      pool = await OrtSessionPool.spawn(
        size: cpuBenchmarkPoolSize,
        model: _model,
        runtimeSource: const OrtRuntimeSource.bundled(),
        options: _sessionOptions(
          artifactRoot: artifactRoot.path,
          strictAssignment: true,
        ),
        logId: 'fonix-cpu-benchmark-pool-assignment',
        maxPendingRunsPerWorker: cpuBenchmarkPoolMaxPendingRunsPerWorker,
        maxMessageBytes: cpuBenchmarkPoolMaxMessageBytes,
        maxOutstandingInputBytesPerWorker:
            cpuBenchmarkPoolMaxOutstandingInputBytesPerWorker,
      );
      final OrtSessionPool strictPool = pool;
      final List<OrtIsolateRunResult> results =
          await settleCpuBenchmarkStrictPoolRuns<
            OrtIsolateRun,
            OrtIsolateRunResult
          >(
            start: () => strictPool.startRun(
              inputs: <String, OrtIsolateValue>{cpuBenchmarkInputName: _input},
              outputNames: const <String>[cpuBenchmarkOutputName],
            ),
            result: (OrtIsolateRun run) => run.result,
            hasExpectedOccupancy: () =>
                strictPool.outstandingRuns == cpuBenchmarkPoolConcurrency &&
                strictPool.outstandingInputBytes ==
                    cpuBenchmarkPoolConcurrency *
                        cpuBenchmarkPoolInputReservationBytesPerRun,
            isDrained: () =>
                strictPool.outstandingRuns == 0 &&
                strictPool.outstandingInputBytes == 0,
          );
      final List<CpuBenchmarkAssignment> assignments =
          <CpuBenchmarkAssignment>[];
      for (final OrtIsolateRunResult result in results) {
        await _validateRunResult(
          Future<OrtIsolateRunResult>.value(result),
          strictAssignment: true,
        );
        final OrtProviderRunEvidence evidence = result.providerEvidence!;
        assignments.add(
          CpuBenchmarkAssignment(
            providerId: 'cpu',
            reportedName: _identity.cpuReportedName,
            nodeExecutionCount: evidence.nodeExecutionCount,
            nodeExecutionsByProvider: evidence.nodeExecutionsByProvider,
          ),
        );
      }
      final Future<void> firstClose = pool.close();
      final Future<void> secondClose = pool.close();
      if (!identical(firstClose, secondClose)) {
        throw const CpuBenchmarkFailure(
          'The strict pool close operation was not idempotent.',
        );
      }
      await firstClose;
      if (pool.outstandingRuns != 0 || pool.outstandingInputBytes != 0) {
        throw const CpuBenchmarkFailure(
          'The strict pool retained work after close.',
        );
      }
      pool = null;
      _deleteArtifactRoot(artifactRoot);
      return List<CpuBenchmarkAssignment>.unmodifiable(assignments);
    } on Object {
      authoritativeFailure = true;
      rethrow;
    } finally {
      final OrtSessionPool? current = pool;
      if (current != null) {
        try {
          await current.close();
        } on Object {
          if (!authoritativeFailure) rethrow;
        }
      }
      if (artifactRoot.existsSync()) {
        try {
          _deleteArtifactRoot(artifactRoot);
        } on Object {
          if (!authoritativeFailure) rethrow;
        }
      }
    }
  }

  Future<void> _validateRunResult(
    Future<OrtIsolateRunResult> pending, {
    required bool strictAssignment,
  }) async {
    final OrtIsolateRunResult result = await pending;
    final OrtIsolateTensor output = result.tensor(cpuBenchmarkOutputName);
    if (!_sameShape(output.shape.dimensions, cpuBenchmarkOutputShape) ||
        output.elementType != OrtTensorElementType.float32) {
      throw const CpuBenchmarkFailure(
        'The pool returned an unexpected output tensor.',
      );
    }
    _verifyReferenceOutput(output.copyFloat32Data(), _referenceOutput);
    _validatePoolDiagnostics(
      result.diagnostics,
      _identity,
      strictAssignment: strictAssignment,
    );
    final OrtProviderRunEvidence? evidence = result.providerEvidence;
    if (strictAssignment) {
      if (evidence == null || !evidence.isFullyAssignedTo('cpu')) {
        throw const CpuBenchmarkFailure(
          'A strict pool worker returned no full CPU assignment evidence.',
        );
      }
    } else if (evidence != null) {
      throw const CpuBenchmarkFailure(
        'The timed pool unexpectedly enabled assignment profiling.',
      );
    }
  }
}

void _validatePoolDiagnostics(
  OrtDiagnostics diagnostics,
  CpuBenchmarkTargetIdentity identity, {
  required bool strictAssignment,
}) {
  final OrtSessionDiagnostics? session = diagnostics.session;
  final List<OrtProviderDiagnostics> cpuProviders = diagnostics.providers
      .where((OrtProviderDiagnostics value) => value.wrapperId == 'cpu')
      .toList(growable: false);
  if (diagnostics.dartPackageVersion != identity.packageVersion ||
      diagnostics.runtimeVersion != identity.runtimeVersion ||
      diagnostics.runtimeIdentity != identity.runtimeLibraryIdentity ||
      diagnostics.runtimeMode.name != identity.runtimeSource ||
      diagnostics.runtimeOwner.name != identity.runtimeOwner ||
      diagnostics.artifactFlavor != identity.artifactFlavor ||
      diagnostics.artifactSha256 != identity.artifactSourceSha256 ||
      diagnostics.platform != identity.platform ||
      diagnostics.architecture != identity.architecture ||
      diagnostics.shimAbiVersion != identity.shimAbi ||
      diagnostics.shimBuildId != identity.shimBuildId ||
      diagnostics.requiredOrtApiVersion != identity.requiredOrtApi ||
      diagnostics.negotiatedOrtApiVersion != identity.negotiatedOrtApi ||
      diagnostics.modelId != cpuBenchmarkModelId ||
      session == null ||
      session.executionMode != 'sequential' ||
      session.graphOptimization != 'all' ||
      session.intraOpThreads != 1 ||
      session.interOpThreads != 1 ||
      !session.memoryPattern ||
      session.fallbackPolicy != (strictAssignment ? 'rejectAny' : 'report') ||
      cpuProviders.length != 1 ||
      cpuProviders.single.discoverable != true ||
      cpuProviders.single.registered != true ||
      cpuProviders.single.reportedName != identity.cpuReportedName ||
      cpuProviders.single.active != (strictAssignment ? true : null)) {
    throw const CpuBenchmarkFailure(
      'A pool worker contradicted the serial target identity or options.',
    );
  }
}

OrtSessionOptions _sessionOptions({
  String? artifactRoot,
  bool strictAssignment = false,
}) => OrtSessionOptions(
  graphOptimization: OrtGraphOptimization.all,
  executionMode: OrtExecutionMode.sequential,
  intraOpThreads: 1,
  interOpThreads: 1,
  enableCpuMemoryArena: true,
  enableMemoryPattern: true,
  deterministicCompute: true,
  artifactRoot: artifactRoot,
  providers: <OrtExecutionProvider>[
    OrtExecutionProvider.cpu(
      requirement: strictAssignment
          ? OrtProviderRequirement.requireFullAssignment
          : OrtProviderRequirement.required,
    ),
  ],
  fallbackPolicy: strictAssignment
      ? OrtFallbackPolicy.rejectAny
      : OrtFallbackPolicy.report,
  sessionLogId: strictAssignment
      ? 'fonix-cpu-benchmark-assignment'
      : 'fonix-cpu-benchmark-timed',
);

CpuBenchmarkTargetIdentity _targetIdentity(
  OrtRuntime runtime,
  OrtSession session,
) {
  final OrtNativeArtifactIdentity? artifact = runtime.buildInfo.artifact;
  final OrtDiagnostics diagnostics = session.diagnostics;
  final List<OrtProviderDiagnostics> cpuProviders = diagnostics.providers
      .where((OrtProviderDiagnostics value) => value.wrapperId == 'cpu')
      .toList(growable: false);
  if (artifact == null ||
      (diagnostics.platform != 'macos' && diagnostics.platform != 'linux') ||
      cpuProviders.length != 1 ||
      cpuProviders.single.discoverable != true ||
      cpuProviders.single.registered != true ||
      cpuProviders.single.reportedName == null) {
    throw const CpuBenchmarkFailure(
      'The target does not satisfy the desktop CPU benchmark contract.',
    );
  }
  return CpuBenchmarkTargetIdentity(
    packageVersion: diagnostics.dartPackageVersion,
    runtimeVersion: diagnostics.runtimeVersion,
    runtimeLibraryIdentity: diagnostics.runtimeIdentity,
    runtimeSource: diagnostics.runtimeMode.name,
    runtimeOwner: diagnostics.runtimeOwner.name,
    artifactFlavor: diagnostics.artifactFlavor,
    artifactId: artifact.id,
    artifactSourceSha256: artifact.sourceSha256,
    platform: diagnostics.platform,
    architecture: diagnostics.architecture,
    shimNativeIdentity: runtime.buildInfo.nativeIdentity,
    shimAbi: diagnostics.shimAbiVersion,
    shimBuildId: diagnostics.shimBuildId,
    requiredOrtApi: diagnostics.requiredOrtApiVersion,
    negotiatedOrtApi: diagnostics.negotiatedOrtApiVersion,
    cpuReportedName: cpuProviders.single.reportedName!,
    compiledProviders: <Map<String, Object?>>[
      for (final MapEntry<String, String?> provider
          in artifact.providers.entries)
        <String, Object?>{
          'wrapperId': provider.key,
          'reportedName': provider.value,
        },
    ],
  );
}

void _verifyReferenceOutput(Float32List actual, Float32List expected) {
  if (!cpuBenchmarkFloat32BitsMatch(actual, expected)) {
    throw const CpuBenchmarkFailure(
      'The CPU benchmark output did not match its exact reference.',
    );
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
    final double value = actual[index];
    if (!value.isFinite || actualBits[index] != expectedBits[index]) {
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

void _deleteArtifactRoot(Directory root) {
  final FileSystemEntityType type = FileSystemEntity.typeSync(
    root.path,
    followLinks: false,
  );
  switch (type) {
    case FileSystemEntityType.notFound:
      return;
    case FileSystemEntityType.directory:
      root.deleteSync(recursive: true);
    case FileSystemEntityType.file:
    case FileSystemEntityType.link:
    case FileSystemEntityType.unixDomainSock:
    case FileSystemEntityType.pipe:
      throw const CpuBenchmarkFailure(
        'The private assignment directory changed type.',
      );
  }
}
