import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';

const String _flutterRevision = 'bd1e75d918605c91b411e8789fb911e6c9a84534';
const Map<String, String> _pinnedLinuxScaffoldSha256 = <String, String>{
  '.gitignore':
      '5ded4f1a9d10d34bf6ee5beb0711d88ee2ef07b0f07ace9ed77935a246e8eb82',
  'CMakeLists.txt':
      'fe2e9c43b7136f5db54aa6ada23e2f660b39fa7dc4b06a78f2cab405a40c0c8f',
  'flutter/CMakeLists.txt':
      '787189e2623313cfee590a0b553338eb9fc68b3af922ddd77c5d39c75f989aeb',
  'flutter/generated_plugin_registrant.cc':
      '7861b227b10a10cc1d7643f82a246b7db35b503f2d9ff1d3c906136e6c260dce',
  'flutter/generated_plugin_registrant.h':
      '00f93bb5b1faefdede8f6065df327522900fb0b8efee86bacf424fd947ed14bb',
  'flutter/generated_plugins.cmake':
      'd5aaa52ef8594d3128b35b6619c6a353e1be5f3c5a63a9c29c0a32b424e91952',
  'runner/CMakeLists.txt':
      '847322e3be8d14145d91487f25c26c79844f89c314c0c3270b7e6b4e7cb7b231',
  'runner/main.cc':
      'ea23f7cdf74091d63bba29ef60486370b766157b848f0cfe62bd8b1c1695a0c1',
  'runner/my_application.cc':
      '09020e21693c180fc058df3a2811fc62a1cf307e1b2a436f40aa2b2d6656ce89',
  'runner/my_application.h':
      'a4218d7ac0db8a9a6716daf216b2d4fc6bdc7ac647e3183078b2c962ebdfcf70',
};

void main() {
  test(
    'pins the Flutter Linux scaffold and runner overlay to one revision',
    () {
      final String metadata = File('.metadata').readAsStringSync();
      expect(metadata, contains('revision: "$_flutterRevision"'));
      expect(
        metadata,
        contains(
          '    - platform: linux\n'
          '      create_revision: $_flutterRevision\n'
          '      base_revision: $_flutterRevision\n',
        ),
      );
      expect(metadata, contains("    - 'linux/CMakeLists.txt'"));
      expect(metadata, contains("    - 'linux/runner/CMakeLists.txt'"));

      final Directory root = Directory('linux');
      final Map<String, File> files = <String, File>{};
      for (final FileSystemEntity entity in root.listSync(
        recursive: true,
        followLinks: false,
      )) {
        final FileSystemEntityType type = FileSystemEntity.typeSync(
          entity.path,
          followLinks: false,
        );
        expect(
          type,
          isIn(<FileSystemEntityType>[
            FileSystemEntityType.directory,
            FileSystemEntityType.file,
          ]),
          reason: entity.path,
        );
        if (type == FileSystemEntityType.file) {
          final String relative = entity.path
              .substring(root.path.length + 1)
              .replaceAll(Platform.pathSeparator, '/');
          files[relative] = File(entity.path);
        }
      }

      expect(files.keys.toSet(), _pinnedLinuxScaffoldSha256.keys.toSet());
      for (final MapEntry<String, String> expected
          in _pinnedLinuxScaffoldSha256.entries) {
        expect(
          sha256.convert(files[expected.key]!.readAsBytesSync()).toString(),
          expected.value,
          reason: expected.key,
        );
      }
    },
  );

  test('installs one relocatable Linux bundle with package native assets', () {
    final String pubspec = File('pubspec.yaml').readAsStringSync();
    expect(pubspec, contains('runtime_mode: bundled'));
    expect(pubspec, contains('artifact_cache: .fonix-artifact-cache'));
    expect(pubspec, contains('assets/models/mul_1.onnx'));
    expect(
      pubspec,
      contains('assets/fonix/fonix-native-artifact-manifest.json'),
    );
    expect(pubspec, contains('assets/fonix/ThirdPartyNotices.txt'));

    final String project = File('linux/CMakeLists.txt').readAsStringSync();
    expect(project, contains('set(BINARY_NAME "fonix_reference")'));
    expect(
      project,
      contains('set(APPLICATION_ID "dev.fonix.fonix_reference")'),
    );
    expect(project, contains(r'set(CMAKE_INSTALL_RPATH "$ORIGIN/lib")'));
    expect(
      project,
      contains(
        'set(NATIVE_ASSETS_DIR '
        r'"${PROJECT_BUILD_DIR}native_assets/linux/")',
      ),
    );
    expect(
      project,
      contains(
        'install(DIRECTORY "\${NATIVE_ASSETS_DIR}"\n'
        '   DESTINATION "\${INSTALL_BUNDLE_LIB_DIR}"',
      ),
    );
    expect(project, isNot(contains('LD_LIBRARY_PATH')));
    expect(project, isNot(contains('CMAKE_SKIP_RPATH')));
    expect(project, isNot(contains('--disable-new-dtags')));

    final String runnerBuild = File(
      'linux/runner/CMakeLists.txt',
    ).readAsStringSync();
    for (final String required in <String>[
      'target_compile_options(\${BINARY_NAME} PRIVATE',
      '-fPIE',
      '-fstack-protector-strong',
      r'$<$<NOT:$<CONFIG:Debug>>:_FORTIFY_SOURCE=2>',
      'target_link_options(\${BINARY_NAME} PRIVATE',
      '-fuse-ld=lld',
      '-pie',
      '-Wl,--enable-new-dtags',
      '-Wl,--build-id=sha1',
      '-Wl,-z,relro',
      '-Wl,-z,now',
      '-Wl,-z,noexecstack',
    ]) {
      expect(runnerBuild, contains(required), reason: required);
    }
    expect(runnerBuild, isNot(contains('LD_LIBRARY_PATH')));
    expect(runnerBuild, isNot(contains('--disable-new-dtags')));

    final String runner = File(
      'linux/runner/my_application.cc',
    ).readAsStringSync();
    expect(runner, contains('G_APPLICATION_NON_UNIQUE'));
    expect(runner, contains('fl_register_plugins(FL_PLUGIN_REGISTRY(view))'));
  });

  test('Linux shares bounded smoke and benchmark one-shot exits', () {
    final String source = File('lib/main.dart').readAsStringSync();
    expect(
      source,
      contains(
        'if ((Platform.isMacOS || Platform.isLinux) &&\n'
        '      desktopReferenceSmokeEnabled(',
      ),
    );
    expect(source, contains('desktopReferenceSmokeEnabled('));
    expect(source, contains('desktopCpuBenchmarkEnabled('));
    expect(source, contains('isMacOS: Platform.isMacOS'));
    expect(source, contains('isLinux: Platform.isLinux'));
    expect(source, contains('environment: Platform.environment'));
    expect(
      source,
      contains(
        r"stdout.writeln('$referenceSmokeReceiptPrefix"
        r"${receipt.toJsonString()}');",
      ),
    );
    expect(
      source,
      contains(
        r"stdout.writeln('$cpuBenchmarkResultPrefix"
        r"${result.toJsonString()}');",
      ),
    );
    expect(
      source,
      contains(
        'await stdout.flush();\n'
        '    await stderr.flush();\n'
        '    exit(status);',
      ),
    );
    expect('exit(status);'.allMatches(source), hasLength(2));
    expect('Platform.environment'.allMatches(source), hasLength(2));
    expect(source, isNot(contains('stderr.writeln(error')));
    expect(source, isNot(contains('stdout.writeln(error')));
  });
}
