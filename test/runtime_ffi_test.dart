import 'dart:convert';
import 'dart:ffi';
import 'dart:io';

import 'package:fonix/fonix.dart';
import 'package:fonix/src/ffi/native_api.dart';
import 'package:fonix/src/runtime.dart';
import 'package:fonix/src/runtime_info.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  group('runtime information protocol', () {
    const String buildJson =
        '{"schemaVersion":3,"nativeIdentity":"fonix_shim",'
        '"shimAbiVersion":1,"requiredOrtApiVersion":27,'
        '"runtimeProfile":"external","androidRuntimeOwner":null,'
        '"allowedRuntimeSources":["process","file"],'
        '"buildId":"native-tests",'
        '"artifact":null}';

    test('accepts an exact matching payload', () {
      final OrtNativeBuildInfo buildInfo = parseOrtNativeBuildInfo(buildJson);
      final OrtRuntimeInfo info = parseOrtRuntimeInfo(
        jsonEncode(<String, Object?>{
          'schemaVersion': 1,
          'nativeIdentity': 'fonix_shim',
          'shimAbiVersion': 1,
          'shimBuildId': 'native-tests',
          'requiredOrtApiVersion': 27,
          'negotiatedOrtApiVersion': 27,
          'runtimeVersion': '1.27.99-fonix-test',
          'runtimeSource': 'file',
          'runtimeProfile': 'external',
          'runtimeLibraryIdentity': 'libfake_ort_good.dylib',
          'logSeverity': 'warning',
          'logId': 'fonix-test',
        }),
        buildInfo: buildInfo,
        requestedSource: OrtRuntimeSourceKind.file,
        requestedApi: OrtApiVersion.v27,
        requestedLogSeverity: OrtLogSeverity.warning,
        requestedLogId: 'fonix-test',
      );

      expect(info.runtimeVersion, '1.27.99-fonix-test');
      expect(info.runtimeSource, OrtRuntimeSourceKind.file);
      expect(info.toJson()['dartPackageVersion'], fonixPackageVersion);
      expect(buildInfo.artifact, isNull);
      expect(buildInfo.androidRuntimeOwner, isNull);
      expect(buildInfo.allowedRuntimeSources, <OrtRuntimeSourceKind>[
        OrtRuntimeSourceKind.process,
        OrtRuntimeSourceKind.file,
      ]);
    });

    test('accepts and exposes the exact closed artifact identity', () {
      final digestA = List<String>.filled(64, 'a').join();
      final digestB = List<String>.filled(64, 'b').join();
      final digestC = List<String>.filled(64, 'c').join();
      final buildInfo = parseOrtNativeBuildInfo(
        jsonEncode(<String, Object?>{
          'schemaVersion': 3,
          'nativeIdentity': 'fonix_shim',
          'shimAbiVersion': 1,
          'requiredOrtApiVersion': 27,
          'runtimeProfile': 'bundled',
          'androidRuntimeOwner': null,
          'allowedRuntimeSources': <String>['bundled'],
          'buildId': 'onnxruntime-1.27.1-macos-arm64-cpu',
          'artifact': <String, Object?>{
            'id': 'onnxruntime-1.27.1-macos-arm64-cpu',
            'lockSha256': digestA,
            'sourceSha256': digestB,
            'targetOs': 'macos',
            'targetArchitecture': 'arm64',
            'targetVariant': 'default',
            'minimumOs': '14.0',
            'flavor': 'cpu',
            'runtimeMode': 'bundled',
            'thirdPartyNoticesSha256': digestC,
            'providers': <Object?>[
              <String, Object?>{
                'wrapperId': 'cpu',
                'reportedName': 'CPUExecutionProvider',
              },
              <String, Object?>{
                'wrapperId': 'coreml',
                'reportedName': 'CoreMLExecutionProvider',
              },
            ],
          },
        }),
      );

      expect(buildInfo.artifact?.id, 'onnxruntime-1.27.1-macos-arm64-cpu');
      expect(buildInfo.artifact?.minimumOs, '14.0');
      expect(buildInfo.artifact?.sourceSha256, digestB);
      expect(buildInfo.artifact?.thirdPartyNoticesSha256, digestC);
      expect(buildInfo.artifact?.providers, <String, String?>{
        'cpu': 'CPUExecutionProvider',
        'coreml': 'CoreMLExecutionProvider',
      });
      expect(buildInfo.toJson()['artifact'], buildInfo.artifact?.toJson());
      expect(buildInfo.toJson()['allowedRuntimeSources'], <String>['bundled']);
    });

    test('accepts an exact sherpa owner and rejects source-policy drift', () {
      final Map<String, Object?> manifest = <String, Object?>{
        'schemaVersion': 3,
        'nativeIdentity': 'fonix_shim',
        'shimAbiVersion': 1,
        'requiredOrtApiVersion': 27,
        'runtimeProfile': 'external',
        'androidRuntimeOwner': 'sherpa',
        'allowedRuntimeSources': <String>['process'],
        'buildId': 'android-owner-sherpa-source-process',
        'artifact': null,
      };
      final OrtNativeBuildInfo buildInfo = parseOrtNativeBuildInfo(
        jsonEncode(manifest),
      );
      expect(buildInfo.androidRuntimeOwner, OrtAndroidRuntimeOwner.sherpa);
      expect(buildInfo.allowedRuntimeSources, const <OrtRuntimeSourceKind>[
        OrtRuntimeSourceKind.process,
      ]);

      expect(
        () => parseOrtNativeBuildInfo(
          jsonEncode(<String, Object?>{
            ...manifest,
            'allowedRuntimeSources': <String>['process', 'file'],
          }),
        ),
        throwsFormatException,
      );
    });

    test('rejects unknown keys and configuration mismatches', () {
      expect(
        () => parseOrtNativeBuildInfo(
          '${buildJson.substring(0, buildJson.length - 1)},"extra":true}',
        ),
        throwsFormatException,
      );

      final OrtNativeBuildInfo buildInfo = parseOrtNativeBuildInfo(buildJson);
      expect(
        () => parseOrtRuntimeInfo(
          _runtimeInfoJson(logId: 'different'),
          buildInfo: buildInfo,
          requestedSource: OrtRuntimeSourceKind.file,
          requestedApi: OrtApiVersion.v27,
          requestedLogSeverity: OrtLogSeverity.warning,
          requestedLogId: 'fonix-test',
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtNativeBuildInfo(
          buildJson.replaceFirst('"external"', '"linked"'),
        ),
        throwsFormatException,
      );
      expect(
        () => parseOrtNativeBuildInfo(
          buildJson.replaceFirst(
            '"schemaVersion":3',
            '"schemaVersion":3,"schemaVersion":3',
          ),
        ),
        throwsFormatException,
      );
    });

    test('rejects a private path in runtime identity', () {
      final OrtNativeBuildInfo buildInfo = parseOrtNativeBuildInfo(buildJson);
      expect(
        () => parseOrtRuntimeInfo(
          _runtimeInfoJson(
            runtimeLibraryIdentity: '/private/user/libonnxruntime.dylib',
          ),
          buildInfo: buildInfo,
          requestedSource: OrtRuntimeSourceKind.file,
          requestedApi: OrtApiVersion.v27,
          requestedLogSeverity: OrtLogSeverity.warning,
          requestedLogId: 'fonix-test',
        ),
        throwsFormatException,
      );
    });
  });

  test('validates log IDs before entering FFI', () {
    expect(
      () => OrtRuntime.open(
        source: OrtRuntimeSource.process(),
        logId: 'private/path',
      ),
      throwsArgumentError,
    );
    expect(
      () => OrtRuntime.open(
        source: OrtRuntimeSource.process(),
        logId: List<String>.filled(129, 'x').join(),
      ),
      throwsArgumentError,
    );
  });

  final _NativeFixture? fixture = _NativeFixture.fromEnvironment();
  if (fixture == null) {
    test(
      'native runtime bridge fixture is provisioned',
      () {},
      skip:
          'Set FONIX_TEST_SHIM_PATH, FONIX_TEST_ORT_PATH, and '
          'FONIX_TEST_UNSUPPORTED_ORT_PATH after building test/native.',
    );
    return;
  }

  group('native runtime bridge', () {
    test('opens through production native-asset bindings', () {
      final OrtRuntime runtime = OrtRuntime.open(
        source: OrtRuntimeSource.file(
          absolutePath: fixture.goodRuntime,
          allowedRoot: fixture.allowedRoot,
        ),
        logSeverity: OrtLogSeverity.warning,
        logId: 'fonix-production-test',
      );
      try {
        expect(runtime.buildInfo.shimAbiVersion, fonixShimAbiVersion);
        expect(runtime.info.negotiatedOrtApiVersion, OrtApiVersion.v27.value);
        expect(runtime.info.runtimeVersion, '1.27.99-fonix-test');
        expect(runtime.info.runtimeSource, OrtRuntimeSourceKind.file);
        expect(
          runtime.info.runtimeLibraryIdentity,
          p.basename(fixture.goodRuntime),
        );
        expect(
          runtime.info.runtimeLibraryIdentity,
          isNot(contains(fixture.allowedRoot)),
        );
      } finally {
        runtime.dispose();
      }

      runtime.dispose();
      expect(runtime.isDisposed, isTrue);
      expect(() => runtime.info, throwsA(isA<OrtDisposedException>()));
      expect(() => runtime.buildInfo, throwsA(isA<OrtDisposedException>()));
    });

    test('dynamic test seam uses the same ABI and disposal contract', () {
      final FonixNativeApi nativeApi = FonixNativeApi.dynamicLibrary(
        DynamicLibrary.open(fixture.shim),
      );
      expect(nativeApi.verifyStableExportsForTesting(), 67);
      final OrtRuntime runtime = openOrtRuntimeWithNativeApiForTesting(
        nativeApi: nativeApi,
        source: OrtRuntimeSource.file(
          absolutePath: fixture.goodRuntime,
          allowedRoot: fixture.allowedRoot,
        ),
        logId: 'fonix-dynamic-test',
      );
      expect(runtime.info.runtimeVersion, '1.27.99-fonix-test');
      runtime.dispose();
      runtime.dispose();
      expect(runtime.isDisposed, isTrue);
    });

    test('maps API negotiation failure without leaking its parent path', () {
      expect(
        () => OrtRuntime.open(
          source: OrtRuntimeSource.file(
            absolutePath: fixture.unsupportedRuntime,
            allowedRoot: fixture.unsupportedRoot,
          ),
          logId: 'fonix-unsupported-test',
        ),
        throwsA(
          isA<OrtApiIncompatibleException>()
              .having(
                (OrtApiIncompatibleException error) => error.message,
                'message',
                isNot(contains(fixture.unsupportedRoot)),
              )
              .having(
                (OrtApiIncompatibleException error) => error.message,
                'message',
                isNot(contains(fixture.unsupportedRuntime)),
              ),
        ),
      );
    });

    test('maps loader failure and removes the allowed-root parent', () {
      final String missingRuntime = p.join(
        fixture.allowedRoot,
        'private-user-runtime-does-not-exist.dylib',
      );
      expect(
        () => OrtRuntime.open(
          source: OrtRuntimeSource.file(
            absolutePath: missingRuntime,
            allowedRoot: fixture.allowedRoot,
          ),
          logId: 'fonix-missing-test',
        ),
        throwsA(
          isA<OrtRuntimeNotFoundException>()
              .having(
                (OrtRuntimeNotFoundException error) => error.message,
                'message',
                isNot(contains(fixture.allowedRoot)),
              )
              .having(
                (OrtRuntimeNotFoundException error) => error.message,
                'message',
                isNot(contains(missingRuntime)),
              ),
        ),
      );
    });

    test('rejects a source unsupported by the native build profile', () {
      expect(
        () => OrtRuntime.open(source: const OrtRuntimeSource.linked()),
        throwsA(isA<OrtNativePackagingException>()),
      );
    });
  });
}

