import 'dart:io';

import 'package:code_assets/code_assets.dart';
import 'package:test/test.dart';

import '../hook/build.dart' as build_hook;
import '../hook/src/build_config.dart';

void main() {
  final expectedExports = _expectedHeaderExports();

  test('ELF version map is a closed 67-symbol public ABI contract', () {
    final map = File(fonixElfExportMapPath).readAsStringSync();
    final match = RegExp(
      r'^FONIX_DORT_1\.0 \{\n'
      r'  global:\n'
      r'((?:    dort_[a-z0-9_]+;\n)+)'
      r'  local:\n'
      r'    \*;\n'
      r'\};\n$',
    ).firstMatch(map);
    expect(match, isNotNull, reason: 'the ELF version map must stay closed');

    final mapped = RegExp(
      r'^    (dort_[a-z0-9_]+);$',
      multiLine: true,
    ).allMatches(match!.group(1)!).map((entry) => entry.group(1)!).toList();
    expect(mapped, hasLength(67));
    expect(mapped.toSet(), hasLength(67));
    expect(mapped.toSet(), expectedExports);

    final appleExports = File(
      'src/fonix_exports.apple',
    ).readAsLinesSync().map((symbol) => symbol.substring(1)).toSet();
    final windowsExports = RegExp(r'^  (dort_[a-z0-9_]+)$', multiLine: true)
        .allMatches(File('src/fonix_shim.def').readAsStringSync())
        .map((entry) => entry.group(1)!)
        .toSet();
    expect(appleExports, expectedExports);
    expect(windowsExports, expectedExports);
  });

  test('standalone CMake keeps the Linux version-map path opaque', () {
    final source = File('src/CMakeLists.txt').readAsStringSync();
    expect(
      source,
      contains(
        '      "-Xlinker"\n'
        r'      "--version-script=${CMAKE_CURRENT_LIST_DIR}/fonix_exports.map"',
      ),
    );
    expect(source, isNot(contains('-Wl,--version-script=')));
  });

  test('GNU readelf column heading is not a dynamic symbol', () {
    final output = StringBuffer(
      '  Num:    Value          Size Type    Bind   Vis      Ndx Name\n'
      '    0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND\n'
      '    1: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  ABS '
      '$fonixElfExportVersion\n',
    );
    var index = 2;
    for (final symbol in expectedExports) {
      output.writeln(
        '    ${index++}: 0000000000001000 12 FUNC GLOBAL DEFAULT 11 '
        '$symbol@@$fonixElfExportVersion',
      );
    }
    expect(
      _definedGlobalDynamicSymbols(output.toString()).keys.toSet(),
      expectedExports,
    );
  });

  test(
    'native Linux x64 hook emits only FONIX_DORT_1.0 exports',
    () async {
      await testCodeBuildHook(
        mainMethod: build_hook.main,
        targetOS: OS.linux,
        targetArchitecture: Architecture.x64,
        check: (input, output) {
          final shim = File.fromUri(output.assets.code.single.file!);
          expect(shim.existsSync(), isTrue);
          expect(
            output.dependencies,
            contains(input.packageRoot.resolve(fonixElfExportMapPath)),
          );

          final readelf = _findExecutable(<String>[
            '/usr/bin/readelf',
            '/bin/readelf',
          ]);
          expect(readelf, isNotNull, reason: 'GNU readelf is required');
          final result = Process.runSync(readelf!, <String>[
            '--wide',
            '--dyn-syms',
            shim.path,
          ]);
          expect(result.exitCode, 0, reason: '${result.stderr}');
          final exports = _definedGlobalDynamicSymbols('${result.stdout}');
          expect(exports.keys.toSet(), expectedExports);
          expect(exports.values.toSet(), <String>{fonixElfExportVersion});
        },
      );
    },
    skip: _isNativeLinuxX64()
        ? false
        : 'Requires a native Linux x86_64 host and GNU readelf.',
  );
}

Set<String> _expectedHeaderExports() {
  final matches = RegExp(
    r'\b(dort_[a-z0-9_]+)\s*\(',
  ).allMatches(File('src/dort.h').readAsStringSync());
  return matches.map((entry) => entry.group(1)!).toSet();
}

Map<String, String> _definedGlobalDynamicSymbols(String output) {
  final exports = <String, String>{};
  var versionDefinitionCount = 0;
  for (final line in output.split('\n')) {
    final fields = line.trim().split(RegExp(r'\s+'));
    if (fields.length < 8 || !RegExp(r'^\d+:$').hasMatch(fields.first)) {
      continue;
    }
    final binding = fields[4];
    final section = fields[6];
    if (binding == 'LOCAL' || section == 'UND') {
      continue;
    }
    final name = fields[7];
    expect(binding, 'GLOBAL', reason: 'unexpected export binding: $line');
    if (name == fonixElfExportVersion) {
      versionDefinitionCount++;
      continue;
    }
    final separator = name.indexOf('@@');
    expect(separator, greaterThan(0), reason: 'unversioned global: $name');
    final symbol = name.substring(0, separator);
    final version = name.substring(separator + 2);
    expect(symbol, startsWith('dort_'), reason: 'stray global: $symbol');
    expect(exports, isNot(contains(symbol)), reason: 'duplicate: $symbol');
    exports[symbol] = version;
  }
  expect(versionDefinitionCount, 1);
  return exports;
}

String? _findExecutable(List<String> candidates) {
  for (final candidate in candidates) {
    final file = File(candidate);
    if (file.existsSync()) {
      return file.path;
    }
  }
  return null;
}

bool _isNativeLinuxX64() {
  if (!Platform.isLinux ||
      _findExecutable(<String>['/usr/bin/readelf']) == null) {
    return false;
  }
  final machine = Process.runSync('uname', const <String>['-m']);
  return machine.exitCode == 0 && '${machine.stdout}'.trim() == 'x86_64';
}
