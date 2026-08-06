import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_sherpa_reference/src/harness_contract.dart';
import 'package:fonix_sherpa_reference/src/qualification_fixtures.dart';
import 'package:fonix_sherpa_reference/src/qualification_state_machine.dart';
import 'package:fonix_sherpa_reference/src/real_qualification_drivers.dart';

void main() {
  late AndroidQualificationFixtures fixtures;

  setUpAll(() {
    fixtures = _fixtures();
  });

  for (final HarnessLoadOrder order in HarnessLoadOrder.values) {
    test('real driver layer satisfies the state machine with host seams: '
        '${order.wireValue}', () async {
      final _FakeFonixSpawner fonix = _FakeFonixSpawner(
        referenceOutput: Uint8List.fromList(
          fixtures.references.fonixOutputBytes,
        ),
      );
      final _FakeSherpaApi sherpaApi = _FakeSherpaApi();
      final _FakeRootFactory roots = _FakeRootFactory();
      final AndroidQualificationDriverFactory factory =
          AndroidQualificationDriverFactory(
            fixtures: fixtures,
            fonixSpawner: fonix,
            sherpaApi: sherpaApi,
            temporaryRoots: roots,
          );
      expect(factory.fonixSessionsCreated, 0);
      expect(factory.sherpaDetectorsCreated, 0);
      expect(factory.temporaryRootsCreated, 0);

      final DeviceQualificationResult result = await QualificationStateMachine(
        driverFactory: factory,
        publicationSink: AuthoritativeLifecyclePublicationSink(),
        pins: fixtures.pins,
        references: fixtures.references,
      ).run(_launch(order));

      final Map<String, Object?> json = result.toJson();
      expect(json['result'], 'passed');
      expect(json['loadOrder'], order.wireValue);
      expect(json['fixtures'], fixtures.pins.fixturesJson());
      expect(factory.fonixSessionsCreated, 2);
      expect(factory.fonixSessionsClosed, 2);
      expect(factory.sherpaDetectorsCreated, 3);
      expect(factory.sherpaDetectorsFreed, 3);
      expect(factory.temporaryRootsCreated, 5);
      expect(factory.temporaryRootsRemoved, 5);
      expect(factory.temporaryRootsRemaining, 0);
      expect(sherpaApi.bindingInitializations, 1);
      expect(fonix.sessions, hasLength(2));
      expect(
        fonix.sessions.map((_FakeFonixSession value) => value.closeCalls),
        everyElement(1),
      );
      expect(sherpaApi.detectors, hasLength(3));
      expect(
        sherpaApi.detectors.map((_FakeSherpaDetector value) => value.freeCalls),
        everyElement(1),
      );
      expect(roots.roots, hasLength(5));
      expect(
        roots.roots.map((_FakeRoot value) => value.removeCalls),
        everyElement(1),
      );
    });
  }

  test(
    'constructors and factory creation are native and filesystem inert',
    () async {
      final _FakeFonixSpawner fonix = _FakeFonixSpawner(
        referenceOutput: Uint8List.fromList(
          fixtures.references.fonixOutputBytes,
        ),
      );
      final _FakeSherpaApi sherpa = _FakeSherpaApi();
      final _FakeRootFactory roots = _FakeRootFactory();
      final AndroidQualificationDriverFactory factory =
          AndroidQualificationDriverFactory(
            fixtures: fixtures,
            fonixSpawner: fonix,
            sherpaApi: sherpa,
            temporaryRoots: roots,
          );

      final FonixQualificationDriver fonixDriver = factory.createFonix();
      final SherpaQualificationDriver sherpaDriver = factory.createSherpa();

      expect(fonixDriver.isAlive, isFalse);
      expect(fonixDriver.outstandingRuns, 0);
      expect(sherpaDriver.isAlive, isFalse);
      expect(sherpaDriver.acceptedFrames, 0);
      expect(fonix.spawnCalls, 0);
      expect(sherpa.bindingInitializations, 0);
      expect(sherpa.createCalls, 0);
      expect(roots.createCalls, 0);
      await fonixDriver.close();
      await fonixDriver.close();
      await sherpaDriver.close();
      await sherpaDriver.close();
      expect(factory.fonixSessionsCreated, 0);
      expect(factory.sherpaDetectorsCreated, 0);
      expect(factory.temporaryRootsCreated, 0);
    },
  );

  test(
    'maps every backend cancellation disposition without collapsing it',
    () async {
      const Map<
        QualificationBackendCancellationDisposition,
        FonixCancellationDisposition
      >
      cases =
          <
            QualificationBackendCancellationDisposition,
            FonixCancellationDisposition
          >{
            QualificationBackendCancellationDisposition
                    .nativeTerminationRequested:
                FonixCancellationDisposition.nativeTerminationRequested,
            QualificationBackendCancellationDisposition.queuedRunRemoved:
                FonixCancellationDisposition.queuedRunRemoved,
            QualificationBackendCancellationDisposition.notCancelled:
                FonixCancellationDisposition.notCancelled,
          };
      for (final MapEntry<
            QualificationBackendCancellationDisposition,
            FonixCancellationDisposition
          >
          entry
          in cases.entries) {
        final _FakeFonixSpawner spawner = _FakeFonixSpawner(
          referenceOutput: Uint8List.fromList(
            fixtures.references.fonixOutputBytes,
          ),
          cancellationDisposition: entry.key,
        );
        final AndroidQualificationDriverFactory factory =
            AndroidQualificationDriverFactory(
              fixtures: fixtures,
              fonixSpawner: spawner,
              sherpaApi: _FakeSherpaApi(),
              temporaryRoots: _FakeRootFactory(),
            );
        final FonixQualificationDriver driver = factory.createFonix();
        await driver.initialize();
        final FonixQualificationRun run = driver.startRun(
          FonixRunPurpose.cancellation,
        );

        expect(await run.cancelWithDisposition(), entry.value);
        final FonixRunResult settlement = await run.settled;
        expect(settlement.outcome, FonixRunOutcome.cancelled);
        expect(settlement.outputBytes, isNull);
        expect(driver.outstandingRuns, 0);
        await driver.close();
        await driver.close();
        expect(factory.fonixSessionsCreated, 1);
        expect(factory.fonixSessionsClosed, 1);
        expect(factory.temporaryRootsRemaining, 0);
      }
    },
  );

  test(
    'uses the exact CPU Silero profile and padded 512-sample frames',
    () async {
      final _FakeSherpaApi api = _FakeSherpaApi();
      final AndroidQualificationDriverFactory factory =
          AndroidQualificationDriverFactory(
            fixtures: fixtures,
            fonixSpawner: _FakeFonixSpawner(
              referenceOutput: Uint8List.fromList(
                fixtures.references.fonixOutputBytes,
              ),
            ),
            sherpaApi: api,
            temporaryRoots: _FakeRootFactory(),
          );
      final SherpaQualificationDriver driver = factory.createSherpa();

      final SherpaInitializationObservation identity = await driver
          .initialize();
      expect(identity.matchesPins(fixtures.pins), isTrue);
      expect(await driver.probeAlive(), isTrue);
      expect(api.configurations, hasLength(1));
      final QualificationSherpaDetectorConfiguration config =
          api.configurations.single;
      expect(config.provider, 'cpu');
      expect(config.sampleRate, 16000);
      expect(config.windowSize, 512);
      expect(config.numThreads, 1);
      expect(config.threshold, 0.5);
      expect(config.minimumSpeechSeconds, 0.25);
      expect(config.minimumSilenceSeconds, 0.8);
      expect(config.maximumSpeechSeconds, 30.0);
      expect(config.bufferSeconds, 60.0);
      expect(config.debug, isFalse);

      final SherpaObservation observation = await driver.runReference(
        SherpaRunPurpose.reference,
      );
      expect(observation.sourceSamples, 128000);
      expect(observation.submittedSamples, 128000);
      expect(observation.segments, const <VadSegment>[
        VadSegment(startSample: 11872, sampleCount: 96160),
      ]);
      final _FakeSherpaDetector detector = api.detectors.single;
      expect(detector.accepted, hasLength(250));
      expect(detector.accepted, everyElement(hasLength(512)));
      expect(driver.acceptedFrames, 250);
      expect(driver.flushCalls, 1);
      expect(driver.publishedSegments, 1);

      await driver.acceptCancellationFrame();
      expect(driver.acceptedFrames, 251);
      expect(driver.flushCalls, 1);
      expect(driver.publishedSegments, 1);
      await driver.retireBetweenFrames();
      await driver.close();
      expect(driver.isAlive, isFalse);
      expect(driver.queuedSegments, 0);
      expect(detector.freeCalls, 1);
      expect(factory.sherpaDetectorsFreed, 1);
      expect(factory.temporaryRootsRemaining, 0);
    },
  );

  test(
    'Fonix observation failure closes the created session and root',
    () async {
      final _FakeFonixSpawner spawner = _FakeFonixSpawner(
        referenceOutput: Uint8List.fromList(
          fixtures.references.fonixOutputBytes,
        ),
        failObservation: true,
      );
      final AndroidQualificationDriverFactory factory =
          AndroidQualificationDriverFactory(
            fixtures: fixtures,
            fonixSpawner: spawner,
            sherpaApi: _FakeSherpaApi(),
            temporaryRoots: _FakeRootFactory(),
          );
      final FonixQualificationDriver driver = factory.createFonix();

      await expectLater(driver.initialize(), throwsStateError);
      await driver.close();
      await driver.close();
      expect(driver.isAlive, isFalse);
      expect(factory.fonixSessionsCreated, 1);
      expect(factory.fonixSessionsClosed, 1);
      expect(factory.temporaryRootsCreated, 1);
      expect(factory.temporaryRootsRemoved, 1);
      expect(spawner.sessions.single.closeCalls, 1);
    },
  );

  test(
    'Sherpa identity failure frees detector and removes staged model root',
    () async {
      final _FakeSherpaApi api = _FakeSherpaApi(version: 'not-semver');
      final AndroidQualificationDriverFactory factory =
          AndroidQualificationDriverFactory(
            fixtures: fixtures,
            fonixSpawner: _FakeFonixSpawner(
              referenceOutput: Uint8List.fromList(
                fixtures.references.fonixOutputBytes,
              ),
            ),
            sherpaApi: api,
            temporaryRoots: _FakeRootFactory(),
          );
      final SherpaQualificationDriver driver = factory.createSherpa();

      await expectLater(
        driver.initialize(),
        throwsA(isA<QualificationFailure>()),
      );
      await driver.close();
      await driver.close();
      expect(factory.sherpaDetectorsCreated, 1);
      expect(factory.sherpaDetectorsFreed, 1);
      expect(factory.temporaryRootsCreated, 1);
      expect(factory.temporaryRootsRemoved, 1);
      expect(api.detectors.single.freeCalls, 1);
    },
  );

  test(
    'staging failure removes its root without claiming a detector',
    () async {
      final _FakeRootFactory roots = _FakeRootFactory(failWrites: true);
      final _FakeSherpaApi api = _FakeSherpaApi();
      final AndroidQualificationDriverFactory factory =
          AndroidQualificationDriverFactory(
            fixtures: fixtures,
            fonixSpawner: _FakeFonixSpawner(
              referenceOutput: Uint8List.fromList(
                fixtures.references.fonixOutputBytes,
              ),
            ),
            sherpaApi: api,
            temporaryRoots: roots,
          );

      await expectLater(factory.createSherpa().initialize(), throwsStateError);
      expect(api.createCalls, 0);
      expect(factory.sherpaDetectorsCreated, 0);
      expect(factory.sherpaDetectorsFreed, 0);
      expect(factory.temporaryRootsCreated, 1);
      expect(factory.temporaryRootsRemoved, 1);
      expect(roots.roots.single.removeCalls, 1);
    },
  );

  test('rejects an out-of-range VAD segment before publication', () async {
    final _FakeSherpaApi api = _FakeSherpaApi(
      emittedSegments: const <VadSegment>[
        VadSegment(startSample: 127900, sampleCount: 200),
      ],
    );
    final AndroidQualificationDriverFactory factory =
        AndroidQualificationDriverFactory(
          fixtures: fixtures,
          fonixSpawner: _FakeFonixSpawner(
            referenceOutput: Uint8List.fromList(
              fixtures.references.fonixOutputBytes,
            ),
          ),
          sherpaApi: api,
          temporaryRoots: _FakeRootFactory(),
        );
    final SherpaQualificationDriver driver = factory.createSherpa();
    await driver.initialize();

    await expectLater(
      driver.runReference(SherpaRunPurpose.reference),
      throwsStateError,
    );
    expect(driver.publishedSegments, 0);
    await driver.close();
    expect(factory.sherpaDetectorsFreed, 1);
    expect(factory.temporaryRootsRemaining, 0);
  });
}

