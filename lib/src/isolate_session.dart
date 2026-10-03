import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:path/path.dart' as p;

import 'diagnostics.dart';
import 'exceptions.dart';
import 'ffi/generated_native_asset_bindings.dart' as bindings;
import 'ffi/native_api.dart';
import 'isolate_value.dart';
import 'metadata.dart';
import 'provider.dart';
import 'provider_evidence.dart';
import 'resource_limits.dart';
import 'runtime.dart';
import 'runtime_source.dart';
import 'session_options.dart';
import 'tensor_type.dart';
import 'utf16.dart';
import 'version.dart';

part 'isolate_protocol.dart';
part 'isolate_protocol_harness.dart';
part 'isolate_worker.dart';

typedef _OrtWorkerEntrypoint = void Function(Map<String, Object?> message);

/// How a worker-run cancellation request was handled.
///
/// This distinguishes a request removed before native execution from a
/// request delivered to ONNX Runtime's active run-options termination state.
/// Callers that only need the historical boolean contract can continue to use
/// [OrtIsolateRun.cancel].
enum OrtRunCancellationDisposition {
  /// The run had already settled, or its native termination token was no
  /// longer active, so no cancellation was requested.
  notCancelled,

  /// The run was removed from the bounded worker queue before dispatch.
  queuedRunRemoved,

  /// The request reached the active native termination registry.
  nativeTerminationRequested;

  /// Whether the run was cancelled or native termination was requested.
  bool get wasRequested => this != notCancelled;
}

/// Fully copied outputs from one isolate-owned inference run.
final class OrtIsolateRunResult {
  OrtIsolateRunResult._(
    Map<String, OrtIsolateValue> outputs, {
    required this.providerEvidence,
    required List<OrtProviderDiagnostics> providerDiagnostics,
    required this.diagnostics,
  }) : providerDiagnostics = List<OrtProviderDiagnostics>.unmodifiable(
         providerDiagnostics,
       ),
       outputs = Map<String, OrtIsolateValue>.unmodifiable(outputs),
       outputNames = List<String>.unmodifiable(outputs.keys);

  final List<String> outputNames;
  final Map<String, OrtIsolateValue> outputs;
  final OrtProviderRunEvidence? providerEvidence;
  final List<OrtProviderDiagnostics> providerDiagnostics;

  /// Immutable, redacted diagnostics for this exact worker run.
  final OrtDiagnostics diagnostics;

  OrtIsolateValue value(String name) {
    final OrtIsolateValue? result = outputs[name];
    if (result == null) {
      throw ArgumentError('The isolate result has no output named $name.');
    }
    return result;
  }

  OrtIsolateTensor tensor(String name) {
    final OrtIsolateValue result = value(name);
    if (result is! OrtIsolateTensor) {
      throw StateError('The named isolate output is not a tensor.');
    }
    return result;
  }
}

/// A submitted isolate run with cooperative, request-scoped cancellation.
final class OrtIsolateRun {
  OrtIsolateRun._(this._owner, this._request);

  final OrtIsolateSession _owner;
  final _PendingIsolateRun _request;

  Future<OrtIsolateRunResult> get result => _request.completer.future;

  bool get isCompleted => _request.completer.isCompleted;

  /// Cancels a queued run or requests ORT termination for an active run.
  ///
  /// The returned boolean is true when this call cancelled a queued request or
  /// reached the native termination registry. Repeated calls return the same
  /// future. A first call after settlement returns false.
  ///
  /// Use [cancelWithDisposition] when evidence must distinguish those two
  /// successful mechanisms.
  Future<bool> cancel() =>
      _request.booleanCancellationFuture ??= cancelWithDisposition().then(
        (OrtRunCancellationDisposition value) => value.wasRequested,
      );

  /// Cancels this run and reports the exact cancellation mechanism.
  ///
  /// Repeated calls return the same future. A native disposition proves that
  /// the request reached the active run-options termination registry; callers
  /// must still await [result] to observe authoritative run settlement.
  Future<OrtRunCancellationDisposition> cancelWithDisposition() =>
      _owner._cancel(_request);
}

/// A long-lived worker isolate that exclusively owns one runtime and session.
final class OrtIsolateSession {
  OrtIsolateSession._({
    required SendPort commandPort,
    required ReceivePort responsePort,
    required ReceivePort lifecyclePort,
    required StreamSubscription<Object?> responseSubscription,
    required StreamSubscription<Object?> lifecycleSubscription,
    required List<String> inputNames,
    required List<String> outputNames,
    required this.diagnostics,
    required this.maxPendingRuns,
    required this.maxMessageBytes,
    required this.maxOutstandingInputBytes,
    required OrtResourceLimits limits,
    required int initialRequestId,
    required bool Function(int token) requestCancelToken,
    void Function(String event)? onControllerEventForTesting,
  }) : _limits = limits,
       _commandPort = commandPort,
       _responsePort = responsePort,
       _lifecyclePort = lifecyclePort,
       _responseSubscription = responseSubscription,
       _lifecycleSubscription = lifecycleSubscription,
       _requestCancelToken = requestCancelToken,
       _onControllerEventForTesting = onControllerEventForTesting,
       _nextRequestId = initialRequestId,
       inputNames = List<String>.unmodifiable(inputNames),
       outputNames = List<String>.unmodifiable(outputNames);

  /// Starts a dedicated isolate and creates the runtime/session inside it.
  static Future<OrtIsolateSession> spawn({
    required OrtModelSource model,
    OrtRuntimeSource runtimeSource = const OrtRuntimeSource.bundled(),
    OrtSessionOptions? options,
    OrtApiVersion requiredApi = OrtApiVersion.v27,
    OrtLogSeverity logSeverity = OrtLogSeverity.warning,
    String logId = 'fonix-worker',
    int maxPendingRuns = 8,
    int maxMessageBytes = _defaultWorkerMessageBytes,
    int? maxOutstandingInputBytes,
    Duration startupTimeout = const Duration(seconds: 30),
  }) {
    final int effectiveMaxOutstandingInputBytes =
        maxOutstandingInputBytes ?? maxMessageBytes;
    _validateWorkerBounds(
      maxPendingRuns: maxPendingRuns,
      maxMessageBytes: maxMessageBytes,
      maxOutstandingInputBytes: effectiveMaxOutstandingInputBytes,
      startupTimeout: startupTimeout,
    );
    final OrtSessionOptions effectiveOptions = options ?? OrtSessionOptions();
    final Map<String, Object?> startup = _encodeWorkerStartup(
      runtimeSource: runtimeSource,
      model: model,
      options: effectiveOptions,
      requiredApi: requiredApi,
      logSeverity: logSeverity,
      logId: logId,
      maxMessageBytes: maxMessageBytes,
    );
    return _spawnOrtWorker(
      entrypoint: _ortIsolateWorkerMain,
      startup: startup,
      maxPendingRuns: maxPendingRuns,
      maxMessageBytes: maxMessageBytes,
      maxOutstandingInputBytes: effectiveMaxOutstandingInputBytes,
      startupTimeout: startupTimeout,
      limits: effectiveOptions.limits,
    );
  }

