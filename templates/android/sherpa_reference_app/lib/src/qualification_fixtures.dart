import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import 'qualification_state_machine.dart';

const String androidSherpaQualificationProfileId = 'silero-vad-load-order-v1';
const String androidSherpaPackageVersion = '1.13.4';
const String androidSherpaNativeRevision =
    '142807252687d81b40d6315f23470a1512a00de3';
const String androidSherpaAudioGeneratorId = 'android-sherpa-synthetic-vad-v1';
const String androidSherpaModelFilename = 'silero_vad.int8.onnx';
const String androidSherpaAudioFilename = 'sherpa_synthetic_speech.wav';
const String androidSherpaReferenceClaimBoundary =
    'The observed segment was generated on macOS arm64 with the pinned '
    'sherpa package and is only an invariant source. Android target '
    'execution remains required.';
const int androidSherpaSampleRate = 16000;
const int androidSherpaWindowSamples = 512;
const int androidSherpaMaximumAudioSamples = 57600000;
const int androidSherpaQualificationAudioSamples = 128000;

const int _matrixHeaderBytes = 24;
const int _matrixSchema = 1;
const int _matrixMaximumDimension = 512;
const int _maximumModelBytes = 64 * 1024 * 1024;
const int _maximumReferenceBytes = 64 * 1024;
const List<int> _matrixMagic = <int>[
  0x46,
  0x4f,
  0x4e,
  0x49,
  0x58,
  0x4d,
  0x31,
  0x00,
];

/// One strictly decoded `FONIXM1` matrix-multiplication input fixture.
///
/// The source encoding is:
///
/// - the eight bytes `FONIXM1\0`;
/// - four little-endian uint32 values: schema, rows, inner, columns;
/// - exactly `rows * inner` little-endian float32 values; and
/// - exactly `inner * columns` little-endian float32 values.
///
/// All collections returned by this class are copies. The private values are
/// therefore the immutable values decoded from the hashed fixture bytes.
final class QualificationMatrixInput {
  QualificationMatrixInput._({
    required this.rows,
    required this.inner,
    required this.columns,
    required Float32List left,
    required Float32List right,
  }) : _left = left,
       _right = right;

  factory QualificationMatrixInput.decode(Uint8List source) {
    if (source.length < _matrixHeaderBytes) {
      throw const FormatException('Fonix matrix fixture is truncated.');
    }
    for (var index = 0; index < _matrixMagic.length; index += 1) {
      if (source[index] != _matrixMagic[index]) {
        throw const FormatException('Fonix matrix fixture magic is invalid.');
      }
    }
    final ByteData data = ByteData.sublistView(source);
    final int schema = data.getUint32(8, Endian.little);
    if (schema != _matrixSchema) {
      throw FormatException('Unsupported Fonix matrix schema $schema.');
    }
    final int rows = data.getUint32(12, Endian.little);
    final int inner = data.getUint32(16, Endian.little);
    final int columns = data.getUint32(20, Endian.little);
    for (final MapEntry<String, int> dimension in <String, int>{
      'rows': rows,
      'inner': inner,
      'columns': columns,
    }.entries) {
      if (dimension.value < 1 || dimension.value > _matrixMaximumDimension) {
        throw RangeError.range(
          dimension.value,
          1,
          _matrixMaximumDimension,
          dimension.key,
        );
      }
    }
    final int leftCount = rows * inner;
    final int rightCount = inner * columns;
    final int valueCount = leftCount + rightCount;
    final int expectedBytes = _matrixHeaderBytes + valueCount * 4;
    if (source.length != expectedBytes) {
      throw const FormatException(
        'Fonix matrix fixture has trailing or missing bytes.',
      );
    }

    final Float32List left = Float32List(leftCount);
    final Float32List right = Float32List(rightCount);
    var offset = _matrixHeaderBytes;
    for (var index = 0; index < left.length; index += 1) {
      final double value = data.getFloat32(offset, Endian.little);
      if (!value.isFinite) {
        throw const FormatException(
          'Fonix matrix fixture contains a non-finite value.',
        );
      }
      left[index] = value;
      offset += 4;
    }
    for (var index = 0; index < right.length; index += 1) {
      final double value = data.getFloat32(offset, Endian.little);
      if (!value.isFinite) {
        throw const FormatException(
          'Fonix matrix fixture contains a non-finite value.',
        );
      }
      right[index] = value;
      offset += 4;
    }
    return QualificationMatrixInput._(
      rows: rows,
      inner: inner,
      columns: columns,
      left: left,
      right: right,
    );
  }

