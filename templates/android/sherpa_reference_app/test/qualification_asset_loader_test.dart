import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:fonix_sherpa_reference/src/qualification_asset_loader.dart';
import 'package:fonix_sherpa_reference/src/qualification_fixtures.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AndroidQualificationAssetLoader', () {
    test('default bundle is one closed qualification state', () async {
      final QualificationFixtureLoadResult<AndroidQualificationFixtures>
      result = await AndroidQualificationAssetLoader().load();

      switch (result) {
        case QualificationFixturesAbsent<AndroidQualificationFixtures>():
          // The committed template is intentionally asset-free.
          break;
        case QualificationFixturesReady<AndroidQualificationFixtures>(
          :final AndroidQualificationFixtures fixtures,
        ):
          // The runtime-provisioned gate runs these same host tests after it
          // has staged the exact closed asset set into its private copy.
          expect(fixtures.audio.sampleCount, 128000);
          expect(
            fixtures.pins.fonixModelSha256,
            'da7dc57b74c05d57109100bccd1e745c234eb9f015be37284cf519f977a76076',
          );
          expect(
            fixtures.pins.sherpaModelSha256,
            'c36d490aff5ab924ca6c7aeec4d8f6bd3d22db6fa17611b9c5b17eae58ac3a20',
          );
      }
    });

    test('marker absence is the only unavailable path', () async {
      final _FakeAssetSource source = _FakeAssetSource(
        keys: const <String>['assets/qualification/untrusted-extra.bin'],
      );
      final QualificationFixtureLoadResult<AndroidQualificationFixtures>
      result = await AndroidQualificationAssetLoader(source: source).load();

      expect(
        result,
        isA<QualificationFixturesAbsent<AndroidQualificationFixtures>>(),
      );
      expect(source.loadedKeys, isEmpty);
    });

    test('asset-index failure is not reported as marker absence', () async {
      final AndroidQualificationAssetLoader loader =
          AndroidQualificationAssetLoader(
            source: _FakeAssetSource(listFailure: StateError('index failed')),
          );

      await expectLater(loader.load(), throwsStateError);
    });

    test('present marker requires the exact closed inventory', () async {
      final _FakeAssetSource source = _FakeAssetSource(
        keys: const <String>[qualificationManifestAsset],
      );
      final AndroidQualificationAssetLoader loader =
          AndroidQualificationAssetLoader(source: source);

      await expectLater(
        loader.load(),
        throwsA(
          isA<QualificationAssetException>().having(
            (QualificationAssetException error) => error.code,
            'code',
            'invalid-asset-inventory',
          ),
        ),
      );
      expect(source.loadedKeys, isEmpty);
    });

    test(
      'present exact inventory turns marker load errors into failures',
      () async {
        final StateError markerFailure = StateError('marker load failed');
        final _FakeAssetSource source = _FakeAssetSource(
          keys: _closedAssetKeys,
          loadFailures: <String, Object>{
            qualificationManifestAsset: markerFailure,
          },
        );
        final AndroidQualificationAssetLoader loader =
            AndroidQualificationAssetLoader(source: source);

        await expectLater(loader.load(), throwsA(same(markerFailure)));
        expect(source.loadedKeys, <String>[qualificationManifestAsset]);
      },
    );

    test('duplicate qualification keys fail before bytes are loaded', () async {
      final _FakeAssetSource source = _FakeAssetSource(
        keys: <String>[..._closedAssetKeys, qualificationManifestAsset],
      );

      await expectLater(
        AndroidQualificationAssetLoader(source: source).load(),
        throwsA(isA<QualificationAssetException>()),
      );
      expect(source.loadedKeys, isEmpty);
    });
  });

  group('PinnedQualificationAsset', () {
    test('copies only the ByteData view and detaches from its buffer', () {
      final Uint8List backing = Uint8List.fromList(<int>[91, 1, 2, 3, 92]);
      final ByteData view = ByteData.sublistView(backing, 1, 4);
      final PinnedQualificationAsset pin = PinnedQualificationAsset(
        path: 'fixture.bin',
        maximumBytes: 3,
        sizeBytes: 3,
        sha256Digest: sha256.convert(<int>[1, 2, 3]).toString(),
      );

      final Uint8List copied = pin.copyAndVerify(view);
      backing[1] = 99;

      expect(copied, <int>[1, 2, 3]);
    });

    test('checks the explicit byte bound before exact size', () {
      final PinnedQualificationAsset pin = PinnedQualificationAsset(
        path: 'fixture.bin',
        maximumBytes: 2,
        sizeBytes: 2,
        sha256Digest: sha256.convert(<int>[1, 2]).toString(),
      );

      expect(
        () => pin.copyAndVerify(
          ByteData.sublistView(Uint8List.fromList(<int>[1, 2, 3])),
        ),
        throwsA(
          isA<QualificationAssetException>().having(
            (QualificationAssetException error) => error.code,
            'code',
            'asset-byte-bound:fixture.bin',
          ),
        ),
      );
    });

    test('rejects exact-size and hash drift independently', () {
      final PinnedQualificationAsset sizePin = PinnedQualificationAsset(
        path: 'fixture.bin',
        maximumBytes: 4,
        sizeBytes: 2,
        sha256Digest: sha256.convert(<int>[1, 2]).toString(),
      );
      final PinnedQualificationAsset hashPin = PinnedQualificationAsset(
        path: 'fixture.bin',
        maximumBytes: 3,
        sizeBytes: 3,
        sha256Digest: sha256.convert(<int>[1, 2, 4]).toString(),
      );

      expect(
        () => sizePin.copyAndVerify(
          ByteData.sublistView(Uint8List.fromList(<int>[1, 2, 3])),
        ),
        throwsA(
          isA<QualificationAssetException>().having(
            (QualificationAssetException error) => error.code,
            'code',
            'asset-size-mismatch:fixture.bin',
          ),
        ),
      );
      expect(
        () => hashPin.copyAndVerify(
          ByteData.sublistView(Uint8List.fromList(<int>[1, 2, 3])),
        ),
        throwsA(
          isA<QualificationAssetException>().having(
            (QualificationAssetException error) => error.code,
            'code',
            'asset-hash-mismatch:fixture.bin',
          ),
        ),
      );
    });
  });
}

const List<String> _closedAssetKeys = <String>[
  qualificationManifestAsset,
  '${qualificationAssetRoot}fonix_dynamic_matmul_chain.onnx',
  '${qualificationAssetRoot}fonix_reference_input.bin',
  '${qualificationAssetRoot}fonix_reference_output.bin',
  '${qualificationAssetRoot}fonix_cancellation_input.bin',
  '${qualificationAssetRoot}silero_vad.int8.onnx',
  '${qualificationAssetRoot}sherpa_synthetic_speech.wav',
  '${qualificationAssetRoot}sherpa_vad_reference.json',
];

final class _FakeAssetSource implements QualificationAssetSource {
  _FakeAssetSource({
    this.keys = const <String>[],
    this.listFailure,
    this.loadFailures = const <String, Object>{},
  });

  final List<String> keys;
  final Object? listFailure;
  final Map<String, Object> loadFailures;
  final List<String> loadedKeys = <String>[];

  @override
  Future<List<String>> listAssetKeys() async {
    final Object? failure = listFailure;
    if (failure != null) throw failure;
    return List<String>.of(keys);
  }

  @override
  Future<ByteData> load(String key) async {
    loadedKeys.add(key);
    final Object? failure = loadFailures[key];
    if (failure != null) throw failure;
    return ByteData(1);
  }
}
