import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as path;
import 'package:test/test.dart';

import '../hook/src/macos_ort_artifact_resolver.dart';

const String _archiveRoot = 'onnxruntime-osx-arm64-1.27.1';
const String _versionedRuntimePath =
    '$_archiveRoot/lib/libonnxruntime.1.27.1.dylib';
const String _unversionedRuntimePath = '$_archiveRoot/lib/libonnxruntime.dylib';
const String _installNameAliasPath = '$_archiveRoot/lib/libonnxruntime.1.dylib';
const List<int> _versionedRuntimeBytes = <int>[1, 27, 1, 42, 99];
const List<int> _unversionedRuntimeBytes = <int>[1, 27, 1, 42, 99];

void main() {
  group('macOS ORT offline resolver and stager', () {
    late _ResolverFixture fixture;

    setUp(() {
      fixture = _ResolverFixture.create();
    });

    tearDown(() {
      fixture.dispose();
    });

    test(
      'selects a mirror artifact and stages deterministic bundle inputs',
      () async {
        fixture.writeStandardArchive();

        final result = await fixture.stage();

        expect(result.archiveOrigin, MacosOrtArchiveOrigin.mirror);
        expect(result.artifactId, fonixMacosOrtArtifactId);
        expect(
          path.basename(result.runtimeFile.path),
          fonixMacosOrtBundleFileName,
        );
        expect(result.runtimeFile.readAsBytesSync(), _versionedRuntimeBytes);
        expect(fixture.stagedRelativePaths(fixture.stageDirectory), <String>[
          fonixMacosOrtManifestFileName,
          fonixMacosOrtBundleFileName,
        ]);

        final manifestText = result.manifestFile.readAsStringSync();
        final manifest = _jsonObject(manifestText);
        expect(manifest['artifactId'], fonixMacosOrtArtifactId);
        final bundleFile = _object(manifest['bundleFile']);
        expect(bundleFile['basename'], fonixMacosOrtBundleFileName);
        expect(
          bundleFile['kind'],
          'regular-file-materialized-from-verified-symlink-target',
        );
        expect(bundleFile['archiveSource'], _versionedRuntimePath);
        final providers = _object(manifest['providers']);
        expect(
          providers['claimBoundary'],
          contains('not provider activation or qualification evidence'),
        );
        final audit = _object(manifest['knownBinaryAudit']);
        expect(audit['status'], 'not-recorded-for-digest');
        expect(audit['providerCapabilityClaimsAllowed'], isFalse);
        expect(manifestText, isNot(contains(fixture.root.path)));
      },
    );

    test(
      'verifies archive size and SHA-256 on every cache or mirror hit',
      () async {
        final goodArchive = fixture.writeStandardArchive();

        fixture.setArchiveSize(goodArchive.length + 1);
        await expectLater(
          fixture.stage,
          throwsA(
            isA<MacosOrtArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              allOf(contains('has size'), contains('entry was rejected')),
            ),
          ),
        );

        fixture.resetStage();
        fixture.setArchiveSize(goodArchive.length);
        fixture.setArchiveSha256('0' * 64);
        await expectLater(
          fixture.stage,
          throwsA(
            isA<MacosOrtArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              allOf(contains('SHA-256'), contains('entry was rejected')),
            ),
          ),
        );
      },
    );

    test('rejects wrong expected member size and digest', () async {
      fixture.writeStandardArchive();
      fixture.setExpectedFileSize(_versionedRuntimePath, 99);

      await expectLater(
        fixture.stage,
        throwsA(
          isA<MacosOrtArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            allOf(contains(_versionedRuntimePath), contains('has size')),
          ),
        ),
      );

      fixture.resetStage();
      fixture.setExpectedFileSize(
        _versionedRuntimePath,
        _versionedRuntimeBytes.length,
      );
      fixture.setExpectedFileSha256(_versionedRuntimePath, 'f' * 64);
      await expectLater(
        fixture.stage,
        throwsA(
          isA<MacosOrtArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            allOf(contains(_versionedRuntimePath), contains('SHA-256')),
          ),
        ),
      );
    });

    test('rejects a missing or ambiguous exact lock tuple', () async {
      fixture.writeStandardArchive();
      fixture.setArtifactTarget(os: 'linux', architecture: 'arm64');

      await expectLater(
        fixture.stage,
        throwsA(
          isA<MacosOrtArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            contains('no artifact for the exact tuple'),
          ),
        ),
      );

      fixture.resetStage();
      fixture.reloadConfiguredStandardLock();
      fixture.addAmbiguousArtifact();
      await expectLater(
        fixture.stage,
        throwsA(
          isA<MacosOrtArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            contains('ambiguously declares 2 artifacts'),
          ),
        ),
      );
    });

    test(
      'rejects traversal and members outside the exact archive root',
      () async {
        fixture.writeArchive(<_TarEntry>[
          ..._standardEntries,
          _TarEntry.file('../escaped.txt', utf8.encode('no')),
        ]);

        await expectLater(
          fixture.stage,
          throwsA(
            isA<MacosOrtArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              contains('path traversal'),
            ),
          ),
        );
        expect(
          File(path.join(fixture.root.path, 'escaped.txt')).existsSync(),
          isFalse,
        );
        expect(fixture.stageDirectory.existsSync(), isFalse);

        fixture.writeArchive(<_TarEntry>[
          ..._standardEntries,
          _TarEntry.file('different-root/extra.txt', utf8.encode('no')),
        ]);
        await expectLater(
          fixture.stage,
          throwsA(
            isA<MacosOrtArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              contains('escapes the exact archive root'),
            ),
          ),
        );
      },
    );

    test(
      'never admits unexpected regular files and rejects symlink surprises',
      () async {
        fixture.writeArchive(<_TarEntry>[
          ..._standardEntries,
          _TarEntry.file(
            '$_archiveRoot/README-extra.md',
            utf8.encode('ignored'),
          ),
        ]);

        final result = await fixture.stage();
        expect(fixture.stagedRelativePaths(fixture.stageDirectory), <String>[
          fonixMacosOrtManifestFileName,
          fonixMacosOrtBundleFileName,
        ]);
        expect(
          result.manifestFile.readAsStringSync(),
          isNot(contains('README-extra.md')),
        );
        final manifest = _jsonObject(result.manifestFile.readAsStringSync());
        final members = _object(manifest['verifiedArchiveMembers']);
        expect(members['ignoredSafeRegularFileCount'], 1);

        fixture.resetStage();
        fixture.writeArchive(<_TarEntry>[
          ..._standardEntries,
          _TarEntry.symlink(
            '$_archiveRoot/lib/unexpected.dylib',
            'libonnxruntime.1.27.1.dylib',
          ),
        ]);
        await expectLater(
          fixture.stage,
          throwsA(
            isA<MacosOrtArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              contains('unexpected symbolic link'),
            ),
          ),
        );
      },
    );

    test('rejects a substituted install-name alias target', () async {
      fixture.writeArchive(<_TarEntry>[
        _TarEntry.file(_versionedRuntimePath, _versionedRuntimeBytes),
        _TarEntry.file(_unversionedRuntimePath, _unversionedRuntimeBytes),
        _TarEntry.symlink(_installNameAliasPath, 'libonnxruntime.dylib'),
      ]);

      await expectLater(
        fixture.stage,
        throwsA(
          isA<MacosOrtArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            allOf(contains('targets'), contains('expected')),
          ),
        ),
      );
    });

    test(
      'fails actionably on an offline miss without creating staging',
      () async {
        fixture.writeStandardArchive();
        fixture.mirrorArchive.deleteSync();

        await expectLater(
          fixture.stage,
          throwsA(
            isA<MacosOrtArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              allOf(
                contains('Offline macOS ORT archive miss'),
                contains(fonixMacosOrtArchiveName),
                contains('No network request was attempted'),
              ),
            ),
          ),
        );
        expect(fixture.stageDirectory.existsSync(), isFalse);
      },
    );

    test(
      'does not fall through from a corrupt cache hit to a good mirror',
      () async {
        final goodArchive = fixture.writeStandardArchive();
        final corruptArchive = Uint8List.fromList(goodArchive);
        corruptArchive[corruptArchive.length - 1] ^= 1;
        fixture.cacheArchive.writeAsBytesSync(corruptArchive);

        await expectLater(
          fixture.stage,
          throwsA(
            isA<MacosOrtArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              allOf(contains('SHA-256'), contains(fixture.cacheArchive.path)),
            ),
          ),
        );
      },
    );

    test('produces identical content from repeated staging', () async {
      fixture.writeStandardArchive();
      final first = await fixture.stage();
      final firstSnapshot = fixture.snapshot(fixture.stageDirectory);

      final secondStage = Directory(path.join(fixture.root.path, 'stage-two'));
      final second = await const MacosOrtArtifactResolver().stage(
        packageRoot: fixture.packageRoot,
        stagingDirectory: secondStage,
        cacheDirectory: fixture.cacheDirectory,
        mirrorDirectory: fixture.mirrorDirectory,
      );
      final secondSnapshot = fixture.snapshot(secondStage);

      expect(firstSnapshot, secondSnapshot);
      expect(
        first.manifestFile.readAsBytesSync(),
        second.manifestFile.readAsBytesSync(),
      );
    });

    test('refuses to stage inside the source checkout', () async {
      fixture.writeStandardArchive();
      final checkoutStage = Directory(
        path.join(fixture.packageRoot.path, '.dart_tool', 'ort-stage'),
      );

      await expectLater(
        () => const MacosOrtArtifactResolver().stage(
          packageRoot: fixture.packageRoot,
          stagingDirectory: checkoutStage,
          mirrorDirectory: fixture.mirrorDirectory,
        ),
        throwsA(
          isA<MacosOrtArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            contains('outside the Fonix checkout'),
          ),
        ),
      );
      expect(checkoutStage.existsSync(), isFalse);
    });
  });
}

