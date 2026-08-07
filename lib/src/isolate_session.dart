part of 'runtime.dart';

const int _ortWorkerProtocolVersion = 4;
const int _defaultWorkerMessageBytes = 64 * 1024 * 1024;
const int _maximumWorkerMessageBytes = 1024 * 1024 * 1024;
const int _maximumOutstandingWorkerInputBytes = 1024 * 1024 * 1024;
const int _maximumPendingWorkerRuns = 1024;
const int _maximumSessionPoolSize = 32;
const int _maximumWorkerCompositeChildren = 1024;

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
    required ReceivePort errorPort,
    required ReceivePort exitPort,
    required StreamSubscription<Object?> responseSubscription,
    required StreamSubscription<Object?> errorSubscription,
    required StreamSubscription<Object?> exitSubscription,
    required List<String> inputNames,
    required List<String> outputNames,
    required this.diagnostics,
    required this.maxPendingRuns,
    required this.maxMessageBytes,
    required this.maxOutstandingInputBytes,
    required bool Function(int token) requestCancelToken,
  }) : _commandPort = commandPort,
       _responsePort = responsePort,
       _errorPort = errorPort,
       _exitPort = exitPort,
       _responseSubscription = responseSubscription,
       _errorSubscription = errorSubscription,
       _exitSubscription = exitSubscription,
       _requestCancelToken = requestCancelToken,
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
    );
  }

  final SendPort _commandPort;
  final ReceivePort _responsePort;
  final ReceivePort _errorPort;
  final ReceivePort _exitPort;
  final StreamSubscription<Object?> _responseSubscription;
  final StreamSubscription<Object?> _errorSubscription;
  final StreamSubscription<Object?> _exitSubscription;
  final bool Function(int token) _requestCancelToken;
  final ListQueue<_PendingIsolateRun> _queue = ListQueue<_PendingIsolateRun>();
  final List<String> inputNames;
  final List<String> outputNames;

  /// Immutable, redacted diagnostics copied at worker session creation.
  final OrtDiagnostics diagnostics;
  final int maxPendingRuns;
  final int maxMessageBytes;
  final int maxOutstandingInputBytes;

  _PendingIsolateRun? _active;
  int _nextRequestId = 1;
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
  int get availableRunSlots => maxPendingRuns - outstandingRuns;
  int get availableInputBytes =>
      maxOutstandingInputBytes - outstandingInputBytes;

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
    final _PendingIsolateRun request = _PendingIsolateRun(
      id: _nextRequestId++,
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
      if (failure.code == _nativeErrorCancelTokenUnknown) {
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
        if (requestId < _nextRequestId) {
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
    unawaited(_errorSubscription.cancel());
    unawaited(_exitSubscription.cancel());
    _responsePort.close();
    _errorPort.close();
    _exitPort.close();
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
      if (_workers.any(
        (OrtIsolateSession worker) => worker.isClosing || worker.isClosed,
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

/// Round-trips session options through the versioned worker message contract.
///
/// This package-internal helper is used only by protocol tests; native handles
/// are never involved.
OrtSessionOptions roundTripOrtWorkerSessionOptionsForTesting(
  OrtSessionOptions options,
) => _decodeSessionOptions(_encodeSessionOptions(options));

Future<OrtIsolateSession> _spawnOrtWorker({
  required _OrtWorkerEntrypoint entrypoint,
  required Map<String, Object?> startup,
  required int maxPendingRuns,
  required int maxMessageBytes,
  required int maxOutstandingInputBytes,
  required Duration startupTimeout,
  void Function(String event)? onControllerEventForTesting,
  bool Function(int token)? requestCancelTokenForTesting,
  void Function(bool, bool, bool, bool)? onParentStateForTesting,
  Future<void>? spawnGateForTesting,
}) async {
  final ReceivePort responsePort = ReceivePort();
  final ReceivePort errorPort = ReceivePort();
  final ReceivePort exitPort = ReceivePort();
  final Completer<OrtIsolateSession> ready = Completer<OrtIsolateSession>();
  Isolate? isolate;
  OrtIsolateSession? session;
  SendPort? ownershipCommandPort;
  Timer? startupTimer;
  var callerAbandoned = false;
  var bootstrapCloseCommandSent = false;
  var bootstrapConnectionsClosed = false;
  late final StreamSubscription<Object?> responseSubscription;
  late final StreamSubscription<Object?> errorSubscription;
  late final StreamSubscription<Object?> exitSubscription;

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
    unawaited(errorSubscription.cancel());
    unawaited(exitSubscription.cancel());
    responsePort.close();
    errorPort.close();
    exitPort.close();
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
    if (bootstrapCloseCommandSent || bootstrapConnectionsClosed) return;
    bootstrapCloseCommandSent = true;
    commandPort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'close',
    });
    emitControllerEvent('gracefulCloseSent');
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
        ownershipCommandPort?.send(<String, Object?>{
          'version': _ortWorkerProtocolVersion,
          'type': 'retire',
        });
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
        // The worker sends startupError before its finally block disposes the
        // native owners. Closing these controller ports does not interrupt it.
        closeBootstrapConnections();
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
        errorPort: errorPort,
        exitPort: exitPort,
        responseSubscription: responseSubscription,
        errorSubscription: errorSubscription,
        exitSubscription: exitSubscription,
        inputNames: inputNames,
        outputNames: outputNames,
        diagnostics: diagnostics,
        maxPendingRuns: maxPendingRuns,
        maxMessageBytes: maxMessageBytes,
        maxOutstandingInputBytes: maxOutstandingInputBytes,
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
      // Cleanup never depends on trusting the malformed ready payload. The
      // worker published this authoritative port before fallible native setup.
      final SendPort? commandPort = ownershipCommandPort;
      if (commandPort != null) {
        sendBootstrapClose(commandPort);
      }
    }
  });
  errorSubscription = errorPort.listen((Object? rawError) {
    final OrtIsolateSession? current = session;
    if (current != null) {
      current._handleWorkerError(rawError);
    } else {
      abandonCaller(
        OrtWorkerStartupException(
          message:
              'Worker isolate crashed during startup: '
              '${_boundedWorkerCrash(rawError)}',
        ),
      );
      closeBootstrapConnections();
    }
  });
  exitSubscription = exitPort.listen((Object? _) {
    final OrtIsolateSession? current = session;
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
      onError: errorPort.sendPort,
      onExit: exitPort.sendPort,
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

void _validateWorkerBounds({
  required int maxPendingRuns,
  required int maxMessageBytes,
  required int maxOutstandingInputBytes,
  required Duration startupTimeout,
}) {
  if (maxPendingRuns < 1 || maxPendingRuns > _maximumPendingWorkerRuns) {
    throw RangeError.range(
      maxPendingRuns,
      1,
      _maximumPendingWorkerRuns,
      'maxPendingRuns',
    );
  }
  if (maxMessageBytes < 1 || maxMessageBytes > _maximumWorkerMessageBytes) {
    throw RangeError.range(
      maxMessageBytes,
      1,
      _maximumWorkerMessageBytes,
      'maxMessageBytes',
    );
  }
  if (maxOutstandingInputBytes < 1 ||
      maxOutstandingInputBytes > _maximumOutstandingWorkerInputBytes) {
    throw RangeError.range(
      maxOutstandingInputBytes,
      1,
      _maximumOutstandingWorkerInputBytes,
      'maxOutstandingInputBytes',
    );
  }
  if (startupTimeout <= Duration.zero ||
      startupTimeout > const Duration(minutes: 10)) {
    throw RangeError('startupTimeout must be in (zero, 10 minutes].');
  }
}

void _validateWorkerText(String value, String name, int maximumBytes) {
  if (value.isEmpty ||
      !_hasWellFormedUtf16(value) ||
      value.contains('\u0000') ||
      value.contains('\n') ||
      value.contains('\r') ||
      utf8.encode(value).length > maximumBytes) {
    throw ArgumentError('$name must be bounded safe UTF-8 text.');
  }
}

Map<String, Object?> _encodeWorkerStartup({
  required OrtRuntimeSource runtimeSource,
  required OrtModelSource model,
  required OrtSessionOptions options,
  required OrtApiVersion requiredApi,
  required OrtLogSeverity logSeverity,
  required String logId,
  required int maxMessageBytes,
}) {
  _validateWorkerText(logId, 'logId', 128);
  return <String, Object?>{
    'version': _ortWorkerProtocolVersion,
    'type': 'startup',
    'runtimeSource': _encodeRuntimeSource(runtimeSource),
    'model': _encodeModelSource(model),
    'options': _encodeSessionOptions(options),
    'requiredApi': requiredApi.value,
    'logSeverity': logSeverity.index,
    'logId': logId,
    'maxMessageBytes': maxMessageBytes,
  };
}

Map<String, Object?> _encodeRuntimeSource(OrtRuntimeSource source) =>
    <String, Object?>{
      'kind': source.kind.name,
      if (source.libraryPath case final String path) 'libraryPath': path,
      if (source.allowedRoot case final String root) 'allowedRoot': root,
      if (source.preferredLibraryNames.isNotEmpty)
        'preferredLibraryNames': source.preferredLibraryNames,
    };

Map<String, Object?> _encodeModelSource(OrtModelSource model) =>
    switch (model) {
      OrtBytesModelSource() => <String, Object?>{
        'kind': 'bytes',
        'modelId': model.modelId,
        'bytes': TransferableTypedData.fromList(<TypedData>[model.bytes]),
        'externalData': <String, Object?>{
          for (final MapEntry<String, Uint8List> entry
              in model.externalData.entries)
            entry.key: TransferableTypedData.fromList(<TypedData>[entry.value]),
        },
      },
      OrtFileModelSource() => <String, Object?>{
        'kind': 'file',
        'modelId': model.modelId,
        'absolutePath': model.absolutePath,
        'allowedRoot': model.allowedRoot,
      },
    };

Map<String, Object?> _encodeSessionOptions(OrtSessionOptions options) =>
    <String, Object?>{
      'graphOptimization': options.graphOptimization.name,
      'executionMode': options.executionMode.name,
      'intraOpThreads': options.intraOpThreads,
      'interOpThreads': options.interOpThreads,
      'enableCpuMemoryArena': options.enableCpuMemoryArena,
      'enableMemoryPattern': options.enableMemoryPattern,
      'deterministicCompute': options.deterministicCompute,
      'enableProfiling': options.enableProfiling,
      'profilePathPrefix': options.profilePathPrefix,
      'optimizedModelPath': options.optimizedModelPath,
      'artifactRoot': options.artifactRoot,
      'optimizedModelOverwrite': options.optimizedModelOverwrite.name,
      'logSeverity': options.logSeverity.index,
      'logVerbosity': options.logVerbosity,
      'sessionLogId': options.sessionLogId,
      'providers': <Object?>[
        for (final OrtExecutionProvider provider in options.providers)
          <String, Object?>{
            'id': provider.id,
            'options': provider.options,
            'requirement': provider.requirement.name,
            'coreMlCache': switch (provider.coreMlCache) {
              null => null,
              final OrtCoreMlCacheConfiguration cache => <String, Object?>{
                'rootDirectory': cache.rootDirectory,
                'modelSha256': cache.modelSha256,
                'applicationSchema': cache.applicationSchema,
              },
            },
          },
      ],
      'fallbackPolicy': options.fallbackPolicy.name,
      'configEntries': options.configEntries,
      'limits': _encodeResourceLimits(options.limits),
    };

Map<String, Object?> _encodeResourceLimits(OrtResourceLimits limits) =>
    <String, Object?>{
      'maxModelBytes': limits.maxModelBytes,
      'maxTensorBytes': limits.maxTensorBytes,
      'maxTensorElements': limits.maxTensorElements,
      'maxRank': limits.maxRank,
      'maxDimension': limits.maxDimension,
      'maxProviders': limits.maxProviders,
      'maxProviderOptions': limits.maxProviderOptions,
      'maxConfigEntries': limits.maxConfigEntries,
      'maxDiagnosticsBytes': limits.maxDiagnosticsBytes,
      'maxTypeDepth': limits.maxTypeDepth,
      'maxTypeNodes': limits.maxTypeNodes,
    };

OrtRuntimeSource _decodeRuntimeSource(Object? raw) {
  final Map<Object?, Object?> source = _workerMap(raw);
  final String kind = _workerString(source, 'kind');
  switch (kind) {
    case 'linked':
      _requireWorkerKeys(source, required: const <String>{'kind'});
      return const OrtRuntimeSource.linked();
    case 'bundled':
      _requireWorkerKeys(source, required: const <String>{'kind'});
      return const OrtRuntimeSource.bundled();
    case 'process':
      _requireWorkerKeys(
        source,
        required: const <String>{'kind'},
        optional: const <String>{'preferredLibraryNames'},
      );
      return OrtRuntimeSource.process(
        preferredLibraryNames: _workerStringList(
          source['preferredLibraryNames'] ?? const <String>[],
          maximum: 8,
        ),
      );
    case 'file':
      _requireWorkerKeys(
        source,
        required: const <String>{'kind', 'libraryPath'},
        optional: const <String>{'allowedRoot'},
      );
      return OrtRuntimeSource.file(
        absolutePath: _workerString(source, 'libraryPath'),
        allowedRoot: _workerNullableString(source, 'allowedRoot'),
      );
    default:
      throw const FormatException('Unknown runtime source kind.');
  }
}

OrtModelSource _decodeModelSource(Object? raw, OrtResourceLimits limits) {
  final Map<Object?, Object?> model = _workerMap(raw);
  final String kind = _workerString(model, 'kind');
  if (kind == 'bytes') {
    _requireWorkerKeys(
      model,
      required: const <String>{'kind', 'modelId', 'bytes', 'externalData'},
    );
  } else if (kind == 'file') {
    _requireWorkerKeys(
      model,
      required: const <String>{
        'kind',
        'modelId',
        'absolutePath',
        'allowedRoot',
      },
    );
  } else {
    throw const FormatException('Unknown model source kind.');
  }
  final String? modelId = _workerNullableString(model, 'modelId');
  return switch (kind) {
    'bytes' => OrtModelSource.bytes(
      _materializeWorkerBytes(model['bytes'], maximum: limits.maxModelBytes),
      externalData: _decodeExternalData(
        model['externalData'],
        maximumTotalBytes: limits.maxModelBytes,
      ),
      modelId: modelId,
      limits: limits,
    ),
    'file' => OrtModelSource.file(
      absolutePath: _workerString(model, 'absolutePath'),
      allowedRoot: _workerString(model, 'allowedRoot'),
      modelId: modelId,
    ),
    _ => throw const FormatException('Unknown model source kind.'),
  };
}

Map<String, Uint8List> _decodeExternalData(
  Object? raw, {
  required int maximumTotalBytes,
}) {
  final Map<Object?, Object?> encoded = _workerMap(raw);
  if (encoded.length > _maxExternalDataFiles) {
    throw const FormatException('Too many external-data entries.');
  }
  final Map<String, Uint8List> result = <String, Uint8List>{};
  var totalBytes = 0;
  for (final MapEntry<Object?, Object?> entry in encoded.entries) {
    if (entry.key is! String) {
      throw const FormatException('External-data name is not a string.');
    }
    final Uint8List bytes = _materializeWorkerBytes(
      entry.value,
      maximum: maximumTotalBytes - totalBytes,
    );
    totalBytes += bytes.length;
    result[entry.key! as String] = bytes;
  }
  return result;
}

OrtSessionOptions _decodeSessionOptions(Object? raw) {
  final Map<Object?, Object?> options = _workerMap(raw);
  _requireWorkerKeys(
    options,
    required: const <String>{
      'graphOptimization',
      'executionMode',
      'intraOpThreads',
      'interOpThreads',
      'enableCpuMemoryArena',
      'enableMemoryPattern',
      'deterministicCompute',
      'enableProfiling',
      'profilePathPrefix',
      'optimizedModelPath',
      'artifactRoot',
      'optimizedModelOverwrite',
      'logSeverity',
      'logVerbosity',
      'sessionLogId',
      'providers',
      'fallbackPolicy',
      'configEntries',
      'limits',
    },
  );
  final OrtResourceLimits limits = _decodeResourceLimits(options['limits']);
  final Object? rawProviders = options['providers'];
  if (rawProviders is! List<Object?> ||
      rawProviders.length > limits.maxProviders) {
    throw const FormatException('Invalid worker provider list.');
  }
  final List<OrtExecutionProvider> providers = <OrtExecutionProvider>[];
  for (final Object? rawProvider in rawProviders) {
    final Map<Object?, Object?> provider = _workerMap(rawProvider);
    _requireWorkerKeys(
      provider,
      required: const <String>{'id', 'options', 'requirement', 'coreMlCache'},
    );
    providers.add(_decodeWorkerProvider(provider, limits));
  }
  return OrtSessionOptions(
    graphOptimization: _workerEnum(
      OrtGraphOptimization.values,
      options['graphOptimization'],
      'graph optimization',
    ),
    executionMode: _workerEnum(
      OrtExecutionMode.values,
      options['executionMode'],
      'execution mode',
    ),
    intraOpThreads: _workerInt(options, 'intraOpThreads'),
    interOpThreads: _workerInt(options, 'interOpThreads'),
    enableCpuMemoryArena: _workerBool(options, 'enableCpuMemoryArena'),
    enableMemoryPattern: _workerBool(options, 'enableMemoryPattern'),
    deterministicCompute: _workerBool(options, 'deterministicCompute'),
    enableProfiling: _workerBool(options, 'enableProfiling'),
    profilePathPrefix: _workerNullableString(options, 'profilePathPrefix'),
    optimizedModelPath: _workerNullableString(options, 'optimizedModelPath'),
    artifactRoot: _workerNullableString(options, 'artifactRoot'),
    optimizedModelOverwrite: _workerEnum(
      OrtOverwritePolicy.values,
      options['optimizedModelOverwrite'],
      'optimized model overwrite',
    ),
    logSeverity:
        OrtLogSeverity.values[_workerBoundedInt(
          options,
          'logSeverity',
          0,
          OrtLogSeverity.values.length - 1,
        )],
    logVerbosity: _workerInt(options, 'logVerbosity'),
    sessionLogId: _workerString(options, 'sessionLogId'),
    providers: providers,
    fallbackPolicy: _workerEnum(
      OrtFallbackPolicy.values,
      options['fallbackPolicy'],
      'fallback policy',
    ),
    configEntries: _workerStringMap(
      options['configEntries'],
      maximum: limits.maxConfigEntries,
    ),
    limits: limits,
  );
}

OrtExecutionProvider _decodeWorkerProvider(
  Map<Object?, Object?> provider,
  OrtResourceLimits limits,
) {
  final String id = _workerString(provider, 'id');
  final Map<String, String> providerOptions = _workerStringMap(
    provider['options'],
    maximum: limits.maxProviderOptions,
  );
  final OrtProviderRequirement requirement = _workerEnum(
    OrtProviderRequirement.values,
    provider['requirement'],
    'provider requirement',
  );
  final Object? rawCache = provider['coreMlCache'];
  if (rawCache == null) {
    return OrtExecutionProvider.named(
      id,
      options: providerOptions,
      requirement: requirement,
      limits: limits,
    );
  }
  if (id != 'coreml') {
    throw const FormatException(
      'Only a Core ML worker provider may carry cache configuration.',
    );
  }
  final Map<Object?, Object?> cache = _workerMap(rawCache);
  _requireWorkerKeys(
    cache,
    required: const <String>{
      'rootDirectory',
      'modelSha256',
      'applicationSchema',
    },
  );
  const Set<String> expectedOptionKeys = <String>{
    'ModelFormat',
    'MLComputeUnits',
    'RequireStaticInputShapes',
    'EnableOnSubgraphs',
  };
  if (providerOptions.length != expectedOptionKeys.length ||
      !providerOptions.keys.toSet().containsAll(expectedOptionKeys)) {
    throw const FormatException(
      'Core ML worker options do not match the typed cache protocol.',
    );
  }
  final OrtCoreMlModelFormat modelFormat =
      switch (providerOptions['ModelFormat']) {
        'NeuralNetwork' => OrtCoreMlModelFormat.neuralNetwork,
        'MLProgram' => OrtCoreMlModelFormat.mlProgram,
        _ => throw const FormatException('Unknown Core ML model format.'),
      };
  final OrtCoreMlComputeUnits computeUnits =
      switch (providerOptions['MLComputeUnits']) {
        'ALL' => OrtCoreMlComputeUnits.all,
        'CPUOnly' => OrtCoreMlComputeUnits.cpuOnly,
        'CPUAndGPU' => OrtCoreMlComputeUnits.cpuAndGpu,
        'CPUAndNeuralEngine' => OrtCoreMlComputeUnits.cpuAndNeuralEngine,
        _ => throw const FormatException(
          'Unknown Core ML compute-unit policy.',
        ),
      };
  bool flag(String key) => switch (providerOptions[key]) {
    '0' => false,
    '1' => true,
    _ => throw FormatException('Core ML option $key is not Boolean.'),
  };

  return OrtExecutionProvider.coreMl(
    modelFormat: modelFormat,
    computeUnits: computeUnits,
    requireStaticInputShapes: flag('RequireStaticInputShapes'),
    enableOnSubgraphs: flag('EnableOnSubgraphs'),
    cache: OrtCoreMlCacheConfiguration(
      rootDirectory: _workerString(cache, 'rootDirectory'),
      modelSha256: _workerString(cache, 'modelSha256'),
      applicationSchema: _workerString(cache, 'applicationSchema'),
    ),
    requirement: requirement,
  );
}

OrtResourceLimits _decodeResourceLimits(Object? raw) {
  final Map<Object?, Object?> limits = _workerMap(raw);
  _requireWorkerKeys(
    limits,
    required: const <String>{
      'maxModelBytes',
      'maxTensorBytes',
      'maxTensorElements',
      'maxRank',
      'maxDimension',
      'maxProviders',
      'maxProviderOptions',
      'maxConfigEntries',
      'maxDiagnosticsBytes',
      'maxTypeDepth',
      'maxTypeNodes',
    },
  );
  return OrtResourceLimits(
    maxModelBytes: _workerInt(limits, 'maxModelBytes'),
    maxTensorBytes: _workerInt(limits, 'maxTensorBytes'),
    maxTensorElements: _workerInt(limits, 'maxTensorElements'),
    maxRank: _workerInt(limits, 'maxRank'),
    maxDimension: _workerInt(limits, 'maxDimension'),
    maxProviders: _workerInt(limits, 'maxProviders'),
    maxProviderOptions: _workerInt(limits, 'maxProviderOptions'),
    maxConfigEntries: _workerInt(limits, 'maxConfigEntries'),
    maxDiagnosticsBytes: _workerInt(limits, 'maxDiagnosticsBytes'),
    maxTypeDepth: _workerInt(limits, 'maxTypeDepth'),
    maxTypeNodes: _workerInt(limits, 'maxTypeNodes'),
  );
}

Map<Object?, Object?> _workerMap(Object? raw) {
  if (raw is! Map<Object?, Object?> || raw.length > 4096) {
    throw const FormatException('Worker protocol value is not a bounded map.');
  }
  return raw;
}

void _requireWorkerKeys(
  Map<Object?, Object?> map, {
  required Set<String> required,
  Set<String> optional = const <String>{},
}) {
  final Set<String> allowed = <String>{...required, ...optional};
  for (final Object? key in map.keys) {
    if (key is! String || !allowed.contains(key)) {
      throw const FormatException('Worker map contains an unknown field.');
    }
  }
  for (final String key in required) {
    if (!map.containsKey(key)) {
      throw FormatException('Worker map is missing required field $key.');
    }
  }
}

void _requireWorkerStartupReplyKeys(
  Map<Object?, Object?> message,
  String type,
) {
  switch (type) {
    case 'ownership':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'commandPort'},
      );
    case 'ready':
      _requireWorkerKeys(
        message,
        required: const <String>{
          'version',
          'type',
          'commandPort',
          'inputNames',
          'outputNames',
          'diagnostics',
        },
      );
    case 'startupError':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'message'},
      );
    default:
      throw const FormatException('Unknown worker startup reply type.');
  }
}

