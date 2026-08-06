import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:fonix/fonix.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import 'qualification_fixtures.dart';
import 'qualification_state_machine.dart';

const String qualificationFonixLeftInputName = 'input.matrix';
const String qualificationFonixRightInputName = 'weight.matrix';
const String qualificationFonixOutputName = 'output.matrix';
const int qualificationFonixMaximumMessageBytes = 16 * 1024 * 1024;

/// A private temporary root owned by one initialized native driver.
abstract interface class QualificationTemporaryRoot {
  String get absolutePath;

  Future<String> writeBytes(String basename, Uint8List bytes);
  Future<Uint8List> readBytes(String basename);
  Future<void> remove();
}

/// Injectable creator used to prove constructor inertia and cleanup on hosts.
abstract interface class QualificationTemporaryRootFactory {
  Future<QualificationTemporaryRoot> create(String prefix);
}

/// `dart:io` implementation used by the Android qualification application.
final class IoQualificationTemporaryRootFactory
    implements QualificationTemporaryRootFactory {
  const IoQualificationTemporaryRootFactory();

  @override
  Future<QualificationTemporaryRoot> create(String prefix) async {
    if (!RegExp(r'^[a-z][a-z0-9-]{0,63}-$').hasMatch(prefix)) {
      throw ArgumentError.value(prefix, 'prefix', 'is not a safe root prefix');
    }
    final Directory directory = await Directory.systemTemp.createTemp(prefix);
    return _IoQualificationTemporaryRoot(directory);
  }
}

final class _IoQualificationTemporaryRoot
    implements QualificationTemporaryRoot {
  _IoQualificationTemporaryRoot(this._directory);

  final Directory _directory;
  bool _removed = false;
  Future<void>? _removeFuture;

  @override
  String get absolutePath => _directory.absolute.path;

  @override
  Future<String> writeBytes(String basename, Uint8List bytes) async {
    _ensureAvailable();
    _validateBasename(basename);
    if (bytes.isEmpty) {
      throw ArgumentError('bytes must not be empty.');
    }
    final File file = File('$absolutePath${Platform.pathSeparator}$basename');
    await file.writeAsBytes(bytes, flush: true);
    return file.absolute.path;
  }

  @override
  Future<Uint8List> readBytes(String basename) async {
    _ensureAvailable();
    _validateBasename(basename);
    final File file = File('$absolutePath${Platform.pathSeparator}$basename');
    return file.readAsBytes();
  }

  @override
  Future<void> remove() => _removeFuture ??= _remove();

  Future<void> _remove() async {
    if (_removed) return;
    if (await _directory.exists()) {
      await _directory.delete(recursive: true);
    }
    _removed = true;
  }

  void _ensureAvailable() {
    if (_removed || _removeFuture != null) {
      throw StateError('The temporary qualification root is retired.');
    }
  }

  static void _validateBasename(String value) {
    if (!RegExp(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$').hasMatch(value)) {
      throw ArgumentError.value(value, 'basename', 'is not a safe basename');
    }
  }
}

/// The exact inputs used to create one isolate-owned Fonix session.
final class QualificationFonixSessionConfiguration {
  QualificationFonixSessionConfiguration({
    required Uint8List modelBytes,
    required this.modelSha256,
    required this.referenceInputSha256,
    required this.referenceOutputSha256,
    required this.cancellationModelSha256,
    required this.cancellationInputSha256,
    required this.artifactRoot,
  }) : _modelBytes = Uint8List.fromList(modelBytes) {
    if (_modelBytes.isEmpty) {
      throw ArgumentError('modelBytes must not be empty.');
    }
    for (final String digest in <String>[
      modelSha256,
      referenceInputSha256,
      referenceOutputSha256,
      cancellationModelSha256,
      cancellationInputSha256,
    ]) {
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(digest)) {
        throw ArgumentError('Fixture identities must be lowercase SHA-256.');
      }
    }
    if (sha256.convert(_modelBytes).toString() != modelSha256 ||
        cancellationModelSha256 != modelSha256) {
      throw ArgumentError(
        'The model bytes and shared cancellation identity must match.',
      );
    }
    if (artifactRoot.isEmpty || artifactRoot.contains('\u0000')) {
      throw ArgumentError('artifactRoot must be a bounded native path.');
    }
  }

  final Uint8List _modelBytes;
  final String modelSha256;
  final String referenceInputSha256;
  final String referenceOutputSha256;
  final String cancellationModelSha256;
  final String cancellationInputSha256;
  final String artifactRoot;

  Uint8List get modelBytes => Uint8List.fromList(_modelBytes);
}