final List<_TarEntry> _standardEntries = <_TarEntry>[
  _TarEntry.file(_versionedRuntimePath, _versionedRuntimeBytes),
  _TarEntry.file(_unversionedRuntimePath, _unversionedRuntimeBytes),
  _TarEntry.symlink(_installNameAliasPath, 'libonnxruntime.1.27.1.dylib'),
];

final class _ResolverFixture {
  _ResolverFixture._({
    required this.root,
    required this.packageRoot,
    required this.cacheDirectory,
    required this.mirrorDirectory,
    required this.stageDirectory,
    required this.baseLockSource,
  });

  factory _ResolverFixture.create() {
    final root = Directory.systemTemp.createTempSync('fonix-ort-resolver-');
    final packageRoot = Directory(path.join(root.path, 'package'))
      ..createSync();
    Directory(path.join(packageRoot.path, 'native')).createSync();
    final cacheDirectory = Directory(path.join(root.path, 'cache'))
      ..createSync();
    final mirrorDirectory = Directory(path.join(root.path, 'mirror'))
      ..createSync();
    return _ResolverFixture._(
      root: root,
      packageRoot: packageRoot,
      cacheDirectory: cacheDirectory,
      mirrorDirectory: mirrorDirectory,
      stageDirectory: Directory(path.join(root.path, 'stage')),
      baseLockSource: File(
        path.join('native', 'versions.lock.yaml'),
      ).readAsStringSync(),
    );
  }

