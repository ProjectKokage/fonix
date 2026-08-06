import 'dart:async';
import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_sherpa_reference/src/harness_contract.dart';
import 'package:fonix_sherpa_reference/src/qualification_state_machine.dart';

const List<int> _fonixBytes = <int>[0, 0, 128, 63];

void main() {
  for (final HarnessLoadOrder order in HarnessLoadOrder.values) {
    test(
      'runs the closed alternating state machine for ${order.wireValue}',
      () async {
        final _FakeDriverFactory factory = _FakeDriverFactory();
        final QualificationStateMachine machine = QualificationStateMachine(
          driverFactory: factory,
          publicationSink: AuthoritativeLifecyclePublicationSink(),
          pins: _pins(),
          references: QualificationReferences(
            fonixOutputBytes: _fonixBytes,
            sherpaBounds: _sherpaBounds(),
          ),
        );
        final HarnessLaunch launch = _launch(order);

        final DeviceQualificationResult result = await machine.run(launch);
        final Map<String, Object?> json = result.toJson();

        expect(json.keys.toSet(), <String>{
          'schemaVersion',
          'result',
          'launchChallengeSha256',
          'loadOrder',
          'runtime',
          'sherpa',
          'fixtures',
          'initialization',
          'workload',
          'lifecycle',
        });
        expect(json, isNot(containsPair('matrix', anything)));
        expect(json, isNot(containsPair('build', anything)));
        expect(json, isNot(containsPair('device', anything)));
        expect(json, isNot(containsPair('process', anything)));
        expect(json['result'], 'passed');
        expect(json['loadOrder'], order.wireValue);
        expect(json['launchChallengeSha256'], launch.launchChallengeSha256);
        final Map<String, Object?> runtime =
            json['runtime']! as Map<String, Object?>;
        expect(runtime, isNot(contains('ortSha256')));
        final Map<String, Object?> sherpa =
            json['sherpa']! as Map<String, Object?>;
        expect(sherpa, isNot(contains('packageVersion')));
        expect(sherpa, isNot(contains('sourceRevision')));
        expect(sherpa.keys, containsAll(<String>['getVersion', 'getGitSha1']));

        final Map<String, Object?> initialization =
            json['initialization']! as Map<String, Object?>;
        expect(
          initialization['events'],
          order == HarnessLoadOrder.dartFirst
              ? <String>['fonix-session-ready', 'sherpa-vad-ready']
              : <String>['sherpa-vad-ready', 'fonix-session-ready'],
        );
        expect(initialization['firstOwnerAliveWhenSecondReady'], isTrue);
        expect(
          factory.events.take(4),
          order == HarnessLoadOrder.dartFirst
              ? <String>[
                  'fonix1:create',
                  'fonix1:init',
                  'sherpa1:create',
                  'sherpa1:init',
                ]
              : <String>[
                  'sherpa1:create',
                  'sherpa1:init',
                  'fonix1:create',
                  'fonix1:init',
                ],
        );

        final Map<String, Object?> workload =
            json['workload']! as Map<String, Object?>;
        expect(workload['requestedCycles'], 2);
        expect(workload['completedCycles'], 2);
        final List<Object?> steps = workload['steps']! as List<Object?>;
        expect(steps, hasLength(4));
        expect(
          steps.map(
            (Object? step) => (step! as Map<String, Object?>)['engine'],
          ),
          <String>['fonix', 'sherpa', 'fonix', 'sherpa'],
        );
        expect(
          steps.map(
            (Object? step) => (step! as Map<String, Object?>)['ordinal'],
          ),
          <int>[1, 2, 3, 4],
        );

        final Map<String, Object?> lifecycle =
            json['lifecycle']! as Map<String, Object?>;
        expect(
          lifecycle['fonixCancellation'],
          containsPair('nativeRequestAcceptedCount', 1),
        );
        expect(
          lifecycle['sherpaCancellation'],
          containsPair('framesAcceptedBeforeRequest', 1),
        );
        expect(
          lifecycle['staleCompletion'],
          allOf(
            containsPair('retiredGeneration', 1),
            containsPair('authoritativeGeneration', 2),
          ),
        );
        expect(
          lifecycle['disposal'],
          allOf(
            containsPair('fonixSessionsCreated', 2),
            containsPair('fonixSessionsClosed', 2),
            containsPair('sherpaDetectorsCreated', 3),
            containsPair('sherpaDetectorsFreed', 3),
            containsPair('temporaryRootsRemaining', 0),
          ),
        );
        expect(factory.temporaryRootsCreated, 2);
        expect(factory.temporaryRootsRemaining, 0);
        expect(factory.fonixSettlementReads, 5);

        final int firstFonixRun = factory.events.indexOf(
          'fonix1:run:reference',
        );
        final int firstSherpaRun = factory.events.indexOf(
          'sherpa1:run:reference',
        );
        final int secondFonixRun = factory.events.indexOf(
          'fonix1:run:reference',
          firstFonixRun + 1,
        );
        final int secondSherpaRun = factory.events.indexOf(
          'sherpa1:run:reference',
          firstSherpaRun + 1,
        );
        expect(
          <int>[firstFonixRun, firstSherpaRun, secondFonixRun, secondSherpaRun],
          orderedEquals(
            <int>[
              firstFonixRun,
              firstSherpaRun,
              secondFonixRun,
              secondSherpaRun,
            ]..sort(),
          ),
        );
        expect(
          factory.events.indexOf('fonix1:close'),
          lessThan(factory.events.indexOf('sherpa2:close')),
        );
        expect(
          factory.events.indexOf('sherpa3:close'),
          lessThan(factory.events.indexOf('fonix2:close')),
        );
      },
    );
  }

  test(
    'rejects reference bytes that do not match the fixture identity',
    () async {
      final QualificationPins pins = _pins(
        fonixReferenceOutputSha256: _digest('f'),
      );
      final QualificationStateMachine machine = QualificationStateMachine(
        driverFactory: _FakeDriverFactory(),
        publicationSink: AuthoritativeLifecyclePublicationSink(),
        pins: pins,
        references: QualificationReferences(
          fonixOutputBytes: _fonixBytes,
          sherpaBounds: _sherpaBounds(),
        ),
      );

      await expectLater(
        machine.run(_launch(HarnessLoadOrder.dartFirst)),
        throwsA(
          isA<QualificationFailure>().having(
            (QualificationFailure failure) => failure.code,
            'code',
            'fonix-reference-identity-mismatch',
          ),
        ),
      );
    },
  );

  test('rejects incompatible or unrelated native identities', () {
    expect(
      () => _fonixInitialization(ortVersion: '2.27.0'),
      throwsA(isA<QualificationFailure>()),
    );
    expect(
      () => _fonixInitialization(ortVersion: '1.26.9'),
      throwsA(isA<QualificationFailure>()),
    );
    expect(
      () => SherpaInitializationObservation(
        getVersion: '1.13.4',
        getGitSha1: 'deadbe',
        profile: _sherpaProfile(),
        modelSha256: _digest('5'),
        audioSha256: _digest('6'),
        referenceSha256: _digest('7'),
      ),
      throwsA(isA<QualificationFailure>()),
    );
  });

  test(
    'partial initialization failure closes every created resource',
    () async {
      final _FakeDriverFactory factory = _FakeDriverFactory(
        failFirstSherpaInitialize: true,
      );
      final QualificationStateMachine machine = QualificationStateMachine(
        driverFactory: factory,
        publicationSink: AuthoritativeLifecyclePublicationSink(),
        pins: _pins(),
        references: QualificationReferences(
          fonixOutputBytes: _fonixBytes,
          sherpaBounds: _sherpaBounds(),
        ),
      );

      await expectLater(
        machine.run(_launch(HarnessLoadOrder.dartFirst)),
        throwsA(isA<StateError>()),
      );
      expect(factory.temporaryRootsCreated, 1);
      expect(factory.temporaryRootsRemaining, 0);
      expect(
        factory.events,
        containsAll(<String>['fonix1:close', 'sherpa1:close']),
      );
      expect(
        factory.events.where((String event) => event.contains(':run:')),
        isEmpty,
      );
    },
  );

  test('rejects adapter-observed fixture identity drift', () async {
    final _FakeDriverFactory factory = _FakeDriverFactory(
      driftFirstFonixObservation: true,
    );
    final QualificationStateMachine machine = QualificationStateMachine(
      driverFactory: factory,
      publicationSink: AuthoritativeLifecyclePublicationSink(),
      pins: _pins(),
      references: QualificationReferences(
        fonixOutputBytes: _fonixBytes,
        sherpaBounds: _sherpaBounds(),
      ),
    );

    await expectLater(
      machine.run(_launch(HarnessLoadOrder.dartFirst)),
      throwsA(
        isA<QualificationFailure>().having(
          (QualificationFailure failure) => failure.code,
          'code',
          'fixture-observation-mismatch',
        ),
      ),
    );
    expect(factory.temporaryRootsRemaining, 0);
  });

  test('rejects and cleans native work during driver construction', () async {
    final _FakeDriverFactory factory = _FakeDriverFactory(
      eagerFirstFonixConstruction: true,
    );
    final QualificationStateMachine machine = QualificationStateMachine(
      driverFactory: factory,
      publicationSink: AuthoritativeLifecyclePublicationSink(),
      pins: _pins(),
      references: QualificationReferences(
        fonixOutputBytes: _fonixBytes,
        sherpaBounds: _sherpaBounds(),
      ),
    );

    await expectLater(
      machine.run(_launch(HarnessLoadOrder.dartFirst)),
      throwsA(
        isA<QualificationFailure>().having(
          (QualificationFailure failure) => failure.code,
          'code',
          'driver-constructor-not-inert',
        ),
      ),
    );
    expect(factory.fonixSessionsCreated, 1);
    expect(factory.fonixSessionsClosed, 1);
    expect(factory.temporaryRootsRemaining, 0);
  });

  test(
    'reverse second-driver construction failure closes the initialized first',
    () async {
      final _FakeDriverFactory factory = _FakeDriverFactory(
        failSherpaCreateOrdinal: 3,
      );
      final QualificationStateMachine machine = QualificationStateMachine(
        driverFactory: factory,
        publicationSink: AuthoritativeLifecyclePublicationSink(),
        pins: _pins(),
        references: QualificationReferences(
          fonixOutputBytes: _fonixBytes,
          sherpaBounds: _sherpaBounds(),
        ),
      );

      await expectLater(
        machine.run(_launch(HarnessLoadOrder.dartFirst)),
        throwsA(isA<StateError>()),
      );
      expect(
        factory.events,
        containsAll(<String>['fonix2:init', 'fonix2:close']),
      );
      expect(factory.events, contains('sherpa3:create-failed'));
      expect(factory.fonixSessionsCreated, 2);
      expect(factory.fonixSessionsClosed, 2);
      expect(factory.sherpaDetectorsCreated, 2);
      expect(factory.sherpaDetectorsFreed, 2);
      expect(factory.temporaryRootsRemaining, 0);
    },
  );

  test('fails when the publication sink leaks a stale completion', () async {
    final _FakeDriverFactory factory = _FakeDriverFactory();
    final QualificationStateMachine machine = QualificationStateMachine(
      driverFactory: factory,
      publicationSink: _LeakyPublicationSink(),
      pins: _pins(),
      references: QualificationReferences(
        fonixOutputBytes: _fonixBytes,
        sherpaBounds: _sherpaBounds(),
      ),
    );

    await expectLater(
      machine.run(_launch(HarnessLoadOrder.dartFirst)),
      throwsA(
        isA<QualificationFailure>().having(
          (QualificationFailure failure) => failure.code,
          'code',
          'stale-publication-not-suppressed',
        ),
      ),
    );
    expect(factory.temporaryRootsRemaining, 0);
    expect(factory.events, contains('fonix1:close'));
  });

  test(
    'fails when sherpa accepts work after the cancellation request',
    () async {
      final _FakeDriverFactory factory = _FakeDriverFactory(
        leakAfterSherpaRetire: true,
      );
      final QualificationStateMachine machine = QualificationStateMachine(
        driverFactory: factory,
        publicationSink: AuthoritativeLifecyclePublicationSink(),
        pins: _pins(),
        references: QualificationReferences(
          fonixOutputBytes: _fonixBytes,
          sherpaBounds: _sherpaBounds(),
        ),
      );

      await expectLater(
        machine.run(_launch(HarnessLoadOrder.dartFirst)),
        throwsA(
          isA<QualificationFailure>().having(
            (QualificationFailure failure) => failure.code,
            'code',
            'sherpa-detector-not-retired',
          ),
        ),
      );
      expect(factory.temporaryRootsRemaining, 0);
    },
  );

  test('rejects queued removal as native cancellation evidence', () async {
    final _FakeDriverFactory factory = _FakeDriverFactory(
      cancellationDisposition: FonixCancellationDisposition.queuedRunRemoved,
    );
    final QualificationStateMachine machine = QualificationStateMachine(
      driverFactory: factory,
      publicationSink: AuthoritativeLifecyclePublicationSink(),
      pins: _pins(),
      references: QualificationReferences(
        fonixOutputBytes: _fonixBytes,
        sherpaBounds: _sherpaBounds(),
      ),
    );

    await expectLater(
      machine.run(_launch(HarnessLoadOrder.dartFirst)),
      throwsA(
        isA<QualificationFailure>().having(
          (QualificationFailure failure) => failure.code,
          'code',
          'fonix-cancellation-failed',
        ),
      ),
    );
    expect(factory.temporaryRootsRemaining, 0);
  });

  test(
    'cancellation dispatch failure retires and drains late output',
    () async {
      final _FakeDriverFactory factory = _FakeDriverFactory(
        throwCancellationDispatch: true,
      );
      final AuthoritativeLifecyclePublicationSink sink =
          AuthoritativeLifecyclePublicationSink();
      final QualificationStateMachine machine = QualificationStateMachine(
        driverFactory: factory,
        publicationSink: sink,
        pins: _pins(),
        references: QualificationReferences(
          fonixOutputBytes: _fonixBytes,
          sherpaBounds: _sherpaBounds(),
        ),
      );

      await expectLater(
        machine.run(_launch(HarnessLoadOrder.dartFirst)),
        throwsA(
          isA<StateError>().having(
            (StateError error) => error.message,
            'message',
            'injected cancellation dispatch failure',
          ),
        ),
      );
      final LifecyclePublicationSnapshot snapshot = sink.snapshot;
      expect(sink.authoritativeGeneration, 2);
      expect(snapshot.boundRuns, 3);
      expect(snapshot.settledRuns, 3);
      expect(snapshot.activeBindings, 0);
      expect(snapshot.currentPublishedOutputs, 2);
      expect(snapshot.cancellationSettlements, 1);
      expect(snapshot.cancellationPublishedOutputs, 0);
      expect(factory.fonixSettlementReads, 3);
      expect(factory.temporaryRootsRemaining, 0);
      expect(factory.fonixSessionsClosed, factory.fonixSessionsCreated);
      expect(factory.sherpaDetectorsFreed, factory.sherpaDetectorsCreated);
    },
  );

  test(
    'cancellation dispatch error wins when the drained settlement also fails',
    () async {
      final _FakeDriverFactory factory = _FakeDriverFactory(
        throwCancellationDispatch: true,
        failCancellationSettlement: true,
      );
      final AuthoritativeLifecyclePublicationSink sink =
          AuthoritativeLifecyclePublicationSink();
      final QualificationStateMachine machine = QualificationStateMachine(
        driverFactory: factory,
        publicationSink: sink,
        pins: _pins(),
        references: QualificationReferences(
          fonixOutputBytes: _fonixBytes,
          sherpaBounds: _sherpaBounds(),
        ),
      );

      await expectLater(
        machine.run(_launch(HarnessLoadOrder.dartFirst)),
        throwsA(
          isA<StateError>().having(
            (StateError error) => error.message,
            'message',
            'injected cancellation dispatch failure',
          ),
        ),
      );
      expect(sink.authoritativeGeneration, 2);
      expect(sink.snapshot.activeBindings, 0);
      expect(sink.snapshot.cancellationPublishedOutputs, 0);
      expect(factory.fonixSettlementReads, 3);
      expect(factory.temporaryRootsRemaining, 0);
      expect(factory.fonixSessionsClosed, factory.fonixSessionsCreated);
      expect(factory.sherpaDetectorsFreed, factory.sherpaDetectorsCreated);
    },
  );

  test('binds the sole settlement path before generation retirement', () async {
    var settlementReads = 0;
    final _FakeFonixRun run = _FakeFonixRun(
      purpose: FonixRunPurpose.stale,
      cancellationDisposition:
          FonixCancellationDisposition.nativeTerminationRequested,
      onSettlementRead: () {
        settlementReads += 1;
      },
      onSettled: () {},
    );
    final AuthoritativeLifecyclePublicationSink sink =
        AuthoritativeLifecyclePublicationSink();

    final Future<FonixPublication> publication = sink.bind(
      run: run,
      generation: sink.authoritativeGeneration,
      purpose: FonixRunPurpose.stale,
    );
    expect(settlementReads, 1);
    expect(sink.advanceGeneration(), 2);

    final FonixPublication result = await publication;
    expect(result.outcome, FonixPublicationOutcome.staleSuppressed);
    expect(result.settledOutputBytes, _fonixBytes);
    expect(result.publishedOutputBytes, isNull);
    expect(settlementReads, 1);
    expect(sink.snapshot.activeBindings, 0);
  });
}