void _requireWorkerReplyKeys(Map<Object?, Object?> message, String type) {
  switch (type) {
    case 'closed':
      _requireWorkerKeys(message, required: const <String>{'version', 'type'});
    case 'fatalProtocol':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'message'},
      );
    case 'started':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'requestId', 'cancelToken'},
      );
    case 'result':
      _requireWorkerKeys(
        message,
        required: const <String>{
          'version',
          'type',
          'requestId',
          'outputs',
          'providerEvidence',
          'providerDiagnostics',
          'diagnostics',
          'wasTerminationRequested',
        },
      );
    case 'ortError':
      _requireWorkerKeys(
        message,
        required: const <String>{
          'version',
          'type',
          'requestId',
          'error',
          'wasTerminationRequested',
        },
      );
    case 'workerError':
    case 'fatalWorkerError':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'requestId', 'message'},
      );
    default:
      throw const FormatException('Unknown worker reply type.');
  }
}

void _requireWorkerCommandKeys(Map<Object?, Object?> command, String type) {
  switch (type) {
    case 'close':
    case 'retire':
      _requireWorkerKeys(command, required: const <String>{'version', 'type'});
    case 'run':
      _requireWorkerKeys(
        command,
        required: const <String>{
          'version',
          'type',
          'requestId',
          'inputs',
          'outputNames',
          'maxMessageBytes',
        },
      );
    default:
      throw const FormatException('Unknown worker command type.');
  }
}

