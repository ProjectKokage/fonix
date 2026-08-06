import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as path;
import 'package:test/test.dart';

import '../hook/src/native_artifact_auditor.dart';
import '../hook/src/native_artifact_resolver.dart';

const Set<String> _linuxRuntimeDependencies = <String>{
  'libdl.so.2',
  'librt.so.1',
  'libpthread.so.0',
  'libstdc++.so.6',
  'libm.so.6',
  'libgcc_s.so.1',
  'libc.so.6',
  'ld-linux-x86-64.so.2',
};

const Set<String> _linuxProviderDependencies = <String>{
  'libstdc++.so.6',
  'libm.so.6',
  'libgcc_s.so.1',
  'libc.so.6',
};

const Set<String> _androidDependencies = <String>{
  'libdl.so',
  'liblog.so',
  'libandroid.so',
  'libm.so',
  'libc.so',
};

const Set<String> _windowsRuntimeImports = <String>{
  'KERNEL32.dll',
  'ADVAPI32.dll',
  'MSVCP140.dll',
  'MSVCP140_1.dll',
  'api-ms-win-core-path-l1-1-0.dll',
  'dbghelp.dll',
  'SETUPAPI.dll',
  'dxgi.dll',
  'VCRUNTIME140_1.dll',
  'VCRUNTIME140.dll',
  'api-ms-win-crt-heap-l1-1-0.dll',
  'api-ms-win-crt-runtime-l1-1-0.dll',
  'api-ms-win-crt-stdio-l1-1-0.dll',
  'api-ms-win-crt-string-l1-1-0.dll',
  'api-ms-win-crt-time-l1-1-0.dll',
  'api-ms-win-crt-filesystem-l1-1-0.dll',
  'api-ms-win-crt-convert-l1-1-0.dll',
  'api-ms-win-crt-locale-l1-1-0.dll',
  'api-ms-win-crt-math-l1-1-0.dll',
};

const Set<String> _windowsProviderImports = <String>{
  'VCRUNTIME140.dll',
  'api-ms-win-crt-runtime-l1-1-0.dll',
  'KERNEL32.dll',
};

