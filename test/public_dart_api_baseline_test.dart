import 'dart:convert';
import 'dart:io';

import 'package:analyzer/dart/constant/value.dart';
import 'package:analyzer/dart/element/element.dart';
import 'package:analyzer/dart/element/type.dart';
import 'package:test/test.dart';

import '../tool/ci/verify_public_dart_api.dart';

const String _fixturePubspec = '''
name: fonix
version: 0.1.0-dev.1
environment:
  sdk: ^3.11.5
dev_dependencies:
  analyzer: 14.1.0
''';

const String _fixtureLock = '''
packages:
  analyzer:
    dependency: "direct dev"
    description:
      name: analyzer
      sha256: "fixture"
      url: "https://pub.dev"
    source: hosted
    version: "14.1.0"
sdks:
  dart: ">=3.11.5 <4.0.0"
''';

Object _authoritativeSdkSkip() {
  return Platform.version.split(' ').first == publicDartApiDartSdkVersion
      ? false
      : 'The authoritative baseline is intentionally bound to Dart '
            '$publicDartApiDartSdkVersion.';
}

Directory _createFixture(String source) {
  final Directory root = Directory.systemTemp.createTempSync(
    'fonix-public-api-',
  );
  Directory('${root.path}/lib').createSync();
  Directory('${root.path}/.dart_tool').createSync();
  File('${root.path}/pubspec.yaml').writeAsStringSync(_fixturePubspec);
  File('${root.path}/pubspec.lock').writeAsStringSync(_fixtureLock);
  File('${root.path}/lib/fonix.dart').writeAsStringSync(source);
  File('${root.path}/.dart_tool/package_config.json').writeAsStringSync(
    '${jsonEncode(<String, Object?>{
      'configVersion': 2,
      'packages': <Object?>[
        <String, Object?>{'name': 'fonix', 'rootUri': '../', 'packageUri': 'lib/', 'languageVersion': '3.11'},
      ],
    })}\n',
  );
  return root;
}

Map<String, Object?> _contract(Map<String, Object?> baseline) =>
    baseline['contract']! as Map<String, Object?>;

Map<String, Object?> _export(
  Map<String, Object?> baseline,
  String exportedName,
) {
  return (_contract(baseline)['exportedElements']! as List<Object?>)
      .cast<Map<String, Object?>>()
      .singleWhere(
        (Map<String, Object?> record) => record['exportedName'] == exportedName,
      );
}

List<Map<String, Object?>> _objectsWithKind(Object? value, String kind) {
  final List<Map<String, Object?>> result = <Map<String, Object?>>[];
  void visit(Object? current) {
    if (current is Map<String, Object?>) {
      if (current['kind'] == kind) {
        result.add(current);
      }
      for (final Object? child in current.values) {
        visit(child);
      }
    } else if (current is List<Object?>) {
      for (final Object? child in current) {
        visit(child);
      }
    }
  }

  visit(value);
  return result;
}

final class _UnsupportedKnownDartObject implements DartObject {
  @override
  ConstructorInvocation? get constructorInvocation => null;

  @override
  bool get hasKnownValue => true;

  @override
  bool get isNull => false;

  @override
  DartType? get type => null;

  @override
  DartType? get typeNotExtensionTypeErased => null;

  @override
  VariableElement? get variable => null;

  @override
  DartObject? getField(String name) => null;

  @override
  bool? toBoolValue() => null;

  @override
  double? toDoubleValue() => null;

  @override
  ExecutableElement? toFunctionValue() => null;

  @override
  int? toIntValue() => null;

  @override
  List<DartObject>? toListValue() => null;

  @override
  Map<DartObject?, DartObject?>? toMapValue() => null;

  @override
  ({Map<String, DartObject> named, List<DartObject> positional})?
  toRecordValue() => null;

  @override
  Set<DartObject>? toSetValue() => null;

  @override
  String? toStringValue() => null;

  @override
  String? toSymbolValue() => null;

  @override
  DartType? toTypeValue() => null;

  @override
  DartType? toTypeValueNotExtensionTypeErased() => null;
}