void _requireWorkerVersion(Map<Object?, Object?> message) {
  if (message['version'] != _ortWorkerProtocolVersion) {
    throw const FormatException('Unsupported worker protocol version.');
  }
}

String _workerString(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value is! String ||
      value.isEmpty ||
      value.length > 4096 ||
      utf8.encode(value).length > 4096 ||
      value.contains('\u0000')) {
    throw FormatException('Worker field $key is not bounded text.');
  }
  return value;
}

String? _workerNullableString(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value == null) return null;
  return _workerString(<Object?, Object?>{key: value}, key);
}

int _workerInt(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value is! int) throw FormatException('Worker field $key is not an int.');
  return value;
}

int _workerPositiveInt(Map<Object?, Object?> map, String key) {
  final int value = _workerInt(map, key);
  if (value <= 0 || value > 0x7fffffffffffffff) {
    throw FormatException(
      'Worker field $key is outside the positive int64 range.',
    );
  }
  return value;
}

int _workerBoundedInt(
  Map<Object?, Object?> map,
  String key,
  int minimum,
  int maximum,
) {
  final int value = _workerInt(map, key);
  if (value < minimum || value > maximum) {
    throw FormatException('Worker field $key is outside its closed range.');
  }
  return value;
}

bool _workerBool(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value is! bool) throw FormatException('Worker field $key is not a bool.');
  return value;
}