QualificationPins _pins({String? fonixReferenceOutputSha256}) =>
    QualificationPins(
      sherpaProfileId: 'silero-vad-1.13.4',
      fonixModelSha256: _digest('1'),
      fonixInputSha256: _digest('2'),
      fonixReferenceOutputSha256:
          fonixReferenceOutputSha256 ?? sha256.convert(_fonixBytes).toString(),
      fonixCancellationModelSha256: _digest('1'),
      fonixCancellationInputSha256: _digest('4'),
      sherpaModelSha256: _digest('5'),
      sherpaAudioSha256: _digest('6'),
      sherpaReferenceSha256: _digest('7'),
    );

FonixInitializationObservation _fonixInitialization({
  String ortVersion = '1.27.0',
  String? modelSha256,
}) => FonixInitializationObservation(
  runtimeOwner: 'sherpa',
  runtimeSource: 'process',
  ortVersion: ortVersion,
  requiredOrtApi: 27,
  negotiatedOrtApi: 27,
  shimAbi: 1,
  shimBuildId: 'android-owner-sherpa-source-process',
  modelSha256: modelSha256 ?? _digest('1'),
  referenceInputSha256: _digest('2'),
  referenceOutputSha256: sha256.convert(_fonixBytes).toString(),
  cancellationModelSha256: _digest('1'),
  cancellationInputSha256: _digest('4'),
);

