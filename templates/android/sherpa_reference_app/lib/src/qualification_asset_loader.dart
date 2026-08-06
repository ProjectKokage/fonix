import 'package:crypto/crypto.dart';
import 'package:flutter/services.dart';

import 'qualification_fixtures.dart';

const String qualificationAssetRoot = 'assets/qualification/';
const String qualificationManifestAsset =
    '${qualificationAssetRoot}fonix_fixture_manifest.json';

/// The closed result of checking the staged qualification asset set.
sealed class QualificationFixtureLoadResult<T> {
  const QualificationFixtureLoadResult();
}

/// The marker is not part of this application build.
final class QualificationFixturesAbsent<T>
    extends QualificationFixtureLoadResult<T> {
  const QualificationFixturesAbsent();
}

/// Every staged asset matched its application-owned byte identity.
final class QualificationFixturesReady<T>
    extends QualificationFixtureLoadResult<T> {
  const QualificationFixturesReady(this.fixtures);

  final T fixtures;
}

abstract interface class QualificationFixtureLoader<T> {
  Future<QualificationFixtureLoadResult<T>> load();
}

/// A bounded asset index and loader seam for host tests.
abstract interface class QualificationAssetSource {
  Future<List<String>> listAssetKeys();
  Future<ByteData> load(String key);
}

/// Reads Flutter's generated asset index and the application asset bundle.
final class FlutterQualificationAssetSource
    implements QualificationAssetSource {
  FlutterQualificationAssetSource({AssetBundle? bundle})
    : _bundle = bundle ?? rootBundle;

  final AssetBundle _bundle;

  @override
  Future<List<String>> listAssetKeys() async {
    final AssetManifest manifest = await AssetManifest.loadFromAssetBundle(
      _bundle,
    );
    return List<String>.unmodifiable(manifest.listAssets());
  }

  @override
  Future<ByteData> load(String key) => _bundle.load(key);
}

/// One exact application-owned asset identity.
///
/// This type is public only within the private reference application so host
/// tests can exercise offset-safe copying and byte bounds independently of the
/// multi-megabyte production fixtures.
final class PinnedQualificationAsset {
  const PinnedQualificationAsset({
    required this.path,
    required this.maximumBytes,
    required this.sizeBytes,
    required this.sha256Digest,
  });

  final String path;
  final int maximumBytes;
  final int sizeBytes;
  final String sha256Digest;

  Uint8List copyAndVerify(ByteData data) {
    if (maximumBytes < 1 ||
        sizeBytes < 1 ||
        sizeBytes > maximumBytes ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(sha256Digest)) {
      throw const QualificationAssetException('invalid-pinned-identity');
    }
    final int byteLength = data.lengthInBytes;
    if (byteLength < 1 || byteLength > maximumBytes) {
      throw QualificationAssetException('asset-byte-bound:$path');
    }
    if (byteLength != sizeBytes) {
      throw QualificationAssetException('asset-size-mismatch:$path');
    }
    final Uint8List source = data.buffer.asUint8List(
      data.offsetInBytes,
      byteLength,
    );
    final Uint8List copied = Uint8List.fromList(source);
    if (sha256.convert(copied).toString() != sha256Digest) {
      throw QualificationAssetException('asset-hash-mismatch:$path');
    }
    return copied;
  }
}