enum QualificationBackendCancellationDisposition {
  nativeTerminationRequested,
  queuedRunRemoved,
  notCancelled,
}

/// A typed backend settlement. Cancellation is never represented by bytes.
final class QualificationFonixBackendSettlement {
  QualificationFonixBackendSettlement.completed(Uint8List outputBytes)
    : cancelled = false,
      _outputBytes = Uint8List.fromList(outputBytes);

  const QualificationFonixBackendSettlement.cancelled()
    : cancelled = true,
      _outputBytes = null;

  final bool cancelled;
  final Uint8List? _outputBytes;

  Uint8List? get outputBytes {
    final Uint8List? value = _outputBytes;
    return value == null ? null : Uint8List.fromList(value);
  }
}

abstract interface class QualificationFonixBackendRun {
  Future<QualificationFonixBackendSettlement> get settled;

  Future<QualificationBackendCancellationDisposition> cancelWithDisposition();
}

abstract interface class QualificationFonixBackendSession {
  int get outstandingRuns;

  FonixInitializationObservation initializationObservation();
  QualificationFonixBackendRun startRun(QualificationMatrixInput input);
  Future<void> close();
}

abstract interface class QualificationFonixSessionSpawner {
  Future<QualificationFonixBackendSession> spawn(
    QualificationFonixSessionConfiguration configuration,
  );
}

/// Real Fonix backend using one process-runtime `OrtIsolateSession`.
final class OrtIsolateQualificationSessionSpawner
    implements QualificationFonixSessionSpawner {
  const OrtIsolateQualificationSessionSpawner();

  @override
  Future<QualificationFonixBackendSession> spawn(
    QualificationFonixSessionConfiguration configuration,
  ) async {
    final OrtIsolateSession session = await OrtIsolateSession.spawn(
      model: OrtModelSource.bytes(
        configuration.modelBytes,
        modelId: 'matmul64-${configuration.modelSha256}',
      ),
      runtimeSource: OrtRuntimeSource.process(
        preferredLibraryNames: const <String>['libonnxruntime.so'],
      ),
      options: OrtSessionOptions(
        intraOpThreads: 1,
        interOpThreads: 1,
        deterministicCompute: true,
        artifactRoot: configuration.artifactRoot,
        providers: <OrtExecutionProvider>[
          OrtExecutionProvider.cpu(
            requirement: OrtProviderRequirement.requireFullAssignment,
          ),
        ],
        fallbackPolicy: OrtFallbackPolicy.rejectAny,
        sessionLogId: 'fonix-sherpa-qualification',
      ),
      requiredApi: OrtApiVersion.v27,
      logSeverity: OrtLogSeverity.warning,
      logId: 'fonix-sherpa-qualification-worker',
      maxPendingRuns: 1,
      maxMessageBytes: qualificationFonixMaximumMessageBytes,
      startupTimeout: const Duration(seconds: 30),
    );
    return _OrtIsolateQualificationSession(session, configuration);
  }
}

final class _OrtIsolateQualificationSession
    implements QualificationFonixBackendSession {
  const _OrtIsolateQualificationSession(this._session, this._configuration);

  final OrtIsolateSession _session;
  final QualificationFonixSessionConfiguration _configuration;

  @override
  int get outstandingRuns => _session.outstandingRuns;

  @override
  FonixInitializationObservation initializationObservation() {
    if (!_sameNames(_session.inputNames, const <String>{
          qualificationFonixLeftInputName,
          qualificationFonixRightInputName,
        }) ||
        !_sameNames(_session.outputNames, const <String>{
          qualificationFonixOutputName,
        })) {
      throw const FormatException(
        'The Fonix qualification model has unexpected input or output names.',
      );
    }
    final OrtDiagnostics diagnostics = _session.diagnostics;
    return FonixInitializationObservation(
      runtimeOwner: diagnostics.runtimeOwner.name,
      runtimeSource: diagnostics.runtimeMode.name,
      ortVersion: diagnostics.runtimeVersion,
      requiredOrtApi: diagnostics.requiredOrtApiVersion,
      negotiatedOrtApi: diagnostics.negotiatedOrtApiVersion,
      shimAbi: diagnostics.shimAbiVersion,
      shimBuildId: diagnostics.shimBuildId,
      modelSha256: _configuration.modelSha256,
      referenceInputSha256: _configuration.referenceInputSha256,
      referenceOutputSha256: _configuration.referenceOutputSha256,
      cancellationModelSha256: _configuration.cancellationModelSha256,
      cancellationInputSha256: _configuration.cancellationInputSha256,
    );
  }

  @override
  QualificationFonixBackendRun startRun(QualificationMatrixInput input) {
    final OrtIsolateRun run = _session.startRun(
      inputs: <String, OrtIsolateValue>{
        qualificationFonixLeftInputName: OrtIsolateTensor.fromFloat32List(
          values: input.left,
          shape: <int>[input.rows, input.inner],
        ),
        qualificationFonixRightInputName: OrtIsolateTensor.fromFloat32List(
          values: input.right,
          shape: <int>[input.inner, input.columns],
        ),
      },
      outputNames: const <String>[qualificationFonixOutputName],
    );
    return _OrtIsolateQualificationRun(
      run,
      expectedRows: input.rows,
      expectedColumns: input.columns,
    );
  }

  @override
  Future<void> close() => _session.close();

  static bool _sameNames(List<String> actual, Set<String> expected) =>
      actual.length == expected.length && actual.every(expected.contains);
}

