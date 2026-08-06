import 'dart:convert';

import 'package:crypto/crypto.dart';

import 'harness_contract.dart';

enum FonixRunPurpose { reference, cancellation, stale, recovery }

enum FonixRunOutcome { completed, cancelled }

enum FonixCancellationDisposition {
  nativeTerminationRequested,
  queuedRunRemoved,
  notCancelled,
}

enum SherpaRunPurpose { reference, recovery }

final class FonixRunResult {
  FonixRunResult.completed(Iterable<int> outputBytes)
    : outcome = FonixRunOutcome.completed,
      outputBytes = List<int>.unmodifiable(outputBytes);

  const FonixRunResult.cancelled()
    : outcome = FonixRunOutcome.cancelled,
      outputBytes = null;

  final FonixRunOutcome outcome;
  final List<int>? outputBytes;
}

abstract interface class FonixQualificationRun {
  Future<FonixRunResult> get settled;
  Future<FonixCancellationDisposition> cancelWithDisposition();
}

abstract interface class FonixQualificationDriver {
  /// False until [initialize] begins the first native operation.
  ///
  /// Factory construction must not load a library, create a native owner, or
  /// create a temporary profile root.
  bool get isAlive;
  int get outstandingRuns;

  Future<FonixInitializationObservation> initialize();
  Future<bool> probeAlive();
  FonixQualificationRun startRun(FonixRunPurpose purpose);
  Future<void> close();
}

abstract interface class SherpaQualificationDriver {
  /// False until [initialize] begins the first native operation.
  ///
  /// Factory construction must not load a library or create a native detector.
  bool get isAlive;
  int get acceptedFrames;
  int get flushCalls;
  int get publishedSegments;
  int get queuedSegments;

  Future<SherpaInitializationObservation> initialize();
  Future<bool> probeAlive();
  Future<SherpaObservation> runReference(SherpaRunPurpose purpose);
  Future<void> acceptCancellationFrame();
  Future<void> retireBetweenFrames();
  Future<void> close();
}

abstract interface class QualificationDriverFactory {
  int get fonixSessionsCreated;
  int get fonixSessionsClosed;
  int get sherpaDetectorsCreated;
  int get sherpaDetectorsFreed;
  int get temporaryRootsCreated;
  int get temporaryRootsRemoved;
  int get temporaryRootsRemaining;

  /// Returns a native-inert driver whose observable counters are all zero.
  FonixQualificationDriver createFonix();

  /// Returns a native-inert driver whose observable counters are all zero.
  SherpaQualificationDriver createSherpa();
}

enum FonixPublicationOutcome {
  currentOutput,
  cancelled,
  cancellationOutputSuppressed,
  staleSuppressed,
}

final class FonixPublication {
  FonixPublication.currentOutput(Iterable<int> outputBytes)
    : outcome = FonixPublicationOutcome.currentOutput,
      settlementOutcome = FonixRunOutcome.completed,
      settledOutputBytes = List<int>.unmodifiable(outputBytes),
      publishedOutputBytes = List<int>.unmodifiable(outputBytes);

  const FonixPublication.cancelled()
    : outcome = FonixPublicationOutcome.cancelled,
      settlementOutcome = FonixRunOutcome.cancelled,
      settledOutputBytes = null,
      publishedOutputBytes = null;

  FonixPublication.cancellationOutputSuppressed(Iterable<int> outputBytes)
    : outcome = FonixPublicationOutcome.cancellationOutputSuppressed,
      settlementOutcome = FonixRunOutcome.completed,
      settledOutputBytes = List<int>.unmodifiable(outputBytes),
      publishedOutputBytes = null;

  FonixPublication.staleSuppressed(Iterable<int> outputBytes)
    : outcome = FonixPublicationOutcome.staleSuppressed,
      settlementOutcome = FonixRunOutcome.completed,
      settledOutputBytes = List<int>.unmodifiable(outputBytes),
      publishedOutputBytes = null;

  final FonixPublicationOutcome outcome;
  final FonixRunOutcome settlementOutcome;
  final List<int>? settledOutputBytes;
  final List<int>? publishedOutputBytes;
}

abstract interface class LifecyclePublicationSink {
  LifecyclePublicationSnapshot get snapshot;
  int get authoritativeGeneration;

  /// Binds the run's sole settlement path before cancellation or retirement.
  ///
  /// The returned future is the only completion future consumed by the state
  /// machine. Implementations decide whether a settlement is current,
  /// cancelled, cancellation output that must be suppressed, or stale.
  Future<FonixPublication> bind({
    required FonixQualificationRun run,
    required int generation,
    required FonixRunPurpose purpose,
  });

  /// Retires the current generation after its run has already been bound.
  int advanceGeneration();
}

final class LifecyclePublicationSnapshot {
  const LifecyclePublicationSnapshot({
    required this.boundRuns,
    required this.settledRuns,
    required this.activeBindings,
    required this.currentPublishedOutputs,
    required this.cancellationSettlements,
    required this.cancellationCancelledResults,
    required this.cancellationPublishedOutputs,
    required this.staleObserved,
    required this.staleSuppressed,
    required this.stalePublishedOutputs,
  });

  const LifecyclePublicationSnapshot.empty()
    : boundRuns = 0,
      settledRuns = 0,
      activeBindings = 0,
      currentPublishedOutputs = 0,
      cancellationSettlements = 0,
      cancellationCancelledResults = 0,
      cancellationPublishedOutputs = 0,
      staleObserved = 0,
      staleSuppressed = 0,
      stalePublishedOutputs = 0;

  final int boundRuns;
  final int settledRuns;
  final int activeBindings;
  final int currentPublishedOutputs;
  final int cancellationSettlements;
  final int cancellationCancelledResults;
  final int cancellationPublishedOutputs;
  final int staleObserved;
  final int staleSuppressed;
  final int stalePublishedOutputs;

  bool get isEmpty =>
      boundRuns == 0 &&
      settledRuns == 0 &&
      activeBindings == 0 &&
      currentPublishedOutputs == 0 &&
      cancellationSettlements == 0 &&
      cancellationCancelledResults == 0 &&
      cancellationPublishedOutputs == 0 &&
      staleObserved == 0 &&
      staleSuppressed == 0 &&
      stalePublishedOutputs == 0;
}

