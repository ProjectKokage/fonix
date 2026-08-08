import 'dart:convert';

import 'package:fonix/src/strict_json.dart';
import 'package:test/test.dart';

void main() {
  group('strict JSON scanner', () {
    test('enforces the exact container depth for empty leaves', () {
      for (var maximumDepth = 1; maximumDepth <= 8; maximumDepth += 1) {
        var array = '[]';
        var object = '{}';
        for (var depth = 1; depth < maximumDepth; depth += 1) {
          array = '[$array]';
          object = '{"value":$object}';
        }

        expect(
          () => validateStrictJsonInternal(
            array,
            label: 'Array',
            maximumDepth: maximumDepth,
          ),
          returnsNormally,
        );
        expect(
          () => validateStrictJsonInternal(
            object,
            label: 'Object',
            maximumDepth: maximumDepth,
          ),
          returnsNormally,
        );
        expect(
          () => validateStrictJsonInternal(
            '[$array]',
            label: 'Array',
            maximumDepth: maximumDepth,
          ),
          throwsFormatException,
        );
        expect(
          () => validateStrictJsonInternal(
            '{"value":$object}',
            label: 'Object',
            maximumDepth: maximumDepth,
          ),
          throwsFormatException,
        );
      }
    });

    test('acceptance implies the platform decoder accepts a seeded corpus', () {
      final random = _DeterministicRandom(0x5eedc0de);
      for (var documentIndex = 0; documentIndex < 512; documentIndex += 1) {
        final source = jsonEncode(_jsonValue(random, 0));
        expect(
          () => validateStrictJsonInternal(source, label: 'Seeded JSON'),
          returnsNormally,
          reason: 'generated document $documentIndex',
        );

        for (var mutationIndex = 0; mutationIndex < 8; mutationIndex += 1) {
          final candidate = _mutate(random, source);
          var accepted = false;
          try {
            validateStrictJsonInternal(candidate, label: 'Seeded JSON');
            accepted = true;
          } on FormatException {
            // Rejection is expected for most mutations.
          }
          if (accepted) {
            expect(
              () => jsonDecode(candidate),
              returnsNormally,
              reason:
                  'scanner accepted document $documentIndex mutation '
                  '$mutationIndex: ${jsonEncode(candidate)}',
            );
          }
        }
      }
    });

    test('rejects decoded-equivalent duplicate keys', () {
      for (final source in const <String>[
        '{"key":1,"key":2}',
        '{"key":1,"\\u006bey":2}',
        '{"\\u006bey":1,"key":2}',
      ]) {
        expect(
          () => validateStrictJsonInternal(source, label: 'Duplicate JSON'),
          throwsFormatException,
        );
      }
    });
  });
}

Object? _jsonValue(_DeterministicRandom random, int depth) {
  final maximumKind = depth >= 4 ? 5 : 7;
  switch (random.nextInt(maximumKind)) {
    case 0:
      return null;
    case 1:
      return random.nextInt(2) == 0;
    case 2:
      return random.nextInt(2000001) - 1000000;
    case 3:
      return (random.nextInt(2000001) - 1000000) / 17;
    case 4:
      return _randomString(random);
    case 5:
      return List<Object?>.generate(
        random.nextInt(5),
        (_) => _jsonValue(random, depth + 1),
        growable: false,
      );
    case 6:
      return <String, Object?>{
        for (var index = 0; index < random.nextInt(5); index += 1)
          'key_${depth}_${index}_${random.nextInt(1 << 16)}': _jsonValue(
            random,
            depth + 1,
          ),
      };
    default:
      throw StateError('unreachable seeded JSON kind');
  }
}

String _randomString(_DeterministicRandom random) {
  const alphabet = <String>[
    '',
    'a',
    '0',
    ' ',
    '"',
    '\\',
    '\n',
    '\t',
    'é',
    '木',
    '🙂',
  ];
  return List<String>.generate(
    random.nextInt(8),
    (_) => alphabet[random.nextInt(alphabet.length)],
    growable: false,
  ).join();
}

String _mutate(_DeterministicRandom random, String source) {
  const insertions = <String>[
    ' ',
    ',',
    ':',
    '[',
    ']',
    '{',
    '}',
    '"',
    '\\',
    '0',
    'e',
    'n',
    '\n',
    '\u0000',
  ];
  final index = random.nextInt(source.length + 1);
  switch (random.nextInt(4)) {
    case 0:
      if (source.isEmpty) return insertions[random.nextInt(insertions.length)];
      final deleteIndex = index == source.length ? index - 1 : index;
      return source.replaceRange(deleteIndex, deleteIndex + 1, '');
    case 1:
      return source.replaceRange(
        index,
        index,
        insertions[random.nextInt(insertions.length)],
      );
    case 2:
      if (source.isEmpty) return insertions[random.nextInt(insertions.length)];
      final replaceIndex = index == source.length ? index - 1 : index;
      return source.replaceRange(
        replaceIndex,
        replaceIndex + 1,
        insertions[random.nextInt(insertions.length)],
      );
    case 3:
      if (source.isEmpty) return source;
      final start = random.nextInt(source.length);
      final end = start + random.nextInt(source.length - start + 1);
      return source.replaceRange(index, index, source.substring(start, end));
    default:
      throw StateError('unreachable seeded mutation kind');
  }
}

final class _DeterministicRandom {
  _DeterministicRandom(this._state);

  int _state;

  int nextInt(int maximum) {
    if (maximum <= 0) throw RangeError.value(maximum, 'maximum');
    _state = (_state * 1664525 + 1013904223) & 0xffffffff;
    return _state % maximum;
  }
}
