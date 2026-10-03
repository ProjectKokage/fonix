import 'dart:async';
import 'dart:ffi';
import 'dart:io';
import 'dart:isolate';

import 'package:path/path.dart' as p;

import 'diagnostics.dart';
import 'exceptions.dart';
import 'isolate_protocol.dart';
import 'isolate_session.dart';
import 'isolate_value.dart';
import 'isolate_worker.dart';
import 'provider.dart';
import 'resource_limits.dart';
import 'runtime.dart';
import 'runtime_source.dart';
import 'session_options.dart';
import 'version.dart';

/// Internal real-isolate protocol harness; omitted from `package:fonix/fonix.dart`.
Future<OrtIsolateSession> spawnOrtIsolateProtocolHarnessForTesting({
  String scenario = 'echo',
  String? runtimeLibraryPath,
  SendPort? startupLifecyclePort,
  void Function(String event)? onControllerEvent,
  bool Function(int token)? requestCancelToken,
  void Function(bool, bool, bool, bool)? onParentState,
  Future<void>? spawnGate,
  int maxPendingRuns = 8,
  int maxMessageBytes = 1024 * 1024,
  int? maxOutstandingInputBytes,
  Duration startupTimeout = const Duration(seconds: 5),
  int initialRequestId = 1,
  int providerOptionCount = 0,
  OrtResourceLimits limits = OrtResourceLimits.defaults,
}) {
  RangeError.checkValueInInterval(
    providerOptionCount,
    0,
    128,
    'providerOptionCount',
  );
  final int effectiveMaxOutstandingInputBytes =
      maxOutstandingInputBytes ?? maxMessageBytes;
  validateWorkerBounds(
    maxPendingRuns: maxPendingRuns,
    maxMessageBytes: maxMessageBytes,
    maxOutstandingInputBytes: effectiveMaxOutstandingInputBytes,
    startupTimeout: startupTimeout,
  );
  if (!const <String>{
    'echo',
    'delay',
    'crash',
    'crashWithError',
    'stale',
    'futureReply',
    'startupError',
    'startupExit',
    'startupCrash',
    'startupGateReady',
    'startupGateError',
    'startupGateExit',
    'startupMalformedReady',
    'startupMalformedOwnershipThenReady',
    'startupReadyMissingCommandPort',
    'startupReadyWrongCommandPort',
    'malformed',
    'malformedWhileOwned',
    'cleanupGateResult',
    'cleanupGateOrtError',
    'fatalProtocolReply',
    'fatalWorkerReply',
    'fatalWorkerStaleReply',
    'missingField',
    'syntheticCancelToken',
    'nativeCancelRegistry',
    'ortError',
    'unknownField',
    'closeWithoutReceipt',
    'scripted',
  }.contains(scenario)) {
    throw ArgumentError.value(scenario, 'scenario');
  }
  if ((scenario == 'nativeCancelRegistry') != (runtimeLibraryPath != null)) {
    throw ArgumentError(
      'runtimeLibraryPath is required only for nativeCancelRegistry.',
    );
  }
  final bool needsStartupLifecycle =
      const <String>{
        'startupGateReady',
        'startupGateError',
        'startupGateExit',
        'startupMalformedReady',
        'startupMalformedOwnershipThenReady',
        'startupReadyMissingCommandPort',
        'startupReadyWrongCommandPort',
        'malformedWhileOwned',
        'cleanupGateResult',
        'cleanupGateOrtError',
        'fatalProtocolReply',
        'fatalWorkerReply',
        'fatalWorkerStaleReply',
        'scripted',
      }.contains(scenario) ||
      spawnGate != null;
  if (needsStartupLifecycle != (startupLifecyclePort != null)) {
    throw ArgumentError(
      'startupLifecyclePort is required only for startup lifecycle scenarios.',
    );
  }
  return spawnOrtWorker(
    entrypoint: _ortIsolateProtocolHarnessMain,
    startup: <String, Object?>{
      'version': ortWorkerProtocolVersion,
      'type': 'harnessStartup',
      'scenario': scenario,
      'runtimeLibraryPath': runtimeLibraryPath,
      'startupLifecyclePort': startupLifecyclePort,
      'maxMessageBytes': maxMessageBytes,
      'providerOptionCount': providerOptionCount,
    },
    maxPendingRuns: maxPendingRuns,
    maxMessageBytes: maxMessageBytes,
    maxOutstandingInputBytes: effectiveMaxOutstandingInputBytes,
    startupTimeout: startupTimeout,
    limits: limits,
    onControllerEventForTesting: onControllerEvent,
    requestCancelTokenForTesting: requestCancelToken,
    onParentStateForTesting: onParentState,
    spawnGateForTesting: spawnGate,
    initialRequestIdForTesting: initialRequestId,
  );
}