  final Directory root;
  final Directory packageRoot;
  final Directory cacheDirectory;
  final Directory mirrorDirectory;
  final Directory stageDirectory;
  final String baseLockSource;

  File get lockFile =>
      File(path.join(packageRoot.path, 'native', 'versions.lock.yaml'));

  File get cacheArchive =>
      File(path.join(cacheDirectory.path, fonixMacosOrtArchiveName));

  File get mirrorArchive =>
      File(path.join(mirrorDirectory.path, fonixMacosOrtArchiveName));

  void dispose() {
    root.deleteSync(recursive: true);
  }

  Future<StagedMacosOrtArtifact> stage() =>
      const MacosOrtArtifactResolver().stage(
        packageRoot: packageRoot,
        stagingDirectory: stageDirectory,
        cacheDirectory: cacheDirectory,
        mirrorDirectory: mirrorDirectory,
      );

  Uint8List writeStandardArchive() => writeArchive(_standardEntries);

  Uint8List writeArchive(List<_TarEntry> entries) {
    final archive = _tgz(entries);
    mirrorArchive.writeAsBytesSync(archive, flush: true);
    _writeConfiguredLock(archive);
    return archive;
  }

  void reloadConfiguredStandardLock() {
    final archive = mirrorArchive.readAsBytesSync();
    _writeConfiguredLock(archive);
  }