final class _OrtIsolateQualificationRun
    implements QualificationFonixBackendRun {
  _OrtIsolateQualificationRun(
    this._run, {
    required this.expectedRows,
    required this.expectedColumns,
  });

  final OrtIsolateRun _run;
  final int expectedRows;
  final int expectedColumns;
  Future<QualificationFonixBackendSettlement>? _settlement;

  @override
  Future<QualificationFonixBackendSettlement> get settled =>
      _settlement ??= _settle();

  Future<QualificationFonixBackendSettlement> _settle() async {
    final OrtIsolateRunResult result;
    try {
      result = await _run.result;
    } on OrtRunCancelledException {
      return const QualificationFonixBackendSettlement.cancelled();
    }
    final OrtIsolateTensor output = result.tensor(qualificationFonixOutputName);
    if (output.elementType != OrtTensorElementType.float32 ||
        !_sameShape(output.shape.dimensions, <int>[
          expectedRows,
          expectedColumns,
        ])) {
      throw const FormatException(
        'Fonix qualification output has the wrong type or shape.',
      );
    }
    final Float32List values = output.copyFloat32Data();
    final ByteData bytes = ByteData(values.length * 4);
    for (var index = 0; index < values.length; index += 1) {
      bytes.setFloat32(index * 4, values[index], Endian.little);
    }
    return QualificationFonixBackendSettlement.completed(
      bytes.buffer.asUint8List(),
    );
  }

  @override
  Future<QualificationBackendCancellationDisposition>
  cancelWithDisposition() async {
    final OrtRunCancellationDisposition value = await _run
        .cancelWithDisposition();
    return switch (value) {
      OrtRunCancellationDisposition.nativeTerminationRequested =>
        QualificationBackendCancellationDisposition.nativeTerminationRequested,
      OrtRunCancellationDisposition.queuedRunRemoved =>
        QualificationBackendCancellationDisposition.queuedRunRemoved,
      OrtRunCancellationDisposition.notCancelled =>
        QualificationBackendCancellationDisposition.notCancelled,
    };
  }
}

/// Closed configuration passed to the real sherpa VAD factory.
final class QualificationSherpaDetectorConfiguration {
  const QualificationSherpaDetectorConfiguration({required this.modelPath});

  final String modelPath;
  int get sampleRate => androidSherpaSampleRate;
  int get windowSize => androidSherpaWindowSamples;
  int get numThreads => 1;
  String get provider => 'cpu';
  double get threshold => 0.5;
  double get minimumSpeechSeconds => 0.25;
  double get minimumSilenceSeconds => 0.8;
  double get maximumSpeechSeconds => 30.0;
  double get bufferSeconds => 60.0;
  bool get debug => false;
}

abstract interface class QualificationSherpaDetector {
  void acceptWaveform(Float32List samples);
  bool isEmpty();
  bool isDetected();
  VadSegment front();
  void pop();
  void clear();
  void reset();
  void flush();
  void free();
}

/// Injectable process-global sherpa binding and detector surface.
abstract interface class QualificationSherpaApi {
  void initializeBindings();
  String getVersion();
  String getGitSha1();
  QualificationSherpaDetector createDetector(
    QualificationSherpaDetectorConfiguration configuration,
  );
}

