import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_sherpa_reference/src/qualification_fixtures.dart';

void main() {
  late _FixtureBytes source;

  setUpAll(() {
    source = _fixtureBytes();
  });

  test('derives every pin and reference from copied fixture bytes', () {
    final AndroidQualificationFixtures fixtures = _prepare(source);

    expect(fixtures.pins.fonixModelSha256, _sha(source.fonixModel));
    expect(fixtures.pins.fonixInputSha256, _sha(source.referenceInput));
    expect(
      fixtures.pins.fonixReferenceOutputSha256,
      _sha(source.referenceOutput),
    );
    expect(
      fixtures.pins.fonixCancellationModelSha256,
      fixtures.pins.fonixModelSha256,
    );
    expect(
      fixtures.pins.fonixCancellationInputSha256,
      _sha(source.cancellationInput),
    );
    expect(fixtures.pins.sherpaModelSha256, _sha(source.sherpaModel));
    expect(fixtures.pins.sherpaAudioSha256, _sha(source.sherpaAudio));
    expect(fixtures.pins.sherpaReferenceSha256, _sha(source.sherpaReference));
    expect(fixtures.references.fonixOutputBytes, source.referenceOutput);
    expect(fixtures.referenceMatrix.left, <double>[1, 2, 3, 4]);
    expect(fixtures.referenceMatrix.hasIdentityRightMatrix, isTrue);
    expect(fixtures.cancellationMatrix.rows, 512);
    expect(fixtures.cancellationMatrix.hasIdentityRightMatrix, isTrue);
    expect(fixtures.cancellationMatrix.left.first, -11 / 16);
    expect(fixtures.cancellationMatrix.left[511], ((511 * 31) % 23 - 11) / 16);
    expect(fixtures.audio.sampleCount, 128000);
    expect(fixtures.audio.frameCount, 250);
    expect(fixtures.audio.paddedSampleCount, 128000);
    final QualificationPcmAudio padded = QualificationPcmAudio.decode(
      _wav(sampleCount: 800),
    );
    expect(padded.frameCount, 2);
    expect(padded.paddedSampleCount, 1024);
    expect(padded.frame(1).sublist(288), everyElement(0));

    final Uint8List consumed = fixtures.consumeFonixModel();
    consumed[0] ^= 0xff;
    expect(fixtures.consumeFonixModel(), source.fonixModel);
  });

  test('strict matrix decoder rejects malformed and unbounded envelopes', () {
    final Uint8List badMagic = Uint8List.fromList(source.referenceInput);
    badMagic[0] ^= 0xff;
    expect(
      () => QualificationMatrixInput.decode(badMagic),
      throwsFormatException,
    );

    final Uint8List trailing = Uint8List.fromList(<int>[
      ...source.referenceInput,
      0,
    ]);
    expect(
      () => QualificationMatrixInput.decode(trailing),
      throwsFormatException,
    );

    final Uint8List nonFinite = Uint8List.fromList(source.referenceInput);
    ByteData.sublistView(nonFinite).setFloat32(24, double.nan, Endian.little);
    expect(
      () => QualificationMatrixInput.decode(nonFinite),
      throwsFormatException,
    );

    final Uint8List oversized = Uint8List.fromList(source.referenceInput);
    ByteData.sublistView(oversized).setUint32(12, 513, Endian.little);
    expect(() => QualificationMatrixInput.decode(oversized), throwsRangeError);
  });

  test('prepared bundle rejects drift in closed matrix and output values', () {
    final Uint8List cancellation = Uint8List.fromList(source.cancellationInput);
    ByteData.sublistView(cancellation).setFloat32(24, 0, Endian.little);
    expect(
      () => _prepare(source, cancellationInput: cancellation),
      throwsFormatException,
    );

    final Uint8List output = Uint8List.fromList(source.referenceOutput);
    ByteData.sublistView(output).setFloat32(0, 9, Endian.little);
    expect(
      () => _prepare(source, referenceOutput: output),
      throwsFormatException,
    );
    expect(
      () => _prepare(
        source,
        referenceOutput: Uint8List.fromList(<int>[...output, 0]),
      ),
      throwsFormatException,
    );
  });

  test('strict PCM16 WAV decoder rejects header, rate, and size drift', () {
    final Uint8List badRiffSize = Uint8List.fromList(source.sherpaAudio);
    ByteData.sublistView(badRiffSize).setUint32(4, 1, Endian.little);
    expect(
      () => QualificationPcmAudio.decode(badRiffSize),
      throwsFormatException,
    );

    final Uint8List badRate = Uint8List.fromList(source.sherpaAudio);
    ByteData.sublistView(badRate).setUint32(24, 8000, Endian.little);
    expect(() => QualificationPcmAudio.decode(badRate), throwsFormatException);

    final Uint8List truncated = Uint8List.fromList(
      source.sherpaAudio.sublist(0, source.sherpaAudio.length - 1),
    );
    expect(
      () => QualificationPcmAudio.decode(truncated),
      throwsFormatException,
    );
  });

  test('Sherpa reference is duplicate-free and bound to WAV/profile', () {
    final String reference = utf8.decode(source.sherpaReference);
    final Uint8List duplicate = Uint8List.fromList(
      utf8.encode(
        reference.replaceFirst(
          '"schemaVersion":1,',
          '"schemaVersion":1,"schemaVersion":1,',
        ),
      ),
    );
    expect(
      () => _prepare(source, sherpaReference: duplicate),
      throwsFormatException,
    );

    final Uint8List wrongSamples = Uint8List.fromList(
      utf8.encode(
        reference.replaceFirst('"sampleCount":128000', '"sampleCount":127999'),
      ),
    );
    expect(
      () => _prepare(source, sherpaReference: wrongSamples),
      throwsFormatException,
    );

    final Uint8List wrongProfile = Uint8List.fromList(
      utf8.encode(reference.replaceFirst('"numThreads":1', '"numThreads":2')),
    );
    expect(
      () => _prepare(source, sherpaReference: wrongProfile),
      throwsFormatException,
    );

    final Uint8List wrongRevision = Uint8List.fromList(
      utf8.encode(
        reference.replaceFirst(
          androidSherpaNativeRevision,
          '042807252687d81b40d6315f23470a1512a00de3',
        ),
      ),
    );
    expect(
      () => _prepare(source, sherpaReference: wrongRevision),
      throwsFormatException,
    );

    final Uint8List changedModel = Uint8List.fromList(source.sherpaModel);
    changedModel[0] ^= 0xff;
    expect(
      () => _prepare(source, sherpaModel: changedModel),
      throwsFormatException,
    );

    final Uint8List changedAudio = Uint8List.fromList(source.sherpaAudio);
    changedAudio.last ^= 0x01;
    expect(
      () => _prepare(source, sherpaAudio: changedAudio),
      throwsFormatException,
    );
  });
}