  final int rows;
  final int inner;
  final int columns;
  final Float32List _left;
  final Float32List _right;

  Float32List get left => Float32List.fromList(_left);
  Float32List get right => Float32List.fromList(_right);

  List<int> get outputShape => <int>[rows, columns];

  bool get hasIdentityRightMatrix {
    if (inner != columns) return false;
    for (var row = 0; row < inner; row += 1) {
      for (var column = 0; column < columns; column += 1) {
        final double expected = row == column ? 1 : 0;
        if (_right[row * columns + column] != expected) return false;
      }
    }
    return true;
  }
}

/// Strictly decoded mono, 16 kHz, little-endian PCM16 WAVE audio.
final class QualificationPcmAudio {
  QualificationPcmAudio._(Float32List samples) : _samples = samples;

  factory QualificationPcmAudio.decode(Uint8List source) {
    if (source.length < 44 ||
        !_matchesAscii(source, 0, 'RIFF') ||
        !_matchesAscii(source, 8, 'WAVE')) {
      throw const FormatException('Sherpa audio must be a RIFF/WAVE file.');
    }
    final ByteData data = ByteData.sublistView(source);
    if (data.getUint32(4, Endian.little) != source.length - 8) {
      throw const FormatException('Sherpa WAV RIFF size is inconsistent.');
    }

    Uint8List? formatChunk;
    Uint8List? sampleChunk;
    var offset = 12;
    while (offset < source.length) {
      if (source.length - offset < 8) {
        throw const FormatException('Sherpa WAV has a truncated chunk header.');
      }
      final String chunkId = String.fromCharCodes(
        source.sublist(offset, offset + 4),
      );
      final int chunkBytes = data.getUint32(offset + 4, Endian.little);
      offset += 8;
      if (chunkBytes > source.length - offset) {
        throw const FormatException('Sherpa WAV has a truncated chunk.');
      }
      final Uint8List chunk = Uint8List.fromList(
        source.sublist(offset, offset + chunkBytes),
      );
      offset += chunkBytes;
      if (chunkBytes.isOdd) {
        if (offset >= source.length || source[offset] != 0) {
          throw const FormatException('Sherpa WAV chunk padding is invalid.');
        }
        offset += 1;
      }
      switch (chunkId) {
        case 'fmt ':
          if (formatChunk != null) {
            throw const FormatException('Sherpa WAV has duplicate fmt chunks.');
          }
          formatChunk = chunk;
        case 'data':
          if (sampleChunk != null) {
            throw const FormatException(
              'Sherpa WAV has duplicate data chunks.',
            );
          }
          sampleChunk = chunk;
      }
    }

    if (formatChunk == null ||
        sampleChunk == null ||
        formatChunk.length != 16) {
      throw const FormatException(
        'Sherpa WAV must have one canonical PCM fmt and data chunk.',
      );
    }
    final ByteData format = ByteData.sublistView(formatChunk);
    if (format.getUint16(0, Endian.little) != 1 ||
        format.getUint16(2, Endian.little) != 1 ||
        format.getUint32(4, Endian.little) != androidSherpaSampleRate ||
        format.getUint32(8, Endian.little) != androidSherpaSampleRate * 2 ||
        format.getUint16(12, Endian.little) != 2 ||
        format.getUint16(14, Endian.little) != 16 ||
        sampleChunk.isEmpty ||
        sampleChunk.length.isOdd) {
      throw const FormatException(
        'Sherpa WAV must be mono PCM16 at the closed sample rate.',
      );
    }
    final int sampleCount = sampleChunk.length ~/ 2;
    if (sampleCount > androidSherpaMaximumAudioSamples) {
      throw const FormatException(
        'Sherpa WAV exceeds the one-hour sample bound.',
      );
    }
    final ByteData pcm = ByteData.sublistView(sampleChunk);
    final Float32List samples = Float32List(sampleCount);
    for (var index = 0; index < sampleCount; index += 1) {
      samples[index] =
          pcm.getInt16(index * 2, Endian.little).toDouble() / 32768.0;
    }
    return QualificationPcmAudio._(samples);
  }