void main() {
  group('portable staged native artifact audit', () {
    test('accepts the closed Linux x64 runtime/provider layout', () {
      final fixture = _AuditFixture.create(
        const NativeArtifactTarget(
          operatingSystem: 'linux',
          architecture: 'x86_64',
          variant: 'default',
        ),
        <String, Uint8List>{
          'libonnxruntime.so.1': _syntheticElf(
            machine: 62,
            soname: 'libonnxruntime.so.1',
            needed: _linuxRuntimeDependencies,
            runpath: r'$ORIGIN',
          ),
          'libonnxruntime_providers_shared.so': _syntheticElf(
            machine: 62,
            soname: 'libonnxruntime_providers_shared.so',
            needed: _linuxProviderDependencies,
          ),
        },
      );
      addTearDown(fixture.dispose);

      final audit = auditStagedNativeArtifact(
        target: fixture.target,
        staged: fixture.staged,
      );

      expect(
        audit.binaries.keys,
        containsAll(<String>[
          'libonnxruntime.so.1',
          'libonnxruntime_providers_shared.so',
        ]),
      );
      expect(audit.binaries['libonnxruntime.so.1']!.format, 'elf64-le');
      expect(
        audit.binaries['libonnxruntime.so.1']!.soname,
        'libonnxruntime.so.1',
      );
    });

    test('accepts the closed Windows x64 runtime/provider layout', () {
      final fixture = _AuditFixture.create(
        const NativeArtifactTarget(
          operatingSystem: 'windows',
          architecture: 'x64',
          variant: 'default',
        ),
        <String, Uint8List>{
          'onnxruntime.dll': _syntheticPe(_windowsRuntimeImports),
          'onnxruntime_providers_shared.dll': _syntheticPe(
            _windowsProviderImports,
          ),
        },
      );
      addTearDown(fixture.dispose);

      final audit = auditStagedNativeArtifact(
        target: fixture.target,
        staged: fixture.staged,
      );

      expect(audit.binaries['onnxruntime.dll']!.format, 'pe32+');
      expect(audit.binaries['onnxruntime.dll']!.machine, 0x8664);
      expect(
        audit.binaries['onnxruntime.dll']!.dependencies,
        equals(_windowsRuntimeImports),
      );
    });

    test('accepts 16 KiB Android arm64 and rejects 4 KiB PT_LOAD', () {
      final target = const NativeArtifactTarget(
        operatingSystem: 'android',
        architecture: 'arm64-v8a',
        variant: 'default',
      );
      final accepted = _AuditFixture.create(target, <String, Uint8List>{
        'libonnxruntime.so': _syntheticElf(
          machine: 183,
          soname: 'libonnxruntime.so',
          needed: _androidDependencies,
          runpath: r'$ORIGIN',
        ),
      });
      addTearDown(accepted.dispose);
      expect(
        auditStagedNativeArtifact(
          target: target,
          staged: accepted.staged,
        ).binaries['libonnxruntime.so']!.android16KiBCompatible,
        isTrue,
      );

      final rejected = _AuditFixture.create(target, <String, Uint8List>{
        'libonnxruntime.so': _syntheticElf(
          machine: 183,
          soname: 'libonnxruntime.so',
          needed: _androidDependencies,
          runpath: r'$ORIGIN',
          alignment: 4096,
        ),
      });
      addTearDown(rejected.dispose);
      expect(
        () =>
            auditStagedNativeArtifact(target: target, staged: rejected.staged),
        throwsA(
          isA<NativeArtifactAuditException>().having(
            (error) => error.message,
            'message',
            contains('16 KiB'),
          ),
        ),
      );
    });

    test('rejects wrong machine, SONAME, dependencies, and PE imports', () {
      final wrongMachine = _linuxFixture(
        runtime: _syntheticElf(
          machine: 183,
          soname: 'libonnxruntime.so.1',
          needed: _linuxRuntimeDependencies,
          runpath: r'$ORIGIN',
        ),
      );
      addTearDown(wrongMachine.dispose);
      expect(
        () => auditStagedNativeArtifact(
          target: wrongMachine.target,
          staged: wrongMachine.staged,
        ),
        throwsA(_auditMessage(contains('object format and machine'))),
      );

      final wrongSoname = _linuxFixture(
        runtime: _syntheticElf(
          machine: 62,
          soname: 'libwrong.so.1',
          needed: _linuxRuntimeDependencies,
          runpath: r'$ORIGIN',
        ),
      );
      addTearDown(wrongSoname.dispose);
      expect(
        () => auditStagedNativeArtifact(
          target: wrongSoname.target,
          staged: wrongSoname.staged,
        ),
        throwsA(_auditMessage(contains('SONAME or RUNPATH'))),
      );

      final wrongDependencies = _linuxFixture(
        runtime: _syntheticElf(
          machine: 62,
          soname: 'libonnxruntime.so.1',
          needed: <String>{..._linuxRuntimeDependencies, 'libsurprise.so'},
          runpath: r'$ORIGIN',
        ),
      );
      addTearDown(wrongDependencies.dispose);
      expect(
        () => auditStagedNativeArtifact(
          target: wrongDependencies.target,
          staged: wrongDependencies.staged,
        ),
        throwsA(_auditMessage(contains('dependency set'))),
      );

      final wrongImports = _AuditFixture.create(
        const NativeArtifactTarget(
          operatingSystem: 'windows',
          architecture: 'x64',
          variant: 'default',
        ),
        <String, Uint8List>{
          'onnxruntime.dll': _syntheticPe(<String>{
            ..._windowsRuntimeImports,
            'surprise.dll',
          }),
          'onnxruntime_providers_shared.dll': _syntheticPe(
            _windowsProviderImports,
          ),
        },
      );
      addTearDown(wrongImports.dispose);
      expect(
        () => auditStagedNativeArtifact(
          target: wrongImports.target,
          staged: wrongImports.staged,
        ),
        throwsA(_auditMessage(contains('dependency set'))),
      );
    });

    test('rejects missing provider, extra files, and manifest hash drift', () {
      final target = const NativeArtifactTarget(
        operatingSystem: 'linux',
        architecture: 'x86_64',
        variant: 'default',
      );
      final missingProvider = _AuditFixture.create(target, <String, Uint8List>{
        'libonnxruntime.so.1': _syntheticElf(
          machine: 62,
          soname: 'libonnxruntime.so.1',
          needed: _linuxRuntimeDependencies,
          runpath: r'$ORIGIN',
        ),
      });
      addTearDown(missingProvider.dispose);
      expect(
        () => auditStagedNativeArtifact(
          target: target,
          staged: missingProvider.staged,
        ),
        throwsA(_auditMessage(contains('payload basenames/layout'))),
      );

      final extraFile = _linuxFixture();
      addTearDown(extraFile.dispose);
      File(
        path.join(extraFile.stagingDirectory.path, 'unexpected.txt'),
      ).writeAsStringSync('unexpected');
      expect(
        () =>
            auditStagedNativeArtifact(target: target, staged: extraFile.staged),
        throwsA(_auditMessage(contains('closed file set'))),
      );

      final hashDrift = _linuxFixture();
      addTearDown(hashDrift.dispose);
      hashDrift.staged.payloadFiles.first.writeAsBytesSync(<int>[1, 2, 3]);
      expect(
        () =>
            auditStagedNativeArtifact(target: target, staged: hashDrift.staged),
        throwsA(_auditMessage(contains('size does not match staged bytes'))),
      );
    });

    test('rejects manifest identity drift with unchanged payload bytes', () {
      final fixture = _linuxFixture();
      addTearDown(fixture.dispose);
      final manifest =
          jsonDecode(fixture.staged.manifestFile.readAsStringSync())
              as Map<String, Object?>;
      (manifest['lock']! as Map<String, Object?>)['sha256'] = 'f' * 64;
      fixture.staged.manifestFile.writeAsStringSync(jsonEncode(manifest));

      expect(
        () => auditStagedNativeArtifact(
          target: fixture.target,
          staged: fixture.staged,
        ),
        throwsA(_auditMessage(contains('lock digest'))),
      );

      final noticeFixture = _linuxFixture();
      addTearDown(noticeFixture.dispose);
      final noticeManifest =
          jsonDecode(noticeFixture.staged.manifestFile.readAsStringSync())
              as Map<String, Object?>;
      final notices = noticeManifest['notices']! as List<Object?>;
      (notices.first! as Map<String, Object?>)['id'] = 'WrongLicense';
      noticeFixture.staged.manifestFile.writeAsStringSync(
        jsonEncode(noticeManifest),
      );
      expect(
        () => auditStagedNativeArtifact(
          target: noticeFixture.target,
          staged: noticeFixture.staged,
        ),
        throwsA(_auditMessage(contains('notice ID'))),
      );
    });

    test('rejects duplicate manifest keys at root and nested levels', () {
      final rootDuplicate = _linuxFixture();
      addTearDown(rootDuplicate.dispose);
      final rootSource = rootDuplicate.staged.manifestFile.readAsStringSync();
      rootDuplicate.staged.manifestFile.writeAsStringSync(
        '${rootSource.substring(0, rootSource.length - 1)},"schema":2}',
      );
      expect(
        () => auditStagedNativeArtifact(
          target: rootDuplicate.target,
          staged: rootDuplicate.staged,
        ),
        throwsA(_auditMessage(contains('duplicate JSON object key'))),
      );

      final nestedDuplicate = _linuxFixture();
      addTearDown(nestedDuplicate.dispose);
      final nestedSource = nestedDuplicate.staged.manifestFile
          .readAsStringSync();
      expect(nestedSource, contains('"os":"linux"'));
      nestedDuplicate.staged.manifestFile.writeAsStringSync(
        nestedSource.replaceFirst(
          '"os":"linux"',
          '"os":"linux","os":"windows"',
        ),
      );
      expect(
        () => auditStagedNativeArtifact(
          target: nestedDuplicate.target,
          staged: nestedDuplicate.staged,
        ),
        throwsA(_auditMessage(contains('duplicate JSON object key'))),
      );
    });
  });

  final officialArtifactCache =
      Platform.environment['FONIX_ORT_ARTIFACT_CACHE'];
  test(
    'audits all five official portable baseline artifacts offline',
    () async {
      final temporaryDirectory = Directory.systemTemp.createTempSync(
        'fonix-official-portable-audit-',
      );
      addTearDown(() => temporaryDirectory.deleteSync(recursive: true));
      final targets = <NativeArtifactTarget>[
        const NativeArtifactTarget(
          operatingSystem: 'linux',
          architecture: 'x86_64',
          variant: 'default',
        ),
        const NativeArtifactTarget(
          operatingSystem: 'linux',
          architecture: 'arm64',
          variant: 'default',
        ),
        const NativeArtifactTarget(
          operatingSystem: 'windows',
          architecture: 'x64',
          variant: 'default',
        ),
        const NativeArtifactTarget(
          operatingSystem: 'android',
          architecture: 'arm64-v8a',
          variant: 'default',
        ),
        const NativeArtifactTarget(
          operatingSystem: 'android',
          architecture: 'x86_64',
          variant: 'default',
        ),
      ];
      for (var index = 0; index < targets.length; index++) {
        final target = targets[index];
        final staged = await const NativeArtifactResolver().stage(
          packageRoot: Directory.current,
          stagingDirectory: Directory(
            path.join(temporaryDirectory.path, 'target-$index'),
          ),
          target: target,
          cacheDirectory: Directory(officialArtifactCache!),
        );

        final audit = auditStagedNativeArtifact(target: target, staged: staged);
        expect(audit.artifactId, staged.artifactId);
        expect(audit.binaries, isNotEmpty);
      }
    },
    skip: officialArtifactCache == null
        ? 'Set FONIX_ORT_ARTIFACT_CACHE to the verified archive directory.'
        : false,
  );
}