SherpaInitializationObservation _sherpaInitialization() =>
    SherpaInitializationObservation(
      getVersion: '1.13.4',
      getGitSha1: '14280725',
      profile: _sherpaProfile(),
      modelSha256: _digest('5'),
      audioSha256: _digest('6'),
      referenceSha256: _digest('7'),
    );

SherpaProfileObservation _sherpaProfile() => SherpaProfileObservation(
  id: 'silero-vad-1.13.4',
  provider: 'cpu',
  sampleRateHz: 16000,
  windowSamples: 512,
  numThreads: 1,
  thresholdMillionths: 500000,
  minimumSpeechMilliseconds: 250,
  minimumSilenceMilliseconds: 800,
  maximumSpeechMilliseconds: 30000,
  bufferMilliseconds: 60000,
);

String _digest(String character) => List<String>.filled(64, character).join();

HarnessLaunch _launch(HarnessLoadOrder order) =>
    HarnessLaunch.fromPlatform(<String, Object?>{
      'schemaVersion': 1,
      'loadOrder': order.wireValue,
      'launchChallengeBase64': base64Encode(utf8.encode('nonce-0001\n')),
    });

SherpaObservation _sherpaReference() => SherpaObservation(
  sourceSamples: 800,
  submittedSamples: 1024,
  segments: const <VadSegment>[VadSegment(startSample: 32, sampleCount: 512)],
  queueEmptyAfterDrain: true,
  detectedAfterDrain: false,
);