String _runtimeInfoJson({
  String logId = 'fonix-test',
  String runtimeLibraryIdentity = 'libfake_ort_good.dylib',
}) => jsonEncode(<String, Object?>{
  'schemaVersion': 1,
  'nativeIdentity': 'fonix_shim',
  'shimAbiVersion': 1,
  'shimBuildId': 'native-tests',
  'requiredOrtApiVersion': 27,
  'negotiatedOrtApiVersion': 27,
  'runtimeVersion': '1.27.99-fonix-test',
  'runtimeSource': 'file',
  'runtimeProfile': 'external',
  'runtimeLibraryIdentity': runtimeLibraryIdentity,
  'logSeverity': 'warning',
  'logId': logId,
});

final class _NativeFixture {
  const _NativeFixture({
    required this.shim,
    required this.goodRuntime,
    required this.unsupportedRuntime,
  });

  factory _NativeFixture._fromEnvironmentChecked() {
    final Map<String, String> environment = Platform.environment;
    final String? shim = environment['FONIX_TEST_SHIM_PATH'];
    final String? goodRuntime = environment['FONIX_TEST_ORT_PATH'];
    final String? unsupportedRuntime =
        environment['FONIX_TEST_UNSUPPORTED_ORT_PATH'];
    if (shim == null || goodRuntime == null || unsupportedRuntime == null) {
      throw const FormatException('missing native test fixture paths');
    }
    final Map<String, String> paths = <String, String>{
      'FONIX_TEST_SHIM_PATH': shim,
      'FONIX_TEST_ORT_PATH': goodRuntime,
      'FONIX_TEST_UNSUPPORTED_ORT_PATH': unsupportedRuntime,
    };
    for (final MapEntry<String, String> entry in paths.entries) {
      final String path = entry.value;
      if (!p.isAbsolute(path) || !File(path).existsSync()) {
        throw FormatException(
          '${entry.key} must name an absolute, existing file.',
        );
      }
    }
    return _NativeFixture(
      shim: p.normalize(shim),
      goodRuntime: p.normalize(goodRuntime),
      unsupportedRuntime: p.normalize(unsupportedRuntime),
    );
  }

  static _NativeFixture? fromEnvironment() {
    final Map<String, String> environment = Platform.environment;
    final bool allAbsent = const <String>[
      'FONIX_TEST_SHIM_PATH',
      'FONIX_TEST_ORT_PATH',
      'FONIX_TEST_UNSUPPORTED_ORT_PATH',
    ].every((String key) => !environment.containsKey(key));
    if (allAbsent) {
      return null;
    }
    return _NativeFixture._fromEnvironmentChecked();
  }

  final String shim;
  final String goodRuntime;
  final String unsupportedRuntime;

  String get allowedRoot => p.dirname(goodRuntime);

  String get unsupportedRoot => p.dirname(unsupportedRuntime);
}
