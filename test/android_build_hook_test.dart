import 'dart:convert';
import 'dart:io';

import 'package:code_assets/code_assets.dart';
import 'package:hooks/hooks.dart';
import 'package:test/test.dart';

import '../hook/build.dart' as build_hook;
import '../hook/src/build_config.dart';

void main() {
  test('application ownership enforces the locked Android API-24 floor', () {
    for (final targetAndroidNdkApi in <int?>[null, 23]) {
      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.android,
          targetArchitecture: Architecture.arm64,
          targetAndroidNdkApi: targetAndroidNdkApi,
          androidRuntimeOwner: fonixAndroidApplicationRuntimeOwner,
        ),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            allOf(contains('target_ndk_api'), contains('at least 24')),
          ),
        ),
      );
    }
    final options = resolveFonixBuildOptions(
      targetOS: OS.android,
      targetArchitecture: Architecture.arm64,
      targetAndroidNdkApi: 24,
      androidRuntimeOwner: fonixAndroidApplicationRuntimeOwner,
    );
    expect(options.targetAndroidNdkApi, 24);
  });

  final officialArtifactCache =
      Platform.environment['FONIX_ORT_ARTIFACT_CACHE'];

  test(
    'sherpa ownership emits only an API-24 process shim for both ABIs',
    () async {
      final userDefines = PackageUserDefines(
        workspacePubspec: PackageUserDefinesSource(
          defines: const <String, Object?>{
            fonixAndroidRuntimeOwnerUserDefine: fonixAndroidSherpaRuntimeOwner,
            // These values are deliberately invalid for their owning parsers.
            // A sherpa-owned Android build must not interpret them.
            fonixArtifactCacheUserDefine: false,
            fonixArtifactMirrorUserDefine: 7,
            fonixApplicationMinimumOsUserDefine: 15.1,
          },
          basePath: Directory.current.uri,
        ),
      );

      for (final target in <(Architecture, String)>[
        (Architecture.arm64, 'arm64-v8a'),
        (Architecture.x64, 'x86_64'),
      ]) {
        await testCodeBuildHook(
          mainMethod: build_hook.main,
          targetOS: OS.android,
          targetArchitecture: target.$1,
          targetAndroidNdkApi: fonixAndroidLockedMinimumNdkApi,
          userDefines: userDefines,
          check: (input, output) {
            expect(output.assets.code, hasLength(1));
            final shimAsset = output.assets.code.single;
            expect(shimAsset.id, fonixShimAssetId);
            expect(shimAsset.linkMode, DynamicLoadingBundled());
            expect(shimAsset.file, isNotNull);
            expect(
              output.assets.code.map((asset) => asset.id),
              isNot(contains(fonixOrtRuntimeAssetId)),
            );

            final shim = File.fromUri(shimAsset.file!);
            expect(shim.uri.pathSegments.last, 'libfonix_shim.so');
            expect(
              _containsBytes(
                shim.readAsBytesSync(),
                utf8.encode('android-owner-sherpa-source-process'),
              ),
              isTrue,
            );

            final auditDirectory = Directory.systemTemp.createTempSync(
              'fonix-android-sherpa-shim-audit-',
            );
            try {
              final abiDirectory = Directory(
                '${auditDirectory.path}/${target.$2}',
              )..createSync();
              shim.copySync('${abiDirectory.path}/libfonix_shim.so');
              final audit = Process.runSync('python3', <String>[
                'templates/android/verify_native_libs.py',
                '--artifact',
                auditDirectory.path,
                '--policy',
                'sherpa-audit',
                '--require-abi',
                target.$2,
                '--require-16k-page-alignment',
                '--quiet',
              ]);
              expect(
                audit.exitCode,
                0,
                reason: '${audit.stdout}\n${audit.stderr}',
              );
            } finally {
              auditDirectory.deleteSync(recursive: true);
            }
          },
        );
      }
    },
  );

  test(
    'sherpa ownership ignores shared artifact fields but rejects unknown keys',
    () {
      final options = resolveFonixBuildOptions(
        targetOS: OS.android,
        targetArchitecture: Architecture.arm64,
        targetAndroidNdkApi: fonixAndroidLockedMinimumNdkApi,
        androidRuntimeOwner: fonixAndroidSherpaRuntimeOwner,
        applicationMinimumOs: 15.1,
      );
      expect(options.runtimeMode, fonixExternalRuntimeMode);
      expect(options.applicationMinimumOs, isNull);
      expect(
        () => validateFonixUserDefineKeys(<String>{
          fonixAndroidRuntimeOwnerUserDefine,
          'android_ort_owner',
        }),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            contains('android_ort_owner'),
          ),
        ),
      );
    },
  );

  test(
    'bundled Android hook emits audited arm64 and x64 assets',
    () async {
      final cacheDirectory = Directory(officialArtifactCache!);
      final userDefines = PackageUserDefines(
        workspacePubspec: PackageUserDefinesSource(
          defines: <String, Object?>{
            fonixAndroidRuntimeOwnerUserDefine:
                fonixAndroidApplicationRuntimeOwner,
            fonixArtifactCacheUserDefine: cacheDirectory.path,
          },
          basePath: Directory.current.uri,
        ),
      );

      for (final target in <(Architecture, String)>[
        (Architecture.arm64, 'arm64-v8a'),
        (Architecture.x64, 'x86_64'),
      ]) {
        await testCodeBuildHook(
          mainMethod: build_hook.main,
          targetOS: OS.android,
          targetArchitecture: target.$1,
          targetAndroidNdkApi: fonixAndroidLockedMinimumNdkApi,
          userDefines: userDefines,
          check: (input, output) {
            expect(output.assets.code, hasLength(2));
            final assets = <String, CodeAsset>{
              for (final asset in output.assets.code) asset.id: asset,
            };
            expect(
              assets.keys,
              containsAll(<String>[fonixShimAssetId, fonixOrtRuntimeAssetId]),
            );
            final runtimeFile = File.fromUri(
              assets[fonixOrtRuntimeAssetId]!.file!,
            );
            expect(runtimeFile.uri.pathSegments.last, 'libonnxruntime.so');
            expect(
              File.fromUri(
                runtimeFile.parent.uri.resolve(
                  'fonix-native-artifact-manifest.json',
                ),
              ).existsSync(),
              isTrue,
            );
            final stagedManifestFile = File.fromUri(
              runtimeFile.parent.uri.resolve(
                'fonix-native-artifact-manifest.json',
              ),
            );
            final stagedManifest =
                jsonDecode(stagedManifestFile.readAsStringSync())
                    as Map<String, Object?>;
            final targetIdentity =
                stagedManifest['target']! as Map<String, Object?>;
            final sourceIdentity =
                stagedManifest['source']! as Map<String, Object?>;
            expect(
              stagedManifest['artifactId'],
              'onnxruntime-1.27.1-android-${target.$2}-cpu',
            );
            expect(targetIdentity['os'], 'android');
            expect(targetIdentity['architecture'], target.$2);
            expect(targetIdentity['runtimeMode'], fonixBundledRuntimeMode);
            expect(
              sourceIdentity['sha256'],
              '9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383',
            );
            final shimFile = File.fromUri(assets[fonixShimAssetId]!.file!);
            expect(
              _containsBytes(
                shimFile.readAsBytesSync(),
                utf8.encode(
                  'android-owner-application-source-bundled-'
                  'artifact-onnxruntime-1.27.1-android-${target.$2}-cpu',
                ),
              ),
              isTrue,
            );
            expect(
              File.fromUri(
                runtimeFile.parent.uri.resolve('notices/ThirdPartyNotices.txt'),
              ).existsSync(),
              isTrue,
            );

            final auditDirectory = Directory.systemTemp.createTempSync(
              'fonix-android-emitted-audit-',
            );
            try {
              final abiDirectory = Directory(
                '${auditDirectory.path}/${target.$2}',
              )..createSync();
              for (final asset in assets.values) {
                final source = File.fromUri(asset.file!);
                source.copySync(
                  '${abiDirectory.path}/${source.uri.pathSegments.last}',
                );
              }
              final audit = Process.runSync('python3', <String>[
                'templates/android/verify_native_libs.py',
                '--artifact',
                auditDirectory.path,
                '--policy',
                'sherpa-audit',
                '--require-abi',
                target.$2,
                '--require-final-single-ort',
                '--require-16k-page-alignment',
                '--quiet',
              ]);
              expect(
                audit.exitCode,
                0,
                reason: '${audit.stdout}\n${audit.stderr}',
              );

              final packageRoot = Directory('${auditDirectory.path}/apk-root')
                ..createSync();
              final packageAbiDirectory = Directory(
                '${packageRoot.path}/lib/${target.$2}',
              )..createSync(recursive: true);
              for (final asset in assets.values) {
                final source = File.fromUri(asset.file!);
                source.copySync(
                  '${packageAbiDirectory.path}/'
                  '${source.uri.pathSegments.last}',
                );
              }
              final apk = File('${auditDirectory.path}/fonix-${target.$2}.apk');
              final packageResult = Process.runSync('python3', <String>[
                '-c',
                '''import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(sys.argv[2], "w", compression=zipfile.ZIP_STORED) as archive:
    for candidate in sorted(root.rglob("*")):
        if candidate.is_file():
            archive.write(candidate, candidate.relative_to(root).as_posix())
''',
                packageRoot.path,
                apk.path,
              ]);
              expect(
                packageResult.exitCode,
                0,
                reason: '${packageResult.stdout}\n${packageResult.stderr}',
              );
              final finalPackageAudit = Process.runSync('python3', <String>[
                'templates/android/verify_native_libs.py',
                '--artifact',
                apk.path,
                '--policy',
                'fonix-standalone-final',
                '--require-abi',
                target.$2,
                '--require-16k-page-alignment',
                '--quiet',
              ]);
              expect(
                finalPackageAudit.exitCode,
                0,
                reason:
                    '${finalPackageAudit.stdout}\n'
                    '${finalPackageAudit.stderr}',
              );
            } finally {
              auditDirectory.deleteSync(recursive: true);
            }
          },
        );
      }
    },
    skip: officialArtifactCache == null
        ? 'Set FONIX_ORT_ARTIFACT_CACHE to the verified archive directory.'
        : false,
  );
}

bool _containsBytes(List<int> bytes, List<int> needle) {
  if (needle.isEmpty || needle.length > bytes.length) {
    return false;
  }
  for (var start = 0; start <= bytes.length - needle.length; start++) {
    var matches = true;
    for (var offset = 0; offset < needle.length; offset++) {
      if (bytes[start + offset] != needle[offset]) {
        matches = false;
        break;
      }
    }
    if (matches) {
      return true;
    }
  }
  return false;
}
