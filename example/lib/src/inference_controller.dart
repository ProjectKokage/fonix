import 'dart:async';

import 'package:flutter/foundation.dart';

import 'inference_backend.dart';

enum InferencePhase {
  idle,
  starting,
  ready,
  running,
  cancelling,
  cancelled,
  suspended,
  failure,
  closed,
}

enum InferenceFailureStage { startup, run, cancellation }

final class InferenceFailure {
  const InferenceFailure({required this.stage, required this.summary});

  final InferenceFailureStage stage;
  final String summary;
}

final class InferenceViewState {
  const InferenceViewState({
    required this.phase,
    required this.request,
    this.startupReceipt,
    this.runReceipt,
    this.failure,
  });

  factory InferenceViewState.initial() => InferenceViewState(
    phase: InferencePhase.idle,
    request: InferenceRequest.reference(),
  );

  final InferencePhase phase;
  final InferenceRequest request;
  final InferenceStartupReceipt? startupReceipt;
  final InferenceRunReceipt? runReceipt;
  final InferenceFailure? failure;
}

final class InferenceController extends ChangeNotifier {
  InferenceController({required InferenceBackendFactory createBackend})
    : _createBackend = createBackend;

  final InferenceBackendFactory _createBackend;

  InferenceViewState _state = InferenceViewState.initial();
  InferenceBackend? _backend;
  Future<void>? _startFuture;
  Future<void>? _runFuture;
  Future<InferenceBackendFailure?>? _runSettlementFuture;
  Future<void>? _cancelFuture;
  Future<void>? _suspendFuture;
  Future<void>? _closeFuture;
  var _generation = 0;
  var _lifecycleRevision = 0;
  var _suspensionDesired = false;
  var _disposed = false;

  InferenceViewState get state => _state;

  Future<void> start() {
    if (_state.phase == InferencePhase.closed || _suspensionDesired) {
      return Future<void>.value();
    }
    final Future<void>? existing = _startFuture;
    if (existing != null) return existing;
    if (_backend != null) {
      return Future<void>.value();
    }
    final Future<void>? suspending = _suspendFuture;
    if (suspending != null) {
      return suspending.then((_) => start());
    }

    final int generation = ++_generation;
    final InferenceBackend backend;
    try {
      backend = _createBackend();
    } on Object catch (error) {
      _publishFailure(InferenceFailureStage.startup, error);
      return Future<void>.value();
    }
    _backend = backend;
    final Completer<void> completer = Completer<void>();
    late final Future<void> future;
    future = completer.future.whenComplete(() {
      if (identical(_startFuture, future)) _startFuture = null;
    });
    _startFuture = future;
    _publish(
      InferenceViewState(
        phase: InferencePhase.starting,
        request: _state.request,
      ),
    );
    unawaited(_forwardCompletion(_start(generation, backend), completer));
    return future;
  }

  Future<void> _start(int generation, InferenceBackend backend) async {
    if (!_isAuthoritative(generation, backend)) return;
    try {
      final InferenceStartupReceipt receipt = await backend.start();
      if (!_isAuthoritative(generation, backend)) {
        await _closeIgnoringFailure(backend);
        return;
      }
      _startFuture = null;
      _publish(
        InferenceViewState(
          phase: InferencePhase.ready,
          request: _state.request,
          startupReceipt: receipt,
        ),
      );
    } on Object catch (error) {
      if (identical(_backend, backend)) _backend = null;
      await _closeIgnoringFailure(backend);
      if (_generation == generation && _state.phase != InferencePhase.closed) {
        _startFuture = null;
        _publishFailure(InferenceFailureStage.startup, error);
      }
    }
  }

  Future<void> run([InferenceRequest? request]) {
    if (_state.phase == InferencePhase.closed || _backend == null) {
      return Future<void>.value();
    }
    final Future<void>? existing = _runFuture;
    if (existing != null) return existing;
    if (_state.phase == InferencePhase.starting ||
        _state.phase == InferencePhase.cancelling ||
        _state.phase == InferencePhase.suspended) {
      return Future<void>.value();
    }
    final InferenceBackend backend = _backend!;
    final InferenceRequest effectiveRequest = request ?? _state.request;
    final int generation = ++_generation;
    final Completer<InferenceBackendFailure?> completer =
        Completer<InferenceBackendFailure?>();
    late final Future<InferenceBackendFailure?> settlement;
    late final Future<void> future;
    settlement = completer.future;
    future = settlement
        .then<void>((InferenceBackendFailure? _) {})
        .whenComplete(() {
          if (identical(_runFuture, future)) _runFuture = null;
          if (identical(_runSettlementFuture, settlement)) {
            _runSettlementFuture = null;
          }
        });
    _runSettlementFuture = settlement;
    _runFuture = future;
    _publish(
      InferenceViewState(
        phase: InferencePhase.running,
        request: effectiveRequest,
        startupReceipt: _state.startupReceipt,
      ),
    );
    unawaited(
      _forwardCompletion(
        _run(generation, backend, effectiveRequest),
        completer,
      ),
    );
    return future;
  }

