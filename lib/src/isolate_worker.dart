import 'dart:async';
import 'dart:convert';
import 'dart:isolate';
import 'dart:typed_data';

import 'diagnostics.dart';
import 'exceptions.dart';
import 'isolate_protocol.dart';
import 'isolate_session.dart';
import 'isolate_value.dart';
import 'metadata.dart';
import 'resource_limits.dart';
import 'runtime.dart';
import 'runtime_source.dart';
import 'session_options.dart';
import 'tensor_type.dart';
import 'version.dart';

OrtValue _nativeValueFromIsolate(
  OrtRuntime runtime,
  OrtIsolateValue value,
  OrtResourceLimits limits,
) {
  switch (value) {
    case OrtIsolateTensor() when value.isString:
      return OrtStringTensor.fromStrings(
        runtime: runtime,
        values: value.copyStrings(),
        shape: value.shape.dimensions,
        limits: limits,
      );
    case OrtIsolateTensor():
      return createCopiedOrtTensor(
        runtime: runtime,
        values: value.copyBytes(),
        shape: value.shape.dimensions,
        elementType: value.elementType,
        limits: limits,
      );
    case OrtIsolateSequence():
      final List<OrtValue> children = <OrtValue>[];
      try {
        for (final OrtIsolateValue child in value.elements) {
          children.add(_nativeValueFromIsolate(runtime, child, limits));
        }
        return OrtSequence.fromValues(
          runtime: runtime,
          values: children,
          limits: limits,
        );
      } finally {
        for (final OrtValue child in children) {
          child.dispose();
        }
      }
    case OrtIsolateMap():
      final OrtValue keys = _nativeValueFromIsolate(
        runtime,
        value.keys,
        limits,
      );
      OrtValue? values;
      try {
        values = _nativeValueFromIsolate(runtime, value.values, limits);
        return OrtMap.fromValues(
          runtime: runtime,
          keys: keys,
          values: values,
          limits: limits,
        );
      } finally {
        values?.dispose();
        keys.dispose();
      }
    case OrtIsolateOptional():
      final OrtIsolateValue? contained = value.value;
      if (contained == null) {
        return OrtOptional.none(
          runtime: runtime,
          elementType: value.elementType,
          limits: limits,
        );
      }
      final OrtValue nativeContained = _nativeValueFromIsolate(
        runtime,
        contained,
        limits,
      );
      try {
        return OrtOptional.some(value: nativeContained, limits: limits);
      } finally {
        nativeContained.dispose();
      }
  }
}

