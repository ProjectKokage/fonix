import 'dart:collection';
import 'dart:convert';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/runtime.dart';
import 'package:test/test.dart';

void main() {
  group('recursive metadata domain', () {
    test('constructors copy and preserve nested ONNX value types', () {
      final List<OrtDimension> callerDimensions = <OrtDimension>[
        OrtDimension.dynamic('batch'),
        OrtDimension.fixed(2),
      ];
      final OrtTypeInfo tensor = OrtTypeInfo.tensor(
        elementType: OrtTensorElementType.float32,
        dimensions: callerDimensions,
      );
      callerDimensions.clear();

      final OrtTypeInfo nested = OrtTypeInfo.optional(
        OrtTypeInfo.sequence(
          OrtTypeInfo.map(
            keyElementType: OrtTensorElementType.string,
            value: tensor,
          ),
        ),
      );
      expect(nested.kind, OrtValueKind.optional);
      expect(nested.optionalElement!.kind, OrtValueKind.sequence);
      expect(
        nested.optionalElement!.sequenceElement!.mapKeyType,
        OrtTensorElementType.string,
      );
      expect(
        nested.optionalElement!.sequenceElement!.mapValueType!.dimensions,
        <OrtDimension>[OrtDimension.dynamic('batch'), OrtDimension.fixed(2)],
      );
      expect(
        () => tensor.dimensions.add(OrtDimension.fixed(3)),
        throwsUnsupportedError,
      );
      expect(
        nested,
        OrtTypeInfo.optional(
          OrtTypeInfo.sequence(
            OrtTypeInfo.map(
              keyElementType: OrtTensorElementType.string,
              value: OrtTypeInfo.tensor(
                elementType: OrtTensorElementType.float32,
                dimensions: <OrtDimension>[
                  OrtDimension.dynamic('batch'),
                  OrtDimension.fixed(2),
                ],
              ),
            ),
          ),
        ),
      );
    });

    test('invalid recursive type combinations fail closed', () {
      expect(
        () => OrtTypeInfo.tensor(elementType: OrtTensorElementType.undefined),
        throwsArgumentError,
      );
      expect(
        () => OrtTypeInfo.map(
          keyElementType: OrtTensorElementType.float32,
          value: OrtTypeInfo.tensor(elementType: OrtTensorElementType.float32),
        ),
        throwsArgumentError,
      );
      expect(() => OrtDimension.dynamic('bad\nname'), throwsArgumentError);
      expect(
        () => OrtTypeInfo.tensor(
          elementType: OrtTensorElementType.float32,
          hasShape: false,
          dimensions: <OrtDimension>[OrtDimension.fixed(1)],
        ),
        throwsArgumentError,
      );
    });

    test('public construction enforces rank, depth, and node limits', () {
      expect(
        () => OrtTypeInfo.tensor(
          elementType: OrtTensorElementType.float32,
          dimensions: _UnreadableDimensionList(33),
        ),
        throwsRangeError,
      );

      OrtTypeInfo nested = OrtTypeInfo.tensor(
        elementType: OrtTensorElementType.float32,
      );
      for (var index = 0; index < 7; index += 1) {
        nested = OrtTypeInfo.optional(nested);
      }
      expect(nested.hashCode, isA<int>());
      expect(nested, nested);
      expect(() => OrtTypeInfo.optional(nested), throwsRangeError);
      expect(
        () => OrtTypeInfo.optional(
          OrtTypeInfo.tensor(elementType: OrtTensorElementType.float32),
          limits: OrtResourceLimits(maxTypeNodes: 1),
        ),
        throwsRangeError,
      );
    });
  });

  group('isolated recursive metadata protocol', () {
    test('strictly parses tensors, sequences, maps, and optionals', () {
      final OrtSessionMetadata metadata =
          parseOrtRecursiveTypeMetadataForTesting(
            jsonEncode(<String, Object?>{
              'schemaVersion': 1,
              'inputs': <Object?>[
                <String, Object?>{
                  'name': '入力',
                  'type': _tensorNode(
                    dimensions: <Object?>[null, 2],
                    symbols: <Object?>['batch', null],
                  ),
                },
              ],
              'outputs': <Object?>[
                <String, Object?>{
                  'name': 'sequence',
                  'type': <String, Object?>{
                    'kind': 'sequence',
                    'element': _tensorNode(
                      dimensions: <Object?>[2],
                      symbols: <Object?>[null],
                    ),
                  },
                },
                <String, Object?>{
                  'name': 'zipmap',
                  'type': <String, Object?>{
                    'kind': 'sequence',
                    'element': <String, Object?>{
                      'kind': 'map',
                      'keyElementType': OrtTensorElementType.string.nativeValue,
                      'value': _tensorNode(hasShape: false),
                    },
                  },
                },
                <String, Object?>{
                  'name': 'optional',
                  'type': <String, Object?>{
                    'kind': 'optional',
                    'element': _tensorNode(
                      dimensions: <Object?>[2],
                      symbols: <Object?>[null],
                    ),
                  },
                },
              ],
            }),
          );

      expect(metadata.inputs.single.name, '入力');
      expect(metadata.inputs.single.type.dimensions.first.symbol, 'batch');
      expect(metadata.outputs[0].type.kind, OrtValueKind.sequence);
      expect(
        metadata.outputs[1].type.sequenceElement!.mapKeyType,
        OrtTensorElementType.string,
      );
      expect(
        metadata.outputs[1].type.sequenceElement!.mapValueType!.hasShape,
        isFalse,
      );
      expect(metadata.outputs[2].type.kind, OrtValueKind.optional);
      expect(
        () => metadata.outputs.add(
          OrtValueInfo(
            name: 'other',
            type: OrtTypeInfo.tensor(elementType: OrtTensorElementType.float32),
          ),
        ),
        throwsUnsupportedError,
      );
    });

    test(
      'rejects unknown fields, invalid map keys, and malformed dimensions',
      () {
        expect(
          () => parseOrtRecursiveTypeMetadataForTesting(
            _typeDocument(<String, Object?>{..._tensorNode(), 'unknown': true}),
          ),
          throwsFormatException,
        );
        expect(
          () => parseOrtRecursiveTypeMetadataForTesting(
            _typeDocument(<String, Object?>{
              'kind': 'map',
              'keyElementType': OrtTensorElementType.float32.nativeValue,
              'value': _tensorNode(),
            }),
          ),
          throwsFormatException,
        );
        expect(
          () => parseOrtRecursiveTypeMetadataForTesting(
            _typeDocument(
              _tensorNode(dimensions: <Object?>[-1], symbols: <Object?>[null]),
            ),
          ),
          throwsFormatException,
        );
        expect(
          () => parseOrtRecursiveTypeMetadataForTesting(
            _typeDocument(
              _tensorNode(dimensions: <Object?>[null], symbols: <Object?>['']),
            ),
          ),
          throwsFormatException,
        );
      },
    );

    test('enforces global node and recursive depth limits', () {
      Object node = _tensorNode();
      for (var index = 0; index < 8; index += 1) {
        node = <String, Object?>{'kind': 'optional', 'element': node};
      }
      expect(
        () => parseOrtRecursiveTypeMetadataForTesting(
          _typeDocument(node as Map<String, Object?>),
        ),
        throwsFormatException,
      );

      expect(
        () => parseOrtRecursiveTypeMetadataForTesting(
          jsonEncode(<String, Object?>{
            'schemaVersion': 1,
            'inputs': <Object?>[
              <String, Object?>{
                'name': 'a',
                'type': <String, Object?>{
                  'kind': 'sequence',
                  'element': _tensorNode(),
                },
              },
            ],
            'outputs': <Object?>[
              <String, Object?>{'name': 'b', 'type': _tensorNode()},
            ],
          }),
          limits: OrtResourceLimits(maxTypeNodes: 2),
        ),
        throwsFormatException,
      );
    });
  });

  group('copied model metadata protocol', () {
    test('copies multilingual fields and immutable custom metadata', () {
      final OrtModelMetadata metadata = parseOrtModelMetadataForTesting(
        jsonEncode(<String, Object?>{
          'schemaVersion': 1,
          'producerName': 'fonix-fixtures',
          'graphName': 'メタデータグラフ',
          'domain': 'dev.fonix.fixtures.metadata',
          'description': 'モデルの説明\nsecond line',
          'graphDescription': 'グラフの説明',
          'version': 42,
          'customMetadata': <String, Object?>{
            'empty': '',
            'purpose': 'model metadata contract',
            'unicode': 'こんにちは🌿',
          },
        }),
      );

      expect(metadata.producerName, 'fonix-fixtures');
      expect(metadata.graphName, 'メタデータグラフ');
      expect(metadata.version, 42);
      expect(metadata.custom['empty'], '');
      expect(metadata.custom['unicode'], 'こんにちは🌿');
      expect(() => metadata.custom['other'] = 'value', throwsUnsupportedError);
      final OrtModelMetadata equalWithDifferentMapOrder = OrtModelMetadata(
        producerName: 'fonix-fixtures',
        graphName: 'メタデータグラフ',
        domain: 'dev.fonix.fixtures.metadata',
        description: 'モデルの説明\nsecond line',
        graphDescription: 'グラフの説明',
        version: 42,
        custom: const <String, String>{
          'unicode': 'こんにちは🌿',
          'purpose': 'model metadata contract',
          'empty': '',
        },
      );
      expect(metadata, equalWithDifferentMapOrder);
      expect(metadata.hashCode, equalWithDifferentMapOrder.hashCode);
    });

    test('rejects unknown, non-string, excessive, and unsafe fields', () {
      final Map<String, Object?> valid = _modelMetadataJson();
      expect(
        () => parseOrtModelMetadataForTesting(
          jsonEncode(<String, Object?>{...valid, 'unknown': true}),
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtModelMetadataForTesting(
          jsonEncode(<String, Object?>{
            ...valid,
            'customMetadata': <String, Object?>{'bad': 1},
          }),
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtModelMetadataForTesting(
          jsonEncode(<String, Object?>{
            ...valid,
            'customMetadata': <String, Object?>{'a': '', 'b': ''},
          }),
          limits: OrtResourceLimits(maxConfigEntries: 1),
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtModelMetadataForTesting(
          jsonEncode(<String, Object?>{...valid, 'producerName': 'bad\nname'}),
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtModelMetadataForTesting(
          jsonEncode(
            valid,
          ).replaceFirst('"version":0', '"version":9223372036854775808'),
        ),
        throwsFormatException,
      );
    });
  });
}

Map<String, Object?> _tensorNode({
  bool hasShape = true,
  List<Object?> dimensions = const <Object?>[],
  List<Object?> symbols = const <Object?>[],
}) => <String, Object?>{
  'kind': 'tensor',
  'elementType': OrtTensorElementType.float32.nativeValue,
  'hasShape': hasShape,
  'dimensions': dimensions,
  'symbolicDimensions': symbols,
};

final class _UnreadableDimensionList extends ListBase<OrtDimension> {
  _UnreadableDimensionList(this.length);

  @override
  int length;

  @override
  OrtDimension operator [](int index) => throw StateError('must not be read');

  @override
  void operator []=(int index, OrtDimension value) =>
      throw UnsupportedError('immutable');
}

String _typeDocument(Map<String, Object?> type) => jsonEncode(<String, Object?>{
  'schemaVersion': 1,
  'inputs': <Object?>[
    <String, Object?>{'name': 'value', 'type': type},
  ],
  'outputs': <Object?>[],
});

Map<String, Object?> _modelMetadataJson() => <String, Object?>{
  'schemaVersion': 1,
  'producerName': '',
  'graphName': '',
  'domain': '',
  'description': '',
  'graphDescription': '',
  'version': 0,
  'customMetadata': <String, Object?>{},
};
