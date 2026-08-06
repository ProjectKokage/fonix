import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:fonix/fonix.dart';

import 'inference_backend.dart';
import 'reference_smoke.dart';

const int androidXnnpackQualificationSchemaVersion = 1;
const String androidXnnpackQualificationProfile = 'xnnpack';

const String _assignmentModelAsset = 'assets/models/xnnpack_matmul.onnx';
const String _assignmentManifestAsset = 'assets/models/xnnpack_matmul.json';
const String _fallbackModelAsset = 'assets/models/mul_1.onnx';
const String _fallbackManifestAsset = 'assets/models/model.json';

// These two identities are reproduced by the deterministic fixture generator
// and independently checked against the final APK and AAB members.
const int _assignmentModelSize = 311;
const String _assignmentModelSha256 =
    'c75aaa93b0e1ae09e0bb12ddee5786c2fda803dfa5f565234e2b65e238623482';
const int _assignmentManifestSize = 1298;
const String _assignmentManifestSha256 =
    '76eb202b02211f34ee64ca6a88a2a24d9f58f334136fa7f1b7fcfe2e763f30ab';

const int _fallbackModelSize = 130;
const String _fallbackModelSha256 =
    '71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10';
const int _fallbackManifestSize = 687;
const String _fallbackManifestSha256 =
    '20ab7b1150a37516159c714abca3cb1cb6e48692da0c77f21c46e15336f71449';

const List<double> _matmulInputValues = <double>[1, 2, 3, 4, 5, 6];
const List<double> _matmulOutputValues = <double>[7, 10, 15, 22, 23, 34];
const int _xnnpackSessionCycles = 2;
const int _xnnpackRunsPerCycle = 3;
const int _qualificationSessionCount = 5;

const String _expectedShimBuildId =
    'android-owner-application-source-bundled-artifact-'
    'onnxruntime-1.27.1-android-arm64-v8a-cpu';
const String _expectedArtifactSha256 =
    '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383';

/// Canonical JSON accepted by the Android host and external package gate.
final String androidXnnpackExpectedReceiptJson = jsonEncode(_expectedReceipt);

/// One closed, path-free receipt for the Android arm64 XNNPACK checkpoint.
final class AndroidXnnpackQualificationReceipt {
  factory AndroidXnnpackQualificationReceipt.fromMap(
    Map<String, Object?> value,
  ) {
    if (!_deepExactEquals(value, _expectedReceipt)) {
      throw const InferenceBackendFailure(
        summary: 'The Android XNNPACK receipt did not match its contract.',
        backendUnusable: true,
      );
    }
    return const AndroidXnnpackQualificationReceipt._();
  }

  const AndroidXnnpackQualificationReceipt._();

  Map<String, Object?> toMap() => _expectedReceipt;

  String toJsonString() {
    final String value = androidXnnpackExpectedReceiptJson;
    if (value.contains('\n') ||
        value.contains('\r') ||
        utf8.encode(value).length > maximumReferenceSmokeReceiptBytes) {
      throw const InferenceBackendFailure(
        summary: 'The Android XNNPACK receipt exceeds its size contract.',
        backendUnusable: true,
      );
    }
    return value;
  }
}