  final SendPort _commandPort;
  final ReceivePort _responsePort;
  final ReceivePort _lifecyclePort;
  final StreamSubscription<Object?> _responseSubscription;
  final StreamSubscription<Object?> _lifecycleSubscription;
  final bool Function(int token) _requestCancelToken;
  final void Function(String event)? _onControllerEventForTesting;
  final ListQueue<_PendingIsolateRun> _queue = ListQueue<_PendingIsolateRun>();
  final List<String> inputNames;
  final List<String> outputNames;

  /// Immutable, redacted diagnostics copied at worker session creation.
  final OrtDiagnostics diagnostics;
  final int maxPendingRuns;
  final int maxMessageBytes;
  final int maxOutstandingInputBytes;

  /// The session's limits, which bound what a worker message may carry.
  final OrtResourceLimits _limits;

  _PendingIsolateRun? _active;
  int _nextRequestId;
  bool _requestIdsExhausted = false;
  int _lastSettledRequestId = 0;
  int _outstandingInputBytes = 0;
  bool _closing = false;
  bool _closeCommandSent = false;
  bool _retireCommandSent = false;
  bool _closed = false;
  bool _connectionsClosed = false;
  OrtWorkerException? _terminalFailure;
  StackTrace? _terminalStackTrace;
  _PendingIsolateRun? _terminalActiveReservation;
  Completer<void>? _closeCompleter;

  bool get isClosing => _closing;
  bool get isClosed => _closed;
  int get outstandingRuns => _queue.length + (_active == null ? 0 : 1);
  int get outstandingInputBytes => _outstandingInputBytes;
  int get availableRunSlots =>
      _requestIdsExhausted ? 0 : maxPendingRuns - outstandingRuns;
  int get availableInputBytes => _requestIdsExhausted
      ? 0
      : maxOutstandingInputBytes - outstandingInputBytes;

  Future<OrtIsolateRunResult> run({
    required Map<String, OrtIsolateValue> inputs,
    List<String>? outputNames,
  }) => startRun(inputs: inputs, outputNames: outputNames).result;

  OrtIsolateRun startRun({
    required Map<String, OrtIsolateValue> inputs,
    List<String>? outputNames,
  }) {
    _ensureAcceptingRuns();
    final _ValidatedWorkerInputs checkedInputs = _validateWorkerRunInputs(
      supplied: inputs,
      knownNames: inputNames,
      maxMessageBytes: maxMessageBytes,
    );
    final List<String> checkedOutputs = _validateWorkerOutputNames(
      supplied: outputNames,
      knownNames: this.outputNames,
    );
    _validateWorkerRequestMessageBytes(
      inputBytes: checkedInputs.bytes,
      outputNames: checkedOutputs,
      maxMessageBytes: maxMessageBytes,
    );
    return _enqueueValidatedRun(checkedInputs, checkedOutputs);
  }

  OrtIsolateRun _enqueueValidatedRun(
    _ValidatedWorkerInputs inputs,
    List<String> outputNames,
  ) {
    _ensureAcceptingRuns();
    if (inputs.bytes > maxOutstandingInputBytes) {
      throw OrtWorkerMessageTooLargeException(
        message: 'The worker input can never fit its aggregate byte capacity.',
        context: <String, Object?>{
          'maxOutstandingInputBytes': maxOutstandingInputBytes,
          'requestedInputBytes': inputs.bytes,
        },
      );
    }
    if (outstandingRuns >= maxPendingRuns ||
        inputs.bytes > availableInputBytes) {
      throw OrtWorkerQueueFullException(
        message: 'The isolate session has reached bounded run capacity.',
        context: <String, Object?>{
          'maxPendingRuns': maxPendingRuns,
          'outstandingRuns': outstandingRuns,
          'maxOutstandingInputBytes': maxOutstandingInputBytes,
          'outstandingInputBytes': outstandingInputBytes,
          'requestedInputBytes': inputs.bytes,
        },
      );
    }
    final int requestId = _nextRequestId;
    if (requestId == _maximumWorkerRequestId) {
      _requestIdsExhausted = true;
    } else {
      _nextRequestId = requestId + 1;
    }
    final _PendingIsolateRun request = _PendingIsolateRun(
      id: requestId,
      inputs: inputs.values,
      inputBytes: inputs.bytes,
      outputNames: outputNames,
    );
    _outstandingInputBytes += inputs.bytes;
    _queue.addLast(request);
    _dispatchNext();
    return OrtIsolateRun._(this, request);
  }

  bool _canAcceptInputBytes(int bytes) =>
      !_requestIdsExhausted &&
      bytes <= maxOutstandingInputBytes &&
      outstandingRuns < maxPendingRuns &&
      bytes <= availableInputBytes;

  /// Gracefully drains the active run after requesting cancellation, then asks
  /// the worker to dispose its session/runtime before exiting.
  ///
  /// No timeout or silent forced-kill fallback is used: providers that do not
  /// cooperate with termination can delay this future. Process isolation is
  /// required when a hard security deadline is needed.
  Future<void> close() {
    final Completer<void>? existing = _closeCompleter;
    if (existing != null) return existing.future;
    final Completer<void> completer = Completer<void>();
    _closeCompleter = completer;
    if (_closed) {
      final OrtWorkerException? terminal = _terminalFailure;
      if (terminal == null) {
        completer.complete();
      } else {
        completer.completeError(terminal, _terminalStackTrace);
      }
      return completer.future;
    }
    final OrtWorkerException? terminal = _terminalFailure;
    if (terminal != null) {
      _closing = true;
      return completer.future;
    }
    _closing = true;
    final OrtWorkerClosedException closedError = OrtWorkerClosedException();
    while (_queue.isNotEmpty) {
      final _PendingIsolateRun request = _queue.removeFirst();
      request.releaseInputs();
      _releaseInputReservation(request);
      request.completeError(closedError);
      request.completeCancellation(OrtRunCancellationDisposition.notCancelled);
    }
    final _PendingIsolateRun? active = _active;
    if (active != null) {
      unawaited(_requestCancellationForClose(active));
    } else {
      if (_closeCommandSent) {
        _sendRetireCommand();
      } else {
        _sendCloseCommand();
      }
    }
    return completer.future;
  }