/// Loads the exact runtime-staged Android coexistence fixtures.
///
/// The committed template declares no qualification assets. A staging gate
/// opts in by adding the fixed marker and the seven exact inputs to Flutter's
/// asset index. The marker's JSON declarations are deliberately never parsed
/// or trusted; every file has an application-owned size and SHA-256 pin here.
final class AndroidQualificationAssetLoader
    implements QualificationFixtureLoader<AndroidQualificationFixtures> {
  AndroidQualificationAssetLoader({QualificationAssetSource? source})
    : _source = source ?? FlutterQualificationAssetSource();

  final QualificationAssetSource _source;

  static const PinnedQualificationAsset _manifest = PinnedQualificationAsset(
    path: qualificationManifestAsset,
    maximumBytes: 64 * 1024,
    sizeBytes: 1771,
    sha256Digest:
        '66218ac6c9e98bf15ae250472229ee517e3475511f4d0e50aaf03007e648bafc',
  );
  static const PinnedQualificationAsset _fonixModel = PinnedQualificationAsset(
    path: '${qualificationAssetRoot}fonix_dynamic_matmul_chain.onnx',
    maximumBytes: 64 * 1024,
    sizeBytes: 4437,
    sha256Digest:
        'da7dc57b74c05d57109100bccd1e745c234eb9f015be37284cf519f977a76076',
  );
  static const PinnedQualificationAsset _fonixReferenceInput =
      PinnedQualificationAsset(
        path: '${qualificationAssetRoot}fonix_reference_input.bin',
        maximumBytes: 1024,
        sizeBytes: 56,
        sha256Digest:
            '0bd93e8768ce38931d43c96a5fab06feb2b2ee6724f94adcb03893d60c864bab',
      );
  static const PinnedQualificationAsset _fonixReferenceOutput =
      PinnedQualificationAsset(
        path: '${qualificationAssetRoot}fonix_reference_output.bin',
        maximumBytes: 1024,
        sizeBytes: 16,
        sha256Digest:
            'ad73b9acd6e4a74b2f5bb5386658ce3bb146cd040a1867646ab3b973fb6632b1',
      );
  static const PinnedQualificationAsset _fonixCancellationInput =
      PinnedQualificationAsset(
        path: '${qualificationAssetRoot}fonix_cancellation_input.bin',
        maximumBytes: 4 * 1024 * 1024,
        sizeBytes: 2097176,
        sha256Digest:
            '45da4d7ebbe3f6414ffd60e140fb86d273d8d0c2f1a30cc8f6d97eb6f993a0fb',
      );
  static const PinnedQualificationAsset _sherpaModel = PinnedQualificationAsset(
    path: '${qualificationAssetRoot}silero_vad.int8.onnx',
    maximumBytes: 1024 * 1024,
    sizeBytes: 212860,
    sha256Digest:
        'c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20',
  );
  static const PinnedQualificationAsset _sherpaAudio = PinnedQualificationAsset(
    path: '${qualificationAssetRoot}sherpa_synthetic_speech.wav',
    maximumBytes: 1024 * 1024,
    sizeBytes: 256044,
    sha256Digest:
        '2956ffe337260f54408e90f03f705e2c354074bb97b8935cdcd0d1760c313c46',
  );
  static const PinnedQualificationAsset _sherpaReference =
      PinnedQualificationAsset(
        path: '${qualificationAssetRoot}sherpa_vad_reference.json',
        maximumBytes: 64 * 1024,
        sizeBytes: 1159,
        sha256Digest:
            'fb49299f25a4287c4c0b726e62ebafb099c6984f304bc0d3c09d4c722c8306cd',
      );

  static const List<PinnedQualificationAsset> _closedAssets =
      <PinnedQualificationAsset>[
        _manifest,
        _fonixModel,
        _fonixReferenceInput,
        _fonixReferenceOutput,
        _fonixCancellationInput,
        _sherpaModel,
        _sherpaAudio,
        _sherpaReference,
      ];

  @override
  Future<QualificationFixtureLoadResult<AndroidQualificationFixtures>>
  load() async {
    final List<String> keys = await _source.listAssetKeys();
    if (!keys.contains(qualificationManifestAsset)) {
      return const QualificationFixturesAbsent<AndroidQualificationFixtures>();
    }

    final List<String> qualificationKeys = keys
        .where((String key) => key.startsWith(qualificationAssetRoot))
        .toList(growable: false);
    final Set<String> expectedKeys = _closedAssets
        .map((PinnedQualificationAsset asset) => asset.path)
        .toSet();
    if (qualificationKeys.length != expectedKeys.length ||
        qualificationKeys.toSet().length != expectedKeys.length ||
        !expectedKeys.every(qualificationKeys.contains)) {
      throw const QualificationAssetException('invalid-asset-inventory');
    }

    final Map<String, Uint8List> bytes = <String, Uint8List>{};
    for (final PinnedQualificationAsset asset in _closedAssets) {
      final ByteData data = await _source.load(asset.path);
      bytes[asset.path] = asset.copyAndVerify(data);
    }

    return QualificationFixturesReady<AndroidQualificationFixtures>(
      AndroidQualificationFixtures.fromBytes(
        fonixModel: bytes[_fonixModel.path]!,
        fonixReferenceInput: bytes[_fonixReferenceInput.path]!,
        fonixReferenceOutput: bytes[_fonixReferenceOutput.path]!,
        fonixCancellationInput: bytes[_fonixCancellationInput.path]!,
        sherpaModel: bytes[_sherpaModel.path]!,
        sherpaAudio: bytes[_sherpaAudio.path]!,
        sherpaReference: bytes[_sherpaReference.path]!,
      ),
    );
  }
}

final class QualificationAssetException implements Exception {
  const QualificationAssetException(this.code);

  final String code;

  @override
  String toString() => 'QualificationAssetException($code)';
}
