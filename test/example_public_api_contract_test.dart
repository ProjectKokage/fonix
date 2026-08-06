import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:test/test.dart';

const String _modelSha256 =
    '71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10';
const String _noticeSha256 =
    '0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2';
const String _androidNoticeSha256 =
    'fb0af774b4d7cffc5b9d046f2aaeade2f37df2f80abf8033c95dfffcc77a8866';

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

    test('commits the exact Android arm64 notice and native-asset identity', () {
      const String directory = 'example/assets/fonix/android-arm64-v8a';
      final File notice = File('$directory/ThirdPartyNotices.txt');
      expect(
        sha256.convert(notice.readAsBytesSync()).toString(),
        _androidNoticeSha256,
      );

      final Map<String, Object?> manifest = _object(
        jsonDecode(
          File(
            '$directory/fonix-native-artifact-manifest.json',
          ).readAsStringSync(),
        ),
      );
      expect(manifest['schema'], 2);
      expect(
        manifest['artifactId'],
        'onnxruntime-1.27.1-android-arm64-v8a-cpu',
      );
      expect(_object(manifest['target']), <String, Object?>{
        'os': 'android',
        'architecture': 'arm64-v8a',
        'variant': 'default',
        'minimumOs': '24',
        'flavor': 'cpu',
        'runtimeMode': 'bundled',
      });
      final Map<String, Object?> source = _object(manifest['source']);
      expect(
        source['sha256'],
        '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
      );
      final List<Object?> payloads =
          (manifest['payloadFiles'] as List<Object?>);
      expect(payloads, hasLength(1));
      expect(_object(payloads.single), <String, Object?>{
        'archivePath': 'jni/arm64-v8a/libonnxruntime.so',
        'stagedPath': 'libonnxruntime.so',
        'sha256':
            'a7579e85ecc5465840d352c35f355e5b7418d36901670d36afd46555304458c2',
        'sizeBytes': 27983536,
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

    test('pins the Android arm64 release and smoke-bridge contract', () {
      final String gradle = File(
        'example/android/app/build.gradle.kts',
      ).readAsStringSync();
      expect(gradle, contains('compileSdk = 36'));
      expect(gradle, contains('ndkVersion = "28.2.13676358"'));
      expect(gradle, contains('minSdk = 24'));
      expect(gradle, contains('targetSdk = 36'));
      expect(gradle, contains('abiFilters.add("arm64-v8a")'));
      expect(gradle, contains('isDebuggable = false'));
      expect(gradle, contains('isMinifyEnabled = true'));
      expect(gradle, isNot(contains('pickFirst')));

      final String manifest = File(
        'example/android/app/src/main/AndroidManifest.xml',
      ).readAsStringSync();
      expect(manifest, contains('android:allowBackup="false"'));
      expect(manifest, contains('android:usesCleartextTraffic="false"'));
      expect(manifest, isNot(contains('<uses-permission')));
      expect('android:exported="true"'.allMatches(manifest), hasLength(1));

      final String activity = File(
        'example/android/app/src/main/kotlin/dev/fonix/'
        'fonix_reference/MainActivity.kt',
      ).readAsStringSync();
      expect(activity, contains('"dev.fonix.reference/smoke"'));
      expect(activity, contains('"FONIX_REFERENCE_RECEIPT="'));
      expect(activity, contains('"FonixReference"'));
      expect(activity, contains('MAX_RECEIPT_BYTES = 16 * 1024'));
      expect(activity, contains('EXPECTED_ARTIFACT_SHA256'));
      expect(activity, contains('EXPECTED_SHIM_BUILD_ID'));

      final String wrapper = File(
        'example/android/gradle/wrapper/gradle-wrapper.properties',
      ).readAsStringSync();
      expect(wrapper, contains('gradle-9.3.1-all.zip'));
      expect(
        wrapper,
        contains(
          'distributionSha256Sum='
          '17f277867f6914d61b1aa02efab1ba7bb439ad652ca485cd8ca6842fccec6e43',
        ),
      );
    });
  });
}

Map<String, Object?> _object(Object? value) {
  if (value is! Map) throw const FormatException('Expected a JSON object.');
  return value.cast<String, Object?>();
}