final class AuthoritativeLifecyclePublicationSink
    implements LifecyclePublicationSink {
  AuthoritativeLifecyclePublicationSink({this.maximumBoundRuns = 128}) {
    if (maximumBoundRuns < 1 || maximumBoundRuns > 1024) {
      throw RangeError.range(maximumBoundRuns, 1, 1024, 'maximumBoundRuns');
    }
  }

  final int maximumBoundRuns;
  var _authoritativeGeneration = 1;
  var _boundRuns = 0;
  var _settledRuns = 0;
  var _activeBindings = 0;
  var _currentPublishedOutputs = 0;
  var _cancellationSettlements = 0;
  var _cancellationCancelledResults = 0;
  final int _cancellationPublishedOutputs = 0;
  var _staleObserved = 0;
  var _staleSuppressed = 0;
  var _stalePublishedOutputs = 0;

  @override
  int get authoritativeGeneration => _authoritativeGeneration;

  @override
  LifecyclePublicationSnapshot get snapshot => LifecyclePublicationSnapshot(
    boundRuns: _boundRuns,
    settledRuns: _settledRuns,
    activeBindings: _activeBindings,
    currentPublishedOutputs: _currentPublishedOutputs,
    cancellationSettlements: _cancellationSettlements,
    cancellationCancelledResults: _cancellationCancelledResults,
    cancellationPublishedOutputs: _cancellationPublishedOutputs,
    staleObserved: _staleObserved,
    staleSuppressed: _staleSuppressed,
    stalePublishedOutputs: _stalePublishedOutputs,
  );

  @override
  Future<FonixPublication> bind({
    required FonixQualificationRun run,
    required int generation,
    required FonixRunPurpose purpose,
  }) {
    if (generation != _authoritativeGeneration) {
      throw const QualificationFailure('publication-generation-not-current');
    }
    if (_boundRuns >= maximumBoundRuns || _activeBindings != 0) {
      throw const QualificationFailure('publication-sink-capacity-exceeded');
    }
    _boundRuns += 1;
    _activeBindings += 1;
    final Future<FonixRunResult> soleSettlement;
    try {
      soleSettlement = run.settled;
    } on Object {
      _activeBindings -= 1;
      rethrow;
    }
    return soleSettlement
        .then((FonixRunResult result) {
          _settledRuns += 1;
          final List<int>? output = result.outputBytes;
          if (purpose == FonixRunPurpose.cancellation) {
            _cancellationSettlements += 1;
            if (result.outcome == FonixRunOutcome.cancelled && output == null) {
              _cancellationCancelledResults += 1;
              return const FonixPublication.cancelled();
            }
            if (output != null) {
              return FonixPublication.cancellationOutputSuppressed(output);
            }
            return const FonixPublication.cancelled();
          }

          if (purpose == FonixRunPurpose.stale) {
            _staleObserved += 1;
          }
          if (result.outcome != FonixRunOutcome.completed || output == null) {
            return const FonixPublication.cancelled();
          }
          if (generation != _authoritativeGeneration) {
            if (purpose == FonixRunPurpose.stale) {
              _staleSuppressed += 1;
            }
            return FonixPublication.staleSuppressed(output);
          }
          if (purpose == FonixRunPurpose.stale) {
            _stalePublishedOutputs += 1;
          }
          _currentPublishedOutputs += 1;
          return FonixPublication.currentOutput(output);
        })
        .whenComplete(() {
          _activeBindings -= 1;
        });
  }

  @override
  int advanceGeneration() {
    if (_authoritativeGeneration >= 0x7fffffff) {
      throw const QualificationFailure('publication-generation-exhausted');
    }
    _authoritativeGeneration += 1;
    return _authoritativeGeneration;
  }
}

final class _CapturedFonixPublication {
  const _CapturedFonixPublication.completed(this._value)
    : _error = null,
      _stackTrace = null;

  const _CapturedFonixPublication.failed(this._error, this._stackTrace)
    : _value = null;

  final FonixPublication? _value;
  final Object? _error;
  final StackTrace? _stackTrace;

  static Future<_CapturedFonixPublication> capture(
    Future<FonixPublication> source,
  ) => source.then<_CapturedFonixPublication>(
    _CapturedFonixPublication.completed,
    onError: (Object error, StackTrace stackTrace) =>
        _CapturedFonixPublication.failed(error, stackTrace),
  );

  FonixPublication valueOrThrow() {
    final Object? error = _error;
    if (error != null) {
      Error.throwWithStackTrace(error, _stackTrace!);
    }
    return _value!;
  }
}

final class QualificationPins {
  QualificationPins({
    required this.sherpaProfileId,
    required this.fonixModelSha256,
    required this.fonixInputSha256,
    required this.fonixReferenceOutputSha256,
    required this.fonixCancellationModelSha256,
    required this.fonixCancellationInputSha256,
    required this.sherpaModelSha256,
    required this.sherpaAudioSha256,
    required this.sherpaReferenceSha256,
  }) {
    if (!_token.hasMatch(sherpaProfileId)) {
      throw const QualificationFailure('invalid-qualification-pins');
    }
    for (final String digest in <String>[
      fonixModelSha256,
      fonixInputSha256,
      fonixReferenceOutputSha256,
      fonixCancellationModelSha256,
      fonixCancellationInputSha256,
      sherpaModelSha256,
      sherpaAudioSha256,
      sherpaReferenceSha256,
    ]) {
      if (!isLowercaseSha256(digest)) {
        throw const QualificationFailure('invalid-qualification-digest');
      }
    }
    if (fonixCancellationModelSha256 != fonixModelSha256) {
      throw const QualificationFailure('fonix-session-model-identity-drift');
    }
  }

  final String sherpaProfileId;
  final String fonixModelSha256;
  final String fonixInputSha256;
  final String fonixReferenceOutputSha256;
  final String fonixCancellationModelSha256;
  final String fonixCancellationInputSha256;
  final String sherpaModelSha256;
  final String sherpaAudioSha256;
  final String sherpaReferenceSha256;

  Map<String, Object?> profileJson() => <String, Object?>{
    'id': sherpaProfileId,
    'provider': 'cpu',
    'sampleRateHz': 16000,
    'windowSamples': 512,
    'numThreads': 1,
    'thresholdMillionths': 500000,
    'minimumSpeechMilliseconds': 250,
    'minimumSilenceMilliseconds': 800,
    'maximumSpeechMilliseconds': 30000,
    'bufferMilliseconds': 60000,
  };

  Map<String, Object?> fixturesJson() => <String, Object?>{
    'fonixModelSha256': fonixModelSha256,
    'fonixInputSha256': fonixInputSha256,
    'fonixReferenceOutputSha256': fonixReferenceOutputSha256,
    'fonixCancellationModelSha256': fonixCancellationModelSha256,
    'fonixCancellationInputSha256': fonixCancellationInputSha256,
    'sherpaModelSha256': sherpaModelSha256,
    'sherpaAudioSha256': sherpaAudioSha256,
    'sherpaReferenceSha256': sherpaReferenceSha256,
  };

  static final RegExp _token = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$');
}

final class FonixInitializationObservation {
  FonixInitializationObservation({
    required this.runtimeOwner,
    required this.runtimeSource,
    required this.ortVersion,
    required this.requiredOrtApi,
    required this.negotiatedOrtApi,
    required this.shimAbi,
    required this.shimBuildId,
    required this.modelSha256,
    required this.referenceInputSha256,
    required this.referenceOutputSha256,
    required this.cancellationModelSha256,
    required this.cancellationInputSha256,
  }) {
    if (runtimeOwner != 'sherpa' ||
        runtimeSource != 'process' ||
        requiredOrtApi != 27 ||
        negotiatedOrtApi != 27 ||
        shimAbi != 1 ||
        shimBuildId != 'android-owner-sherpa-source-process' ||
        !_semanticVersion.hasMatch(ortVersion)) {
      throw const QualificationFailure('invalid-runtime-diagnostics');
    }
    final List<int> parts = ortVersion
        .split('.')
        .map(int.parse)
        .toList(growable: false);
    if (parts[0] != 1 || parts[1] < 27) {
      throw const QualificationFailure('incompatible-ort-version');
    }
    for (final String digest in <String>[
      modelSha256,
      referenceInputSha256,
      referenceOutputSha256,
      cancellationModelSha256,
      cancellationInputSha256,
    ]) {
      if (!isLowercaseSha256(digest)) {
        throw const QualificationFailure('invalid-fonix-fixture-observation');
      }
    }
  }