  Future<void> _requestCancellationForClose(_PendingIsolateRun request) async {
    try {
      await _cancel(request);
    } on Object {
      // Cancellation only accelerates graceful close. If the request itself
      // fails, keep draining the authoritative run and close the worker after
      // its result settles instead of surfacing an unhandled asynchronous
      // error from this caller-less cancellation future.
    }
  }

  void _ensureAcceptingRuns() {
    final OrtWorkerException? terminal = _terminalFailure;
    if (terminal != null) throw terminal;
    if (_closing || _closed) throw OrtWorkerClosedException();
    if (_requestIdsExhausted) {
      throw OrtWorkerClosedException(
        message: 'The isolate session exhausted its request identifier space.',
        context: const <String, Object?>{
          'maximumRequestId': _maximumWorkerRequestId,
        },
      );
    }
  }

  Future<OrtRunCancellationDisposition> _cancel(_PendingIsolateRun request) {
    final Future<OrtRunCancellationDisposition>? existing =
        request.cancellationFuture;
    if (existing != null) return existing;
    final Completer<OrtRunCancellationDisposition> completer =
        Completer<OrtRunCancellationDisposition>();
    request.cancellationCompleter = completer;
    request.cancellationFuture = completer.future;
    if (request.completer.isCompleted) {
      completer.complete(OrtRunCancellationDisposition.notCancelled);
      return completer.future;
    }
    if (!request.dispatched) {
      final bool removed = _queue.remove(request);
      if (removed) {
        request.cancelRequested = true;
        request.releaseInputs();
        _releaseInputReservation(request);
        request.completeError(
          OrtRunCancelledException(
            context: <String, Object?>{'requestId': request.id},
          ),
        );
        completer.complete(OrtRunCancellationDisposition.queuedRunRemoved);
        _dispatchNext();
        return completer.future;
      }
    }
    request.cancelRequested = true;
    final int? token = request.cancelToken;
    if (token != null) {
      _requestNativeCancellation(request, token);
    }
    return completer.future;
  }

  void _requestNativeCancellation(_PendingIsolateRun request, int token) {
    if (request.cancellationCompleter?.isCompleted ?? true) return;
    try {
      final bool requested = _requestCancelToken(token);
      request.nativeCancellationRequested = requested;
      request.cancellationCompleter!.complete(
        requested
            ? OrtRunCancellationDisposition.nativeTerminationRequested
            : OrtRunCancellationDisposition.notCancelled,
      );
    } on FonixNativeFailure catch (failure, stackTrace) {
      if (failure.code ==
          bindings.dort_error_code.DORT_ERROR_CANCEL_TOKEN_UNKNOWN) {
        request.cancellationCompleter!.complete(
          OrtRunCancellationDisposition.notCancelled,
        );
      } else {
        request.cancellationCompleter!.completeError(
          OrtWorkerException(
            operation: 'worker_cancel_request',
            code: failure.code,
            message: 'The native cancellation request failed.',
            context: <String, Object?>{'requestId': request.id},
            cause: failure,
          ),
          stackTrace,
        );
      }
    } on Object catch (error, stackTrace) {
      request.cancellationCompleter!.completeError(error, stackTrace);
    }
  }

