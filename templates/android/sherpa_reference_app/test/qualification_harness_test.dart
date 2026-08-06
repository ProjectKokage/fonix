import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_sherpa_reference/src/harness_contract.dart';
import 'package:fonix_sherpa_reference/src/qualification_asset_loader.dart';
import 'package:fonix_sherpa_reference/src/qualification_harness.dart';

void main() {
  group('QualificationHarness', () {
    test('reads launch before reporting an absent marker', () async {
      final List<String> events = <String>[];
      final _FakeHarnessChannel channel = _FakeHarnessChannel(
        launch: _launch(),
        events: events,
      );
      final QualificationHarness<String> harness = QualificationHarness<String>(
        channel: channel,
        fixtureLoader: _FakeFixtureLoader<String>(
          result: const QualificationFixturesAbsent<String>(),
          events: events,
        ),
        executor: (_, _) async => throw StateError('must not execute'),
      );

      expect(
        await harness.run(),
        QualificationHarnessOutcome.nativeFixturesUnprovisioned,
      );
      expect(events, <String>['read-launch', 'load-fixtures', 'complete']);
      expect(channel.completions, hasLength(1));
      expect(
        channel.completions.single.status,
        HarnessCompletionStatus.unavailable,
      );
      expect(channel.completions.single.toJson(), <String, Object?>{
        'schemaVersion': 1,
        'result': 'unavailable',
        'launchChallengeSha256': _launch().launchChallengeSha256,
        'loadOrder': 'dart-first',
        'reason': 'native-fixtures-unprovisioned',
      });
    });

    test('executes a ready fixture and publishes only a bound pass', () async {
      final HarnessLaunch launch = _launch(
        loadOrder: HarnessLoadOrder.sherpaFirst,
      );
      final _FakeHarnessChannel channel = _FakeHarnessChannel(launch: launch);
      var executorCalls = 0;
      final QualificationHarness<String> harness = QualificationHarness<String>(
        channel: channel,
        fixtureLoader: _FakeFixtureLoader<String>(
          result: const QualificationFixturesReady<String>('fixture'),
        ),
        executor: (HarnessLaunch observed, String fixture) async {
          executorCalls += 1;
          expect(observed, same(launch));
          expect(fixture, 'fixture');
          return _PassedPayload(launch);
        },
      );

      expect(await harness.run(), QualificationHarnessOutcome.passed);
      expect(executorCalls, 1);
      expect(channel.completions.single.status, HarnessCompletionStatus.passed);
    });

    test('does not access fixtures after an invalid launch', () async {
      final List<String> events = <String>[];
      final QualificationHarness<String> harness = QualificationHarness<String>(
        channel: _FakeHarnessChannel(
          launch: _launch(),
          readFailure: StateError('invalid launch'),
          events: events,
        ),
        fixtureLoader: _FakeFixtureLoader<String>(
          result: const QualificationFixturesReady<String>('fixture'),
          events: events,
        ),
        executor: (_, _) async => throw StateError('must not execute'),
      );

      expect(await harness.run(), QualificationHarnessOutcome.launchRejected);
      expect(events, <String>['read-launch']);
    });

    test('reduces loader failures to the generic failed payload', () async {
      final HarnessLaunch launch = _launch();
      final _FakeHarnessChannel channel = _FakeHarnessChannel(launch: launch);
      final QualificationHarness<String> harness = QualificationHarness<String>(
        channel: channel,
        fixtureLoader: _FakeFixtureLoader<String>(
          result: const QualificationFixturesAbsent<String>(),
          failure: StateError('private loader detail'),
        ),
        executor: (_, _) async => throw StateError('must not execute'),
      );

      expect(
        await harness.run(),
        QualificationHarnessOutcome.qualificationFailed,
      );
      final HarnessCompletionPayload completion = channel.completions.single;
      expect(completion.status, HarnessCompletionStatus.failed);
      expect(completion.toJson(), <String, Object?>{
        'schemaVersion': 1,
        'result': 'failed',
        'launchChallengeSha256': launch.launchChallengeSha256,
        'loadOrder': 'dart-first',
        'reason': 'qualification-failed',
      });
      expect(jsonEncode(completion.toJson()), isNot(contains('private')));
    });

    test(
      'reduces executor and unbound-result failures to one payload',
      () async {
        for (final QualificationExecutor<String> executor
            in <QualificationExecutor<String>>[
              (_, _) async => throw StateError('private native detail'),
              (HarnessLaunch launch, _) async => _PassedPayload(
                _launch(loadOrder: HarnessLoadOrder.sherpaFirst),
              ),
            ]) {
          final _FakeHarnessChannel channel = _FakeHarnessChannel(
            launch: _launch(),
          );
          final QualificationHarness<String> harness =
              QualificationHarness<String>(
                channel: channel,
                fixtureLoader: _FakeFixtureLoader<String>(
                  result: const QualificationFixturesReady<String>('fixture'),
                ),
                executor: executor,
              );

          expect(
            await harness.run(),
            QualificationHarnessOutcome.qualificationFailed,
          );
          expect(
            channel.completions.single.toJson()['reason'],
            'qualification-failed',
          );
        }
      },
    );

    test(
      'does not attempt a second completion after publication fails',
      () async {
        final _FakeHarnessChannel channel = _FakeHarnessChannel(
          launch: _launch(),
          completionFailure: StateError('complete failed'),
        );
        final QualificationHarness<String> harness =
            QualificationHarness<String>(
              channel: channel,
              fixtureLoader: _FakeFixtureLoader<String>(
                result: const QualificationFixturesAbsent<String>(),
              ),
              executor: (_, _) async => throw StateError('must not execute'),
            );

        expect(
          await harness.run(),
          QualificationHarnessOutcome.publicationFailed,
        );
        expect(channel.completionAttempts, 1);
      },
    );
  });
}