  final Float32List _samples;

  int get sampleCount => _samples.length;
  int get paddedSampleCount =>
      ((_samples.length + androidSherpaWindowSamples - 1) ~/
          androidSherpaWindowSamples) *
      androidSherpaWindowSamples;
  int get frameCount => paddedSampleCount ~/ androidSherpaWindowSamples;

  Float32List frame(int index) {
    if (index < 0 || index >= frameCount) {
      throw RangeError.index(index, this, 'index', null, frameCount);
    }
    final Float32List result = Float32List(androidSherpaWindowSamples);
    final int start = index * androidSherpaWindowSamples;
    final int available = _samples.length - start;
    final int count = available < androidSherpaWindowSamples
        ? available
        : androidSherpaWindowSamples;
    result.setRange(0, count, _samples, start);
    return result;
  }
}

/// Immutable qualification fixtures and identities derived from their bytes.
///
/// No native library is opened, native object is created, or temporary root is
/// created by this factory. It copies and strictly validates the supplied
/// bytes once. Drivers created from the bundle consume those private copies,
/// so the returned pins are observations of the same bytes used for native
/// model creation and input construction rather than caller-declared hashes.
final class AndroidQualificationFixtures {
  factory AndroidQualificationFixtures.fromBytes({
    required Uint8List fonixModel,
    required Uint8List fonixReferenceInput,
    required Uint8List fonixReferenceOutput,
    required Uint8List fonixCancellationInput,
    required Uint8List sherpaModel,
    required Uint8List sherpaAudio,
    required Uint8List sherpaReference,
  }) {
    _boundedNonEmpty(fonixModel, _maximumModelBytes, 'Fonix model');
    _boundedNonEmpty(sherpaModel, _maximumModelBytes, 'Sherpa model');
    _boundedNonEmpty(
      sherpaReference,
      _maximumReferenceBytes,
      'Sherpa reference',
    );

    final Uint8List copiedFonixModel = Uint8List.fromList(fonixModel);
    final Uint8List copiedReferenceInput = Uint8List.fromList(
      fonixReferenceInput,
    );
    final Uint8List copiedReferenceOutput = Uint8List.fromList(
      fonixReferenceOutput,
    );
    final Uint8List copiedCancellationInput = Uint8List.fromList(
      fonixCancellationInput,
    );
    final Uint8List copiedSherpaModel = Uint8List.fromList(sherpaModel);
    final Uint8List copiedSherpaAudio = Uint8List.fromList(sherpaAudio);
    final Uint8List copiedSherpaReference = Uint8List.fromList(sherpaReference);

    final QualificationMatrixInput referenceMatrix =
        QualificationMatrixInput.decode(copiedReferenceInput);
    final QualificationMatrixInput cancellationMatrix =
        QualificationMatrixInput.decode(copiedCancellationInput);
    _validateReferenceMatrix(referenceMatrix);
    _validateCancellationMatrix(cancellationMatrix);
    _validateReferenceOutput(copiedReferenceOutput, referenceMatrix);

    final QualificationPcmAudio audio = QualificationPcmAudio.decode(
      copiedSherpaAudio,
    );
    if (audio.frameCount < 2) {
      throw const FormatException(
        'Sherpa audio must contain at least two bounded VAD frames.',
      );
    }
    final String fonixModelSha = sha256.convert(copiedFonixModel).toString();
    final String sherpaModelSha = sha256.convert(copiedSherpaModel).toString();
    final String sherpaAudioSha = sha256.convert(copiedSherpaAudio).toString();
    final _SherpaReferenceDecoded decodedReference = _decodeSherpaReference(
      copiedSherpaReference,
      sourceSamples: audio.sampleCount,
      modelBytes: copiedSherpaModel.length,
      modelSha256: sherpaModelSha,
      audioBytes: copiedSherpaAudio.length,
      audioSha256: sherpaAudioSha,
    );

    final QualificationPins pins = QualificationPins(
      sherpaProfileId: decodedReference.profileId,
      fonixModelSha256: fonixModelSha,
      fonixInputSha256: sha256.convert(copiedReferenceInput).toString(),
      fonixReferenceOutputSha256: sha256
          .convert(copiedReferenceOutput)
          .toString(),
      // The closed dynamic-shape MatMul-chain model is intentionally shared
      // by reference and cancellation runs because one driver owns one
      // long-lived OrtIsolateSession.
      fonixCancellationModelSha256: fonixModelSha,
      fonixCancellationInputSha256: sha256
          .convert(copiedCancellationInput)
          .toString(),
      sherpaModelSha256: sherpaModelSha,
      sherpaAudioSha256: sherpaAudioSha,
      sherpaReferenceSha256: sha256.convert(copiedSherpaReference).toString(),
    );
    return AndroidQualificationFixtures._(
      fonixModel: copiedFonixModel,
      referenceMatrix: referenceMatrix,
      cancellationMatrix: cancellationMatrix,
      sherpaModel: copiedSherpaModel,
      audio: audio,
      pins: pins,
      references: QualificationReferences(
        fonixOutputBytes: copiedReferenceOutput,
        sherpaBounds: decodedReference.bounds,
      ),
    );
  }

