import 'dart:convert';
import 'dart:io';

import 'package:code_assets/code_assets.dart';
import 'package:hooks/hooks.dart';
import 'package:test/test.dart';

import '../hook/build.dart' as build_hook;
import '../hook/src/build_config.dart';

void main() {
  final artifactCache = Platform.environment['FONIX_ORT_ARTIFACT_CACHE'];
  final zigPath = Platform.environment['FONIX_TEST_ZIG'];
  final enabled =
      Platform.isMacOS &&
      artifactCache != null &&
      zigPath != null &&
      File(zigPath).existsSync();

  test(
    'Zig cross-builds emitted Linux x64/arm64 and Windows x64 bundles',
    () async {
      final temporaryDirectory = Directory.systemTemp.createTempSync(
        'fonix-zig-hook-cross-build-',
      );
      addTearDown(() => temporaryDirectory.deleteSync(recursive: true));
      final wrapper = _createZigClangWrapper(
        temporaryDirectory,
        File(zigPath!),
      );
      final userDefines = PackageUserDefines(
        workspacePubspec: PackageUserDefinesSource(
          defines: <String, Object?>{
            fonixRuntimeModeUserDefine: fonixBundledRuntimeMode,
            fonixArtifactCacheUserDefine: artifactCache,
          },
          basePath: Directory.current.uri,
        ),
      );

      for (final target in <_CrossTarget>[
        const _CrossTarget(
          auditTarget: 'linux-x64',
          operatingSystem: OS.linux,
          architecture: Architecture.x64,
          expectedMachine: 'elf64-x86-64',
          expectedRuntime: 'libonnxruntime.so.1',
          expectedProvider: 'libonnxruntime_providers_shared.so',
        ),
        const _CrossTarget(
          auditTarget: 'linux-arm64',
          operatingSystem: OS.linux,
          architecture: Architecture.arm64,
          expectedMachine: 'elf64-littleaarch64',
          expectedRuntime: 'libonnxruntime.so.1',
          expectedProvider: 'libonnxruntime_providers_shared.so',
        ),
        const _CrossTarget(
          auditTarget: 'windows-x64',
          operatingSystem: OS.windows,
          architecture: Architecture.x64,
          expectedMachine: 'coff-x86-64',
          expectedRuntime: 'onnxruntime.dll',
          expectedProvider: 'onnxruntime_providers_shared.dll',
        ),
      ]) {
        final compiler = CCompilerConfig(
          compiler: wrapper.uri,
          linker: Uri.file('/usr/bin/ld'),
          archiver: Uri.file('/usr/bin/ar'),
          windows: target.operatingSystem == OS.windows
              ? WindowsCCompilerConfig()
              : null,
        );
        await testCodeBuildHook(
          mainMethod: build_hook.main,
          targetOS: target.operatingSystem,
          targetArchitecture: target.architecture,
          cCompiler: compiler,
          userDefines: userDefines,
          check: (input, output) {
            expect(output.assets.code, hasLength(3));
            final assets = <String, CodeAsset>{
              for (final asset in output.assets.code) asset.id: asset,
            };
            expect(
              assets.keys,
              equals(<String>{
                fonixShimAssetId,
                fonixOrtRuntimeAssetId,
                fonixOrtProviderSharedAssetId,
              }),
            );
            final shim = File.fromUri(assets[fonixShimAssetId]!.file!);
            final runtime = File.fromUri(assets[fonixOrtRuntimeAssetId]!.file!);
            final provider = File.fromUri(
              assets[fonixOrtProviderSharedAssetId]!.file!,
            );
            expect(runtime.uri.pathSegments.last, target.expectedRuntime);
            expect(provider.uri.pathSegments.last, target.expectedProvider);
            expect(runtime.parent.path, provider.parent.path);
            expect(
              File.fromUri(
                runtime.parent.uri.resolve(
                  'fonix-native-artifact-manifest.json',
                ),
              ).existsSync(),
              isTrue,
            );
            expect(
              File.fromUri(
                runtime.parent.uri.resolve('notices/LICENSE'),
              ).existsSync(),
              isTrue,
            );
            expect(
              File.fromUri(
                runtime.parent.uri.resolve('notices/ThirdPartyNotices.txt'),
              ).existsSync(),
              isTrue,
            );
            _auditCrossBuiltShim(shim, target);

            final bundleDirectory = Directory(
              '${temporaryDirectory.path}/bundle-${target.auditTarget}',
            )..createSync();
            for (final source in <File>[shim, runtime, provider]) {
              source.copySync(
                '${bundleDirectory.path}/${source.uri.pathSegments.last}',
              );
            }
            File.fromUri(
              runtime.parent.uri.resolve('fonix-native-artifact-manifest.json'),
            ).copySync(
              '${bundleDirectory.path}/fonix-native-artifact-manifest.json',
            );
            final bundleNotices = Directory('${bundleDirectory.path}/notices')
              ..createSync();
            for (final name in <String>['LICENSE', 'ThirdPartyNotices.txt']) {
              File.fromUri(
                runtime.parent.uri.resolve('notices/$name'),
              ).copySync('${bundleNotices.path}/$name');
            }
            final finalAudit = Process.runSync('python3', <String>[
              'tool/ci/audit_desktop_bundle.py',
              '--bundle',
              bundleDirectory.path,
              '--target',
              target.auditTarget,
              '--objdump',
              '/usr/bin/objdump',
              '--lock',
              'native/versions.lock.yaml',
            ]);
            expect(
              finalAudit.exitCode,
              0,
              reason: '${finalAudit.stdout}\n${finalAudit.stderr}',
            );
          },
        );
      }
      expect(
        File('${Directory.current.path}/a.o').existsSync(),
        isFalse,
        reason: 'compiler probing must not contaminate the source root',
      );
    },
    skip: enabled
        ? false
        : 'Requires macOS, FONIX_ORT_ARTIFACT_CACHE, and FONIX_TEST_ZIG.',
  );
}