void _preflightNativeWorkerValue(
  OrtValue value, {
  required WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  switch (value) {
    case OrtTensor():
      budget.addBytes(value.shape.rank * 8);
      budget.addBytes(value.info.byteLength);
    case OrtStringTensor():
      budget.addBytes(value.shape.rank * 8);
      budget.addBytes(value.info.byteLength);
      addWorkerStringRetention(budget, value.shape.elementCount);
    case OrtSequence():
      for (final OrtValue child in value.elements) {
        _preflightNativeWorkerValue(child, budget: budget, depth: depth + 1);
      }
    case OrtMap():
      _preflightNativeWorkerValue(value.keys, budget: budget, depth: depth + 1);
      _preflightNativeWorkerValue(
        value.values,
        budget: budget,
        depth: depth + 1,
      );
    case OrtOptional():
      measureWorkerType(
        value.type.optionalElement!,
        budget: budget,
        depth: depth + 1,
      );
      if (value.value case final OrtValue child) {
        _preflightNativeWorkerValue(child, budget: budget, depth: depth + 1);
      }
    default:
      throw StateError('Unsupported native value implementation.');
  }
}

Map<String, Object?> _encodeNativeWorkerValue(
  OrtValue value, {
  required WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  switch (value) {
    case OrtTensor():
      budget.addBytes(value.shape.rank * 8);
      budget.addBytes(value.info.byteLength);
      final Uint8List bytes = copyOrtTensorTypedBytes(value, value.elementType);
      return <String, Object?>{
        'kind': 'tensor',
        'elementType': value.elementType.nativeValue,
        'shape': value.shape.dimensions,
        'data': TransferableTypedData.fromList(<TypedData>[bytes]),
      };
    case OrtStringTensor():
      budget.addBytes(value.shape.rank * 8);
      budget.addBytes(value.info.byteLength);
      addWorkerStringRetention(budget, value.shape.elementCount);
      return <String, Object?>{
        'kind': 'tensor',
        'elementType': OrtTensorElementType.string.nativeValue,
        'shape': value.shape.dimensions,
        'strings': value.copyStrings(),
      };
    case OrtSequence():
      return <String, Object?>{
        'kind': 'sequence',
        'elements': <Object?>[
          for (final OrtValue child in value.elements)
            _encodeNativeWorkerValue(child, budget: budget, depth: depth + 1),
        ],
      };
    case OrtMap():
      return <String, Object?>{
        'kind': 'map',
        'keys': _encodeNativeWorkerValue(
          value.keys,
          budget: budget,
          depth: depth + 1,
        ),
        'values': _encodeNativeWorkerValue(
          value.values,
          budget: budget,
          depth: depth + 1,
        ),
      };
    case OrtOptional():
      return <String, Object?>{
        'kind': 'optional',
        'elementType': encodeWorkerType(
          value.type.optionalElement!,
          budget: budget,
          depth: depth + 1,
        ),
        'value': value.value == null
            ? null
            : _encodeNativeWorkerValue(
                value.value!,
                budget: budget,
                depth: depth + 1,
              ),
      };
    default:
      throw StateError('Unsupported native value implementation.');
  }
}

final class _OpenedOrtWorker {
  const _OpenedOrtWorker({
    required this.runtime,
    required this.session,
    required this.options,
    required this.maxMessageBytes,
  });

  final OrtRuntime runtime;
  final OrtSession session;
  final OrtSessionOptions options;
  final int maxMessageBytes;
}

_OpenedOrtWorker _openOrtWorkerResources(Map<Object?, Object?> startup) {
  requireWorkerKeys(
    startup,
    required: const <String>{
      'version',
      'type',
      'responsePort',
      'runtimeSource',
      'model',
      'options',
      'requiredApi',
      'logSeverity',
      'logId',
      'maxMessageBytes',
    },
  );
  final int maxMessageBytes = workerBoundedInt(
    startup,
    'maxMessageBytes',
    1,
    maximumWorkerMessageBytes,
  );
  final OrtSessionOptions options = decodeSessionOptions(startup['options']);
  final OrtRuntimeSource runtimeSource = decodeRuntimeSource(
    startup['runtimeSource'],
  );
  final int requiredApiValue = workerInt(startup, 'requiredApi');
  if (requiredApiValue != OrtApiVersion.v27.value) {
    throw const FormatException('Unsupported worker ORT API version.');
  }
  final OrtLogSeverity logSeverity =
      OrtLogSeverity.values[workerBoundedInt(
        startup,
        'logSeverity',
        0,
        OrtLogSeverity.values.length - 1,
      )];
  final String logId = workerString(startup, 'logId');
  final OrtModelSource model = decodeModelSource(
    startup['model'],
    options.limits,
  );
  final OrtRuntime runtime = OrtRuntime.open(
    source: runtimeSource,
    requiredApi: OrtApiVersion.v27,
    logSeverity: logSeverity,
    logId: logId,
  );
  try {
    final OrtSession session = OrtSession.fromModel(
      runtime: runtime,
      model: model,
      options: options,
    );
    return _OpenedOrtWorker(
      runtime: runtime,
      session: session,
      options: options,
      maxMessageBytes: maxMessageBytes,
    );
  } on Object {
    runtime.dispose();
    rethrow;
  }
}

Future<String> awaitOrtWorkerStartupRetirement(ReceivePort commandPort) async {
  await for (final Object? rawCommand in commandPort) {
    final Map<Object?, Object?> command = workerMap(rawCommand);
    requireWorkerVersion(command);
    final String type = workerString(command, 'type');
    requireWorkerCommandKeys(command, type);
    if (type != 'close' && type != 'retire') {
      throw const FormatException(
        'Startup-failed worker received a non-retirement command.',
      );
    }
    commandPort.close();
    return type;
  }
  throw const FormatException(
    'Startup-failed worker lost its retirement command port.',
  );
}

void ortIsolateWorkerMain(Map<String, Object?> initialMessage) async {
  SendPort? responsePort;
  ReceivePort? commandPort;
  OrtRuntime? runtime;
  OrtSession? session;
  try {
    final Map<Object?, Object?> startup = workerMap(initialMessage);
    requireWorkerVersion(startup);
    if (workerString(startup, 'type') != 'startup') {
      throw const FormatException('Expected the worker startup command.');
    }
    final Object? rawResponsePort = startup['responsePort'];
    if (rawResponsePort is! SendPort) {
      throw const FormatException('Worker response port is missing.');
    }
    responsePort = rawResponsePort;
    commandPort = ReceivePort();
    responsePort.send(<String, Object?>{
      'version': ortWorkerProtocolVersion,
      'type': 'ownership',
      'commandPort': commandPort.sendPort,
    });
    final _OpenedOrtWorker opened = _openOrtWorkerResources(startup);
    runtime = opened.runtime;
    session = opened.session;
    final OrtSessionOptions options = opened.options;
    final int maxMessageBytes = opened.maxMessageBytes;
    // Release startup protocol objects (including consumed transfer wrappers)
    // before entering the long-lived command loop. Decoded model/external bytes
    // were scoped inside _openOrtWorkerResources and are not retained here.
    initialMessage.clear();
    responsePort.send(<String, Object?>{
      'version': ortWorkerProtocolVersion,
      'type': 'ready',
      'commandPort': commandPort.sendPort,
      'inputNames': <String>[
        for (final OrtValueInfo input in session.inputs) input.name,
      ],
      'outputNames': <String>[
        for (final OrtValueInfo output in session.outputs) output.name,
      ],
      'diagnostics': session.diagnostics.toJson(),
    });

    var lastRequestId = 0;
    var closeReceiptSent = false;
    var terminalReplySent = false;
    await for (final Object? rawCommand in commandPort) {
      try {
        final Map<Object?, Object?> command = workerMap(rawCommand);
        requireWorkerVersion(command);
        final String type = workerString(command, 'type');
        requireWorkerCommandKeys(command, type);
        if (terminalReplySent) {
          if (type != 'retire' && type != 'close') {
            throw const FormatException(
              'Worker received work after its terminal reply.',
            );
          }
          commandPort.close();
          return;
        }
        if (type == 'retire') {
          if (!closeReceiptSent) {
            throw const FormatException('Worker retirement was not ready.');
          }
          commandPort.close();
          return;
        }
        if (closeReceiptSent) {
          throw const FormatException(
            'Worker received work after its close receipt.',
          );
        }
        if (type == 'close') {
          session!.dispose();
          session = null;
          runtime!.dispose();
          runtime = null;
          responsePort.send(<String, Object?>{
            'version': ortWorkerProtocolVersion,
            'type': 'closed',
          });
          closeReceiptSent = true;
          continue;
        }
        if (type != 'run') {
          throw const FormatException('Unknown worker command type.');
        }
        final int requestId = workerPositiveInt(command, 'requestId');
        if (requestId <= lastRequestId) {
          throw const FormatException('Worker request ID is stale.');
        }
        lastRequestId = requestId;
        final int commandMessageBytes = workerBoundedInt(
          command,
          'maxMessageBytes',
          1,
          maximumWorkerMessageBytes,
        );
        if (commandMessageBytes != maxMessageBytes) {
          throw const FormatException('Worker message bound changed.');
        }
        final _OrtWorkerRunCompletion completion = _executeOrtWorkerRun(
          responsePort: responsePort,
          command: command,
          requestId: requestId,
          runtime: runtime!,
          session: session!,
          options: options,
          maxMessageBytes: maxMessageBytes,
        );
        // _executeOrtWorkerRun returns only after every ordinary per-run native
        // owner has been disposed. Publishing settlement here prevents the
        // controller from releasing its aggregate input reservation early.
        responsePort.send(completion.reply);
        if (completion.fatalCleanupFailure) {
          terminalReplySent = true;
        }
      } on Object catch (error) {
        if (!terminalReplySent) {
          responsePort.send(<String, Object?>{
            'version': ortWorkerProtocolVersion,
            'type': 'fatalProtocol',
            'message': _safeWorkerFailureText(error),
          });
          terminalReplySent = true;
        }
      }
    }
  } on Object catch (error) {
    responsePort?.send(<String, Object?>{
      'version': ortWorkerProtocolVersion,
      'type': 'startupError',
      'message': _safeWorkerFailureText(error),
    });
    final ReceivePort? failedCommandPort = commandPort;
    if (responsePort != null && failedCommandPort != null) {
      // Keep this isolate alive until the controller has observed the exact
      // startup failure. The finally block below remains the sole owner of
      // native cleanup, and onExit is published only after it completes.
      await awaitOrtWorkerStartupRetirement(failedCommandPort);
    }
  } finally {
    commandPort?.close();
    session?.dispose();
    runtime?.dispose();
  }
}

final class _OrtWorkerRunCompletion {
  const _OrtWorkerRunCompletion({
    required this.reply,
    this.fatalCleanupFailure = false,
  });

  final Map<String, Object?> reply;
  final bool fatalCleanupFailure;
}

_OrtWorkerRunCompletion _executeOrtWorkerRun({
  required SendPort responsePort,
  required Map<Object?, Object?> command,
  required int requestId,
  required OrtRuntime runtime,
  required OrtSession session,
  required OrtSessionOptions options,
  required int maxMessageBytes,
}) {
  final List<OrtValue> nativeInputs = <OrtValue>[];
  OrtRunOptions? runOptions;
  OrtRunResult? runResult;
  int? cancelToken;
  Object? runError;
  var wasTerminationRequested = false;
  try {
    final Map<Object?, Object?> encodedInputs = workerMap(command['inputs']);
    if (encodedInputs.length > session.inputs.length) {
      throw const FormatException('Worker input count exceeds session inputs.');
    }
    final Set<String> knownInputNames = <String>{
      for (final OrtValueInfo input in session.inputs) input.name,
    };
    final WorkerMessageBudget decodeBudget = WorkerMessageBudget(
      maxBytes: maxMessageBytes,
    );
    final List<String> outputNames = workerStringList(
      command['outputNames'],
      maximum: session.outputs.length,
    );
    for (final String outputName in outputNames) {
      decodeBudget.addUtf8(outputName);
    }
    final Map<String, OrtValue> inputs = <String, OrtValue>{};
    for (final MapEntry<Object?, Object?> entry in encodedInputs.entries) {
      if (entry.key is! String || !knownInputNames.contains(entry.key)) {
        throw const FormatException('Worker input name is invalid.');
      }
      final String name = entry.key! as String;
      decodeBudget.addUtf8(name);
      final OrtIsolateValue isolateValue = decodeIsolateValue(
        entry.value,
        budget: decodeBudget,
        depth: 0,
        limits: options.limits,
      );
      final OrtValue nativeValue = _nativeValueFromIsolate(
        runtime,
        isolateValue,
        options.limits,
      );
      nativeInputs.add(nativeValue);
      inputs[name] = nativeValue;
    }
    runOptions = OrtRunOptions(runtime: runtime);
    cancelToken = registerOrtRunCancelToken(runOptions);
    try {
      responsePort.send(<String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'started',
        'requestId': requestId,
        'cancelToken': cancelToken,
      });
    } on Object catch (error, stackTrace) {
      try {
        finishOrtRunCancelToken(runOptions, cancelToken);
        cancelToken = null;
      } on Object catch (cleanupError) {
        throw WorkerCancellationCleanupFailure(cleanupError);
      }
      Error.throwWithStackTrace(error, stackTrace);
    }

    try {
      runResult = session.run(
        inputs: inputs,
        outputNames: outputNames,
        runOptions: runOptions,
      );
    } on Object catch (error) {
      runError = error;
    } finally {
      // Control cannot reach token finish below until this synchronous call
      // has returned on both success and error paths.
    }

    try {
      wasTerminationRequested = finishOrtRunCancelToken(
        runOptions,
        cancelToken,
      );
      cancelToken = null;
    } on Object catch (error) {
      return _OrtWorkerRunCompletion(
        fatalCleanupFailure: true,
        reply: <String, Object?>{
          'version': ortWorkerProtocolVersion,
          'type': 'fatalWorkerError',
          'requestId': requestId,
          'message':
              'Native cancellation state could not be retired safely: '
              '${_safeWorkerFailureText(error)}',
        },
      );
    }

    if (runError != null) {
      if (runError case final OrtException error) {
        return _OrtWorkerRunCompletion(
          reply: <String, Object?>{
            'version': ortWorkerProtocolVersion,
            'type': 'ortError',
            'requestId': requestId,
            'error': encodeWorkerOrtError(error),
            'wasTerminationRequested': wasTerminationRequested,
          },
        );
      }
      return _OrtWorkerRunCompletion(
        reply: <String, Object?>{
          'version': ortWorkerProtocolVersion,
          'type': 'workerError',
          'requestId': requestId,
          'message': _safeWorkerFailureText(runError),
        },
      );
    }

    final OrtRunResult completedResult = runResult!;
    final WorkerMessageBudget preflightBudget = WorkerMessageBudget(
      maxBytes: maxMessageBytes,
    );
    for (final String name in outputNames) {
      preflightBudget.addUtf8(name);
      _preflightNativeWorkerValue(
        completedResult.value(name),
        budget: preflightBudget,
        depth: 0,
      );
    }
    final WorkerMessageBudget encodeBudget = WorkerMessageBudget(
      maxBytes: maxMessageBytes,
    );
    final Map<String, Object?> encodedOutputs = <String, Object?>{};
    for (final String name in outputNames) {
      encodeBudget.addUtf8(name);
      encodedOutputs[name] = _encodeNativeWorkerValue(
        completedResult.value(name),
        budget: encodeBudget,
        depth: 0,
      );
    }
    final Map<String, Object?>? providerEvidenceReceipt = completedResult
        .providerEvidence
        ?.toJson();
    final List<Object?> providerDiagnosticsReceipt = <Object?>[
      for (final OrtProviderDiagnostics diagnostic
          in completedResult.providerDiagnostics)
        diagnostic.toJson(),
    ];
    final Map<String, Object?> diagnosticsReceipt = completedResult.diagnostics
        .toJson();
    encodeBudget
      ..addUtf8(jsonEncode(providerEvidenceReceipt))
      ..addUtf8(jsonEncode(providerDiagnosticsReceipt))
      ..addUtf8(jsonEncode(diagnosticsReceipt));
    completedResult.dispose();
    runResult = null;
    return _OrtWorkerRunCompletion(
      reply: <String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': requestId,
        'outputs': encodedOutputs,
        'providerEvidence': providerEvidenceReceipt,
        'providerDiagnostics': providerDiagnosticsReceipt,
        'diagnostics': diagnosticsReceipt,
        'wasTerminationRequested': wasTerminationRequested,
      },
    );
  } on WorkerCancellationCleanupFailure catch (error) {
    return _OrtWorkerRunCompletion(
      fatalCleanupFailure: true,
      reply: <String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'fatalWorkerError',
        'requestId': requestId,
        'message':
            'Native cancellation state could not be retired safely: '
            '${_safeWorkerFailureText(error.cause)}',
      },
    );
  } on OrtException catch (error) {
    return _OrtWorkerRunCompletion(
      reply: <String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'ortError',
        'requestId': requestId,
        'error': encodeWorkerOrtError(error),
        'wasTerminationRequested': wasTerminationRequested,
      },
    );
  } on Object catch (error) {
    return _OrtWorkerRunCompletion(
      reply: <String, Object?>{
        'version': ortWorkerProtocolVersion,
        'type': 'workerError',
        'requestId': requestId,
        'message': _safeWorkerFailureText(error),
      },
    );
  } finally {
    runResult?.dispose();
    runOptions?.dispose();
    for (final OrtValue input in nativeInputs) {
      input.dispose();
    }
    // On finish/unset failure, the registry intentionally retains runOptions.
    // Releasing this Dart owner remains fail-safe.
  }
}

String _safeWorkerFailureText(Object error) {
  if (error is OrtException) {
    return '${error.runtimeType}(${error.operation}, ${error.code}): '
        '${boundedWorkerText(error.message, 'native operation failed')}';
  }
  if (error is FormatException ||
      error is ArgumentError ||
      error is RangeError ||
      error is StateError ||
      error is UnsupportedError) {
    return boundedWorkerText(error.toString(), 'worker validation failed');
  }
  return 'Worker failure (${error.runtimeType}).';
}