final class _FakeRootFactory implements QualificationTemporaryRootFactory {
  _FakeRootFactory({this.failWrites = false});

  final bool failWrites;
  final List<_FakeRoot> roots = <_FakeRoot>[];
  var createCalls = 0;

  @override
  Future<QualificationTemporaryRoot> create(String prefix) async {
    createCalls += 1;
    final _FakeRoot root = _FakeRoot(
      '/private/fake/qualification-$createCalls',
      failWrites: failWrites,
    );
    roots.add(root);
    return root;
  }
}

final class _FakeRoot implements QualificationTemporaryRoot {
  _FakeRoot(this.absolutePath, {required this.failWrites});

  @override
  final String absolutePath;
  final bool failWrites;
  final Map<String, Uint8List> files = <String, Uint8List>{};
  var removeCalls = 0;

  @override
  Future<Uint8List> readBytes(String basename) async {
    final Uint8List? value = files[basename];
    if (value == null) throw StateError('missing staged file');
    return Uint8List.fromList(value);
  }

  @override
  Future<void> remove() async {
    removeCalls += 1;
    files.clear();
  }

  @override
  Future<String> writeBytes(String basename, Uint8List bytes) async {
    if (failWrites) throw StateError('injected staging failure');
    files[basename] = Uint8List.fromList(bytes);
    return '$absolutePath/$basename';
  }
}