T _workerEnum<T extends Enum>(List<T> values, Object? raw, String label) {
  if (raw is String) {
    for (final T value in values) {
      if (value.name == raw) return value;
    }
  }
  throw FormatException('Worker $label is outside its closed enum.');
}

List<String> _workerStringList(Object? raw, {required int maximum}) {
  if (raw is! List<Object?> || raw.length > maximum) {
    throw const FormatException('Worker value is not a bounded string list.');
  }
  final List<String> result = <String>[];
  final Set<String> seen = <String>{};
  for (final Object? value in raw) {
    if (value is! String ||
        value.isEmpty ||
        value.length > 1024 ||
        utf8.encode(value).length > 1024 ||
        value.contains('\u0000') ||
        !seen.add(value)) {
      throw const FormatException('Worker string list is invalid.');
    }
    result.add(value);
  }
  return List<String>.unmodifiable(result);
}

Map<String, String> _workerStringMap(Object? raw, {required int maximum}) {
  final Map<Object?, Object?> map = _workerMap(raw);
  if (map.length > maximum) {
    throw const FormatException('Worker string map exceeds its bound.');
  }
  final Map<String, String> result = <String, String>{};
  for (final MapEntry<Object?, Object?> entry in map.entries) {
    if (entry.key is! String || entry.value is! String) {
      throw const FormatException('Worker string map contains another type.');
    }
    result[entry.key! as String] = entry.value! as String;
  }
  return result;
}

Uint8List _materializeWorkerBytes(Object? raw, {required int maximum}) {
  if (maximum < 0 || raw is! TransferableTypedData) {
    throw const FormatException('Worker bytes are not transferable data.');
  }
  final ByteBuffer buffer = raw.materialize();
  if (buffer.lengthInBytes > maximum) {
    throw const FormatException('Worker bytes exceed their configured bound.');
  }
  return Uint8List.fromList(buffer.asUint8List());
}

String _boundedWorkerText(Object? raw, String fallback) {
  if (raw is! String || raw.isEmpty || raw.contains('\u0000')) return fallback;
  final List<int> bytes = utf8.encode(raw);
  if (bytes.length <= 1024) return raw;
  return utf8.decode(bytes.sublist(0, 1024), allowMalformed: true);
}

String _boundedWorkerCrash(Object? raw) {
  Object? error = raw;
  if (raw is List<Object?> && raw.isNotEmpty) error = raw.first;
  return _boundedWorkerText(error?.toString(), 'unreported worker error');
}

final class _ValidatedWorkerInputs {
  const _ValidatedWorkerInputs({required this.values, required this.bytes});

  final Map<String, OrtIsolateValue> values;
  final int bytes;
}

_ValidatedWorkerInputs _validateWorkerRunInputs({
  required Map<String, OrtIsolateValue> supplied,
  required List<String> knownNames,
  required int maxMessageBytes,
}) {
  if (supplied.length > knownNames.length) {
    throw ArgumentError('Too many isolate-session inputs were supplied.');
  }
  final Set<String> known = knownNames.toSet();
  final _WorkerMessageBudget budget = _WorkerMessageBudget(
    maxBytes: maxMessageBytes,
  );
  final Map<String, OrtIsolateValue> copied = <String, OrtIsolateValue>{};
  for (final MapEntry<String, OrtIsolateValue> entry in supplied.entries) {
    if (!known.contains(entry.key) || copied.containsKey(entry.key)) {
      throw ArgumentError('An isolate-session input name is unknown.');
    }
    budget.addUtf8(entry.key);
    _measureIsolateValue(entry.value, budget: budget, depth: 0);
    copied[entry.key] = entry.value;
  }
  return _ValidatedWorkerInputs(
    values: Map<String, OrtIsolateValue>.unmodifiable(copied),
    bytes: budget.bytes,
  );
}

List<String> _validateWorkerOutputNames({
  required List<String>? supplied,
  required List<String> knownNames,
}) {
  if (supplied != null &&
      (supplied.isEmpty || supplied.length > knownNames.length)) {
    throw ArgumentError('At least one known worker output is required.');
  }
  final List<String> selected = supplied == null
      ? List<String>.of(knownNames)
      : List<String>.of(supplied);
  if (selected.isEmpty || selected.length > knownNames.length) {
    throw ArgumentError('At least one known worker output is required.');
  }
  final Set<String> known = knownNames.toSet();
  final Set<String> seen = <String>{};
  for (final String name in selected) {
    if (!known.contains(name) || !seen.add(name)) {
      throw ArgumentError('Worker output names must be known and unique.');
    }
  }
  return List<String>.unmodifiable(selected);
}

final class _WorkerMessageBudget {
  _WorkerMessageBudget({required this.maxBytes});

  final int maxBytes;
  int bytes = 0;
  int nodes = 0;

  int get remainingBytes => maxBytes - bytes;

  void addNode(int depth) {
    if (depth > 8 || ++nodes > 4096) {
      throw OrtWorkerMessageTooLargeException(
        message: 'The worker value tree exceeds its depth/node bound.',
        context: <String, Object?>{'maxDepth': 8, 'maxNodes': 4096},
      );
    }
  }

  void addBytes(int count) {
    if (count < 0 || count > maxBytes - bytes) {
      throw OrtWorkerMessageTooLargeException(
        message: 'The worker message exceeds its configured byte bound.',
        context: <String, Object?>{'maxMessageBytes': maxBytes},
      );
    }
    bytes += count;
  }

  void addUtf8(String value) => addBytes(utf8.encode(value).length);
}

void _measureIsolateValue(
  OrtIsolateValue value, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  switch (value) {
    case OrtIsolateTensor():
      budget.addBytes(value.shape.rank * 8);
      if (value.isString) {
        for (final String string in value.copyStrings()) {
          budget.addUtf8(string);
        }
      } else {
        budget.addBytes(value.byteLength);
      }
    case OrtIsolateSequence():
      for (final OrtIsolateValue child in value.elements) {
        _measureIsolateValue(child, budget: budget, depth: depth + 1);
      }
    case OrtIsolateMap():
      _measureIsolateValue(value.keys, budget: budget, depth: depth + 1);
      _measureIsolateValue(value.values, budget: budget, depth: depth + 1);
    case OrtIsolateOptional():
      _measureWorkerType(value.elementType, budget: budget, depth: depth + 1);
      if (value.value case final OrtIsolateValue child) {
        _measureIsolateValue(child, budget: budget, depth: depth + 1);
      }
  }
}

Map<String, Object?> _encodeIsolateValue(
  OrtIsolateValue value, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  return switch (value) {
    OrtIsolateTensor() when value.isString => <String, Object?>{
      'kind': 'tensor',
      'elementType': value.elementType.nativeValue,
      'shape': _workerShapeForTransfer(value, budget),
      'strings': _workerStringsForTransfer(value, budget),
    },
    OrtIsolateTensor() => <String, Object?>{
      'kind': 'tensor',
      'elementType': value.elementType.nativeValue,
      'shape': _workerShapeForTransfer(value, budget),
      'data': _transferWorkerTensor(value, budget),
    },
    OrtIsolateSequence() => <String, Object?>{
      'kind': 'sequence',
      'elements': <Object?>[
        for (final OrtIsolateValue child in value.elements)
          _encodeIsolateValue(child, budget: budget, depth: depth + 1),
      ],
    },
    OrtIsolateMap() => <String, Object?>{
      'kind': 'map',
      'keys': _encodeIsolateValue(value.keys, budget: budget, depth: depth + 1),
      'values': _encodeIsolateValue(
        value.values,
        budget: budget,
        depth: depth + 1,
      ),
    },
    OrtIsolateOptional() => <String, Object?>{
      'kind': 'optional',
      'elementType': _encodeWorkerType(
        value.elementType,
        budget: budget,
        depth: depth + 1,
      ),
      'value': value.value == null
          ? null
          : _encodeIsolateValue(value.value!, budget: budget, depth: depth + 1),
    },
  };
}

List<int> _workerShapeForTransfer(
  OrtIsolateTensor value,
  _WorkerMessageBudget budget,
) {
  budget.addBytes(value.shape.rank * 8);
  return value.shape.dimensions;
}

List<String> _workerStringsForTransfer(
  OrtIsolateTensor value,
  _WorkerMessageBudget budget,
) {
  final List<String> strings = value.copyStrings();
  for (final String string in strings) {
    budget.addUtf8(string);
  }
  return strings;
}