/// Runs the exact CPU/XNNPACK assignment, fallback, and recovery checkpoint.
Future<AndroidXnnpackQualificationReceipt> runAndroidXnnpackQualification({
  AssetBundle? assets,
}) async {
  if (!Platform.isAndroid) {
    throw const InferenceBackendFailure(
      summary: 'The XNNPACK qualification path is Android-only.',
      backendUnusable: true,
    );
  }
  final AssetBundle bundle = assets ?? rootBundle;
  final Uint8List assignmentModel = await _loadExactAsset(
    bundle,
    _assignmentModelAsset,
    expectedSize: _assignmentModelSize,
    expectedSha256: _assignmentModelSha256,
  );
  await _loadExactAsset(
    bundle,
    _assignmentManifestAsset,
    expectedSize: _assignmentManifestSize,
    expectedSha256: _assignmentManifestSha256,
  );
  final Uint8List fallbackModel = await _loadExactAsset(
    bundle,
    _fallbackModelAsset,
    expectedSize: _fallbackModelSize,
    expectedSha256: _fallbackModelSha256,
  );
  await _loadExactAsset(
    bundle,
    _fallbackManifestAsset,
    expectedSize: _fallbackManifestSize,
    expectedSha256: _fallbackManifestSha256,
  );

  final _SessionOutcome<_RunSnapshot> cpu = await _withSession(
    modelBytes: assignmentModel,
    modelId: 'xnnpack-matmul-sha256-$_assignmentModelSha256',
    providers: <OrtExecutionProvider>[
      OrtExecutionProvider.cpu(
        requirement: OrtProviderRequirement.requireFullAssignment,
      ),
    ],
    fallbackPolicy: OrtFallbackPolicy.rejectAny,
    logId: 'fonix-xnnpack-cpu-reference',
    body: (_QualificationSession session) async {
      _validateSessionPolicy(
        session.worker.diagnostics,
        fallbackPolicy: OrtFallbackPolicy.rejectAny,
      );
      return _runMatMul(session.worker, expectedProvider: 'cpu');
    },
  );

  final _SessionOutcome<List<_RunSnapshot>> firstCycle = await _runXnnpackCycle(
    assignmentModel,
    cycle: 1,
  );

  final _SessionOutcome<_RunSnapshot> reportedFallback = await _withSession(
    modelBytes: fallbackModel,
    modelId: 'mul-1-sha256-$_fallbackModelSha256',
    providers: <OrtExecutionProvider>[
      OrtExecutionProvider.xnnpack(
        intraOpThreads: 1,
        requirement: OrtProviderRequirement.required,
      ),
      OrtExecutionProvider.cpu(),
    ],
    fallbackPolicy: OrtFallbackPolicy.report,
    logId: 'fonix-xnnpack-fallback-report',
    body: (_QualificationSession session) async {
      _validateSessionPolicy(
        session.worker.diagnostics,
        fallbackPolicy: OrtFallbackPolicy.report,
      );
      _validateXnnpackProvider(
        session.worker.diagnostics.providers,
        expectedActive: null,
      );
      final _RunSnapshot result = await _runFallback(
        session.worker,
        expectedProvider: 'cpu',
      );
      _validateXnnpackProvider(
        result.providerDiagnostics,
        expectedActive: false,
      );
      return result;
    },
  );

  final _SessionOutcome<_FallbackRejection> rejectedFallback =
      await _withSession(
        modelBytes: fallbackModel,
        modelId: 'mul-1-sha256-$_fallbackModelSha256',
        providers: <OrtExecutionProvider>[
          OrtExecutionProvider.xnnpack(
            intraOpThreads: 1,
            requirement: OrtProviderRequirement.requireFullAssignment,
          ),
          OrtExecutionProvider.cpu(),
        ],
        fallbackPolicy: OrtFallbackPolicy.rejectAny,
        logId: 'fonix-xnnpack-fallback-reject',
        body: (_QualificationSession session) async {
          _validateSessionPolicy(
            session.worker.diagnostics,
            fallbackPolicy: OrtFallbackPolicy.rejectAny,
          );
          _validateXnnpackProvider(
            session.worker.diagnostics.providers,
            expectedActive: null,
          );
          return _requireRejectedFallback(session.worker);
        },
      );

  final _SessionOutcome<List<_RunSnapshot>> secondCycle =
      await _runXnnpackCycle(assignmentModel, cycle: 2);

  final List<_SessionOutcome<Object?>> outcomes = <_SessionOutcome<Object?>>[
    cpu,
    firstCycle,
    reportedFallback,
    rejectedFallback,
    secondCycle,
  ];
  final _RuntimeIdentity identity = cpu.runtimeIdentity;
  for (final _SessionOutcome<Object?> outcome in outcomes.skip(1)) {
    if (outcome.runtimeIdentity != identity) {
      throw const InferenceBackendFailure(
        summary: 'The runtime identity changed during XNNPACK qualification.',
        backendUnusable: true,
      );
    }
  }
  final List<_RunSnapshot> xnnpackRuns = <_RunSnapshot>[
    ...firstCycle.value,
    ...secondCycle.value,
  ];
  if (xnnpackRuns.length != _xnnpackSessionCycles * _xnnpackRunsPerCycle) {
    throw const InferenceBackendFailure(
      summary: 'The XNNPACK repetition contract was not completed.',
      backendUnusable: true,
    );
  }
  for (final _RunSnapshot run in xnnpackRuns) {
    if (!_sameDoubles(run.outputValues, cpu.value.outputValues)) {
      throw const InferenceBackendFailure(
        summary: 'XNNPACK output differed from the strict CPU reference.',
        backendUnusable: true,
      );
    }
  }

  return AndroidXnnpackQualificationReceipt.fromMap(<String, Object?>{
    'schemaVersion': androidXnnpackQualificationSchemaVersion,
    'status': 'passed',
    'smokeProfile': androidXnnpackQualificationProfile,
    'runtime': identity.receipt,
    'models': <String, Object?>{
      'assignmentSha256': _assignmentModelSha256,
      'assignmentManifestSha256': _assignmentManifestSha256,
      'fallbackSha256': _fallbackModelSha256,
      'fallbackManifestSha256': _fallbackManifestSha256,
    },
    'strictSessionPolicy': const <String, Object?>{
      'executionMode': 'sequential',
      'graphOptimization': 'all',
      'ortIntraOpThreads': 1,
      'ortInterOpThreads': 1,
      'xnnpackIntraOpThreads': 1,
      'fallbackPolicy': 'rejectAny',
    },
    'xnnpackProvider': const <String, Object?>{
      'compiled': true,
      'discoverable': true,
      'registered': true,
      'registrationMechanism': 'generic',
      'registrationName': 'XNNPACK',
      'reportedName': 'XnnpackExecutionProvider',
      'options': <String, Object?>{'intra_op_num_threads': '1'},
    },
    'cpuReference': cpu.value.receipt,
    'xnnpack': <String, Object?>{
      'outputValues': _integerOutput(xnnpackRuns.last.outputValues),
      'activeProviders': xnnpackRuns.last.activeProviders,
      'nodeExecutionCount': xnnpackRuns.last.nodeExecutionCount,
      'nodeExecutionsByProvider': xnnpackRuns.last.nodeExecutionsByProvider,
      'sessionCycles': _xnnpackSessionCycles,
      'runsPerCycle': _xnnpackRunsPerCycle,
      'validatedRuns': xnnpackRuns.length,
      'allRunsFullAssignment': true,
      'allRunsExactCpuParity': true,
    },
    'fallback': <String, Object?>{
      'reportNodeExecutionCount': reportedFallback.value.nodeExecutionCount,
      'reportNodeExecutionsByProvider':
          reportedFallback.value.nodeExecutionsByProvider,
      'reportCpuFallback': true,
      'rejectionDomain': rejectedFallback.value.domain,
      'rejectionOperation': rejectedFallback.value.operation,
      'rejectionCode': rejectedFallback.value.code,
      'rejectionProviderId': rejectedFallback.value.providerId,
      'outputPublished': rejectedFallback.value.outputPublished,
    },
    'postRejectionRecovery': 'passed',
    'sessionCount': outcomes.length,
    'doubleClose': 'passed',
    'profileRootsRemoved': 'passed',
    'parityPolicy': 'exact-float32',
  });
}