final class _FakeFonixSpawner implements QualificationFonixSessionSpawner {
  _FakeFonixSpawner({
    required this.referenceOutput,
    this.cancellationDisposition =
        QualificationBackendCancellationDisposition.nativeTerminationRequested,
    this.failObservation = false,
  });

  final Uint8List referenceOutput;
  final QualificationBackendCancellationDisposition cancellationDisposition;
  final bool failObservation;
  final List<_FakeFonixSession> sessions = <_FakeFonixSession>[];
  final List<QualificationFonixSessionConfiguration> configurations =
      <QualificationFonixSessionConfiguration>[];
  var spawnCalls = 0;

  @override
  Future<QualificationFonixBackendSession> spawn(
    QualificationFonixSessionConfiguration configuration,
  ) async {
    spawnCalls += 1;
    configurations.add(configuration);
    final _FakeFonixSession session = _FakeFonixSession(
      configuration: configuration,
      referenceOutput: referenceOutput,
      cancellationDisposition: cancellationDisposition,
      failObservation: failObservation,
    );
    sessions.add(session);
    return session;
  }
}

final class _FakeFonixSession implements QualificationFonixBackendSession {
  _FakeFonixSession({
    required this.configuration,
    required this.referenceOutput,
    required this.cancellationDisposition,
    required this.failObservation,
  });