TransferableTypedData _transferWorkerTensor(
  OrtIsolateTensor value,
  _WorkerMessageBudget budget,
) {
  final Uint8List bytes = value.copyBytes();
  budget.addBytes(bytes.length);
  return TransferableTypedData.fromList(<TypedData>[bytes]);
}

void _measureWorkerType(
  OrtTypeInfo type, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  switch (type.kind) {
    case OrtValueKind.tensor:
      budget.addBytes(type.dimensions.length * 8);
      for (final OrtDimension dimension in type.dimensions) {
        if (dimension.symbol case final String symbol) budget.addUtf8(symbol);
      }
    case OrtValueKind.sequence:
      _measureWorkerType(
        type.sequenceElement!,
        budget: budget,
        depth: depth + 1,
      );
    case OrtValueKind.map:
      _measureWorkerType(type.mapValueType!, budget: budget, depth: depth + 1);
    case OrtValueKind.optional:
      _measureWorkerType(
        type.optionalElement!,
        budget: budget,
        depth: depth + 1,
      );
    case OrtValueKind.unknown:
    case OrtValueKind.opaque:
    case OrtValueKind.sparseTensor:
      throw UnsupportedError('Unsupported isolate type ${type.kind.name}.');
  }
}

Map<String, Object?> _encodeWorkerType(
  OrtTypeInfo type, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  return switch (type.kind) {
    OrtValueKind.tensor => <String, Object?>{
      'kind': 'tensor',
      'elementType': type.tensorElementType!.nativeValue,
      'hasShape': type.hasShape,
      'dimensions': <Object?>[
        for (final OrtDimension dimension in type.dimensions)
          _encodeWorkerDimension(dimension, budget),
      ],
    },
    OrtValueKind.sequence => <String, Object?>{
      'kind': 'sequence',
      'element': _encodeWorkerType(
        type.sequenceElement!,
        budget: budget,
        depth: depth + 1,
      ),
    },
    OrtValueKind.map => <String, Object?>{
      'kind': 'map',
      'keyElementType': type.mapKeyType!.nativeValue,
      'value': _encodeWorkerType(
        type.mapValueType!,
        budget: budget,
        depth: depth + 1,
      ),
    },
    OrtValueKind.optional => <String, Object?>{
      'kind': 'optional',
      'element': _encodeWorkerType(
        type.optionalElement!,
        budget: budget,
        depth: depth + 1,
      ),
    },
    _ => throw UnsupportedError('Unsupported isolate type ${type.kind.name}.'),
  };
}

Map<String, Object?> _encodeWorkerDimension(
  OrtDimension dimension,
  _WorkerMessageBudget budget,
) {
  budget.addBytes(8);
  if (dimension.symbol case final String symbol) budget.addUtf8(symbol);
  return <String, Object?>{
    'value': dimension.value,
    'symbol': dimension.symbol,
  };
}

OrtTypeInfo _decodeWorkerType(
  Object? raw, {
  required _WorkerMessageBudget budget,
  required int depth,
  required OrtResourceLimits limits,
}) {
  budget.addNode(depth);
  final Map<Object?, Object?> type = _workerMap(raw);
  final String kind = _workerString(type, 'kind');
  switch (kind) {
    case 'tensor':
      _requireWorkerKeys(
        type,
        required: const <String>{
          'kind',
          'elementType',
          'hasShape',
          'dimensions',
        },
      );
      final Object? rawDimensions = type['dimensions'];
      if (rawDimensions is! List<Object?> ||
          rawDimensions.length > limits.maxRank) {
        throw const FormatException('Worker type dimensions are invalid.');
      }
      return OrtTypeInfo.tensor(
        elementType: OrtTensorElementType.fromNativeValue(
          _workerInt(type, 'elementType'),
        ),
        hasShape: _workerBool(type, 'hasShape'),
        dimensions: <OrtDimension>[
          for (final Object? rawDimension in rawDimensions)
            _decodeWorkerDimension(rawDimension, budget),
        ],
        limits: limits,
      );
    case 'sequence':
      _requireWorkerKeys(type, required: const <String>{'kind', 'element'});
      return OrtTypeInfo.sequence(
        _decodeWorkerType(
          type['element'],
          budget: budget,
          depth: depth + 1,
          limits: limits,
        ),
        limits: limits,
      );
    case 'map':
      _requireWorkerKeys(
        type,
        required: const <String>{'kind', 'keyElementType', 'value'},
      );
      return OrtTypeInfo.map(
        keyElementType: OrtTensorElementType.fromNativeValue(
          _workerInt(type, 'keyElementType'),
        ),
        value: _decodeWorkerType(
          type['value'],
          budget: budget,
          depth: depth + 1,
          limits: limits,
        ),
        limits: limits,
      );
    case 'optional':
      _requireWorkerKeys(type, required: const <String>{'kind', 'element'});
      return OrtTypeInfo.optional(
        _decodeWorkerType(
          type['element'],
          budget: budget,
          depth: depth + 1,
          limits: limits,
        ),
        limits: limits,
      );
    default:
      throw const FormatException('Unknown worker type kind.');
  }
}

OrtDimension _decodeWorkerDimension(Object? raw, _WorkerMessageBudget budget) {
  final Map<Object?, Object?> dimension = _workerMap(raw);
  _requireWorkerKeys(dimension, required: const <String>{'value', 'symbol'});
  budget.addBytes(8);
  final Object? rawValue = dimension['value'];
  final Object? rawSymbol = dimension['symbol'];
  if (rawValue is int && rawSymbol == null) {
    return OrtDimension.fixed(rawValue);
  }
  if (rawValue == null && (rawSymbol == null || rawSymbol is String)) {
    if (rawSymbol is String) budget.addUtf8(rawSymbol);
    return OrtDimension.dynamic(rawSymbol as String?);
  }
  throw const FormatException('Worker dimension is inconsistent.');
}