Future<_SessionOutcome<List<_RunSnapshot>>> _runXnnpackCycle(
  Uint8List modelBytes, {
  required int cycle,
}) => _withSession(
  modelBytes: modelBytes,
  modelId: 'xnnpack-matmul-sha256-$_assignmentModelSha256',
  providers: <OrtExecutionProvider>[
    OrtExecutionProvider.xnnpack(
      intraOpThreads: 1,
      requirement: OrtProviderRequirement.requireFullAssignment,
    ),
    OrtExecutionProvider.cpu(),
  ],
  fallbackPolicy: OrtFallbackPolicy.rejectAny,
  logId: 'fonix-xnnpack-cycle-$cycle',
  body: (_QualificationSession session) async {
    _validateSessionPolicy(
      session.worker.diagnostics,
      fallbackPolicy: OrtFallbackPolicy.rejectAny,
    );
    _validateXnnpackProvider(
      session.worker.diagnostics.providers,
      expectedActive: null,
    );
    final List<_RunSnapshot> runs = <_RunSnapshot>[];
    for (var index = 0; index < _xnnpackRunsPerCycle; index += 1) {
      final _RunSnapshot run = await _runMatMul(
        session.worker,
        expectedProvider: 'xnnpack',
      );
      _validateXnnpackProvider(run.providerDiagnostics, expectedActive: true);
      runs.add(run);
    }
    return List<_RunSnapshot>.unmodifiable(runs);
  },
);