SherpaReferenceBounds _sherpaBounds() => SherpaReferenceBounds(
  sourceSamples: 800,
  minimumSegments: 1,
  maximumSegments: 2,
  minimumTotalSegmentSamples: 256,
  maximumTotalSegmentSamples: 700,
  maximumSegmentSamples: 600,
);

final class _FakeDriverFactory implements QualificationDriverFactory {
  _FakeDriverFactory({
    this.failFirstSherpaInitialize = false,
    this.failSherpaCreateOrdinal,
    this.eagerFirstFonixConstruction = false,
    this.leakAfterSherpaRetire = false,
    this.driftFirstFonixObservation = false,
    this.throwCancellationDispatch = false,
    this.failCancellationSettlement = false,
    this.cancellationDisposition =
        FonixCancellationDisposition.nativeTerminationRequested,
  });

  final bool failFirstSherpaInitialize;
  final int? failSherpaCreateOrdinal;
  final bool eagerFirstFonixConstruction;
  final bool leakAfterSherpaRetire;
  final bool driftFirstFonixObservation;
  final bool throwCancellationDispatch;
  final bool failCancellationSettlement;
  final FonixCancellationDisposition cancellationDisposition;
  final List<String> events = <String>[];
  var _fonixCount = 0;
  var _sherpaCount = 0;
  var _fonixSessionsCreated = 0;
  var _fonixSessionsClosed = 0;
  var _sherpaDetectorsCreated = 0;
  var _sherpaDetectorsFreed = 0;
  var _temporaryRootsCreated = 0;
  var _temporaryRootsRemoved = 0;
  var _temporaryRootsRemaining = 0;
  var fonixSettlementReads = 0;