  void _dispatchNext() {
    if (_active != null || _terminalFailure != null || _closed) return;
    if (_closing) {
      _sendCloseCommand();
      return;
    }
    if (_queue.isEmpty) return;
    final _PendingIsolateRun request = _queue.removeFirst();
    request.dispatched = true;
    _active = request;
    try {
      final Map<String, OrtIsolateValue> inputs = request.takeInputs();
      final _WorkerMessageBudget budget = _WorkerMessageBudget(
        maxBytes: maxMessageBytes,
      );
      final Map<String, Object?> encodedInputs = <String, Object?>{};
      for (final MapEntry<String, OrtIsolateValue> entry in inputs.entries) {
        budget.addUtf8(entry.key);
        encodedInputs[entry.key] = _encodeIsolateValue(
          entry.value,
          budget: budget,
          depth: 0,
        );
      }
      for (final String outputName in request.outputNames) {
        budget.addUtf8(outputName);
      }
      _commandPort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
        'type': 'run',
        'requestId': request.id,
        'inputs': encodedInputs,
        'outputNames': request.outputNames,
        'maxMessageBytes': maxMessageBytes,
      });
    } on Object catch (error, stackTrace) {
      _active = null;
      _releaseInputReservation(request);
      request.completeError(error, stackTrace);
      request.completeCancellation(OrtRunCancellationDisposition.notCancelled);
      _dispatchNext();
    }
  }

  void _sendCloseCommand() {
    if (_closeCommandSent || _closed) return;
    _closeCommandSent = true;
    _commandPort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'close',
    });
  }

  void _handleWorkerMessage(Object? rawMessage) {
    if (_closed) return;
    if (_terminalFailure != null) {
      try {
        final Map<Object?, Object?> message = _workerMap(rawMessage);
        _requireWorkerVersion(message);
        final String type = _workerString(message, 'type');
        if (type != 'closed') return;
        _requireWorkerReplyKeys(message, type);
        if (!_closeCommandSent || _active != null) return;
        _sendRetireCommand();
        _finishRetirement();
      } on Object {
        // Preserve the first terminal failure. A malformed late message cannot
        // authorize cleanup or replace it; the authoritative exit receipt can
        // still retire the worker.
      }
      return;
    }
    try {
      final Map<Object?, Object?> message = _workerMap(rawMessage);
      _requireWorkerVersion(message);
      final String type = _workerString(message, 'type');
      _requireWorkerReplyKeys(message, type);
      if (type == 'closed') {
        if (!_closeCommandSent || _active != null) {
          throw const FormatException('Unexpected worker close receipt.');
        }
        _sendRetireCommand();
        _finishRetirement();
        return;
      }
      if (type == 'fatalProtocol') {
        _failTerminal(
          OrtWorkerProtocolException(
            message: _boundedWorkerText(
              message['message'],
              'worker protocol failure',
            ),
          ),
          acknowledgeWorkerTerminalReply: true,
        );
        return;
      }
      final int requestId = _workerPositiveInt(message, 'requestId');
      final _PendingIsolateRun? request = _active;
      if (type == 'fatalWorkerError') {
        if (request == null || requestId != request.id) {
          throw const FormatException(
            'Fatal worker reply does not match the active request.',
          );
        }
        final OrtWorkerProtocolException error = OrtWorkerProtocolException(
          message: _boundedWorkerText(
            message['message'],
            'The worker could not retire native run state safely.',
          ),
          context: <String, Object?>{'requestId': request.id},
        );
        request.completeError(error);
        request.completeCancellationError(error);
        _failTerminal(error, acknowledgeWorkerTerminalReply: true);
        return;
      }
      if (request == null || requestId != request.id) {
        if (requestId <= _lastSettledRequestId) {
          return; // A stale reply cannot settle a newer request.
        }
        throw const FormatException('Worker reply has an unknown request ID.');
      }
      switch (type) {
        case 'started':
          if (request.cancelToken != null) {
            throw const FormatException('Duplicate worker start receipt.');
          }
          final int token = _workerPositiveInt(message, 'cancelToken');
          request.cancelToken = token;
          if (request.cancelRequested) {
            _requestNativeCancellation(request, token);
          }
          break;
        case 'result':
          _workerBool(message, 'wasTerminationRequested');
          final OrtIsolateRunResult result = _decodeWorkerResult(
            message['outputs'],
            rawProviderEvidence: message['providerEvidence'],
            rawProviderDiagnostics: message['providerDiagnostics'],
            rawDiagnostics: message['diagnostics'],
            expectedSessionDiagnostics: diagnostics,
            expectedNames: request.outputNames,
            maxMessageBytes: maxMessageBytes,
            limits: _limits,
          );
          request.complete(result);
          request.completeCancellation(
            OrtRunCancellationDisposition.notCancelled,
          );
          _settleActive(request);
          break;
        case 'ortError':
          final OrtException error = _decodeWorkerOrtError(message['error']);
          final bool wasTerminationRequested = _workerBool(
            message,
            'wasTerminationRequested',
          );
          request.completeError(
            wasTerminationRequested && request.nativeCancellationRequested
                ? OrtRunCancelledException(
                    context: <String, Object?>{'requestId': request.id},
                    cause: error,
                  )
                : error,
          );
          request.completeCancellation(
            OrtRunCancellationDisposition.notCancelled,
          );
          _settleActive(request);
          break;
        case 'workerError':
          request.completeError(
            OrtWorkerProtocolException(
              message: _boundedWorkerText(
                message['message'],
                'The worker could not complete the run protocol.',
              ),
              context: <String, Object?>{'requestId': request.id},
            ),
          );
          request.completeCancellation(
            OrtRunCancellationDisposition.notCancelled,
          );
          _settleActive(request);
          break;
        default:
          throw const FormatException('Unknown worker message type.');
      }
    } on OrtWorkerException catch (error, stackTrace) {
      _failTerminal(error, stackTrace: stackTrace);
    } on Object catch (error, stackTrace) {
      _failTerminal(
        OrtWorkerProtocolException(
          message: 'The worker sent a malformed versioned message.',
          cause: error,
        ),
        stackTrace: stackTrace,
      );
    }
  }

  void _settleActive(_PendingIsolateRun request) {
    if (!identical(_active, request)) return;
    assert(request.id > _lastSettledRequestId);
    _lastSettledRequestId = request.id;
    request.cancelToken = null;
    _active = null;
    _releaseInputReservation(request);
    _dispatchNext();
  }

  void _handleWorkerError(Object? rawError) {
    final String summary = _boundedWorkerCrash(rawError);
    _failTerminal(
      OrtWorkerCrashedException(message: 'Worker isolate crashed: $summary'),
    );
  }

  void _handleWorkerExit() {
    if (_closed) return;
    if (_terminalFailure == null) {
      _failTerminal(
        OrtWorkerCrashedException(
          message: 'Worker isolate exited without a graceful close receipt.',
        ),
      );
    }
    _finishRetirement();
  }

  void _failTerminal(
    OrtWorkerException error, {
    StackTrace? stackTrace,
    bool acknowledgeWorkerTerminalReply = false,
  }) {
    if (_terminalFailure != null || _closed) return;
    _terminalFailure = error;
    _terminalStackTrace = stackTrace;
    _closing = true;
    final _PendingIsolateRun? active = _active;
    _active = null;
    if (active != null) {
      active.releaseInputs();
      // A malformed or fatal reply can arrive before the worker's native run
      // state has been disposed. Keep the active byte reservation until the
      // worker proves cleanup with `closed`, or until its exit is observed.
      _terminalActiveReservation = active;
    }
    active?.completeError(error, stackTrace);
    active?.completeCancellationError(error, stackTrace);
    while (_queue.isNotEmpty) {
      final _PendingIsolateRun request = _queue.removeFirst();
      request.releaseInputs();
      _releaseInputReservation(request);
      request.completeError(error, stackTrace);
      request.completeCancellationError(error, stackTrace);
    }
    if (acknowledgeWorkerTerminalReply) {
      // The worker waits for this acknowledgement before exiting, so onExit
      // cannot overtake and replace the exact terminal reply. Its outer
      // finally block disposes session/runtime before that exit is observed.
      _sendRetireCommand();
    } else {
      // The authoritative command port was published before fallible native
      // setup. Request ordered cleanup through it even after a malformed
      // reply: that reply may have been sent before the worker's run-state
      // finally block completed.
      _sendCloseCommand();
    }
  }

  void _releaseInputReservation(_PendingIsolateRun request) {
    if (!request.inputReservationHeld) return;
    assert(_outstandingInputBytes >= request.inputBytes);
    request.inputReservationHeld = false;
    _outstandingInputBytes -= request.inputBytes;
  }

  void _finishRetirement() {
    if (_closed) return;
    final _PendingIsolateRun? terminalActive = _terminalActiveReservation;
    _terminalActiveReservation = null;
    if (terminalActive != null) {
      _releaseInputReservation(terminalActive);
    }
    _closed = true;
    final Completer<void>? completer = _closeCompleter;
    if (completer != null && !completer.isCompleted) {
      final OrtWorkerException? terminal = _terminalFailure;
      if (terminal == null) {
        completer.complete();
      } else {
        completer.completeError(terminal, _terminalStackTrace);
      }
    }
    _closeConnections();
  }

  void _sendRetireCommand() {
    if (_retireCommandSent) return;
    _retireCommandSent = true;
    _commandPort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'retire',
    });
  }

  void _closeConnections() {
    if (_connectionsClosed) return;
    _connectionsClosed = true;
    unawaited(_responseSubscription.cancel());
    unawaited(_lifecycleSubscription.cancel());
    _responsePort.close();
    _lifecyclePort.close();
    try {
      _onControllerEventForTesting?.call('connectionsClosed');
    } on Object {
      // A package-internal test observer must never affect worker ownership.
    }
  }
}