Future<_RunSnapshot> _runMatMul(
  OrtIsolateSession session, {
  required String expectedProvider,
}) async {
  final OrtIsolateRunResult result = await session.run(
    inputs: <String, OrtIsolateValue>{
      'input': OrtIsolateTensor.fromFloat32List(
        values: Float32List.fromList(_matmulInputValues),
        shape: const <int>[3, 2],
      ),
    },
    outputNames: const <String>['output'],
  );
  return _RunSnapshot.fromResult(
    result,
    outputName: 'output',
    expectedShape: const <int>[3, 2],
    expectedOutput: _matmulOutputValues,
    expectedProvider: expectedProvider,
  );
}

Future<_RunSnapshot> _runFallback(
  OrtIsolateSession session, {
  required String expectedProvider,
}) async {
  final OrtIsolateRunResult result = await session.run(
    inputs: <String, OrtIsolateValue>{
      'X': OrtIsolateTensor.fromFloat32List(
        values: Float32List.fromList(const <double>[1, 2, 3, 4, 5, 6]),
        shape: const <int>[3, 2],
      ),
    },
    outputNames: const <String>['Y'],
  );
  return _RunSnapshot.fromResult(
    result,
    outputName: 'Y',
    expectedShape: const <int>[3, 2],
    expectedOutput: const <double>[1, 4, 9, 16, 25, 36],
    expectedProvider: expectedProvider,
  );
}

Future<_FallbackRejection> _requireRejectedFallback(
  OrtIsolateSession session,
) async {
  var outputPublished = false;
  try {
    await session.run(
      inputs: <String, OrtIsolateValue>{
        'X': OrtIsolateTensor.fromFloat32List(
          values: Float32List.fromList(const <double>[1, 2, 3, 4, 5, 6]),
          shape: const <int>[3, 2],
        ),
      },
      outputNames: const <String>['Y'],
    );
    outputPublished = true;
  } on OrtProviderUnavailableException catch (error) {
    if (error.domain != OrtErrorDomain.provider ||
        error.operation != 'provider_evidence_validate' ||
        error.code != 1001 ||
        error.ortCode != null ||
        error.context.length != 1 ||
        error.context['providerId'] != 'xnnpack' ||
        outputPublished) {
      throw const InferenceBackendFailure(
        summary: 'The strict fallback rejection was not canonical.',
        backendUnusable: true,
      );
    }
    return _FallbackRejection(
      domain: error.domain.name,
      operation: error.operation,
      code: error.code,
      providerId: error.context['providerId']! as String,
      outputPublished: outputPublished,
    );
  }
  throw const InferenceBackendFailure(
    summary: 'The strict XNNPACK fallback run published an output.',
    backendUnusable: true,
  );
}