  void resetStage() {
    if (stageDirectory.existsSync()) {
      stageDirectory.deleteSync(recursive: true);
    }
  }

  void setArchiveSize(int value) {
    _mutateLock((artifact) {
      _object(artifact['source'])['size_bytes'] = value;
    });
  }

  void setArchiveSha256(String value) {
    _mutateLock((artifact) {
      _object(artifact['source'])['sha256'] = value;
    });
  }

  void setExpectedFileSize(String relativePath, int value) {
    _mutateExpectedFile(relativePath, (file) {
      file['size_bytes'] = value;
    });
  }

  void setExpectedFileSha256(String relativePath, String value) {
    _mutateExpectedFile(relativePath, (file) {
      file['sha256'] = value;
    });
  }

  void setArtifactTarget({required String os, required String architecture}) {
    _mutateLock((artifact) {
      final target = _object(artifact['target']);
      target['os'] = os;
      target['architecture'] = architecture;
    });
  }

  void addAmbiguousArtifact() {
    final root = _readLockObject();
    final artifacts = _list(root['artifacts']);
    final duplicate = _jsonObject(jsonEncode(_macosArtifact(root)));
    duplicate['id'] = 'onnxruntime-1.27.1-macos-arm64-cpu-duplicate';
    artifacts.add(duplicate);
    _writeLockObject(root);
  }

  List<String> stagedRelativePaths(Directory directory) {
    final paths = <String>[
      for (final entity in directory.listSync(recursive: true))
        path.relative(entity.path, from: directory.path),
    ]..sort();
    return paths;
  }

  Map<String, String> snapshot(Directory directory) {
    final result = <String, String>{};
    for (final entity in directory.listSync(recursive: true)) {
      final relative = path.relative(entity.path, from: directory.path);
      final type = FileSystemEntity.typeSync(entity.path, followLinks: false);
      if (type == FileSystemEntityType.file) {
        result[relative] =
            'file:${sha256.convert(File(entity.path).readAsBytesSync())}';
      } else if (type == FileSystemEntityType.link) {
        result[relative] = 'link:${Link(entity.path).targetSync()}';
      } else if (type == FileSystemEntityType.directory) {
        result[relative] = 'directory';
      }
    }
    return result;
  }

  void _writeConfiguredLock(Uint8List archive) {
    final root = _jsonObject(baseLockSource);
    final artifact = _macosArtifact(root);
    final source = _object(artifact['source']);
    source['size_bytes'] = archive.length;
    source['sha256'] = sha256.convert(archive).toString();
    final fileBytes = <String, List<int>>{
      _versionedRuntimePath: _versionedRuntimeBytes,
      _unversionedRuntimePath: _unversionedRuntimeBytes,
    };
    for (final value in _list(artifact['expected_files'])) {
      final file = _object(value);
      final relativePath = file['path'] as String;
      final bytes = fileBytes[relativePath]!;
      file['size_bytes'] = bytes.length;
      file['sha256'] = sha256.convert(bytes).toString();
    }
    _writeLockObject(root);
  }

