import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:typed_data';

import 'package:code_assets/code_assets.dart';
import 'package:fonix/fonix.dart';
import 'package:fonix/src/ffi/native_api.dart';
import 'package:fonix/src/runtime.dart' as runtime_testing;
import 'package:fonix/src/runtime_info.dart' as runtime_info_testing;
import 'package:hooks/hooks.dart';
import 'package:native_toolchain_c/native_toolchain_c.dart';
import 'package:test/test.dart';

import '../hook/build.dart' as build_hook;
import '../hook/src/build_config.dart';
import '../hook/src/macos_ort_artifact_resolver.dart';

void main() {
  final realMacosOrtMirror =
      Platform.environment['FONIX_TEST_MACOS_ORT_ARCHIVE_DIR'];
  final officialArtifactCache =
      Platform.environment['FONIX_ORT_ARTIFACT_CACHE'];

  group('build configuration', () {
    test('defaults to the external non-linking runtime profile', () {
      final options = resolveFonixBuildOptions(
        targetOS: OS.macOS,
        targetArchitecture: Architecture.arm64,
      );

      expect(options.runtimeMode, fonixExternalRuntimeMode);
      expect(options.linksOnnxRuntime, isFalse);
      expect(
        options.runtimeSources,
        equals(<FonixRuntimeSource>{
          FonixRuntimeSource.process,
          FonixRuntimeSource.file,
        }),
      );
      expect(fonixRequiredOrtApiVersion, 27);
      expect(fonixShimAssetId, 'package:fonix/fonix_shim');
      expect(fonixOrtRuntimeAssetId, 'package:fonix/onnxruntime');
    });

    test('accepts every locked dynamic baseline tuple', () {
      final options = resolveFonixBuildOptions(
        targetOS: OS.macOS,
        targetArchitecture: Architecture.arm64,
        runtimeMode: ' BUNDLED ',
        applicationMinimumOs: '14.0',
      );

      expect(options.runtimeMode, fonixBundledRuntimeMode);
      expect(
        options.runtimeSources,
        equals(<FonixRuntimeSource>{FonixRuntimeSource.bundled}),
      );
      expect(options.linksOnnxRuntime, isFalse);

      for (final target in <(OS, Architecture)>[
        (OS.macOS, Architecture.arm64),
        (OS.linux, Architecture.arm64),
        (OS.linux, Architecture.x64),
        (OS.windows, Architecture.x64),
        (OS.android, Architecture.arm64),
        (OS.android, Architecture.x64),
      ]) {
        final resolved = resolveFonixBuildOptions(
          targetOS: target.$1,
          targetArchitecture: target.$2,
          runtimeMode: fonixBundledRuntimeMode,
          androidRuntimeOwner: target.$1 == OS.android
              ? fonixAndroidApplicationRuntimeOwner
              : null,
          applicationMinimumOs: target.$1 == OS.macOS ? '14.0' : null,
          targetAndroidNdkApi: target.$1 == OS.android ? 24 : null,
        );
        expect(resolved.runtimeMode, fonixBundledRuntimeMode);
        expect(resolved.targetVariant, 'default');
      }

      for (final target in <(OS, Architecture)>[
        (OS.macOS, Architecture.x64),
        (OS.windows, Architecture.arm64),
      ]) {
        expect(
          () => resolveFonixBuildOptions(
            targetOS: target.$1,
            targetArchitecture: target.$2,
            runtimeMode: fonixBundledRuntimeMode,
          ),
          throwsA(
            isA<BuildError>().having(
              (error) => error.message,
              'message',
              contains('no baseline lock tuple'),
            ),
          ),
        );
      }
    });

    for (final targetOS in <OS>[OS.macOS, OS.linux, OS.windows, OS.android]) {
      for (final architecture in <Architecture>[
        Architecture.arm64,
        Architecture.x64,
      ]) {
        test('accepts ${targetOS.name}/${architecture.name}', () {
          final options = resolveFonixBuildOptions(
            targetOS: targetOS,
            targetArchitecture: architecture,
            runtimeMode: ' EXTERNAL ',
            androidRuntimeOwner: targetOS == OS.android ? ' SHERPA ' : null,
            targetAndroidNdkApi: targetOS == OS.android ? 24 : null,
          );

          expect(options.targetOS, targetOS);
          expect(options.targetArchitecture, architecture);
        });
      }
    }

    test('defaults iOS arm64 device and simulator to linked mode', () {
      for (final sdk in <IOSSdk>[IOSSdk.iPhoneOS, IOSSdk.iPhoneSimulator]) {
        final options = resolveFonixBuildOptions(
          targetOS: OS.iOS,
          targetArchitecture: Architecture.arm64,
          targetIOSSdk: sdk,
          // Flutter currently forwards this fixed native-assets value. It is
          // compiler metadata, not the consuming app's deployment floor.
          targetIOSVersion: 13,
          applicationMinimumOs: '15.1',
        );
        expect(options.runtimeMode, fonixLinkedRuntimeMode);
        expect(options.linksOnnxRuntime, isTrue);
        expect(
          options.runtimeSources,
          equals(<FonixRuntimeSource>{FonixRuntimeSource.linked}),
        );
        expect(
          options.targetVariant,
          sdk == IOSSdk.iPhoneSimulator ? 'simulator' : 'device',
        );
        expect(
          fonixPinnedAppleDeploymentFlags(options, lockedMinimumOs: '15.1'),
          <String>['-mios-version-min=15.1'],
        );
      }

      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.iOS,
          targetArchitecture: Architecture.arm64,
        ),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            contains('explicit device or simulator SDK'),
          ),
        ),
      );
      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.iOS,
          targetArchitecture: Architecture.arm64,
          targetIOSSdk: IOSSdk.iPhoneOS,
        ),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            allOf(
              contains('target-version metadata'),
              contains('final application'),
            ),
          ),
        ),
      );
    });

    test('fails closed on missing, malformed, or low Apple app floors', () {
      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.macOS,
          targetArchitecture: Architecture.arm64,
          runtimeMode: fonixBundledRuntimeMode,
        ),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            allOf(
              contains(fonixApplicationMinimumOsUserDefine),
              contains('targetVersion is compiler metadata only'),
            ),
          ),
        ),
      );
      for (final invalid in <Object>[' 14.0', '14', '014.0', 14]) {
        expect(
          () => resolveFonixBuildOptions(
            targetOS: OS.macOS,
            targetArchitecture: Architecture.arm64,
            runtimeMode: fonixBundledRuntimeMode,
            applicationMinimumOs: invalid,
          ),
          throwsA(isA<BuildError>()),
        );
      }
      final options = resolveFonixBuildOptions(
        targetOS: OS.iOS,
        targetArchitecture: Architecture.arm64,
        targetIOSSdk: IOSSdk.iPhoneOS,
        targetIOSVersion: 13,
        applicationMinimumOs: '15.0',
      );
      expect(
        () => validateFonixApplicationMinimumOs(options, '15.1'),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            allOf(contains('15.0'), contains('15.1')),
          ),
        ),
      );
    });

    test('rejects unsupported architectures', () {
      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.android,
          targetArchitecture: Architecture.arm,
          targetAndroidNdkApi: 24,
          androidRuntimeOwner: fonixAndroidSherpaRuntimeOwner,
        ),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            contains('Supported architectures are arm64 and x64'),
          ),
        ),
      );
    });

    test('requires the locked Android API-24 floor for both owners', () {
      for (final targetAndroidNdkApi in <int?>[null, 23]) {
        for (final owner in <String>[
          fonixAndroidSherpaRuntimeOwner,
          fonixAndroidApplicationRuntimeOwner,
        ]) {
          expect(
            () => resolveFonixBuildOptions(
              targetOS: OS.android,
              targetArchitecture: Architecture.arm64,
              targetAndroidNdkApi: targetAndroidNdkApi,
              androidRuntimeOwner: owner,
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
      }
      final options = resolveFonixBuildOptions(
        targetOS: OS.android,
        targetArchitecture: Architecture.arm64,
        targetAndroidNdkApi: 24,
        androidRuntimeOwner: fonixAndroidApplicationRuntimeOwner,
      );
      expect(options.targetAndroidNdkApi, 24);
      expect(options.runtimeMode, fonixBundledRuntimeMode);
    });

    test(
      'requires explicit closed Android ownership without mode fallback',
      () {
        expect(
          () => resolveFonixBuildOptions(
            targetOS: OS.android,
            targetArchitecture: Architecture.arm64,
            targetAndroidNdkApi: 24,
          ),
          throwsA(
            isA<BuildError>().having(
              (error) => error.message,
              'message',
              contains(fonixAndroidRuntimeOwnerUserDefine),
            ),
          ),
        );
        for (final contradiction in <(String, String)>[
          (fonixAndroidSherpaRuntimeOwner, fonixBundledRuntimeMode),
          (fonixAndroidApplicationRuntimeOwner, fonixExternalRuntimeMode),
        ]) {
          expect(
            () => resolveFonixBuildOptions(
              targetOS: OS.android,
              targetArchitecture: Architecture.arm64,
              targetAndroidNdkApi: 24,
              androidRuntimeOwner: contradiction.$1,
              runtimeMode: contradiction.$2,
            ),
            throwsA(
              isA<BuildError>().having(
                (error) => error.message,
                'message',
                allOf(contains('requires'), contains('never falls back')),
              ),
            ),
          );
        }
        expect(
          () => resolveFonixBuildOptions(
            targetOS: OS.android,
            targetArchitecture: Architecture.arm64,
            targetAndroidNdkApi: 24,
            androidRuntimeOwner: 'wrapper',
          ),
          throwsA(isA<BuildError>()),
        );
        expect(
          () => resolveFonixBuildOptions(
            targetOS: OS.linux,
            targetArchitecture: Architecture.x64,
            androidRuntimeOwner: fonixAndroidSherpaRuntimeOwner,
          ),
          throwsA(
            isA<BuildError>().having(
              (error) => error.message,
              'message',
              contains('valid only for Android'),
            ),
          ),
        );
      },
    );

    test('rejects unknown package-scoped user-define fields', () {
      expect(
        () => validateFonixUserDefineKeys(<String>{
          fonixRuntimeModeUserDefine,
          'android_ort_owner',
        }),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            allOf(
              contains('android_ort_owner'),
              contains(fonixAndroidRuntimeOwnerUserDefine),
            ),
          ),
        ),
      );
      expect(
        () => validateFonixUserDefineKeys(fonixKnownUserDefineKeys),
        returnsNormally,
      );
    });

    test('rejects linked mode outside the locked iOS targets', () {
      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.macOS,
          targetArchitecture: Architecture.arm64,
          runtimeMode: fonixLinkedRuntimeMode,
        ),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            allOf(contains('linked'), contains('only for iOS arm64')),
          ),
        ),
      );
    });

    test('rejects invalid runtime mode types and values', () {
      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.macOS,
          targetArchitecture: Architecture.arm64,
          runtimeMode: true,
        ),
        throwsA(isA<BuildError>()),
      );
      expect(
        () => resolveFonixBuildOptions(
          targetOS: OS.macOS,
          targetArchitecture: Architecture.arm64,
          runtimeMode: 'latest',
        ),
        throwsA(isA<BuildError>()),
      );
    });
  });

  group('native source discovery', () {
    late Directory temporaryDirectory;

    setUp(() {
      temporaryDirectory = Directory.systemTemp.createTempSync(
        'fonix-hook-test-',
      );
      Directory.fromUri(temporaryDirectory.uri.resolve('src/')).createSync();
    });

    tearDown(() {
      temporaryDirectory.deleteSync(recursive: true);
    });

    test('discovers sorted direct regular C files only', () {
      final sourceDirectory = Directory.fromUri(
        temporaryDirectory.uri.resolve('src/'),
      );
      File.fromUri(sourceDirectory.uri.resolve('zeta.c')).writeAsStringSync('');
      File.fromUri(
        sourceDirectory.uri.resolve('alpha.c'),
      ).writeAsStringSync('');
      File.fromUri(
        sourceDirectory.uri.resolve('ignored.cc'),
      ).writeAsStringSync('');
      final nested = Directory.fromUri(sourceDirectory.uri.resolve('nested/'))
        ..createSync();
      File.fromUri(nested.uri.resolve('nested.c')).writeAsStringSync('');

      if (!Platform.isWindows) {
        Link.fromUri(
          sourceDirectory.uri.resolve('linked.c'),
        ).createSync(sourceDirectory.uri.resolve('alpha.c').toFilePath());
      }

      expect(
        discoverFonixNativeSources(temporaryDirectory.uri),
        equals(<String>['src/alpha.c', 'src/zeta.c']),
      );
    });

    test('rejects an empty source directory', () {
      expect(
        () => discoverFonixNativeSources(temporaryDirectory.uri),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            contains('no regular src/*.c files'),
          ),
        ),
      );
    });
  });

  group('native input validation', () {
    late Directory temporaryDirectory;

    setUp(() {
      temporaryDirectory = Directory.systemTemp.createTempSync(
        'fonix-input-test-',
      );
      Directory.fromUri(temporaryDirectory.uri.resolve('src/')).createSync();
      Directory.fromUri(
        temporaryDirectory.uri.resolve('third_party/onnxruntime/include/'),
      ).createSync(recursive: true);
      File.fromUri(
        temporaryDirectory.uri.resolve('src/dort.h'),
      ).writeAsStringSync('');
      File.fromUri(
        temporaryDirectory.uri.resolve('src/dort_core.c'),
      ).writeAsStringSync('');
      File.fromUri(
        temporaryDirectory.uri.resolve('src/fonix_exports.apple'),
      ).writeAsStringSync('');
      File.fromUri(
        temporaryDirectory.uri.resolve(fonixElfExportMapPath),
      ).writeAsStringSync('$fonixElfExportVersion { local: *; };\n');
      File.fromUri(
        temporaryDirectory.uri.resolve(
          'third_party/onnxruntime/include/onnxruntime_ep_c_api.h',
        ),
      ).writeAsStringSync('');
    });

    tearDown(() {
      temporaryDirectory.deleteSync(recursive: true);
    });

    test('requires the exact API-27 header', () {
      final ortHeader = File.fromUri(
        temporaryDirectory.uri.resolve(
          'third_party/onnxruntime/include/onnxruntime_c_api.h',
        ),
      );
      ortHeader.writeAsStringSync('#define ORT_API_VERSION 26\n');

      expect(
        () => validateFonixNativeInputs(temporaryDirectory.uri, <String>[
          'src/dort_core.c',
        ]),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            allOf(contains('requires exact'), contains('API 26')),
          ),
        ),
      );

      ortHeader.writeAsStringSync('#define ORT_API_VERSION 27\n');
      expect(
        validateFonixNativeInputs(temporaryDirectory.uri, <String>[
          'src/dort_core.c',
        ]),
        hasLength(6),
      );
    });

    test('tracks the exact Linux ELF version map as a native input', () {
      final ortHeader = File.fromUri(
        temporaryDirectory.uri.resolve(
          'third_party/onnxruntime/include/onnxruntime_c_api.h',
        ),
      )..writeAsStringSync('#define ORT_API_VERSION 27\n');
      expect(ortHeader.existsSync(), isTrue);

      final exportMap = temporaryDirectory.uri.resolve(fonixElfExportMapPath);
      final dependencies = validateFonixNativeInputs(
        temporaryDirectory.uri,
        const <String>['src/dort_core.c'],
      );
      expect(dependencies, contains(exportMap));

      File.fromUri(exportMap).deleteSync();
      expect(
        () => validateFonixNativeInputs(temporaryDirectory.uri, const <String>[
          'src/dort_core.c',
        ]),
        throwsA(
          isA<BuildError>().having(
            (error) => error.message,
            'message',
            contains(fonixElfExportMapPath),
          ),
        ),
      );
    });
  });

  group('builder', () {
    test('uses strict C11 external-shim settings without ORT linkage', () {
      final options = resolveFonixBuildOptions(
        targetOS: OS.android,
        targetArchitecture: Architecture.arm64,
        targetAndroidNdkApi: 24,
        androidRuntimeOwner: fonixAndroidSherpaRuntimeOwner,
      );
      final builder = createFonixShimBuilder(options, const <String>[
        'src/dort_core.c',
      ]);

      expect(builder.name, fonixShimName);
      expect(builder.assetName, fonixShimName);
      expect(builder.packageName, fonixPackageName);
      expect(builder.std, 'c11');
      expect(builder.language, Language.c);
      expect(builder.linkModePreference, LinkModePreference.dynamic);
      expect(
        fonixPinnedAppleDeploymentFlags(options, lockedMinimumOs: '0.0'),
        isEmpty,
      );
      expect(builder.flags, contains('-Werror'));
      expect(builder.flags, contains('-fvisibility=hidden'));
      expect(builder.flags, contains('-fstack-protector-strong'));
      expect(builder.flags, contains('-pthread'));
      expect(builder.flags, contains('-Wl,-z,max-page-size=16384'));
      expect(builder.flags, contains('-Wl,-soname,libfonix_shim.so'));
      expect(builder.flags, isNot(contains('-Wl,--build-id=sha1')));
      expect(builder.libraries, equals(<String>['dl']));
      expect(builder.libraries, isNot(contains('onnxruntime')));
      expect(builder.defines['FONIX_SHIM_BUILDING'], '1');
      expect(builder.defines['FONIX_RUNTIME_PROFILE_EXTERNAL'], '1');
      expect(builder.defines['FONIX_RUNTIME_PROFILE'], '"external"');
      expect(builder.defines['FONIX_ANDROID_RUNTIME_OWNER'], '"sherpa"');
      expect(
        builder.defines['FONIX_ANDROID_ALLOWED_RUNTIME_SOURCE'],
        '"process"',
      );
      expect(
        builder.defines['FONIX_SHIM_BUILD_ID'],
        '"android-owner-sherpa-source-process"',
      );
      final encodedBuildId = builder.defines['FONIX_SHIM_BUILD_ID']!;
      final parsedBuildInfo = runtime_info_testing.parseOrtNativeBuildInfo(
        jsonEncode(<String, Object?>{
          'schemaVersion': 3,
          'nativeIdentity': 'fonix_shim',
          'shimAbiVersion': 1,
          'requiredOrtApiVersion': 27,
          'runtimeProfile': fonixExternalRuntimeMode,
          'androidRuntimeOwner': fonixAndroidSherpaRuntimeOwner,
          'allowedRuntimeSources': <String>['process'],
          'buildId': encodedBuildId.substring(1, encodedBuildId.length - 1),
          'artifact': null,
        }),
      );
      expect(parsedBuildInfo.buildId, 'android-owner-sherpa-source-process');
      expect(builder.defines['_GNU_SOURCE'], '1');
    });

    test('application ownership build identity passes the native parser', () {
      final options = resolveFonixBuildOptions(
        targetOS: OS.android,
        targetArchitecture: Architecture.x64,
        targetAndroidNdkApi: 24,
        androidRuntimeOwner: fonixAndroidApplicationRuntimeOwner,
      );
      const artifactId = 'onnxruntime-1.27.1-android-x86_64-cpu';
      final defines = fonixAndroidOwnershipDefines(
        options,
        artifactId: artifactId,
      );
      final encodedBuildId = defines['FONIX_SHIM_BUILD_ID']!;
      final buildId = encodedBuildId.substring(1, encodedBuildId.length - 1);
      final parsedBuildInfo = runtime_info_testing.parseOrtNativeBuildInfo(
        jsonEncode(<String, Object?>{
          'schemaVersion': 3,
          'nativeIdentity': 'fonix_shim',
          'shimAbiVersion': 1,
          'requiredOrtApiVersion': 27,
          'runtimeProfile': fonixBundledRuntimeMode,
          'androidRuntimeOwner': fonixAndroidApplicationRuntimeOwner,
          'allowedRuntimeSources': <String>['bundled'],
          'buildId': buildId,
          'artifact': <String, Object?>{
            'id': artifactId,
            'lockSha256': List<String>.filled(64, 'a').join(),
            'sourceSha256': List<String>.filled(64, 'b').join(),
            'targetOs': 'android',
            'targetArchitecture': 'x86_64',
            'targetVariant': 'default',
            'minimumOs': '24',
            'flavor': 'cpu',
            'runtimeMode': fonixBundledRuntimeMode,
            'thirdPartyNoticesSha256': List<String>.filled(64, 'c').join(),
            'providers': <Object?>[
              <String, Object?>{
                'wrapperId': 'cpu',
                'reportedName': 'CPUExecutionProvider',
              },
            ],
          },
        }),
      );
      expect(
        parsedBuildInfo.buildId,
        'android-owner-application-source-bundled-artifact-$artifactId',
      );
      expect(
        () => fonixAndroidOwnershipDefines(
          options,
          artifactId: 'unsafe=artifact',
        ),
        throwsStateError,
      );
    });

    test('uses MSVC warnings and exact Windows system libraries', () {
      final builder = createFonixShimBuilder(
        resolveFonixBuildOptions(
          targetOS: OS.windows,
          targetArchitecture: Architecture.x64,
        ),
        const <String>['src/dort_core.c'],
      );

      expect(builder.flags, containsAll(<String>['/W4', '/WX', '/utf-8']));
      expect(builder.flags, isNot(contains('-fvisibility=hidden')));
      expect(builder.flags, isNot(contains('-Wl,--build-id=sha1')));
      expect(builder.libraries, <String>['bcrypt', 'advapi32']);
      expect(builder.libraries, isNot(contains('dl')));
    });

    test('selects the GNU version script only for Linux', () {
      final packageRoot = Directory.current.uri;
      final linuxFlags = fonixLinuxVersionScriptFlags(OS.linux, packageRoot);
      final linuxOptions = resolveFonixBuildOptions(
        targetOS: OS.linux,
        targetArchitecture: Architecture.x64,
        runtimeMode: fonixBundledRuntimeMode,
        applicationMinimumOs: '14.0',
      );

      expect(linuxFlags, hasLength(2));
      expect(linuxFlags.first, '-Xlinker');
      expect(
        linuxFlags.last,
        '--version-script='
        '${packageRoot.resolve(fonixElfExportMapPath).toFilePath()}',
      );
      expect(fonixCompilerFlags(OS.linux), contains('-Wl,--build-id=sha1'));
      expect(fonixCompilerFlags(OS.linux), contains('-fuse-ld=lld'));
      expect(
        fonixPinnedAppleDeploymentFlags(
          linuxOptions,
          lockedMinimumOs: 'glibc-2.27',
        ),
        isEmpty,
        reason:
            'a shared Apple application floor must not parse the Linux lock floor',
      );
      for (final targetOS in <OS>[OS.android, OS.iOS, OS.macOS, OS.windows]) {
        expect(
          fonixLinuxVersionScriptFlags(targetOS, packageRoot),
          isEmpty,
          reason: '${targetOS.name} link behavior must remain unchanged',
        );
        expect(
          fonixCompilerFlags(targetOS),
          isNot(contains('-fuse-ld=lld')),
          reason: '${targetOS.name} linker selection must remain unchanged',
        );
      }
    });

    test('keeps commas in the Linux version-script path opaque', () {
      final packageRoot = Uri.directory('/private/tmp/fonix,workspace/');

      expect(fonixLinuxVersionScriptFlags(OS.linux, packageRoot), <String>[
        '-Xlinker',
        '--version-script='
            '${packageRoot.resolve(fonixElfExportMapPath).toFilePath()}',
      ]);
    });

    test('compiles the bundled shim for the exact locked macOS tuple', () {
      final options = resolveFonixBuildOptions(
        targetOS: OS.macOS,
        targetArchitecture: Architecture.arm64,
        runtimeMode: fonixBundledRuntimeMode,
        applicationMinimumOs: '14.0',
      );
      final builder = createFonixShimBuilder(options, const <String>[
        'src/dort_core.c',
      ]);

      expect(builder.defines['FONIX_RUNTIME_PROFILE_BUNDLED'], '1');
      expect(builder.defines['FONIX_RUNTIME_PROFILE'], '"bundled"');
      expect(
        builder.defines,
        isNot(contains('FONIX_RUNTIME_PROFILE_EXTERNAL')),
      );
      expect(builder.libraries, isEmpty);
      expect(builder.linkModePreference, LinkModePreference.dynamic);
      expect(
        fonixPinnedAppleDeploymentFlags(options, lockedMinimumOs: '14.0'),
        <String>['-mmacos-version-min=14.0'],
      );
    });
  });

  test('build hook emits the bundled shim asset and declares inputs', () async {
    await testCodeBuildHook(
      mainMethod: build_hook.main,
      check: (input, output) {
        final asset = output.assets.code.single;
        expect(asset.id, fonixShimAssetId);
        expect(asset.linkMode, DynamicLoadingBundled());
        expect(asset.file, isNotNull);
        expect(File.fromUri(asset.file!).existsSync(), isTrue);

        final sources = discoverFonixNativeSources(input.packageRoot);
        final requiredDependencies = validateFonixNativeInputs(
          input.packageRoot,
          sources,
        );
        expect(output.dependencies, containsAll(requiredDependencies));
      },
    );
  });

  test(
    'bundled hook emits the exact offline macOS ORT code asset',
    () async {
      final mirrorDirectory = Directory(realMacosOrtMirror!);
      final userDefines = PackageUserDefines(
        workspacePubspec: PackageUserDefinesSource(
          defines: <String, Object?>{
            fonixRuntimeModeUserDefine: fonixBundledRuntimeMode,
            fonixArtifactMirrorUserDefine: mirrorDirectory.path,
            fonixApplicationMinimumOsUserDefine: '14.0',
          },
          basePath: Directory.current.uri,
        ),
      );

      await testCodeBuildHook(
        mainMethod: build_hook.main,
        targetOS: OS.macOS,
        targetArchitecture: Architecture.arm64,
        // Flutter's code-assets layer currently supplies 13 independently of
        // the app project. Fonix must still compile against the locked 14.0
        // runtime floor and audit the final app separately.
        targetMacOSVersion: 13,
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
          final runtimeAsset = assets[fonixOrtRuntimeAssetId]!;
          expect(runtimeAsset.linkMode, DynamicLoadingBundled());
          expect(runtimeAsset.file, isNotNull);
          expect(
            File.fromUri(runtimeAsset.file!).uri.pathSegments.last,
            fonixMacosOrtBundleFileName,
          );
          expect(File.fromUri(runtimeAsset.file!).existsSync(), isTrue);
          final shimFile = File.fromUri(assets[fonixShimAssetId]!.file!);
          final deploymentCheck = Process.runSync('/usr/bin/otool', <String>[
            '-l',
            shimFile.path,
          ]);
          expect(
            deploymentCheck.exitCode,
            0,
            reason: '${deploymentCheck.stderr}',
          );
          expect(
            '${deploymentCheck.stdout}',
            matches(RegExp(r'\bminos 14\.0\b')),
          );
          expect(
            File.fromUri(
              runtimeAsset.file!.resolve(fonixMacosOrtManifestFileName),
            ).existsSync(),
            isTrue,
          );
          expect(
            output.dependencies,
            contains(input.packageRoot.resolve('native/versions.lock.yaml')),
          );
          expect(
            output.dependencies,
            contains(mirrorDirectory.uri.resolve(fonixMacosOrtArchiveName)),
          );

          final adjacent = Directory.systemTemp.createTempSync(
            'fonix-bundled-open-',
          );
          try {
            final copiedShim = File.fromUri(
              assets[fonixShimAssetId]!.file!,
            ).copySync('${adjacent.path}/libfonix_shim.dylib');
            File.fromUri(
              runtimeAsset.file!,
            ).copySync('${adjacent.path}/$fonixMacosOrtBundleFileName');
            final nativeApi = FonixNativeApi.dynamicLibrary(
              DynamicLibrary.open(copiedShim.path),
            );
            final runtime = runtime_testing
                .openOrtRuntimeWithNativeApiForTesting(
                  nativeApi: nativeApi,
                  source: const OrtRuntimeSource.bundled(),
                  logId: 'bundled-hook-test',
                );
            OrtSession? session;
            OrtTensor? inputTensor;
            OrtRunResult? result;
            try {
              expect(
                runtime.buildInfo.runtimeProfile,
                OrtRuntimeProfile.bundled,
              );
              expect(
                runtime.buildInfo.artifact?.id,
                'onnxruntime-1.27.1-macos-arm64-cpu',
              );
              expect(runtime.buildInfo.buildId, runtime.buildInfo.artifact?.id);
              expect(runtime.buildInfo.artifact?.minimumOs, '14.0');
              expect(
                runtime.buildInfo.artifact?.sourceSha256,
                'e42b77a7281cc6e55141bf44fcfbac2c782b823a491bbb6ac33c781dd991f8a6',
              );
              expect(
                runtime.buildInfo.artifact?.thirdPartyNoticesSha256,
                '0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2',
              );
              expect(runtime.buildInfo.artifact?.providers, <String, String?>{
                'cpu': 'CPUExecutionProvider',
                'coreml': 'CoreMLExecutionProvider',
                'webgpu': 'WebGpuExecutionProvider',
              });
              expect(runtime.info.runtimeSource, OrtRuntimeSourceKind.bundled);
              expect(runtime.info.runtimeVersion, '1.27.1');
              session = OrtSession.fromBytes(
                runtime: runtime,
                modelBytes: File('test/fixtures/mul_1.onnx').readAsBytesSync(),
              );
              inputTensor = OrtTensor.fromFloat32List(
                runtime: runtime,
                values: Float32List.fromList(<double>[1, 2, 3, 4, 5, 6]),
                shape: const <int>[3, 2],
              );
              result = session.run(
                inputs: <String, OrtTensor>{'X': inputTensor},
              );
              expect(result.tensor('Y').copyFloat32Data(), <double>[
                1,
                4,
                9,
                16,
                25,
                36,
              ]);
            } finally {
              result?.dispose();
              inputTensor?.dispose();
              session?.dispose();
              runtime.dispose();
            }
          } finally {
            adjacent.deleteSync(recursive: true);
          }
        },
      );
    },
    skip: !Platform.isMacOS
        ? 'Requires a macOS arm64 host toolchain.'
        : realMacosOrtMirror == null
        ? 'Set FONIX_TEST_MACOS_ORT_ARCHIVE_DIR to the verified archive directory.'
        : false,
  );

  test(
    'linked hook builds iOS device and simulator shims from pinned ORT',
    () async {
      final cacheDirectory = Directory(officialArtifactCache!);
      final userDefines = PackageUserDefines(
        workspacePubspec: PackageUserDefinesSource(
          defines: <String, Object?>{
            fonixArtifactCacheUserDefine: cacheDirectory.path,
            fonixApplicationMinimumOsUserDefine: '15.1',
          },
          basePath: Directory.current.uri,
        ),
      );

      for (final sdk in <IOSSdk>[IOSSdk.iPhoneOS, IOSSdk.iPhoneSimulator]) {
        await testCodeBuildHook(
          mainMethod: build_hook.main,
          targetOS: OS.iOS,
          targetArchitecture: Architecture.arm64,
          targetIOSSdk: sdk,
          // Reproduce Flutter's current fixed code-assets target value. The
          // emitted shim must still use the exact locked 15.1 floor.
          targetIOSVersion: 13,
          userDefines: userDefines,
          check: (input, output) {
            expect(output.assets.code, hasLength(1));
            final shim = output.assets.code.single;
            expect(shim.id, fonixShimAssetId);
            expect(shim.linkMode, DynamicLoadingBundled());
            expect(shim.file, isNotNull);
            final shimFile = File.fromUri(shim.file!);
            expect(shimFile.existsSync(), isTrue);
            final deploymentCheck = Process.runSync('/usr/bin/otool', <String>[
              '-l',
              shimFile.path,
            ]);
            expect(
              deploymentCheck.exitCode,
              0,
              reason: '${deploymentCheck.stderr}',
            );
            expect(
              '${deploymentCheck.stdout}',
              matches(RegExp(r'\bminos 15\.1\b')),
            );
            final embeddedIdentity = Process.runSync(
              '/usr/bin/strings',
              <String>[shimFile.path],
            );
            expect(
              embeddedIdentity.exitCode,
              0,
              reason: '${embeddedIdentity.stderr}',
            );
            expect(
              '${embeddedIdentity.stdout}',
              contains(
                sdk == IOSSdk.iPhoneSimulator
                    ? 'onnxruntime-1.27.1-ios-arm64-simulator-cpu'
                    : 'onnxruntime-1.27.1-ios-arm64-device-cpu',
              ),
            );
            expect(
              '${embeddedIdentity.stdout}',
              contains(
                'fb0af774b4d7cffc5b9d046f2aaeade2f37df2f80abf8033c95dfffcc77a8866',
              ),
            );
            expect(
              output.assets.code.map((asset) => asset.id),
              isNot(contains(fonixOrtRuntimeAssetId)),
            );
            expect(
              output.dependencies.any(
                (dependency) =>
                    dependency.pathSegments.last ==
                    'microsoft.ml.onnxruntime.1.27.1.nupkg',
              ),
              isTrue,
            );

            final unresolved = Process.runSync('/usr/bin/nm', <String>[
              '-u',
              shimFile.path,
            ]);
            expect(unresolved.exitCode, 0, reason: '${unresolved.stderr}');
            expect('${unresolved.stdout}', isNot(contains('OrtGetApiBase')));
            final exportCheck = Process.runSync('python3', <String>[
              'test/native/check_exports.py',
              shimFile.path,
            ]);
            expect(
              exportCheck.exitCode,
              0,
              reason: '${exportCheck.stdout}\n${exportCheck.stderr}',
            );
          },
        );
      }
    },
    skip: !Platform.isMacOS
        ? 'Requires Apple device and simulator toolchains.'
        : officialArtifactCache == null
        ? 'Set FONIX_ORT_ARTIFACT_CACHE to the verified archive directory.'
        : false,
  );
}
