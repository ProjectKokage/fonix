import 'dart:async';

import 'package:flutter/material.dart';

import 'src/qualification_fixtures.dart';
import 'src/qualification_harness.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(HarnessApp(harness: createAndroidQualificationHarness()));
}

final class HarnessApp extends StatefulWidget {
  const HarnessApp({super.key, required this.harness});

  final QualificationHarness<AndroidQualificationFixtures> harness;

  @override
  State<HarnessApp> createState() => _HarnessAppState();
}

final class _HarnessAppState extends State<HarnessApp> {
  String _status = 'Reading the trusted-runner launch contract…';

  @override
  void initState() {
    super.initState();
    unawaited(_runQualification());
  }

  Future<void> _runQualification() async {
    final QualificationHarnessOutcome outcome = await widget.harness.run();
    _setStatus(switch (outcome) {
      QualificationHarnessOutcome.passed =>
        'The bounded native qualification completed and was published.',
      QualificationHarnessOutcome.nativeFixturesUnprovisioned =>
        'Harness contract accepted. Native qualification fixtures are not '
            'provisioned in this source template.',
      QualificationHarnessOutcome.qualificationFailed =>
        'Native qualification failed. No native error details were published.',
      QualificationHarnessOutcome.launchRejected =>
        'Launch rejected: the host contract is invalid.',
      QualificationHarnessOutcome.publicationFailed =>
        'The bounded harness result could not be published.',
    });
  }

  void _setStatus(String value) {
    if (!mounted) return;
    setState(() {
      _status = value;
    });
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    home: Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              _status,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ),
        ),
      ),
    ),
  );
}