  void _mutateLock(void Function(Map<String, Object?> artifact) mutation) {
    final root = _readLockObject();
    mutation(_macosArtifact(root));
    _writeLockObject(root);
  }

  Map<String, Object?> _macosArtifact(Map<String, Object?> root) => _object(
    _list(root['artifacts']).singleWhere(
      (value) => _object(_object(value)['target'])['os'] == 'macos',
    ),
  );

  void _mutateExpectedFile(
    String relativePath,
    void Function(Map<String, Object?> file) mutation,
  ) {
    _mutateLock((artifact) {
      final file = _list(artifact['expected_files'])
          .map(_object)
          .singleWhere((candidate) => candidate['path'] == relativePath);
      mutation(file);
    });
  }

  Map<String, Object?> _readLockObject() =>
      _jsonObject(lockFile.readAsStringSync());

  void _writeLockObject(Map<String, Object?> value) {
    lockFile.writeAsStringSync(jsonEncode(value), flush: true);
  }
}

final class _TarEntry {
  const _TarEntry._({
    required this.name,
    required this.type,
    required this.data,
    required this.linkTarget,
  });

  factory _TarEntry.file(String name, List<int> data) => _TarEntry._(
    name: name,
    type: 48,
    data: Uint8List.fromList(data),
    linkTarget: '',
  );

  factory _TarEntry.symlink(String name, String target) =>
      _TarEntry._(name: name, type: 50, data: Uint8List(0), linkTarget: target);

  final String name;
  final int type;
  final Uint8List data;
  final String linkTarget;
}

Uint8List _tgz(List<_TarEntry> entries) {
  final tar = BytesBuilder(copy: false);
  for (final entry in entries) {
    final header = Uint8List(512);
    _writeTarText(header, 0, 100, entry.name);
    _writeTarOctal(header, 100, 8, entry.type == 50 ? 0x1ff : 0x1ed);
    _writeTarOctal(header, 108, 8, 0);
    _writeTarOctal(header, 116, 8, 0);
    _writeTarOctal(header, 124, 12, entry.data.length);
    _writeTarOctal(header, 136, 12, 0);
    for (var index = 148; index < 156; index += 1) {
      header[index] = 32;
    }
    header[156] = entry.type;
    _writeTarText(header, 157, 100, entry.linkTarget);
    _writeTarText(header, 257, 6, 'ustar');
    header[262] = 0;
    header[263] = 48;
    header[264] = 48;
    final checksum = header.fold<int>(0, (sum, byte) => sum + byte);
    final checksumText = checksum.toRadixString(8).padLeft(6, '0');
    _writeTarText(header, 148, 6, checksumText);
    header[154] = 0;
    header[155] = 32;
    tar.add(header);
    tar.add(entry.data);
    final padding = (512 - (entry.data.length % 512)) % 512;
    if (padding != 0) {
      tar.add(Uint8List(padding));
    }
  }
  tar.add(Uint8List(1024));
  return Uint8List.fromList(gzip.encode(tar.takeBytes()));
}

void _writeTarText(Uint8List target, int offset, int length, String value) {
  final bytes = utf8.encode(value);
  if (bytes.length > length) {
    throw ArgumentError.value(value, 'value', 'does not fit the tar field');
  }
  target.setRange(offset, offset + bytes.length, bytes);
}

void _writeTarOctal(Uint8List target, int offset, int length, int value) {
  final encoded = value.toRadixString(8).padLeft(length - 1, '0');
  _writeTarText(target, offset, length - 1, encoded);
  target[offset + length - 1] = 0;
}

Map<String, Object?> _jsonObject(String source) {
  final Object? decoded = jsonDecode(source);
  return _object(decoded);
}

Map<String, Object?> _object(Object? value) =>
    (value as Map<Object?, Object?>).cast<String, Object?>();

List<Object?> _list(Object? value) => value as List<Object?>;