AndroidQualificationFixtures _prepare(
  _FixtureBytes source, {
  Uint8List? referenceOutput,
  Uint8List? cancellationInput,
  Uint8List? sherpaModel,
  Uint8List? sherpaAudio,
  Uint8List? sherpaReference,
}) => AndroidQualificationFixtures.fromBytes(
  fonixModel: source.fonixModel,
  fonixReferenceInput: source.referenceInput,
  fonixReferenceOutput: referenceOutput ?? source.referenceOutput,
  fonixCancellationInput: cancellationInput ?? source.cancellationInput,
  sherpaModel: sherpaModel ?? source.sherpaModel,
  sherpaAudio: sherpaAudio ?? source.sherpaAudio,
  sherpaReference: sherpaReference ?? source.sherpaReference,
);

final class _FixtureBytes {
  const _FixtureBytes({
    required this.fonixModel,
    required this.referenceInput,
    required this.referenceOutput,
    required this.cancellationInput,
    required this.sherpaModel,
    required this.sherpaAudio,
    required this.sherpaReference,
  });

  final Uint8List fonixModel;
  final Uint8List referenceInput;
  final Uint8List referenceOutput;
  final Uint8List cancellationInput;
  final Uint8List sherpaModel;
  final Uint8List sherpaAudio;
  final Uint8List sherpaReference;
}

