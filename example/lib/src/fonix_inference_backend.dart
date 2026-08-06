import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';
import 'package:fonix/fonix.dart';

import 'inference_backend.dart';

const String _modelAsset = 'assets/models/mul_1.onnx';

final class FonixInferenceBackend implements InferenceBackend {
  FonixInferenceBackend({AssetBundle? assets}) : _assets = assets ?? rootBundle;

  final AssetBundle _assets;

  Future<InferenceStartupReceipt>? _startupFuture;
  Future<void>? _closeFuture;
  OrtIsolateSession? _session;
  OrtIsolateRun? _activeRun;
  Directory? _artifactRoot;
  var _closed = false;

  @override
  Future<InferenceStartupReceipt> start() {
    if (_closed) {
      return Future<InferenceStartupReceipt>.error(
        const InferenceBackendFailure(
          summary: 'The inference backend is already closed.',
          backendUnusable: true,
        ),
      );
    }
    return _startupFuture ??= _start();
  }

  Future<InferenceStartupReceipt> _start() async {
    try {
      final ByteData data = await _assets.load(_modelAsset);
      final Uint8List modelBytes = data.buffer.asUint8List(
        data.offsetInBytes,
        data.lengthInBytes,
      );
      if (modelBytes.length != 130 ||
          sha256.convert(modelBytes).toString() != referenceModelSha256) {
        throw const InferenceBackendFailure(
          summary: 'The bundled reference model failed its identity check.',
          backendUnusable: true,
        );
      }
      final Directory artifactRoot = await Directory.systemTemp.createTemp(
        'fonix-reference-',
      );
      _artifactRoot = artifactRoot;
      final OrtIsolateSession session = await OrtIsolateSession.spawn(
        runtimeSource: const OrtRuntimeSource.bundled(),
        model: OrtModelSource.bytes(
          modelBytes,
          modelId: 'mul-1-sha256-$referenceModelSha256',
        ),
        options: OrtSessionOptions(
          artifactRoot: artifactRoot.path,
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.cpu(
              requirement: OrtProviderRequirement.requireFullAssignment,
            ),
          ],
          fallbackPolicy: OrtFallbackPolicy.rejectAny,
        ),
        maxPendingRuns: 1,
        maxMessageBytes: 1024 * 1024,
        startupTimeout: const Duration(seconds: 30),
      );
      if (_closed) {
        await session.close();
        await _deleteArtifactRoot();
        throw const InferenceBackendFailure(
          summary: 'Inference startup was superseded by shutdown.',
          backendUnusable: true,
        );
      }
      _session = session;
      final OrtDiagnostics diagnostics = session.diagnostics;
      final String? artifactSha256 = diagnostics.artifactSha256;
      if (artifactSha256 == null) {
        throw const InferenceBackendFailure(
          summary: 'The bundled runtime reported no artifact identity.',
          backendUnusable: true,
        );
      }
      return InferenceStartupReceipt(
        runtimeVersion: diagnostics.runtimeVersion,
        runtimeSource: diagnostics.runtimeMode.name,
        runtimeOwner: diagnostics.runtimeOwner.name,
        artifactFlavor: diagnostics.artifactFlavor,
        platform: diagnostics.platform,
        architecture: diagnostics.architecture,
        shimBuildId: diagnostics.shimBuildId,
        artifactSha256: artifactSha256,
        modelSha256: referenceModelSha256,
        registeredProviders: diagnostics.providers
            .where(
              (OrtProviderDiagnostics provider) => provider.registered == true,
            )
            .map((OrtProviderDiagnostics provider) => provider.wrapperId),
      );
    } on InferenceBackendFailure {
      rethrow;
    } on Object catch (error) {
      await _deleteArtifactRootIgnoringFailure();
      throw _translateFailure(error, operation: 'startup');
    }
  }

  @override
  Future<InferenceRunReceipt> run(InferenceRequest request) async {
    final OrtIsolateSession? session = _session;
    if (_closed || session == null) {
      throw const InferenceBackendFailure(
        summary: 'The inference backend is not ready.',
        backendUnusable: true,
      );
    }
    if (_activeRun != null) {
      throw const InferenceBackendFailure(
        summary: 'One inference is already active.',
      );
    }
    final Stopwatch stopwatch = Stopwatch()..start();
    final OrtIsolateRun run = session.startRun(
      inputs: <String, OrtIsolateValue>{
        'X': OrtIsolateTensor.fromFloat32List(
          values: Float32List.fromList(request.values),
          shape: const <int>[3, 2],
        ),
      },
      outputNames: const <String>['Y'],
    );
    _activeRun = run;
    try {
      final OrtIsolateRunResult result = await run.result;
      stopwatch.stop();
      final OrtIsolateTensor output = result.tensor('Y');
      if (!_sameInts(output.shape.dimensions, const <int>[3, 2])) {
        throw const InferenceBackendFailure(
          summary: 'The reference model returned an unexpected output shape.',
        );
      }
      final OrtProviderRunEvidence? evidence = result.providerEvidence;
      if (evidence == null) {
        throw const InferenceBackendFailure(
          summary: 'The run returned no provider-assignment evidence.',
        );
      }
      final List<String> activeProviders = evidence.activeProviderIds.toList()
        ..sort();
      return InferenceRunReceipt(
        outputValues: output.copyFloat32Data(),
        activeProviders: activeProviders,
        fullAssignment: evidence.isFullyAssignedTo('cpu'),
        elapsed: stopwatch.elapsed,
      );
    } on InferenceBackendFailure {
      rethrow;
    } on Object catch (error) {
      throw _translateFailure(error, operation: 'run');
    } finally {
      if (identical(_activeRun, run)) _activeRun = null;
    }
  }

  @override
  Future<void> cancel() async {
    final OrtIsolateRun? run = _activeRun;
    if (run == null) return;
    try {
      await run.cancel();
    } on Object catch (error) {
      throw _translateFailure(error, operation: 'cancel');
    }
  }

  @override
  Future<void> close() {
    final Future<void>? existing = _closeFuture;
    if (existing != null) return existing;
    _closed = true;
    final Future<void> future = _close();
    _closeFuture = future;
    return future;
  }

  Future<void> _close() async {
    final Future<InferenceStartupReceipt>? startup = _startupFuture;
    if (startup != null) {
      try {
        await startup;
      } on Object {
        // Startup owns its partial cleanup.
      }
    }
    final OrtIsolateSession? session = _session;
    _session = null;
    Object? closeError;
    if (session != null) {
      try {
        await session.close();
      } on Object catch (error) {
        closeError = error;
      }
    }
    try {
      await _deleteArtifactRoot();
    } on Object catch (error) {
      closeError ??= error;
    }
    if (closeError != null) {
      throw _translateFailure(closeError, operation: 'close');
    }
  }

  Future<void> _deleteArtifactRootIgnoringFailure() async {
    try {
      await _deleteArtifactRoot();
    } on Object {
      // The original startup failure remains authoritative.
    }
  }

  Future<void> _deleteArtifactRoot() async {
    final Directory? directory = _artifactRoot;
    _artifactRoot = null;
    if (directory == null) return;
    final FileSystemEntityType type = await FileSystemEntity.type(
      directory.path,
      followLinks: false,
    );
    switch (type) {
      case FileSystemEntityType.notFound:
        return;
      case FileSystemEntityType.directory:
        await directory.delete(recursive: true);
      case FileSystemEntityType.file:
      case FileSystemEntityType.link:
      case FileSystemEntityType.unixDomainSock:
      case FileSystemEntityType.pipe:
        throw const InferenceBackendFailure(
          summary: 'The private profiling directory changed type.',
          backendUnusable: true,
        );
    }
  }
}

InferenceBackendFailure _translateFailure(
  Object error, {
  required String operation,
}) {
  if (error is InferenceBackendFailure) return error;
  if (error is OrtException) {
    final bool unusable =
        error is OrtWorkerStartupException ||
        error is OrtWorkerCrashedException ||
        error is OrtWorkerProtocolException ||
        error is OrtWorkerClosedException;
    return InferenceBackendFailure(
      summary:
          '${error.runtimeType} during $operation '
          '(${error.domain.name}:${error.code}, ${error.operation}).',
      backendUnusable: unusable,
    );
  }
  return InferenceBackendFailure(
    summary: 'Inference $operation failed (${error.runtimeType}).',
    backendUnusable: operation == 'startup',
  );
}

bool _sameInts(List<int> first, List<int> second) {
  if (first.length != second.length) return false;
  for (var index = 0; index < first.length; index += 1) {
    if (first[index] != second[index]) return false;
  }
  return true;
}
