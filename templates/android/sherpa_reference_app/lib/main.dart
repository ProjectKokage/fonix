import 'dart:async';

import 'package:flutter/material.dart';

import 'src/harness_channel.dart';
import 'src/harness_contract.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const _HarnessApp());
}

final class _HarnessApp extends StatefulWidget {
  const _HarnessApp();

  @override
  State<_HarnessApp> createState() => _HarnessAppState();
}

final class _HarnessAppState extends State<_HarnessApp> {
  String _status = 'Reading the trusted-runner launch contract…';

  @override
  void initState() {
    super.initState();
    unawaited(_reportCurrentCapability());
  }

  Future<void> _reportCurrentCapability() async {
    const HarnessChannel channel = HarnessChannel();
    final HarnessLaunch launch;
    try {
      launch = await channel.readLaunch();
    } on Object {
      _setStatus('Launch rejected: the host contract is invalid.');
      return;
    }

    try {
      await channel.complete(HarnessUnavailableResult.nativeFixtures(launch));
      _setStatus(
        'Harness contract accepted. Native qualification fixtures and '
        'drivers are not provisioned in this source template.',
      );
    } on Object {
      _setStatus('The bounded unavailable result could not be published.');
    }
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