  @override
  int get fonixSessionsCreated => _fonixSessionsCreated;

  @override
  int get fonixSessionsClosed => _fonixSessionsClosed;

  @override
  int get sherpaDetectorsCreated => _sherpaDetectorsCreated;

  @override
  int get sherpaDetectorsFreed => _sherpaDetectorsFreed;

  @override
  int get temporaryRootsCreated => _temporaryRootsCreated;

  @override
  int get temporaryRootsRemoved => _temporaryRootsRemoved;

  @override
  int get temporaryRootsRemaining => _temporaryRootsRemaining;

  @override
  FonixQualificationDriver createFonix() {
    _fonixCount += 1;
    events.add('fonix$_fonixCount:create');
    return _FakeFonixDriver(
      this,
      _fonixCount,
      cancellationDisposition: cancellationDisposition,
      throwCancellationDispatch: throwCancellationDispatch,
      failCancellationSettlement: failCancellationSettlement,
      eagerNativeConstruction: eagerFirstFonixConstruction && _fonixCount == 1,
    );
  }

  @override
  SherpaQualificationDriver createSherpa() {
    _sherpaCount += 1;
    if (_sherpaCount == failSherpaCreateOrdinal) {
      events.add('sherpa$_sherpaCount:create-failed');
      throw StateError('injected fake sherpa construction failure');
    }
    events.add('sherpa$_sherpaCount:create');
    return _FakeSherpaDriver(
      this,
      _sherpaCount,
      failInitialize: failFirstSherpaInitialize && _sherpaCount == 1,
      leakAfterRetire: leakAfterSherpaRetire && _sherpaCount == 1,
    );
  }