  AndroidQualificationFixtures._({
    required Uint8List fonixModel,
    required this.referenceMatrix,
    required this.cancellationMatrix,
    required Uint8List sherpaModel,
    required this.audio,
    required this.pins,
    required this.references,
  }) : _fonixModel = fonixModel,
       _sherpaModel = sherpaModel;

  final Uint8List _fonixModel;
  final Uint8List _sherpaModel;
  final QualificationMatrixInput referenceMatrix;
  final QualificationMatrixInput cancellationMatrix;
  final QualificationPcmAudio audio;
  final QualificationPins pins;
  final QualificationReferences references;

  Uint8List consumeFonixModel() {
    final Uint8List value = Uint8List.fromList(_fonixModel);
    _requireDigest(value, pins.fonixModelSha256, 'Fonix model');
    return value;
  }

  Uint8List consumeSherpaModel() {
    final Uint8List value = Uint8List.fromList(_sherpaModel);
    _requireDigest(value, pins.sherpaModelSha256, 'Sherpa model');
    return value;
  }

  QualificationMatrixInput matrixFor(FonixRunPurpose purpose) =>
      switch (purpose) {
        FonixRunPurpose.reference ||
        FonixRunPurpose.stale ||
        FonixRunPurpose.recovery => referenceMatrix,
        FonixRunPurpose.cancellation => cancellationMatrix,
      };

  void validateFonixReferenceOutput(Uint8List bytes) {
    if (bytes.length != references.fonixOutputBytes.length ||
        sha256.convert(bytes).toString() != pins.fonixReferenceOutputSha256) {
      throw const QualificationFailure('fonix-reference-mismatch');
    }
    for (var index = 0; index < bytes.length; index += 1) {
      if (bytes[index] != references.fonixOutputBytes[index]) {
        throw const QualificationFailure('fonix-reference-mismatch');
      }
    }
  }

  void validateStagedSherpaModel(Uint8List bytes) {
    _requireDigest(bytes, pins.sherpaModelSha256, 'staged Sherpa model');
  }
}

final class _SherpaReferenceDecoded {
  const _SherpaReferenceDecoded({
    required this.profileId,
    required this.bounds,
  });

  final String profileId;
  final SherpaReferenceBounds bounds;
}

