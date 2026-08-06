import 'dart:collection';
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
import 'package:test/test.dart';

void main() {
  group('explicit external-data byte sources', () {
    test('copy caller data and preserve caller enumeration order', () {
      final Uint8List callerModel = Uint8List.fromList(<int>[1, 2, 3]);
      final Uint8List aliasedBytes = Uint8List.fromList(<int>[4, 5]);
      final LinkedHashMap<String, Uint8List> callerExternalData =
          LinkedHashMap<String, Uint8List>()
            ..['layers/z.bin'] = aliasedBytes
            ..['layers/a.bin'] = aliasedBytes;

      final OrtBytesModelSource source =
          OrtModelSource.bytes(callerModel, externalData: callerExternalData)
              as OrtBytesModelSource;

      callerModel[0] = 99;
      aliasedBytes[0] = 88;
      callerExternalData
        ..clear()
        ..['replacement.bin'] = Uint8List.fromList(<int>[7]);

      expect(source.bytes, <int>[1, 2, 3]);
      expect(source.externalData.keys, <String>[
        'layers/z.bin',
        'layers/a.bin',
      ]);
      expect(source.externalData['layers/z.bin'], <int>[4, 5]);
      expect(source.externalData['layers/a.bin'], <int>[4, 5]);
      expect(source.hasExternalData, isTrue);
      expect(source.byteLength, 3);
      expect(source.externalDataByteLength, 4);
      expect(source.totalByteLength, 7);

      final Map<String, Uint8List> firstCopy = source.externalData;
      expect(
        () => firstCopy['new.bin'] = Uint8List.fromList(<int>[1]),
        throwsUnsupportedError,
      );
      firstCopy['layers/z.bin']![1] = 77;
      expect(source.externalData['layers/z.bin'], <int>[4, 5]);
      expect(source.externalData['layers/a.bin'], <int>[4, 5]);
    });

    test('accepts exact bounded relative UTF-8 names', () {
      final String maximumAsciiName = List<String>.filled(1024, 'a').join();
      final OrtBytesModelSource source =
          OrtModelSource.bytes(
                Uint8List.fromList(<int>[1]),
                externalData: <String, Uint8List>{
                  'weights.bin': Uint8List(0),
                  '.hidden': Uint8List(0),
                  'dir/層🌿.bin': Uint8List(0),
                  maximumAsciiName: Uint8List(0),
                },
              )
              as OrtBytesModelSource;

      expect(source.externalData.keys, <String>[
        'weights.bin',
        '.hidden',
        'dir/層🌿.bin',
        maximumAsciiName,
      ]);
      expect(source.externalDataByteLength, 0);
    });

    test('rejects malformed, absolute, and traversing names without echo', () {
      final List<String> invalidNames = <String>[
        '',
        '/a',
        'a/',
        'a//b',
        '.',
        '..',
        './a',
        '../private-person-token.bin',
        'a/./b',
        'a/../b',
        r'a\b',
        r'\\server\share',
        '//server/share',
        'C:/a',
        'c:a',
        'dir/name:stream',
        r'C:\a',
        'a\u001f/b',
        'a\u007f/b',
        'a/\u0000/b',
        String.fromCharCode(0xd800),
        List<String>.filled(1025, 'a').join(),
        List<String>.filled(513, 'é').join(),
      ];

      for (final String invalidName in invalidNames) {
        Object? error;
        try {
          OrtModelSource.bytes(
            Uint8List.fromList(<int>[1]),
            externalData: <String, Uint8List>{invalidName: Uint8List(0)},
          );
        } catch (caught) {
          error = caught;
        }
        expect(error, isA<ArgumentError>(), reason: 'name: $invalidName');
        expect(
          error.toString(),
          isNot(contains('../private-person-token.bin')),
        );
      }
    });

    test('accepts at most 256 explicitly enumerated files', () {
      final Map<String, Uint8List> maximumFiles = <String, Uint8List>{
        for (var index = 0; index < 256; index++)
          'file-$index.bin': Uint8List(0),
      };
      final OrtBytesModelSource source =
          OrtModelSource.bytes(
                Uint8List.fromList(<int>[1]),
                externalData: maximumFiles,
              )
              as OrtBytesModelSource;
      expect(source.externalData.length, 256);

      expect(
        () => OrtModelSource.bytes(
          Uint8List.fromList(<int>[1]),
          externalData: <String, Uint8List>{
            ...maximumFiles,
            'file-256.bin': Uint8List(0),
          },
        ),
        throwsRangeError,
      );
    });

    test('bounds the combined model and enumerated external bytes', () {
      final Uint8List aliasedBytes = Uint8List.fromList(<int>[4, 5]);
      final OrtBytesModelSource exact =
          OrtModelSource.bytes(
                Uint8List.fromList(<int>[1, 2]),
                externalData: <String, Uint8List>{
                  'first.bin': aliasedBytes,
                  'second.bin': aliasedBytes,
                },
                limits: OrtResourceLimits(maxModelBytes: 6),
              )
              as OrtBytesModelSource;
      expect(exact.totalByteLength, 6);

      expect(
        () => OrtModelSource.bytes(
          Uint8List.fromList(<int>[1, 2]),
          externalData: <String, Uint8List>{
            'first.bin': Uint8List.fromList(<int>[3, 4]),
            'second.bin': Uint8List.fromList(<int>[5, 6, 7]),
          },
          limits: OrtResourceLimits(maxModelBytes: 6),
        ),
        throwsRangeError,
      );
      expect(
        () => OrtModelSource.bytes(
          Uint8List.fromList(<int>[1]),
          externalData: <String, Uint8List>{
            'first.bin': aliasedBytes,
            'second.bin': aliasedBytes,
          },
          limits: OrtResourceLimits(maxModelBytes: 4),
        ),
        throwsRangeError,
      );
    });

    test('reports an empty external-data map without mutable aliases', () {
      final OrtBytesModelSource source =
          OrtModelSource.bytes(Uint8List.fromList(<int>[1]))
              as OrtBytesModelSource;
      expect(source.hasExternalData, isFalse);
      expect(source.externalData, isEmpty);
      expect(source.externalDataByteLength, 0);
      expect(source.totalByteLength, source.byteLength);
    });
  });
}