  void recordFonixInitialized() {
    _fonixSessionsCreated += 1;
    _temporaryRootsCreated += 1;
    _temporaryRootsRemaining += 1;
  }

  void recordFonixClosed() {
    _fonixSessionsClosed += 1;
    _temporaryRootsRemoved += 1;
    _temporaryRootsRemaining -= 1;
  }

  void recordSherpaInitialized() {
    _sherpaDetectorsCreated += 1;
  }

  void recordSherpaFreed() {
    _sherpaDetectorsFreed += 1;
  }
}

final class _FakeFonixDriver implements FonixQualificationDriver {
  _FakeFonixDriver(
    this.factory,
    this.id, {
    required this.cancellationDisposition,
    required this.throwCancellationDispatch,
    required this.failCancellationSettlement,
    required bool eagerNativeConstruction,
  }) {
    if (eagerNativeConstruction) {
      _alive = true;
      _initialized = true;
      factory.recordFonixInitialized();
    }
  }

  final _FakeDriverFactory factory;
  final int id;
  final FonixCancellationDisposition cancellationDisposition;
  final bool throwCancellationDispatch;
  final bool failCancellationSettlement;
  var _alive = false;
  var _closed = false;
  var _initialized = false;
  var _outstanding = 0;

  @override
  bool get isAlive => _alive;