File _createZigClangWrapper(Directory root, File zig) {
  final wrapperDirectory = Directory('${root.path}/wrapper')..createSync();
  final wrapper = File('${wrapperDirectory.path}/clang');
  final globalCache = '${root.path}/zig-global-cache';
  final localCache = '${root.path}/zig-local-cache';
  wrapper.writeAsStringSync('''#!/usr/bin/env python3
import os
import sys

zig = ${jsonEncode(zig.path)}
if sys.argv[1:] == ["--version"]:
    os.execv(zig, (zig, "version"))
targets = {
    "--target=x86_64-linux-gnu": "x86_64-linux-gnu.2.27",
    "--target=aarch64-linux-gnu": "aarch64-linux-gnu.2.27",
    "--target=x86_64-pc-windows-msvc": "x86_64-windows-gnu",
}
arguments = []
for argument in sys.argv[1:]:
    target = targets.get(argument)
    if target is not None:
        arguments.extend(("-target", target))
    elif argument == "/W4":
        arguments.extend(("-Wall", "-Wextra"))
    elif argument == "/WX":
        arguments.append("-Werror")
    elif argument == "/utf-8":
        arguments.append("-finput-charset=UTF-8")
    else:
        arguments.append(argument)
os.environ["ZIG_GLOBAL_CACHE_DIR"] = ${jsonEncode(globalCache)}
os.environ["ZIG_LOCAL_CACHE_DIR"] = ${jsonEncode(localCache)}
os.execv(zig, (zig, "cc", *arguments))
''');
  final chmod = Process.runSync('/bin/chmod', <String>['0700', wrapper.path]);
  if (chmod.exitCode != 0) {
    throw StateError('Could not make the Zig compiler wrapper executable.');
  }
  return wrapper;
}

void _auditCrossBuiltShim(File shim, _CrossTarget target) {
  expect(shim.existsSync(), isTrue);
  final report = Process.runSync('/usr/bin/objdump', <String>['-p', shim.path]);
  expect(report.exitCode, 0, reason: '${report.stdout}\n${report.stderr}');
  final output = '${report.stdout}';
  expect(output.toLowerCase(), contains(target.expectedMachine.toLowerCase()));
  expect(output.toLowerCase(), isNot(contains('onnxruntime')));
  if (target.operatingSystem == OS.linux) {
    expect(RegExp(r'SONAME\s+libfonix_shim\.so').hasMatch(output), isTrue);
    expect(RegExp(r'RUNPATH\s+\$ORIGIN').hasMatch(output), isTrue);
  } else {
    expect(output, contains('DLL Name: KERNEL32.dll'));
    expect(output.toLowerCase(), contains('dll name: bcrypt.dll'));
    expect(output.toLowerCase(), contains('dll name: advapi32.dll'));
    expect(output, contains('dort_get_abi_version'));
    expect(output, contains('dort_get_build_manifest_json'));
  }
}

final class _CrossTarget {
  const _CrossTarget({
    required this.auditTarget,
    required this.operatingSystem,
    required this.architecture,
    required this.expectedMachine,
    required this.expectedRuntime,
    required this.expectedProvider,
  });

  final String auditTarget;
  final OS operatingSystem;
  final Architecture architecture;
  final String expectedMachine;
  final String expectedRuntime;
  final String expectedProvider;
}