void _ortIsolateProtocolHarnessMain(Map<String, Object?> initialMessage) async {
  final Map<Object?, Object?> startup = workerMap(initialMessage);
  requireWorkerKeys(
    startup,
    required: const <String>{
      'version',
      'type',
      'responsePort',
      'scenario',
      'runtimeLibraryPath',
      'startupLifecyclePort',
      'maxMessageBytes',
      'providerOptionCount',
    },
  );
  requireWorkerVersion(startup);
  if (workerString(startup, 'type') != 'harnessStartup') {
    throw const FormatException('Expected the harness startup command.');
  }
  final Object? rawResponsePort = startup['responsePort'];
  if (rawResponsePort is! SendPort) {
    throw const FormatException('Harness response port is missing.');
  }
  final SendPort responsePort = rawResponsePort;
  final String scenario = workerString(startup, 'scenario');
  final String? runtimeLibraryPath = workerNullableString(
    startup,
    'runtimeLibraryPath',
  );
  final Object? rawStartupLifecyclePort = startup['startupLifecyclePort'];
  if (rawStartupLifecyclePort != null && rawStartupLifecyclePort is! SendPort) {
    throw const FormatException('Harness lifecycle port is malformed.');
  }
  final SendPort? startupLifecyclePort = rawStartupLifecyclePort as SendPort?;
  final int maxMessageBytes = workerBoundedInt(
    startup,
    'maxMessageBytes',
    1,
    maximumWorkerMessageBytes,
  );
  final int providerOptionCount = workerBoundedInt(
    startup,
    'providerOptionCount',
    0,
    128,
  );
  final ReceivePort commands = ReceivePort();
  responsePort.send(<String, Object?>{
    'version': ortWorkerProtocolVersion,
    'type': 'ownership',
    'commandPort': scenario == 'startupMalformedOwnershipThenReady'
        ? 'malformed-command-port'
        : commands.sendPort,
  });
  if (scenario == 'startupExit') {
    commands.close();
    return;
  }
  if (scenario == 'startupCrash') {
    commands.close();
    throw StateError('Synthetic uncaught startup isolate failure.');
  }
  if (scenario == 'startupError') {
    responsePort.send(<String, Object?>{
      'version': ortWorkerProtocolVersion,
      'type': 'startupError',
      'message': 'Synthetic bounded startup failure.',
    });
    await awaitOrtWorkerStartupRetirement(commands);
    return;
  }
  if (const <String>{
    'startupGateReady',
    'startupGateError',
    'startupGateExit',
  }.contains(scenario)) {
    final ReceivePort startupGate = ReceivePort();
    startupLifecyclePort!.send(<String, Object?>{
      'type': 'startupGate',
      'port': startupGate.sendPort,
    });
    try {
      await startupGate.first;
    } finally {
      startupGate.close();
    }
    if (scenario == 'startupGateExit') {
      startupLifecyclePort.send('startupExit');
      commands.close();
      return;
    }
    if (scenario == 'startupGateError') {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'startupError',
        'message': 'Synthetic delayed bounded startup failure.',
      });
      startupLifecyclePort.send('startupError');
      final String acknowledgement = await awaitOrtWorkerStartupRetirement(
        commands,
      );
      startupLifecyclePort.send('startupRetired:$acknowledgement');
      startupLifecyclePort.send('disposed');
      return;
    }
  }
  final OrtRuntime? cancellationRuntime = scenario == 'nativeCancelRegistry'
      ? OrtRuntime.open(
          source: OrtRuntimeSource.file(
            absolutePath: runtimeLibraryPath!,
            allowedRoot: p.dirname(runtimeLibraryPath),
          ),
          logId: 'fonix-isolate-registry-test',
        )
      : null;
  final ReceivePort? wrongCommands = scenario == 'startupReadyWrongCommandPort'
      ? ReceivePort()
      : null;
  final Map<String, Object?> readyMessage = <String, Object?>{
    'version': ortWorkerProtocolVersion,
    'type': 'ready',
    'commandPort': wrongCommands?.sendPort ?? commands.sendPort,
    'inputNames': const <String>['X'],
    'outputNames': const <String>['Y'],
    'diagnostics': scenario == 'startupMalformedReady'
        ? 'malformed-diagnostics'
        : _syntheticWorkerDiagnostics(providerOptionCount: providerOptionCount),
  };
  if (scenario == 'startupReadyMissingCommandPort') {
    readyMessage.remove('commandPort');
  }
  responsePort.send(readyMessage);
  var priorRequestId = 0;
  var closeReceiptSent = false;
  var terminalReplySent = false;
  await for (final Object? rawCommand in commands) {
    final Map<Object?, Object?> command = workerMap(rawCommand);
    requireWorkerVersion(command);
    final String type = workerString(command, 'type');
    requireWorkerCommandKeys(command, type);
    if (terminalReplySent) {
      if (type != 'retire' && type != 'close') {
        throw const FormatException('Harness received work after terminal.');
      }
      startupLifecyclePort?.send('terminalAcknowledgementReceived');
      commands.close();
      return;
    }
    if (type == 'retire') {
      if (!closeReceiptSent) {
        throw const FormatException('Harness retirement was not ready.');
      }
      commands.close();
      return;
    }
    if (closeReceiptSent) {
      throw const FormatException('Harness received work after close.');
    }
    if (type == 'close') {
      startupLifecyclePort?.send('closeReceived');
      wrongCommands?.close();
      cancellationRuntime?.dispose();
      startupLifecyclePort?.send('disposed');
      if (scenario == 'closeWithoutReceipt') {
        commands.close();
        return;
      }
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'closed',
      });
      closeReceiptSent = true;
      continue;
    }
    final int requestId = workerPositiveInt(command, 'requestId');

    if (scenario == 'scripted') {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'started',
        'requestId': requestId,
        'cancelToken': requestId,
      });
      final ReceivePort runGate = ReceivePort();
      startupLifecyclePort!.send(<String, Object?>{
        'type': 'scriptedRunGate',
        'requestId': requestId,
        'port': runGate.sendPort,
      });
      final Object? rawDisposition;
      try {
        rawDisposition = await runGate.first;
      } finally {
        runGate.close();
      }
      if (rawDisposition is! String ||
          !const <String>{
            'result',
            'ortError',
            'crash',
          }.contains(rawDisposition)) {
        throw const FormatException('Unknown scripted run disposition.');
      }
      if (rawDisposition == 'crash') {
        Isolate.current.kill(priority: Isolate.immediate);
        return;
      }
      if (rawDisposition == 'ortError') {
        responsePort.send(<String, Object?>{
          'version': ortWorkerProtocolVersion,
          'type': 'ortError',
          'requestId': requestId,
          'error': <String, Object?>{
            'kind': 'run',
            'operation': 'session_run',
            'domain': OrtErrorDomain.ortStatus.name,
            'code': 17,
            'ortCode': 1,
            'message': 'Synthetic scripted ORT failure.',
            'context': const <String, Object?>{},
          },
          'wasTerminationRequested': false,
        });
        priorRequestId = requestId;
        continue;
      }
    }

    Future<void> awaitRunStateCleanupGate() async {
      final ReceivePort cleanupGate = ReceivePort();
      startupLifecyclePort!.send(<String, Object?>{
        'type': 'runStateGate',
        'requestId': requestId,
        'port': cleanupGate.sendPort,
      });
      try {
        await cleanupGate.first;
      } finally {
        cleanupGate.close();
      }
      startupLifecyclePort.send(<String, Object?>{
        'type': 'runStateDisposed',
        'requestId': requestId,
      });
    }

    if (scenario == 'crashWithError') {
      throw StateError('Synthetic uncaught run isolate failure.');
    }
    if (scenario == 'crash') {
      Isolate.current.kill(priority: Isolate.immediate);
      return;
    }
    if (scenario == 'fatalProtocolReply') {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'fatalProtocol',
        'message': 'Synthetic fatal worker protocol failure.',
      });
      terminalReplySent = true;
      continue;
    }
    if (scenario == 'fatalWorkerReply') {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'fatalWorkerError',
        'requestId': requestId,
        'message': 'Synthetic fatal native cleanup failure.',
      });
      terminalReplySent = true;
      continue;
    }
    if (scenario == 'fatalWorkerStaleReply' && priorRequestId != 0) {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'fatalWorkerError',
        'requestId': priorRequestId,
        'message': 'Synthetic stale fatal native cleanup failure.',
      });
      terminalReplySent = true;
      continue;
    }
    if (scenario == 'malformedWhileOwned') {
      final ReceivePort ownershipGate = ReceivePort();
      startupLifecyclePort!.send(<String, Object?>{
        'type': 'runStateGate',
        'port': ownershipGate.sendPort,
      });
      responsePort.send(<String, Object?>{
        'version': 999,
        'type': 'result',
        'requestId': requestId,
      });
      try {
        await ownershipGate.first;
      } finally {
        ownershipGate.close();
      }
      startupLifecyclePort.send('runStateDisposed');
      continue;
    }
    if (scenario == 'malformed') {
      responsePort.send(<String, Object?>{
        'version': 999,
        'type': 'result',
        'requestId': requestId,
      });
      continue;
    }
    if (scenario == 'missingField') {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': requestId,
        'outputs': const <String, Object?>{},
      });
      continue;
    }
    if (scenario == 'unknownField') {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': requestId,
        'outputs': const <String, Object?>{},
        'providerEvidence': null,
        'providerDiagnostics': const <Object?>[],
        'diagnostics': _syntheticWorkerDiagnostics(),
        'wasTerminationRequested': false,
        'unexpected': true,
      });
      continue;
    }
    if (scenario == 'ortError' || scenario == 'cleanupGateOrtError') {
      await Future<void>.delayed(const Duration(milliseconds: 20));
      if (scenario == 'cleanupGateOrtError') {
        await awaitRunStateCleanupGate();
      }
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'ortError',
        'requestId': requestId,
        'error': <String, Object?>{
          'kind': 'run',
          'operation': 'session_run',
          'domain': OrtErrorDomain.ortStatus.name,
          'code': 17,
          'ortCode': 1,
          'message': 'Synthetic unrelated ORT failure.',
          'context': const <String, Object?>{},
        },
        'wasTerminationRequested': false,
      });
      continue;
    }
    if (scenario == 'nativeCancelRegistry') {
      final OrtRunOptions runOptions = OrtRunOptions(
        runtime: cancellationRuntime!,
      );
      final int cancelToken = registerOrtRunCancelToken(runOptions);
      var wasTerminationRequested = false;
      try {
        responsePort.send(<String, Object?>{
          'version': ortWorkerProtocolVersion,
          'type': 'started',
          'requestId': requestId,
          'cancelToken': cancelToken,
        });
        await Future<void>.delayed(const Duration(milliseconds: 50));
        wasTerminationRequested = finishOrtRunCancelToken(
          runOptions,
          cancelToken,
        );
      } finally {
        runOptions.dispose();
      }
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'ortError',
        'requestId': requestId,
        'error': <String, Object?>{
          'kind': 'run',
          'operation': 'session_run',
          'domain': OrtErrorDomain.ortStatus.name,
          'code': 17,
          'ortCode': 1,
          'message': 'Synthetic terminated worker run.',
          'context': const <String, Object?>{},
        },
        'wasTerminationRequested': wasTerminationRequested,
      });
      continue;
    }
    if (scenario == 'syntheticCancelToken') {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'started',
        'requestId': requestId,
        'cancelToken': 1,
      });
      await Future<void>.delayed(const Duration(milliseconds: 20));
    }
    final WorkerMessageBudget decodeBudget = WorkerMessageBudget(
      maxBytes: maxMessageBytes,
    );
    final Map<Object?, Object?> inputs = workerMap(command['inputs']);
    final OrtIsolateValue input = decodeIsolateValue(
      inputs['X'],
      budget: decodeBudget,
      depth: 0,
      limits: OrtResourceLimits(
        maxModelBytes: maxMessageBytes,
        maxTensorBytes: maxMessageBytes,
      ),
    );
    if (scenario == 'delay') {
      await Future<void>.delayed(const Duration(milliseconds: 150));
    }
    if (scenario == 'cleanupGateResult') {
      await awaitRunStateCleanupGate();
    }
    Map<String, Object?> encodedOutput() {
      final WorkerMessageBudget budget = WorkerMessageBudget(
        maxBytes: maxMessageBytes,
      );
      return <String, Object?>{
        'Y': encodeIsolateValue(input, budget: budget, depth: 0),
      };
    }

    if (scenario == 'stale' && priorRequestId != 0) {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': priorRequestId,
        'outputs': encodedOutput(),
        'providerEvidence': null,
        'providerDiagnostics': const <Object?>[],
        'diagnostics': _syntheticWorkerDiagnostics(),
        'wasTerminationRequested': false,
      });
    }
    if (scenario == 'futureReply' && priorRequestId == 0) {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': requestId + 1,
        'outputs': encodedOutput(),
        'providerEvidence': null,
        'providerDiagnostics': const <Object?>[],
        'diagnostics': _syntheticWorkerDiagnostics(),
        'wasTerminationRequested': false,
      });
    }
    responsePort.send(<String, Object?>{
      'version': ortWorkerProtocolVersion,
      'type': 'result',
      'requestId': requestId,
      'outputs': encodedOutput(),
      'providerEvidence': null,
      'providerDiagnostics': const <Object?>[],
      'diagnostics': _syntheticWorkerDiagnostics(),
      'wasTerminationRequested': false,
    });
    priorRequestId = requestId;
  }
}