  final String runtimeOwner;
  final String runtimeSource;
  final String ortVersion;
  final int requiredOrtApi;
  final int negotiatedOrtApi;
  final int shimAbi;
  final String shimBuildId;
  final String modelSha256;
  final String referenceInputSha256;
  final String referenceOutputSha256;
  final String cancellationModelSha256;
  final String cancellationInputSha256;

  Map<String, Object?> toJson() => <String, Object?>{
    'runtimeOwner': runtimeOwner,
    'runtimeSource': runtimeSource,
    'ortVersion': ortVersion,
    'requiredOrtApi': requiredOrtApi,
    'negotiatedOrtApi': negotiatedOrtApi,
    'shimAbi': shimAbi,
    'shimBuildId': shimBuildId,
  };

  Map<String, Object?> fixturesJson() => <String, Object?>{
    'fonixModelSha256': modelSha256,
    'fonixInputSha256': referenceInputSha256,
    'fonixReferenceOutputSha256': referenceOutputSha256,
    'fonixCancellationModelSha256': cancellationModelSha256,
    'fonixCancellationInputSha256': cancellationInputSha256,
  };

  bool matchesPins(QualificationPins pins) =>
      modelSha256 == pins.fonixModelSha256 &&
      referenceInputSha256 == pins.fonixInputSha256 &&
      referenceOutputSha256 == pins.fonixReferenceOutputSha256 &&
      cancellationModelSha256 == pins.fonixCancellationModelSha256 &&
      cancellationInputSha256 == pins.fonixCancellationInputSha256;

  @override
  bool operator ==(Object other) =>
      other is FonixInitializationObservation &&
      runtimeOwner == other.runtimeOwner &&
      runtimeSource == other.runtimeSource &&
      ortVersion == other.ortVersion &&
      requiredOrtApi == other.requiredOrtApi &&
      negotiatedOrtApi == other.negotiatedOrtApi &&
      shimAbi == other.shimAbi &&
      shimBuildId == other.shimBuildId &&
      modelSha256 == other.modelSha256 &&
      referenceInputSha256 == other.referenceInputSha256 &&
      referenceOutputSha256 == other.referenceOutputSha256 &&
      cancellationModelSha256 == other.cancellationModelSha256 &&
      cancellationInputSha256 == other.cancellationInputSha256;

  @override
  int get hashCode => Object.hash(
    runtimeOwner,
    runtimeSource,
    ortVersion,
    requiredOrtApi,
    negotiatedOrtApi,
    shimAbi,
    shimBuildId,
    modelSha256,
    referenceInputSha256,
    referenceOutputSha256,
    cancellationModelSha256,
    cancellationInputSha256,
  );

  static final RegExp _semanticVersion = RegExp(
    r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$',
  );
}

final class SherpaProfileObservation {
  SherpaProfileObservation({
    required this.id,
    required this.provider,
    required this.sampleRateHz,
    required this.windowSamples,
    required this.numThreads,
    required this.thresholdMillionths,
    required this.minimumSpeechMilliseconds,
    required this.minimumSilenceMilliseconds,
    required this.maximumSpeechMilliseconds,
    required this.bufferMilliseconds,
  }) {
    if (!QualificationPins._token.hasMatch(id) ||
        !QualificationPins._token.hasMatch(provider) ||
        sampleRateHz < 1 ||
        sampleRateHz > 384000 ||
        windowSamples < 1 ||
        windowSamples > 65536 ||
        numThreads < 1 ||
        numThreads > 64 ||
        thresholdMillionths < 0 ||
        thresholdMillionths > 1000000 ||
        minimumSpeechMilliseconds < 0 ||
        minimumSpeechMilliseconds > 3600000 ||
        minimumSilenceMilliseconds < 0 ||
        minimumSilenceMilliseconds > 3600000 ||
        maximumSpeechMilliseconds < 1 ||
        maximumSpeechMilliseconds > 3600000 ||
        bufferMilliseconds < 1 ||
        bufferMilliseconds > 3600000) {
      throw const QualificationFailure('invalid-sherpa-profile-observation');
    }
  }

  final String id;
  final String provider;
  final int sampleRateHz;
  final int windowSamples;
  final int numThreads;
  final int thresholdMillionths;
  final int minimumSpeechMilliseconds;
  final int minimumSilenceMilliseconds;
  final int maximumSpeechMilliseconds;
  final int bufferMilliseconds;

  Map<String, Object?> toJson() => <String, Object?>{
    'id': id,
    'provider': provider,
    'sampleRateHz': sampleRateHz,
    'windowSamples': windowSamples,
    'numThreads': numThreads,
    'thresholdMillionths': thresholdMillionths,
    'minimumSpeechMilliseconds': minimumSpeechMilliseconds,
    'minimumSilenceMilliseconds': minimumSilenceMilliseconds,
    'maximumSpeechMilliseconds': maximumSpeechMilliseconds,
    'bufferMilliseconds': bufferMilliseconds,
  };

  bool matchesPins(QualificationPins pins) {
    final Map<String, Object?> expected = pins.profileJson();
    final Map<String, Object?> observed = toJson();
    return expected.length == observed.length &&
        expected.entries.every(
          (MapEntry<String, Object?> entry) =>
              observed[entry.key] == entry.value,
        );
  }

  @override
  bool operator ==(Object other) =>
      other is SherpaProfileObservation &&
      id == other.id &&
      provider == other.provider &&
      sampleRateHz == other.sampleRateHz &&
      windowSamples == other.windowSamples &&
      numThreads == other.numThreads &&
      thresholdMillionths == other.thresholdMillionths &&
      minimumSpeechMilliseconds == other.minimumSpeechMilliseconds &&
      minimumSilenceMilliseconds == other.minimumSilenceMilliseconds &&
      maximumSpeechMilliseconds == other.maximumSpeechMilliseconds &&
      bufferMilliseconds == other.bufferMilliseconds;

  @override
  int get hashCode => Object.hash(
    id,
    provider,
    sampleRateHz,
    windowSamples,
    numThreads,
    thresholdMillionths,
    minimumSpeechMilliseconds,
    minimumSilenceMilliseconds,
    maximumSpeechMilliseconds,
    bufferMilliseconds,
  );
}

final class SherpaInitializationObservation {
  SherpaInitializationObservation({
    required this.getVersion,
    required this.getGitSha1,
    required this.profile,
    required this.modelSha256,
    required this.audioSha256,
    required this.referenceSha256,
  }) {
    if (!FonixInitializationObservation._semanticVersion.hasMatch(getVersion) ||
        !_nativeRevision.hasMatch(getGitSha1)) {
      throw const QualificationFailure('invalid-sherpa-diagnostics');
    }
    for (final String digest in <String>[
      modelSha256,
      audioSha256,
      referenceSha256,
    ]) {
      if (!isLowercaseSha256(digest)) {
        throw const QualificationFailure('invalid-sherpa-fixture-observation');
      }
    }
  }