Future<_SessionOutcome<T>> _withSession<T>({
  required Uint8List modelBytes,
  required String modelId,
  required List<OrtExecutionProvider> providers,
  required OrtFallbackPolicy fallbackPolicy,
  required String logId,
  required Future<T> Function(_QualificationSession session) body,
}) async {
  final Directory artifactRoot = await Directory.systemTemp.createTemp(
    'fonix-xnnpack-qualification-',
  );
  OrtIsolateSession? worker;
  T? value;
  _RuntimeIdentity? identity;
  Object? authoritativeError;
  StackTrace? authoritativeStack;
  try {
    worker = await OrtIsolateSession.spawn(
      runtimeSource: const OrtRuntimeSource.bundled(),
      model: OrtModelSource.bytes(modelBytes, modelId: modelId),
      options: OrtSessionOptions(
        graphOptimization: OrtGraphOptimization.all,
        executionMode: OrtExecutionMode.sequential,
        intraOpThreads: 1,
        interOpThreads: 1,
        artifactRoot: artifactRoot.path,
        providers: providers,
        fallbackPolicy: fallbackPolicy,
      ),
      logId: logId,
      maxPendingRuns: 1,
      maxMessageBytes: 1024 * 1024,
      startupTimeout: const Duration(seconds: 30),
    );
    identity = _RuntimeIdentity.fromDiagnostics(worker.diagnostics);
    value = await body(_QualificationSession(worker));
  } on Object catch (error, stackTrace) {
    authoritativeError = error;
    authoritativeStack = stackTrace;
  }

  Object? cleanupError;
  StackTrace? cleanupStack;
  if (worker != null) {
    try {
      await worker.close();
      await worker.close();
    } on Object catch (error, stackTrace) {
      cleanupError = error;
      cleanupStack = stackTrace;
    }
  }
  try {
    await retireAndroidXnnpackArtifactRootForTesting(
      artifactRoot,
      requireEmpty: authoritativeError == null && cleanupError == null,
    );
  } on Object catch (error, stackTrace) {
    cleanupError ??= error;
    cleanupStack ??= stackTrace;
  }
  if (authoritativeError != null) {
    Error.throwWithStackTrace(authoritativeError, authoritativeStack!);
  }
  if (cleanupError != null) {
    Error.throwWithStackTrace(cleanupError, cleanupStack!);
  }
  return _SessionOutcome<T>(runtimeIdentity: identity!, value: value as T);
}

Future<void> retireAndroidXnnpackArtifactRootForTesting(
  Directory directory, {
  required bool requireEmpty,
}) async {
  Object? validationError;
  StackTrace? validationStack;
  if (requireEmpty) {
    try {
      await _requireEmptyAndDelete(directory);
      return;
    } on Object catch (error, stackTrace) {
      validationError = error;
      validationStack = stackTrace;
    }
  }

  try {
    await _deletePrivateArtifactRoot(directory);
  } on Object catch (error, stackTrace) {
    if (validationError == null) {
      Error.throwWithStackTrace(error, stackTrace);
    }
  }
  if (validationError != null) {
    Error.throwWithStackTrace(validationError, validationStack!);
  }
}

Future<void> _requireEmptyAndDelete(Directory directory) async {
  final FileSystemEntityType type = await FileSystemEntity.type(
    directory.path,
    followLinks: false,
  );
  if (type != FileSystemEntityType.directory) {
    throw const InferenceBackendFailure(
      summary: 'A private XNNPACK profiling root changed type.',
      backendUnusable: true,
    );
  }
  if (!await directory.list(followLinks: false).isEmpty) {
    throw const InferenceBackendFailure(
      summary: 'A private XNNPACK profiling root was not retired.',
      backendUnusable: true,
    );
  }
  await directory.delete();
  if (await FileSystemEntity.type(directory.path, followLinks: false) !=
      FileSystemEntityType.notFound) {
    throw const InferenceBackendFailure(
      summary: 'A private XNNPACK profiling root remained after cleanup.',
      backendUnusable: true,
    );
  }
}

Future<void> _deletePrivateArtifactRoot(Directory directory) async {
  final String path = directory.path;
  switch (await FileSystemEntity.type(path, followLinks: false)) {
    case FileSystemEntityType.notFound:
      return;
    case FileSystemEntityType.directory:
      await directory.delete(recursive: true);
    case FileSystemEntityType.file:
      await File(path).delete();
    case FileSystemEntityType.link:
      await Link(path).delete();
    default:
      throw const InferenceBackendFailure(
        summary: 'A private XNNPACK profiling root could not be retired.',
        backendUnusable: true,
      );
  }
  if (await FileSystemEntity.type(path, followLinks: false) !=
      FileSystemEntityType.notFound) {
    throw const InferenceBackendFailure(
      summary: 'A private XNNPACK profiling root remained after cleanup.',
      backendUnusable: true,
    );
  }
}

Future<Uint8List> _loadExactAsset(
  AssetBundle assets,
  String path, {
  required int expectedSize,
  required String expectedSha256,
}) async {
  final ByteData data = await assets.load(path);
  final Uint8List value = Uint8List.fromList(
    data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
  );
  if (value.length != expectedSize ||
      sha256.convert(value).toString() != expectedSha256) {
    throw const InferenceBackendFailure(
      summary: 'A bundled XNNPACK qualification asset failed verification.',
      backendUnusable: true,
    );
  }
  return value;
}