  final QualificationFonixSessionConfiguration configuration;
  final Uint8List referenceOutput;
  final QualificationBackendCancellationDisposition cancellationDisposition;
  final bool failObservation;
  var _outstandingRuns = 0;
  var closeCalls = 0;

  @override
  int get outstandingRuns => _outstandingRuns;

  @override
  FonixInitializationObservation initializationObservation() {
    if (failObservation) throw StateError('injected observation failure');
    return FonixInitializationObservation(
      runtimeOwner: 'sherpa',
      runtimeSource: 'process',
      ortVersion: '1.27.1',
      requiredOrtApi: 27,
      negotiatedOrtApi: 27,
      shimAbi: 1,
      shimBuildId: 'android-owner-sherpa-source-process',
      modelSha256: configuration.modelSha256,
      referenceInputSha256: configuration.referenceInputSha256,
      referenceOutputSha256: configuration.referenceOutputSha256,
      cancellationModelSha256: configuration.cancellationModelSha256,
      cancellationInputSha256: configuration.cancellationInputSha256,
    );
  }

  @override
  QualificationFonixBackendRun startRun(QualificationMatrixInput input) {
    _outstandingRuns += 1;
    final _FakeFonixRun run = input.rows == 512
        ? _FakeFonixRun.pending(cancellationDisposition)
        : _FakeFonixRun.completed(referenceOutput);
    unawaited(
      run.settled.whenComplete(() {
        _outstandingRuns -= 1;
      }),
    );
    return run;
  }

