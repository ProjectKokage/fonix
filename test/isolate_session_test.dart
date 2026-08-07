import 'dart:async';
import 'dart:collection';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/runtime.dart'
    show
        createOrtSessionPoolForTesting,
        roundTripOrtWorkerSessionOptionsForTesting,
        spawnOrtIsolateProtocolHarnessForTesting;
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

OrtIsolateTensor _tensor(List<double> values) =>
    OrtIsolateTensor.fromFloat32List(
      values: Float32List.fromList(values),
      shape: <int>[values.length],
    );

final class _UnreadableList<T> extends ListBase<T> {
  _UnreadableList(this._length);

  final int _length;
  int elementReads = 0;

  @override
  int get length => _length;

  @override
  set length(int value) => throw UnsupportedError('immutable test list');

  @override
  T operator [](int index) {
    elementReads += 1;
    throw StateError('oversized list elements must not be read');
  }

  @override
  void operator []=(int index, T value) =>
      throw UnsupportedError('immutable test list');
}

void main() {
  group('worker option protocol', () {
    test('preserves typed Core ML cache identity inputs', () {
      final String root = p.join(
        Directory.systemTemp.absolute.path,
        'fonix-worker-cache-contract',
      );
      final String modelSha256 = List<String>.filled(64, 'a').join();
      final OrtSessionOptions original = OrtSessionOptions(
        artifactRoot: root,
        providers: <OrtExecutionProvider>[
          OrtExecutionProvider.coreMl(
            modelFormat: OrtCoreMlModelFormat.mlProgram,
            computeUnits: OrtCoreMlComputeUnits.cpuOnly,
            requireStaticInputShapes: true,
            enableOnSubgraphs: true,
            cache: OrtCoreMlCacheConfiguration(
              rootDirectory: root,
              modelSha256: modelSha256,
              applicationSchema: 'worker-v2',
            ),
            requirement: OrtProviderRequirement.requireFullAssignment,
          ),
          OrtExecutionProvider.cpu(),
        ],
        fallbackPolicy: OrtFallbackPolicy.rejectAny,
      );

      final OrtSessionOptions restored =
          roundTripOrtWorkerSessionOptionsForTesting(original);
      final OrtExecutionProvider coreMl = restored.providers.first;
      expect(coreMl.options, original.providers.first.options);
      expect(coreMl.requirement, OrtProviderRequirement.requireFullAssignment);
      expect(coreMl.coreMlCache?.rootDirectory, root);
      expect(coreMl.coreMlCache?.modelSha256, modelSha256);
      expect(coreMl.coreMlCache?.applicationSchema, 'worker-v2');
      expect(restored.artifactRoot, root);
      expect(restored.fallbackPolicy, OrtFallbackPolicy.rejectAny);
    });
  });

  group('pointer-free isolate values', () {
    test('copies typed input and preserves composite output structure', () {
      final Float32List caller = Float32List.fromList(<double>[1, 2, 3]);
      final OrtIsolateTensor tensor = OrtIsolateTensor.fromFloat32List(
        values: caller,
        shape: const <int>[3],
      );
      caller.fillRange(0, caller.length, 99);
      expect(tensor.copyFloat32Data(), <double>[1, 2, 3]);

      final OrtIsolateValue composite = OrtIsolateSequence(<OrtIsolateValue>[
        tensor,
        _tensor(<double>[4, 5, 6]),
      ]);
      expect(composite.kind, OrtValueKind.sequence);

      final OrtIsolateOptional absent = OrtIsolateOptional.none(
        elementType: OrtTypeInfo.sequence(
          OrtTypeInfo.tensor(
            elementType: OrtTensorElementType.string,
            hasShape: true,
            dimensions: <OrtDimension>[OrtDimension.fixed(2)],
          ),
        ),
      );
      expect(absent.value, isNull);
      expect(absent.type.optionalElement, absent.elementType);
    });

    test('validates exact bytes, booleans, strings, and map cardinality', () {
      expect(
        () => OrtIsolateTensor.fromBytes(
          elementType: OrtTensorElementType.float32,
          bytes: Uint8List(3),
          shape: const <int>[1],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtIsolateTensor.fromBytes(
          elementType: OrtTensorElementType.boolean,
          bytes: Uint8List.fromList(<int>[2]),
          shape: const <int>[1],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtIsolateTensor.fromStrings(
          values: const <String>['bad\u0000value'],
          shape: const <int>[1],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtIsolateMap(
          keys: OrtIsolateTensor.fromInt64List(
            values: Int64List.fromList(<int>[1, 2]),
            shape: const <int>[2],
          ),
          values: _tensor(<double>[1]),
        ),
        throwsArgumentError,
      );
    });

    test('bounds lazy sequence elements before materializing them', () {
      final OrtIsolateTensor element = _tensor(<double>[1]);
      final OrtIsolateSequence maximum = OrtIsolateSequence(
        List<OrtIsolateValue>.filled(1024, element),
      );
      expect(maximum.elements, hasLength(1024));

      var observations = 0;
      Iterable<OrtIsolateValue> endlessElements() sync* {
        while (true) {
          observations += 1;
          yield element;
        }
      }

      expect(() => OrtIsolateSequence(endlessElements()), throwsRangeError);
      expect(observations, 1025);
    });
  });

  group('real isolate protocol controller', () {
    test('round-trips copied and transferable composite values', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting();
      try {
        expect(worker.diagnostics.runtimeVersion, 'synthetic');
        expect(worker.diagnostics.session, isNotNull);
        final OrtIsolateSequence input = OrtIsolateSequence(<OrtIsolateValue>[
          _tensor(<double>[1, 2]),
          _tensor(<double>[3, 4]),
        ]);
        final OrtIsolateRunResult result = await worker.run(
          inputs: <String, OrtIsolateValue>{'X': input},
        );
        final OrtIsolateSequence output =
            result.value('Y') as OrtIsolateSequence;
        expect(
          (output.elements[1] as OrtIsolateTensor).copyFloat32Data(),
          <double>[3, 4],
        );
        expect(result.diagnostics.runtimeIdentity, 'synthetic-worker-runtime');
        expect(result.diagnostics.providers, isEmpty);
      } finally {
        await worker.close();
      }
    });

    test('round-trips the recursive element type of Optional None', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting();
      try {
        final OrtTypeInfo elementType = OrtTypeInfo.sequence(
          OrtTypeInfo.tensor(
            elementType: OrtTensorElementType.int64,
            hasShape: true,
            dimensions: <OrtDimension>[OrtDimension.dynamic('items')],
          ),
        );
        final OrtIsolateRunResult result = await worker.run(
          inputs: <String, OrtIsolateValue>{
            'X': OrtIsolateOptional.none(elementType: elementType),
          },
        );
        final OrtIsolateOptional output =
            result.value('Y') as OrtIsolateOptional;
        expect(output.value, isNull);
        expect(output.elementType, elementType);
      } finally {
        await worker.close();
      }
    });

    test('queued cancellation happens before a native token exists', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'delay',
            maxPendingRuns: 2,
          );
      final OrtIsolateRun first = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[1]),
        },
      );
      final OrtIsolateRun queued = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[2]),
        },
      );
      final Future<void> cancelledResult = expectLater(
        queued.result,
        throwsA(isA<OrtRunCancelledException>()),
      );
      final Future<OrtRunCancellationDisposition> queuedCancellation = queued
          .cancelWithDisposition();
      expect(
        identical(queuedCancellation, queued.cancelWithDisposition()),
        isTrue,
      );
      expect(
        await queuedCancellation,
        OrtRunCancellationDisposition.queuedRunRemoved,
      );
      final Future<bool> booleanCancellation = queued.cancel();
      expect(identical(booleanCancellation, queued.cancel()), isTrue);
      expect(await booleanCancellation, isTrue);
      await cancelledResult;
      expect((await first.result).tensor('Y').copyFloat32Data(), <double>[1]);
      expect(
        await first.cancelWithDisposition(),
        OrtRunCancellationDisposition.notCancelled,
      );
      expect(await first.cancel(), isFalse);
      await worker.close();
    });

    test('applies bounded queue backpressure', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'delay',
            maxPendingRuns: 1,
          );
      final OrtIsolateRun active = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[1]),
        },
      );
      expect(
        () => worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[2]),
          },
        ),
        throwsA(isA<OrtWorkerQueueFullException>()),
      );
      await active.result;
      await worker.close();
    });

    test('rejects excess outputs before reading caller elements', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting();
      final OrtSessionPool pool = createOrtSessionPoolForTesting(
        <OrtIsolateSession>[worker],
      );
      final _UnreadableList<String> outputs = _UnreadableList<String>(2);
      try {
        expect(
          () => worker.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[1]),
            },
            outputNames: outputs,
          ),
          throwsArgumentError,
        );
        expect(outputs.elementReads, 0);
        expect(worker.outstandingRuns, 0);

        expect(
          () => pool.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[2]),
            },
            outputNames: outputs,
          ),
          throwsArgumentError,
        );
        expect(outputs.elementReads, 0);
        expect(pool.outstandingRuns, 0);
      } finally {
        await pool.close();
      }
    });

    test('bounds aggregate outstanding input bytes', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'delay',
            maxPendingRuns: 3,
            maxMessageBytes: 1024,
            maxOutstandingInputBytes: 26,
          );
      final OrtIsolateRun first = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[1]),
        },
      );
      final OrtIsolateRun second = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[2]),
        },
      );
      expect(worker.outstandingInputBytes, 26);
      expect(worker.availableInputBytes, 0);
      expect(
        () => worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[3]),
          },
        ),
        throwsA(
          isA<OrtWorkerQueueFullException>()
              .having(
                (OrtWorkerQueueFullException error) =>
                    error.context['maxOutstandingInputBytes'],
                'aggregate bound',
                26,
              )
              .having(
                (OrtWorkerQueueFullException error) =>
                    error.context['outstandingInputBytes'],
                'reserved bytes',
                26,
              )
              .having(
                (OrtWorkerQueueFullException error) =>
                    error.context['requestedInputBytes'],
                'requested bytes',
                13,
              ),
        ),
      );

      await first.result;
      expect(worker.outstandingInputBytes, 13);
      final OrtIsolateRun third = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[3]),
        },
      );
      expect(worker.outstandingInputBytes, 26);
      await Future.wait<OrtIsolateRunResult>(<Future<OrtIsolateRunResult>>[
        second.result,
        third.result,
      ]);
      expect(worker.outstandingInputBytes, 0);
      await worker.close();
    });

    test(
      'holds input bytes and queued work until result run state is disposed',
      () async {
        final ReceivePort lifecyclePort = ReceivePort();
        final Map<int, Completer<SendPort>> gates = <int, Completer<SendPort>>{
          1: Completer<SendPort>(),
          2: Completer<SendPort>(),
        };
        final StreamSubscription<Object?> subscription = lifecyclePort.listen((
          Object? event,
        ) {
          if (event case <Object?, Object?>{
            'type': 'runStateGate',
            'requestId': final int requestId,
            'port': final SendPort port,
          }) {
            gates[requestId]?.complete(port);
          }
        });
        final OrtIsolateSession worker =
            await spawnOrtIsolateProtocolHarnessForTesting(
              scenario: 'cleanupGateResult',
              startupLifecyclePort: lifecyclePort.sendPort,
              maxPendingRuns: 2,
              maxMessageBytes: 1024,
              maxOutstandingInputBytes: 26,
            );
        try {
          final OrtIsolateRun first = worker.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[1]),
            },
          );
          final OrtIsolateRun second = worker.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[2]),
            },
          );
          var firstSettled = false;
          final Future<OrtIsolateRunResult> firstResult = first.result
              .whenComplete(() {
                firstSettled = true;
              });

          final SendPort firstGate = await gates[1]!.future.timeout(
            const Duration(seconds: 2),
          );
          await Future<void>.delayed(const Duration(milliseconds: 30));
          expect(firstSettled, isFalse);
          expect(gates[2]!.isCompleted, isFalse);
          expect(worker.outstandingInputBytes, 26);

          firstGate.send('disposed');
          expect((await firstResult).tensor('Y').copyFloat32Data(), <double>[
            1,
          ]);
          expect(worker.outstandingInputBytes, 13);

          final SendPort secondGate = await gates[2]!.future.timeout(
            const Duration(seconds: 2),
          );
          secondGate.send('disposed');
          expect((await second.result).tensor('Y').copyFloat32Data(), <double>[
            2,
          ]);
          expect(worker.outstandingInputBytes, 0);
        } finally {
          await worker.close();
          await subscription.cancel();
          lifecyclePort.close();
        }
      },
    );

    test('holds input bytes until error run state is disposed', () async {
      final ReceivePort lifecyclePort = ReceivePort();
      final Completer<SendPort> gate = Completer<SendPort>();
      final StreamSubscription<Object?> subscription = lifecyclePort.listen((
        Object? event,
      ) {
        if (event case <Object?, Object?>{
          'type': 'runStateGate',
          'port': final SendPort port,
        }) {
          gate.complete(port);
        }
      });
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'cleanupGateOrtError',
            startupLifecyclePort: lifecyclePort.sendPort,
            maxMessageBytes: 1024,
            maxOutstandingInputBytes: 13,
          );
      try {
        final OrtIsolateRun run = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        var settled = false;
        final Future<void> settlement = run.result.then<void>(
          (_) => fail('The synthetic ORT error unexpectedly succeeded.'),
          onError: (Object error, StackTrace stackTrace) {
            expect(error, isA<OrtRunException>());
            settled = true;
          },
        );
        final SendPort cleanupGate = await gate.future.timeout(
          const Duration(seconds: 2),
        );
        await Future<void>.delayed(const Duration(milliseconds: 30));
        expect(settled, isFalse);
        expect(worker.outstandingInputBytes, 13);

        cleanupGate.send('disposed');
        await settlement;
        expect(settled, isTrue);
        expect(worker.outstandingInputBytes, 0);
      } finally {
        await worker.close();
        await subscription.cancel();
        lifecyclePort.close();
      }
    });

    test(
      'queued cancellation immediately releases its byte reservation',
      () async {
        final OrtIsolateSession worker =
            await spawnOrtIsolateProtocolHarnessForTesting(
              scenario: 'delay',
              maxPendingRuns: 3,
              maxMessageBytes: 1024,
              maxOutstandingInputBytes: 26,
            );
        final OrtIsolateRun active = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        final OrtIsolateRun queued = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[2]),
          },
        );
        final Future<void> cancelledResult = expectLater(
          queued.result,
          throwsA(isA<OrtRunCancelledException>()),
        );
        expect(
          await queued.cancelWithDisposition(),
          OrtRunCancellationDisposition.queuedRunRemoved,
        );
        await cancelledResult;
        expect(worker.outstandingInputBytes, 13);
        final OrtIsolateRun replacement = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[3]),
          },
        );
        await Future.wait<OrtIsolateRunResult>(<Future<OrtIsolateRunResult>>[
          active.result,
          replacement.result,
        ]);
        expect(worker.outstandingInputBytes, 0);
        await worker.close();
      },
    );

    test('rejects input that can never fit the aggregate bound', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            maxMessageBytes: 1024,
            maxOutstandingInputBytes: 12,
          );
      try {
        expect(
          () => worker.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[1]),
            },
          ),
          throwsA(
            isA<OrtWorkerMessageTooLargeException>()
                .having(
                  (OrtWorkerMessageTooLargeException error) =>
                      error.context['maxOutstandingInputBytes'],
                  'aggregate bound',
                  12,
                )
                .having(
                  (OrtWorkerMessageTooLargeException error) =>
                      error.context['requestedInputBytes'],
                  'requested bytes',
                  13,
                ),
          ),
        );
        expect(worker.outstandingInputBytes, 0);
      } finally {
        await worker.close();
      }
    });

    test('validates the aggregate input-byte configuration', () {
      expect(
        () => spawnOrtIsolateProtocolHarnessForTesting(
          maxOutstandingInputBytes: 0,
        ),
        throwsRangeError,
      );
      expect(
        () => spawnOrtIsolateProtocolHarnessForTesting(
          maxOutstandingInputBytes: 1024 * 1024 * 1024 + 1,
        ),
        throwsRangeError,
      );
    });

    test('pool distributes load and preserves bounded capacity', () async {
      final OrtIsolateSession first =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'delay',
            maxPendingRuns: 1,
          );
      final OrtIsolateSession second =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'delay',
            maxPendingRuns: 1,
          );
      final OrtSessionPool pool = createOrtSessionPoolForTesting(
        <OrtIsolateSession>[first, second],
      );
      try {
        final OrtIsolateRun firstRun = pool.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        final OrtIsolateRun secondRun = pool.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[2]),
          },
        );
        expect(pool.outstandingRuns, 2);
        expect(
          () => pool.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[3]),
            },
          ),
          throwsA(isA<OrtWorkerQueueFullException>()),
        );
        expect((await firstRun.result).tensor('Y').copyFloat32Data(), <double>[
          1,
        ]);
        expect((await secondRun.result).tensor('Y').copyFloat32Data(), <double>[
          2,
        ]);
      } finally {
        await pool.close();
      }
    });

    test('pool skips a worker without input-byte capacity', () async {
      final OrtIsolateSession first =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'delay',
            maxPendingRuns: 2,
            maxMessageBytes: 1024,
            maxOutstandingInputBytes: 13,
          );
      final OrtIsolateSession second =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'delay',
            maxPendingRuns: 2,
            maxMessageBytes: 1024,
            maxOutstandingInputBytes: 26,
          );
      final OrtIsolateRun firstRun = first.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[1]),
        },
      );
      final OrtSessionPool pool = createOrtSessionPoolForTesting(
        <OrtIsolateSession>[first, second],
      );
      try {
        final OrtIsolateRun routed = pool.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[2]),
          },
        );
        expect(first.outstandingRuns, 1);
        expect(second.outstandingRuns, 1);
        expect(pool.outstandingInputBytes, 26);
        await Future.wait<OrtIsolateRunResult>(<Future<OrtIsolateRunResult>>[
          firstRun.result,
          routed.result,
        ]);
        expect(pool.outstandingInputBytes, 0);
      } finally {
        await pool.close();
      }
    });

    test(
      'pool reports a terminal worker failure instead of backpressure',
      () async {
        final OrtIsolateSession crashing =
            await spawnOrtIsolateProtocolHarnessForTesting(scenario: 'crash');
        final OrtSessionPool pool = createOrtSessionPoolForTesting(
          <OrtIsolateSession>[crashing],
        );
        await expectLater(
          pool.run(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[1]),
            },
          ),
          throwsA(isA<OrtWorkerCrashedException>()),
        );
        expect(
          () => pool.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[2]),
            },
          ),
          throwsA(isA<OrtWorkerCrashedException>()),
        );
        await expectLater(
          pool.close(),
          throwsA(isA<OrtWorkerCrashedException>()),
        );
      },
    );

    test('keeps the caller isolate responsive while worker waits', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(scenario: 'delay');
      var timerFired = false;
      final Timer timer = Timer(const Duration(milliseconds: 20), () {
        timerFired = true;
      });
      try {
        final Future<OrtIsolateRunResult> run = worker.run(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        await Future<void>.delayed(const Duration(milliseconds: 60));
        expect(timerFired, isTrue);
        await run;
      } finally {
        timer.cancel();
        await worker.close();
      }
    });

    test('does not relabel an unrelated ORT error as cancellation', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(scenario: 'ortError');
      try {
        final OrtIsolateRun run = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        final Future<OrtRunCancellationDisposition> cancellation = run
            .cancelWithDisposition();
        await expectLater(run.result, throwsA(isA<OrtRunException>()));
        expect(await cancellation, OrtRunCancellationDisposition.notCancelled);
        expect(await run.cancel(), isFalse);
      } finally {
        await worker.close();
      }
    });

    test('reports an accepted active native termination request', () async {
      final List<int> requestedTokens = <int>[];
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'syntheticCancelToken',
            requestCancelToken: (int token) {
              requestedTokens.add(token);
              return true;
            },
          );
      try {
        final OrtIsolateRun run = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        final Future<bool> booleanCancellation = run.cancel();
        expect(await booleanCancellation, isTrue);
        expect(
          await run.cancelWithDisposition(),
          OrtRunCancellationDisposition.nativeTerminationRequested,
        );
        expect(requestedTokens, <int>[1]);
        expect((await run.result).tensor('Y').copyFloat32Data(), <double>[1]);
      } finally {
        await worker.close();
      }
    });

    test('ignores stale replies without contaminating a newer run', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(scenario: 'stale');
      try {
        await worker.run(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        final OrtIsolateRunResult second = await worker.run(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[2]),
          },
        );
        expect(second.tensor('Y').copyFloat32Data(), <double>[2]);
      } finally {
        await worker.close();
      }
    });

    test('rejects a future reply while that request is queued', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'futureReply',
            maxPendingRuns: 2,
          );
      final OrtIsolateRun active = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[1]),
        },
      );
      final OrtIsolateRun queued = worker.startRun(
        inputs: <String, OrtIsolateValue>{
          'X': _tensor(<double>[2]),
        },
      );
      try {
        final Matcher terminalProtocolFailure =
            isA<OrtWorkerProtocolException>().having(
              (OrtWorkerProtocolException error) => error.message,
              'message',
              'The worker sent a malformed versioned message.',
            );
        final Future<void> activeFailure = expectLater(
          active.result.timeout(const Duration(seconds: 2)),
          throwsA(terminalProtocolFailure),
        );
        final Future<void> queuedFailure = expectLater(
          queued.result.timeout(const Duration(seconds: 2)),
          throwsA(terminalProtocolFailure),
        );
        await Future.wait<void>(<Future<void>>[activeFailure, queuedFailure]);
        await expectLater(worker.close(), throwsA(terminalProtocolFailure));
        expect(worker.outstandingInputBytes, 0);
        expect(worker.isClosed, isTrue);
      } finally {
        try {
          await worker.close();
        } on OrtWorkerException {
          // The fixed terminal protocol result remains authoritative.
        }
      }
    });

    test('distinguishes startup, crash, and protocol failures', () async {
      await expectLater(
        spawnOrtIsolateProtocolHarnessForTesting(scenario: 'startupError'),
        throwsA(isA<OrtWorkerStartupException>()),
      );
      await expectLater(
        spawnOrtIsolateProtocolHarnessForTesting(scenario: 'startupExit'),
        throwsA(isA<OrtWorkerStartupException>()),
      );

      final OrtIsolateSession crashing =
          await spawnOrtIsolateProtocolHarnessForTesting(scenario: 'crash');
      await expectLater(
        crashing.run(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        ),
        throwsA(isA<OrtWorkerCrashedException>()),
      );
      await expectLater(
        crashing.close(),
        throwsA(isA<OrtWorkerCrashedException>()),
      );

      final OrtIsolateSession malformed =
          await spawnOrtIsolateProtocolHarnessForTesting(scenario: 'malformed');
      await expectLater(
        malformed.run(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        ),
        throwsA(isA<OrtWorkerProtocolException>()),
      );
      await expectLater(
        malformed.close(),
        throwsA(isA<OrtWorkerProtocolException>()),
      );

      for (final String scenario in <String>['missingField', 'unknownField']) {
        final OrtIsolateSession invalid =
            await spawnOrtIsolateProtocolHarnessForTesting(scenario: scenario);
        await expectLater(
          invalid.run(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[1]),
            },
          ),
          throwsA(isA<OrtWorkerProtocolException>()),
        );
        await expectLater(
          invalid.close(),
          throwsA(isA<OrtWorkerProtocolException>()),
        );
      }
    });

    test(
      'malformed reply fails the run before ordered worker retirement',
      () async {
        final ReceivePort lifecyclePort = ReceivePort();
        final Completer<SendPort> runStateGate = Completer<SendPort>();
        final Completer<void> workerDisposed = Completer<void>();
        final List<String> workerEvents = <String>[];
        final StreamSubscription<Object?> subscription = lifecyclePort.listen((
          Object? event,
        ) {
          if (event is Map<Object?, Object?> &&
              event['type'] == 'runStateGate' &&
              event['port'] is SendPort) {
            runStateGate.complete(event['port']! as SendPort);
          } else if (event is String) {
            workerEvents.add(event);
            if (event == 'disposed' && !workerDisposed.isCompleted) {
              workerDisposed.complete();
            }
          }
        });
        try {
          final OrtIsolateSession worker =
              await spawnOrtIsolateProtocolHarnessForTesting(
                scenario: 'malformedWhileOwned',
                startupLifecyclePort: lifecyclePort.sendPort,
              );
          final OrtSessionPool pool = createOrtSessionPoolForTesting(
            <OrtIsolateSession>[worker],
          );
          final OrtIsolateRun run = pool.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[1]),
            },
          );
          final SendPort gate = await runStateGate.future.timeout(
            const Duration(seconds: 2),
          );
          await expectLater(
            run.result,
            throwsA(isA<OrtWorkerProtocolException>()),
          );
          expect(worker.outstandingInputBytes, 13);
          expect(pool.outstandingInputBytes, 13);

          var closeSettled = false;
          final Future<void> closing = pool.close().whenComplete(() {
            closeSettled = true;
          });
          await Future<void>.delayed(const Duration(milliseconds: 30));
          expect(closeSettled, isFalse);
          expect(workerEvents, isEmpty);

          gate.send('dispose');
          await workerDisposed.future.timeout(const Duration(seconds: 2));
          await expectLater(
            closing,
            throwsA(isA<OrtWorkerProtocolException>()),
          );
          expect(workerEvents, <String>[
            'runStateDisposed',
            'closeReceived',
            'disposed',
          ]);
          expect(worker.outstandingInputBytes, 0);
          expect(pool.outstandingInputBytes, 0);
          expect(worker.isClosed, isTrue);
        } finally {
          await subscription.cancel();
          lifecyclePort.close();
        }
      },
    );

    test(
      'worker terminal replies are acknowledged before exit can overtake them',
      () async {
        for (final (String scenario, String message) in <(String, String)>[
          ('fatalProtocolReply', 'Synthetic fatal worker protocol failure.'),
          ('fatalWorkerReply', 'Synthetic fatal native cleanup failure.'),
        ]) {
          final ReceivePort lifecyclePort = ReceivePort();
          final Completer<void> acknowledgementReceived = Completer<void>();
          final StreamSubscription<Object?> subscription = lifecyclePort.listen(
            (Object? event) {
              if (event == 'terminalAcknowledgementReceived' &&
                  !acknowledgementReceived.isCompleted) {
                acknowledgementReceived.complete();
              }
            },
          );
          try {
            final OrtIsolateSession worker =
                await spawnOrtIsolateProtocolHarnessForTesting(
                  scenario: scenario,
                  startupLifecyclePort: lifecyclePort.sendPort,
                );
            final Matcher exactTerminalError = isA<OrtWorkerProtocolException>()
                .having(
                  (OrtWorkerProtocolException error) => error.message,
                  'worker-authored terminal message',
                  message,
                );
            await expectLater(
              worker.run(
                inputs: <String, OrtIsolateValue>{
                  'X': _tensor(<double>[1]),
                },
              ),
              throwsA(exactTerminalError),
            );
            await acknowledgementReceived.future.timeout(
              const Duration(seconds: 2),
            );
            await expectLater(worker.close(), throwsA(exactTerminalError));
            expect(worker.outstandingInputBytes, 0);
            expect(worker.isClosed, isTrue);
          } finally {
            await subscription.cancel();
            lifecyclePort.close();
          }
        }
      },
    );

    test(
      'stale fatal worker reply cannot enter stale-result suppression',
      () async {
        final ReceivePort lifecyclePort = ReceivePort();
        final Completer<void> acknowledgementReceived = Completer<void>();
        final StreamSubscription<Object?> subscription = lifecyclePort.listen((
          Object? event,
        ) {
          if (event == 'terminalAcknowledgementReceived' &&
              !acknowledgementReceived.isCompleted) {
            acknowledgementReceived.complete();
          }
        });
        try {
          final OrtIsolateSession worker =
              await spawnOrtIsolateProtocolHarnessForTesting(
                scenario: 'fatalWorkerStaleReply',
                startupLifecyclePort: lifecyclePort.sendPort,
              );
          expect(
            (await worker.run(
              inputs: <String, OrtIsolateValue>{
                'X': _tensor(<double>[1]),
              },
            )).tensor('Y').copyFloat32Data(),
            <double>[1],
          );
          final Matcher malformedTerminal = isA<OrtWorkerProtocolException>()
              .having(
                (OrtWorkerProtocolException error) => error.message,
                'controller terminal protocol error',
                'The worker sent a malformed versioned message.',
              );
          await expectLater(
            worker.run(
              inputs: <String, OrtIsolateValue>{
                'X': _tensor(<double>[2]),
              },
            ),
            throwsA(malformedTerminal),
          );
          await acknowledgementReceived.future.timeout(
            const Duration(seconds: 2),
          );
          await expectLater(worker.close(), throwsA(malformedTerminal));
          expect(worker.outstandingInputBytes, 0);
          expect(worker.isClosed, isTrue);
        } finally {
          await subscription.cancel();
          lifecyclePort.close();
        }
      },
    );

    test('worker exit cannot replace the required close receipt', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'closeWithoutReceipt',
          );
      await expectLater(
        worker.close(),
        throwsA(isA<OrtWorkerCrashedException>()),
      );
      expect(worker.isClosed, isTrue);
    });

    test(
      'parent installs timer and isolate ownership before worker resumes',
      () async {
        (bool, bool, bool, bool)? parentState;
        var observationCount = 0;
        final OrtIsolateSession worker =
            await spawnOrtIsolateProtocolHarnessForTesting(
              onParentState:
                  (
                    bool isolateAssigned,
                    bool timerActive,
                    bool readyCompleted,
                    bool resumeCapabilityAvailable,
                  ) {
                    observationCount += 1;
                    parentState = (
                      isolateAssigned,
                      timerActive,
                      readyCompleted,
                      resumeCapabilityAvailable,
                    );
                  },
            );
        try {
          expect(observationCount, 1);
          expect(parentState, (true, true, false, true));
        } finally {
          await worker.close();
        }
      },
    );

    test('startup timeout includes the isolate spawn future', () async {
      final Completer<void> spawnGate = Completer<void>();
      final ReceivePort lifecyclePort = ReceivePort();
      final Completer<void> controllerClosed = Completer<void>();
      final Completer<void> latePausedIsolateKilled = Completer<void>();
      final List<String> workerEvents = <String>[];
      final List<String> controllerEvents = <String>[];
      final StreamSubscription<Object?> lifecycleSubscription = lifecyclePort
          .listen((Object? event) {
            if (event is String) {
              workerEvents.add(event);
            }
          });
      void observeController(String event) {
        controllerEvents.add(event);
        if (event == 'connectionsClosed' && !controllerClosed.isCompleted) {
          controllerClosed.complete();
        }
        if (event == 'latePausedIsolateKilled' &&
            !latePausedIsolateKilled.isCompleted) {
          latePausedIsolateKilled.complete();
        }
      }

      try {
        final Stopwatch elapsed = Stopwatch()..start();
        final Future<OrtIsolateSession> spawning =
            spawnOrtIsolateProtocolHarnessForTesting(
              startupLifecyclePort: lifecyclePort.sendPort,
              spawnGate: spawnGate.future,
              onControllerEvent: observeController,
              startupTimeout: const Duration(milliseconds: 30),
            );
        await expectLater(
          spawning,
          throwsA(
            isA<OrtWorkerStartupException>().having(
              (OrtWorkerStartupException error) =>
                  error.context['startupTimeoutMilliseconds'],
              'bounded timeout',
              30,
            ),
          ),
        );
        elapsed.stop();
        expect(elapsed.elapsed, lessThan(const Duration(seconds: 1)));
        await controllerClosed.future.timeout(const Duration(seconds: 2));
        expect(controllerEvents, <String>[
          'callerAbandoned',
          'connectionsClosed',
        ]);
        expect(workerEvents, isEmpty);

        spawnGate.complete();
        await latePausedIsolateKilled.future.timeout(
          const Duration(seconds: 2),
        );
        expect(workerEvents, isEmpty);
        expect(controllerEvents, <String>[
          'callerAbandoned',
          'connectionsClosed',
          'latePausedIsolateKilled',
        ]);
      } finally {
        if (!spawnGate.isCompleted) spawnGate.complete();
        await lifecycleSubscription.cancel();
        lifecyclePort.close();
      }
    });

    test(
      'late spawn failure cannot replace the authoritative startup timeout',
      () async {
        final Completer<void> spawnGate = Completer<void>();
        final ReceivePort lifecyclePort = ReceivePort();
        final List<String> workerEvents = <String>[];
        final List<String> controllerEvents = <String>[];
        final StreamSubscription<Object?> lifecycleSubscription = lifecyclePort
            .listen((Object? event) {
              if (event is String) workerEvents.add(event);
            });
        try {
          final Future<OrtIsolateSession> spawning =
              spawnOrtIsolateProtocolHarnessForTesting(
                startupLifecyclePort: lifecyclePort.sendPort,
                spawnGate: spawnGate.future,
                onControllerEvent: controllerEvents.add,
                startupTimeout: const Duration(milliseconds: 30),
              );
          await expectLater(
            spawning,
            throwsA(
              isA<OrtWorkerStartupException>().having(
                (OrtWorkerStartupException error) =>
                    error.context['startupTimeoutMilliseconds'],
                'original bounded timeout',
                30,
              ),
            ),
          );
          expect(controllerEvents, <String>[
            'callerAbandoned',
            'connectionsClosed',
          ]);

          spawnGate.completeError(StateError('synthetic late spawn failure'));
          await Future<void>.delayed(const Duration(milliseconds: 30));
          expect(controllerEvents, <String>[
            'callerAbandoned',
            'connectionsClosed',
          ]);
          expect(workerEvents, isEmpty);
        } finally {
          if (!spawnGate.isCompleted) {
            spawnGate.completeError(StateError('test cleanup'));
          }
          await lifecycleSubscription.cancel();
          lifecyclePort.close();
        }
      },
    );

    test(
      'successful readiness cancels startup timeout without closing session',
      () async {
        final List<String> controllerEvents = <String>[];
        final OrtIsolateSession worker =
            await spawnOrtIsolateProtocolHarnessForTesting(
              startupTimeout: const Duration(milliseconds: 500),
              onControllerEvent: controllerEvents.add,
            );
        try {
          await Future<void>.delayed(const Duration(milliseconds: 550));
          expect(controllerEvents, isEmpty);
          final OrtIsolateRunResult result = await worker.run(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[7]),
            },
          );
          expect(result.tensor('Y').copyFloat32Data(), <double>[7]);
        } finally {
          await worker.close();
        }
      },
    );

    test(
      'startup timeout abandons the caller but gracefully retires late ready',
      () async {
        final ReceivePort lifecyclePort = ReceivePort();
        final Completer<SendPort> startupGate = Completer<SendPort>();
        final Completer<void> workerDisposed = Completer<void>();
        final Completer<void> controllerClosed = Completer<void>();
        final List<String> workerEvents = <String>[];
        final List<String> controllerEvents = <String>[];
        final StreamSubscription<Object?> lifecycleSubscription = lifecyclePort
            .listen((Object? event) {
              if (event is Map<Object?, Object?> &&
                  event['type'] == 'startupGate' &&
                  event['port'] is SendPort) {
                startupGate.complete(event['port']! as SendPort);
                return;
              }
              if (event is String) {
                workerEvents.add(event);
                if (event == 'disposed' && !workerDisposed.isCompleted) {
                  workerDisposed.complete();
                }
              }
            });
        void observeController(String event) {
          controllerEvents.add(event);
          if (event == 'connectionsClosed' && !controllerClosed.isCompleted) {
            controllerClosed.complete();
          }
        }

        try {
          final Future<OrtIsolateSession> spawning =
              spawnOrtIsolateProtocolHarnessForTesting(
                scenario: 'startupGateReady',
                startupLifecyclePort: lifecyclePort.sendPort,
                onControllerEvent: observeController,
                startupTimeout: const Duration(milliseconds: 30),
              );
          final SendPort gate = await startupGate.future.timeout(
            const Duration(seconds: 2),
          );
          await expectLater(
            spawning,
            throwsA(
              isA<OrtWorkerStartupException>()
                  .having(
                    (OrtWorkerStartupException error) =>
                        error.context['startupTimeoutMilliseconds'],
                    'bounded timeout',
                    30,
                  )
                  .having(
                    (OrtWorkerStartupException error) => error.message,
                    'message',
                    contains('bounded wait'),
                  ),
            ),
          );

          expect(controllerEvents, <String>[
            'callerAbandoned',
            'gracefulCloseSent',
          ]);
          gate.send('continue');
          await Future.wait<void>(<Future<void>>[
            workerDisposed.future,
            controllerClosed.future,
          ]).timeout(const Duration(seconds: 2));

          expect(workerEvents, <String>['closeReceived', 'disposed']);
          expect(controllerEvents, <String>[
            'callerAbandoned',
            'gracefulCloseSent',
            'lateReady',
            'connectionsClosed',
          ]);
          expect(
            controllerEvents.where(
              (String event) => event == 'connectionsClosed',
            ),
            hasLength(1),
          );
        } finally {
          await lifecycleSubscription.cancel();
          lifecyclePort.close();
        }
      },
    );

    for (final String scenario in <String>[
      'startupGateError',
      'startupGateExit',
    ]) {
      test(
        'startup timeout race with ${scenario == 'startupGateError' ? 'error' : 'exit'} closes controller resources once',
        () async {
          final ReceivePort lifecyclePort = ReceivePort();
          final Completer<SendPort> startupGate = Completer<SendPort>();
          final Completer<void> controllerClosed = Completer<void>();
          final List<String> controllerEvents = <String>[];
          final StreamSubscription<Object?> lifecycleSubscription =
              lifecyclePort.listen((Object? event) {
                if (event is Map<Object?, Object?> &&
                    event['type'] == 'startupGate' &&
                    event['port'] is SendPort) {
                  startupGate.complete(event['port']! as SendPort);
                }
              });
          void observeController(String event) {
            controllerEvents.add(event);
            if (event == 'connectionsClosed' && !controllerClosed.isCompleted) {
              controllerClosed.complete();
            }
          }

          try {
            final Future<OrtIsolateSession> spawning =
                spawnOrtIsolateProtocolHarnessForTesting(
                  scenario: scenario,
                  startupLifecyclePort: lifecyclePort.sendPort,
                  onControllerEvent: observeController,
                  startupTimeout: const Duration(milliseconds: 30),
                );
            final SendPort gate = await startupGate.future.timeout(
              const Duration(seconds: 2),
            );
            await expectLater(
              spawning,
              throwsA(
                isA<OrtWorkerStartupException>().having(
                  (OrtWorkerStartupException error) => error.message,
                  'message',
                  contains('bounded wait'),
                ),
              ),
            );
            gate.send('continue');
            await controllerClosed.future.timeout(const Duration(seconds: 2));

            expect(controllerEvents, <String>[
              'callerAbandoned',
              'gracefulCloseSent',
              'connectionsClosed',
            ]);
            expect(
              controllerEvents.where(
                (String event) => event == 'gracefulCloseSent',
              ),
              hasLength(1),
            );
          } finally {
            await lifecycleSubscription.cancel();
            lifecyclePort.close();
          }
        },
      );
    }

    for (final (String scenario, String label) in <(String, String)>[
      ('startupReadyMissingCommandPort', 'missing command port'),
      ('startupReadyWrongCommandPort', 'wrong command port'),
      ('startupMalformedReady', 'malformed diagnostics'),
    ]) {
      test(
        'startup $label retires through authoritative ownership once',
        () async {
          final ReceivePort lifecyclePort = ReceivePort();
          final Completer<void> workerDisposed = Completer<void>();
          final Completer<void> controllerClosed = Completer<void>();
          final List<String> workerEvents = <String>[];
          final List<String> controllerEvents = <String>[];
          final StreamSubscription<Object?> lifecycleSubscription =
              lifecyclePort.listen((Object? event) {
                if (event is String) {
                  workerEvents.add(event);
                  if (event == 'disposed' && !workerDisposed.isCompleted) {
                    workerDisposed.complete();
                  }
                }
              });
          void observeController(String event) {
            controllerEvents.add(event);
            if (event == 'connectionsClosed' && !controllerClosed.isCompleted) {
              controllerClosed.complete();
            }
          }

          try {
            await expectLater(
              spawnOrtIsolateProtocolHarnessForTesting(
                scenario: scenario,
                startupLifecyclePort: lifecyclePort.sendPort,
                onControllerEvent: observeController,
              ),
              throwsA(isA<OrtWorkerProtocolException>()),
            );
            await Future.wait<void>(<Future<void>>[
              workerDisposed.future,
              controllerClosed.future,
            ]).timeout(const Duration(seconds: 2));

            expect(workerEvents, <String>['closeReceived', 'disposed']);
            expect(controllerEvents, <String>[
              'callerAbandoned',
              'gracefulCloseSent',
              'connectionsClosed',
            ]);
            expect(
              controllerEvents.where(
                (String event) => event == 'gracefulCloseSent',
              ),
              hasLength(1),
            );
            expect(
              controllerEvents.where(
                (String event) => event == 'connectionsClosed',
              ),
              hasLength(1),
            );
          } finally {
            await lifecycleSubscription.cancel();
            lifecyclePort.close();
          }
        },
      );
    }

    test(
      'close is graceful, idempotent, and races an active run safely',
      () async {
        final OrtIsolateSession worker =
            await spawnOrtIsolateProtocolHarnessForTesting(scenario: 'delay');
        final OrtIsolateRun active = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        expect(worker.outstandingInputBytes, 13);
        final Future<void> firstClose = worker.close();
        final Future<void> secondClose = worker.close();
        expect(identical(firstClose, secondClose), isTrue);
        await active.result;
        expect(worker.outstandingInputBytes, 0);
        await firstClose;
        expect(worker.isClosed, isTrue);
        expect(
          () => worker.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': _tensor(<double>[2]),
            },
          ),
          throwsA(isA<OrtWorkerClosedException>()),
        );
      },
    );

    test(
      'close consumes native cancellation failure and still drains the run',
      () async {
        final List<Object> uncaughtErrors = <Object>[];
        final List<int> requestedTokens = <int>[];
        final Future<void> closeFlow = runZonedGuarded<Future<void>>(
          () async {
            final OrtIsolateSession worker =
                await spawnOrtIsolateProtocolHarnessForTesting(
                  scenario: 'syntheticCancelToken',
                  requestCancelToken: (int token) {
                    requestedTokens.add(token);
                    throw StateError('Synthetic native cancellation failure.');
                  },
                );
            final OrtIsolateRun active = worker.startRun(
              inputs: <String, OrtIsolateValue>{
                'X': _tensor(<double>[1]),
              },
            );

            final Future<void> closing = worker.close();
            expect(
              (await active.result).tensor('Y').copyFloat32Data(),
              <double>[1],
            );
            await closing;
            expect(worker.isClosed, isTrue);
          },
          (Object error, StackTrace stackTrace) {
            uncaughtErrors.add(error);
          },
        )!;

        await closeFlow.timeout(const Duration(seconds: 2));
        await Future<void>.delayed(Duration.zero);
        expect(requestedTokens, <int>[1]);
        expect(uncaughtErrors, isEmpty);
      },
    );

    test('rejects messages above the configured byte bound', () async {
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(maxMessageBytes: 32);
      try {
        expect(
          () => worker.startRun(
            inputs: <String, OrtIsolateValue>{
              'X': OrtIsolateTensor.fromUint8List(
                values: Uint8List(64),
                shape: const <int>[64],
              ),
            },
          ),
          throwsA(isA<OrtWorkerMessageTooLargeException>()),
        );
      } finally {
        await worker.close();
      }
    });
  });

  final String? fakeRuntimePath =
      Platform.environment['FONIX_TEST_FAKE_ORT_PATH'];
  test(
    'native cancellation registry is shared across Dart isolates',
    () async {
      final String normalizedRuntime = p.normalize(fakeRuntimePath!);
      final OrtIsolateSession worker =
          await spawnOrtIsolateProtocolHarnessForTesting(
            scenario: 'nativeCancelRegistry',
            runtimeLibraryPath: normalizedRuntime,
          );
      try {
        final OrtIsolateRun run = worker.startRun(
          inputs: <String, OrtIsolateValue>{
            'X': _tensor(<double>[1]),
          },
        );
        final Future<OrtRunCancellationDisposition> cancellation = run
            .cancelWithDisposition();
        await expectLater(run.result, throwsA(isA<OrtRunCancelledException>()));
        expect(
          await cancellation,
          OrtRunCancellationDisposition.nativeTerminationRequested,
        );
        expect(await run.cancel(), isTrue);
      } finally {
        await worker.close();
      }
    },
    skip: fakeRuntimePath == null
        ? 'Set FONIX_TEST_FAKE_ORT_PATH to the strict native fake runtime.'
        : false,
  );

  final String? runtimePath = Platform.environment['FONIX_TEST_REAL_ORT_PATH'];
  test(
    'real worker owns runtime/session and runs the CPU fixture',
    () async {
      final String normalizedRuntime = p.normalize(runtimePath!);
      final String fixtureRoot = p.normalize(p.absolute('test', 'fixtures'));
      final Directory artifactRoot = Directory.systemTemp.createTempSync(
        'fonix-worker-evidence-',
      );
      final OrtIsolateSession worker = await OrtIsolateSession.spawn(
        runtimeSource: OrtRuntimeSource.file(
          absolutePath: normalizedRuntime,
          allowedRoot: p.dirname(normalizedRuntime),
        ),
        model: OrtModelSource.file(
          absolutePath: p.join(fixtureRoot, 'mul_1.onnx'),
          allowedRoot: fixtureRoot,
        ),
        options: OrtSessionOptions(
          artifactRoot: artifactRoot.path,
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.cpu(
              requirement: OrtProviderRequirement.requireFullAssignment,
            ),
          ],
          fallbackPolicy: OrtFallbackPolicy.rejectAny,
        ),
      );
      try {
        expect(worker.diagnostics.runtimeVersion, '1.27.1');
        expect(
          worker.diagnostics.providers
              .singleWhere(
                (OrtProviderDiagnostics value) => value.wrapperId == 'cpu',
              )
              .active,
          isNull,
        );
        final OrtIsolateRunResult result = await worker.run(
          inputs: <String, OrtIsolateValue>{
            'X': OrtIsolateTensor.fromFloat32List(
              values: Float32List.fromList(<double>[1, 2, 3, 4, 5, 6]),
              shape: const <int>[3, 2],
            ),
          },
        );
        expect(result.tensor('Y').copyFloat32Data(), <double>[
          1,
          4,
          9,
          16,
          25,
          36,
        ]);
        expect(result.providerEvidence?.isFullyAssignedTo('cpu'), isTrue);
        expect(
          result.diagnostics.providers
              .singleWhere(
                (OrtProviderDiagnostics value) => value.wrapperId == 'cpu',
              )
              .active,
          isTrue,
        );
        expect(
          result.providerDiagnostics
              .singleWhere(
                (OrtProviderDiagnostics value) => value.wrapperId == 'cpu',
              )
              .active,
          isTrue,
        );
      } finally {
        await worker.close();
        artifactRoot.deleteSync(recursive: true);
      }
    },
    skip: runtimePath == null
        ? 'Set FONIX_TEST_REAL_ORT_PATH to the exact ORT v1.27.1 runtime.'
        : false,
  );
}