_SherpaReferenceDecoded _decodeSherpaReference(
  Uint8List source, {
  required int sourceSamples,
  required int modelBytes,
  required String modelSha256,
  required int audioBytes,
  required String audioSha256,
}) {
  final String text;
  try {
    text = utf8.decode(source, allowMalformed: false);
  } on FormatException {
    throw const FormatException('Sherpa reference is not valid UTF-8.');
  }
  _StrictJsonScanner(
    text,
    label: 'Sherpa reference',
    maximumDepth: 8,
  ).validate();
  final Object? decoded = jsonDecode(text);
  final Map<String, Object?> root = _jsonObject(decoded, 'Sherpa reference');
  _exactKeys(root, const <String>{
    'schemaVersion',
    'sherpa',
    'profile',
    'model',
    'audio',
    'observedSegments',
    'invariant',
    'claimBoundary',
  }, 'Sherpa reference');
  if (_jsonInt(root, 'schemaVersion', 1, 1) != 1) {
    throw const FormatException('Sherpa reference schema must be 1.');
  }
  if (root['claimBoundary'] != androidSherpaReferenceClaimBoundary) {
    throw const FormatException(
      'Sherpa reference claim boundary does not match the closed contract.',
    );
  }

  final Map<String, Object?> sherpaIdentity = _jsonObject(
    root['sherpa'],
    'Sherpa reference native identity',
  );
  _exactKeys(sherpaIdentity, const <String>{
    'packageVersion',
    'nativeRevision',
  }, 'Sherpa reference native identity');
  if (sherpaIdentity['packageVersion'] != androidSherpaPackageVersion ||
      sherpaIdentity['nativeRevision'] != androidSherpaNativeRevision) {
    throw const FormatException(
      'Sherpa reference native identity does not match the selected build.',
    );
  }

  final Map<String, Object?> profile = _jsonObject(
    root['profile'],
    'Sherpa reference profile',
  );
  _exactKeys(profile, const <String>{
    'id',
    'provider',
    'sampleRateHz',
    'windowSamples',
    'numThreads',
    'thresholdMillionths',
    'minimumSpeechMilliseconds',
    'minimumSilenceMilliseconds',
    'maximumSpeechMilliseconds',
    'bufferMilliseconds',
  }, 'Sherpa reference profile');
  final Object? rawProfileId = profile['id'];
  if (rawProfileId != androidSherpaQualificationProfileId ||
      profile['provider'] != 'cpu' ||
      _jsonInt(profile, 'sampleRateHz', 1, 1000000) !=
          androidSherpaSampleRate ||
      _jsonInt(profile, 'windowSamples', 1, 1000000) !=
          androidSherpaWindowSamples ||
      _jsonInt(profile, 'numThreads', 1, 1024) != 1 ||
      _jsonInt(profile, 'thresholdMillionths', 0, 1000000) != 500000 ||
      _jsonInt(profile, 'minimumSpeechMilliseconds', 0, 3600000) != 250 ||
      _jsonInt(profile, 'minimumSilenceMilliseconds', 0, 3600000) != 800 ||
      _jsonInt(profile, 'maximumSpeechMilliseconds', 1, 3600000) != 30000 ||
      _jsonInt(profile, 'bufferMilliseconds', 1, 3600000) != 60000) {
    throw const FormatException(
      'Sherpa reference profile does not match the closed CPU profile.',
    );
  }

  final Map<String, Object?> model = _jsonObject(
    root['model'],
    'Sherpa reference model',
  );
  _exactKeys(model, const <String>{
    'file',
    'sizeBytes',
    'sha256',
  }, 'Sherpa reference model');
  if (model['file'] != androidSherpaModelFilename ||
      _jsonInt(model, 'sizeBytes', 1, _maximumModelBytes) != modelBytes ||
      model['sha256'] != modelSha256) {
    throw const FormatException(
      'Sherpa reference model identity does not match the supplied bytes.',
    );
  }

  final Map<String, Object?> audio = _jsonObject(
    root['audio'],
    'Sherpa reference audio',
  );
  _exactKeys(audio, const <String>{
    'file',
    'encoding',
    'generatorId',
    'sizeBytes',
    'sha256',
    'sampleCount',
  }, 'Sherpa reference audio');
  if (audio['file'] != androidSherpaAudioFilename ||
      audio['encoding'] != 'wav-pcm-s16le-mono' ||
      audio['generatorId'] != androidSherpaAudioGeneratorId ||
      _jsonInt(
            audio,
            'sizeBytes',
            1,
            androidSherpaMaximumAudioSamples * 2 + 44,
          ) !=
          audioBytes ||
      audio['sha256'] != audioSha256 ||
      _jsonInt(audio, 'sampleCount', 1, androidSherpaMaximumAudioSamples) !=
          sourceSamples ||
      sourceSamples != androidSherpaQualificationAudioSamples) {
    throw const FormatException(
      'Sherpa reference audio identity does not match the WAV.',
    );
  }

  final Object? rawObservedSegments = root['observedSegments'];
  if (rawObservedSegments is! List<Object?> ||
      rawObservedSegments.length != 1) {
    throw const FormatException(
      'Sherpa reference must contain one bounded host observation.',
    );
  }
  final Map<String, Object?> observed = _jsonObject(
    rawObservedSegments.single,
    'Sherpa reference observed segment',
  );
  _exactKeys(observed, const <String>{
    'startSample',
    'sampleCount',
  }, 'Sherpa reference observed segment');
  final int observedStart = _jsonInt(observed, 'startSample', 0, sourceSamples);
  final int observedCount = _jsonInt(observed, 'sampleCount', 1, sourceSamples);
  if (observedStart != 11872 ||
      observedCount != 96160 ||
      observedCount > sourceSamples - observedStart) {
    throw const FormatException(
      'Sherpa reference host observation does not match the closed fixture.',
    );
  }

  final Map<String, Object?> invariant = _jsonObject(
    root['invariant'],
    'Sherpa reference invariant',
  );
  _exactKeys(invariant, const <String>{
    'minimumSegments',
    'maximumSegments',
    'minimumTotalSegmentSamples',
    'maximumTotalSegmentSamples',
    'maximumSegmentSamples',
  }, 'Sherpa reference invariant');
  final int minimumSegments = _jsonInt(invariant, 'minimumSegments', 1, 32);
  final int maximumSegments = _jsonInt(invariant, 'maximumSegments', 1, 32);
  final int minimumTotal = _jsonInt(
    invariant,
    'minimumTotalSegmentSamples',
    1,
    sourceSamples,
  );
  final int maximumTotal = _jsonInt(
    invariant,
    'maximumTotalSegmentSamples',
    1,
    sourceSamples,
  );
  final int maximumSegment = _jsonInt(
    invariant,
    'maximumSegmentSamples',
    1,
    sourceSamples,
  );
  if (minimumSegments != 1 ||
      maximumSegments != 1 ||
      minimumTotal != 95232 ||
      maximumTotal != 97280 ||
      maximumSegment != 97280) {
    throw const FormatException(
      'Sherpa reference invariant is not the closed Android tolerance.',
    );
  }
  return _SherpaReferenceDecoded(
    profileId: rawProfileId! as String,
    bounds: SherpaReferenceBounds(
      sourceSamples: sourceSamples,
      minimumSegments: minimumSegments,
      maximumSegments: maximumSegments,
      minimumTotalSegmentSamples: minimumTotal,
      maximumTotalSegmentSamples: maximumTotal,
      maximumSegmentSamples: maximumSegment,
    ),
  );
}

