import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:fonix/fonix.dart';

const int cpuBenchmarkFragmentSchemaVersion = 1;
const String cpuBenchmarkActivationKey = 'FONIX_CPU_BENCHMARK';
const String cpuBenchmarkFragmentPrefix = 'FONIX_CPU_BENCHMARK_FRAGMENT=';
const int maximumCpuBenchmarkFragmentBytes = 128 * 1024;

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
const int _maximumRunsPerThroughputWindow = 1000000;
const int _maximumDurationMicroseconds = 1000000000000000;
const int _maximumRssBytes = 0x7fffffffffffffff;

/// Whether this process requested the closed desktop CPU benchmark path.
///
/// The target fragment is available only to the macOS and Linux final
/// applications. It is measurement input for a later host-side evidence
/// aggregate, never a performance or support claim by itself.
bool desktopCpuBenchmarkEnabled({
  required bool isMacOS,
  required bool isLinux,
  required Map<String, String> environment,
}) => isMacOS != isLinux && environment[cpuBenchmarkActivationKey] == '1';

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
    required this.runtimeSource,
    required this.runtimeOwner,
    required this.artifactFlavor,
    required this.artifactId,
    required this.artifactSourceSha256,
    required this.platform,
    required this.architecture,
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
        !_digest.hasMatch(artifactSourceSha256) ||
        !const <String>{'macos', 'linux'}.contains(platform) ||
        !const <String>{'arm64', 'x86_64'}.contains(architecture) ||
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
  final String runtimeSource;
  final String runtimeOwner;
  final String artifactFlavor;
  final String artifactId;
  final String artifactSourceSha256;
  final String platform;
  final String architecture;
  final int shimAbi;
  final String shimBuildId;
  final int requiredOrtApi;
  final int negotiatedOrtApi;
  final String cpuReportedName;
  final List<Map<String, Object?>> compiledProviders;

  Map<String, Object?> toMap() => <String, Object?>{
    'packageVersion': packageVersion,
    'runtimeVersion': runtimeVersion,
    'runtimeSource': runtimeSource,
    'runtimeOwner': runtimeOwner,
    'artifactFlavor': artifactFlavor,
    'artifactId': artifactId,
    'artifactSourceSha256': artifactSourceSha256,
    'platform': platform,
    'architecture': architecture,
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
    required this.identity,
    required this.runtimeLoadMicroseconds,
    required this.sessionCreateMicroseconds,
    required this.dataPreparationMicroseconds,
    required this.firstRun,
    required this.stabilizationRuns,
    required this.stabilizationBatchMedians,
    required this.warmRuns,
    required this.throughputWindows,
    required this.assignment,
    required this.rssSamples,
  });

  final CpuBenchmarkTargetIdentity identity;
  final int runtimeLoadMicroseconds;
  final int sessionCreateMicroseconds;
  final int dataPreparationMicroseconds;
  final CpuBenchmarkRunSample firstRun;
  final int stabilizationRuns;
  final List<int> stabilizationBatchMedians;
  final List<CpuBenchmarkRunSample> warmRuns;
  final List<({int completedRuns, int durationMicroseconds})> throughputWindows;
  final CpuBenchmarkAssignment assignment;
  final List<CpuBenchmarkRssSample> rssSamples;

  Map<String, Object?> toMap() => <String, Object?>{
    'schemaVersion': cpuBenchmarkFragmentSchemaVersion,
    'result': 'measured',
    'purpose': 'measurement-only-target-fragment',
    'freshProcessRequired': true,
    'executionSurface': 'synchronous-public-api',
    'model': <String, Object?>{
      'id': cpuBenchmarkModelId,
      'onnxSha256': cpuBenchmarkModelSha256,
      'inputFixtureSha256': cpuBenchmarkInputSha256,
      'referenceOutputSha256': cpuBenchmarkOutputSha256,
      'metadataSha256': cpuBenchmarkMetadataSha256,
      'opset': 17,
      'precision': 'float32',
      'inputName': cpuBenchmarkInputName,
      'inputShape': cpuBenchmarkInputShape,
      'outputName': cpuBenchmarkOutputName,
      'outputShape': cpuBenchmarkOutputShape,
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
    'stabilization': <String, Object?>{
      'method': 'bounded-batch-median-relative-change',
      'batchSize': _stabilizationBatchSize,
      'thresholdBasisPoints': _stabilizationThresholdBasisPoints,
      'requiredConsecutiveTransitions': _stabilizationRequiredTransitions,
      'maximumRuns': _maximumStabilizationRuns,
      'actualRuns': stabilizationRuns,
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
    'claimBoundary':
        'One fresh-process target measurement fragment only; not a '
        'performance baseline, regression threshold, provider qualification, '
        'platform support claim, release approval, or cross-target evidence.',
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
  CpuBenchmarkRssReader? readRss,
  CpuBenchmarkMonotonicReader? readMonotonicMicroseconds,
}) async {
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
    return CpuBenchmarkFragment._(
      identity: identity,
      runtimeLoadMicroseconds: runtimeLoadMicroseconds,
      sessionCreateMicroseconds: sessionCreateMicroseconds,
      dataPreparationMicroseconds: dataPreparationMicroseconds,
      firstRun: firstRun,
      stabilizationRuns: stabilization.runCount,
      stabilizationBatchMedians: List<int>.unmodifiable(
        stabilization.batchMedians,
      ),
      warmRuns: List<CpuBenchmarkRunSample>.unmodifiable(warmRuns),
      throughputWindows: List.unmodifiable(throughputWindows),
      assignment: assignment,
      rssSamples: List<CpuBenchmarkRssSample>.unmodifiable(rssSamples),
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

final class _StabilizationResult {
  const _StabilizationResult({
    required this.runCount,
    required this.batchMedians,
  });

  final int runCount;
  final List<int> batchMedians;
}

_StabilizationResult _stabilize(CpuBenchmarkProbe probe) {
  final List<int> medians = <int>[];
  var stableTransitions = 0;
  var runCount = 0;
  while (runCount < _maximumStabilizationRuns) {
    final List<int> batch = <int>[];
    for (var index = 0; index < _stabilizationBatchSize; index += 1) {
      final CpuBenchmarkRunSample sample = probe.runOnce();
      _validateRunSample(sample);
      batch.add(sample.inferenceMicroseconds);
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
      return _StabilizationResult(runCount: runCount, batchMedians: medians);
    }
  }
  throw const CpuBenchmarkFailure(
    'Warm inference did not stabilize within the fixed run bound.',
  );
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
    runtimeSource: diagnostics.runtimeMode.name,
    runtimeOwner: diagnostics.runtimeOwner.name,
    artifactFlavor: diagnostics.artifactFlavor,
    artifactId: artifact.id,
    artifactSourceSha256: artifact.sourceSha256,
    platform: diagnostics.platform,
    architecture: diagnostics.architecture,
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