  final String getVersion;
  final String getGitSha1;
  final SherpaProfileObservation profile;
  final String modelSha256;
  final String audioSha256;
  final String referenceSha256;

  Map<String, Object?> fixturesJson() => <String, Object?>{
    'sherpaModelSha256': modelSha256,
    'sherpaAudioSha256': audioSha256,
    'sherpaReferenceSha256': referenceSha256,
  };

  bool matchesPins(QualificationPins pins) =>
      modelSha256 == pins.sherpaModelSha256 &&
      audioSha256 == pins.sherpaAudioSha256 &&
      referenceSha256 == pins.sherpaReferenceSha256 &&
      profile.matchesPins(pins);

  @override
  bool operator ==(Object other) =>
      other is SherpaInitializationObservation &&
      getVersion == other.getVersion &&
      getGitSha1 == other.getGitSha1 &&
      profile == other.profile &&
      modelSha256 == other.modelSha256 &&
      audioSha256 == other.audioSha256 &&
      referenceSha256 == other.referenceSha256;

  @override
  int get hashCode => Object.hash(
    getVersion,
    getGitSha1,
    profile,
    modelSha256,
    audioSha256,
    referenceSha256,
  );

  static final RegExp _nativeRevision = RegExp(r'^[0-9a-f]{7,40}$');
}

final class QualificationIdentity {
  QualificationIdentity._({
    required this.pins,
    required this.fonix,
    required this.sherpa,
  }) {
    if (!fonix.matchesPins(pins) || !sherpa.matchesPins(pins)) {
      throw const QualificationFailure('fixture-observation-mismatch');
    }
  }

  final QualificationPins pins;
  final FonixInitializationObservation fonix;
  final SherpaInitializationObservation sherpa;

  Map<String, Object?> runtimeJson() => fonix.toJson();

  Map<String, Object?> sherpaJson() => <String, Object?>{
    'getVersion': sherpa.getVersion,
    'getGitSha1': sherpa.getGitSha1,
    'profile': sherpa.profile.toJson(),
  };

  Map<String, Object?> fixturesJson() => <String, Object?>{
    ...fonix.fixturesJson(),
    ...sherpa.fixturesJson(),
  };
}

final class VadSegment {
  const VadSegment({required this.startSample, required this.sampleCount});

  final int startSample;
  final int sampleCount;

  Map<String, Object?> toJson() => <String, Object?>{
    'startSample': startSample,
    'sampleCount': sampleCount,
  };

  @override
  bool operator ==(Object other) =>
      other is VadSegment &&
      startSample == other.startSample &&
      sampleCount == other.sampleCount;

  @override
  int get hashCode => Object.hash(startSample, sampleCount);
}

final class SherpaObservation {
  SherpaObservation({
    required this.sourceSamples,
    required this.submittedSamples,
    required Iterable<VadSegment> segments,
    required this.queueEmptyAfterDrain,
    required this.detectedAfterDrain,
  }) : segments = List<VadSegment>.unmodifiable(segments);

  final int sourceSamples;
  final int submittedSamples;
  final List<VadSegment> segments;
  final bool queueEmptyAfterDrain;
  final bool detectedAfterDrain;

  Map<String, Object?> toJson() => <String, Object?>{
    'sourceSamples': sourceSamples,
    'submittedSamples': submittedSamples,
    'segments': <Object?>[
      for (final VadSegment segment in segments) segment.toJson(),
    ],
    'queueEmptyAfterDrain': queueEmptyAfterDrain,
    'detectedAfterDrain': detectedAfterDrain,
  };

  @override
  bool operator ==(Object other) =>
      other is SherpaObservation &&
      sourceSamples == other.sourceSamples &&
      submittedSamples == other.submittedSamples &&
      _listEquals(segments, other.segments) &&
      queueEmptyAfterDrain == other.queueEmptyAfterDrain &&
      detectedAfterDrain == other.detectedAfterDrain;

  @override
  int get hashCode => Object.hash(
    sourceSamples,
    submittedSamples,
    Object.hashAll(segments),
    queueEmptyAfterDrain,
    detectedAfterDrain,
  );
}

final class SherpaReferenceBounds {
  SherpaReferenceBounds({
    required this.sourceSamples,
    required this.minimumSegments,
    required this.maximumSegments,
    required this.minimumTotalSegmentSamples,
    required this.maximumTotalSegmentSamples,
    required this.maximumSegmentSamples,
  }) {
    if (sourceSamples <= 0 ||
        minimumSegments <= 0 ||
        minimumSegments > maximumSegments ||
        maximumSegments > 32 ||
        minimumTotalSegmentSamples <= 0 ||
        minimumTotalSegmentSamples > maximumTotalSegmentSamples ||
        maximumTotalSegmentSamples > sourceSamples ||
        maximumSegmentSamples <= 0 ||
        maximumSegmentSamples > maximumTotalSegmentSamples) {
      throw const QualificationFailure('invalid-sherpa-reference-bounds');
    }
  }

  final int sourceSamples;
  final int minimumSegments;
  final int maximumSegments;
  final int minimumTotalSegmentSamples;
  final int maximumTotalSegmentSamples;
  final int maximumSegmentSamples;

  void validate(SherpaObservation observation) {
    _validateSherpaObservation(observation);
    if (observation.sourceSamples != sourceSamples ||
        observation.segments.length < minimumSegments ||
        observation.segments.length > maximumSegments) {
      throw const QualificationFailure('sherpa-reference-mismatch');
    }
    var totalSamples = 0;
    for (final VadSegment segment in observation.segments) {
      if (segment.sampleCount > maximumSegmentSamples) {
        throw const QualificationFailure('sherpa-reference-mismatch');
      }
      totalSamples += segment.sampleCount;
    }
    if (totalSamples < minimumTotalSegmentSamples ||
        totalSamples > maximumTotalSegmentSamples) {
      throw const QualificationFailure('sherpa-reference-mismatch');
    }
  }
}

final class QualificationReferences {
  QualificationReferences({
    required Iterable<int> fonixOutputBytes,
    required this.sherpaBounds,
  }) : fonixOutputBytes = List<int>.unmodifiable(fonixOutputBytes) {
    if (this.fonixOutputBytes.isEmpty) {
      throw const QualificationFailure('empty-fonix-reference');
    }
  }

  final List<int> fonixOutputBytes;
  final SherpaReferenceBounds sherpaBounds;
}

final class DeviceQualificationResult implements HarnessCompletionPayload {
  DeviceQualificationResult._({
    required this.loadOrder,
    required this.launchChallengeSha256,
    required this.identity,
    required this.initializationEvents,
    required List<_QualificationStep> steps,
    required this.requestedCycles,
    required this.framesAcceptedBeforeCancellation,
    required this.framesAcceptedAfterCancellation,
    required this.flushCallsAfterCancellation,
    required this.segmentsPublishedAfterCancellation,
    required this.retiredGeneration,
    required this.authoritativeGeneration,
    required this.recoveryFonixOutputBytes,
    required this.recoverySherpa,
    required this.fonixSessionsCreated,
    required this.fonixSessionsClosed,
    required this.sherpaDetectorsCreated,
    required this.sherpaDetectorsFreed,
    required this.temporaryRootsCreated,
    required this.temporaryRootsRemoved,
    required this.pendingFonixRuns,
    required this.queuedSherpaSegments,
    required this.publicationSnapshot,
    required this.nativeCancellationAcceptedCount,
  }) : _steps = steps;