  @override
  Future<void> close() async {
    closeCalls += 1;
  }
}

final class _FakeFonixRun implements QualificationFonixBackendRun {
  _FakeFonixRun.pending(this._disposition)
    : _completer = Completer<QualificationFonixBackendSettlement>();

  _FakeFonixRun.completed(Uint8List bytes)
    : _disposition = QualificationBackendCancellationDisposition.notCancelled,
      _completer = Completer<QualificationFonixBackendSettlement>()
        ..complete(QualificationFonixBackendSettlement.completed(bytes));

  final QualificationBackendCancellationDisposition _disposition;
  final Completer<QualificationFonixBackendSettlement> _completer;

  @override
  Future<QualificationFonixBackendSettlement> get settled => _completer.future;

  @override
  Future<QualificationBackendCancellationDisposition>
  cancelWithDisposition() async {
    if (!_completer.isCompleted) {
      _completer.complete(
        const QualificationFonixBackendSettlement.cancelled(),
      );
    }
    return _disposition;
  }
}

final class _FakeSherpaApi implements QualificationSherpaApi {
  _FakeSherpaApi({
    this.version = '1.13.4',
    this.emittedSegments = const <VadSegment>[
      VadSegment(startSample: 11872, sampleCount: 96160),
    ],
  });

  final String version;
  final String gitSha1 = '142807252687d81b40d6315f23470a1512a00de3';
  final List<VadSegment> emittedSegments;
  final List<QualificationSherpaDetectorConfiguration> configurations =
      <QualificationSherpaDetectorConfiguration>[];
  final List<_FakeSherpaDetector> detectors = <_FakeSherpaDetector>[];
  var bindingInitializations = 0;
  var createCalls = 0;

  @override
  QualificationSherpaDetector createDetector(
    QualificationSherpaDetectorConfiguration configuration,
  ) {
    createCalls += 1;
    configurations.add(configuration);
    final _FakeSherpaDetector detector = _FakeSherpaDetector(emittedSegments);
    detectors.add(detector);
    return detector;
  }

  @override
  String getGitSha1() => gitSha1;

  @override
  String getVersion() => version;

  @override
  void initializeBindings() {
    bindingInitializations += 1;
  }
}

final class _FakeSherpaDetector implements QualificationSherpaDetector {
  _FakeSherpaDetector(this.emittedSegments);

  final List<VadSegment> emittedSegments;
  final List<Float32List> accepted = <Float32List>[];
  final List<VadSegment> _queue = <VadSegment>[];
  var freeCalls = 0;

  @override
  void acceptWaveform(Float32List samples) {
    accepted.add(Float32List.fromList(samples));
  }

  @override
  void clear() {
    _queue.clear();
  }

  @override
  void flush() {
    _queue.addAll(emittedSegments);
  }

  @override
  void free() {
    freeCalls += 1;
    _queue.clear();
  }

  @override
  VadSegment front() => _queue.first;

  @override
  bool isDetected() => false;

  @override
  bool isEmpty() => _queue.isEmpty;

  @override
  void pop() {
    _queue.removeAt(0);
  }

  @override
  void reset() {}
}

HarnessLaunch _launch(HarnessLoadOrder order) =>
    HarnessLaunch.fromPlatform(<String, Object?>{
      'schemaVersion': 1,
      'loadOrder': order.wireValue,
      'launchChallengeBase64': base64Encode(utf8.encode('driver-test\n')),
    });

