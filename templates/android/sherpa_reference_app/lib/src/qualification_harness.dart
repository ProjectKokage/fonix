import 'harness_channel.dart';
import 'harness_contract.dart';
import 'qualification_asset_loader.dart';
import 'qualification_fixtures.dart';
import 'qualification_state_machine.dart';
import 'real_qualification_drivers.dart';

abstract interface class QualificationHarnessChannel {
  Future<HarnessLaunch> readLaunch();
  Future<void> complete(HarnessCompletionPayload payload);
}

final class PlatformQualificationHarnessChannel
    implements QualificationHarnessChannel {
  const PlatformQualificationHarnessChannel({
    HarnessChannel delegate = const HarnessChannel(),
  }) : _delegate = delegate;

  final HarnessChannel _delegate;

  @override
  Future<HarnessLaunch> readLaunch() => _delegate.readLaunch();

  @override
  Future<void> complete(HarnessCompletionPayload payload) =>
      _delegate.complete(payload);
}

typedef QualificationExecutor<T> =
    Future<HarnessCompletionPayload> Function(HarnessLaunch launch, T fixtures);

enum QualificationHarnessOutcome {
  passed,
  nativeFixturesUnprovisioned,
  qualificationFailed,
  launchRejected,
  publicationFailed,
}

/// One-shot application orchestration with a generic host-test seam.
///
/// Launch validation always precedes asset access. Once a valid launch exists,
/// the sole non-failure unavailable state is an absent qualification marker.
/// Every loader, fixture, driver, or state-machine exception is reduced to the
/// same bounded `qualification-failed` completion without exposing its value.
final class QualificationHarness<T> {
  const QualificationHarness({
    required this.channel,
    required this.fixtureLoader,
    required this.executor,
  });

  final QualificationHarnessChannel channel;
  final QualificationFixtureLoader<T> fixtureLoader;
  final QualificationExecutor<T> executor;

  Future<QualificationHarnessOutcome> run() async {
    final HarnessLaunch launch;
    try {
      launch = await channel.readLaunch();
    } on Object {
      return QualificationHarnessOutcome.launchRejected;
    }

    late final HarnessCompletionPayload completion;
    late final QualificationHarnessOutcome successOutcome;
    try {
      final QualificationFixtureLoadResult<T> loaded = await fixtureLoader
          .load();
      switch (loaded) {
        case QualificationFixturesAbsent<T>():
          completion = HarnessUnavailableResult.nativeFixtures(launch);
          successOutcome =
              QualificationHarnessOutcome.nativeFixturesUnprovisioned;
        case QualificationFixturesReady<T>(:final T fixtures):
          final HarnessCompletionPayload result = await executor(
            launch,
            fixtures,
          );
          if (result.status != HarnessCompletionStatus.passed ||
              result.loadOrder != launch.loadOrder ||
              result.launchChallengeSha256 != launch.launchChallengeSha256) {
            throw StateError('The qualification result is not launch-bound.');
          }
          completion = result;
          successOutcome = QualificationHarnessOutcome.passed;
      }
    } on Object {
      completion = HarnessUnavailableResult.qualificationFailed(launch);
      successOutcome = QualificationHarnessOutcome.qualificationFailed;
    }

    try {
      await channel.complete(completion);
    } on Object {
      return QualificationHarnessOutcome.publicationFailed;
    }
    return successOutcome;
  }
}

QualificationHarness<AndroidQualificationFixtures>
createAndroidQualificationHarness() =>
    QualificationHarness<AndroidQualificationFixtures>(
      channel: const PlatformQualificationHarnessChannel(),
      fixtureLoader: AndroidQualificationAssetLoader(),
      executor: runAndroidDeviceQualification,
    );

Future<HarnessCompletionPayload> runAndroidDeviceQualification(
  HarnessLaunch launch,
  AndroidQualificationFixtures fixtures,
) {
  final AndroidQualificationDriverFactory driverFactory =
      AndroidQualificationDriverFactory(fixtures: fixtures);
  final AuthoritativeLifecyclePublicationSink publicationSink =
      AuthoritativeLifecyclePublicationSink();
  return QualificationStateMachine(
    driverFactory: driverFactory,
    publicationSink: publicationSink,
    pins: fixtures.pins,
    references: fixtures.references,
  ).run(launch);
}