final class _PendingIsolateRun {
  _PendingIsolateRun({
    required this.id,
    required Map<String, OrtIsolateValue> inputs,
    required this.inputBytes,
    required this.outputNames,
  }) : _inputs = inputs;

  final int id;
  Map<String, OrtIsolateValue>? _inputs;
  final int inputBytes;
  final List<String> outputNames;
  final Completer<OrtIsolateRunResult> completer =
      Completer<OrtIsolateRunResult>();
  bool dispatched = false;
  bool cancelRequested = false;
  bool nativeCancellationRequested = false;
  int? cancelToken;
  Completer<OrtRunCancellationDisposition>? cancellationCompleter;
  Future<OrtRunCancellationDisposition>? cancellationFuture;
  Future<bool>? booleanCancellationFuture;
  bool inputReservationHeld = true;

  Map<String, OrtIsolateValue> takeInputs() {
    final Map<String, OrtIsolateValue>? values = _inputs;
    if (values == null) {
      throw StateError('Worker request inputs were already released.');
    }
    _inputs = null;
    return values;
  }

  void releaseInputs() {
    _inputs = null;
  }

  void complete(OrtIsolateRunResult result) {
    if (!completer.isCompleted) completer.complete(result);
  }

  void completeError(Object error, [StackTrace? stackTrace]) {
    if (!completer.isCompleted) completer.completeError(error, stackTrace);
  }

  void completeCancellation(OrtRunCancellationDisposition result) {
    final Completer<OrtRunCancellationDisposition>? cancellation =
        cancellationCompleter;
    if (cancellation != null && !cancellation.isCompleted) {
      cancellation.complete(result);
    }
  }

  void completeCancellationError(Object error, [StackTrace? stackTrace]) {
    final Completer<OrtRunCancellationDisposition>? cancellation =
        cancellationCompleter;
    if (cancellation != null && !cancellation.isCompleted) {
      cancellation.completeError(error, stackTrace);
    }
  }
}

final class _WorkerCancellationCleanupFailure implements Exception {
  const _WorkerCancellationCleanupFailure(this.cause);

  final Object cause;
}

/// An explicitly sized pool of independent isolate-owned sessions.
final class OrtSessionPool {
  OrtSessionPool._(this._workers) {
    final OrtIsolateSession first = _workers.first;
    for (final OrtIsolateSession worker in _workers.skip(1)) {
      if (!_sameWorkerNames(worker.inputNames, first.inputNames) ||
          !_sameWorkerNames(worker.outputNames, first.outputNames) ||
          worker.maxMessageBytes != first.maxMessageBytes) {
        throw ArgumentError(
          'Session-pool workers must share one input/output message contract.',
        );
      }
    }
  }

  static Future<OrtSessionPool> spawn({
    required int size,
    required OrtModelSource model,
    OrtRuntimeSource runtimeSource = const OrtRuntimeSource.bundled(),
    OrtSessionOptions? options,
    OrtApiVersion requiredApi = OrtApiVersion.v27,
    OrtLogSeverity logSeverity = OrtLogSeverity.warning,
    String logId = 'fonix-pool',
    int maxPendingRunsPerWorker = 8,
    int maxMessageBytes = _defaultWorkerMessageBytes,
    int? maxOutstandingInputBytesPerWorker,
    Duration startupTimeout = const Duration(seconds: 30),
  }) async {
    if (size < 1 || size > _maximumSessionPoolSize) {
      throw RangeError.range(size, 1, _maximumSessionPoolSize, 'size');
    }
    final List<OrtIsolateSession> workers = <OrtIsolateSession>[];
    try {
      for (var index = 0; index < size; index += 1) {
        workers.add(
          await OrtIsolateSession.spawn(
            model: model,
            runtimeSource: runtimeSource,
            options: options,
            requiredApi: requiredApi,
            logSeverity: logSeverity,
            logId: '$logId-$index',
            maxPendingRuns: maxPendingRunsPerWorker,
            maxMessageBytes: maxMessageBytes,
            maxOutstandingInputBytes: maxOutstandingInputBytesPerWorker,
            startupTimeout: startupTimeout,
          ),
        );
      }
      return OrtSessionPool._(List<OrtIsolateSession>.unmodifiable(workers));
    } on Object catch (error, stackTrace) {
      final List<Object> cleanupFailures = <Object>[];
      for (final OrtIsolateSession worker in workers.reversed) {
        try {
          await worker.close();
        } on Object catch (cleanupError) {
          cleanupFailures.add(cleanupError);
        }
      }
      if (error is OrtWorkerStartupException && cleanupFailures.isEmpty) {
        Error.throwWithStackTrace(error, stackTrace);
      }
      throw OrtWorkerStartupException(
        message: 'The bounded session pool did not start completely.',
        context: <String, Object?>{
          'requestedSize': size,
          'startedWorkers': workers.length,
          'cleanupFailureCount': cleanupFailures.length,
        },
        cause: error,
      );
    }
  }

  final List<OrtIsolateSession> _workers;
  int _tieBreaker = 0;
  bool _closing = false;
  Completer<void>? _closeCompleter;

  int get size => _workers.length;
  bool get isClosing => _closing;
  int get outstandingRuns => _workers.fold<int>(
    0,
    (int total, OrtIsolateSession worker) => total + worker.outstandingRuns,
  );
  int get outstandingInputBytes => _workers.fold<int>(
    0,
    (int total, OrtIsolateSession worker) =>
        total + worker.outstandingInputBytes,
  );

  Future<OrtIsolateRunResult> run({
    required Map<String, OrtIsolateValue> inputs,
    List<String>? outputNames,
  }) => startRun(inputs: inputs, outputNames: outputNames).result;