void main() {
  test('canonical rendering sorts every object key and ends with one LF', () {
    final String rendered = renderPublicDartApiBaseline(<String, Object?>{
      'z': <String, Object?>{'b': 2, 'a': 1},
      'a': <Object?>[
        <String, Object?>{'d': 4, 'c': 3},
      ],
    });

    expect(
      rendered,
      '{\n'
      '  "a": [\n'
      '    {\n'
      '      "c": 3,\n'
      '      "d": 4\n'
      '    }\n'
      '  ],\n'
      '  "z": {\n'
      '    "a": 1,\n'
      '    "b": 2\n'
      '  }\n'
      '}\n',
    );
  });

  test('baseline comparison rejects tampered bytes', () {
    expect(
      () => verifyPublicDartApiBaselineText(
        committedText: '{"schemaVersion": 2}\n',
        currentText: '{"schemaVersion": 1}\n',
      ),
      throwsA(
        isA<PublicDartApiError>().having(
          (PublicDartApiError error) => error.message,
          'message',
          contains('line 1'),
        ),
      ),
    );
  });

  test('known unsupported annotation constants fail closed', () {
    expect(
      () =>
          encodePublicDartApiConstantForTesting(_UnsupportedKnownDartObject()),
      throwsA(
        isA<PublicDartApiError>().having(
          (PublicDartApiError error) => error.message,
          'message',
          contains('Unsupported known public annotation constant kind'),
        ),
      ),
    );
  });

  test(
    'committed analyzer baseline matches on its authoritative SDK',
    () async {
      final Map<String, Object?> baseline = await verifyPublicDartApiBaseline(
        Directory.current,
      );
      final Map<String, Object?> contract =
          baseline['contract']! as Map<String, Object?>;
      expect(contract['generatorDartSdk'], publicDartApiDartSdkVersion);
      expect(contract['analyzerVersion'], publicDartApiAnalyzerVersion);
      expect(
        (contract['exportedElements']! as List<Object?>).length,
        greaterThan(0),
      );
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'redirecting factory target changes the constructor record',
    () async {
      const String firstTarget = '''
library;

abstract class Api {
  factory Api() = First;
}

final class First implements Api {
  First();
}

final class Second implements Api {
  Second();
}
''';
      final Directory fixture = _createFixture(firstTarget);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstTarget.replaceFirst(
            'factory Api() = First;',
            'factory Api() = Second;',
          ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        final Map<String, Object?> beforeConstructor =
            (_export(before, 'Api')['constructors']! as List<Object?>).single
                as Map<String, Object?>;
        final Map<String, Object?> afterConstructor =
            (_export(after, 'Api')['constructors']! as List<Object?>).single
                as Map<String, Object?>;
        final Map<String, Object?> beforeTarget =
            (beforeConstructor['constructor']!
                    as Map<String, Object?>)['redirectTarget']!
                as Map<String, Object?>;
        final Map<String, Object?> afterTarget =
            (afterConstructor['constructor']!
                    as Map<String, Object?>)['redirectTarget']!
                as Map<String, Object?>;

        expect(
          (beforeTarget['enclosing']! as Map<String, Object?>)['name'],
          'First',
        );
        expect(
          (afterTarget['enclosing']! as Map<String, Object?>)['name'],
          'Second',
        );
        expect(beforeConstructor, isNot(afterConstructor));
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'generic redirect target arguments change the constructor record',
    () async {
      const String firstTarget = '''
library;

import 'a.dart' as a;
import 'b.dart' as b;

abstract class Api {
  factory Api() = _Impl<a.Same>;
}

final class _Impl<T> implements Api {
  _Impl();
}
''';
      final Directory fixture = _createFixture(firstTarget);
      File(
        '${fixture.path}/lib/a.dart',
      ).writeAsStringSync('final class Same {}\n');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Same {}\n');
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstTarget.replaceFirst('_Impl<a.Same>', '_Impl<b.Same>'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> redirectTarget(Map<String, Object?> baseline) {
          final Map<String, Object?> constructor =
              (_export(baseline, 'Api')['constructors']! as List<Object?>)
                      .single
                  as Map<String, Object?>;
          return ((constructor['constructor']!
                  as Map<String, Object?>)['redirectTarget']!)
              as Map<String, Object?>;
        }

        String targetArgumentLibrary(Map<String, Object?> baseline) {
          final Map<String, Object?> returnType =
              redirectTarget(baseline)['returnType']! as Map<String, Object?>;
          final Map<String, Object?> argument =
              (returnType['arguments']! as List<Object?>).single
                  as Map<String, Object?>;
          return (argument['element']! as Map<String, Object?>)['libraryUri']!
              as String;
        }

        expect(
          redirectTarget(before)['signature'],
          redirectTarget(after)['signature'],
        );
        expect(targetArgumentLibrary(before), 'package:fonix/a.dart');
        expect(targetArgumentLibrary(after), 'package:fonix/b.dart');
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'multi-hop private redirect changes the constructor record',
    () async {
      const String firstTarget = '''
library;

abstract class Api {
  factory Api() = _Middle;
}

abstract class _Middle implements Api {
  factory _Middle() = _First;
}

final class _First implements _Middle {
  _First();
}

final class _Second implements _Middle {
  _Second();
}
''';
      final Directory fixture = _createFixture(firstTarget);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstTarget.replaceFirst(
            'factory _Middle() = _First;',
            'factory _Middle() = _Second;',
          ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> terminalRedirect(Map<String, Object?> baseline) {
          final Map<String, Object?> constructor =
              (_export(baseline, 'Api')['constructors']! as List<Object?>)
                      .single
                  as Map<String, Object?>;
          final Map<String, Object?> firstHop =
              (constructor['constructor']!
                      as Map<String, Object?>)['redirectTarget']!
                  as Map<String, Object?>;
          return firstHop['redirectTarget']! as Map<String, Object?>;
        }

        expect(
          (terminalRedirect(before)['enclosing']!
              as Map<String, Object?>)['name'],
          '_First',
        );
        expect(
          (terminalRedirect(after)['enclosing']!
              as Map<String, Object?>)['name'],
          '_Second',
        );
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'constructor tear-off default retains its private redirect chain',
    () async {
      const String firstTarget = '''
library;

abstract class _Middle {
  factory _Middle() = _First;
}

final class _First implements _Middle {
  _First();
}

final class _Second implements _Middle {
  _Second();
}

Object api([Object callback = _Middle.new]) => callback;
''';
      final Directory fixture = _createFixture(firstTarget);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstTarget.replaceFirst(
            'factory _Middle() = _First;',
            'factory _Middle() = _Second;',
          ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> redirectTarget(Map<String, Object?> baseline) {
          final Map<String, Object?> defaultValue =
              (((_export(baseline, 'api')['executable']!
                                  as Map<String, Object?>)['parameters']!
                              as List<Object?>)
                          .single
                      as Map<String, Object?>)['defaultValue']!
                  as Map<String, Object?>;
          final Map<String, Object?> function =
              defaultValue['value']! as Map<String, Object?>;
          final Map<String, Object?> constructor =
              function['value']! as Map<String, Object?>;
          return constructor['redirectTarget']! as Map<String, Object?>;
        }

        expect(
          (redirectTarget(before)['enclosing']!
              as Map<String, Object?>)['name'],
          '_First',
        );
        expect(
          (redirectTarget(after)['enclosing']! as Map<String, Object?>)['name'],
          '_Second',
        );
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'constructor tear-off defaults with hidden parameter defaults fail closed',
    () async {
      const String firstDefault = '''
library;

const int _hidden = 1;

final class _Callback {
  _Callback([int value = _hidden]);
}

Object api([Object callback = _Callback.new]) => callback;
''';
      final Directory fixture = _createFixture(firstDefault);
      try {
        for (final String source in <String>[
          firstDefault,
          firstDefault.replaceFirst('_hidden = 1', '_hidden = 2'),
        ]) {
          File('${fixture.path}/lib/fonix.dart').writeAsStringSync(source);
          await expectLater(
            buildPublicDartApiBaseline(fixture),
            throwsA(
              isA<PublicDartApiError>().having(
                (PublicDartApiError error) => error.message,
                'message',
                contains(
                  'Constructor tear-off constants with explicit parameter '
                  'defaults',
                ),
              ),
            ),
          );
        }
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'same-named parameter types from different libraries change the record',
    () async {
      const String firstParameterType = '''
library;

import 'a.dart' as a;
import 'b.dart' as b;

void api(a.Same value) {}
''';
      final Directory fixture = _createFixture(firstParameterType);
      File(
        '${fixture.path}/lib/a.dart',
      ).writeAsStringSync('final class Same {}\n');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Same {}\n');
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstParameterType.replaceFirst('a.Same value', 'b.Same value'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        final Map<String, Object?> beforeApi = _export(before, 'api');
        final Map<String, Object?> afterApi = _export(after, 'api');
        final Map<String, Object?> beforeParameter =
            ((beforeApi['executable']! as Map<String, Object?>)['parameters']!
                        as List<Object?>)
                    .single
                as Map<String, Object?>;
        final Map<String, Object?> afterParameter =
            ((afterApi['executable']! as Map<String, Object?>)['parameters']!
                        as List<Object?>)
                    .single
                as Map<String, Object?>;

        expect(beforeApi['signature'], afterApi['signature']);
        expect(
          ((beforeParameter['type']! as Map<String, Object?>)['element']!
              as Map<String, Object?>)['libraryUri'],
          'package:fonix/a.dart',
        );
        expect(
          ((afterParameter['type']! as Map<String, Object?>)['element']!
              as Map<String, Object?>)['libraryUri'],
          'package:fonix/b.dart',
        );
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    're-exporting an identical declaration from another library changes identity',
    () async {
      final Directory fixture = _createFixture("export 'a.dart';\n");
      File(
        '${fixture.path}/lib/a.dart',
      ).writeAsStringSync('final class Api {}\n');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Api {}\n');
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File(
          '${fixture.path}/lib/fonix.dart',
        ).writeAsStringSync("export 'b.dart';\n");
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        final Map<String, Object?> beforeApi = _export(before, 'Api');
        final Map<String, Object?> afterApi = _export(after, 'Api');
        expect(beforeApi['signature'], afterApi['signature']);
        expect(
          (beforeApi['identity']! as Map<String, Object?>)['libraryUri'],
          'package:fonix/a.dart',
        );
        expect(
          (afterApi['identity']! as Map<String, Object?>)['libraryUri'],
          'package:fonix/b.dart',
        );
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'private const-backed public const changes its evaluated value',
    () async {
      const String firstValue = '''
library;

const int _hidden = 2;
const int publicValue = _hidden;
''';
      final Directory fixture = _createFixture(firstValue);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstValue.replaceFirst('_hidden = 2', '_hidden = 3'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> variable(Map<String, Object?> baseline) =>
            ((_export(baseline, 'publicValue')['property']!
                    as Map<String, Object?>)['variable']!)
                as Map<String, Object?>;
        expect(
          variable(before)['constantInitializer'],
          variable(after)['constantInitializer'],
        );
        expect(jsonEncode(variable(before)['constantValue']), contains('2'));
        expect(jsonEncode(variable(after)['constantValue']), contains('3'));
        expect(
          variable(before)['constantValue'],
          isNot(variable(after)['constantValue']),
        );
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'private const-backed optional default changes its evaluated value',
    () async {
      const String firstValue = '''
library;

const int _hidden = 2;
void api([int value = _hidden]) {}
''';
      final Directory fixture = _createFixture(firstValue);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstValue.replaceFirst('_hidden = 2', '_hidden = 3'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> defaultValue(Map<String, Object?> baseline) {
          final Map<String, Object?> executable =
              _export(baseline, 'api')['executable']! as Map<String, Object?>;
          final Map<String, Object?> parameter =
              (executable['parameters']! as List<Object?>).single
                  as Map<String, Object?>;
          return parameter['defaultValue']! as Map<String, Object?>;
        }

        expect(defaultValue(before)['source'], defaultValue(after)['source']);
        expect(
          jsonEncode(defaultValue(before)['value']),
          contains('"value":2'),
        );
        expect(jsonEncode(defaultValue(after)['value']), contains('"value":3'));
        expect(
          defaultValue(before)['value'],
          isNot(defaultValue(after)['value']),
        );
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'private typedef retarget changes an annotation type value',
    () async {
      const String firstAlias = '''
library;

import 'a.dart' as a;
import 'b.dart' as b;

typedef _Alias = a.Same;

final class Tag {
  const Tag(this.value);

  final Type value;
}

@Tag(_Alias)
final class Api {}
''';
      final Directory fixture = _createFixture(firstAlias);
      File(
        '${fixture.path}/lib/a.dart',
      ).writeAsStringSync('final class Same {}\n');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Same {}\n');
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstAlias.replaceFirst(
            'typedef _Alias = a.Same;',
            'typedef _Alias = b.Same;',
          ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        final Map<String, Object?> beforeAnnotation =
            (_export(before, 'Api')['annotations']! as List<Object?>).single
                as Map<String, Object?>;
        final Map<String, Object?> afterAnnotation =
            (_export(after, 'Api')['annotations']! as List<Object?>).single
                as Map<String, Object?>;
        final Map<String, Object?> beforeAlias = _objectsWithKind(
          beforeAnnotation,
          'alias',
        ).first;
        final Map<String, Object?> afterAlias = _objectsWithKind(
          afterAnnotation,
          'alias',
        ).first;
        expect(beforeAnnotation['source'], afterAnnotation['source']);
        expect(
          ((beforeAlias['instantiatedTarget']!
                  as Map<String, Object?>)['element']!
              as Map<String, Object?>)['libraryUri'],
          'package:fonix/a.dart',
        );
        expect(
          ((afterAlias['instantiatedTarget']!
                  as Map<String, Object?>)['element']!
              as Map<String, Object?>)['libraryUri'],
          'package:fonix/b.dart',
        );
        expect(beforeAnnotation['value'], isNot(afterAnnotation['value']));
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'private extension-type constant retarget preserves nominal identity',
    () async {
      const String firstType = '''
library;

import 'a.dart' as a;
import 'b.dart' as b;

const Type _hidden = a.Same;

final class Tag {
  const Tag(this.value);

  final Type value;
}

@Tag(_hidden)
final class Api {}
''';
      final Directory fixture = _createFixture(firstType);
      File(
        '${fixture.path}/lib/a.dart',
      ).writeAsStringSync('extension type Same(int value) {}\n');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('extension type Same(int value) {}\n');
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstType.replaceFirst(
            'const Type _hidden = a.Same;',
            'const Type _hidden = b.Same;',
          ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        final Map<String, Object?> beforeAnnotation =
            (_export(before, 'Api')['annotations']! as List<Object?>).single
                as Map<String, Object?>;
        final Map<String, Object?> afterAnnotation =
            (_export(after, 'Api')['annotations']! as List<Object?>).single
                as Map<String, Object?>;

        Set<String> extensionTypeLibraries(Map<String, Object?> annotation) {
          return <String>{
            for (final Map<String, Object?> typeValue in _objectsWithKind(
              annotation,
              'type',
            ))
              if ((typeValue['value']! as Map<String, Object?>)['element']
                  case final Map<String, Object?> element
                  when element['name'] == 'Same')
                element['libraryUri']! as String,
          };
        }

        expect(beforeAnnotation['source'], afterAnnotation['source']);
        expect(extensionTypeLibraries(beforeAnnotation), <String>{
          'package:fonix/a.dart',
        });
        expect(extensionTypeLibraries(afterAnnotation), <String>{
          'package:fonix/b.dart',
        });
        expect(beforeAnnotation['value'], isNot(afterAnnotation['value']));
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'private extension-type representation drift changes a type constant',
    () async {
      const String intRepresentation = '''
library;

extension type _Hidden(int value) {}

const Type _hidden = _Hidden;

final class Tag {
  const Tag(this.value);

  final Type value;
}

@Tag(_hidden)
final class Api {}
''';
      final Directory fixture = _createFixture(intRepresentation);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          intRepresentation.replaceFirst(
            '_Hidden(int value)',
            '_Hidden(String value)',
          ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> typeValue(Map<String, Object?> baseline) {
          final Map<String, Object?> annotation =
              (_export(baseline, 'Api')['annotations']! as List<Object?>).single
                  as Map<String, Object?>;
          return _objectsWithKind(annotation['value'], 'type').firstWhere((
            Map<String, Object?> value,
          ) {
            final Map<String, Object?> nominal =
                value['value']! as Map<String, Object?>;
            final Map<String, Object?> element =
                nominal['element']! as Map<String, Object?>;
            return element['name'] == '_Hidden';
          });
        }

        String erasedName(Map<String, Object?> baseline) {
          final Map<String, Object?> erased =
              typeValue(baseline)['erasedValue']! as Map<String, Object?>;
          return (erased['element']! as Map<String, Object?>)['name']!
              as String;
        }

        Map<String, Object?> nominalElement(Map<String, Object?> baseline) =>
            ((typeValue(baseline)['value']!
                    as Map<String, Object?>)['element']!)
                as Map<String, Object?>;
        expect(nominalElement(before), nominalElement(after));
        expect(erasedName(before), 'int');
        expect(erasedName(after), 'String');
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'annotation constructor parameter identity changes behind a null argument',
    () async {
      const String firstType = '''
library;

import 'a.dart' as a;
import 'b.dart' as b;

final class _Tag {
  const _Tag(a.Same? ignored);
}

@_Tag(null)
final class Api {}
''';
      final Directory fixture = _createFixture(firstType);
      File(
        '${fixture.path}/lib/a.dart',
      ).writeAsStringSync('final class Same {}\n');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Same {}\n');
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstType.replaceFirst('a.Same? ignored', 'b.Same? ignored'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> targetParameter(Map<String, Object?> baseline) {
          final Map<String, Object?> annotation =
              (_export(baseline, 'Api')['annotations']! as List<Object?>).single
                  as Map<String, Object?>;
          final Map<String, Object?> value =
              annotation['value']! as Map<String, Object?>;
          final Map<String, Object?> constructor =
              value['constructor']! as Map<String, Object?>;
          return (constructor['parameters']! as List<Object?>).single
              as Map<String, Object?>;
        }

        String parameterLibrary(Map<String, Object?> baseline) {
          final Map<String, Object?> type =
              targetParameter(baseline)['type']! as Map<String, Object?>;
          return (type['element']! as Map<String, Object?>)['libraryUri']!
              as String;
        }

        expect(parameterLibrary(before), 'package:fonix/a.dart');
        expect(parameterLibrary(after), 'package:fonix/b.dart');
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test('function type parameter metadata fails closed', () async {
    const String source = '''
library;

const int _hidden = 1;

final class _Tag {
  const _Tag(this.value);

  final int value;
}

typedef Api = void Function(@_Tag(_hidden) int value);
''';
    final Directory fixture = _createFixture(source);
    try {
      await expectLater(
        buildPublicDartApiBaseline(fixture),
        throwsA(
          isA<PublicDartApiError>().having(
            (PublicDartApiError error) => error.message,
            'message',
            contains('Function-type formal/type-parameter metadata'),
          ),
        ),
      );
    } finally {
      fixture.deleteSync(recursive: true);
    }
  }, skip: _authoritativeSdkSkip());

  test(
    'function type type-parameter metadata fails closed',
    () async {
      const String source = '''
library;

final class _Tag {
  const _Tag();
}

typedef Api = void Function<@_Tag() T>(T value);
''';
      final Directory fixture = _createFixture(source);
      try {
        await expectLater(
          buildPublicDartApiBaseline(fixture),
          throwsA(
            isA<PublicDartApiError>().having(
              (PublicDartApiError error) => error.message,
              'message',
              contains('Function-type formal/type-parameter metadata'),
            ),
          ),
        );
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'nested function type metadata fails closed before values are suppressed',
    () async {
      const String source = '''
library;

const int _hidden = 1;

final class _Other {
  const _Other(this.value);

  final int value;
}

final class _Tag {
  const _Tag(this.value);

  final Type value;
}

typedef _Inner = void Function(@_Other(_hidden) int value);
typedef Api = void Function(@_Tag(_Inner) int value);
''';
      final Directory fixture = _createFixture(source);
      try {
        await expectLater(
          buildPublicDartApiBaseline(fixture),
          throwsA(
            isA<PublicDartApiError>().having(
              (PublicDartApiError error) => error.message,
              'message',
              contains('Function-type formal/type-parameter metadata'),
            ),
          ),
        );
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'hidden generic function tear-off defaults fail closed',
    () async {
      const String firstInstantiation = '''
library;

Type reveal<T>() => T;

const Object _hidden = reveal<int>;

Object api([Object callback = _hidden]) => callback;
''';
      final Directory fixture = _createFixture(firstInstantiation);
      try {
        for (final String source in <String>[
          firstInstantiation,
          firstInstantiation.replaceFirst('reveal<int>', 'reveal<String>'),
        ]) {
          File('${fixture.path}/lib/fonix.dart').writeAsStringSync(source);
          await expectLater(
            buildPublicDartApiBaseline(fixture),
            throwsA(
              isA<PublicDartApiError>().having(
                (PublicDartApiError error) => error.message,
                'message',
                contains('Generic function tear-off constants'),
              ),
            ),
          );
        }
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'analysis errors in an exported defining library fail closed',
    () async {
      final Directory fixture = _createFixture("export 'a.dart';\n");
      File('${fixture.path}/lib/a.dart').writeAsStringSync('''
final class Api {}
int _broken() => missingName;
''');
      try {
        await expectLater(
          buildPublicDartApiBaseline(fixture),
          throwsA(
            isA<PublicDartApiError>().having(
              (PublicDartApiError error) => error.message,
              'message',
              contains(
                'Public export library package:fonix/a.dart has analysis errors',
              ),
            ),
          ),
        );
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'analysis errors in an intermediate exported library fail closed',
    () async {
      final Directory fixture = _createFixture("export 'a.dart' show Api;\n");
      File('${fixture.path}/lib/a.dart').writeAsStringSync('''
export 'b.dart' show Api;

int _broken() => missingName;
''');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Api {}\n');
      try {
        await expectLater(
          buildPublicDartApiBaseline(fixture),
          throwsA(
            isA<PublicDartApiError>().having(
              (PublicDartApiError error) => error.message,
              'message',
              contains(
                'Public export library package:fonix/a.dart has analysis errors',
              ),
            ),
          ),
        );
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'intermediate exported library annotation drift changes the contract',
    () async {
      final Directory fixture = _createFixture("export 'a.dart' show Api;\n");
      const String firstAnnotation = '''
@Deprecated('old')
library;

export 'b.dart' show Api;
''';
      File('${fixture.path}/lib/a.dart').writeAsStringSync(firstAnnotation);
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Api {}\n');
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/a.dart').writeAsStringSync(
          firstAnnotation.replaceFirst(
            "Deprecated('old')",
            "Deprecated('new')",
          ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> libraryRecord(Map<String, Object?> baseline) {
          return (_contract(baseline)['exportedLibraries']! as List<Object?>)
              .cast<Map<String, Object?>>()
              .singleWhere((Map<String, Object?> library) {
                final Map<String, Object?> identity =
                    library['identity']! as Map<String, Object?>;
                return identity['libraryUri'] == 'package:fonix/a.dart';
              });
        }

        expect(
          libraryRecord(before)['annotations'],
          isNot(libraryRecord(after)['annotations']),
        );
        expect(
          jsonEncode(libraryRecord(before)['annotations']),
          contains('old'),
        );
        expect(
          jsonEncode(libraryRecord(after)['annotations']),
          contains('new'),
        );
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'library, declaration, and parameter annotation drift changes the record',
    () async {
      const String oldAnnotations = '''
@Deprecated('library-old')
library;

final class ApiTag {
  const ApiTag(this.value);

  final String value;
}

@ApiTag('type-old')
abstract class AnnotatedApi {
  @ApiTag('method-old')
  void run(@ApiTag('parameter-old') int value);
}
''';
      final Directory fixture = _createFixture(oldAnnotations);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        final List<Object?> libraryAnnotations =
            _contract(before)['libraryAnnotations']! as List<Object?>;
        final Map<String, Object?> libraryAnnotation =
            libraryAnnotations.single as Map<String, Object?>;
        expect(jsonEncode(libraryAnnotation['value']), contains('library-old'));
        expect(
          jsonEncode(_export(before, 'AnnotatedApi')['annotations']),
          contains('type-old'),
        );
        final Map<String, Object?> beforeMethod =
            (_export(before, 'AnnotatedApi')['methods']! as List<Object?>)
                    .single
                as Map<String, Object?>;
        expect(jsonEncode(beforeMethod['annotations']), contains('method-old'));
        expect(
          jsonEncode(beforeMethod['parameterAnnotations']),
          contains('parameter-old'),
        );

        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          oldAnnotations.replaceFirst('library-old', 'library-new'),
        );
        final Map<String, Object?> libraryAfter =
            await buildPublicDartApiBaseline(fixture);
        expect(
          _contract(before)['libraryAnnotations'],
          isNot(_contract(libraryAfter)['libraryAnnotations']),
        );
        expect(before['contractSha256'], isNot(libraryAfter['contractSha256']));

        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          oldAnnotations.replaceFirst('type-old', 'type-new'),
        );
        final Map<String, Object?> typeAfter = await buildPublicDartApiBaseline(
          fixture,
        );
        expect(
          _export(before, 'AnnotatedApi')['annotations'],
          isNot(_export(typeAfter, 'AnnotatedApi')['annotations']),
        );

        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          oldAnnotations.replaceFirst('method-old', 'method-new'),
        );
        final Map<String, Object?> methodAfter =
            await buildPublicDartApiBaseline(fixture);
        final Map<String, Object?> changedMethod =
            (_export(methodAfter, 'AnnotatedApi')['methods']! as List<Object?>)
                    .single
                as Map<String, Object?>;
        expect(
          beforeMethod['annotations'],
          isNot(changedMethod['annotations']),
        );

        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          oldAnnotations.replaceFirst('parameter-old', 'parameter-new'),
        );
        final Map<String, Object?> parameterAfter =
            await buildPublicDartApiBaseline(fixture);
        final Map<String, Object?> changedParameterMethod =
            (_export(parameterAfter, 'AnnotatedApi')['methods']!
                        as List<Object?>)
                    .single
                as Map<String, Object?>;
        expect(
          beforeMethod['parameterAnnotations'],
          isNot(changedParameterMethod['parameterAnnotations']),
        );
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'constructor-derived annotation field drift changes the record',
    () async {
      const String doubledField = '''
library;

final class DerivedTag {
  const DerivedTag(int input) : value = input * 2;

  final int value;
}

@DerivedTag(3)
final class Api {}
''';
      final Directory fixture = _createFixture(doubledField);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          doubledField.replaceFirst('input * 2', 'input * 3'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        final Map<String, Object?> beforeAnnotation =
            (_export(before, 'Api')['annotations']! as List<Object?>).single
                as Map<String, Object?>;
        final Map<String, Object?> afterAnnotation =
            (_export(after, 'Api')['annotations']! as List<Object?>).single
                as Map<String, Object?>;
        final Map<String, Object?> beforeValue =
            beforeAnnotation['value']! as Map<String, Object?>;
        final Map<String, Object?> afterValue =
            afterAnnotation['value']! as Map<String, Object?>;

        expect(beforeAnnotation['source'], afterAnnotation['source']);
        expect(
          beforeValue['positionalArguments'],
          afterValue['positionalArguments'],
        );
        expect(beforeValue['fields'], isNot(afterValue['fields']));
        expect(jsonEncode(beforeValue['fields']), contains('"value":6'));
        expect(jsonEncode(afterValue['fields']), contains('"value":9'));
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'superclass-derived annotation field drift changes the record',
    () async {
      const String doubledField = '''
library;

class BaseTag {
  const BaseTag(int input) : baseValue = input * 2;

  final int baseValue;
}

final class DerivedTag extends BaseTag {
  const DerivedTag(int input) : super(input);
}

@DerivedTag(3)
final class Api {}
''';
      final Directory fixture = _createFixture(doubledField);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          doubledField.replaceFirst('input * 2', 'input * 3'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        final Map<String, Object?> beforeValue =
            (((_export(before, 'Api')['annotations']! as List<Object?>).single
                    as Map<String, Object?>)['value']!)
                as Map<String, Object?>;
        final Map<String, Object?> afterValue =
            (((_export(after, 'Api')['annotations']! as List<Object?>).single
                    as Map<String, Object?>)['value']!)
                as Map<String, Object?>;
        final List<Object?> beforeFields =
            beforeValue['fields']! as List<Object?>;
        final List<Object?> afterFields =
            afterValue['fields']! as List<Object?>;

        expect(
          beforeValue['positionalArguments'],
          afterValue['positionalArguments'],
        );
        expect(beforeFields, hasLength(1));
        expect(afterFields, hasLength(1));
        expect(jsonEncode(beforeFields), contains('"baseValue"'));
        expect(jsonEncode(beforeFields), contains('"value":6'));
        expect(jsonEncode(afterFields), contains('"value":9'));
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'set and map constants preserve evaluated iteration order',
    () async {
      const String firstOrder = '''
library;

const Set<int> _setValue = <int>{1, 2};
const Map<String, int> _mapValue = <String, int>{'a': 1, 'b': 2};

final class _Tag {
  const _Tag(this.setValue, this.mapValue);

  final Set<int> setValue;
  final Map<String, int> mapValue;
}

@_Tag(_setValue, _mapValue)
final class Api {}
''';
      final Directory fixture = _createFixture(firstOrder);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstOrder
              .replaceFirst('<int>{1, 2}', '<int>{2, 1}')
              .replaceFirst(
                "<String, int>{'a': 1, 'b': 2}",
                "<String, int>{'b': 2, 'a': 1}",
              ),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> annotationValue(Map<String, Object?> baseline) {
          final Map<String, Object?> annotation =
              (_export(baseline, 'Api')['annotations']! as List<Object?>).single
                  as Map<String, Object?>;
          return annotation['value']! as Map<String, Object?>;
        }

        List<Object?> scalarValues(Map<String, Object?> constant) =>
            (constant['values']! as List<Object?>)
                .cast<Map<String, Object?>>()
                .map((Map<String, Object?> value) => value['value'])
                .toList();

        List<Object?> mapKeys(Map<String, Object?> constant) =>
            (constant['entries']! as List<Object?>)
                .cast<Map<String, Object?>>()
                .map(
                  (Map<String, Object?> entry) =>
                      (entry['key']! as Map<String, Object?>)['value'],
                )
                .toList();

        final Map<String, Object?> beforeSet = _objectsWithKind(
          annotationValue(before),
          'set',
        ).first;
        final Map<String, Object?> afterSet = _objectsWithKind(
          annotationValue(after),
          'set',
        ).first;
        final Map<String, Object?> beforeMap = _objectsWithKind(
          annotationValue(before),
          'map',
        ).first;
        final Map<String, Object?> afterMap = _objectsWithKind(
          annotationValue(after),
          'map',
        ).first;

        expect(scalarValues(beforeSet), <Object?>[1, 2]);
        expect(scalarValues(afterSet), <Object?>[2, 1]);
        expect(mapKeys(beforeMap), <Object?>['a', 'b']);
        expect(mapKeys(afterMap), <Object?>['b', 'a']);
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'double constants preserve the raw IEEE-754 sign bit for NaN',
    () async {
      const String positiveNan = '''
library;

const double _hidden = double.nan;

final class _Tag {
  const _Tag(this.value);

  final double value;
}

@_Tag(_hidden)
final class Api {}
''';
      final Directory fixture = _createFixture(positiveNan);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          positiveNan.replaceFirst('= double.nan', '= -double.nan'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        Map<String, Object?> doubleValue(Map<String, Object?> baseline) {
          final Map<String, Object?> annotation =
              (_export(baseline, 'Api')['annotations']! as List<Object?>).single
                  as Map<String, Object?>;
          return _objectsWithKind(annotation['value'], 'double').first;
        }

        expect(doubleValue(before)['value'], 'NaN');
        expect(doubleValue(after)['value'], 'NaN');
        expect(doubleValue(before)['bits'], startsWith('7'));
        expect(doubleValue(after)['bits'], startsWith('f'));
        expect(doubleValue(before)['bits'], isNot(doubleValue(after)['bits']));
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'enum constant ordinal changes when a private enum is reordered',
    () async {
      const String firstOrder = '''
library;

enum _Choice { first, second }

final class _Tag {
  const _Tag(this.value);

  final _Choice value;
}

@_Tag(_Choice.first)
final class Api {}
''';
      final Directory fixture = _createFixture(firstOrder);
      try {
        final Map<String, Object?> before = await buildPublicDartApiBaseline(
          fixture,
        );
        File('${fixture.path}/lib/fonix.dart').writeAsStringSync(
          firstOrder.replaceFirst('{ first, second }', '{ second, first }'),
        );
        final Map<String, Object?> after = await buildPublicDartApiBaseline(
          fixture,
        );

        int ordinal(Map<String, Object?> baseline) {
          final Map<String, Object?> annotation =
              (_export(baseline, 'Api')['annotations']! as List<Object?>).single
                  as Map<String, Object?>;
          return _objectsWithKind(annotation['value'], 'enum').first['ordinal']!
              as int;
        }

        expect(ordinal(before), 0);
        expect(ordinal(after), 1);
        expect(before['contractSha256'], isNot(after['contractSha256']));
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test('private Symbol constants fail closed', () async {
    const String source = '''
library;

const Symbol _hidden = #_privateName;

final class _Tag {
  const _Tag(this.value);

  final Symbol value;
}

@_Tag(_hidden)
final class Api {}
''';
    final Directory fixture = _createFixture(source);
    try {
      await expectLater(
        buildPublicDartApiBaseline(fixture),
        throwsA(
          isA<PublicDartApiError>().having(
            (PublicDartApiError error) => error.message,
            'message',
            contains('Private Symbol constants'),
          ),
        ),
      );
    } finally {
      fixture.deleteSync(recursive: true);
    }
  }, skip: _authoritativeSdkSkip());

  test(
    'environment constants in private imported sources fail closed',
    () async {
      final Directory fixture = _createFixture(
        "export 'a.dart' show publicValue;\n",
      );
      File('${fixture.path}/lib/a.dart').writeAsStringSync('''
import 'c.dart' as c;

const int publicValue = c.hidden;
''');
      File('${fixture.path}/lib/c.dart').writeAsStringSync('''
const int hidden = int.fromEnvironment('FIRST_KEY');
''');
      try {
        await expectLater(
          buildPublicDartApiBaseline(fixture),
          throwsA(
            isA<PublicDartApiError>().having(
              (PublicDartApiError error) => error.message,
              'message',
              contains('environment-dependent constant invocation'),
            ),
          ),
        );
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test('bool.hasEnvironment fails closed', () async {
    const String source = '''
library;

const bool publicValue = bool.hasEnvironment('FEATURE');
''';
    final Directory fixture = _createFixture(source);
    try {
      await expectLater(
        buildPublicDartApiBaseline(fixture),
        throwsA(
          isA<PublicDartApiError>().having(
            (PublicDartApiError error) => error.message,
            'message',
            contains('environment-dependent constant invocation'),
          ),
        ),
      );
    } finally {
      fixture.deleteSync(recursive: true);
    }
  }, skip: _authoritativeSdkSkip());

  test(
    'dot-shorthand environment constructors fail closed',
    () async {
      const List<String> sources = <String>[
        "const int publicValue = .fromEnvironment('FEATURE');\n",
        "const bool publicValue = .hasEnvironment('FEATURE');\n",
      ];
      for (final String source in sources) {
        final Directory fixture = _createFixture(source);
        try {
          await expectLater(
            buildPublicDartApiBaseline(fixture),
            throwsA(
              isA<PublicDartApiError>().having(
                (PublicDartApiError error) => error.message,
                'message',
                contains('environment-dependent constant invocation'),
              ),
            ),
          );
        } finally {
          fixture.deleteSync(recursive: true);
        }
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'same-named local constructors are not environment constants',
    () async {
      const String source = '''
library;

final class Local {
  const Local.fromEnvironment(String name);
}

const Local publicValue = Local.fromEnvironment('FEATURE');
''';
      final Directory fixture = _createFixture(source);
      try {
        final Map<String, Object?> baseline = await buildPublicDartApiBaseline(
          fixture,
        );
        expect(_export(baseline, 'publicValue'), isNotEmpty);
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test(
    'conditional import and export configurations fail closed',
    () async {
      const List<String> sources = <String>[
        "export 'a.dart' if (dart.library.html) 'b.dart';\n",
        "import 'a.dart' if (dart.library.html) 'b.dart';\n"
            'final class Api {}\n',
      ];
      for (final String source in sources) {
        final Directory fixture = _createFixture(source);
        File(
          '${fixture.path}/lib/a.dart',
        ).writeAsStringSync('final class Api {}\n');
        File(
          '${fixture.path}/lib/b.dart',
        ).writeAsStringSync('final class Api {}\n');
        try {
          await expectLater(
            buildPublicDartApiBaseline(fixture),
            throwsA(
              isA<PublicDartApiError>().having(
                (PublicDartApiError error) => error.message,
                'message',
                contains('Conditional import/export configurations'),
              ),
            ),
          );
        } finally {
          fixture.deleteSync(recursive: true);
        }
      }
    },
    skip: _authoritativeSdkSkip(),
  );

  test('links below lib fail closed', () async {
    final Directory fixture = _createFixture('final class Api {}\n');
    Link(
      '${fixture.path}/lib/linked.dart',
    ).createSync('${fixture.path}/lib/fonix.dart');
    try {
      await expectLater(
        buildPublicDartApiBaseline(fixture),
        throwsA(
          isA<PublicDartApiError>().having(
            (PublicDartApiError error) => error.message,
            'message',
            contains('must not contain filesystem links'),
          ),
        ),
      );
    } finally {
      fixture.deleteSync(recursive: true);
    }
  }, skip: _authoritativeSdkSkip());

  test(
    'closed annotation values preserve every supported kind and type identity',
    () async {
      const String source = '''
library;

import 'a.dart' as a;
import 'b.dart' as b;

enum Choice { one }

void callback() {}

final class ValuesTag {
  const ValuesTag({
    required this.choice,
    required this.symbol,
    required this.callbackValue,
    required this.listValue,
    required this.setValue,
    required this.mapValue,
    required this.recordValue,
    required this.firstType,
    required this.secondType,
    required this.firstNestedType,
    required this.secondNestedType,
  });

  final Choice choice;
  final Symbol symbol;
  final void Function() callbackValue;
  final List<int> listValue;
  final Set<String> setValue;
  final Map<String, int> mapValue;
  final ({int count, String label}) recordValue;
  final Type firstType;
  final Type secondType;
  final Type firstNestedType;
  final Type secondNestedType;
}

@ValuesTag(
  choice: Choice.one,
  symbol: #token,
  callbackValue: callback,
  listValue: <int>[1, 2],
  setValue: <String>{'beta', 'alpha'},
  mapValue: <String, int>{'one': 1, 'two': 2},
  recordValue: (count: 2, label: 'record'),
  firstType: a.Same,
  secondType: b.Same,
  firstNestedType: List<a.Same>,
  secondNestedType: List<b.Same>,
)
final class Api {}
''';
      final Directory fixture = _createFixture(source);
      File(
        '${fixture.path}/lib/a.dart',
      ).writeAsStringSync('final class Same {}\n');
      File(
        '${fixture.path}/lib/b.dart',
      ).writeAsStringSync('final class Same {}\n');
      try {
        final Map<String, Object?> baseline = await buildPublicDartApiBaseline(
          fixture,
        );
        final Map<String, Object?> annotation =
            (_export(baseline, 'Api')['annotations']! as List<Object?>).single
                as Map<String, Object?>;
        final Object? value = annotation['value'];
        for (final String kind in <String>{
          'enum',
          'symbol',
          'function',
          'list',
          'set',
          'map',
          'record',
          'type',
        }) {
          expect(_objectsWithKind(value, kind), isNotEmpty, reason: kind);
        }

        final List<Map<String, Object?>> typeValues = _objectsWithKind(
          value,
          'type',
        );
        final Set<String> typeLibraries = <String>{
          for (final Map<String, Object?> typeValue in typeValues)
            ((((typeValue['value']! as Map<String, Object?>)['element']!
                    as Map<String, Object?>)['libraryUri'])
                as String),
        };
        expect(
          typeLibraries,
          containsAll(<String>{'package:fonix/a.dart', 'package:fonix/b.dart'}),
        );
        expect(<Object?>{
          for (final Map<String, Object?> typeValue in typeValues)
            (typeValue['value']! as Map<String, Object?>)['signature'],
        }, containsAll(<Object?>{'Same', 'List<Same>'}));

        final List<Map<String, Object?>> listTypes =
            _objectsWithKind(value, 'interface').where((
              Map<String, Object?> typeValue,
            ) {
              final Map<String, Object?> element =
                  typeValue['element']! as Map<String, Object?>;
              if (element['name'] != 'List') {
                return false;
              }
              final List<Object?> arguments =
                  typeValue['arguments']! as List<Object?>;
              if (arguments.length != 1) {
                return false;
              }
              final Map<String, Object?> argument =
                  arguments.single as Map<String, Object?>;
              final Map<String, Object?> argumentElement =
                  argument['element']! as Map<String, Object?>;
              return argumentElement['name'] == 'Same';
            }).toList();
        expect(<String>{
          for (final Map<String, Object?> type in listTypes) jsonEncode(type),
        }, hasLength(2));
        final Set<String> nestedArgumentLibraries = <String>{
          for (final Map<String, Object?> listType in listTypes)
            (((listType['arguments']! as List<Object?>).single
                        as Map<String, Object?>)['element']!
                    as Map<String, Object?>)['libraryUri']!
                as String,
        };
        expect(nestedArgumentLibraries, <String>{
          'package:fonix/a.dart',
          'package:fonix/b.dart',
        });
        Map<String, Object?> listTypeFor(String libraryUri) {
          return listTypes.firstWhere((Map<String, Object?> listType) {
            final Map<String, Object?> argument =
                (listType['arguments']! as List<Object?>).single
                    as Map<String, Object?>;
            return (argument['element']!
                    as Map<String, Object?>)['libraryUri'] ==
                libraryUri;
          });
        }

        expect(
          jsonEncode(listTypeFor('package:fonix/a.dart')),
          isNot(jsonEncode(listTypeFor('package:fonix/b.dart'))),
        );
      } finally {
        fixture.deleteSync(recursive: true);
      }
    },
    skip: _authoritativeSdkSkip(),
  );
}
