import 'dart:collection';

const String referenceModelSha256 =
    '71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10';
const List<double> referenceInputValues = <double>[1, 2, 3, 4, 5, 6];
const List<double> referenceOutputValues = <double>[1, 4, 9, 16, 25, 36];

typedef InferenceBackendFactory = InferenceBackend Function();

abstract interface class InferenceBackend {
  Future<InferenceStartupReceipt> start();

  Future<InferenceRunReceipt> run(InferenceRequest request);

  Future<void> cancel();

  Future<void> close();
}

final class InferenceRequest {
  factory InferenceRequest(Iterable<double> values) {
    final List<double> copied = List<double>.unmodifiable(values);
    if (copied.length != 6 || copied.any((double value) => !value.isFinite)) {
      throw ArgumentError('Inference input must contain six finite values.');
    }
    return InferenceRequest._(copied);
  }

  factory InferenceRequest.reference() =>
      InferenceRequest(referenceInputValues);

  const InferenceRequest._(this.values);

  final List<double> values;
}

final class InferenceStartupReceipt {
  InferenceStartupReceipt({
    required String runtimeVersion,
    required String runtimeSource,
    required String runtimeOwner,
    required String artifactFlavor,
    required String platform,
    required String architecture,
    required String shimBuildId,
    required String artifactSha256,
    required String modelSha256,
    required Iterable<String> registeredProviders,
  }) : runtimeVersion = _closedToken(
         runtimeVersion,
         'runtimeVersion',
         maximumLength: 128,
       ),
       runtimeSource = _closedToken(runtimeSource, 'runtimeSource'),
       runtimeOwner = _closedToken(runtimeOwner, 'runtimeOwner'),
       artifactFlavor = _closedToken(artifactFlavor, 'artifactFlavor'),
       platform = _closedToken(platform, 'platform'),
       architecture = _closedToken(architecture, 'architecture'),
       shimBuildId = _closedToken(
         shimBuildId,
         'shimBuildId',
         maximumLength: 128,
       ),
       artifactSha256 = _lowercaseSha256(artifactSha256, 'artifactSha256'),
       modelSha256 = _lowercaseSha256(modelSha256, 'modelSha256'),
       registeredProviders = _closedProviderList(
         registeredProviders,
         'registeredProviders',
       );

  final String runtimeVersion;
  final String runtimeSource;
  final String runtimeOwner;
  final String artifactFlavor;
  final String platform;
  final String architecture;
  final String shimBuildId;
  final String artifactSha256;
  final String modelSha256;
  final List<String> registeredProviders;
}

final class InferenceRunReceipt {
  InferenceRunReceipt({
    required Iterable<double> outputValues,
    required Iterable<String> activeProviders,
    required this.fullAssignment,
    required this.elapsed,
  }) : outputValues = UnmodifiableListView<double>(
         List<double>.of(outputValues),
       ),
       activeProviders = _closedProviderList(
         activeProviders,
         'activeProviders',
       ) {
    if (this.outputValues.length != 6 ||
        this.outputValues.any((double value) => !value.isFinite)) {
      throw ArgumentError('Inference output must contain six finite values.');
    }
    if (elapsed.isNegative || elapsed > const Duration(minutes: 10)) {
      throw ArgumentError.value(elapsed, 'elapsed');
    }
  }

  final List<double> outputValues;
  final List<String> activeProviders;
  final bool fullAssignment;
  final Duration elapsed;
}

/// A path-free failure translated at the app-owned native-package boundary.
final class InferenceBackendFailure implements Exception {
  const InferenceBackendFailure({
    required this.summary,
    this.backendUnusable = false,
  });

  final String summary;
  final bool backendUnusable;

  @override
  String toString() => summary;
}

String _closedToken(String value, String name, {int maximumLength = 64}) {
  if (value.isEmpty ||
      value.length > maximumLength ||
      !RegExp(r'^[A-Za-z0-9][A-Za-z0-9._+-]*$').hasMatch(value)) {
    throw ArgumentError.value(value, name, 'Must be a bounded identity token.');
  }
  return value;
}

String _lowercaseSha256(String value, String name) {
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
    throw ArgumentError.value(value, name, 'Must be a lowercase SHA-256.');
  }
  return value;
}

List<String> _closedProviderList(Iterable<String> values, String name) {
  final List<String> copied = List<String>.of(values);
  if (copied.isEmpty || copied.length > 8) {
    throw ArgumentError.value(copied, name, 'Must contain 1 to 8 providers.');
  }
  final Set<String> unique = <String>{};
  for (final String value in copied) {
    final String provider = _closedToken(value, name);
    if (!unique.add(provider)) {
      throw ArgumentError.value(copied, name, 'Must not contain duplicates.');
    }
  }
  return UnmodifiableListView<String>(copied);
}