  OrtIsolateRun startRun({
    required Map<String, OrtIsolateValue> inputs,
    List<String>? outputNames,
  }) {
    if (_closing) throw OrtWorkerClosedException();
    final OrtIsolateSession contractWorker = _workers.first;
    final _ValidatedWorkerInputs checkedInputs = _validateWorkerRunInputs(
      supplied: inputs,
      knownNames: contractWorker.inputNames,
      maxMessageBytes: contractWorker.maxMessageBytes,
    );
    final List<String> checkedOutputs = _validateWorkerOutputNames(
      supplied: outputNames,
      knownNames: contractWorker.outputNames,
    );
    _validateWorkerRequestMessageBytes(
      inputBytes: checkedInputs.bytes,
      outputNames: checkedOutputs,
      maxMessageBytes: contractWorker.maxMessageBytes,
    );
    if (!_workers.any(
      (OrtIsolateSession worker) =>
          checkedInputs.bytes <= worker.maxOutstandingInputBytes,
    )) {
      throw OrtWorkerMessageTooLargeException(
        message:
            'The worker input can never fit any pool worker byte capacity.',
        context: <String, Object?>{
          'requestedInputBytes': checkedInputs.bytes,
          'maximumWorkerInputBytes': _workers.fold<int>(
            0,
            (int maximum, OrtIsolateSession worker) =>
                maximum > worker.maxOutstandingInputBytes
                ? maximum
                : worker.maxOutstandingInputBytes,
          ),
        },
      );
    }
    OrtIsolateSession? selected;
    OrtWorkerException? terminalFailure;
    var selectedIndex = -1;
    var selectedLoad = 1 << 62;
    final int startIndex = _tieBreaker;
    for (var offset = 0; offset < _workers.length; offset += 1) {
      final int index = (startIndex + offset) % _workers.length;
      final OrtIsolateSession worker = _workers[index];
      terminalFailure ??= worker._terminalFailure;
      if (worker.isClosing ||
          worker.isClosed ||
          !worker._canAcceptInputBytes(checkedInputs.bytes)) {
        continue;
      }
      if (worker.outstandingRuns < selectedLoad) {
        selected = worker;
        selectedIndex = index;
        selectedLoad = worker.outstandingRuns;
      }
    }
    if (selected == null) {
      if (terminalFailure != null) throw terminalFailure;
      if (_workers
          .where(
            (OrtIsolateSession worker) =>
                checkedInputs.bytes <= worker.maxOutstandingInputBytes,
          )
          .every(
            (OrtIsolateSession worker) =>
                worker.isClosing ||
                worker.isClosed ||
                worker._requestIdsExhausted,
          )) {
        throw OrtWorkerClosedException(
          message: 'The session pool has no live worker available.',
          context: <String, Object?>{'poolSize': size},
        );
      }
      throw OrtWorkerQueueFullException(
        message: 'Every session-pool worker has reached bounded capacity.',
        context: <String, Object?>{
          'poolSize': size,
          'outstandingRuns': outstandingRuns,
          'outstandingInputBytes': outstandingInputBytes,
          'requestedInputBytes': checkedInputs.bytes,
        },
      );
    }
    _tieBreaker = (selectedIndex + 1) % _workers.length;
    return selected._enqueueValidatedRun(checkedInputs, checkedOutputs);
  }

  Future<void> close() {
    final Completer<void>? existing = _closeCompleter;
    if (existing != null) return existing.future;
    final Completer<void> completer = Completer<void>();
    _closeCompleter = completer;
    _closing = true;
    unawaited(
      Future.wait<void>(
        _workers.map((OrtIsolateSession worker) => worker.close()),
        eagerError: false,
      ).then(completer.complete, onError: completer.completeError),
    );
    return completer.future;
  }
}