Matcher _auditMessage(Matcher message) => isA<NativeArtifactAuditException>()
    .having((error) => error.message, 'message', message);

_AuditFixture _linuxFixture({Uint8List? runtime}) => _AuditFixture.create(
  const NativeArtifactTarget(
    operatingSystem: 'linux',
    architecture: 'x86_64',
    variant: 'default',
  ),
  <String, Uint8List>{
    'libonnxruntime.so.1':
        runtime ??
        _syntheticElf(
          machine: 62,
          soname: 'libonnxruntime.so.1',
          needed: _linuxRuntimeDependencies,
          runpath: r'$ORIGIN',
        ),
    'libonnxruntime_providers_shared.so': _syntheticElf(
      machine: 62,
      soname: 'libonnxruntime_providers_shared.so',
      needed: _linuxProviderDependencies,
    ),
  },
);

final class _AuditFixture {
  _AuditFixture({
    required this.root,
    required this.stagingDirectory,
    required this.target,
    required this.staged,
  });

  final Directory root;
  final Directory stagingDirectory;
  final NativeArtifactTarget target;
  final StagedNativeArtifact staged;

  static _AuditFixture create(
    NativeArtifactTarget target,
    Map<String, Uint8List> payloadBytes,
  ) {
    final root = Directory.systemTemp.createTempSync('fonix-audit-fixture-');
    final stagingDirectory = Directory(path.join(root.path, 'stage'))
      ..createSync();
    final payloads = <File>[];
    for (final entry in payloadBytes.entries) {
      final file = File(path.join(stagingDirectory.path, entry.key));
      file.writeAsBytesSync(entry.value);
      payloads.add(file);
    }
    final noticesDirectory = Directory(
      path.join(stagingDirectory.path, 'notices'),
    )..createSync();
    final noticeBytes = <String, Uint8List>{
      'LICENSE': Uint8List.fromList(utf8.encode('synthetic license\n')),
      'ThirdPartyNotices.txt': Uint8List.fromList(
        utf8.encode('synthetic notices\n'),
      ),
    };
    final notices = <File>[
      for (final entry in noticeBytes.entries)
        File(path.join(noticesDirectory.path, entry.key))
          ..writeAsBytesSync(entry.value),
    ];
    final artifactId =
        'onnxruntime-test-${target.operatingSystem}-${target.architecture}';
    final minimumOs = switch (target.operatingSystem) {
      'android' => '24',
      'linux' => 'glibc-2.27',
      'windows' => '10.0',
      _ => throw StateError('Unsupported fixture target.'),
    };
    final manifest = <String, Object?>{
      'schema': 2,
      'artifactId': artifactId,
      'lock': <String, Object?>{
        'path': 'native/versions.lock.yaml',
        'sha256': '0' * 64,
        'snapshotDate': '2026-08-06',
        'releaseState': 'fixture',
      },
      'target': <String, Object?>{
        'os': target.operatingSystem,
        'architecture': target.architecture,
        'variant': target.variant,
        'minimumOs': minimumOs,
        'flavor': target.flavor,
        'runtimeMode': 'bundled',
      },
      'source': <String, Object?>{
        'url': 'https://example.invalid/fixture.zip',
        'sourceRevision': null,
        'archive': 'zip',
        'sha256': '1' * 64,
        'sizeBytes': 1,
      },
      'containers': <Object?>[],
      'payloadFiles': <Object?>[
        for (final entry in payloadBytes.entries)
          <String, Object?>{
            'archivePath': 'payload/${entry.key}',
            'stagedPath': entry.key,
            'sha256': sha256.convert(entry.value).toString(),
            'sizeBytes': entry.value.length,
          },
      ],
      'verifiedSymlinks': <Object?>[],
      'notices': <Object?>[
        for (final entry in noticeBytes.entries)
          <String, Object?>{
            'id': entry.key == 'LICENSE' ? 'MIT' : 'ThirdPartyNotices',
            'containerDepth': 0,
            'archivePath': entry.key,
            'stagedPath': 'notices/${entry.key}',
            'sha256': sha256.convert(entry.value).toString(),
            'sizeBytes': entry.value.length,
          },
      ],
      'archiveInspections': <Object?>[],
      'claimBoundary': 'Synthetic staging fixture only.',
    };
    final manifestFile = File(
      path.join(stagingDirectory.path, fonixNativeArtifactManifestFileName),
    )..writeAsStringSync(jsonEncode(manifest));
    final archive = File(path.join(root.path, 'fixture.zip'))
      ..writeAsBytesSync(<int>[0]);
    final archiveSha256 = sha256.convert(archive.readAsBytesSync()).toString();
    (manifest['source']! as Map<String, Object?>)['sha256'] = archiveSha256;
    manifestFile.writeAsStringSync(jsonEncode(manifest));
    return _AuditFixture(
      root: root,
      stagingDirectory: stagingDirectory,
      target: target,
      staged: StagedNativeArtifact(
        artifactId: artifactId,
        identity: NativeArtifactIdentity(
          artifactId: artifactId,
          lockSha256: '0' * 64,
          sourceSha256: archiveSha256,
          operatingSystem: target.operatingSystem,
          architecture: target.architecture,
          variant: target.variant,
          minimumOs: minimumOs,
          flavor: target.flavor,
          runtimeMode: 'bundled',
          thirdPartyNoticesSha256: sha256
              .convert(noticeBytes['ThirdPartyNotices.txt']!)
              .toString(),
          providers: const <String, String?>{'cpu': 'CPUExecutionProvider'},
        ),
        archiveFile: archive,
        archiveOrigin: NativeArtifactArchiveOrigin.cache,
        payloadFiles: payloads,
        noticeFiles: notices,
        manifestFile: manifestFile,
      ),
    );
  }