_FixtureBytes _fixtureBytes() {
  final Uint8List referenceInput = _matrixBytes(
    2,
    left: (int row, int column) => <double>[1, 2, 3, 4][row * 2 + column],
  );
  final Uint8List referenceOutput = Uint8List(16);
  final ByteData output = ByteData.sublistView(referenceOutput);
  for (var index = 0; index < 4; index += 1) {
    output.setFloat32(index * 4, (index + 1).toDouble(), Endian.little);
  }
  final Uint8List model = Uint8List(212860);
  for (var index = 0; index < model.length; index += 1) {
    model[index] = (index * 37 + 11) & 0xff;
  }
  final Uint8List audio = _wav(sampleCount: 128000);
  final Map<String, Object?> profile = <String, Object?>{
    'id': androidSherpaQualificationProfileId,
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
  final Uint8List sherpaReference = Uint8List.fromList(
    utf8.encode(
      jsonEncode(<String, Object?>{
        'schemaVersion': 1,
        'sherpa': <String, Object?>{
          'packageVersion': androidSherpaPackageVersion,
          'nativeRevision': androidSherpaNativeRevision,
        },
        'profile': profile,
        'model': <String, Object?>{
          'file': androidSherpaModelFilename,
          'sizeBytes': model.length,
          'sha256': _sha(model),
        },
        'audio': <String, Object?>{
          'file': androidSherpaAudioFilename,
          'encoding': 'wav-pcm-s16le-mono',
          'generatorId': androidSherpaAudioGeneratorId,
          'sizeBytes': audio.length,
          'sha256': _sha(audio),
          'sampleCount': 128000,
        },
        'observedSegments': <Object?>[
          <String, Object?>{'startSample': 11872, 'sampleCount': 96160},
        ],
        'invariant': <String, Object?>{
          'minimumSegments': 1,
          'maximumSegments': 1,
          'minimumTotalSegmentSamples': 95232,
          'maximumTotalSegmentSamples': 97280,
          'maximumSegmentSamples': 97280,
        },
        'claimBoundary': androidSherpaReferenceClaimBoundary,
      }),
    ),
  );
  return _FixtureBytes(
    fonixModel: Uint8List.fromList(<int>[8, 8, 18, 4, 84, 69, 83, 84]),
    referenceInput: referenceInput,
    referenceOutput: referenceOutput,
    cancellationInput: _matrixBytes(
      512,
      left: (int row, int column) =>
          (((row * 17 + column * 31) % 23) - 11) / 16,
    ),
    sherpaModel: model,
    sherpaAudio: audio,
    sherpaReference: sherpaReference,
  );
}

Uint8List _matrixBytes(
  int dimension, {
  required double Function(int row, int column) left,
}) {
  final int elements = dimension * dimension;
  final Uint8List bytes = Uint8List(24 + elements * 8);
  bytes.setRange(0, 8, <int>[70, 79, 78, 73, 88, 77, 49, 0]);
  final ByteData data = ByteData.sublistView(bytes);
  data.setUint32(8, 1, Endian.little);
  data.setUint32(12, dimension, Endian.little);
  data.setUint32(16, dimension, Endian.little);
  data.setUint32(20, dimension, Endian.little);
  var offset = 24;
  for (var row = 0; row < dimension; row += 1) {
    for (var column = 0; column < dimension; column += 1) {
      data.setFloat32(offset, left(row, column), Endian.little);
      offset += 4;
    }
  }
  for (var row = 0; row < dimension; row += 1) {
    for (var column = 0; column < dimension; column += 1) {
      data.setFloat32(offset, row == column ? 1 : 0, Endian.little);
      offset += 4;
    }
  }
  return bytes;
}

Uint8List _wav({required int sampleCount}) {
  final int dataBytes = sampleCount * 2;
  final Uint8List bytes = Uint8List(44 + dataBytes);
  bytes.setRange(0, 4, ascii.encode('RIFF'));
  bytes.setRange(8, 12, ascii.encode('WAVE'));
  bytes.setRange(12, 16, ascii.encode('fmt '));
  bytes.setRange(36, 40, ascii.encode('data'));
  final ByteData data = ByteData.sublistView(bytes);
  data.setUint32(4, bytes.length - 8, Endian.little);
  data.setUint32(16, 16, Endian.little);
  data.setUint16(20, 1, Endian.little);
  data.setUint16(22, 1, Endian.little);
  data.setUint32(24, 16000, Endian.little);
  data.setUint32(28, 32000, Endian.little);
  data.setUint16(32, 2, Endian.little);
  data.setUint16(34, 16, Endian.little);
  data.setUint32(40, dataBytes, Endian.little);
  for (var index = 0; index < sampleCount; index += 1) {
    data.setInt16(44 + index * 2, (index % 200) - 100, Endian.little);
  }
  return bytes;
}

String _sha(Uint8List value) => sha256.convert(value).toString();