Map<String, Object?> _syntheticWorkerDiagnostics({
  int providerOptionCount = 0,
}) => <String, Object?>{
  'schemaVersion': 1,
  'dartPackageVersion': fonixPackageVersion,
  'shimAbiVersion': fonixShimAbiVersion,
  'shimBuildId': 'fonix-worker-harness',
  'requiredOrtApiVersion': OrtApiVersion.v27.value,
  'negotiatedOrtApiVersion': OrtApiVersion.v27.value,
  'runtimeVersion': 'synthetic',
  'runtimeOwner': OrtRuntimeOwner.application.name,
  'runtimeMode': OrtRuntimeMode.file.name,
  'runtimeIdentity': 'synthetic-worker-runtime',
  'platform': Platform.operatingSystem,
  'architecture': Abi.current().toString().split('_').last,
  'artifactFlavor': 'synthetic',
  'artifactSha256': null,
  'modelId': 'synthetic-worker-model',
  'session': <String, Object?>{
    'executionMode': OrtExecutionMode.sequential.name,
    'graphOptimization': OrtGraphOptimization.all.name,
    'intraOpThreads': 0,
    'interOpThreads': 0,
    'memoryPattern': true,
    'fallbackPolicy': OrtFallbackPolicy.report.name,
  },
  'providers': <Object?>[
    if (providerOptionCount > 0)
      OrtProviderDiagnostics(
        wrapperId: 'synthetic',
        registrationMechanism: OrtProviderRegistrationMechanism.generic,
        registrationName: 'SyntheticExecutionProvider',
        reportedName: null,
        compiled: null,
        discoverable: null,
        registered: true,
        active: null,
        qualified: null,
        options: <String, String>{
          for (int index = 0; index < providerOptionCount; index += 1)
            'option_$index': 'value',
        },
      ).toJson(),
  ],
};