void _validateReferenceMatrix(QualificationMatrixInput value) {
  if (value.rows != 2 ||
      value.inner != 2 ||
      value.columns != 2 ||
      !value.hasIdentityRightMatrix) {
    throw const FormatException(
      'Fonix reference matrix must be the closed 2x2 identity workload.',
    );
  }
  final Float32List left = value.left;
  const List<double> expected = <double>[1, 2, 3, 4];
  for (var index = 0; index < expected.length; index += 1) {
    if (left[index] != expected[index]) {
      throw const FormatException(
        'Fonix reference matrix values do not match the closed workload.',
      );
    }
  }
}

void _validateCancellationMatrix(QualificationMatrixInput value) {
  if (value.rows != 512 ||
      value.inner != 512 ||
      value.columns != 512 ||
      !value.hasIdentityRightMatrix) {
    throw const FormatException(
      'Fonix cancellation matrix must be the closed 512x512 workload.',
    );
  }
  final Float32List left = value.left;
  for (var row = 0; row < 512; row += 1) {
    for (var column = 0; column < 512; column += 1) {
      final double expected =
          (((row * 17 + column * 31) % 23) - 11).toDouble() / 16.0;
      if (left[row * 512 + column] != expected) {
        throw const FormatException(
          'Fonix cancellation matrix values do not match the closed workload.',
        );
      }
    }
  }
}