void _validateSessionPolicy(
  OrtDiagnostics diagnostics, {
  required OrtFallbackPolicy fallbackPolicy,
}) {
  final OrtSessionDiagnostics? session = diagnostics.session;
  if (session == null ||
      session.executionMode != 'sequential' ||
      session.graphOptimization != 'all' ||
      session.intraOpThreads != 1 ||
      session.interOpThreads != 1 ||
      !session.memoryPattern ||
      session.fallbackPolicy != fallbackPolicy.name) {
    throw const InferenceBackendFailure(
      summary: 'The XNNPACK session policy changed unexpectedly.',
      backendUnusable: true,
    );
  }
}

void _validateXnnpackProvider(
  List<OrtProviderDiagnostics> providers, {
  required bool? expectedActive,
}) {
  final List<OrtProviderDiagnostics> matches = providers
      .where((OrtProviderDiagnostics value) => value.wrapperId == 'xnnpack')
      .toList(growable: false);
  if (matches.length != 1) {
    throw const InferenceBackendFailure(
      summary: 'XNNPACK diagnostics were missing or ambiguous.',
      backendUnusable: true,
    );
  }
  final OrtProviderDiagnostics provider = matches.single;
  final String? expectedAssignment = expectedActive == null
      ? null
      : 'ort-run-profile-v1:1';
  final String? expectedFallback = expectedActive == false
      ? 'No node assignment was observed in this profiled run.'
      : null;
  if (provider.registrationMechanism !=
          OrtProviderRegistrationMechanism.generic ||
      provider.registrationName != 'XNNPACK' ||
      provider.reportedName != 'XnnpackExecutionProvider' ||
      provider.compiled != true ||
      provider.discoverable != true ||
      provider.registered != true ||
      provider.active != expectedActive ||
      provider.qualified != null ||
      provider.assignmentEvidence != expectedAssignment ||
      provider.fallbackReason != expectedFallback ||
      !_deepExactEquals(provider.options, const <String, String>{
        'intra_op_num_threads': '1',
      })) {
    throw const InferenceBackendFailure(
      summary: 'XNNPACK provider diagnostics changed unexpectedly.',
      backendUnusable: true,
    );
  }
}

List<int> _integerOutput(List<double> values) => List<int>.unmodifiable(<int>[
  for (final double value in values) value.toInt(),
]);

bool _sameInts(List<int> first, List<int> second) {
  if (first.length != second.length) return false;
  for (var index = 0; index < first.length; index += 1) {
    if (first[index] != second[index]) return false;
  }
  return true;
}

bool _sameDoubles(List<double> first, List<double> second) {
  if (first.length != second.length) return false;
  for (var index = 0; index < first.length; index += 1) {
    if (first[index] != second[index]) return false;
  }
  return true;
}

bool _deepExactEquals(Object? actual, Object? expected) {
  if (actual is Map && expected is Map) {
    if (actual.length != expected.length) return false;
    for (final Object? key in expected.keys) {
      if (!actual.containsKey(key) ||
          !_deepExactEquals(actual[key], expected[key])) {
        return false;
      }
    }
    return true;
  }
  if (actual is List && expected is List) {
    if (actual.length != expected.length) return false;
    for (var index = 0; index < actual.length; index += 1) {
      if (!_deepExactEquals(actual[index], expected[index])) return false;
    }
    return true;
  }
  return actual.runtimeType == expected.runtimeType && actual == expected;
}

final class _RunSnapshot {
  _RunSnapshot({
    required this.outputValues,
    required this.activeProviders,
    required this.nodeExecutionCount,
    required this.nodeExecutionsByProvider,
    required this.providerDiagnostics,
  });