AndroidQualificationFixtures _fixtures() {
  final Uint8List output = Uint8List(16);
  final ByteData outputData = ByteData.sublistView(output);
  for (var index = 0; index < 4; index += 1) {
    outputData.setFloat32(index * 4, (index + 1).toDouble(), Endian.little);
  }
  final Uint8List model = Uint8List(212860);
  for (var index = 0; index < model.length; index += 1) {
    model[index] = (index * 37 + 11) & 0xff;
  }
  final Uint8List audio = _wav(128000);
  return AndroidQualificationFixtures.fromBytes(
    fonixModel: Uint8List.fromList(<int>[8, 8, 18, 4, 84, 69, 83, 84]),
    fonixReferenceInput: _matrix(
      2,
      (int row, int column) => <double>[1, 2, 3, 4][row * 2 + column],
    ),
    fonixReferenceOutput: output,
    fonixCancellationInput: _matrix(
      512,
      (int row, int column) => (((row * 17 + column * 31) % 23) - 11) / 16,
    ),
    sherpaModel: model,
    sherpaAudio: audio,
    sherpaReference: Uint8List.fromList(
      utf8.encode(
        jsonEncode(<String, Object?>{
          'schemaVersion': 1,
          'sherpa': <String, Object?>{
            'packageVersion': androidSherpaPackageVersion,
            'nativeRevision': androidSherpaNativeRevision,
          },
          'profile': <String, Object?>{
            'id': androidSherpaQualificationProfileId,
            'provider': 'cpu',
            'sampleRateHz': 16000,
            'windowSamples': 512,
            'numThreads': 1,
            'thresholdMillionths': 500000,
            'minimumSpeechMilliseconds': 250,
            'minimumSilenceMilliseconds': 800,
            'maximumSpeechMilliseconds': 30000,
            'bufferMilliseconds': 60000,
          },
          'model': <String, Object?>{
            'file': androidSherpaModelFilename,
            'sizeBytes': model.length,
            'sha256': sha256.convert(model).toString(),
          },
          'audio': <String, Object?>{
            'file': androidSherpaAudioFilename,
            'encoding': 'wav-pcm-s16le-mono',
            'generatorId': androidSherpaAudioGeneratorId,
            'sizeBytes': audio.length,
            'sha256': sha256.convert(audio).toString(),
            'sampleCount': 128000,
          },
          'observedSegments': <Object?>[
            <String, Object?>{'startSample': 11872, 'sampleCount': 96160},
          ],
          'invariant': <String, Object?>{
            'minimumSegments': 1,
            'maximumSegments': 1,
            'minimumTotalSegmentSamples': 95232,
            'maximumTotalSegmentSamples': 97280,
            'maximumSegmentSamples': 97280,
          },
          'claimBoundary': androidSherpaReferenceClaimBoundary,
        }),
      ),
    ),
  );
}

Uint8List _matrix(int dimension, double Function(int row, int column) left) {
  final int elements = dimension * dimension;
  final Uint8List bytes = Uint8List(24 + elements * 8);
  bytes.setRange(0, 8, <int>[70, 79, 78, 73, 88, 77, 49, 0]);
  final ByteData data = ByteData.sublistView(bytes);
  data.setUint32(8, 1, Endian.little);
  data.setUint32(12, dimension, Endian.little);
  data.setUint32(16, dimension, Endian.little);
  data.setUint32(20, dimension, Endian.little);
  var offset = 24;
  for (var row = 0; row < dimension; row += 1) {
    for (var column = 0; column < dimension; column += 1) {
      data.setFloat32(offset, left(row, column), Endian.little);
      offset += 4;
    }
  }
  for (var row = 0; row < dimension; row += 1) {
    for (var column = 0; column < dimension; column += 1) {
      data.setFloat32(offset, row == column ? 1 : 0, Endian.little);
      offset += 4;
    }
  }
  return bytes;
}

Uint8List _wav(int sampleCount) {
  final Uint8List bytes = Uint8List(44 + sampleCount * 2);
  bytes.setRange(0, 4, ascii.encode('RIFF'));
  bytes.setRange(8, 12, ascii.encode('WAVE'));
  bytes.setRange(12, 16, ascii.encode('fmt '));
  bytes.setRange(36, 40, ascii.encode('data'));
  final ByteData data = ByteData.sublistView(bytes);
  data.setUint32(4, bytes.length - 8, Endian.little);
  data.setUint32(16, 16, Endian.little);
  data.setUint16(20, 1, Endian.little);
  data.setUint16(22, 1, Endian.little);
  data.setUint32(24, 16000, Endian.little);
  data.setUint32(28, 32000, Endian.little);
  data.setUint16(32, 2, Endian.little);
  data.setUint16(34, 16, Endian.little);
  data.setUint32(40, sampleCount * 2, Endian.little);
  for (var index = 0; index < sampleCount; index += 1) {
    data.setInt16(44 + index * 2, (index % 200) - 100, Endian.little);
  }
  return bytes;
}