void _validateReferenceOutput(
  Uint8List bytes,
  QualificationMatrixInput reference,
) {
  if (bytes.length != 16) {
    throw const FormatException(
      'Fonix reference output must be exactly four float32-le values.',
    );
  }
  final ByteData data = ByteData.sublistView(bytes);
  final Float32List expected = reference.left;
  for (var index = 0; index < expected.length; index += 1) {
    final double value = data.getFloat32(index * 4, Endian.little);
    if (!value.isFinite || value != expected[index]) {
      throw const FormatException(
        'Fonix reference output does not match the closed identity result.',
      );
    }
  }
}

void _boundedNonEmpty(Uint8List value, int maximum, String label) {
  if (value.isEmpty || value.length > maximum) {
    throw RangeError('$label must contain 1..$maximum bytes.');
  }
}

void _requireDigest(Uint8List bytes, String expected, String label) {
  if (sha256.convert(bytes).toString() != expected) {
    throw StateError('$label changed after fixture preparation.');
  }
}

bool _matchesAscii(Uint8List value, int offset, String expected) {
  if (offset < 0 || value.length - offset < expected.length) return false;
  for (var index = 0; index < expected.length; index += 1) {
    if (value[offset + index] != expected.codeUnitAt(index)) return false;
  }
  return true;
}

Map<String, Object?> _jsonObject(Object? value, String label) {
  if (value is! Map<Object?, Object?>) {
    throw FormatException('$label must be an object.');
  }
  final Map<String, Object?> result = <String, Object?>{};
  for (final MapEntry<Object?, Object?> entry in value.entries) {
    if (entry.key is! String) {
      throw FormatException('$label contains a non-string key.');
    }
    result[entry.key! as String] = entry.value;
  }
  return result;
}

void _exactKeys(
  Map<String, Object?> value,
  Set<String> expected,
  String label,
) {
  if (value.length != expected.length || !value.keys.every(expected.contains)) {
    throw FormatException('$label has an unexpected field set.');
  }
}

int _jsonInt(
  Map<String, Object?> object,
  String key,
  int minimum,
  int maximum,
) {
  final Object? value = object[key];
  if (value is! int || value < minimum || value > maximum) {
    throw FormatException('$key must be an integer in $minimum..$maximum.');
  }
  return value;
}

final class _StrictJsonScanner {
  _StrictJsonScanner(
    this.source, {
    required this.label,
    required this.maximumDepth,
  });

  final String source;
  final String label;
  final int maximumDepth;
  var _index = 0;

  void validate() {
    _skipWhitespace();
    _value(0);
    _skipWhitespace();
    if (_index != source.length) _fail('contains trailing JSON content.');
  }

  void _value(int depth) {
    if (depth > maximumDepth || _index >= source.length) {
      _fail('contains invalid or excessively nested JSON.');
    }
    switch (source.codeUnitAt(_index)) {
      case 0x7b:
        _object(depth + 1);
      case 0x5b:
        _array(depth + 1);
      case 0x22:
        _string();
      case 0x74:
        _literal('true');
      case 0x66:
        _literal('false');
      case 0x6e:
        _literal('null');
      default:
        _number();
    }
  }

  void _object(int depth) {
    _index += 1;
    _skipWhitespace();
    if (_consume(0x7d)) return;
    final Set<String> keys = <String>{};
    while (true) {
      if (_index >= source.length || source.codeUnitAt(_index) != 0x22) {
        _fail('contains an object key that is not a JSON string.');
      }
      final String key = _string();
      if (!keys.add(key)) _fail('contains a duplicate object key.');
      _skipWhitespace();
      if (!_consume(0x3a)) _fail('contains an object key without a value.');
      _skipWhitespace();
      _value(depth);
      _skipWhitespace();
      if (_consume(0x7d)) return;
      if (!_consume(0x2c)) {
        _fail('contains an object that is not comma separated.');
      }
      _skipWhitespace();
    }
  }

