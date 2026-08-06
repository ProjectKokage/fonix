import 'dart:async';

import 'package:fonix_reference/src/inference_backend.dart';

InferenceStartupReceipt fakeStartupReceipt({
  String platform = 'macos',
  String architecture = 'arm64',
  String shimBuildId = 'fonix-reference-test',
  String artifactSha256 =
      'e42b77a7281cc6e55141bf44fcfbac2c782b823a491bbb6ac33c781dd991f8a6',
}) => InferenceStartupReceipt(
  runtimeVersion: '1.27.1',
  runtimeSource: 'bundled',
  runtimeOwner: 'wrapper',
  artifactFlavor: 'cpu',
  platform: platform,
  architecture: architecture,
  shimBuildId: shimBuildId,
  artifactSha256: artifactSha256,
  modelSha256: referenceModelSha256,
  registeredProviders: const <String>['cpu'],
);

InferenceRunReceipt fakeRunReceipt({
  List<double> output = referenceOutputValues,
}) => InferenceRunReceipt(
  outputValues: output,
  activeProviders: const <String>['cpu'],
  fullAssignment: true,
  elapsed: const Duration(milliseconds: 2),
);

final class FakeInferenceBackend implements InferenceBackend {
  final Completer<InferenceStartupReceipt> startup =
      Completer<InferenceStartupReceipt>();
  final List<Completer<InferenceRunReceipt>> runs =
      <Completer<InferenceRunReceipt>>[];
  final List<InferenceRequest> requests = <InferenceRequest>[];

  int cancelCalls = 0;
  int closeCalls = 0;
  int closeWorkCount = 0;
  Object? cancelError;
  Completer<void>? closeGate;
  Future<void>? _closeFuture;

  @override
  Future<InferenceStartupReceipt> start() => startup.future;

  @override
  Future<InferenceRunReceipt> run(InferenceRequest request) {
    requests.add(request);
    final Completer<InferenceRunReceipt> completer =
        Completer<InferenceRunReceipt>();
    runs.add(completer);
    return completer.future;
  }

  @override
  Future<void> cancel() async {
    cancelCalls += 1;
    final Object? error = cancelError;
    if (error != null) throw error;
  }

  @override
  Future<void> close() {
    closeCalls += 1;
    return _closeFuture ??= Future<void>.sync(() async {
      closeWorkCount += 1;
      await closeGate?.future;
    });
  }
}

final class FakeBackendFactory {
  final List<FakeInferenceBackend> backends = <FakeInferenceBackend>[];

  FakeInferenceBackend call() {
    final FakeInferenceBackend backend = FakeInferenceBackend();
    backends.add(backend);
    return backend;
  }
}
