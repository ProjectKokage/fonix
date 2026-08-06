import 'dart:collection';
import 'dart:convert';

import 'inference_backend.dart';

const int referenceSmokeSchemaVersion = 1;
const String referenceSmokeReceiptPrefix = 'FONIX_REFERENCE_RECEIPT=';
const int maximumReferenceSmokeReceiptBytes = 16 * 1024;

/// The path-free, closed receipt shared by the macOS and Android smoke paths.
final class ReferenceSmokeReceipt {
  ReferenceSmokeReceipt._({
    required this.startup,
    required this.outputValues,
    required this.activeProviders,
    required this.fullCpuAssignment,
  });

  factory ReferenceSmokeReceipt.fromRun({
    required InferenceStartupReceipt startup,
    required InferenceRunReceipt run,
  }) {
    if (!_sameDoubles(run.outputValues, referenceOutputValues) ||
        !run.fullAssignment ||
        !_sameStrings(run.activeProviders, const <String>['cpu'])) {
      throw const InferenceBackendFailure(
        summary: 'The packaged smoke receipt did not match its contract.',
      );
    }
    return ReferenceSmokeReceipt._(
      startup: startup,
      outputValues: UnmodifiableListView<int>(<int>[
        for (final double value in run.outputValues) value.toInt(),
      ]),
      activeProviders: UnmodifiableListView<String>(
        List<String>.of(run.activeProviders),
      ),
      fullCpuAssignment: run.fullAssignment,
    );
  }

  final InferenceStartupReceipt startup;
  final List<int> outputValues;
  final List<String> activeProviders;
  final bool fullCpuAssignment;

  Map<String, Object?> toMap() =>
      Map<String, Object?>.unmodifiable(<String, Object?>{
        'schemaVersion': referenceSmokeSchemaVersion,
        'status': 'passed',
        'runtimeVersion': startup.runtimeVersion,
        'runtimeSource': startup.runtimeSource,
        'runtimeOwner': startup.runtimeOwner,
        'artifactFlavor': startup.artifactFlavor,
        'platform': startup.platform,
        'architecture': startup.architecture,
        'shimBuildId': startup.shimBuildId,
        'artifactSha256': startup.artifactSha256,
        'modelSha256': startup.modelSha256,
        'outputValues': outputValues,
        'activeProviders': activeProviders,
        'fullCpuAssignment': fullCpuAssignment,
        'doubleClose': 'passed',
      });

  String toJsonString() {
    final String value = jsonEncode(toMap());
    if (value.contains('\n') ||
        value.contains('\r') ||
        utf8.encode(value).length > maximumReferenceSmokeReceiptBytes) {
      throw const InferenceBackendFailure(
        summary: 'The packaged smoke receipt exceeds its size contract.',
      );
    }
    return value;
  }
}

/// Runs the fixed model once and settles all backend ownership before return.
Future<ReferenceSmokeReceipt> runReferenceSmoke(
  InferenceBackend backend,
) async {
  var closed = false;
  try {
    final InferenceStartupReceipt startup = await backend.start();
    final InferenceRunReceipt run = await backend.run(
      InferenceRequest.reference(),
    );
    final ReferenceSmokeReceipt receipt = ReferenceSmokeReceipt.fromRun(
      startup: startup,
      run: run,
    );
    await backend.close();
    await backend.close();
    closed = true;
    return receipt;
  } finally {
    if (!closed) {
      try {
        await backend.close();
      } on Object {
        // Preserve the authoritative smoke failure.
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