  Future<InferenceBackendFailure?> _run(
    int generation,
    InferenceBackend backend,
    InferenceRequest request,
  ) async {
    if (!_isAuthoritative(generation, backend)) return null;
    try {
      final InferenceRunReceipt receipt = await backend.run(request);
      if (!_isAuthoritative(generation, backend)) return null;
      _runFuture = null;
      _runSettlementFuture = null;
      _publish(
        InferenceViewState(
          phase: InferencePhase.ready,
          request: request,
          startupReceipt: _state.startupReceipt,
          runReceipt: receipt,
        ),
      );
      return null;
    } on Object catch (error) {
      final InferenceBackendFailure? terminalFailure =
          error is InferenceBackendFailure && error.backendUnusable
          ? error
          : null;
      if (!_isAuthoritative(generation, backend)) return terminalFailure;
      if (terminalFailure != null) {
        _backend = null;
        await _closeIgnoringFailure(backend);
        if (_generation != generation) return terminalFailure;
      }
      _runFuture = null;
      _runSettlementFuture = null;
      _publishFailure(InferenceFailureStage.run, error, request: request);
      return terminalFailure;
    }
  }

  Future<void> cancel() {
    final Future<void>? existing = _cancelFuture;
    if (existing != null) return existing;
    final InferenceBackend? backend = _backend;
    final Future<InferenceBackendFailure?>? runSettlement =
        _runSettlementFuture;
    if (backend == null || runSettlement == null) return Future<void>.value();

    final int generation = ++_generation;
    final Completer<void> completer = Completer<void>();
    late final Future<void> future;
    future = completer.future.whenComplete(() {
      if (identical(_cancelFuture, future)) _cancelFuture = null;
    });
    _cancelFuture = future;
    _publish(
      InferenceViewState(
        phase: InferencePhase.cancelling,
        request: _state.request,
        startupReceipt: _state.startupReceipt,
      ),
    );
    unawaited(
      _forwardCompletion(
        _cancel(generation, backend, runSettlement),
        completer,
      ),
    );
    return future;
  }

  Future<void> _cancel(
    int generation,
    InferenceBackend backend,
    Future<InferenceBackendFailure?> runSettlement,
  ) async {
    Object? cancellationError;
    try {
      await backend.cancel();
    } on Object catch (error) {
      cancellationError = error;
    }
    final InferenceBackendFailure? terminalRunFailure = await runSettlement;
    if (!_isAuthoritative(generation, backend)) return;
    _runFuture = null;
    _runSettlementFuture = null;
    final bool backendUnusable =
        terminalRunFailure != null ||
        cancellationError is InferenceBackendFailure &&
            cancellationError.backendUnusable;
    if (backendUnusable) {
      _backend = null;
      await _closeIgnoringFailure(backend);
      if (_generation != generation ||
          _state.phase == InferencePhase.closed ||
          _state.phase == InferencePhase.suspended) {
        return;
      }
    }
    _cancelFuture = null;
    if (cancellationError != null) {
      _publishFailure(InferenceFailureStage.cancellation, cancellationError);
      return;
    }
    if (terminalRunFailure != null) {
      _publishFailure(InferenceFailureStage.run, terminalRunFailure);
      return;
    }
    _publish(
      InferenceViewState(
        phase: InferencePhase.cancelled,
        request: _state.request,
        startupReceipt: _state.startupReceipt,
      ),
    );
  }

  Future<void> retry() {
    final InferenceFailure? failure = _state.failure;
    if (_state.phase == InferencePhase.cancelled) return run();
    if (failure == null) return Future<void>.value();
    if (failure.stage == InferenceFailureStage.startup || _backend == null) {
      return start();
    }
    return run(_state.request);
  }