bool _sameWorkerNames(List<String> left, List<String> right) {
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

/// Internal pool constructor for real-isolate protocol tests.
OrtSessionPool createOrtSessionPoolForTesting(List<OrtIsolateSession> workers) {
  if (workers.isEmpty || workers.length > _maximumSessionPoolSize) {
    throw RangeError.range(
      workers.length,
      1,
      _maximumSessionPoolSize,
      'workers.length',
    );
  }
  return OrtSessionPool._(List<OrtIsolateSession>.unmodifiable(workers));
}

Future<OrtIsolateSession> _spawnOrtWorker({
  required _OrtWorkerEntrypoint entrypoint,
  required Map<String, Object?> startup,
  required int maxPendingRuns,
  required int maxMessageBytes,
  required int maxOutstandingInputBytes,
  required Duration startupTimeout,
  required OrtResourceLimits limits,
  void Function(String event)? onControllerEventForTesting,
  bool Function(int token)? requestCancelTokenForTesting,
  void Function(bool, bool, bool, bool)? onParentStateForTesting,
  Future<void>? spawnGateForTesting,
  int initialRequestIdForTesting = 1,
}) async {
  if (initialRequestIdForTesting < 1 ||
      initialRequestIdForTesting > _maximumWorkerRequestId) {
    throw RangeError.range(
      initialRequestIdForTesting,
      1,
      _maximumWorkerRequestId,
      'initialRequestIdForTesting',
    );
  }
  final ReceivePort responsePort = ReceivePort();
  final ReceivePort lifecyclePort = ReceivePort();
  final Completer<OrtIsolateSession> ready = Completer<OrtIsolateSession>();
  Isolate? isolate;
  OrtIsolateSession? session;
  SendPort? ownershipCommandPort;
  SendPort? cleanupOnlyReadyCommandPort;
  Timer? startupTimer;
  var callerAbandoned = false;
  var bootstrapCloseCommandSent = false;
  var bootstrapRetireCommandSent = false;
  var bootstrapConnectionsClosed = false;
  late final StreamSubscription<Object?> responseSubscription;
  late final StreamSubscription<Object?> lifecycleSubscription;

  void emitControllerEvent(String event) {
    try {
      onControllerEventForTesting?.call(event);
    } on Object {
      // A package-internal test observer must never affect worker ownership.
    }
  }

  void closeBootstrapConnections() {
    if (bootstrapConnectionsClosed) return;
    bootstrapConnectionsClosed = true;
    startupTimer?.cancel();
    unawaited(responseSubscription.cancel());
    unawaited(lifecycleSubscription.cancel());
    responsePort.close();
    lifecyclePort.close();
    emitControllerEvent('connectionsClosed');
  }

  bool abandonCaller(Object error, [StackTrace? stackTrace]) {
    if (ready.isCompleted) return false;
    callerAbandoned = true;
    startupTimer?.cancel();
    ready.completeError(error, stackTrace);
    emitControllerEvent('callerAbandoned');
    return true;
  }

  void sendBootstrapClose(SendPort commandPort) {
    if (bootstrapCloseCommandSent ||
        bootstrapRetireCommandSent ||
        bootstrapConnectionsClosed) {
      return;
    }
    bootstrapCloseCommandSent = true;
    commandPort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'close',
    });
    emitControllerEvent('gracefulCloseSent');
  }

  void sendBootstrapRetire(SendPort commandPort) {
    if (bootstrapRetireCommandSent ||
        bootstrapCloseCommandSent ||
        bootstrapConnectionsClosed) {
      return;
    }
    bootstrapRetireCommandSent = true;
    commandPort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'retire',
    });
    emitControllerEvent('startupRetireSent');
  }

  responseSubscription = responsePort.listen((Object? rawMessage) {
    final OrtIsolateSession? current = session;
    if (current != null) {
      current._handleWorkerMessage(rawMessage);
      return;
    }
    try {
      final Map<Object?, Object?> message = _workerMap(rawMessage);
      _requireWorkerVersion(message);
      final String type = _workerString(message, 'type');
      if (type == 'ownership') {
        if (ownershipCommandPort != null) {
          throw const FormatException(
            'Worker sent a duplicate startup ownership port.',
          );
        }
        final Object? rawCommandPort = message['commandPort'];
        if (rawCommandPort is! SendPort) {
          throw const FormatException(
            'Worker startup ownership port is malformed.',
          );
        }
        ownershipCommandPort = rawCommandPort;
        _requireWorkerStartupReplyKeys(message, type);
        if (callerAbandoned) sendBootstrapClose(rawCommandPort);
        return;
      }
      if (bootstrapCloseCommandSent && type == 'closed') {
        _requireWorkerReplyKeys(message, type);
        (ownershipCommandPort ?? cleanupOnlyReadyCommandPort)?.send(
          <String, Object?>{
            'version': _ortWorkerProtocolVersion,
            'type': 'retire',
          },
        );
        closeBootstrapConnections();
        return;
      }
      _requireWorkerStartupReplyKeys(message, type);
      if (type == 'startupError') {
        abandonCaller(
          OrtWorkerStartupException(
            message: _boundedWorkerText(
              message['message'],
              'Worker runtime/session startup failed.',
            ),
          ),
        );
        // The worker keeps the authoritative command port alive until this
        // acknowledgement. It then disposes every native owner before exit,
        // which is the controller's cleanup boundary.
        sendBootstrapRetire(
          ownershipCommandPort ??
              (throw const FormatException(
                'Worker startup failure has no authoritative ownership port.',
              )),
        );
        return;
      }
      if (type != 'ready') {
        throw const FormatException('Expected the worker ready message.');
      }
      final Object? rawCommandPort = message['commandPort'];
      if (rawCommandPort is! SendPort) {
        throw const FormatException('Worker ready command port is malformed.');
      }
      final SendPort commandPort = rawCommandPort;
      if (callerAbandoned && ownershipCommandPort == null) {
        // A malformed ownership message has already failed startup. A later
        // valid ready port is useful only for orderly cleanup; it must never
        // establish a usable session or replace authoritative ownership.
        final SendPort? existingCleanupPort = cleanupOnlyReadyCommandPort;
        if (existingCleanupPort != null && commandPort != existingCleanupPort) {
          throw const FormatException(
            'Worker sent conflicting cleanup-only ready ports.',
          );
        }
        cleanupOnlyReadyCommandPort = commandPort;
        emitControllerEvent('lateReady');
        sendBootstrapClose(commandPort);
        return;
      }
      final SendPort authoritativeCommandPort =
          ownershipCommandPort ??
          (throw const FormatException(
            'Worker became ready before startup ownership was established.',
          ));
      if (commandPort != authoritativeCommandPort) {
        throw const FormatException(
          'Worker ready command port does not match startup ownership.',
        );
      }
      if (callerAbandoned) {
        // Native setup has returned and the worker has exposed its command
        // port. Retire it through the normal protocol even though the bounded
        // caller-facing startup future has already failed.
        emitControllerEvent('lateReady');
        sendBootstrapClose(authoritativeCommandPort);
        return;
      }
      final List<String> inputNames = _workerStringList(
        message['inputNames'],
        maximum: 256,
      );
      final List<String> outputNames = _workerStringList(
        message['outputNames'],
        maximum: 256,
      );
      final OrtDiagnostics diagnostics = _decodeWorkerFullDiagnostics(
        message['diagnostics'],
        limits,
      );
      if (outputNames.isEmpty) {
        throw const FormatException('Worker session has no output names.');
      }
      if (isolate == null) {
        throw const FormatException('Worker became ready before spawn.');
      }
      session = OrtIsolateSession._(
        commandPort: authoritativeCommandPort,
        responsePort: responsePort,
        lifecyclePort: lifecyclePort,
        responseSubscription: responseSubscription,
        lifecycleSubscription: lifecycleSubscription,
        inputNames: inputNames,
        outputNames: outputNames,
        diagnostics: diagnostics,
        maxPendingRuns: maxPendingRuns,
        maxMessageBytes: maxMessageBytes,
        maxOutstandingInputBytes: maxOutstandingInputBytes,
        limits: limits,
        initialRequestId: initialRequestIdForTesting,
        onControllerEventForTesting: onControllerEventForTesting,
        requestCancelToken:
            requestCancelTokenForTesting ??
            FonixNativeApi.nativeAsset().requestCancelToken,
      );
      startupTimer?.cancel();
      ready.complete(session!);
    } on Object catch (error, stackTrace) {
      abandonCaller(
        OrtWorkerProtocolException(
          message: 'Worker startup used a malformed protocol message.',
          cause: error,
        ),
        stackTrace,
      );
      // Cleanup uses either the authoritative ownership port or the sole valid
      // ready port accepted after startup had already been abandoned.
      final SendPort? commandPort =
          ownershipCommandPort ?? cleanupOnlyReadyCommandPort;
      if (commandPort != null) {
        sendBootstrapClose(commandPort);
      }
    }
  });
  lifecycleSubscription = lifecyclePort.listen((Object? event) {
    final OrtIsolateSession? current = session;
    if (event != null) {
      if (current != null) {
        current._handleWorkerError(event);
      } else {
        abandonCaller(
          OrtWorkerStartupException(
            message:
                'Worker isolate crashed during startup: '
                '${_boundedWorkerCrash(event)}',
          ),
        );
        // Fatal isolate errors are followed by an exit event on this same
        // ordered port. Retain the controller ports until that cleanup boundary.
      }
      return;
    }
    if (current != null) {
      current._handleWorkerExit();
    } else {
      abandonCaller(
        OrtWorkerStartupException(
          message: 'Worker isolate exited before runtime/session readiness.',
        ),
      );
      closeBootstrapConnections();
    }
  });

  final Map<String, Object?> initialMessage = <String, Object?>{
    ...startup,
    'responsePort': responsePort.sendPort,
  };
  startupTimer = Timer(startupTimeout, () {
    final bool timeoutWon = abandonCaller(
      OrtWorkerStartupException(
        message: 'Worker runtime/session startup exceeded its bounded wait.',
        context: <String, Object?>{
          'startupTimeoutMilliseconds': startupTimeout.inMilliseconds,
        },
      ),
    );
    if (!timeoutWon) return;
    final SendPort? commandPort = ownershipCommandPort;
    if (commandPort != null) {
      sendBootstrapClose(commandPort);
    } else if (isolate == null) {
      // The spawn future has not produced an isolate, so no native setup can
      // have begun. Close the controller side now; a late paused isolate is
      // killed before resume by acceptSpawnedIsolate.
      closeBootstrapConnections();
    }
  });

  Future<Isolate> spawnWorker() async {
    final Future<void>? spawnGate = spawnGateForTesting;
    if (spawnGate != null) await spawnGate;
    return Isolate.spawn<Map<String, Object?>>(
      entrypoint,
      initialMessage,
      paused: true,
      // Dart's isolate error and exit notifications are independently ordered
      // only when they target one mailbox. The non-null error must be observed
      // before the null exit so exact crash diagnostics remain authoritative.
      onError: lifecyclePort.sendPort,
      onExit: lifecyclePort.sendPort,
      errorsAreFatal: true,
      debugName: 'fonix-ort-worker',
    );
  }

  void failBeforeResume(Isolate? spawnedIsolate, OrtWorkerException error) {
    // A paused isolate has not entered native setup, so this is the one state
    // where immediate termination cannot interrupt native ownership.
    spawnedIsolate?.kill(priority: Isolate.immediate);
    abandonCaller(error);
    closeBootstrapConnections();
  }

  void acceptSpawnedIsolate(Isolate spawnedIsolate) {
    if (bootstrapConnectionsClosed || callerAbandoned) {
      spawnedIsolate.kill(priority: Isolate.immediate);
      closeBootstrapConnections();
      emitControllerEvent('latePausedIsolateKilled');
      return;
    }
    isolate = spawnedIsolate;
    final Capability? resumeCapability = spawnedIsolate.pauseCapability;
    if (resumeCapability == null) {
      failBeforeResume(
        spawnedIsolate,
        OrtWorkerStartupException(
          message: 'Paused worker isolate has no resume capability.',
        ),
      );
      return;
    }
    try {
      onParentStateForTesting?.call(
        identical(isolate, spawnedIsolate),
        startupTimer?.isActive ?? false,
        ready.isCompleted,
        spawnedIsolate.pauseCapability != null,
      );
    } on Object {
      // A package-internal observer cannot alter startup ownership.
    }
    try {
      spawnedIsolate.resume(resumeCapability);
    } on Object catch (error) {
      failBeforeResume(
        spawnedIsolate,
        OrtWorkerStartupException(
          message: 'Could not resume the paused isolate session worker.',
          cause: error,
        ),
      );
    }
  }

  unawaited(
    spawnWorker().then<void>(
      acceptSpawnedIsolate,
      onError: (Object error, StackTrace stackTrace) {
        abandonCaller(
          OrtWorkerStartupException(
            message: 'Could not spawn the isolate session worker.',
            cause: error,
          ),
          stackTrace,
        );
        closeBootstrapConnections();
      },
    ),
  );
  return ready.future;
}