  @override
  HarnessCompletionStatus get status => HarnessCompletionStatus.passed;

  @override
  final HarnessLoadOrder loadOrder;

  @override
  final String launchChallengeSha256;

  final QualificationIdentity identity;
  final List<String> initializationEvents;
  final List<_QualificationStep> _steps;
  final int requestedCycles;
  final int framesAcceptedBeforeCancellation;
  final int framesAcceptedAfterCancellation;
  final int flushCallsAfterCancellation;
  final int segmentsPublishedAfterCancellation;
  final int retiredGeneration;
  final int authoritativeGeneration;
  final List<int> recoveryFonixOutputBytes;
  final SherpaObservation recoverySherpa;
  final int fonixSessionsCreated;
  final int fonixSessionsClosed;
  final int sherpaDetectorsCreated;
  final int sherpaDetectorsFreed;
  final int temporaryRootsCreated;
  final int temporaryRootsRemoved;
  final int pendingFonixRuns;
  final int queuedSherpaSegments;
  final LifecyclePublicationSnapshot publicationSnapshot;
  final int nativeCancellationAcceptedCount;

  @override
  Map<String, Object?> toJson() => <String, Object?>{
    'schemaVersion': harnessWireSchemaVersion,
    'result': 'passed',
    'launchChallengeSha256': launchChallengeSha256,
    'loadOrder': loadOrder.wireValue,
    'runtime': identity.runtimeJson(),
    'sherpa': identity.sherpaJson(),
    'fixtures': identity.fixturesJson(),
    'initialization': <String, Object?>{
      'events': initializationEvents,
      'firstOwnerAliveWhenSecondReady': true,
    },
    'workload': <String, Object?>{
      'requestedCycles': requestedCycles,
      'completedCycles': requestedCycles,
      'steps': <Object?>[
        for (final _QualificationStep step in _steps) step.toJson(),
      ],
    },
    'lifecycle': <String, Object?>{
      'fonixCancellation': <String, Object?>{
        'mode': 'active-native-termination',
        'requestCount': 1,
        'nativeRequestAcceptedCount': nativeCancellationAcceptedCount,
        'settlementCount': publicationSnapshot.cancellationSettlements,
        'cancelledResultCount':
            publicationSnapshot.cancellationCancelledResults,
        'publishedOutputCount':
            publicationSnapshot.cancellationPublishedOutputs,
        'outstandingRunsAfterSettlement': 0,
        'settledBeforeRecovery': true,
      },
      'sherpaCancellation': <String, Object?>{
        'mode': 'between-bounded-frames',
        'requestCount': 1,
        'framesAcceptedBeforeRequest': framesAcceptedBeforeCancellation,
        'framesAcceptedAfterRequest': framesAcceptedAfterCancellation,
        'flushCallsAfterRequest': flushCallsAfterCancellation,
        'segmentsPublishedAfterRequest': segmentsPublishedAfterCancellation,
        'detectorRetiredBeforeRecovery': true,
      },
      'staleCompletion': <String, Object?>{
        'inducedCount': 1,
        'observedCount': publicationSnapshot.staleObserved,
        'suppressedCount': publicationSnapshot.staleSuppressed,
        'publishedOutputCount': publicationSnapshot.stalePublishedOutputs,
        'retiredGeneration': retiredGeneration,
        'authoritativeGeneration': authoritativeGeneration,
      },
      'recovery': <String, Object?>{
        'fonixOutputEncoding': 'float32-le',
        'fonixOutputBytesBase64': base64Encode(recoveryFonixOutputBytes),
        'fonixOutputSha256': sha256
            .convert(recoveryFonixOutputBytes)
            .toString(),
        'sherpa': recoverySherpa.toJson(),
      },
      'disposal': <String, Object?>{
        'ordersTested': <String>['fonix-then-sherpa', 'sherpa-then-fonix'],
        'fonixSessionsCreated': fonixSessionsCreated,
        'fonixSessionsClosed': fonixSessionsClosed,
        'sherpaDetectorsCreated': sherpaDetectorsCreated,
        'sherpaDetectorsFreed': sherpaDetectorsFreed,
        'fonixDoubleClose': 'passed',
        'sherpaDoubleFree': 'passed',
        'pendingFonixRuns': pendingFonixRuns,
        'queuedSherpaSegments': queuedSherpaSegments,
        'temporaryRootsCreated': temporaryRootsCreated,
        'temporaryRootsRemoved': temporaryRootsRemoved,
        'temporaryRootsRemaining': 0,
      },
    },
  };
}

final class QualificationStateMachine {
  const QualificationStateMachine({
    required this.driverFactory,
    required this.publicationSink,
    required this.pins,
    required this.references,
    this.requestedCycles = 2,
  });

  final QualificationDriverFactory driverFactory;
  final LifecyclePublicationSink publicationSink;
  final QualificationPins pins;
  final QualificationReferences references;
  final int requestedCycles;