/// Real `sherpa_onnx` 1.13.4 API. Its process-global binding is initialized
/// at most once, even when more than one driver factory exists.
final class SherpaOnnxQualificationApi implements QualificationSherpaApi {
  const SherpaOnnxQualificationApi();

  static bool _bindingsInitialized = false;

  @override
  void initializeBindings() {
    if (_bindingsInitialized) return;
    sherpa.initBindings();
    _bindingsInitialized = true;
  }

  @override
  String getVersion() => sherpa.getVersion();

  @override
  String getGitSha1() => sherpa.getGitSha1();

  @override
  QualificationSherpaDetector createDetector(
    QualificationSherpaDetectorConfiguration configuration,
  ) {
    final sherpa.VoiceActivityDetector detector = sherpa.VoiceActivityDetector(
      config: sherpa.VadModelConfig(
        sileroVad: sherpa.SileroVadModelConfig(
          model: configuration.modelPath,
          threshold: configuration.threshold,
          minSilenceDuration: configuration.minimumSilenceSeconds,
          minSpeechDuration: configuration.minimumSpeechSeconds,
          windowSize: configuration.windowSize,
          maxSpeechDuration: configuration.maximumSpeechSeconds,
        ),
        sampleRate: configuration.sampleRate,
        numThreads: configuration.numThreads,
        provider: configuration.provider,
        debug: configuration.debug,
      ),
      bufferSizeInSeconds: configuration.bufferSeconds,
    );
    return _SherpaOnnxQualificationDetector(detector);
  }
}

final class _SherpaOnnxQualificationDetector
    implements QualificationSherpaDetector {
  const _SherpaOnnxQualificationDetector(this._detector);

  final sherpa.VoiceActivityDetector _detector;

  @override
  void acceptWaveform(Float32List samples) => _detector.acceptWaveform(samples);

  @override
  void clear() => _detector.clear();

  @override
  void flush() => _detector.flush();

  @override
  void free() => _detector.free();

  @override
  VadSegment front() {
    final sherpa.SpeechSegment segment = _detector.front();
    return VadSegment(
      startSample: segment.start,
      sampleCount: segment.samples.length,
    );
  }

  @override
  bool isDetected() => _detector.isDetected();

  @override
  bool isEmpty() => _detector.isEmpty();

  @override
  void pop() => _detector.pop();

  @override
  void reset() => _detector.reset();
}

/// Real driver factory integrated with the existing qualification state
/// machine. Construction and `create*` methods are native-inert.
final class AndroidQualificationDriverFactory
    implements QualificationDriverFactory {
  AndroidQualificationDriverFactory({
    required this.fixtures,
    QualificationFonixSessionSpawner fonixSpawner =
        const OrtIsolateQualificationSessionSpawner(),
    QualificationSherpaApi sherpaApi = const SherpaOnnxQualificationApi(),
    QualificationTemporaryRootFactory temporaryRoots =
        const IoQualificationTemporaryRootFactory(),
  }) : _fonixSpawner = fonixSpawner,
       _sherpaApi = sherpaApi,
       _temporaryRoots = temporaryRoots;

  final AndroidQualificationFixtures fixtures;
  final QualificationFonixSessionSpawner _fonixSpawner;
  final QualificationSherpaApi _sherpaApi;
  final QualificationTemporaryRootFactory _temporaryRoots;

  var _fonixSessionsCreated = 0;
  var _fonixSessionsClosed = 0;
  var _sherpaDetectorsCreated = 0;
  var _sherpaDetectorsFreed = 0;
  var _temporaryRootsCreated = 0;
  var _temporaryRootsRemoved = 0;
  Future<void>? _sherpaBindingsInitialization;

  @override
  int get fonixSessionsCreated => _fonixSessionsCreated;

  @override
  int get fonixSessionsClosed => _fonixSessionsClosed;

  @override
  int get sherpaDetectorsCreated => _sherpaDetectorsCreated;

  @override
  int get sherpaDetectorsFreed => _sherpaDetectorsFreed;

  @override
  int get temporaryRootsCreated => _temporaryRootsCreated;

  @override
  int get temporaryRootsRemoved => _temporaryRootsRemoved;

  @override
  int get temporaryRootsRemaining =>
      _temporaryRootsCreated - _temporaryRootsRemoved;

  @override
  FonixQualificationDriver createFonix() =>
      _AndroidFonixQualificationDriver(this);

  @override
  SherpaQualificationDriver createSherpa() =>
      _AndroidSherpaQualificationDriver(this);

  Future<_TrackedQualificationTemporaryRoot> _createRoot(String prefix) async {
    final QualificationTemporaryRoot root = await _temporaryRoots.create(
      prefix,
    );
    _temporaryRootsCreated += 1;
    return _TrackedQualificationTemporaryRoot(
      root,
      onRemoved: () {
        _temporaryRootsRemoved += 1;
      },
    );
  }

  Future<QualificationFonixBackendSession> _spawnFonix(
    QualificationFonixSessionConfiguration configuration,
  ) async {
    final QualificationFonixBackendSession session = await _fonixSpawner.spawn(
      configuration,
    );
    _fonixSessionsCreated += 1;
    return session;
  }

  void _fonixClosed() {
    _fonixSessionsClosed += 1;
  }

  Future<void> _ensureSherpaBindings() => _sherpaBindingsInitialization ??=
      Future<void>.sync(_sherpaApi.initializeBindings);

  QualificationSherpaDetector _createSherpaDetector(
    QualificationSherpaDetectorConfiguration configuration,
  ) {
    final QualificationSherpaDetector detector = _sherpaApi.createDetector(
      configuration,
    );
    _sherpaDetectorsCreated += 1;
    return detector;
  }

  void _sherpaFreed() {
    _sherpaDetectorsFreed += 1;
  }
}