  factory _RunSnapshot.fromResult(
    OrtIsolateRunResult result, {
    required String outputName,
    required List<int> expectedShape,
    required List<double> expectedOutput,
    required String expectedProvider,
  }) {
    final OrtIsolateTensor tensor = result.tensor(outputName);
    final List<double> values = tensor.copyFloat32Data();
    final OrtProviderRunEvidence? evidence = result.providerEvidence;
    final List<String>? activeProviders = evidence?.activeProviderIds.toList()
      ?..sort();
    if (!_sameInts(tensor.shape.dimensions, expectedShape) ||
        !_sameDoubles(values, expectedOutput) ||
        evidence == null ||
        evidence.nodeExecutionCount != 1 ||
        !_deepExactEquals(evidence.nodeExecutionsByProvider, <String, int>{
          expectedProvider: 1,
        }) ||
        activeProviders == null ||
        !_deepExactEquals(activeProviders, <String>[expectedProvider]) ||
        !evidence.isFullyAssignedTo(expectedProvider)) {
      throw const InferenceBackendFailure(
        summary: 'A provider run did not match its exact assignment contract.',
        backendUnusable: true,
      );
    }
    return _RunSnapshot(
      outputValues: List<double>.unmodifiable(values),
      activeProviders: List<String>.unmodifiable(activeProviders),
      nodeExecutionCount: evidence.nodeExecutionCount,
      nodeExecutionsByProvider: Map<String, int>.unmodifiable(
        evidence.nodeExecutionsByProvider,
      ),
      providerDiagnostics: result.providerDiagnostics,
    );
  }

  final List<double> outputValues;
  final List<String> activeProviders;
  final int nodeExecutionCount;
  final Map<String, int> nodeExecutionsByProvider;
  final List<OrtProviderDiagnostics> providerDiagnostics;

  Map<String, Object?> get receipt => <String, Object?>{
    'outputValues': _integerOutput(outputValues),
    'activeProviders': activeProviders,
    'nodeExecutionCount': nodeExecutionCount,
    'nodeExecutionsByProvider': nodeExecutionsByProvider,
    'fullAssignment': true,
  };
}

final class _RuntimeIdentity {
  const _RuntimeIdentity({
    required this.runtimeVersion,
    required this.runtimeSource,
    required this.runtimeOwner,
    required this.artifactFlavor,
    required this.platform,
    required this.architecture,
    required this.shimBuildId,
    required this.artifactSha256,
    required this.privateIdentity,
  });

  factory _RuntimeIdentity.fromDiagnostics(OrtDiagnostics diagnostics) {
    final String? artifactSha256 = diagnostics.artifactSha256;
    if (artifactSha256 == null) {
      throw const InferenceBackendFailure(
        summary: 'The Android runtime omitted its artifact identity.',
        backendUnusable: true,
      );
    }
    return _RuntimeIdentity(
      runtimeVersion: diagnostics.runtimeVersion,
      runtimeSource: diagnostics.runtimeMode.name,
      runtimeOwner: diagnostics.runtimeOwner.name,
      artifactFlavor: diagnostics.artifactFlavor,
      platform: diagnostics.platform,
      architecture: diagnostics.architecture,
      shimBuildId: diagnostics.shimBuildId,
      artifactSha256: artifactSha256,
      privateIdentity: jsonEncode(<String, Object?>{
        'dartPackageVersion': diagnostics.dartPackageVersion,
        'shimAbiVersion': diagnostics.shimAbiVersion,
        'requiredOrtApiVersion': diagnostics.requiredOrtApiVersion,
        'negotiatedOrtApiVersion': diagnostics.negotiatedOrtApiVersion,
        'runtimeIdentity': diagnostics.runtimeIdentity,
      }),
    );
  }

  final String runtimeVersion;
  final String runtimeSource;
  final String runtimeOwner;
  final String artifactFlavor;
  final String platform;
  final String architecture;
  final String shimBuildId;
  final String artifactSha256;
  final String privateIdentity;

  Map<String, Object?> get receipt => <String, Object?>{
    'version': runtimeVersion,
    'source': runtimeSource,
    'owner': runtimeOwner,
    'artifactFlavor': artifactFlavor,
    'platform': platform,
    'architecture': architecture,
    'shimBuildId': shimBuildId,
    'artifactSha256': artifactSha256,
  };

