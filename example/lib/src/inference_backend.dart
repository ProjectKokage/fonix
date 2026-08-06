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
    required this.runtimeVersion,
    required this.runtimeSource,
    required this.runtimeOwner,
    required this.artifactFlavor,
    required this.platform,
    required this.architecture,
    required this.modelSha256,
    required Iterable<String> registeredProviders,
  }) : registeredProviders = UnmodifiableListView<String>(
         List<String>.of(registeredProviders),
       );

  final String runtimeVersion;
  final String runtimeSource;
  final String runtimeOwner;
  final String artifactFlavor;
  final String platform;
  final String architecture;
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
       activeProviders = UnmodifiableListView<String>(
         List<String>.of(activeProviders),
       ) {
    if (this.outputValues.length != 6 ||
        this.outputValues.any((double value) => !value.isFinite)) {
      throw ArgumentError('Inference output must contain six finite values.');
    }
    if (elapsed.isNegative) {
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