OrtIsolateRunResult _decodeWorkerResult(
  Object? raw, {
  required Object? rawProviderEvidence,
  required Object? rawProviderDiagnostics,
  required Object? rawDiagnostics,
  required OrtDiagnostics expectedSessionDiagnostics,
  required List<String> expectedNames,
  required int maxMessageBytes,
  required OrtResourceLimits limits,
}) {
  final Map<Object?, Object?> outputs = _workerMap(raw);
  if (outputs.length != expectedNames.length) {
    throw const FormatException('Worker result count changed unexpectedly.');
  }
  final _WorkerMessageBudget budget = _WorkerMessageBudget(
    maxBytes: maxMessageBytes,
  );
  final Map<String, OrtIsolateValue> decoded = <String, OrtIsolateValue>{};
  for (final String name in expectedNames) {
    if (!outputs.containsKey(name)) {
      throw const FormatException('Worker result omitted a requested output.');
    }
    budget.addUtf8(name);
    decoded[name] = _decodeIsolateValue(
      outputs[name],
      budget: budget,
      depth: 0,
      limits: OrtResourceLimits(
        maxTensorBytes: maxMessageBytes,
        maxModelBytes: maxMessageBytes,
      ),
    );
  }
  final OrtProviderRunEvidence? evidence = _decodeWorkerProviderEvidence(
    rawProviderEvidence,
    budget,
  );
  final List<OrtProviderDiagnostics> diagnostics =
      _decodeWorkerProviderDiagnostics(
        rawProviderDiagnostics,
        evidence: evidence,
        budget: budget,
        limits: limits,
      );
  final OrtDiagnostics fullDiagnostics = _decodeWorkerFullDiagnostics(
    rawDiagnostics,
    limits,
  );
  budget.addUtf8(jsonEncode(fullDiagnostics.toJson()));
  if (jsonEncode(<Object?>[
        for (final OrtProviderDiagnostics value in diagnostics) value.toJson(),
      ]) !=
      jsonEncode(<Object?>[
        for (final OrtProviderDiagnostics value in fullDiagnostics.providers)
          value.toJson(),
      ])) {
    throw const FormatException(
      'Worker diagnostics conflict with the provider receipt.',
    );
  }
  final Map<String, Object?> expectedSession = expectedSessionDiagnostics
      .toJson();
  final Map<String, Object?> actualSession = fullDiagnostics.toJson();
  expectedSession['providers'] = const <Object?>[];
  actualSession['providers'] = const <Object?>[];
  if (jsonEncode(expectedSession) != jsonEncode(actualSession)) {
    throw const FormatException(
      'Worker run diagnostics conflict with session creation facts.',
    );
  }
  return OrtIsolateRunResult._(
    decoded,
    providerEvidence: evidence,
    providerDiagnostics: diagnostics,
    diagnostics: fullDiagnostics,
  );
}