  void dispose() {
    if (root.existsSync()) {
      root.deleteSync(recursive: true);
    }
  }
}

Uint8List _syntheticElf({
  required int machine,
  required String soname,
  required Set<String> needed,
  String? runpath,
  int alignment = 16 * 1024,
}) {
  const baseAddress = 0x10000;
  const programHeaderOffset = 64;
  const dynamicOffset = 0x200;
  const stringOffset = 0x500;
  final strings = BytesBuilder(copy: false)..addByte(0);

  int addString(String value) {
    final offset = strings.length;
    strings.add(utf8.encode(value));
    strings.addByte(0);
    return offset;
  }

  final sonameOffset = addString(soname);
  final runpathOffset = runpath == null ? null : addString(runpath);
  final neededOffsets = <int>[for (final name in needed) addString(name)];
  final stringBytes = strings.takeBytes();
  final dynamicEntries = <(int, int)>[
    (5, baseAddress + stringOffset),
    (10, stringBytes.length),
    (14, sonameOffset),
    if (runpathOffset != null) (29, runpathOffset),
    for (final offset in neededOffsets) (1, offset),
    (0, 0),
  ];
  final length = stringOffset + stringBytes.length;
  final bytes = Uint8List(length);
  final data = ByteData.sublistView(bytes);
  bytes.setRange(0, 7, <int>[0x7f, 0x45, 0x4c, 0x46, 2, 1, 1]);
  data.setUint16(16, 3, Endian.little);
  data.setUint16(18, machine, Endian.little);
  data.setUint32(20, 1, Endian.little);
  data.setUint64(32, programHeaderOffset, Endian.little);
  data.setUint16(52, 64, Endian.little);
  data.setUint16(54, 56, Endian.little);
  data.setUint16(56, 2, Endian.little);

  data.setUint32(programHeaderOffset, 1, Endian.little);
  data.setUint32(programHeaderOffset + 4, 5, Endian.little);
  data.setUint64(programHeaderOffset + 8, 0, Endian.little);
  data.setUint64(programHeaderOffset + 16, baseAddress, Endian.little);
  data.setUint64(programHeaderOffset + 24, baseAddress, Endian.little);
  data.setUint64(programHeaderOffset + 32, length, Endian.little);
  data.setUint64(programHeaderOffset + 40, length, Endian.little);
  data.setUint64(programHeaderOffset + 48, alignment, Endian.little);

  final dynamicHeader = programHeaderOffset + 56;
  data.setUint32(dynamicHeader, 2, Endian.little);
  data.setUint32(dynamicHeader + 4, 6, Endian.little);
  data.setUint64(dynamicHeader + 8, dynamicOffset, Endian.little);
  data.setUint64(
    dynamicHeader + 16,
    baseAddress + dynamicOffset,
    Endian.little,
  );
  data.setUint64(
    dynamicHeader + 24,
    baseAddress + dynamicOffset,
    Endian.little,
  );
  data.setUint64(dynamicHeader + 32, dynamicEntries.length * 16, Endian.little);
  data.setUint64(dynamicHeader + 40, dynamicEntries.length * 16, Endian.little);
  data.setUint64(dynamicHeader + 48, 8, Endian.little);

  for (var index = 0; index < dynamicEntries.length; index++) {
    data.setUint64(
      dynamicOffset + index * 16,
      dynamicEntries[index].$1,
      Endian.little,
    );
    data.setUint64(
      dynamicOffset + index * 16 + 8,
      dynamicEntries[index].$2,
      Endian.little,
    );
  }
  bytes.setRange(stringOffset, stringOffset + stringBytes.length, stringBytes);
  return bytes;
}