  Future<void> suspend() {
    if (_state.phase == InferencePhase.closed) {
      return Future<void>.value();
    }
    _suspensionDesired = true;
    _lifecycleRevision += 1;
    final Future<void>? existing = _suspendFuture;
    if (existing != null) return existing;
    if (_state.phase == InferencePhase.suspended) {
      return Future<void>.value();
    }
    ++_generation;
    final InferenceBackend? backend = _backend;
    _backend = null;
    final Future<void>? running = _runFuture;
    final Future<void>? starting = _startFuture;
    final Future<void>? cancelling = _cancelFuture;
    final Completer<void> completer = Completer<void>();
    late final Future<void> future;
    future = completer.future.whenComplete(() {
      if (identical(_suspendFuture, future)) _suspendFuture = null;
    });
    _suspendFuture = future;
    _publish(
      InferenceViewState(
        phase: InferencePhase.suspended,
        request: _state.request,
        startupReceipt: _state.startupReceipt,
        runReceipt: _state.runReceipt,
      ),
    );
    unawaited(
      _forwardCompletion(
        _suspend(backend, running, starting, cancelling),
        completer,
      ),
    );
    return future;
  }

  Future<void> _suspend(
    InferenceBackend? backend,
    Future<void>? running,
    Future<void>? starting,
    Future<void>? cancelling,
  ) async {
    if (cancelling != null) {
      await cancelling;
    } else {
      if (backend != null && running != null) {
        try {
          await backend.cancel();
        } on Object {
          // Suspension still closes the authoritative backend below.
        }
      }
      if (running != null) await running;
    }
    if (starting != null) await starting;
    if (backend != null) await _closeIgnoringFailure(backend);
  }

  Future<void> resume() async {
    if (_state.phase != InferencePhase.suspended) return;
    _suspensionDesired = false;
    final int lifecycleRevision = ++_lifecycleRevision;
    await _suspendFuture;
    if (_state.phase == InferencePhase.suspended &&
        !_suspensionDesired &&
        _lifecycleRevision == lifecycleRevision) {
      await start();
    }
  }

  Future<void> close() {
    final Future<void>? existing = _closeFuture;
    if (existing != null) return existing;
    _suspensionDesired = true;
    _lifecycleRevision += 1;
    ++_generation;
    final InferenceBackend? backend = _backend;
    _backend = null;
    final Future<void>? running = _runFuture;
    final Future<void>? starting = _startFuture;
    final Future<void>? cancelling = _cancelFuture;
    final Future<void>? suspending = _suspendFuture;
    final Completer<void> completer = Completer<void>();
    final Future<void> future = completer.future;
    _closeFuture = future;
    _publish(
      InferenceViewState(
        phase: InferencePhase.closed,
        request: _state.request,
        startupReceipt: _state.startupReceipt,
        runReceipt: _state.runReceipt,
      ),
    );
    unawaited(
      _forwardCompletion(
        _close(backend, running, starting, cancelling, suspending),
        completer,
      ),
    );
    return future;
  }

  Future<void> _close(
    InferenceBackend? backend,
    Future<void>? running,
    Future<void>? starting,
    Future<void>? cancelling,
    Future<void>? suspending,
  ) async {
    if (cancelling != null) {
      await cancelling;
    } else {
      if (backend != null && running != null) {
        try {
          await backend.cancel();
        } on Object {
          // Close remains best-effort and consumes cancellation failures.
        }
      }
      if (running != null) await running;
    }
    if (starting != null) await starting;
    if (backend != null) await _closeIgnoringFailure(backend);
    if (suspending != null) await suspending;
  }

  bool _isAuthoritative(int generation, InferenceBackend backend) =>
      _generation == generation &&
      identical(_backend, backend) &&
      _state.phase != InferencePhase.closed &&
      _state.phase != InferencePhase.suspended;

  void _publishFailure(
    InferenceFailureStage stage,
    Object error, {
    InferenceRequest? request,
  }) {
    final String summary = switch (error) {
      InferenceBackendFailure(:final summary) => summary,
      _ => '${error.runtimeType}',
    };
    _publish(
      InferenceViewState(
        phase: InferencePhase.failure,
        request: request ?? _state.request,
        startupReceipt: _state.startupReceipt,
        runReceipt: _state.runReceipt,
        failure: InferenceFailure(stage: stage, summary: summary),
      ),
    );
  }

  Future<void> _closeIgnoringFailure(InferenceBackend backend) async {
    try {
      await backend.close();
    } on Object {
      // The controller owns no recovery path after this backend is retired.
    }
  }

  void _publish(InferenceViewState value) {
    _state = value;
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

Future<void> _forwardCompletion<T>(
  Future<T> operation,
  Completer<T> completer,
) async {
  try {
    completer.complete(await operation);
  } on Object catch (error, stackTrace) {
    completer.completeError(error, stackTrace);
  }
}
