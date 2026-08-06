import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/inference_backend.dart';
import 'package:fonix_reference/src/inference_controller.dart';

import 'fake_inference_backend.dart';

void main() {
  test('starts and publishes an exact successful run receipt', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    expect(controller.state.phase, InferencePhase.starting);
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    expect(controller.state.phase, InferencePhase.ready);

    final Future<void> running = controller.run();
    expect(controller.state.phase, InferencePhase.running);
    expect(backend.requests.single.values, referenceInputValues);
    backend.runs.single.complete(fakeRunReceipt());
    await running;

    expect(controller.state.phase, InferencePhase.ready);
    expect(controller.state.runReceipt?.outputValues, referenceOutputValues);
    expect(controller.state.runReceipt?.activeProviders, <String>['cpu']);
    await controller.close();
    controller.dispose();
  });

  test('retries startup with a fresh backend', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> firstStart = controller.start();
    factory.backends.first.startup.completeError(StateError('private detail'));
    await firstStart;
    expect(controller.state.phase, InferencePhase.failure);
    expect(controller.state.failure?.stage, InferenceFailureStage.startup);
    expect(controller.state.failure?.summary, 'StateError');

    final Future<void> retry = controller.retry();
    expect(factory.backends, hasLength(2));
    factory.backends.last.startup.complete(fakeStartupReceipt());
    await retry;
    expect(controller.state.phase, InferencePhase.ready);
    expect(factory.backends.first.closeWorkCount, 1);
    await controller.close();
    controller.dispose();
  });

  test('retries a run with the exact prior request', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;

    final InferenceRequest request = InferenceRequest(<double>[
      6,
      5,
      4,
      3,
      2,
      1,
    ]);
    final Future<void> firstRun = controller.run(request);
    backend.runs.first.completeError(
      const InferenceBackendFailure(summary: 'Bounded run failure.'),
    );
    await firstRun;
    expect(controller.state.failure?.stage, InferenceFailureStage.run);

    final Future<void> retry = controller.retry();
    expect(backend.requests, hasLength(2));
    expect(backend.requests.last.values, request.values);
    backend.runs.last.complete(fakeRunReceipt());
    await retry;
    expect(controller.state.runReceipt?.outputValues, referenceOutputValues);
    await controller.close();
    controller.dispose();
  });

  test('cancel is idempotent and suppresses a stale completion', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    final Future<void> running = controller.run();

    final Future<void> firstCancel = controller.cancel();
    final Future<void> secondCancel = controller.cancel();
    expect(identical(firstCancel, secondCancel), isTrue);
    expect(controller.state.phase, InferencePhase.cancelling);
    backend.runs.single.complete(fakeRunReceipt());
    await Future.wait(<Future<void>>[running, firstCancel]);

    expect(backend.cancelCalls, 1);
    expect(controller.state.phase, InferencePhase.cancelled);
    expect(controller.state.runReceipt, isNull);
    await controller.close();
    controller.dispose();
  });

  test(
    'terminal cancellation failure retires the backend before retry',
    () async {
      final FakeBackendFactory factory = FakeBackendFactory();
      final InferenceController controller = InferenceController(
        createBackend: factory.call,
      );
      final Future<void> starting = controller.start();
      final FakeInferenceBackend first = factory.backends.single;
      first.startup.complete(fakeStartupReceipt());
      await starting;
      final Future<void> running = controller.run();
      first.cancelError = const InferenceBackendFailure(
        summary: 'Worker cancellation failed.',
        backendUnusable: true,
      );

      final Future<void> cancelling = controller.cancel();
      first.runs.single.completeError(
        const InferenceBackendFailure(summary: 'Run cancelled.'),
      );
      await Future.wait(<Future<void>>[running, cancelling]);

      expect(controller.state.phase, InferencePhase.failure);
      expect(
        controller.state.failure?.stage,
        InferenceFailureStage.cancellation,
      );
      expect(first.closeWorkCount, 1);
      final Future<void> retrying = controller.retry();
      expect(factory.backends, hasLength(2));
      factory.backends.last.startup.complete(fakeStartupReceipt());
      await retrying;
      expect(controller.state.phase, InferencePhase.ready);
      await controller.close();
      controller.dispose();
    },
  );

  test(
    'terminal run failure during successful cancel retires the backend',
    () async {
      final FakeBackendFactory factory = FakeBackendFactory();
      final InferenceController controller = InferenceController(
        createBackend: factory.call,
      );
      final Future<void> starting = controller.start();
      final FakeInferenceBackend first = factory.backends.single;
      first.startup.complete(fakeStartupReceipt());
      await starting;
      final Future<void> running = controller.run();

      final Future<void> cancelling = controller.cancel();
      first.runs.single.completeError(
        const InferenceBackendFailure(
          summary: 'Worker terminated during cancellation.',
          backendUnusable: true,
        ),
      );
      await Future.wait(<Future<void>>[running, cancelling]);

      expect(controller.state.phase, InferencePhase.failure);
      expect(controller.state.failure?.stage, InferenceFailureStage.run);
      expect(first.closeWorkCount, 1);
      final Future<void> retrying = controller.retry();
      expect(factory.backends, hasLength(2));
      factory.backends.last.startup.complete(fakeStartupReceipt());
      await retrying;
      expect(controller.state.phase, InferencePhase.ready);
      await controller.close();
      controller.dispose();
    },
  );

  test('suspend joins active cancellation without cancelling twice', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    final Future<void> running = controller.run();
    final Future<void> cancelling = controller.cancel();

    final Future<void> suspending = controller.suspend();
    backend.runs.single.complete(fakeRunReceipt());
    await Future.wait(<Future<void>>[running, cancelling, suspending]);

    expect(backend.cancelCalls, 1);
    expect(backend.closeWorkCount, 1);
    expect(controller.state.phase, InferencePhase.suspended);
    await controller.close();
    controller.dispose();
  });

  test('close joins active cancellation without cancelling twice', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    final Future<void> running = controller.run();
    final Future<void> cancelling = controller.cancel();

    final Future<void> closing = controller.close();
    backend.runs.single.complete(fakeRunReceipt());
    await Future.wait(<Future<void>>[running, cancelling, closing]);

    expect(backend.cancelCalls, 1);
    expect(backend.closeWorkCount, 1);
    expect(controller.state.phase, InferencePhase.closed);
    controller.dispose();
  });

  test(
    'close waits for terminal cancellation cleanup already in flight',
    () async {
      final FakeBackendFactory factory = FakeBackendFactory();
      final InferenceController controller = InferenceController(
        createBackend: factory.call,
      );
      final Future<void> starting = controller.start();
      final FakeInferenceBackend backend = factory.backends.single;
      backend.startup.complete(fakeStartupReceipt());
      await starting;
      backend.cancelError = const InferenceBackendFailure(
        summary: 'Worker cancellation failed.',
        backendUnusable: true,
      );
      backend.closeGate = Completer<void>();
      final Future<void> running = controller.run();
      final Future<void> cancelling = controller.cancel();
      backend.runs.single.completeError(
        const InferenceBackendFailure(summary: 'Run cancelled.'),
      );
      await running;
      await Future<void>.delayed(Duration.zero);
      expect(backend.closeWorkCount, 1);

      var closeCompleted = false;
      final Future<void> closing = controller.close().whenComplete(() {
        closeCompleted = true;
      });
      await Future<void>.delayed(Duration.zero);
      expect(closeCompleted, isFalse);

      backend.closeGate!.complete();
      await Future.wait(<Future<void>>[cancelling, closing]);
      expect(controller.state.phase, InferencePhase.closed);
      expect(backend.closeWorkCount, 1);
      controller.dispose();
    },
  );

  test('close during startup retires the late backend exactly once', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    final Future<void> closing = controller.close();
    expect(controller.state.phase, InferencePhase.closed);
    backend.startup.complete(fakeStartupReceipt());
    await Future.wait(<Future<void>>[starting, closing]);

    expect(backend.closeWorkCount, 1);
    expect(controller.state.phase, InferencePhase.closed);
    controller.dispose();
  });

  test('close cancels and drains an active run', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    final Future<void> running = controller.run();
    final Future<void> closing = controller.close();
    backend.runs.single.completeError(
      const InferenceBackendFailure(summary: 'Cancelled.'),
    );
    await Future.wait(<Future<void>>[running, closing]);

    expect(backend.cancelCalls, 1);
    expect(backend.closeWorkCount, 1);
    expect(controller.state.phase, InferencePhase.closed);
    controller.dispose();
  });

  test(
    'suspend closes the worker and resume creates one replacement',
    () async {
      final FakeBackendFactory factory = FakeBackendFactory();
      final InferenceController controller = InferenceController(
        createBackend: factory.call,
      );
      final Future<void> starting = controller.start();
      final FakeInferenceBackend first = factory.backends.single;
      first.startup.complete(fakeStartupReceipt());
      await starting;
      await controller.suspend();
      expect(controller.state.phase, InferencePhase.suspended);
      expect(first.closeWorkCount, 1);

      final Future<void> resuming = controller.resume();
      await Future<void>.delayed(Duration.zero);
      expect(factory.backends, hasLength(2));
      factory.backends.last.startup.complete(fakeStartupReceipt());
      await resuming;
      expect(controller.state.phase, InferencePhase.ready);
      await controller.close();
      controller.dispose();
    },
  );

  test('close waits for cleanup already owned by suspend', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    backend.closeGate = Completer<void>();

    final Future<void> suspending = controller.suspend();
    var closeCompleted = false;
    final Future<void> closing = controller.close().whenComplete(() {
      closeCompleted = true;
    });
    await Future<void>.delayed(Duration.zero);
    expect(closeCompleted, isFalse);

    backend.closeGate!.complete();
    await Future.wait(<Future<void>>[suspending, closing]);
    expect(backend.closeWorkCount, 1);
    expect(controller.state.phase, InferencePhase.closed);
    controller.dispose();
  });

  test('a later pause invalidates a resume queued behind suspension', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend first = factory.backends.single;
    first.startup.complete(fakeStartupReceipt());
    await starting;
    first.closeGate = Completer<void>();

    final Future<void> firstPause = controller.suspend();
    final Future<void> queuedResume = controller.resume();
    final Future<void> finalPause = controller.suspend();
    expect(identical(firstPause, finalPause), isTrue);
    first.closeGate!.complete();
    await Future.wait(<Future<void>>[firstPause, queuedResume, finalPause]);

    expect(factory.backends, hasLength(1));
    expect(controller.state.phase, InferencePhase.suspended);

    final Future<void> finalResume = controller.resume();
    await Future<void>.delayed(Duration.zero);
    expect(factory.backends, hasLength(2));
    factory.backends.last.startup.complete(fakeStartupReceipt());
    await finalResume;
    expect(controller.state.phase, InferencePhase.ready);
    await controller.close();
    controller.dispose();
  });

  test('close performs backend cleanup once across repeated calls', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;

    final Future<void> first = controller.close();
    final Future<void> second = controller.close();
    expect(identical(first, second), isTrue);
    await first;
    expect(backend.closeWorkCount, 1);
    controller.dispose();
  });

  test('reentrant start listener receives the authoritative future', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    Future<void>? nestedStart;
    controller.addListener(() {
      if (controller.state.phase == InferencePhase.starting &&
          nestedStart == null) {
        nestedStart = controller.start();
      }
    });

    final Future<void> starting = controller.start();
    expect(identical(starting, nestedStart), isTrue);
    expect(factory.backends, hasLength(1));
    factory.backends.single.startup.complete(fakeStartupReceipt());
    await starting;
    expect(controller.state.phase, InferencePhase.ready);
    await controller.close();
    controller.dispose();
  });

  test('reentrant run and cancel listeners share their operations', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    Future<void>? nestedRun;
    Future<void>? nestedCancel;
    controller.addListener(() {
      if (controller.state.phase == InferencePhase.running &&
          nestedRun == null) {
        nestedRun = controller.run();
      }
      if (controller.state.phase == InferencePhase.cancelling &&
          nestedCancel == null) {
        nestedCancel = controller.cancel();
      }
    });

    final Future<void> running = controller.run();
    expect(identical(running, nestedRun), isTrue);
    expect(backend.requests, hasLength(1));
    final Future<void> cancelling = controller.cancel();
    expect(identical(cancelling, nestedCancel), isTrue);
    backend.runs.single.complete(fakeRunReceipt());
    await Future.wait(<Future<void>>[running, cancelling]);

    expect(backend.cancelCalls, 1);
    expect(controller.state.phase, InferencePhase.cancelled);
    await controller.close();
    controller.dispose();
  });

  test('reentrant resume waits for authoritative suspension cleanup', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend first = factory.backends.single;
    first.startup.complete(fakeStartupReceipt());
    await starting;
    first.closeGate = Completer<void>();
    Future<void>? resumed;
    controller.addListener(() {
      if (controller.state.phase == InferencePhase.suspended &&
          resumed == null) {
        resumed = controller.resume();
      }
    });

    final Future<void> suspending = controller.suspend();
    expect(factory.backends, hasLength(1));
    expect(first.closeWorkCount, 1);
    first.closeGate!.complete();
    await suspending;
    await Future<void>.delayed(Duration.zero);
    expect(factory.backends, hasLength(2));
    factory.backends.last.startup.complete(fakeStartupReceipt());
    await resumed;
    expect(controller.state.phase, InferencePhase.ready);
    await controller.close();
    controller.dispose();
  });

  test('reentrant close listener receives cleanup future', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    backend.closeGate = Completer<void>();
    Future<void>? nestedClose;
    controller.addListener(() {
      if (controller.state.phase == InferencePhase.closed &&
          nestedClose == null) {
        nestedClose = controller.close();
      }
    });

    final Future<void> authoritativeClosing = controller.close();
    expect(identical(authoritativeClosing, nestedClose), isTrue);
    var closeCompleted = false;
    final Future<void> closing = authoritativeClosing.whenComplete(() {
      closeCompleted = true;
    });
    expect(identical(controller.close(), authoritativeClosing), isTrue);
    await Future<void>.delayed(Duration.zero);
    expect(closeCompleted, isFalse);
    expect(backend.closeWorkCount, 1);

    backend.closeGate!.complete();
    await Future.wait(<Future<void>>[closing, nestedClose!]);
    expect(closeCompleted, isTrue);
    expect(backend.closeWorkCount, 1);
    controller.dispose();
  });

  test('start preserves active run and cancellation phases', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;

    final Future<void> running = controller.run();
    await controller.start();
    expect(controller.state.phase, InferencePhase.running);
    expect(factory.backends, hasLength(1));

    final Future<void> cancelling = controller.cancel();
    await controller.start();
    expect(controller.state.phase, InferencePhase.cancelling);
    expect(factory.backends, hasLength(1));
    backend.runs.single.complete(fakeRunReceipt());
    await Future.wait(<Future<void>>[running, cancelling]);
    await controller.close();
    controller.dispose();
  });

  test('startup failure listener can retry with a fresh backend', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    Future<void>? retrying;
    controller.addListener(() {
      if (controller.state.failure?.stage == InferenceFailureStage.startup &&
          retrying == null) {
        retrying = controller.retry();
      }
    });

    final Future<void> firstStart = controller.start();
    factory.backends.single.startup.completeError(StateError('private'));
    await firstStart;
    expect(factory.backends, hasLength(2));
    factory.backends.last.startup.complete(fakeStartupReceipt());
    await retrying;

    expect(controller.state.phase, InferencePhase.ready);
    expect(factory.backends.first.closeWorkCount, 1);
    await controller.close();
    controller.dispose();
  });

  test('run failure listener can retry after native settlement', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    Future<void>? retrying;
    controller.addListener(() {
      if (controller.state.failure?.stage == InferenceFailureStage.run &&
          retrying == null) {
        retrying = controller.retry();
      }
    });

    final Future<void> firstRun = controller.run();
    backend.runs.single.completeError(
      const InferenceBackendFailure(summary: 'Transient run failure.'),
    );
    await firstRun;
    expect(backend.requests, hasLength(2));
    backend.runs.last.complete(fakeRunReceipt());
    await retrying;

    expect(controller.state.phase, InferencePhase.ready);
    expect(controller.state.runReceipt?.outputValues, referenceOutputValues);
    await controller.close();
    controller.dispose();
  });

  test('ready listener can chain a run after prior settlement', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    var chainingEnabled = false;
    Future<void>? chainedRun;
    controller.addListener(() {
      if (chainingEnabled &&
          controller.state.phase == InferencePhase.ready &&
          chainedRun == null) {
        chainedRun = controller.run();
      }
    });

    chainingEnabled = true;
    final Future<void> firstRun = controller.run();
    backend.runs.single.complete(fakeRunReceipt());
    await firstRun;
    expect(backend.requests, hasLength(2));
    backend.runs.last.complete(fakeRunReceipt());
    await chainedRun;

    expect(controller.state.phase, InferencePhase.ready);
    await controller.close();
    controller.dispose();
  });

  test('cancelled listener can submit a new run after settlement', () async {
    final FakeBackendFactory factory = FakeBackendFactory();
    final InferenceController controller = InferenceController(
      createBackend: factory.call,
    );
    final Future<void> starting = controller.start();
    final FakeInferenceBackend backend = factory.backends.single;
    backend.startup.complete(fakeStartupReceipt());
    await starting;
    Future<void>? nextRun;
    controller.addListener(() {
      if (controller.state.phase == InferencePhase.cancelled &&
          nextRun == null) {
        nextRun = controller.run();
      }
    });

    final Future<void> firstRun = controller.run();
    final Future<void> cancelling = controller.cancel();
    backend.runs.single.complete(fakeRunReceipt());
    await Future.wait(<Future<void>>[firstRun, cancelling]);
    expect(backend.requests, hasLength(2));
    backend.runs.last.complete(fakeRunReceipt());
    await nextRun;

    expect(controller.state.phase, InferencePhase.ready);
    await controller.close();
    controller.dispose();
  });
}