  @override
  bool operator ==(Object other) =>
      other is _RuntimeIdentity &&
      runtimeVersion == other.runtimeVersion &&
      runtimeSource == other.runtimeSource &&
      runtimeOwner == other.runtimeOwner &&
      artifactFlavor == other.artifactFlavor &&
      platform == other.platform &&
      architecture == other.architecture &&
      shimBuildId == other.shimBuildId &&
      artifactSha256 == other.artifactSha256 &&
      privateIdentity == other.privateIdentity;

  @override
  int get hashCode => Object.hash(
    runtimeVersion,
    runtimeSource,
    runtimeOwner,
    artifactFlavor,
    platform,
    architecture,
    shimBuildId,
    artifactSha256,
    privateIdentity,
  );
}

final class _FallbackRejection {
  const _FallbackRejection({
    required this.domain,
    required this.operation,
    required this.code,
    required this.providerId,
    required this.outputPublished,
  });

  final String domain;
  final String operation;
  final int code;
  final String providerId;
  final bool outputPublished;
}

final class _QualificationSession {
  const _QualificationSession(this.worker);

  final OrtIsolateSession worker;
}

final class _SessionOutcome<T> {
  const _SessionOutcome({required this.runtimeIdentity, required this.value});

  final _RuntimeIdentity runtimeIdentity;
  final T value;
}

const Map<String, Object?> _expectedReceipt = <String, Object?>{
  'schemaVersion': androidXnnpackQualificationSchemaVersion,
  'status': 'passed',
  'smokeProfile': androidXnnpackQualificationProfile,
  'runtime': <String, Object?>{
    'version': '1.27.1',
    'source': 'bundled',
    'owner': 'application',
    'artifactFlavor': 'cpu',
    'platform': 'android',
    'architecture': 'arm64-v8a',
    'shimBuildId': _expectedShimBuildId,
    'artifactSha256': _expectedArtifactSha256,
  },
  'models': <String, Object?>{
    'assignmentSha256': _assignmentModelSha256,
    'assignmentManifestSha256': _assignmentManifestSha256,
    'fallbackSha256': _fallbackModelSha256,
    'fallbackManifestSha256': _fallbackManifestSha256,
  },
  'strictSessionPolicy': <String, Object?>{
    'executionMode': 'sequential',
    'graphOptimization': 'all',
    'ortIntraOpThreads': 1,
    'ortInterOpThreads': 1,
    'xnnpackIntraOpThreads': 1,
    'fallbackPolicy': 'rejectAny',
  },
  'xnnpackProvider': <String, Object?>{
    'compiled': true,
    'discoverable': true,
    'registered': true,
    'registrationMechanism': 'generic',
    'registrationName': 'XNNPACK',
    'reportedName': 'XnnpackExecutionProvider',
    'options': <String, Object?>{'intra_op_num_threads': '1'},
  },
  'cpuReference': <String, Object?>{
    'outputValues': <int>[7, 10, 15, 22, 23, 34],
    'activeProviders': <String>['cpu'],
    'nodeExecutionCount': 1,
    'nodeExecutionsByProvider': <String, Object?>{'cpu': 1},
    'fullAssignment': true,
  },
  'xnnpack': <String, Object?>{
    'outputValues': <int>[7, 10, 15, 22, 23, 34],
    'activeProviders': <String>['xnnpack'],
    'nodeExecutionCount': 1,
    'nodeExecutionsByProvider': <String, Object?>{'xnnpack': 1},
    'sessionCycles': _xnnpackSessionCycles,
    'runsPerCycle': _xnnpackRunsPerCycle,
    'validatedRuns': 6,
    'allRunsFullAssignment': true,
    'allRunsExactCpuParity': true,
  },
  'fallback': <String, Object?>{
    'reportNodeExecutionCount': 1,
    'reportNodeExecutionsByProvider': <String, Object?>{'cpu': 1},
    'reportCpuFallback': true,
    'rejectionDomain': 'provider',
    'rejectionOperation': 'provider_evidence_validate',
    'rejectionCode': 1001,
    'rejectionProviderId': 'xnnpack',
    'outputPublished': false,
  },
  'postRejectionRecovery': 'passed',
  'sessionCount': _qualificationSessionCount,
  'doubleClose': 'passed',
  'profileRootsRemoved': 'passed',
  'parityPolicy': 'exact-float32',
};
