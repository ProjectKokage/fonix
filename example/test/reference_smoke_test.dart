import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_reference/src/inference_backend.dart';
import 'package:fonix_reference/src/reference_smoke.dart';

import 'fake_inference_backend.dart';

const String _challengeValue =
    '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const String _otherChallengeValue =
    'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210';
const int _processId = 4242;
const MethodChannel _iosReferenceLaunchChannel = MethodChannel(
  residentReferenceLaunchChannelName,
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  tearDown(() async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(_iosReferenceLaunchChannel, null);
  });

  test('settles one exact run and double-closes one backend', () async {
    final FakeInferenceBackend backend = FakeInferenceBackend();
    final Future<ReferenceSmokeReceipt> running = runReferenceSmoke(backend);
    backend.startup.complete(fakeStartupReceipt());
    await Future<void>.delayed(Duration.zero);
    backend.runs.single.complete(fakeRunReceipt());

    final ReferenceSmokeReceipt receipt = await running;

    expect(receipt.outputValues, <int>[1, 4, 9, 16, 25, 36]);
    expect(receipt.activeProviders, <String>['cpu']);
    expect(receipt.toMap().keys, <String>[
      'schemaVersion',
      'status',
      'runtimeVersion',
      'runtimeSource',
      'runtimeOwner',
      'artifactFlavor',
      'platform',
      'architecture',
      'shimBuildId',
      'artifactSha256',
      'modelSha256',
      'outputValues',
      'activeProviders',
      'fullCpuAssignment',
      'doubleClose',
    ]);
    expect(receipt.toJsonString(), isNot(contains('\n')));
    expect(backend.closeCalls, 2);
    expect(backend.closeWorkCount, 1);
  });

  test(
    'resident smoke emits one receipt and leaves shutdown to its host',
    () async {
      final FakeInferenceBackend backend = FakeInferenceBackend();
      final ResidentReferenceChallenge challenge = _challenge();
      final List<String> passed = <String>[];
      final List<String> failed = <String>[];
      final Future<void> running = runResidentReferenceSmoke(
        backend,
        challenge: challenge,
        processId: _processId,
        onPassed: passed.add,
        onFailed: failed.add,
      );
      backend.startup.complete(fakeStartupReceipt());
      await Future<void>.delayed(Duration.zero);
      backend.runs.single.complete(fakeRunReceipt());

      await running;

      expect(passed, hasLength(1));
      expect(passed.single, startsWith(referenceSmokeReceiptPrefix));
      final Map<String, Object?> payload = _linePayload(passed.single);
      expect(payload['challenge'], _challengeValue);
      expect(payload['processId'], _processId);
      expect(payload.keys.toList(), <String>[
        'schemaVersion',
        'status',
        'runtimeVersion',
        'runtimeSource',
        'runtimeOwner',
        'artifactFlavor',
        'platform',
        'architecture',
        'shimBuildId',
        'artifactSha256',
        'modelSha256',
        'outputValues',
        'activeProviders',
        'fullCpuAssignment',
        'doubleClose',
        'challenge',
        'processId',
      ]);
      expect(failed, isEmpty);
      expect(backend.closeCalls, 2);
      expect(backend.closeWorkCount, 1);
    },
  );

  test('resident smoke emits one bounded path-free failure', () async {
    final FakeInferenceBackend backend = FakeInferenceBackend();
    final ResidentReferenceChallenge challenge = _challenge();
    final List<String> passed = <String>[];
    final List<String> failed = <String>[];
    final Future<void> running = runResidentReferenceSmoke(
      backend,
      challenge: challenge,
      processId: _processId,
      onPassed: passed.add,
      onFailed: failed.add,
    );
    backend.startup.completeError(StateError('/private/model.onnx'));

    await running;

    expect(passed, isEmpty);
    expect(failed, <String>[
      '$referenceSmokeFailurePrefix'
          '{"schemaVersion":1,"status":"failed","errorType":"StateError",'
          '"challenge":"$_challengeValue","processId":$_processId}',
    ]);
    expect(failed.single, isNot(contains('/private')));
    expect(backend.closeWorkCount, 1);
  });

  test('resident smoke awaits its authoritative line sink', () async {
    final FakeInferenceBackend backend = FakeInferenceBackend();
    final ResidentReferenceChallenge challenge = _challenge();
    final List<String> events = <String>[];
    final Future<void> running = runResidentReferenceSmoke(
      backend,
      challenge: challenge,
      processId: _processId,
      onPassed: (String line) async {
        await Future<void>.delayed(Duration.zero);
        events.add(line);
      },
      onFailed: (String line) async => events.add(line),
    );
    backend.startup.complete(fakeStartupReceipt());
    await Future<void>.delayed(Duration.zero);
    backend.runs.single.complete(fakeRunReceipt());

    await running;

    expect(events, hasLength(1));
    expect(events.single, startsWith(referenceSmokeReceiptPrefix));
    expect(_linePayload(events.single)['challenge'], _challengeValue);
    expect(_linePayload(events.single)['processId'], _processId);
  });

  test('resident challenge rejects malformed and path input', () {
    for (final String malformed in <String>[
      '',
      _repeat('0', 63),
      _repeat('0', 65),
      _repeat('A', 64),
      '../../fonix-reference-receipt.txt',
      '${_repeat('0', 63)}/',
    ]) {
      expect(
        () => ResidentReferenceChallenge.parse(malformed),
        throwsArgumentError,
        reason: malformed,
      );
    }
    expect(
      ResidentReferenceChallenge.parse(_challengeValue).value,
      _challengeValue,
    );
  });

  test('iOS activation channel sends one exact argument-free method', () async {
    MethodCall? observed;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(_iosReferenceLaunchChannel, (
          MethodCall call,
        ) async {
          observed = call;
          return <String, Object?>{
            'schemaVersion': referenceSmokeSchemaVersion,
            'challenge': _challengeValue,
          };
        });

    final ResidentReferenceChallenge? challenge =
        await readIosResidentReferenceChallenge();

    expect(observed?.method, residentReferenceLaunchMethod);
    expect(observed?.arguments, isNull);
    expect(challenge?.value, _challengeValue);
  });

  test('iOS activation channel preserves repeated absent activation', () async {
    var calls = 0;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(_iosReferenceLaunchChannel, (
          MethodCall call,
        ) async {
          calls += 1;
          return null;
        });

    expect(await readIosResidentReferenceChallenge(), isNull);
    expect(await readIosResidentReferenceChallenge(), isNull);
    expect(calls, 2);
  });

  test('iOS activation accepts the codec dynamic-map shape', () async {
    final Map<dynamic, dynamic> activation = <dynamic, dynamic>{
      'schemaVersion': 1,
      'challenge': _challengeValue,
    };

    expect(
      (await readIosResidentReferenceChallenge(
        readActivation: () async => activation,
      ))?.value,
      _challengeValue,
    );
  });

  test('iOS activation rejects malformed and unknown reply shapes', () async {
    final List<Object?> malformed = <Object?>[
      true,
      const <Object?>[],
      const <String, Object?>{},
      const <String, Object?>{'schemaVersion': 1},
      const <String, Object?>{'challenge': _challengeValue},
      const <String, Object?>{
        'schemaVersion': 1,
        'challenge': _challengeValue,
        'unknown': true,
      },
      const <String, Object?>{'schemaVersion': 2, 'challenge': _challengeValue},
      const <String, Object?>{
        'schemaVersion': 1.0,
        'challenge': _challengeValue,
      },
      const <String, Object?>{
        'schemaVersion': true,
        'challenge': _challengeValue,
      },
      const <String, Object?>{'schemaVersion': 1, 'challenge': 1},
      <String, Object?>{
        'schemaVersion': 1,
        'challenge': _challengeValue.toUpperCase(),
      },
    ];
    for (final Object? raw in malformed) {
      await expectLater(
        readIosResidentReferenceChallenge(readActivation: () async => raw),
        throwsA(
          isA<InferenceBackendFailure>()
              .having(
                (InferenceBackendFailure error) => error.summary,
                'summary',
                'The iOS reference launch activation was malformed.',
              )
              .having(
                (InferenceBackendFailure error) => error.backendUnusable,
                'backendUnusable',
                isTrue,
              ),
        ),
        reason: '$raw',
      );
    }
  });

  test('iOS activation duplicate reads are stable for hot restart', () async {
    var calls = 0;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(_iosReferenceLaunchChannel, (
          MethodCall call,
        ) async {
          calls += 1;
          return <String, Object?>{
            'schemaVersion': referenceSmokeSchemaVersion,
            'challenge': _challengeValue,
          };
        });

    final ResidentReferenceChallenge? first =
        await readIosResidentReferenceChallenge();
    final ResidentReferenceChallenge? second =
        await readIosResidentReferenceChallenge();

    expect(calls, 2);
    expect(first?.value, _challengeValue);
    expect(second?.value, first?.value);
  });

  test(
    'iOS activation translates repeated platform errors path-free',
    () async {
      var calls = 0;
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(_iosReferenceLaunchChannel, (
            MethodCall call,
          ) async {
            calls += 1;
            throw PlatformException(
              code: 'invalid-reference-smoke-activation',
              message: '/private/native/value',
            );
          });
      for (var attempt = 0; attempt < 2; attempt += 1) {
        await expectLater(
          readIosResidentReferenceChallenge(),
          throwsA(
            isA<InferenceBackendFailure>()
                .having(
                  (InferenceBackendFailure error) => error.summary,
                  'summary',
                  'The iOS reference launch activation was unavailable.',
                )
                .having(
                  (InferenceBackendFailure error) => error.toString(),
                  'diagnostic',
                  isNot(contains('/private')),
                ),
          ),
        );
      }
      expect(calls, 2);
    },
  );

  test('iOS activation has a bounded wait', () async {
    final Completer<Object?> pending = Completer<Object?>();
    await expectLater(
      readIosResidentReferenceChallenge(
        readActivation: () => pending.future,
        timeout: const Duration(milliseconds: 1),
      ),
      throwsA(
        isA<InferenceBackendFailure>().having(
          (InferenceBackendFailure error) => error.summary,
          'summary',
          'The iOS reference launch activation timed out.',
        ),
      ),
    );
    for (final Duration timeout in <Duration>[
      Duration.zero,
      maximumResidentReferenceActivationWait + const Duration(microseconds: 1),
    ]) {
      await expectLater(
        readIosResidentReferenceChallenge(timeout: timeout),
        throwsArgumentError,
      );
    }
  });

  test('resident process identity is exact, positive, and bounded', () async {
    final ResidentReferenceChallenge challenge = _challenge();
    final String maximumJson = residentReferenceSmokeFailureJson(
      StateError('hidden'),
      challenge,
      maximumResidentReferenceProcessId,
    );
    final String maximumLine = '$referenceSmokeFailurePrefix$maximumJson';
    expect(
      _linePayload(maximumLine)['processId'],
      maximumResidentReferenceProcessId,
    );

    for (final int invalid in <int>[
      -1,
      0,
      maximumResidentReferenceProcessId + 1,
    ]) {
      await expectLater(
        runResidentReferenceSmoke(
          FakeInferenceBackend(),
          challenge: challenge,
          processId: invalid,
          onPassed: (_) {},
          onFailed: (_) {},
        ),
        throwsArgumentError,
      );
      expect(
        () => residentReferenceSmokeFailureJson(
          StateError('hidden'),
          challenge,
          invalid,
        ),
        throwsArgumentError,
      );
    }
  });

  test(
    'resident line rejects oversized and non-ASCII input before IO',
    () async {
      final ResidentReferenceChallenge challenge = _challenge();
      for (final String line in <String>[
        '$referenceSmokeFailurePrefix'
            '${_repeat('a', maximumResidentReferenceLineBytes)}',
        _failureLine(challenge).replaceFirst('StateError', 'StatéError'),
        _failureLine(challenge).replaceFirst('StateError', 'State\tError'),
      ]) {
        final _FakeResidentPublicationIo io = _FakeResidentPublicationIo();
        await expectLater(
          publishResidentReferenceLine(
            line,
            challenge: challenge,
            processId: _processId,
            directory: Directory('/virtual/resident'),
            publicationIo: io,
          ),
          throwsA(isA<InferenceBackendFailure>()),
        );
        expect(io.directoryExistsCalls, 0, reason: line.length.toString());
      }
    },
  );

  test('resident line accepts only the exact final bounded length', () async {
    final ResidentReferenceChallenge challenge = _challenge();
    final String base = _failureLine(challenge);
    final String boundary =
        base +
        _repeat(' ', maximumResidentReferenceLineBytes - 1 - base.length);
    expect(boundary.length + 1, maximumResidentReferenceLineBytes);
    final _FakeResidentPublicationIo accepted = _FakeResidentPublicationIo();

    await publishResidentReferenceLine(
      boundary,
      challenge: challenge,
      processId: _processId,
      directory: Directory('/virtual/resident'),
      publicationIo: accepted,
    );

    expect(accepted.openFile.bytes.length, maximumResidentReferenceLineBytes);
    final _FakeResidentPublicationIo rejected = _FakeResidentPublicationIo();
    await expectLater(
      publishResidentReferenceLine(
        '$boundary ',
        challenge: challenge,
        processId: _processId,
        directory: Directory('/virtual/resident'),
        publicationIo: rejected,
      ),
      throwsA(isA<InferenceBackendFailure>()),
    );
    expect(rejected.directoryExistsCalls, 0);
  });

  test(
    'resident publication closes and cleans up every primary IO failure',
    () async {
      for (final String operation in <String>[
        'write',
        'flush',
        'close',
        'rename',
        'stat',
      ]) {
        final _FakeResidentPublicationIo io = _FakeResidentPublicationIo();
        final FileSystemException failure = FileSystemException(
          operation,
          '/private/$operation-secret',
        );
        switch (operation) {
          case 'write':
            io.openFile.writeError = failure;
          case 'flush':
            io.openFile.flushError = failure;
          case 'close':
            io.openFile.closeErrors.add(failure);
          case 'rename':
            io.renameError = failure;
          case 'stat':
            io.statError = failure;
        }

        final InferenceBackendFailure published = await _publicationFailure(io);

        expect(
          published.summary,
          'The resident smoke publication failed.',
          reason: operation,
        );
        expect(published.toString(), isNot(contains('/private')));
        expect(published.backendUnusable, isTrue);
        expect(io.deleteCalls, 1, reason: operation);
        expect(io.openFile.closeCalls, isPositive, reason: operation);
      }
    },
  );

  test('resident publication validates staging before final rename', () async {
    final _FakeResidentPublicationIo io = _FakeResidentPublicationIo();
    io.statMetadata = const ResidentReferenceFileMetadata(
      type: FileSystemEntityType.file,
      size: 0,
    );

    final InferenceBackendFailure published = await _publicationFailure(io);

    expect(
      published.summary,
      'The resident smoke output did not stage completely.',
    );
    expect(io.renameCalls, 0);
    expect(io.deleteCalls, 1);
    expect(
      io.paths.keys,
      isNot(contains('/virtual/resident/${_challenge().receiptFileName}')),
    );
  });

  test(
    'resident publication preserves primary failure across cleanup failures',
    () async {
      final _FakeResidentPublicationIo io = _FakeResidentPublicationIo();
      io.openFile.writeError = const InferenceBackendFailure(
        summary: 'authoritative write failure',
        backendUnusable: true,
      );
      io.openFile.closeErrors.add(
        const InferenceBackendFailure(summary: 'cleanup close failure'),
      );
      io.deleteError = FileSystemException(
        'cleanup delete failure',
        '/private/cleanup-secret',
      );

      final InferenceBackendFailure published = await _publicationFailure(io);

      expect(published.summary, 'authoritative write failure');
      expect(published.toString(), isNot(contains('/private')));
      expect(io.openFile.closeCalls, 1);
      expect(io.deleteCalls, 1);
    },
  );

  test(
    'resident line publishes to exact challenged paths atomically',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'fonix-resident-receipt-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final ResidentReferenceChallenge challenge = _challenge();
      final String line = _failureLine(challenge);

      await publishResidentReferenceLine(
        line,
        challenge: challenge,
        processId: _processId,
        directory: directory,
      );

      final File output = File(
        '${directory.path}/fonix-reference-receipt-$_challengeValue.txt',
      );
      final File staging = File(
        '${directory.path}/fonix-reference-receipt-$_challengeValue.txt.tmp',
      );
      expect(challenge.receiptFileName, output.uri.pathSegments.last);
      expect(challenge.stagingFileName, staging.uri.pathSegments.last);
      expect(await output.readAsString(), '$line\n');
      expect(await staging.exists(), isFalse);
      await expectLater(
        publishResidentReferenceLine(
          line,
          challenge: challenge,
          processId: _processId,
          directory: directory,
        ),
        throwsA(isA<InferenceBackendFailure>()),
      );
    },
  );

  test(
    'resident line neither accepts nor overwrites another challenge',
    () async {
      final Directory directory = await Directory.systemTemp.createTemp(
        'fonix-resident-other-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final ResidentReferenceChallenge challenge = _challenge();
      final ResidentReferenceChallenge other = _otherChallenge();
      final File otherOutput = File(
        '${directory.path}/${other.receiptFileName}',
      );
      final File otherStaging = File(
        '${directory.path}/${other.stagingFileName}',
      );
      await otherOutput.writeAsString('stale-final');
      await otherStaging.writeAsString('stale-staging');

      await expectLater(
        publishResidentReferenceLine(
          _failureLine(other),
          challenge: challenge,
          processId: _processId,
          directory: directory,
        ),
        throwsA(isA<InferenceBackendFailure>()),
      );
      expect(
        await File('${directory.path}/${challenge.receiptFileName}').exists(),
        isFalse,
      );
      expect(await otherOutput.readAsString(), 'stale-final');
      expect(await otherStaging.readAsString(), 'stale-staging');

      final String wrongProcessJson = residentReferenceSmokeFailureJson(
        StateError('hidden'),
        challenge,
        _processId + 1,
      );
      await expectLater(
        publishResidentReferenceLine(
          '$referenceSmokeFailurePrefix$wrongProcessJson',
          challenge: challenge,
          processId: _processId,
          directory: directory,
        ),
        throwsA(isA<InferenceBackendFailure>()),
      );
      expect(
        await File('${directory.path}/${challenge.receiptFileName}').exists(),
        isFalse,
      );

      await publishResidentReferenceLine(
        _failureLine(challenge),
        challenge: challenge,
        processId: _processId,
        directory: directory,
      );
      expect(await otherOutput.readAsString(), 'stale-final');
      expect(await otherStaging.readAsString(), 'stale-staging');
    },
  );

  test('resident line rejects current stale final and staging paths', () async {
    final ResidentReferenceChallenge challenge = _challenge();
    for (final String fileName in <String>[
      challenge.receiptFileName,
      challenge.stagingFileName,
    ]) {
      final Directory directory = await Directory.systemTemp.createTemp(
        'fonix-resident-stale-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final File stale = File('${directory.path}/$fileName');
      await stale.writeAsString('preserve');

      await expectLater(
        publishResidentReferenceLine(
          _failureLine(challenge),
          challenge: challenge,
          processId: _processId,
          directory: directory,
        ),
        throwsA(isA<InferenceBackendFailure>()),
      );
      expect(await stale.readAsString(), 'preserve');
    }
  });

  test('resident line rejects current final and staging links', () async {
    final ResidentReferenceChallenge challenge = _challenge();
    for (final String fileName in <String>[
      challenge.receiptFileName,
      challenge.stagingFileName,
    ]) {
      final Directory directory = await Directory.systemTemp.createTemp(
        'fonix-resident-link-',
      );
      addTearDown(() => directory.delete(recursive: true));
      final File target = File('${directory.path}/target');
      await target.writeAsString('preserve');
      await Link('${directory.path}/$fileName').create(target.path);

      await expectLater(
        publishResidentReferenceLine(
          _failureLine(challenge),
          challenge: challenge,
          processId: _processId,
          directory: directory,
        ),
        throwsA(isA<InferenceBackendFailure>()),
      );
      expect(await target.readAsString(), 'preserve');
    }
  });

  test('startup receipt owns and validates every native identity', () {
    final List<String> providers = <String>['cpu'];
    final InferenceStartupReceipt receipt = InferenceStartupReceipt(
      runtimeVersion: '1.27.1',
      runtimeSource: 'bundled',
      runtimeOwner: 'application',
      artifactFlavor: 'cpu',
      platform: 'android',
      architecture: 'arm64-v8a',
      shimBuildId: 'android-owner-application-source-bundled',
      artifactSha256:
          '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
      modelSha256: referenceModelSha256,
      registeredProviders: providers,
    );
    providers.add('xnnpack');

    expect(receipt.registeredProviders, <String>['cpu']);
    expect(receipt.shimBuildId, 'android-owner-application-source-bundled');
    expect(
      receipt.artifactSha256,
      '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
    );
    expect(
      () => receipt.registeredProviders.add('xnnpack'),
      throwsUnsupportedError,
    );
  });

  test('startup receipt rejects paths, invalid digests, and duplicates', () {
    InferenceStartupReceipt create({
      String shimBuildId = 'fonix-test',
      String artifactSha256 =
          '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
      Iterable<String> providers = const <String>['cpu'],
    }) => InferenceStartupReceipt(
      runtimeVersion: '1.27.1',
      runtimeSource: 'bundled',
      runtimeOwner: 'application',
      artifactFlavor: 'cpu',
      platform: 'android',
      architecture: 'arm64-v8a',
      shimBuildId: shimBuildId,
      artifactSha256: artifactSha256,
      modelSha256: referenceModelSha256,
      registeredProviders: providers,
    );

    expect(() => create(shimBuildId: '/private/shim'), throwsArgumentError);
    expect(() => create(artifactSha256: 'not-a-digest'), throwsArgumentError);
    expect(
      () => create(providers: const <String>['cpu', 'cpu']),
      throwsArgumentError,
    );
  });
}

ResidentReferenceChallenge _challenge() =>
    ResidentReferenceChallenge.parse(_challengeValue);

ResidentReferenceChallenge _otherChallenge() =>
    ResidentReferenceChallenge.parse(_otherChallengeValue);

String _failureLine(ResidentReferenceChallenge challenge) {
  final String json = residentReferenceSmokeFailureJson(
    StateError('hidden'),
    challenge,
    _processId,
  );
  return '$referenceSmokeFailurePrefix$json';
}

String _repeat(String value, int count) =>
    List<String>.filled(count, value, growable: false).join();

Map<String, Object?> _linePayload(String line) {
  final String prefix = line.startsWith(referenceSmokeReceiptPrefix)
      ? referenceSmokeReceiptPrefix
      : referenceSmokeFailurePrefix;
  return jsonDecode(line.substring(prefix.length)) as Map<String, Object?>;
}

Future<InferenceBackendFailure> _publicationFailure(
  _FakeResidentPublicationIo io,
) async {
  InferenceBackendFailure? result;
  try {
    await publishResidentReferenceLine(
      _failureLine(_challenge()),
      challenge: _challenge(),
      processId: _processId,
      directory: Directory('/virtual/resident'),
      publicationIo: io,
    );
  } on InferenceBackendFailure catch (error) {
    result = error;
  }
  expect(result, isNotNull);
  return result!;
}

final class _FakeResidentPublicationIo
    implements ResidentReferencePublicationIo {
  final _FakeResidentOpenFile openFile = _FakeResidentOpenFile();
  final Map<String, FileSystemEntityType> paths =
      <String, FileSystemEntityType>{};

  int directoryExistsCalls = 0;
  int deleteCalls = 0;
  int renameCalls = 0;
  Object? renameError;
  Object? statError;
  Object? deleteError;
  ResidentReferenceFileMetadata? statMetadata;

  @override
  Future<bool> directoryExists(String path) async {
    directoryExistsCalls += 1;
    return true;
  }

  @override
  Future<FileSystemEntityType> pathType(String path) async =>
      paths[path] ?? FileSystemEntityType.notFound;

  @override
  Future<void> createExclusive(String path) async {
    paths[path] = FileSystemEntityType.file;
  }

  @override
  Future<ResidentReferenceOpenFile> openWriteOnly(String path) async =>
      openFile;

  @override
  Future<void> rename(String from, String to) async {
    renameCalls += 1;
    final Object? error = renameError;
    if (error != null) throw error;
    paths.remove(from);
    paths[to] = FileSystemEntityType.file;
  }

  @override
  Future<ResidentReferenceFileMetadata> stat(String path) async {
    final Object? error = statError;
    if (error != null) throw error;
    return statMetadata ??
        ResidentReferenceFileMetadata(
          type: paths[path] ?? FileSystemEntityType.notFound,
          size: openFile.bytes.length,
        );
  }

  @override
  Future<void> delete(String path) async {
    deleteCalls += 1;
    final Object? error = deleteError;
    if (error != null) throw error;
    paths.remove(path);
  }
}

final class _FakeResidentOpenFile implements ResidentReferenceOpenFile {
  final List<int> bytes = <int>[];
  final List<Object> closeErrors = <Object>[];

  int closeCalls = 0;
  Object? writeError;
  Object? flushError;

  @override
  Future<void> writeFrom(List<int> value) async {
    bytes.addAll(value);
    final Object? error = writeError;
    if (error != null) throw error;
  }

  @override
  Future<void> flush() async {
    final Object? error = flushError;
    if (error != null) throw error;
  }

  @override
  Future<void> close() async {
    closeCalls += 1;
    if (closeErrors.isNotEmpty) throw closeErrors.removeAt(0);
  }
}
