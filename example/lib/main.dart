import 'dart:convert';
import 'dart:io';

import 'package:flutter/widgets.dart';

import 'app.dart';
import 'src/fonix_inference_backend.dart';
import 'src/inference_backend.dart';

const String _smokeEnvironmentKey = 'FONIX_REFERENCE_SMOKE';
const String _receiptPrefix = 'FONIX_REFERENCE_RECEIPT=';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (Platform.environment[_smokeEnvironmentKey] == '1') {
    final int status = await _runPackagedSmoke();
    await stdout.flush();
    await stderr.flush();
    exit(status);
  }
  runApp(FonixReferenceApp(createBackend: FonixInferenceBackend.new));
}

Future<int> _runPackagedSmoke() async {
  final InferenceBackend backend = FonixInferenceBackend();
  var closed = false;
  try {
    final InferenceStartupReceipt startup = await backend.start();
    final InferenceRunReceipt run = await backend.run(
      InferenceRequest.reference(),
    );
    if (!_sameDoubles(run.outputValues, referenceOutputValues) ||
        !run.fullAssignment ||
        !_sameStrings(run.activeProviders, const <String>['cpu'])) {
      throw const InferenceBackendFailure(
        summary: 'The packaged smoke receipt did not match its contract.',
      );
    }
    await backend.close();
    await backend.close();
    closed = true;
    stdout.writeln(
      '$_receiptPrefix${jsonEncode(<String, Object?>{
        'schemaVersion': 1,
        'status': 'passed',
        'runtimeVersion': startup.runtimeVersion,
        'runtimeSource': startup.runtimeSource,
        'runtimeOwner': startup.runtimeOwner,
        'artifactFlavor': startup.artifactFlavor,
        'platform': startup.platform,
        'architecture': startup.architecture,
        'modelSha256': startup.modelSha256,
        'outputValues': <int>[for (final double value in run.outputValues) value.toInt()],
        'activeProviders': run.activeProviders,
        'fullCpuAssignment': run.fullAssignment,
        'doubleClose': 'passed',
      })}',
    );
    return 0;
  } on Object catch (error) {
    stderr.writeln('Fonix reference smoke failed (${error.runtimeType}).');
    return 1;
  } finally {
    if (!closed) {
      try {
        await backend.close();
      } on Object {
        // Preserve the authoritative smoke failure and exit non-zero.
      }
    }
  }
}

bool _sameDoubles(List<double> first, List<double> second) {
  if (first.length != second.length) return false;
  for (var index = 0; index < first.length; index += 1) {
    if (first[index] != second[index]) return false;
  }
  return true;
}

bool _sameStrings(List<String> first, List<String> second) {
  if (first.length != second.length) return false;
  for (var index = 0; index < first.length; index += 1) {
    if (first[index] != second[index]) return false;
  }
  return true;
}