  void _array(int depth) {
    _index += 1;
    _skipWhitespace();
    if (_consume(0x5d)) return;
    while (true) {
      _value(depth);
      _skipWhitespace();
      if (_consume(0x5d)) return;
      if (!_consume(0x2c)) {
        _fail('contains an array that is not comma separated.');
      }
      _skipWhitespace();
    }
  }

  String _string() {
    final int start = _index;
    _index += 1;
    var escaped = false;
    while (_index < source.length) {
      final int codeUnit = source.codeUnitAt(_index);
      _index += 1;
      if (escaped) {
        if (codeUnit == 0x75) {
          for (var count = 0; count < 4; count += 1) {
            if (_index >= source.length || !_isHex(source.codeUnitAt(_index))) {
              _fail('contains an invalid JSON escape.');
            }
            _index += 1;
          }
        } else if (!const <int>{
          0x22,
          0x5c,
          0x2f,
          0x62,
          0x66,
          0x6e,
          0x72,
          0x74,
        }.contains(codeUnit)) {
          _fail('contains an invalid JSON escape.');
        }
        escaped = false;
        continue;
      }
      if (codeUnit == 0x5c) {
        escaped = true;
      } else if (codeUnit == 0x22) {
        final Object? decoded = jsonDecode(source.substring(start, _index));
        if (decoded is! String) _fail('contains an invalid JSON string.');
        return decoded;
      } else if (codeUnit < 0x20) {
        _fail('contains a JSON control character.');
      }
    }
    _fail('contains an unterminated JSON string.');
  }

  void _literal(String literal) {
    if (_index + literal.length > source.length ||
        source.substring(_index, _index + literal.length) != literal) {
      _fail('contains an invalid JSON literal.');
    }
    _index += literal.length;
  }

  void _number() {
    final int start = _index;
    _consume(0x2d);
    if (_consume(0x30)) {
      if (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _fail('contains an invalid JSON number.');
      }
    } else {
      if (_index >= source.length || !_isDigit19(source.codeUnitAt(_index))) {
        _fail('contains an invalid JSON value.');
      }
      while (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _index += 1;
      }
    }
    if (_consume(0x2e)) {
      final int fractionStart = _index;
      while (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _index += 1;
      }
      if (_index == fractionStart) _fail('contains an invalid JSON number.');
    }
    if (_consume(0x65) || _consume(0x45)) {
      _consume(0x2b) || _consume(0x2d);
      final int exponentStart = _index;
      while (_index < source.length && _isDigit(source.codeUnitAt(_index))) {
        _index += 1;
      }
      if (_index == exponentStart) _fail('contains an invalid JSON number.');
    }
    if (_index == start) _fail('contains an invalid JSON value.');
  }

  void _skipWhitespace() {
    while (_index < source.length) {
      final int codeUnit = source.codeUnitAt(_index);
      if (codeUnit != 0x20 &&
          codeUnit != 0x09 &&
          codeUnit != 0x0a &&
          codeUnit != 0x0d) {
        return;
      }
      _index += 1;
    }
  }

  bool _consume(int codeUnit) {
    if (_index < source.length && source.codeUnitAt(_index) == codeUnit) {
      _index += 1;
      return true;
    }
    return false;
  }

  Never _fail(String message) => throw FormatException('$label $message');

  static bool _isDigit(int codeUnit) => codeUnit >= 0x30 && codeUnit <= 0x39;
  static bool _isDigit19(int codeUnit) => codeUnit >= 0x31 && codeUnit <= 0x39;
  static bool _isHex(int codeUnit) =>
      _isDigit(codeUnit) ||
      (codeUnit >= 0x41 && codeUnit <= 0x46) ||
      (codeUnit >= 0x61 && codeUnit <= 0x66);
}