final class _TrackedQualificationTemporaryRoot
    implements QualificationTemporaryRoot {
  _TrackedQualificationTemporaryRoot(this._delegate, {required this.onRemoved});

  final QualificationTemporaryRoot _delegate;
  final void Function() onRemoved;
  Future<void>? _removeFuture;
  bool _counted = false;

  @override
  String get absolutePath => _delegate.absolutePath;

  @override
  Future<Uint8List> readBytes(String basename) => _delegate.readBytes(basename);

  @override
  Future<void> remove() => _removeFuture ??= _remove();

  Future<void> _remove() async {
    await _delegate.remove();
    if (!_counted) {
      _counted = true;
      onRemoved();
    }
  }

  @override
  Future<String> writeBytes(String basename, Uint8List bytes) =>
      _delegate.writeBytes(basename, bytes);
}

final class _AndroidFonixQualificationDriver
    implements FonixQualificationDriver {
  _AndroidFonixQualificationDriver(this._factory);

  final AndroidQualificationDriverFactory _factory;
  QualificationFonixBackendSession? _session;
  _TrackedQualificationTemporaryRoot? _root;
  Future<FonixInitializationObservation>? _initialization;
  Future<void>? _cleanupFuture;
  Future<void>? _closeFuture;
  var _alive = false;
  var _closeRequested = false;

  @override
  bool get isAlive => _alive && !_closeRequested;

  @override
  int get outstandingRuns => _session?.outstandingRuns ?? 0;

  @override
  Future<FonixInitializationObservation> initialize() {
    if (_closeRequested) {
      return Future<FonixInitializationObservation>.error(
        StateError('The Fonix qualification driver is closed.'),
      );
    }
    return _initialization ??= _initialize();
  }

  Future<FonixInitializationObservation> _initialize() async {
    try {
      final _TrackedQualificationTemporaryRoot root = await _factory
          ._createRoot('fonix-sherpa-profile-');
      _root = root;
      _throwIfClosing();
      final Uint8List modelBytes = _factory.fixtures.consumeFonixModel();
      final QualificationFonixBackendSession session = await _factory
          ._spawnFonix(
            QualificationFonixSessionConfiguration(
              modelBytes: modelBytes,
              modelSha256: _factory.fixtures.pins.fonixModelSha256,
              referenceInputSha256: _factory.fixtures.pins.fonixInputSha256,
              referenceOutputSha256:
                  _factory.fixtures.pins.fonixReferenceOutputSha256,
              cancellationModelSha256:
                  _factory.fixtures.pins.fonixCancellationModelSha256,
              cancellationInputSha256:
                  _factory.fixtures.pins.fonixCancellationInputSha256,
              artifactRoot: root.absolutePath,
            ),
          );
      _session = session;
      _throwIfClosing();
      final FonixInitializationObservation observation = session
          .initializationObservation();
      await _requireReferenceSettlement(
        session.startRun(_factory.fixtures.referenceMatrix).settled,
      );
      _throwIfClosing();
      if (session.outstandingRuns != 0) {
        throw StateError('Fonix initialization left an outstanding run.');
      }
      _alive = true;
      return observation;
    } on Object catch (error, stackTrace) {
      return _cleanupAfterFailure(error, stackTrace);
    }
  }

  @override
  Future<bool> probeAlive() async {
    final QualificationFonixBackendSession? session = _session;
    if (!isAlive || session == null || session.outstandingRuns != 0) {
      return false;
    }
    try {
      await _requireReferenceSettlement(
        session.startRun(_factory.fixtures.referenceMatrix).settled,
      );
      return isAlive && session.outstandingRuns == 0;
    } on Object {
      return false;
    }
  }

  @override
  FonixQualificationRun startRun(FonixRunPurpose purpose) {
    final QualificationFonixBackendSession? session = _session;
    if (!isAlive || session == null) {
      throw StateError('The Fonix qualification driver is not initialized.');
    }
    final QualificationFonixBackendRun run = session.startRun(
      _factory.fixtures.matrixFor(purpose),
    );
    return _AndroidFonixQualificationRun(
      run,
      fixtures: _factory.fixtures,
      requireReferenceOutput: purpose != FonixRunPurpose.cancellation,
    );
  }

  @override
  Future<void> close() => _closeFuture ??= _close();

  Future<void> _close() async {
    _closeRequested = true;
    _alive = false;
    final Future<FonixInitializationObservation>? initialization =
        _initialization;
    if (initialization != null) {
      try {
        await initialization;
      } on Object {
        // Initialization performs the same authoritative cleanup.
      }
    }
    await _cleanup();
  }

  Future<Never> _cleanupAfterFailure(
    Object initializationError,
    StackTrace initializationStack,
  ) async {
    try {
      await _cleanup();
    } on Object {
      rethrow;
    }
    Error.throwWithStackTrace(initializationError, initializationStack);
  }

  Future<void> _cleanup() => _cleanupFuture ??= _performCleanup();

  Future<void> _performCleanup() async {
    _alive = false;
    Object? firstError;
    StackTrace? firstStack;
    final QualificationFonixBackendSession? session = _session;
    _session = null;
    if (session != null) {
      try {
        await session.close();
        _factory._fonixClosed();
      } on Object catch (error, stackTrace) {
        firstError = error;
        firstStack = stackTrace;
      }
    }
    final _TrackedQualificationTemporaryRoot? root = _root;
    _root = null;
    if (root != null) {
      try {
        await root.remove();
      } on Object catch (error, stackTrace) {
        firstError ??= error;
        firstStack ??= stackTrace;
      }
    }
    if (firstError != null) {
      Error.throwWithStackTrace(firstError, firstStack!);
    }
  }

  Future<void> _requireReferenceSettlement(
    Future<QualificationFonixBackendSettlement> future,
  ) async {
    final QualificationFonixBackendSettlement settlement = await future;
    final Uint8List? output = settlement.outputBytes;
    if (settlement.cancelled || output == null) {
      throw StateError('Fonix liveness inference was cancelled.');
    }
    _factory.fixtures.validateFonixReferenceOutput(output);
  }

  void _throwIfClosing() {
    if (_closeRequested) {
      throw StateError('Fonix initialization was superseded by close.');
    }
  }
}