  Future<DeviceQualificationResult> run(HarnessLaunch launch) async {
    if (requestedCycles < 2 || requestedCycles > 64) {
      throw const QualificationFailure('invalid-cycle-count');
    }
    if (sha256.convert(references.fonixOutputBytes).toString() !=
        pins.fonixReferenceOutputSha256) {
      throw const QualificationFailure('fonix-reference-identity-mismatch');
    }
    if (!publicationSink.snapshot.isEmpty ||
        publicationSink.authoritativeGeneration != 1) {
      throw const QualificationFailure('publication-gate-not-fresh');
    }

    final List<FonixQualificationDriver> fonixDrivers =
        <FonixQualificationDriver>[];
    final List<SherpaQualificationDriver> sherpaDrivers =
        <SherpaQualificationDriver>[];
    var completed = false;
    try {
      final List<String> initializationEvents = <String>[];
      final _InitializedPrimary primary = await _initializePrimary(
        launch.loadOrder,
        fonixDrivers,
        sherpaDrivers,
        initializationEvents,
      );
      final FonixQualificationDriver primaryFonix = primary.fonix;
      final SherpaQualificationDriver primarySherpa = primary.sherpa;
      final QualificationIdentity identity = primary.identity;

      final List<_QualificationStep> steps = <_QualificationStep>[];
      var ordinal = 1;
      for (var cycle = 0; cycle < requestedCycles; cycle += 1) {
        final FonixPublication fonixPublication = await _publishFonixRun(
          primaryFonix.startRun(FonixRunPurpose.reference),
          FonixRunPurpose.reference,
        );
        final List<int> fonixBytes = _publishedFonixBytes(fonixPublication);
        _expectFonixReference(fonixBytes);
        steps.add(_FonixStep(ordinal: ordinal, outputBytes: fonixBytes));
        ordinal += 1;

        final SherpaObservation sherpaObservation = await primarySherpa
            .runReference(SherpaRunPurpose.reference);
        _expectSherpaReference(sherpaObservation);
        steps.add(
          _SherpaStep(ordinal: ordinal, observation: sherpaObservation),
        );
        ordinal += 1;
      }

      final FonixQualificationRun cancellationRun = primaryFonix.startRun(
        FonixRunPurpose.cancellation,
      );
      final Future<FonixPublication> cancellationPublicationFuture =
          publicationSink.bind(
            run: cancellationRun,
            generation: publicationSink.authoritativeGeneration,
            purpose: FonixRunPurpose.cancellation,
          );
      final Future<_CapturedFonixPublication> cancellationPublicationDrain =
          _CapturedFonixPublication.capture(cancellationPublicationFuture);
      final FonixCancellationDisposition cancellationDisposition;
      try {
        cancellationDisposition = await cancellationRun.cancelWithDisposition();
      } on Object catch (dispatchError, dispatchStack) {
        try {
          publicationSink.advanceGeneration();
        } on Object {
          // The cancellation dispatch failure remains authoritative. The sink
          // also suppresses cancellation-purpose output independently, so a
          // broken retirement implementation cannot make output publishable.
        }
        await cancellationPublicationDrain;
        Error.throwWithStackTrace(dispatchError, dispatchStack);
      }
      final FonixPublication cancellationResult =
          (await cancellationPublicationDrain).valueOrThrow();
      final LifecyclePublicationSnapshot cancellationPublication =
          publicationSink.snapshot;
      final int nativeCancellationAcceptedCount =
          cancellationDisposition ==
              FonixCancellationDisposition.nativeTerminationRequested
          ? 1
          : 0;
      if (nativeCancellationAcceptedCount != 1 ||
          cancellationResult.outcome != FonixPublicationOutcome.cancelled ||
          cancellationResult.settlementOutcome != FonixRunOutcome.cancelled ||
          cancellationResult.settledOutputBytes != null ||
          cancellationResult.publishedOutputBytes != null ||
          cancellationPublication.cancellationSettlements != 1 ||
          cancellationPublication.cancellationCancelledResults != 1 ||
          cancellationPublication.cancellationPublishedOutputs != 0 ||
          primaryFonix.outstandingRuns != 0) {
        throw const QualificationFailure('fonix-cancellation-failed');
      }

      final _SherpaCounters beforeCancellationProbe = _SherpaCounters.capture(
        primarySherpa,
      );
      await primarySherpa.acceptCancellationFrame();
      final _SherpaCounters atCancellationRequest = _SherpaCounters.capture(
        primarySherpa,
      );
      final int framesAcceptedBeforeCancellation =
          atCancellationRequest.acceptedFrames -
          beforeCancellationProbe.acceptedFrames;
      final int flushCallsBeforeCancellation =
          atCancellationRequest.flushCalls - beforeCancellationProbe.flushCalls;
      final int segmentsPublishedBeforeCancellation =
          atCancellationRequest.publishedSegments -
          beforeCancellationProbe.publishedSegments;
      final int totalFixtureFrames =
          (references.sherpaBounds.sourceSamples + 511) ~/ 512;
      if (framesAcceptedBeforeCancellation <= 0 ||
          framesAcceptedBeforeCancellation >= totalFixtureFrames ||
          flushCallsBeforeCancellation != 0 ||
          segmentsPublishedBeforeCancellation != 0) {
        throw const QualificationFailure('sherpa-cancellation-frame-missing');
      }
      await primarySherpa.retireBetweenFrames();
      final _SherpaCounters afterCancellation = _SherpaCounters.capture(
        primarySherpa,
      );
      final int framesAcceptedAfterCancellation =
          afterCancellation.acceptedFrames -
          atCancellationRequest.acceptedFrames;
      final int flushCallsAfterCancellation =
          afterCancellation.flushCalls - atCancellationRequest.flushCalls;
      final int segmentsPublishedAfterCancellation =
          afterCancellation.publishedSegments -
          atCancellationRequest.publishedSegments;
      if (primarySherpa.isAlive ||
          framesAcceptedAfterCancellation != 0 ||
          flushCallsAfterCancellation != 0 ||
          segmentsPublishedAfterCancellation != 0) {
        throw const QualificationFailure('sherpa-detector-not-retired');
      }

      final int retiredGeneration = publicationSink.authoritativeGeneration;
      final FonixQualificationRun staleRun = primaryFonix.startRun(
        FonixRunPurpose.stale,
      );
      final Future<FonixPublication> stalePublicationFuture = publicationSink
          .bind(
            run: staleRun,
            generation: retiredGeneration,
            purpose: FonixRunPurpose.stale,
          );
      final int authoritativeGeneration = publicationSink.advanceGeneration();
      final FonixPublication staleResult = await stalePublicationFuture;
      final List<int>? staleBytes = staleResult.settledOutputBytes;
      if (staleResult.outcome != FonixPublicationOutcome.staleSuppressed ||
          staleResult.settlementOutcome != FonixRunOutcome.completed ||
          staleBytes == null ||
          staleResult.publishedOutputBytes != null) {
        throw const QualificationFailure('stale-publication-not-suppressed');
      }
      _expectFonixReference(staleBytes);
      final LifecyclePublicationSnapshot lifecyclePublication =
          publicationSink.snapshot;
      if (retiredGeneration == authoritativeGeneration ||
          lifecyclePublication.staleObserved != 1 ||
          lifecyclePublication.staleSuppressed != 1 ||
          lifecyclePublication.stalePublishedOutputs != 0) {
        throw const QualificationFailure('stale-publication-not-suppressed');
      }

      final SherpaQualificationDriver recoverySherpa = _createFreshSherpa(
        sherpaDrivers,
      );
      final SherpaInitializationObservation recoverySherpaIdentity =
          await recoverySherpa.initialize();
      if (!recoverySherpa.isAlive) {
        throw const QualificationFailure('sherpa-recovery-not-ready');
      }
      if (recoverySherpaIdentity != identity.sherpa) {
        throw const QualificationFailure('sherpa-recovery-identity-drift');
      }
      final FonixPublication recoveryFonixResult = await _publishFonixRun(
        primaryFonix.startRun(FonixRunPurpose.recovery),
        FonixRunPurpose.recovery,
      );
      final List<int> recoveryFonixBytes = _publishedFonixBytes(
        recoveryFonixResult,
      );
      _expectFonixReference(recoveryFonixBytes);
      final SherpaObservation recoverySherpaObservation = await recoverySherpa
          .runReference(SherpaRunPurpose.recovery);
      _expectSherpaReference(recoverySherpaObservation);

      final FonixQualificationDriver reverseFonix = _createFreshFonix(
        fonixDrivers,
      );
      final FonixInitializationObservation reverseFonixIdentity =
          await reverseFonix.initialize();
      if (!reverseFonix.isAlive) {
        throw const QualificationFailure('reverse-disposal-pair-not-ready');
      }
      final SherpaQualificationDriver reverseSherpa = _createFreshSherpa(
        sherpaDrivers,
      );
      final SherpaInitializationObservation reverseSherpaIdentity =
          await reverseSherpa.initialize();
      if (!reverseFonix.isAlive || !reverseSherpa.isAlive) {
        throw const QualificationFailure('reverse-disposal-pair-not-ready');
      }
      if (reverseFonixIdentity != identity.fonix ||
          reverseSherpaIdentity != identity.sherpa) {
        throw const QualificationFailure('reverse-pair-identity-drift');
      }

      await primaryFonix.close();
      await primaryFonix.close();
      await recoverySherpa.close();
      await recoverySherpa.close();
      await primarySherpa.close();
      await primarySherpa.close();

      await reverseSherpa.close();
      await reverseSherpa.close();
      await reverseFonix.close();
      await reverseFonix.close();

      final int pendingFonixRuns = fonixDrivers.fold(
        0,
        (int sum, FonixQualificationDriver driver) =>
            sum + driver.outstandingRuns,
      );
      final int queuedSherpaSegments = sherpaDrivers.fold(
        0,
        (int sum, SherpaQualificationDriver driver) =>
            sum + driver.queuedSegments,
      );
      final LifecyclePublicationSnapshot finalPublication =
          publicationSink.snapshot;
      if (finalPublication.boundRuns != requestedCycles + 3 ||
          finalPublication.settledRuns != requestedCycles + 3 ||
          finalPublication.activeBindings != 0 ||
          finalPublication.currentPublishedOutputs != requestedCycles + 1 ||
          fonixDrivers.any(
            (FonixQualificationDriver driver) =>
                driver.isAlive || driver.outstandingRuns != 0,
          ) ||
          pendingFonixRuns != 0 ||
          queuedSherpaSegments != 0 ||
          sherpaDrivers.any(
            (SherpaQualificationDriver driver) =>
                driver.isAlive || driver.queuedSegments != 0,
          ) ||
          driverFactory.fonixSessionsCreated != fonixDrivers.length ||
          driverFactory.fonixSessionsClosed !=
              driverFactory.fonixSessionsCreated ||
          driverFactory.sherpaDetectorsCreated != sherpaDrivers.length ||
          driverFactory.sherpaDetectorsFreed !=
              driverFactory.sherpaDetectorsCreated ||
          driverFactory.temporaryRootsCreated <= 0 ||
          driverFactory.temporaryRootsRemoved !=
              driverFactory.temporaryRootsCreated ||
          driverFactory.temporaryRootsRemaining != 0) {
        throw const QualificationFailure('disposal-settlement-failed');
      }

      final DeviceQualificationResult result = DeviceQualificationResult._(
        loadOrder: launch.loadOrder,
        launchChallengeSha256: launch.launchChallengeSha256,
        identity: identity,
        initializationEvents: List<String>.unmodifiable(initializationEvents),
        steps: List<_QualificationStep>.unmodifiable(steps),
        requestedCycles: requestedCycles,
        framesAcceptedBeforeCancellation: framesAcceptedBeforeCancellation,
        framesAcceptedAfterCancellation: framesAcceptedAfterCancellation,
        flushCallsAfterCancellation: flushCallsAfterCancellation,
        segmentsPublishedAfterCancellation: segmentsPublishedAfterCancellation,
        retiredGeneration: retiredGeneration,
        authoritativeGeneration: authoritativeGeneration,
        recoveryFonixOutputBytes: List<int>.unmodifiable(recoveryFonixBytes),
        recoverySherpa: recoverySherpaObservation,
        fonixSessionsCreated: driverFactory.fonixSessionsCreated,
        fonixSessionsClosed: driverFactory.fonixSessionsClosed,
        sherpaDetectorsCreated: driverFactory.sherpaDetectorsCreated,
        sherpaDetectorsFreed: driverFactory.sherpaDetectorsFreed,
        temporaryRootsCreated: driverFactory.temporaryRootsCreated,
        temporaryRootsRemoved: driverFactory.temporaryRootsRemoved,
        pendingFonixRuns: pendingFonixRuns,
        queuedSherpaSegments: queuedSherpaSegments,
        publicationSnapshot: finalPublication,
        nativeCancellationAcceptedCount: nativeCancellationAcceptedCount,
      );
      completed = true;
      return result;
    } finally {
      if (!completed) {
        for (final FonixQualificationDriver driver in fonixDrivers) {
          await _closeFonixQuietly(driver);
        }
        for (final SherpaQualificationDriver driver in sherpaDrivers) {
          await _closeSherpaQuietly(driver);
        }
      }
    }
  }

