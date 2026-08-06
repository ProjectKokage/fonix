import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/runtime.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  group('model sources', () {
    test('byte models are copied and bounded', () {
      final Uint8List callerBytes = Uint8List.fromList(<int>[1, 2, 3]);
      final OrtBytesModelSource source =
          OrtModelSource.bytes(callerBytes, modelId: 'mul-test')
              as OrtBytesModelSource;
      callerBytes[0] = 9;

      expect(source.kind, OrtModelSourceKind.bytes);
      expect(source.modelId, 'mul-test');
      expect(source.bytes, <int>[1, 2, 3]);
      final Uint8List firstCopy = source.bytes;
      firstCopy[1] = 8;
      expect(source.bytes, <int>[1, 2, 3]);
      expect(() => OrtModelSource.bytes(Uint8List(0)), throwsRangeError);
      expect(
        () => OrtModelSource.bytes(
          Uint8List(4),
          limits: OrtResourceLimits(maxModelBytes: 3),
        ),
        throwsRangeError,
      );
    });

    test('file models always carry a bounded absolute root', () {
      final String root = p.join(Directory.systemTemp.path, 'fonix-models');
      final String modelPath = p.join(root, 'mul.onnx');
      final OrtFileModelSource implicit =
          OrtModelSource.file(absolutePath: modelPath) as OrtFileModelSource;
      expect(implicit.allowedRoot, p.dirname(p.normalize(modelPath)));

      final OrtFileModelSource explicit =
          OrtModelSource.file(absolutePath: modelPath, allowedRoot: root)
              as OrtFileModelSource;
      expect(explicit.absolutePath, p.normalize(modelPath));
      expect(explicit.allowedRoot, p.normalize(root));
      expect(
        () => OrtModelSource.file(
          absolutePath: p.join(Directory.systemTemp.path, 'outside.onnx'),
          allowedRoot: root,
        ),
        throwsArgumentError,
      );
    });
  });

  group('resource and session limits', () {
    test('defaults and customization cannot exceed native hard limits', () {
      expect(OrtResourceLimits.defaults.maxModelBytes, 512 * 1024 * 1024);
      expect(
        () => OrtResourceLimits(maxModelBytes: 2 * 1024 * 1024 * 1024 + 1),
        throwsRangeError,
      );
      expect(() => OrtResourceLimits(maxRank: 33), throwsRangeError);
      expect(
        () => OrtResourceLimits(maxDiagnosticsBytes: 1024 * 1024 + 1),
        throwsRangeError,
      );
    });

    test('artifact paths require and remain inside an explicit root', () {
      final String root = p.join(Directory.systemTemp.path, 'fonix-artifacts');
      final String profile = p.join(root, 'profile');
      final OrtSessionOptions options = OrtSessionOptions(
        enableProfiling: true,
        profilePathPrefix: profile,
        artifactRoot: root,
      );
      expect(options.profilePathPrefix, p.normalize(profile));
      expect(options.artifactRoot, p.normalize(root));

      expect(
        () => OrtSessionOptions(
          enableProfiling: true,
          profilePathPrefix: profile,
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtSessionOptions(
          optimizedModelPath: p.join(Directory.systemTemp.path, 'outside.onnx'),
          artifactRoot: root,
        ),
        throwsArgumentError,
      );
    });

    test('invalid private configuration values are not echoed', () {
      const String privateValue = '/Users/private/person/token\n';
      Object? error;
      try {
        OrtExecutionProvider.named(
          'cuda',
          options: const <String, String>{'secret_path': privateValue},
        );
      } catch (caught) {
        error = caught;
      }
      expect(error, isA<ArgumentError>());
      expect(error.toString(), isNot(contains(privateValue.trim())));
    });
  });

  group('strict native metadata protocol', () {
    test(
      'accepts static, anonymous dynamic, and named symbolic dimensions',
      () {
        final OrtSessionMetadata metadata = parseOrtSessionMetadataForTesting(
          jsonEncode(<String, Object?>{
            'schemaVersion': 2,
            'inputs': <Object?>[
              <String, Object?>{
                'name': 'X',
                'kind': 'tensor',
                'elementType': OrtTensorElementType.float32.nativeValue,
                'hasShape': true,
                'dimensions': <Object?>[2, null, null],
                'symbolicDimensions': <Object?>[null, null, 'batch'],
              },
            ],
            'outputs': <Object?>[
              <String, Object?>{
                'name': 'Y',
                'kind': 'tensor',
                'elementType': OrtTensorElementType.float32.nativeValue,
                'hasShape': true,
                'dimensions': <Object?>[2, 3],
                'symbolicDimensions': <Object?>[null, null],
              },
            ],
          }),
        );

        final List<OrtDimension> dimensions =
            metadata.inputs.single.type.dimensions;
        expect(dimensions[0].value, 2);
        expect(dimensions[1].isDynamic, isTrue);
        expect(dimensions[1].symbol, isNull);
        expect(dimensions[2].isDynamic, isTrue);
        expect(dimensions[2].symbol, 'batch');
        expect(metadata.outputs.single.name, 'Y');
        expect(metadata.inputs.single.type.hasShape, isTrue);
      },
    );

    test('distinguishes an unknown rank from a scalar shape', () {
      final OrtSessionMetadata unknown = parseOrtSessionMetadataForTesting(
        _metadataJson(
          hasShape: false,
          dimensions: const <Object?>[],
          symbols: const <Object?>[],
        ),
      );
      final OrtSessionMetadata scalar = parseOrtSessionMetadataForTesting(
        _metadataJson(
          dimensions: const <Object?>[],
          symbols: const <Object?>[],
        ),
      );

      expect(unknown.inputs.single.type.hasShape, isFalse);
      expect(scalar.inputs.single.type.hasShape, isTrue);
      expect(unknown.inputs.single.type, isNot(scalar.inputs.single.type));
      expect(
        () => parseOrtSessionMetadataForTesting(
          _metadataJson(
            hasShape: false,
            dimensions: const <Object?>[2],
            symbols: const <Object?>[null],
          ),
        ),
        throwsFormatException,
      );
    });

    test('rejects negative dimensions and empty symbolic names', () {
      expect(
        () => parseOrtSessionMetadataForTesting(
          _metadataJson(
            dimensions: const <Object?>[-1],
            symbols: const <Object?>[null],
          ),
        ),
        throwsFormatException,
      );

      expect(
        () => parseOrtTensorInfoForTesting(
          jsonEncode(<String, Object?>{
            'schemaVersion': 1,
            'kind': 'tensor',
            'elementType': OrtTensorElementType.int32.nativeValue,
            'dimensions': <int>[1],
            'byteLength': 3,
          }),
          limits: OrtResourceLimits(maxTensorBytes: 3),
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtSessionMetadataForTesting(
          _metadataJson(
            dimensions: const <Object?>[null],
            symbols: const <Object?>[''],
          ),
        ),
        throwsFormatException,
      );
    });

    test('tensor info is exact, bounded, and internally consistent', () {
      final OrtTensorInfo info = parseOrtTensorInfoForTesting(
        jsonEncode(<String, Object?>{
          'schemaVersion': 1,
          'kind': 'tensor',
          'elementType': OrtTensorElementType.int32.nativeValue,
          'dimensions': <int>[2, 3],
          'byteLength': 24,
        }),
      );
      expect(info.elementType, OrtTensorElementType.int32);
      expect(info.shape.dimensions, <int>[2, 3]);
      expect(info.byteLength, 24);

      expect(
        () => parseOrtTensorInfoForTesting(
          jsonEncode(<String, Object?>{
            'schemaVersion': 1,
            'kind': 'tensor',
            'elementType': OrtTensorElementType.int32.nativeValue,
            'dimensions': <int>[2, 3],
            'byteLength': 23,
          }),
        ),
        throwsFormatException,
      );
    });
  });
}

String _metadataJson({
  bool hasShape = true,
  required List<Object?> dimensions,
  required List<Object?> symbols,
}) => jsonEncode(<String, Object?>{
  'schemaVersion': 2,
  'inputs': <Object?>[
    <String, Object?>{
      'name': 'X',
      'kind': 'tensor',
      'elementType': OrtTensorElementType.float32.nativeValue,
      'hasShape': hasShape,
      'dimensions': dimensions,
      'symbolicDimensions': symbols,
    },
  ],
  'outputs': <Object?>[],
});