Uint8List _syntheticPe(Set<String> imports) {
  const peOffset = 0x80;
  const optionalSize = 240;
  const optionalOffset = peOffset + 24;
  const sectionTable = optionalOffset + optionalSize;
  const rawOffset = 0x200;
  const virtualAddress = 0x1000;
  const importOffset = 0x300;
  const namesOffset = 0x500;
  const rawSize = 0xe00;
  final bytes = Uint8List(rawOffset + rawSize);
  final data = ByteData.sublistView(bytes);
  bytes[0] = 0x4d;
  bytes[1] = 0x5a;
  data.setUint32(0x3c, peOffset, Endian.little);
  data.setUint32(peOffset, 0x00004550, Endian.little);
  data.setUint16(peOffset + 4, 0x8664, Endian.little);
  data.setUint16(peOffset + 6, 1, Endian.little);
  data.setUint16(peOffset + 20, optionalSize, Endian.little);
  data.setUint16(peOffset + 22, 0x2022, Endian.little);
  data.setUint16(optionalOffset, 0x20b, Endian.little);
  data.setUint32(optionalOffset + 32, 0x1000, Endian.little);
  data.setUint32(optionalOffset + 36, 0x200, Endian.little);
  data.setUint32(optionalOffset + 56, 0x2000, Endian.little);
  data.setUint32(optionalOffset + 60, rawOffset, Endian.little);
  data.setUint16(optionalOffset + 68, 3, Endian.little);
  data.setUint16(optionalOffset + 70, 0x4160, Endian.little);
  data.setUint32(optionalOffset + 108, 16, Endian.little);
  data.setUint32(
    optionalOffset + 120,
    virtualAddress + importOffset - rawOffset,
    Endian.little,
  );
  data.setUint32(
    optionalOffset + 124,
    (imports.length + 1) * 20,
    Endian.little,
  );

  bytes.setRange(
    sectionTable,
    sectionTable + 8,
    ascii.encode('.rdata\u0000\u0000'),
  );
  data.setUint32(sectionTable + 8, rawSize, Endian.little);
  data.setUint32(sectionTable + 12, virtualAddress, Endian.little);
  data.setUint32(sectionTable + 16, rawSize, Endian.little);
  data.setUint32(sectionTable + 20, rawOffset, Endian.little);
  data.setUint32(sectionTable + 36, 0x40000040, Endian.little);

  var nameOffset = namesOffset;
  var index = 0;
  for (final name in imports) {
    final descriptor = importOffset + index * 20;
    data.setUint32(descriptor, virtualAddress + 0x700, Endian.little);
    data.setUint32(
      descriptor + 12,
      virtualAddress + nameOffset - rawOffset,
      Endian.little,
    );
    data.setUint32(descriptor + 16, virtualAddress + 0x700, Endian.little);
    final encoded = ascii.encode(name);
    bytes.setRange(nameOffset, nameOffset + encoded.length, encoded);
    nameOffset += encoded.length + 1;
    index += 1;
  }
  if (nameOffset > bytes.length) {
    throw StateError('Synthetic PE imports exceeded the fixture section.');
  }
  return bytes;
}