  @override
  int get outstandingRuns => _outstanding;

  @override
  Future<FonixInitializationObservation> initialize() async {
    if (_initialized || _closed) {
      throw StateError('invalid fake Fonix initialize');
    }
    _alive = true;
    _initialized = true;
    factory.recordFonixInitialized();
    factory.events.add('fonix$id:init');
    return _fonixInitialization(
      modelSha256: factory.driftFirstFonixObservation && id == 1
          ? _digest('8')
          : null,
    );
  }

  @override
  Future<bool> probeAlive() async {
    factory.events.add('fonix$id:probe-alive');
    return _alive && !_closed;
  }

  @override
  FonixQualificationRun startRun(FonixRunPurpose purpose) {
    if (!_alive || _closed) throw StateError('fake Fonix is not alive');
    _outstanding += 1;
    factory.events.add('fonix$id:run:${purpose.name}');
    return _FakeFonixRun(
      purpose: purpose,
      cancellationDisposition: cancellationDisposition,
      throwCancellationDispatch: throwCancellationDispatch,
      failCancellationSettlement: failCancellationSettlement,
      onSettlementRead: () {
        factory.fonixSettlementReads += 1;
      },
      onSettled: () {
        _outstanding -= 1;
      },
    );
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    if (_outstanding != 0) throw StateError('fake Fonix run is outstanding');
    _closed = true;
    _alive = false;
    if (_initialized) factory.recordFonixClosed();
    factory.events.add('fonix$id:close');
  }
}

final class _FakeFonixRun implements FonixQualificationRun {
  _FakeFonixRun({
    required this.purpose,
    required this.cancellationDisposition,
    this.throwCancellationDispatch = false,
    this.failCancellationSettlement = false,
    required this.onSettlementRead,
    required void Function() onSettled,
  }) : _completer = Completer<FonixRunResult>() {
    _settled = _completer.future.whenComplete(onSettled);
    if (purpose != FonixRunPurpose.cancellation) {
      scheduleMicrotask(
        () => _completer.complete(FonixRunResult.completed(_fonixBytes)),
      );
    }
  }

  final FonixRunPurpose purpose;
  final FonixCancellationDisposition cancellationDisposition;
  final bool throwCancellationDispatch;
  final bool failCancellationSettlement;
  final void Function() onSettlementRead;
  final Completer<FonixRunResult> _completer;
  late final Future<FonixRunResult> _settled;

  @override
  Future<FonixRunResult> get settled {
    onSettlementRead();
    return _settled;
  }

  @override
  Future<FonixCancellationDisposition> cancelWithDisposition() async {
    if (purpose != FonixRunPurpose.cancellation || _completer.isCompleted) {
      return FonixCancellationDisposition.notCancelled;
    }
    if (throwCancellationDispatch) {
      scheduleMicrotask(() {
        if (_completer.isCompleted) return;
        if (failCancellationSettlement) {
          _completer.completeError(
            StateError('injected cancellation settlement failure'),
          );
        } else {
          _completer.complete(FonixRunResult.completed(_fonixBytes));
        }
      });
      throw StateError('injected cancellation dispatch failure');
    }
    if (cancellationDisposition == FonixCancellationDisposition.notCancelled) {
      _completer.complete(FonixRunResult.completed(_fonixBytes));
    } else {
      _completer.complete(const FonixRunResult.cancelled());
    }
    return cancellationDisposition;
  }
}