final class _AndroidFonixQualificationRun implements FonixQualificationRun {
  _AndroidFonixQualificationRun(
    this._backend, {
    required this.fixtures,
    required this.requireReferenceOutput,
  });

  final QualificationFonixBackendRun _backend;
  final AndroidQualificationFixtures fixtures;
  final bool requireReferenceOutput;
  Future<FonixRunResult>? _settlement;

  @override
  Future<FonixRunResult> get settled => _settlement ??= _settle();

  Future<FonixRunResult> _settle() async {
    final QualificationFonixBackendSettlement value = await _backend.settled;
    if (value.cancelled) return const FonixRunResult.cancelled();
    final Uint8List? bytes = value.outputBytes;
    if (bytes == null) {
      throw StateError('A completed Fonix run omitted its output.');
    }
    if (requireReferenceOutput) fixtures.validateFonixReferenceOutput(bytes);
    return FonixRunResult.completed(bytes);
  }

  @override
  Future<FonixCancellationDisposition> cancelWithDisposition() async {
    final QualificationBackendCancellationDisposition value = await _backend
        .cancelWithDisposition();
    return switch (value) {
      QualificationBackendCancellationDisposition.nativeTerminationRequested =>
        FonixCancellationDisposition.nativeTerminationRequested,
      QualificationBackendCancellationDisposition.queuedRunRemoved =>
        FonixCancellationDisposition.queuedRunRemoved,
      QualificationBackendCancellationDisposition.notCancelled =>
        FonixCancellationDisposition.notCancelled,
    };
  }
}