  Future<_InitializedPrimary> _initializePrimary(
    HarnessLoadOrder order,
    List<FonixQualificationDriver> fonixDrivers,
    List<SherpaQualificationDriver> sherpaDrivers,
    List<String> events,
  ) async {
    late final FonixQualificationDriver fonix;
    late final SherpaQualificationDriver sherpa;
    late final FonixInitializationObservation fonixIdentity;
    late final SherpaInitializationObservation sherpaIdentity;
    switch (order) {
      case HarnessLoadOrder.dartFirst:
        fonix = _createFreshFonix(fonixDrivers);
        fonixIdentity = await fonix.initialize();
        if (!fonix.isAlive) {
          throw const QualificationFailure('fonix-not-ready');
        }
        events.add('fonix-session-ready');
        sherpa = _createFreshSherpa(sherpaDrivers);
        sherpaIdentity = await sherpa.initialize();
        if (!await fonix.probeAlive() || !sherpa.isAlive) {
          throw const QualificationFailure('first-owner-not-alive');
        }
        events.add('sherpa-vad-ready');
      case HarnessLoadOrder.sherpaFirst:
        sherpa = _createFreshSherpa(sherpaDrivers);
        sherpaIdentity = await sherpa.initialize();
        if (!sherpa.isAlive) {
          throw const QualificationFailure('sherpa-not-ready');
        }
        events.add('sherpa-vad-ready');
        fonix = _createFreshFonix(fonixDrivers);
        fonixIdentity = await fonix.initialize();
        if (!await sherpa.probeAlive() || !fonix.isAlive) {
          throw const QualificationFailure('first-owner-not-alive');
        }
        events.add('fonix-session-ready');
    }
    return _InitializedPrimary(
      fonix: fonix,
      sherpa: sherpa,
      identity: QualificationIdentity._(
        pins: pins,
        fonix: fonixIdentity,
        sherpa: sherpaIdentity,
      ),
    );
  }

  FonixQualificationDriver _createFreshFonix(
    List<FonixQualificationDriver> owners,
  ) {
    final _FactoryOwnershipSnapshot before = _FactoryOwnershipSnapshot.capture(
      driverFactory,
    );
    final FonixQualificationDriver driver = driverFactory.createFonix();
    owners.add(driver);
    if (driver.isAlive ||
        driver.outstandingRuns != 0 ||
        _FactoryOwnershipSnapshot.capture(driverFactory) != before) {
      throw const QualificationFailure('driver-constructor-not-inert');
    }
    return driver;
  }

  SherpaQualificationDriver _createFreshSherpa(
    List<SherpaQualificationDriver> owners,
  ) {
    final _FactoryOwnershipSnapshot before = _FactoryOwnershipSnapshot.capture(
      driverFactory,
    );
    final SherpaQualificationDriver driver = driverFactory.createSherpa();
    owners.add(driver);
    if (driver.isAlive ||
        driver.acceptedFrames != 0 ||
        driver.flushCalls != 0 ||
        driver.publishedSegments != 0 ||
        driver.queuedSegments != 0 ||
        _FactoryOwnershipSnapshot.capture(driverFactory) != before) {
      throw const QualificationFailure('driver-constructor-not-inert');
    }
    return driver;
  }