HarnessLaunch _launch({
  HarnessLoadOrder loadOrder = HarnessLoadOrder.dartFirst,
}) => HarnessLaunch.fromPlatform(<Object?, Object?>{
  'schemaVersion': 1,
  'loadOrder': loadOrder.wireValue,
  'launchChallengeBase64': base64Encode(<int>[1, 3, 3, 7]),
});

final class _FakeHarnessChannel implements QualificationHarnessChannel {
  _FakeHarnessChannel({
    required this.launch,
    this.readFailure,
    this.completionFailure,
    List<String>? events,
  }) : events = events ?? <String>[];

  final HarnessLaunch launch;
  final Object? readFailure;
  final Object? completionFailure;
  final List<String> events;
  final List<HarnessCompletionPayload> completions =
      <HarnessCompletionPayload>[];
  var completionAttempts = 0;

  @override
  Future<HarnessLaunch> readLaunch() async {
    events.add('read-launch');
    final Object? failure = readFailure;
    if (failure != null) throw failure;
    return launch;
  }

  @override
  Future<void> complete(HarnessCompletionPayload payload) async {
    events.add('complete');
    completionAttempts += 1;
    completions.add(payload);
    final Object? failure = completionFailure;
    if (failure != null) throw failure;
  }
}

final class _FakeFixtureLoader<T> implements QualificationFixtureLoader<T> {
  _FakeFixtureLoader({required this.result, this.failure, List<String>? events})
    : events = events ?? <String>[];

  final QualificationFixtureLoadResult<T> result;
  final Object? failure;
  final List<String> events;

  @override
  Future<QualificationFixtureLoadResult<T>> load() async {
    events.add('load-fixtures');
    final Object? value = failure;
    if (value != null) throw value;
    return result;
  }
}

final class _PassedPayload implements HarnessCompletionPayload {
  const _PassedPayload(this.launch);

  final HarnessLaunch launch;

  @override
  HarnessCompletionStatus get status => HarnessCompletionStatus.passed;

  @override
  HarnessLoadOrder get loadOrder => launch.loadOrder;

  @override
  String get launchChallengeSha256 => launch.launchChallengeSha256;

  @override
  Map<String, Object?> toJson() => <String, Object?>{
    'schemaVersion': 1,
    'result': 'passed',
    'launchChallengeSha256': launchChallengeSha256,
    'loadOrder': loadOrder.wireValue,
  };
}