OrtIsolateValue _decodeIsolateValue(
  Object? raw, {
  required _WorkerMessageBudget budget,
  required int depth,
  required OrtResourceLimits limits,
}) {
  budget.addNode(depth);
  final Map<Object?, Object?> value = _workerMap(raw);
  final String kind = _workerString(value, 'kind');
  switch (kind) {
    case 'tensor':
      final OrtTensorElementType elementType =
          OrtTensorElementType.fromNativeValue(
            _workerInt(value, 'elementType'),
          );
      final List<int> shape = _workerIntList(
        value['shape'],
        maximum: limits.maxRank,
      );
      budget.addBytes(shape.length * 8);
      if (elementType == OrtTensorElementType.string) {
        _requireWorkerKeys(
          value,
          required: const <String>{'kind', 'elementType', 'shape', 'strings'},
        );
        final List<String> strings = _workerStringListAllowEmpty(
          value['strings'],
          maximum: limits.maxTensorElements,
          budget: budget,
        );
        return OrtIsolateTensor.fromStrings(
          values: strings,
          shape: shape,
          limits: limits,
        );
      }
      _requireWorkerKeys(
        value,
        required: const <String>{'kind', 'elementType', 'shape', 'data'},
      );
      final Uint8List bytes = _materializeWorkerBytes(
        value['data'],
        maximum: budget.maxBytes - budget.bytes,
      );
      budget.addBytes(bytes.length);
      return OrtIsolateTensor.fromBytes(
        elementType: elementType,
        bytes: bytes,
        shape: shape,
        limits: limits,
      );
    case 'sequence':
      _requireWorkerKeys(value, required: const <String>{'kind', 'elements'});
      final Object? rawElements = value['elements'];
      if (rawElements is! List<Object?> ||
          rawElements.isEmpty ||
          rawElements.length > _maximumWorkerCompositeChildren) {
        throw const FormatException('Invalid worker sequence payload.');
      }
      return OrtIsolateSequence(<OrtIsolateValue>[
        for (final Object? child in rawElements)
          _decodeIsolateValue(
            child,
            budget: budget,
            depth: depth + 1,
            limits: limits,
          ),
      ]);
    case 'map':
      _requireWorkerKeys(
        value,
        required: const <String>{'kind', 'keys', 'values'},
      );
      final OrtIsolateValue keys = _decodeIsolateValue(
        value['keys'],
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      final OrtIsolateValue values = _decodeIsolateValue(
        value['values'],
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      if (keys is! OrtIsolateTensor || values is! OrtIsolateTensor) {
        throw const FormatException('Invalid worker map payload.');
      }
      return OrtIsolateMap(keys: keys, values: values);
    case 'optional':
      _requireWorkerKeys(
        value,
        required: const <String>{'kind', 'elementType', 'value'},
      );
      final OrtTypeInfo elementType = _decodeWorkerType(
        value['elementType'],
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      final Object? contained = value['value'];
      if (contained == null) {
        return OrtIsolateOptional.none(elementType: elementType);
      }
      final OrtIsolateValue decoded = _decodeIsolateValue(
        contained,
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      if (decoded.type != elementType) {
        throw const FormatException('Optional value type does not match.');
      }
      return OrtIsolateOptional.some(decoded);
    default:
      throw const FormatException('Unknown worker value kind.');
  }
}

List<int> _workerIntList(Object? raw, {required int maximum}) {
  if (raw is! List<Object?> || raw.length > maximum) {
    throw const FormatException('Worker value is not a bounded int list.');
  }
  return List<int>.unmodifiable(<int>[
    for (final Object? value in raw)
      value is int
          ? value
          : throw const FormatException('Worker int list is invalid.'),
  ]);
}

List<String> _workerStringListAllowEmpty(
  Object? raw, {
  required int maximum,
  required _WorkerMessageBudget budget,
}) {
  if (raw is! List<Object?> || raw.length > maximum) {
    throw const FormatException('Worker string tensor list is invalid.');
  }
  final List<String> result = <String>[];
  for (final Object? value in raw) {
    if (value is! String ||
        value.contains('\u0000') ||
        !_hasWellFormedUtf16(value)) {
      throw const FormatException('Worker string tensor text is invalid.');
    }
    budget.addUtf8(value);
    result.add(value);
  }
  return List<String>.unmodifiable(result);
}

OrtIsolateRunResult _decodeWorkerResult(
  Object? raw, {
  required Object? rawProviderEvidence,
  required Object? rawProviderDiagnostics,
  required Object? rawDiagnostics,
  required OrtDiagnostics expectedSessionDiagnostics,
  required List<String> expectedNames,
  required int maxMessageBytes,
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
      );
  final OrtDiagnostics fullDiagnostics = _decodeWorkerFullDiagnostics(
    rawDiagnostics,
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

OrtDiagnostics _decodeWorkerFullDiagnostics(Object? raw) {
  final Map<Object?, Object?> object = _workerMap(raw);
  try {
    return OrtDiagnostics.fromJsonString(
      jsonEncode(object),
      limits: OrtResourceLimits.defaults,
    );
  } on FormatException {
    rethrow;
  } on Object catch (error) {
    throw FormatException('Worker diagnostics are invalid.', error);
  }
}

OrtProviderRunEvidence? _decodeWorkerProviderEvidence(
  Object? raw,
  _WorkerMessageBudget budget,
) {
  if (raw == null) return null;
  final Map<Object?, Object?> object = _workerMap(raw);
  _requireWorkerKeys(
    object,
    required: const <String>{
      'schemaVersion',
      'nodeExecutionCount',
      'nodeExecutionsByProvider',
    },
  );
  if (_workerInt(object, 'schemaVersion') != 1) {
    throw const FormatException('Worker provider evidence schema is invalid.');
  }
  final int nodeExecutionCount = _workerPositiveInt(
    object,
    'nodeExecutionCount',
  );
  budget.addBytes(16);
  final Map<Object?, Object?> rawCounts = _workerMap(
    object['nodeExecutionsByProvider'],
  );
  if (rawCounts.isEmpty || rawCounts.length > 16) {
    throw const FormatException('Worker provider evidence is out of bounds.');
  }
  final Map<String, int> counts = <String, int>{};
  for (final MapEntry<Object?, Object?> entry in rawCounts.entries) {
    if (entry.key is! String || entry.value is! int) {
      throw const FormatException('Worker provider evidence is malformed.');
    }
    final String id = entry.key! as String;
    budget
      ..addUtf8(id)
      ..addBytes(8);
    counts[id] = entry.value! as int;
  }
  return providerRunEvidenceFromCountsInternal(
    nodeExecutionCount: nodeExecutionCount,
    nodeExecutionsByProvider: counts,
  );
}

List<OrtProviderDiagnostics> _decodeWorkerProviderDiagnostics(
  Object? raw, {
  required OrtProviderRunEvidence? evidence,
  required _WorkerMessageBudget budget,
}) {
  if (raw is! List<Object?> || raw.length > 16) {
    throw const FormatException('Worker provider diagnostics are invalid.');
  }
  final Set<String> seen = <String>{};
  final List<OrtProviderDiagnostics> diagnostics = <OrtProviderDiagnostics>[];
  for (final Object? rawDiagnostic in raw) {
    final Map<Object?, Object?> object = _workerMap(rawDiagnostic);
    _requireWorkerKeys(
      object,
      required: const <String>{
        'wrapperId',
        'registrationMechanism',
        'registrationName',
        'reportedName',
        'compiled',
        'discoverable',
        'registered',
        'active',
        'qualified',
        'assignmentEvidence',
        'fallbackReason',
        'options',
      },
    );
    final String wrapperId = _workerString(object, 'wrapperId');
    final String registration = _workerString(object, 'registrationMechanism');
    if (!RegExp(r'^[a-z][a-z0-9_-]{0,63}$').hasMatch(wrapperId) ||
        !seen.add(wrapperId)) {
      throw const FormatException(
        'Worker provider diagnostics contain an invalid ID.',
      );
    }
    budget
      ..addUtf8(wrapperId)
      ..addUtf8(registration);
    final bool? active = _workerNullableBool(object['active'], 'active');
    if ((evidence == null && active != null) ||
        (evidence != null && active != evidence.isActive(wrapperId))) {
      throw const FormatException(
        'Worker provider diagnostics conflict with assignment evidence.',
      );
    }
    final Map<Object?, Object?> rawOptions = _workerMap(object['options']);
    if (rawOptions.length > OrtResourceLimits.defaults.maxProviderOptions) {
      throw const FormatException('Worker provider options are out of bounds.');
    }
    final Map<String, String> options = <String, String>{};
    for (final MapEntry<Object?, Object?> entry in rawOptions.entries) {
      if (entry.key is! String || entry.value is! String) {
        throw const FormatException('Worker provider options are malformed.');
      }
      final String key = entry.key! as String;
      final String value = entry.value! as String;
      budget
        ..addUtf8(key)
        ..addUtf8(value);
      options[key] = value;
    }
    final String? registrationName = _workerNullableText(
      object['registrationName'],
      'registrationName',
      budget,
    );
    final String? reportedName = _workerNullableText(
      object['reportedName'],
      'reportedName',
      budget,
    );
    final String? assignmentEvidence = _workerNullableText(
      object['assignmentEvidence'],
      'assignmentEvidence',
      budget,
    );
    final String? fallbackReason = _workerNullableText(
      object['fallbackReason'],
      'fallbackReason',
      budget,
    );
    diagnostics.add(
      OrtProviderDiagnostics(
        wrapperId: wrapperId,
        registrationMechanism: switch (registration) {
          'implicit' => OrtProviderRegistrationMechanism.implicit,
          'generic' => OrtProviderRegistrationMechanism.generic,
          'provider-specific' =>
            OrtProviderRegistrationMechanism.providerSpecific,
          'plugin' => OrtProviderRegistrationMechanism.plugin,
          'unknown' => OrtProviderRegistrationMechanism.unknown,
          _ => throw const FormatException(
            'Worker provider registration mechanism is invalid.',
          ),
        },
        registrationName: registrationName,
        reportedName: reportedName,
        compiled: _workerNullableBool(object['compiled'], 'compiled'),
        discoverable: _workerNullableBool(
          object['discoverable'],
          'discoverable',
        ),
        registered: _workerNullableBool(object['registered'], 'registered'),
        active: active,
        qualified: _workerNullableBool(object['qualified'], 'qualified'),
        options: options,
        assignmentEvidence: assignmentEvidence,
        fallbackReason: fallbackReason,
      ),
    );
  }
  return List<OrtProviderDiagnostics>.unmodifiable(diagnostics);
}

bool? _workerNullableBool(Object? raw, String field) {
  if (raw == null || raw is bool) return raw as bool?;
  throw FormatException('Worker $field must be a boolean or null.');
}

String? _workerNullableText(
  Object? raw,
  String field,
  _WorkerMessageBudget budget,
) {
  if (raw == null) return null;
  if (raw is! String ||
      raw.length > 4096 ||
      raw.contains('\u0000') ||
      !_hasWellFormedUtf16(raw)) {
    throw FormatException('Worker $field is invalid.');
  }
  budget.addUtf8(raw);
  return raw;
}

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
      return _createCopiedTensor(
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
  required _WorkerMessageBudget budget,
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
      _measureWorkerType(
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
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  switch (value) {
    case OrtTensor():
      budget.addBytes(value.shape.rank * 8);
      budget.addBytes(value.info.byteLength);
      final Uint8List bytes = value._copyTypedBytes(value.elementType);
      return <String, Object?>{
        'kind': 'tensor',
        'elementType': value.elementType.nativeValue,
        'shape': value.shape.dimensions,
        'data': TransferableTypedData.fromList(<TypedData>[bytes]),
      };
    case OrtStringTensor():
      budget.addBytes(value.shape.rank * 8);
      budget.addBytes(value.info.byteLength);
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
        'elementType': _encodeWorkerType(
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

Map<String, Object?> _encodeWorkerOrtError(OrtException error) =>
    <String, Object?>{
      'operation': _boundedWorkerText(error.operation, 'worker_run'),
      'domain': error.domain.name,
      'code': error.code,
      'ortCode': error.ortCode,
      'message': _boundedWorkerText(error.message, 'ONNX Runtime run failed.'),
      'context': _messageSafeWorkerContext(error.context),
    };

OrtException _decodeWorkerOrtError(Object? raw) {
  final Map<Object?, Object?> encoded = _workerMap(raw);
  _requireWorkerKeys(
    encoded,
    required: const <String>{
      'operation',
      'domain',
      'code',
      'ortCode',
      'message',
      'context',
    },
  );
  final String operation = _workerString(encoded, 'operation');
  final OrtErrorDomain domain = _workerEnum(
    OrtErrorDomain.values,
    encoded['domain'],
    'error domain',
  );
  final int code = _workerInt(encoded, 'code');
  final Object? rawOrtCode = encoded['ortCode'];
  if (rawOrtCode != null && rawOrtCode is! int) {
    throw const FormatException('Worker ORT code is invalid.');
  }
  final String message = _workerString(encoded, 'message');
  final Map<String, Object?> context = _decodeWorkerContext(encoded['context']);
  return switch (domain) {
    OrtErrorDomain.loader => OrtRuntimeNotFoundException(
      operation: operation,
      code: code,
      message: message,
      context: context,
    ),
    OrtErrorDomain.ortApi => OrtApiIncompatibleException(
      operation: operation,
      code: code,
      message: message,
      context: context,
    ),
    OrtErrorDomain.provider => OrtProviderUnavailableException(
      operation: operation,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
    OrtErrorDomain.ortStatus => OrtRunException(
      operation: operation,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
    OrtErrorDomain.unsupported => OrtUnsupportedValueException(
      operation: operation,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
    OrtErrorDomain.packaging => OrtNativePackagingException(
      operation: operation,
      code: code,
      message: message,
      context: context,
    ),
    _ => OrtException(
      operation: operation,
      domain: domain,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
  };
}

Map<String, Object?> _messageSafeWorkerContext(Map<String, Object?> context) {
  final Map<String, Object?> result = <String, Object?>{};
  var count = 0;
  for (final MapEntry<String, Object?> entry in context.entries) {
    if (++count > 32) break;
    final Object? value = entry.value;
    if (value == null || value is bool || value is int || value is double) {
      result[entry.key] = value;
    } else if (value is String) {
      result[entry.key] = _boundedWorkerText(value, '<redacted>');
    }
  }
  return result;
}

Map<String, Object?> _decodeWorkerContext(Object? raw) {
  final Map<Object?, Object?> encoded = _workerMap(raw);
  if (encoded.length > 32) {
    throw const FormatException('Worker error context exceeds its bound.');
  }
  final Map<String, Object?> result = <String, Object?>{};
  for (final MapEntry<Object?, Object?> entry in encoded.entries) {
    final Object? value = entry.value;
    if (entry.key is! String ||
        !(value == null ||
            value is bool ||
            value is int ||
            value is double ||
            value is String)) {
      throw const FormatException('Worker error context is not serializable.');
    }
    result[entry.key! as String] = value;
  }
  return result;
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
  _requireWorkerKeys(
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
  final int maxMessageBytes = _workerBoundedInt(
    startup,
    'maxMessageBytes',
    1,
    _maximumWorkerMessageBytes,
  );
  final OrtSessionOptions options = _decodeSessionOptions(startup['options']);
  final OrtRuntimeSource runtimeSource = _decodeRuntimeSource(
    startup['runtimeSource'],
  );
  final int requiredApiValue = _workerInt(startup, 'requiredApi');
  if (requiredApiValue != OrtApiVersion.v27.value) {
    throw const FormatException('Unsupported worker ORT API version.');
  }
  final OrtLogSeverity logSeverity =
      OrtLogSeverity.values[_workerBoundedInt(
        startup,
        'logSeverity',
        0,
        OrtLogSeverity.values.length - 1,
      )];
  final String logId = _workerString(startup, 'logId');
  final OrtModelSource model = _decodeModelSource(
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

void _ortIsolateWorkerMain(Map<String, Object?> initialMessage) async {
  SendPort? responsePort;
  ReceivePort? commandPort;
  OrtRuntime? runtime;
  OrtSession? session;
  try {
    final Map<Object?, Object?> startup = _workerMap(initialMessage);
    _requireWorkerVersion(startup);
    if (_workerString(startup, 'type') != 'startup') {
      throw const FormatException('Expected the worker startup command.');
    }
    final Object? rawResponsePort = startup['responsePort'];
    if (rawResponsePort is! SendPort) {
      throw const FormatException('Worker response port is missing.');
    }
    responsePort = rawResponsePort;
    commandPort = ReceivePort();
    responsePort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
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
      'version': _ortWorkerProtocolVersion,
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
        final Map<Object?, Object?> command = _workerMap(rawCommand);
        _requireWorkerVersion(command);
        final String type = _workerString(command, 'type');
        _requireWorkerCommandKeys(command, type);
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
            'version': _ortWorkerProtocolVersion,
            'type': 'closed',
          });
          closeReceiptSent = true;
          continue;
        }
        if (type != 'run') {
          throw const FormatException('Unknown worker command type.');
        }
        final int requestId = _workerPositiveInt(command, 'requestId');
        if (requestId <= lastRequestId) {
          throw const FormatException('Worker request ID is stale.');
        }
        lastRequestId = requestId;
        final int commandMessageBytes = _workerBoundedInt(
          command,
          'maxMessageBytes',
          1,
          _maximumWorkerMessageBytes,
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
            'version': _ortWorkerProtocolVersion,
            'type': 'fatalProtocol',
            'message': _safeWorkerFailureText(error),
          });
          terminalReplySent = true;
        }
      }
    }
  } on Object catch (error) {
    responsePort?.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'startupError',
      'message': _safeWorkerFailureText(error),
    });
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
    final Map<Object?, Object?> encodedInputs = _workerMap(command['inputs']);
    if (encodedInputs.length > session.inputs.length) {
      throw const FormatException('Worker input count exceeds session inputs.');
    }
    final Set<String> knownInputNames = <String>{
      for (final OrtValueInfo input in session.inputs) input.name,
    };
    final _WorkerMessageBudget decodeBudget = _WorkerMessageBudget(
      maxBytes: maxMessageBytes,
    );
    final Map<String, OrtValue> inputs = <String, OrtValue>{};
    for (final MapEntry<Object?, Object?> entry in encodedInputs.entries) {
      if (entry.key is! String || !knownInputNames.contains(entry.key)) {
        throw const FormatException('Worker input name is invalid.');
      }
      final String name = entry.key! as String;
      decodeBudget.addUtf8(name);
      final OrtIsolateValue isolateValue = _decodeIsolateValue(
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
    final List<String> outputNames = _workerStringList(
      command['outputNames'],
      maximum: session.outputs.length,
    );

    runOptions = OrtRunOptions(runtime: runtime);
    cancelToken = runOptions._nativeApi.registerCancelToken(
      runOptions._nativeHandle,
    );
    try {
      responsePort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
        'type': 'started',
        'requestId': requestId,
        'cancelToken': cancelToken,
      });
    } on Object catch (error, stackTrace) {
      try {
        runOptions._nativeApi.finishCancelToken(cancelToken);
        cancelToken = null;
      } on Object catch (cleanupError) {
        throw _WorkerCancellationCleanupFailure(cleanupError);
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
      wasTerminationRequested = runOptions._nativeApi.finishCancelToken(
        cancelToken,
      );
      cancelToken = null;
    } on Object catch (error) {
      return _OrtWorkerRunCompletion(
        fatalCleanupFailure: true,
        reply: <String, Object?>{
          'version': _ortWorkerProtocolVersion,
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
            'version': _ortWorkerProtocolVersion,
            'type': 'ortError',
            'requestId': requestId,
            'error': _encodeWorkerOrtError(error),
            'wasTerminationRequested': wasTerminationRequested,
          },
        );
      }
      return _OrtWorkerRunCompletion(
        reply: <String, Object?>{
          'version': _ortWorkerProtocolVersion,
          'type': 'workerError',
          'requestId': requestId,
          'message': _safeWorkerFailureText(runError),
        },
      );
    }

    final OrtRunResult completedResult = runResult!;
    final _WorkerMessageBudget preflightBudget = _WorkerMessageBudget(
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
    final _WorkerMessageBudget encodeBudget = _WorkerMessageBudget(
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
        'version': _ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': requestId,
        'outputs': encodedOutputs,
        'providerEvidence': providerEvidenceReceipt,
        'providerDiagnostics': providerDiagnosticsReceipt,
        'diagnostics': diagnosticsReceipt,
        'wasTerminationRequested': wasTerminationRequested,
      },
    );
  } on _WorkerCancellationCleanupFailure catch (error) {
    return _OrtWorkerRunCompletion(
      fatalCleanupFailure: true,
      reply: <String, Object?>{
        'version': _ortWorkerProtocolVersion,
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
        'version': _ortWorkerProtocolVersion,
        'type': 'ortError',
        'requestId': requestId,
        'error': _encodeWorkerOrtError(error),
        'wasTerminationRequested': wasTerminationRequested,
      },
    );
  } on Object catch (error) {
    return _OrtWorkerRunCompletion(
      reply: <String, Object?>{
        'version': _ortWorkerProtocolVersion,
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
        '${_boundedWorkerText(error.message, 'native operation failed')}';
  }
  if (error is FormatException ||
      error is ArgumentError ||
      error is RangeError ||
      error is StateError ||
      error is UnsupportedError) {
    return _boundedWorkerText(error.toString(), 'worker validation failed');
  }
  return 'Worker failure (${error.runtimeType}).';
}

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
}) {
  final int effectiveMaxOutstandingInputBytes =
      maxOutstandingInputBytes ?? maxMessageBytes;
  _validateWorkerBounds(
    maxPendingRuns: maxPendingRuns,
    maxMessageBytes: maxMessageBytes,
    maxOutstandingInputBytes: effectiveMaxOutstandingInputBytes,
    startupTimeout: startupTimeout,
  );
  if (!const <String>{
    'echo',
    'delay',
    'crash',
    'stale',
    'startupError',
    'startupExit',
    'startupGateReady',
    'startupGateError',
    'startupGateExit',
    'startupMalformedReady',
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
        'startupReadyMissingCommandPort',
        'startupReadyWrongCommandPort',
        'malformedWhileOwned',
        'cleanupGateResult',
        'cleanupGateOrtError',
        'fatalProtocolReply',
        'fatalWorkerReply',
        'fatalWorkerStaleReply',
      }.contains(scenario) ||
      spawnGate != null;
  if (needsStartupLifecycle != (startupLifecyclePort != null)) {
    throw ArgumentError(
      'startupLifecyclePort is required only for startup lifecycle scenarios.',
    );
  }
  return _spawnOrtWorker(
    entrypoint: _ortIsolateProtocolHarnessMain,
    startup: <String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'harnessStartup',
      'scenario': scenario,
      'runtimeLibraryPath': runtimeLibraryPath,
      'startupLifecyclePort': startupLifecyclePort,
      'maxMessageBytes': maxMessageBytes,
    },
    maxPendingRuns: maxPendingRuns,
    maxMessageBytes: maxMessageBytes,
    maxOutstandingInputBytes: effectiveMaxOutstandingInputBytes,
    startupTimeout: startupTimeout,
    onControllerEventForTesting: onControllerEvent,
    requestCancelTokenForTesting: requestCancelToken,
    onParentStateForTesting: onParentState,
    spawnGateForTesting: spawnGate,
  );
}

void _ortIsolateProtocolHarnessMain(Map<String, Object?> initialMessage) async {
  final Map<Object?, Object?> startup = _workerMap(initialMessage);
  _requireWorkerKeys(
    startup,
    required: const <String>{
      'version',
      'type',
      'responsePort',
      'scenario',
      'runtimeLibraryPath',
      'startupLifecyclePort',
      'maxMessageBytes',
    },
  );
  _requireWorkerVersion(startup);
  if (_workerString(startup, 'type') != 'harnessStartup') {
    throw const FormatException('Expected the harness startup command.');
  }
  final Object? rawResponsePort = startup['responsePort'];
  if (rawResponsePort is! SendPort) {
    throw const FormatException('Harness response port is missing.');
  }
  final SendPort responsePort = rawResponsePort;
  final String scenario = _workerString(startup, 'scenario');
  final String? runtimeLibraryPath = _workerNullableString(
    startup,
    'runtimeLibraryPath',
  );
  final Object? rawStartupLifecyclePort = startup['startupLifecyclePort'];
  if (rawStartupLifecyclePort != null && rawStartupLifecyclePort is! SendPort) {
    throw const FormatException('Harness lifecycle port is malformed.');
  }
  final SendPort? startupLifecyclePort = rawStartupLifecyclePort as SendPort?;
  final int maxMessageBytes = _workerBoundedInt(
    startup,
    'maxMessageBytes',
    1,
    _maximumWorkerMessageBytes,
  );
  final ReceivePort commands = ReceivePort();
  responsePort.send(<String, Object?>{
    'version': _ortWorkerProtocolVersion,
    'type': 'ownership',
    'commandPort': commands.sendPort,
  });
  if (scenario == 'startupExit') {
    commands.close();
    return;
  }
  if (scenario == 'startupError') {
    responsePort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
      'type': 'startupError',
      'message': 'Synthetic bounded startup failure.',
    });
    commands.close();
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
        'version': _ortWorkerProtocolVersion,
        'type': 'startupError',
        'message': 'Synthetic delayed bounded startup failure.',
      });
      startupLifecyclePort.send('startupError');
      commands.close();
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
    'version': _ortWorkerProtocolVersion,
    'type': 'ready',
    'commandPort': wrongCommands?.sendPort ?? commands.sendPort,
    'inputNames': const <String>['X'],
    'outputNames': const <String>['Y'],
    'diagnostics': scenario == 'startupMalformedReady'
        ? 'malformed-diagnostics'
        : _syntheticWorkerDiagnostics(),
  };
  if (scenario == 'startupReadyMissingCommandPort') {
    readyMessage.remove('commandPort');
  }
  responsePort.send(readyMessage);
  var priorRequestId = 0;
  var closeReceiptSent = false;
  var terminalReplySent = false;
  await for (final Object? rawCommand in commands) {
    final Map<Object?, Object?> command = _workerMap(rawCommand);
    _requireWorkerVersion(command);
    final String type = _workerString(command, 'type');
    _requireWorkerCommandKeys(command, type);
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
        'version': _ortWorkerProtocolVersion,
        'type': 'closed',
      });
      closeReceiptSent = true;
      continue;
    }
    final int requestId = _workerPositiveInt(command, 'requestId');

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

    if (scenario == 'crash') {
      Isolate.current.kill(priority: Isolate.immediate);
      return;
    }
    if (scenario == 'fatalProtocolReply') {
      responsePort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
        'type': 'fatalProtocol',
        'message': 'Synthetic fatal worker protocol failure.',
      });
      terminalReplySent = true;
      continue;
    }
    if (scenario == 'fatalWorkerReply') {
      responsePort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
        'type': 'fatalWorkerError',
        'requestId': requestId,
        'message': 'Synthetic fatal native cleanup failure.',
      });
      terminalReplySent = true;
      continue;
    }
    if (scenario == 'fatalWorkerStaleReply' && priorRequestId != 0) {
      responsePort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
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
        'version': _ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': requestId,
        'outputs': const <String, Object?>{},
      });
      continue;
    }
    if (scenario == 'unknownField') {
      responsePort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
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
        'version': _ortWorkerProtocolVersion,
        'type': 'ortError',
        'requestId': requestId,
        'error': <String, Object?>{
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
      final int cancelToken = runOptions._nativeApi.registerCancelToken(
        runOptions._nativeHandle,
      );
      var wasTerminationRequested = false;
      try {
        responsePort.send(<String, Object?>{
          'version': _ortWorkerProtocolVersion,
          'type': 'started',
          'requestId': requestId,
          'cancelToken': cancelToken,
        });
        await Future<void>.delayed(const Duration(milliseconds: 50));
        wasTerminationRequested = runOptions._nativeApi.finishCancelToken(
          cancelToken,
        );
      } finally {
        runOptions.dispose();
      }
      responsePort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
        'type': 'ortError',
        'requestId': requestId,
        'error': <String, Object?>{
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
        'version': _ortWorkerProtocolVersion,
        'type': 'started',
        'requestId': requestId,
        'cancelToken': 1,
      });
      await Future<void>.delayed(const Duration(milliseconds: 20));
    }
    final _WorkerMessageBudget decodeBudget = _WorkerMessageBudget(
      maxBytes: maxMessageBytes,
    );
    final Map<Object?, Object?> inputs = _workerMap(command['inputs']);
    final OrtIsolateValue input = _decodeIsolateValue(
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
      final _WorkerMessageBudget budget = _WorkerMessageBudget(
        maxBytes: maxMessageBytes,
      );
      return <String, Object?>{
        'Y': _encodeIsolateValue(input, budget: budget, depth: 0),
      };
    }

    if (scenario == 'stale' && priorRequestId != 0) {
      responsePort.send(<String, Object?>{
        'version': _ortWorkerProtocolVersion,
        'type': 'result',
        'requestId': priorRequestId,
        'outputs': encodedOutput(),
        'providerEvidence': null,
        'providerDiagnostics': const <Object?>[],
        'diagnostics': _syntheticWorkerDiagnostics(),
        'wasTerminationRequested': false,
      });
    }
    responsePort.send(<String, Object?>{
      'version': _ortWorkerProtocolVersion,
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

Map<String, Object?> _syntheticWorkerDiagnostics() => <String, Object?>{
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
  'providers': const <Object?>[],
};
