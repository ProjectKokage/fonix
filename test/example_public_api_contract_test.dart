import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:test/test.dart';

const String _modelSha256 =
    '71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10';
const String _noticeSha256 =
    '0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2';

void main() {
  group('committed Flutter reference application', () {
    test('pins the exact public smoke-model bytes and contract', () {
      final File exampleModel = File('example/assets/models/mul_1.onnx');
      final File fixtureModel = File('test/fixtures/mul_1.onnx');
      final List<int> bytes = exampleModel.readAsBytesSync();
      expect(bytes, fixtureModel.readAsBytesSync());
      expect(bytes, hasLength(130));
      expect(sha256.convert(bytes).toString(), _modelSha256);

      final Map<String, Object?> manifest = _object(
        jsonDecode(File('example/assets/models/model.json').readAsStringSync()),
      );
      expect(manifest.keys.toSet(), <String>{
        'schemaVersion',
        'id',
        'path',
        'source',
        'sourceRevision',
        'sha256',
        'sizeBytes',
        'input',
        'output',
        'claimBoundary',
      });
      expect(manifest['schemaVersion'], 1);
      expect(manifest['sha256'], _modelSha256);
      expect(manifest['sizeBytes'], 130);
      expect(_object(manifest['input'])['values'], <Object?>[1, 2, 3, 4, 5, 6]);
      expect(_object(manifest['output'])['values'], <Object?>[
        1,
        4,
        9,
        16,
        25,
        36,
      ]);
      expect(
        manifest['claimBoundary'],
        contains('not representative performance'),
      );
    });

    test('commits the exact macOS notice and native-asset identity', () {
      final File notice = File('example/assets/fonix/ThirdPartyNotices.txt');
      expect(
        sha256.convert(notice.readAsBytesSync()).toString(),
        _noticeSha256,
      );

      final Map<String, Object?> manifest = _object(
        jsonDecode(
          File(
            'example/assets/fonix/fonix-native-artifact-manifest.json',
          ).readAsStringSync(),
        ),
      );
      expect(manifest['schema'], 2);
      expect(manifest['artifactId'], 'onnxruntime-1.27.1-macos-arm64-cpu');
      expect(_object(manifest['target']), <String, Object?>{
        'os': 'macos',
        'architecture': 'arm64',
        'variant': 'default',
        'minimumOs': '14.0',
        'flavor': 'cpu',
        'runtimeMode': 'bundled',
      });
    });

    test('uses only the public Fonix library from application code', () {
      final List<File> dartFiles = Directory('example/lib')
          .listSync(recursive: true, followLinks: false)
          .whereType<File>()
          .where((File file) => file.path.endsWith('.dart'))
          .toList(growable: false);
      expect(dartFiles, isNotEmpty);
      final String source = dartFiles
          .map((File file) => file.readAsStringSync())
          .join('\n');
      expect(source, isNot(contains('package:fonix/src/')));
      expect(source, isNot(contains('dart:ffi')));
      expect(source, isNot(contains('generated_bindings')));
      expect(source, isNot(contains('dort_')));
      expect(source, contains("package:fonix/fonix.dart"));
    });

    test('pins the app floor, architecture, hardening, and hook inputs', () {
      final String pubspec = File('example/pubspec.yaml').readAsStringSync();
      expect(pubspec, contains('fonix:\n    path: ..'));
      expect(pubspec, contains('runtime_mode: bundled'));
      expect(pubspec, contains('artifact_cache: .fonix-artifact-cache'));
      expect(pubspec, contains("application_minimum_os: '14.0'"));
      expect(
        pubspec,
        contains('assets/fonix/fonix-native-artifact-manifest.json'),
      );
      expect(pubspec, contains('assets/fonix/ThirdPartyNotices.txt'));

      final String project = File(
        'example/macos/Runner.xcodeproj/project.pbxproj',
      ).readAsStringSync();
      expect(
        'MACOSX_DEPLOYMENT_TARGET = 14.0;'.allMatches(project),
        hasLength(3),
      );
      expect('EXCLUDED_ARCHS = x86_64;'.allMatches(project), hasLength(3));
      expect(
        'ENABLE_HARDENED_RUNTIME = YES;'.allMatches(project),
        hasLength(2),
      );

      final String releaseEntitlements = File(
        'example/macos/Runner/Release.entitlements',
      ).readAsStringSync();
      expect(
        '<key>com.apple.security.app-sandbox</key>'.allMatches(
          releaseEntitlements,
        ),
        hasLength(1),
      );
      expect(
        '<key>com.apple.security.cs.disable-library-validation</key>'
            .allMatches(releaseEntitlements),
        hasLength(1),
      );
    });
  });
}

Map<String, Object?> _object(Object? value) {
  if (value is! Map) throw const FormatException('Expected a JSON object.');
  return value.cast<String, Object?>();
}
