import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_sherpa_reference/main.dart';
import 'package:fonix_sherpa_reference/src/harness_contract.dart';
import 'package:fonix_sherpa_reference/src/qualification_asset_loader.dart';
import 'package:fonix_sherpa_reference/src/qualification_fixtures.dart';
import 'package:fonix_sherpa_reference/src/qualification_harness.dart';

void main() {
  testWidgets('shows the safe committed-template unavailable status', (
    WidgetTester tester,
  ) async {
    final _WidgetHarnessChannel channel = _WidgetHarnessChannel(
      readLaunch: () async => _launch(),
    );
    await tester.pumpWidget(
      HarnessApp(
        harness: QualificationHarness<AndroidQualificationFixtures>(
          channel: channel,
          fixtureLoader: const _AbsentFixtureLoader(),
          executor: (_, _) async => throw StateError('must not execute'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('fixtures are not provisioned'), findsOneWidget);
    expect(
      channel.completions.single.status,
      HarnessCompletionStatus.unavailable,
    );
  });

  testWidgets('does not update state after the app is disposed', (
    WidgetTester tester,
  ) async {
    final Completer<HarnessLaunch> launch = Completer<HarnessLaunch>();
    final _WidgetHarnessChannel channel = _WidgetHarnessChannel(
      readLaunch: () => launch.future,
    );
    await tester.pumpWidget(
      HarnessApp(
        harness: QualificationHarness<AndroidQualificationFixtures>(
          channel: channel,
          fixtureLoader: const _AbsentFixtureLoader(),
          executor: (_, _) async => throw StateError('must not execute'),
        ),
      ),
    );

    await tester.pumpWidget(const SizedBox.shrink());
    launch.complete(_launch());
    await tester.pump();
    await tester.pump();

    expect(tester.takeException(), isNull);
    expect(channel.completions, hasLength(1));
  });
}

HarnessLaunch _launch() => HarnessLaunch.fromPlatform(<Object?, Object?>{
  'schemaVersion': 1,
  'loadOrder': 'dart-first',
  'launchChallengeBase64': base64Encode(<int>[8, 6, 7, 5]),
});

final class _WidgetHarnessChannel implements QualificationHarnessChannel {
  _WidgetHarnessChannel({required Future<HarnessLaunch> Function() readLaunch})
    : _readLaunch = readLaunch;

  final Future<HarnessLaunch> Function() _readLaunch;
  final List<HarnessCompletionPayload> completions =
      <HarnessCompletionPayload>[];

  @override
  Future<HarnessLaunch> readLaunch() => _readLaunch();

  @override
  Future<void> complete(HarnessCompletionPayload payload) async {
    completions.add(payload);
  }
}

final class _AbsentFixtureLoader
    implements QualificationFixtureLoader<AndroidQualificationFixtures> {
  const _AbsentFixtureLoader();

  @override
  Future<QualificationFixtureLoadResult<AndroidQualificationFixtures>>
  load() async =>
      const QualificationFixturesAbsent<AndroidQualificationFixtures>();
}