final class _FakeSherpaDriver implements SherpaQualificationDriver {
  _FakeSherpaDriver(
    this.factory,
    this.id, {
    required this.failInitialize,
    required this.leakAfterRetire,
  });

  final _FakeDriverFactory factory;
  final int id;
  final bool failInitialize;
  final bool leakAfterRetire;
  var _alive = false;
  var _closed = false;
  var _initialized = false;
  var _freed = false;
  var _acceptedFrames = 0;
  var _flushCalls = 0;
  var _publishedSegments = 0;

  @override
  int get acceptedFrames => _acceptedFrames;

  @override
  int get flushCalls => _flushCalls;

  @override
  bool get isAlive => _alive;

  @override
  int get publishedSegments => _publishedSegments;

  @override
  int get queuedSegments => 0;

  @override
  Future<SherpaInitializationObservation> initialize() async {
    if (_alive || _closed) throw StateError('invalid fake sherpa initialize');
    if (failInitialize) {
      factory.events.add('sherpa$id:init-failed');
      throw StateError('injected fake sherpa initialization failure');
    }
    _alive = true;
    _initialized = true;
    factory.recordSherpaInitialized();
    factory.events.add('sherpa$id:init');
    return _sherpaInitialization();
  }

  @override
  Future<bool> probeAlive() async {
    factory.events.add('sherpa$id:probe-alive');
    return _alive && !_closed;
  }

  @override
  Future<SherpaObservation> runReference(SherpaRunPurpose purpose) async {
    if (!_alive || _closed) throw StateError('fake sherpa is not alive');
    factory.events.add('sherpa$id:run:${purpose.name}');
    final SherpaObservation result = _sherpaReference();
    _acceptedFrames += result.submittedSamples ~/ 512;
    _flushCalls += 1;
    _publishedSegments += result.segments.length;
    return result;
  }

  @override
  Future<void> acceptCancellationFrame() async {
    if (!_alive || _closed) throw StateError('fake sherpa is not alive');
    _acceptedFrames += 1;
    factory.events.add('sherpa$id:cancellation-frame');
  }

  @override
  Future<void> retireBetweenFrames() async {
    if (!_alive || _closed) throw StateError('fake sherpa is not alive');
    _alive = false;
    _freeOnce();
    if (leakAfterRetire) {
      _acceptedFrames += 1;
      _flushCalls += 1;
      _publishedSegments += 1;
    }
    factory.events.add('sherpa$id:retire');
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    _alive = false;
    _freeOnce();
    factory.events.add('sherpa$id:close');
  }

  void _freeOnce() {
    if (!_initialized || _freed) return;
    _freed = true;
    factory.recordSherpaFreed();
  }
}

final class _LeakyPublicationSink implements LifecyclePublicationSink {
  final AuthoritativeLifecyclePublicationSink _delegate =
      AuthoritativeLifecyclePublicationSink();

  @override
  int get authoritativeGeneration => _delegate.authoritativeGeneration;

  @override
  LifecyclePublicationSnapshot get snapshot {
    final LifecyclePublicationSnapshot value = _delegate.snapshot;
    if (value.staleObserved == 0) return value;
    return LifecyclePublicationSnapshot(
      boundRuns: value.boundRuns,
      settledRuns: value.settledRuns,
      activeBindings: value.activeBindings,
      currentPublishedOutputs: value.currentPublishedOutputs + 1,
      cancellationSettlements: value.cancellationSettlements,
      cancellationCancelledResults: value.cancellationCancelledResults,
      cancellationPublishedOutputs: value.cancellationPublishedOutputs,
      staleObserved: value.staleObserved,
      staleSuppressed: 0,
      stalePublishedOutputs: 1,
    );
  }

  @override
  Future<FonixPublication> bind({
    required FonixQualificationRun run,
    required int generation,
    required FonixRunPurpose purpose,
  }) async {
    final FonixPublication publication = await _delegate.bind(
      run: run,
      generation: generation,
      purpose: purpose,
    );
    if (purpose != FonixRunPurpose.stale) return publication;
    return FonixPublication.currentOutput(publication.settledOutputBytes!);
  }

  @override
  int advanceGeneration() => _delegate.advanceGeneration();
}