final class _AndroidSherpaQualificationDriver
    implements SherpaQualificationDriver {
  _AndroidSherpaQualificationDriver(this._factory);

  final AndroidQualificationDriverFactory _factory;
  QualificationSherpaDetector? _detector;
  _TrackedQualificationTemporaryRoot? _root;
  Future<SherpaInitializationObservation>? _initialization;
  Future<void>? _cleanupFuture;
  Future<void>? _closeFuture;
  var _alive = false;
  var _closeRequested = false;
  var _acceptedFrames = 0;
  var _flushCalls = 0;
  var _publishedSegments = 0;

  @override
  bool get isAlive => _alive && !_closeRequested;

  @override
  int get acceptedFrames => _acceptedFrames;

  @override
  int get flushCalls => _flushCalls;

  @override
  int get publishedSegments => _publishedSegments;

  @override
  int get queuedSegments {
    final QualificationSherpaDetector? detector = _detector;
    if (!isAlive || detector == null) return 0;
    // sherpa exposes queue emptiness but not its cardinality. One is an honest
    // non-empty sentinel; only zero is used as an exact cleanup claim.
    return detector.isEmpty() ? 0 : 1;
  }

  @override
  Future<SherpaInitializationObservation> initialize() {
    if (_closeRequested) {
      return Future<SherpaInitializationObservation>.error(
        StateError('The Sherpa qualification driver is closed.'),
      );
    }
    return _initialization ??= _initialize();
  }

  Future<SherpaInitializationObservation> _initialize() async {
    try {
      await _factory._ensureSherpaBindings();
      _throwIfClosing();
      final _TrackedQualificationTemporaryRoot root = await _factory
          ._createRoot('fonix-sherpa-vad-');
      _root = root;
      final Uint8List model = _factory.fixtures.consumeSherpaModel();
      const String modelBasename = 'silero-vad.onnx';
      final String modelPath = await root.writeBytes(modelBasename, model);
      final Uint8List stagedModel = await root.readBytes(modelBasename);
      _factory.fixtures.validateStagedSherpaModel(stagedModel);
      _throwIfClosing();
      final QualificationSherpaDetectorConfiguration configuration =
          QualificationSherpaDetectorConfiguration(modelPath: modelPath);
      final QualificationSherpaDetector detector = _factory
          ._createSherpaDetector(configuration);
      _detector = detector;
      _throwIfClosing();
      final String observedVersion = _factory._sherpaApi.getVersion();
      final String observedRevision = _factory._sherpaApi.getGitSha1();
      if (observedVersion != androidSherpaPackageVersion ||
          !androidSherpaNativeRevision.startsWith(observedRevision)) {
        throw const QualificationFailure('invalid-sherpa-diagnostics');
      }
      final SherpaInitializationObservation observation =
          SherpaInitializationObservation(
            getVersion: observedVersion,
            getGitSha1: observedRevision,
            profile: SherpaProfileObservation(
              id: _factory.fixtures.pins.sherpaProfileId,
              provider: configuration.provider,
              sampleRateHz: configuration.sampleRate,
              windowSamples: configuration.windowSize,
              numThreads: configuration.numThreads,
              thresholdMillionths: (configuration.threshold * 1000000).round(),
              minimumSpeechMilliseconds:
                  (configuration.minimumSpeechSeconds * 1000).round(),
              minimumSilenceMilliseconds:
                  (configuration.minimumSilenceSeconds * 1000).round(),
              maximumSpeechMilliseconds:
                  (configuration.maximumSpeechSeconds * 1000).round(),
              bufferMilliseconds: (configuration.bufferSeconds * 1000).round(),
            ),
            modelSha256: _factory.fixtures.pins.sherpaModelSha256,
            audioSha256: _factory.fixtures.pins.sherpaAudioSha256,
            referenceSha256: _factory.fixtures.pins.sherpaReferenceSha256,
          );
      if (!detector.isEmpty() || detector.isDetected()) {
        throw StateError('A fresh Sherpa detector has non-empty native state.');
      }
      _throwIfClosing();
      _alive = true;
      return observation;
    } on Object catch (error, stackTrace) {
      return _cleanupAfterFailure(error, stackTrace);
    }
  }

  @override
  Future<bool> probeAlive() async {
    final QualificationSherpaDetector? detector = _detector;
    if (!isAlive || detector == null) return false;
    try {
      return detector.isEmpty() && !detector.isDetected() && isAlive;
    } on Object {
      return false;
    }
  }

  @override
  Future<SherpaObservation> runReference(SherpaRunPurpose purpose) async {
    final QualificationSherpaDetector detector = _requireDetector();
    detector.clear();
    detector.reset();
    final QualificationPcmAudio audio = _factory.fixtures.audio;
    for (var frameIndex = 0; frameIndex < audio.frameCount; frameIndex += 1) {
      detector.acceptWaveform(audio.frame(frameIndex));
      _acceptedFrames += 1;
    }
    detector.flush();
    _flushCalls += 1;

    final List<VadSegment> segments = <VadSegment>[];
    var previousEnd = 0;
    while (!detector.isEmpty()) {
      if (segments.length >= 32) {
        throw StateError('Sherpa emitted more than 32 bounded segments.');
      }
      final VadSegment segment = detector.front();
      if (segment.startSample < previousEnd ||
          segment.startSample < 0 ||
          segment.sampleCount <= 0 ||
          segment.startSample > audio.sampleCount ||
          segment.sampleCount > audio.sampleCount - segment.startSample) {
        throw StateError('Sherpa emitted an invalid or overlapping segment.');
      }
      detector.pop();
      segments.add(segment);
      _publishedSegments += 1;
      previousEnd = segment.startSample + segment.sampleCount;
    }
    final bool empty = detector.isEmpty();
    final bool detected = detector.isDetected();
    if (!empty || detected) {
      throw StateError('Sherpa did not drain to a stable native state.');
    }
    return SherpaObservation(
      sourceSamples: audio.sampleCount,
      submittedSamples: audio.paddedSampleCount,
      segments: segments,
      queueEmptyAfterDrain: empty,
      detectedAfterDrain: detected,
    );
  }

  @override
  Future<void> acceptCancellationFrame() async {
    final QualificationSherpaDetector detector = _requireDetector();
    detector.clear();
    detector.reset();
    final QualificationPcmAudio audio = _factory.fixtures.audio;
    if (audio.frameCount < 2) {
      throw StateError('Sherpa cancellation requires at least two frames.');
    }
    detector.acceptWaveform(audio.frame(0));
    _acceptedFrames += 1;
  }

  @override
  Future<void> retireBetweenFrames() => close();

  @override
  Future<void> close() => _closeFuture ??= _close();

  Future<void> _close() async {
    _closeRequested = true;
    _alive = false;
    final Future<SherpaInitializationObservation>? initialization =
        _initialization;
    if (initialization != null) {
      try {
        await initialization;
      } on Object {
        // Initialization performs the same authoritative cleanup.
      }
    }
    await _cleanup();
  }

  Future<Never> _cleanupAfterFailure(
    Object initializationError,
    StackTrace initializationStack,
  ) async {
    try {
      await _cleanup();
    } on Object {
      rethrow;
    }
    Error.throwWithStackTrace(initializationError, initializationStack);
  }

  Future<void> _cleanup() => _cleanupFuture ??= _performCleanup();

  Future<void> _performCleanup() async {
    _alive = false;
    Object? firstError;
    StackTrace? firstStack;
    final QualificationSherpaDetector? detector = _detector;
    _detector = null;
    if (detector != null) {
      try {
        detector.free();
        _factory._sherpaFreed();
      } on Object catch (error, stackTrace) {
        firstError = error;
        firstStack = stackTrace;
      }
    }
    final _TrackedQualificationTemporaryRoot? root = _root;
    _root = null;
    if (root != null) {
      try {
        await root.remove();
      } on Object catch (error, stackTrace) {
        firstError ??= error;
        firstStack ??= stackTrace;
      }
    }
    if (firstError != null) {
      Error.throwWithStackTrace(firstError, firstStack!);
    }
  }

  QualificationSherpaDetector _requireDetector() {
    final QualificationSherpaDetector? detector = _detector;
    if (!isAlive || detector == null) {
      throw StateError('The Sherpa qualification driver is not initialized.');
    }
    return detector;
  }

  void _throwIfClosing() {
    if (_closeRequested) {
      throw StateError('Sherpa initialization was superseded by close.');
    }
  }
}

bool _sameShape(List<int> left, List<int> right) {
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) return false;
  }
  return true;
}
