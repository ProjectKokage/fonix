import 'dart:async';
import 'dart:ui' show AppExitResponse;

import 'package:flutter/material.dart';

import 'src/inference_backend.dart';
import 'src/inference_controller.dart';

final class FonixReferenceApp extends StatelessWidget {
  const FonixReferenceApp({required this.createBackend, super.key});

  final InferenceBackendFactory createBackend;

  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'Fonix Reference',
    theme: ThemeData(
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xff4f46e5),
        brightness: Brightness.light,
      ),
      useMaterial3: true,
    ),
    home: ReferenceInferenceScreen(createBackend: createBackend),
  );
}

final class ReferenceInferenceScreen extends StatefulWidget {
  const ReferenceInferenceScreen({required this.createBackend, super.key});

  final InferenceBackendFactory createBackend;

  @override
  State<ReferenceInferenceScreen> createState() =>
      _ReferenceInferenceScreenState();
}

final class _ReferenceInferenceScreenState
    extends State<ReferenceInferenceScreen>
    with WidgetsBindingObserver {
  late final InferenceController _controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _controller = InferenceController(createBackend: widget.createBackend)
      ..addListener(_onStateChanged);
    unawaited(_controller.start());
  }

  void _onStateChanged() {
    if (mounted) setState(() {});
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        unawaited(_controller.resume());
      case AppLifecycleState.hidden:
      case AppLifecycleState.paused:
        unawaited(_controller.suspend());
      case AppLifecycleState.detached:
        unawaited(_closeIgnoringFailure());
      case AppLifecycleState.inactive:
        break;
    }
  }

  @override
  Future<AppExitResponse> didRequestAppExit() async {
    await _closeIgnoringFailure();
    return AppExitResponse.exit;
  }

  Future<void> _closeIgnoringFailure() async {
    try {
      await _controller.close();
    } on Object {
      // App exit cannot recover a retired native worker.
    }
  }

  Future<void> _disposeController() async {
    await _closeIgnoringFailure();
    _controller.dispose();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller.removeListener(_onStateChanged);
    unawaited(_disposeController());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final InferenceViewState state = _controller.state;
    final bool canRun = state.phase == InferencePhase.ready;
    final bool canRetry =
        state.phase == InferencePhase.failure ||
        state.phase == InferencePhase.cancelled;
    final bool canSuspend =
        state.phase != InferencePhase.closed &&
        state.phase != InferencePhase.suspended &&
        state.phase != InferencePhase.starting;

    return Scaffold(
      appBar: AppBar(title: const Text('Fonix reference')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 720),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Text(
                    'Public API → worker isolate → bundled ONNX Runtime',
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'A deterministic CPU smoke run with explicit assignment '
                    'evidence. This tiny model is not a benchmark.',
                  ),
                  const SizedBox(height: 20),
                  _StatusCard(state: state),
                  const SizedBox(height: 16),
                  _MatrixCard(
                    title: 'Input X · float32 [3, 2]',
                    values: state.request.values,
                    valueKey: const Key('input-values'),
                  ),
                  const SizedBox(height: 12),
                  _MatrixCard(
                    title: 'Output Y · float32 [3, 2]',
                    values: state.runReceipt?.outputValues,
                    valueKey: const Key('output-values'),
                  ),
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: <Widget>[
                      FilledButton.icon(
                        key: const Key('run-button'),
                        onPressed: canRun
                            ? () => unawaited(_controller.run())
                            : null,
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('Run inference'),
                      ),
                      OutlinedButton.icon(
                        key: const Key('cancel-button'),
                        onPressed: state.phase == InferencePhase.running
                            ? () => unawaited(_controller.cancel())
                            : null,
                        icon: const Icon(Icons.stop),
                        label: const Text('Cancel'),
                      ),
                      OutlinedButton.icon(
                        key: const Key('retry-button'),
                        onPressed: canRetry
                            ? () => unawaited(_controller.retry())
                            : null,
                        icon: const Icon(Icons.refresh),
                        label: const Text('Retry'),
                      ),
                      TextButton(
                        key: const Key('lifecycle-button'),
                        onPressed: state.phase == InferencePhase.suspended
                            ? () => unawaited(_controller.resume())
                            : canSuspend
                            ? () => unawaited(_controller.suspend())
                            : null,
                        child: Text(
                          state.phase == InferencePhase.suspended
                              ? 'Resume session'
                              : 'Suspend session',
                        ),
                      ),
                    ],
                  ),
                  if (state.startupReceipt case final receipt?) ...<Widget>[
                    const SizedBox(height: 20),
                    _DiagnosticsCard(startup: receipt, run: state.runReceipt),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

final class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.state});

  final InferenceViewState state;

  @override
  Widget build(BuildContext context) {
    final String status = switch (state.phase) {
      InferencePhase.idle => 'Idle',
      InferencePhase.starting => 'Starting worker…',
      InferencePhase.ready when state.runReceipt != null => 'Run completed',
      InferencePhase.ready => 'Ready',
      InferencePhase.running => 'Inference running…',
      InferencePhase.cancelling => 'Waiting for native run to settle…',
      InferencePhase.cancelled => 'Run cancelled',
      InferencePhase.suspended => 'Session suspended',
      InferencePhase.failure => 'Operation failed',
      InferencePhase.closed => 'Session closed',
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              status,
              key: const Key('status-text'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            if (state.failure case final failure?) ...<Widget>[
              const SizedBox(height: 6),
              Text(
                failure.summary,
                key: const Key('failure-text'),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

final class _MatrixCard extends StatelessWidget {
  const _MatrixCard({
    required this.title,
    required this.values,
    required this.valueKey,
  });

  final String title;
  final List<double>? values;
  final Key valueKey;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 8),
          Text(
            values == null ? '—' : _formatMatrix(values!),
            key: valueKey,
            style: const TextStyle(fontFamily: 'monospace'),
          ),
        ],
      ),
    ),
  );
}

final class _DiagnosticsCard extends StatelessWidget {
  const _DiagnosticsCard({required this.startup, required this.run});

  final InferenceStartupReceipt startup;
  final InferenceRunReceipt? run;

  @override
  Widget build(BuildContext context) => Card(
    key: const Key('diagnostics-card'),
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Diagnostics', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text('ORT ${startup.runtimeVersion} · ${startup.runtimeSource}'),
          Text(
            '${startup.platform} ${startup.architecture} · ${startup.artifactFlavor}',
          ),
          Text('Owner: ${startup.runtimeOwner}'),
          Text('Shim: ${startup.shimBuildId}'),
          Text('Artifact SHA-256: ${startup.artifactSha256}'),
          Text('Registered: ${startup.registeredProviders.join(', ')}'),
          Text(
            run == null
                ? 'Active provider: awaiting run evidence'
                : 'Active: ${run!.activeProviders.join(', ')} · '
                      'full CPU assignment: ${run!.fullAssignment}',
            key: const Key('provider-receipt'),
          ),
        ],
      ),
    ),
  );
}

String _formatMatrix(List<double> values) {
  if (values.length != 6) return 'invalid shape';
  final Iterable<String> rows = <List<double>>[
    values.sublist(0, 2),
    values.sublist(2, 4),
    values.sublist(4, 6),
  ].map((List<double> row) => '[${row.map(_formatValue).join(', ')}]');
  return rows.join('\n');
}

String _formatValue(double value) => value == value.roundToDouble()
    ? value.toInt().toString()
    : value.toString();