  Future<FonixPublication> _publishFonixRun(
    FonixQualificationRun run,
    FonixRunPurpose purpose,
  ) => publicationSink.bind(
    run: run,
    generation: publicationSink.authoritativeGeneration,
    purpose: purpose,
  );

  List<int> _publishedFonixBytes(FonixPublication result) {
    final List<int>? bytes = result.publishedOutputBytes;
    if (result.outcome != FonixPublicationOutcome.currentOutput ||
        result.settlementOutcome != FonixRunOutcome.completed ||
        bytes == null ||
        bytes.isEmpty) {
      throw const QualificationFailure('fonix-output-not-published');
    }
    return bytes;
  }

  void _expectFonixReference(List<int> value) {
    if (!_listEquals(value, references.fonixOutputBytes)) {
      throw const QualificationFailure('fonix-reference-mismatch');
    }
  }

  void _expectSherpaReference(SherpaObservation value) {
    references.sherpaBounds.validate(value);
  }
}

final class _InitializedPrimary {
  const _InitializedPrimary({
    required this.fonix,
    required this.sherpa,
    required this.identity,
  });

  final FonixQualificationDriver fonix;
  final SherpaQualificationDriver sherpa;
  final QualificationIdentity identity;
}

abstract interface class _QualificationStep {
  Map<String, Object?> toJson();
}

final class _FonixStep implements _QualificationStep {
  _FonixStep({required this.ordinal, required Iterable<int> outputBytes})
    : outputBytes = List<int>.unmodifiable(outputBytes);

  final int ordinal;
  final List<int> outputBytes;

  @override
  Map<String, Object?> toJson() => <String, Object?>{
    'ordinal': ordinal,
    'engine': 'fonix',
    'outputEncoding': 'float32-le',
    'outputBytesBase64': base64Encode(outputBytes),
    'outputSha256': sha256.convert(outputBytes).toString(),
  };
}

final class _SherpaStep implements _QualificationStep {
  const _SherpaStep({required this.ordinal, required this.observation});

  final int ordinal;
  final SherpaObservation observation;

  @override
  Map<String, Object?> toJson() => <String, Object?>{
    'ordinal': ordinal,
    'engine': 'sherpa',
    ...observation.toJson(),
  };
}

final class _SherpaCounters {
  const _SherpaCounters({
    required this.acceptedFrames,
    required this.flushCalls,
    required this.publishedSegments,
  });

  factory _SherpaCounters.capture(SherpaQualificationDriver driver) {
    final _SherpaCounters value = _SherpaCounters(
      acceptedFrames: driver.acceptedFrames,
      flushCalls: driver.flushCalls,
      publishedSegments: driver.publishedSegments,
    );
    if (value.acceptedFrames < 0 ||
        value.flushCalls < 0 ||
        value.publishedSegments < 0) {
      throw const QualificationFailure('invalid-sherpa-counters');
    }
    return value;
  }

  final int acceptedFrames;
  final int flushCalls;
  final int publishedSegments;
}

final class _FactoryOwnershipSnapshot {
  const _FactoryOwnershipSnapshot({
    required this.fonixSessionsCreated,
    required this.fonixSessionsClosed,
    required this.sherpaDetectorsCreated,
    required this.sherpaDetectorsFreed,
    required this.temporaryRootsCreated,
    required this.temporaryRootsRemoved,
    required this.temporaryRootsRemaining,
  });

  factory _FactoryOwnershipSnapshot.capture(
    QualificationDriverFactory factory,
  ) {
    final _FactoryOwnershipSnapshot snapshot = _FactoryOwnershipSnapshot(
      fonixSessionsCreated: factory.fonixSessionsCreated,
      fonixSessionsClosed: factory.fonixSessionsClosed,
      sherpaDetectorsCreated: factory.sherpaDetectorsCreated,
      sherpaDetectorsFreed: factory.sherpaDetectorsFreed,
      temporaryRootsCreated: factory.temporaryRootsCreated,
      temporaryRootsRemoved: factory.temporaryRootsRemoved,
      temporaryRootsRemaining: factory.temporaryRootsRemaining,
    );
    if (snapshot._values.any((int value) => value < 0)) {
      throw const QualificationFailure('invalid-factory-counters');
    }
    return snapshot;
  }

  final int fonixSessionsCreated;
  final int fonixSessionsClosed;
  final int sherpaDetectorsCreated;
  final int sherpaDetectorsFreed;
  final int temporaryRootsCreated;
  final int temporaryRootsRemoved;
  final int temporaryRootsRemaining;

  List<int> get _values => <int>[
    fonixSessionsCreated,
    fonixSessionsClosed,
    sherpaDetectorsCreated,
    sherpaDetectorsFreed,
    temporaryRootsCreated,
    temporaryRootsRemoved,
    temporaryRootsRemaining,
  ];

  @override
  bool operator ==(Object other) =>
      other is _FactoryOwnershipSnapshot &&
      fonixSessionsCreated == other.fonixSessionsCreated &&
      fonixSessionsClosed == other.fonixSessionsClosed &&
      sherpaDetectorsCreated == other.sherpaDetectorsCreated &&
      sherpaDetectorsFreed == other.sherpaDetectorsFreed &&
      temporaryRootsCreated == other.temporaryRootsCreated &&
      temporaryRootsRemoved == other.temporaryRootsRemoved &&
      temporaryRootsRemaining == other.temporaryRootsRemaining;

  @override
  int get hashCode => Object.hash(
    fonixSessionsCreated,
    fonixSessionsClosed,
    sherpaDetectorsCreated,
    sherpaDetectorsFreed,
    temporaryRootsCreated,
    temporaryRootsRemoved,
    temporaryRootsRemaining,
  );
}

void _validateSherpaObservation(SherpaObservation value) {
  if (value.sourceSamples <= 0 ||
      value.submittedSamples != ((value.sourceSamples + 511) ~/ 512) * 512 ||
      value.segments.isEmpty ||
      !value.queueEmptyAfterDrain ||
      value.detectedAfterDrain) {
    throw const QualificationFailure('invalid-sherpa-observation');
  }
  var previousEnd = 0;
  for (final VadSegment segment in value.segments) {
    final int end = segment.startSample + segment.sampleCount;
    if (segment.startSample < previousEnd ||
        segment.sampleCount <= 0 ||
        end > value.sourceSamples) {
      throw const QualificationFailure('invalid-sherpa-segment');
    }
    previousEnd = end;
  }
}

bool _listEquals<T>(List<T> left, List<T> right) {
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

Future<void> _closeFonixQuietly(FonixQualificationDriver driver) async {
  try {
    await driver.close();
  } on Object {
    // Preserve the original qualification failure.
  }
}

Future<void> _closeSherpaQuietly(SherpaQualificationDriver driver) async {
  try {
    await driver.close();
  } on Object {
    // Preserve the original qualification failure.
  }
}

final class QualificationFailure implements Exception {
  const QualificationFailure(this.code);

  final String code;

  @override
  String toString() => 'QualificationFailure($code)';
}
