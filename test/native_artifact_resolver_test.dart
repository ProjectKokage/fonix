import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:fonix/src/hook/native_artifact_resolver.dart';
import 'package:path/path.dart' as path;
import 'package:test/test.dart';

void main() {
  group('portable native artifact resolver', () {
    test('stages only a verified nested payload and notices', () async {
      final fixture = _Fixture.create();
      addTearDown(fixture.dispose);

      final first = await fixture.stage('stage-one');
      final second = await fixture.stage('stage-two');

      expect(first.artifactId, 'fixture-linux-x86-64');
      expect(first.identity.artifactId, first.artifactId);
      expect(first.identity.operatingSystem, 'linux');
      expect(first.identity.architecture, 'x86_64');
      expect(first.identity.minimumOs, 'glibc-2.27');
      expect(first.identity.runtimeMode, 'bundled');
      expect(first.identity.providers, <String, String?>{
        'cpu': 'CPUExecutionProvider',
      });
      expect(
        first.identity.thirdPartyNoticesSha256,
        sha256.convert(utf8.encode('fixture notices')).toString(),
      );
      expect(first.archiveOrigin, NativeArtifactArchiveOrigin.cache);
      expect(first.payloadFiles.map((file) => path.basename(file.path)), [
        'libonnxruntime.so.1',
      ]);
      expect(first.payloadFiles.single.readAsBytesSync(), fixture.payload);
      expect(
        first.noticeFiles.map((file) => path.basename(file.path)).toSet(),
        {'LICENSE', 'ThirdPartyNotices.txt'},
      );
      expect(
        first.manifestFile.readAsBytesSync(),
        second.manifestFile.readAsBytesSync(),
      );
      final manifest = _object(
        jsonDecode(first.manifestFile.readAsStringSync()),
      );
      expect(manifest['schema'], 2);
      expect(_list(manifest['containers']), hasLength(1));
      expect(_list(manifest['payloadFiles']), hasLength(1));
      expect(_list(manifest['notices']), hasLength(2));
      expect(
        File(
          path.join(first.manifestFile.parent.path, 'ignored.txt'),
        ).existsSync(),
        isFalse,
      );
    });

    test(
      'a corrupt cache hit does not fall through to a valid mirror',
      () async {
        final fixture = _Fixture.create(includeMirror: true);
        addTearDown(fixture.dispose);
        final cacheFile = File(path.join(fixture.cache.path, 'fixture.zip'));
        final corrupt = cacheFile.readAsBytesSync()..[0] ^= 0x01;
        cacheFile.writeAsBytesSync(corrupt);

        await expectLater(
          fixture.stage('stage-corrupt'),
          throwsA(
            isA<NativeArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              contains('SHA-256'),
            ),
          ),
        );
        expect(
          Directory(path.join(fixture.root.path, 'stage-corrupt')).existsSync(),
          isFalse,
        );
      },
    );

    test('rejects duplicate and traversing members before staging', () async {
      for (final badEntry in <String>['ignored.txt', '../escape']) {
        final fixture = _Fixture.create(extraOuterEntry: badEntry);
        addTearDown(fixture.dispose);

        await expectLater(
          fixture.stage('stage-bad'),
          throwsA(isA<NativeArtifactResolutionException>()),
        );
        expect(
          Directory(path.join(fixture.root.path, 'stage-bad')).existsSync(),
          isFalse,
        );
      }
    });

    test('rejects a symbolic link that escapes its archive', () async {
      final fixture = _Fixture.create(escapingSymlink: true);
      addTearDown(fixture.dispose);

      await expectLater(
        fixture.stage('stage-link'),
        throwsA(
          isA<NativeArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            contains('unsafe target'),
          ),
        ),
      );
    });

    test(
      'requires a fresh external staging directory and exact tuple',
      () async {
        final fixture = _Fixture.create();
        addTearDown(fixture.dispose);
        final existing = Directory(path.join(fixture.root.path, 'existing'))
          ..createSync();

        await expectLater(
          const NativeArtifactResolver().stage(
            packageRoot: fixture.packageRoot,
            stagingDirectory: existing,
            target: _Fixture.target,
            cacheDirectory: fixture.cache,
          ),
          throwsA(isA<NativeArtifactResolutionException>()),
        );
        await expectLater(
          const NativeArtifactResolver().stage(
            packageRoot: fixture.packageRoot,
            stagingDirectory: Directory(
              path.join(fixture.packageRoot.path, 'inside-checkout'),
            ),
            target: _Fixture.target,
            cacheDirectory: fixture.cache,
          ),
          throwsA(isA<NativeArtifactResolutionException>()),
        );
        await expectLater(
          const NativeArtifactResolver().stage(
            packageRoot: fixture.packageRoot,
            stagingDirectory: Directory(
              path.join(fixture.root.path, 'wrong-target'),
            ),
            target: const NativeArtifactTarget(
              operatingSystem: 'windows',
              architecture: 'x64',
              variant: 'default',
            ),
            cacheDirectory: fixture.cache,
          ),
          throwsA(
            isA<NativeArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              contains('no artifact for exact tuple'),
            ),
          ),
        );
      },
    );
  });

  final officialCache = Platform.environment['FONIX_ORT_ARTIFACT_CACHE'];
  test(
    'stages every official local baseline artifact',
    () async {
      final temporary = Directory.systemTemp.createTempSync(
        'fonix-official-artifacts-',
      );
      addTearDown(() => temporary.deleteSync(recursive: true));
      const targets = <NativeArtifactTarget>[
        NativeArtifactTarget(
          operatingSystem: 'ios',
          architecture: 'arm64',
          variant: 'device',
        ),
        NativeArtifactTarget(
          operatingSystem: 'ios',
          architecture: 'arm64',
          variant: 'simulator',
        ),
        NativeArtifactTarget(
          operatingSystem: 'ios',
          architecture: 'x86_64',
          variant: 'simulator',
        ),
        NativeArtifactTarget(
          operatingSystem: 'macos',
          architecture: 'arm64',
          variant: 'default',
        ),
        NativeArtifactTarget(
          operatingSystem: 'android',
          architecture: 'arm64-v8a',
          variant: 'default',
        ),
        NativeArtifactTarget(
          operatingSystem: 'android',
          architecture: 'x86_64',
          variant: 'default',
        ),
        NativeArtifactTarget(
          operatingSystem: 'linux',
          architecture: 'x86_64',
          variant: 'default',
        ),
        NativeArtifactTarget(
          operatingSystem: 'linux',
          architecture: 'arm64',
          variant: 'default',
        ),
        NativeArtifactTarget(
          operatingSystem: 'windows',
          architecture: 'x64',
          variant: 'default',
        ),
      ];
      for (var index = 0; index < targets.length; index++) {
        final staged = await const NativeArtifactResolver().stage(
          packageRoot: Directory.current,
          stagingDirectory: Directory(
            path.join(temporary.path, 'stage-$index'),
          ),
          target: targets[index],
          cacheDirectory: Directory(officialCache!),
        );
        expect(staged.payloadFiles, isNotEmpty);
        expect(staged.noticeFiles, hasLength(2));
      }
    },
    skip: officialCache == null
        ? 'Set FONIX_ORT_ARTIFACT_CACHE to opt into the local official-archive smoke.'
        : false,
  );

  // No active lock entry is a tar.gz archive or declares a symbolic link, so
  // these fixtures keep both resolver paths exercised.
  group('tar.gz source archive', () {
    test('stages a member beside a locked symbolic link', () async {
      final fixture = _TarFixture.create();
      addTearDown(fixture.dispose);

      final staged = await fixture.stage('stage-tar');

      expect(staged.payloadFiles.map((file) => path.basename(file.path)), [
        'libonnxruntime.so.1',
        'libonnxruntime_next.so.1',
      ]);
      expect(staged.payloadFiles.first.readAsBytesSync(), fixture.payload);
      final manifest = _object(
        jsonDecode(staged.manifestFile.readAsStringSync()),
      );
      expect(_list(manifest['verifiedSymlinks']), [
        <String, Object?>{
          'path': 'pkg/lib/libruntime.so.1',
          'target': 'libruntime.so.1.0.0',
        },
      ]);
    });

    test(
      'rejects a symbolic link whose target differs from the lock',
      () async {
        final fixture = _TarFixture.create(
          archivedTarget: 'libruntime.so.1.0.1',
        );
        addTearDown(fixture.dispose);

        await expectLater(
          fixture.stage('stage-tar-link'),
          throwsA(
            isA<NativeArtifactResolutionException>().having(
              (error) => error.message,
              'message',
              contains('expected "libruntime.so.1.0.0"'),
            ),
          ),
        );
      },
    );

    test('rejects an archive missing a locked symbolic link', () async {
      final fixture = _TarFixture.create(archivedTarget: null);
      addTearDown(fixture.dispose);

      await expectLater(
        fixture.stage('stage-tar-missing'),
        throwsA(
          isA<NativeArtifactResolutionException>().having(
            (error) => error.message,
            'message',
            contains('missing expected symbolic links'),
          ),
        ),
      );
    });
  });
}

final class _Fixture {
  _Fixture._({
    required this.root,
    required this.packageRoot,
    required this.cache,
    required this.mirror,
    required this.payload,
  });

  static const target = NativeArtifactTarget(
    operatingSystem: 'linux',
    architecture: 'x86_64',
    variant: 'default',
  );

  final Directory root;
  final Directory packageRoot;
  final Directory cache;
  final Directory mirror;
  final Uint8List payload;

  static _Fixture create({
    bool includeMirror = false,
    String? extraOuterEntry,
    bool escapingSymlink = false,
  }) {
    final root = Directory.systemTemp.createTempSync('fonix-resolver-test-');
    final packageRoot = Directory(path.join(root.path, 'package'))
      ..createSync(recursive: true);
    final cache = Directory(path.join(root.path, 'cache'))..createSync();
    final mirror = Directory(path.join(root.path, 'mirror'))..createSync();
    final payload = Uint8List.fromList(utf8.encode('verified runtime bytes'));
    final inner = _zip(<_ZipFixtureEntry>[
      _ZipFixtureEntry.regular('lib/runtime.so', payload),
      _ZipFixtureEntry.regular(
        'ignored-inner.txt',
        Uint8List.fromList(utf8.encode('ignored')),
      ),
    ]);
    final outerEntries = <_ZipFixtureEntry>[
      _ZipFixtureEntry.regular('nested/runtime.zip', inner),
      _ZipFixtureEntry.regular(
        'LICENSE',
        Uint8List.fromList(utf8.encode('MIT fixture license')),
      ),
      _ZipFixtureEntry.regular(
        'ThirdPartyNotices.txt',
        Uint8List.fromList(utf8.encode('fixture notices')),
      ),
      _ZipFixtureEntry.regular(
        'ignored.txt',
        Uint8List.fromList(utf8.encode('ignored')),
      ),
      if (extraOuterEntry != null)
        _ZipFixtureEntry.regular(
          extraOuterEntry,
          Uint8List.fromList(utf8.encode('bad')),
        ),
      if (escapingSymlink)
        _ZipFixtureEntry.symlink('links/escape', '../../outside'),
    ];
    final outer = _zip(outerEntries);
    final cacheFile = File(path.join(cache.path, 'fixture.zip'))
      ..writeAsBytesSync(outer);
    if (includeMirror) {
      cacheFile.copySync(path.join(mirror.path, 'fixture.zip'));
    }

    final value = _object(
      jsonDecode(File('native/versions.lock.yaml').readAsStringSync()),
    );
    final artifact = _object(
      _list(value['artifacts']).singleWhere((value) {
        final target = _object(_object(value)['target']);
        return target['os'] == 'linux' && target['architecture'] == 'x86_64';
      }),
    );
    artifact['id'] = 'fixture-linux-x86-64';
    artifact['source'] = <String, Object?>{
      'url': 'https://artifacts.invalid/fixture.zip',
      'source_revision': List.filled(40, 'a').join(),
      'sha256': sha256.convert(outer).toString(),
      'size_bytes': outer.length,
      'archive': 'zip',
    };
    artifact['containers'] = <Object?>[
      <String, Object?>{
        'path': 'nested/runtime.zip',
        'sha256': sha256.convert(inner).toString(),
        'size_bytes': inner.length,
        'archive': 'zip',
      },
    ];
    artifact['expected_files'] = <Object?>[
      <String, Object?>{
        'path': 'lib/runtime.so',
        'staged_path': 'libonnxruntime.so.1',
        'sha256': sha256.convert(payload).toString(),
        'size_bytes': payload.length,
      },
    ];
    artifact['expected_symlinks'] = <Object?>[];
    final license = utf8.encode('MIT fixture license');
    final notices = utf8.encode('fixture notices');
    artifact['notices'] = <Object?>[
      <String, Object?>{
        'id': 'MIT',
        'container_depth': 0,
        'path': 'LICENSE',
        'staged_path': 'notices/LICENSE',
        'sha256': sha256.convert(license).toString(),
        'size_bytes': license.length,
      },
      <String, Object?>{
        'id': 'ThirdPartyNotices',
        'container_depth': 0,
        'path': 'ThirdPartyNotices.txt',
        'staged_path': 'notices/ThirdPartyNotices.txt',
        'sha256': sha256.convert(notices).toString(),
        'size_bytes': notices.length,
      },
    ];
    value['artifacts'] = <Object?>[artifact];
    final lockFile = File(
      path.join(packageRoot.path, 'native', 'versions.lock.yaml'),
    );
    lockFile.parent.createSync(recursive: true);
    lockFile.writeAsStringSync(jsonEncode(value));
    return _Fixture._(
      root: root,
      packageRoot: packageRoot,
      cache: cache,
      mirror: mirror,
      payload: payload,
    );
  }

  Future<StagedNativeArtifact> stage(String directoryName) =>
      const NativeArtifactResolver().stage(
        packageRoot: packageRoot,
        stagingDirectory: Directory(path.join(root.path, directoryName)),
        target: target,
        cacheDirectory: cache,
        mirrorDirectory: mirror,
      );

  void dispose() {
    if (root.existsSync()) {
      root.deleteSync(recursive: true);
    }
  }
}

final class _TarFixture {
  _TarFixture._({
    required this.root,
    required this.packageRoot,
    required this.cache,
    required this.payload,
  });

  final Directory root;
  final Directory packageRoot;
  final Directory cache;
  final Uint8List payload;

  /// [archivedTarget] is the link target written into the archive; the lock
  /// always expects `libruntime.so.1.0.0`. `null` omits the link.
  static _TarFixture create({String? archivedTarget = 'libruntime.so.1.0.0'}) {
    final root = Directory.systemTemp.createTempSync('fonix-resolver-tar-');
    final packageRoot = Directory(path.join(root.path, 'package'))
      ..createSync(recursive: true);
    final cache = Directory(path.join(root.path, 'cache'))..createSync();
    final payload = Uint8List.fromList(utf8.encode('verified tar runtime'));
    final next = Uint8List.fromList(utf8.encode('next tar runtime'));
    final license = Uint8List.fromList(utf8.encode('MIT fixture license'));
    final notices = Uint8List.fromList(utf8.encode('fixture notices'));
    final archive = _tarGz(<_TarFixtureEntry>[
      _TarFixtureEntry('pkg/lib/libruntime.so.1.0.0', bytes: payload),
      _TarFixtureEntry('pkg/lib/libruntime.so.1.0.1', bytes: next),
      if (archivedTarget != null)
        _TarFixtureEntry('pkg/lib/libruntime.so.1', linkTarget: archivedTarget),
      _TarFixtureEntry('pkg/LICENSE', bytes: license),
      _TarFixtureEntry('pkg/ThirdPartyNotices.txt', bytes: notices),
    ]);
    File(path.join(cache.path, 'fixture.tgz')).writeAsBytesSync(archive);

    final value = _object(
      jsonDecode(File('native/versions.lock.yaml').readAsStringSync()),
    );
    final artifact = _object(
      _list(value['artifacts']).singleWhere((value) {
        final target = _object(_object(value)['target']);
        return target['os'] == 'linux' && target['architecture'] == 'x86_64';
      }),
    );
    Map<String, Object?> digest(Uint8List bytes) => <String, Object?>{
      'sha256': sha256.convert(bytes).toString(),
      'size_bytes': bytes.length,
    };
    artifact['id'] = 'fixture-tar-linux-x86-64';
    artifact['source'] = <String, Object?>{
      'url': 'https://artifacts.invalid/fixture.tgz',
      'source_revision': List.filled(40, 'a').join(),
      ...digest(archive),
      'archive': 'tgz',
    };
    artifact['containers'] = <Object?>[];
    artifact['expected_files'] = <Object?>[
      <String, Object?>{
        'path': 'pkg/lib/libruntime.so.1.0.0',
        'staged_path': 'libonnxruntime.so.1',
        ...digest(payload),
      },
      <String, Object?>{
        'path': 'pkg/lib/libruntime.so.1.0.1',
        'staged_path': 'libonnxruntime_next.so.1',
        ...digest(next),
      },
    ];
    artifact['expected_symlinks'] = <Object?>[
      <String, Object?>{
        'path': 'pkg/lib/libruntime.so.1',
        'target': 'libruntime.so.1.0.0',
      },
    ];
    artifact['notices'] = <Object?>[
      <String, Object?>{
        'id': 'MIT',
        'container_depth': 0,
        'path': 'pkg/LICENSE',
        'staged_path': 'notices/LICENSE',
        ...digest(license),
      },
      <String, Object?>{
        'id': 'ThirdPartyNotices',
        'container_depth': 0,
        'path': 'pkg/ThirdPartyNotices.txt',
        'staged_path': 'notices/ThirdPartyNotices.txt',
        ...digest(notices),
      },
    ];
    value['artifacts'] = <Object?>[artifact];
    final lockFile = File(
      path.join(packageRoot.path, 'native', 'versions.lock.yaml'),
    );
    lockFile.parent.createSync(recursive: true);
    lockFile.writeAsStringSync(jsonEncode(value));
    return _TarFixture._(
      root: root,
      packageRoot: packageRoot,
      cache: cache,
      payload: payload,
    );
  }

  Future<StagedNativeArtifact> stage(String directoryName) =>
      const NativeArtifactResolver().stage(
        packageRoot: packageRoot,
        stagingDirectory: Directory(path.join(root.path, directoryName)),
        target: _Fixture.target,
        cacheDirectory: cache,
      );

  void dispose() {
    if (root.existsSync()) {
      root.deleteSync(recursive: true);
    }
  }
}

final class _TarFixtureEntry {
  _TarFixtureEntry(this.name, {Uint8List? bytes, this.linkTarget})
    : bytes = bytes ?? Uint8List(0);

  final String name;
  final Uint8List bytes;
  final String? linkTarget;
}

/// A gzip-compressed POSIX ustar stream of regular files and symbolic links.
Uint8List _tarGz(List<_TarFixtureEntry> entries) {
  final output = BytesBuilder(copy: false);
  for (final entry in entries) {
    final header = Uint8List(512);
    void put(int offset, String text) =>
        header.setRange(offset, offset + text.length, ascii.encode(text));
    put(0, entry.name);
    put(100, '0000644');
    put(108, '0000000');
    put(116, '0000000');
    put(124, entry.bytes.length.toRadixString(8).padLeft(11, '0'));
    put(136, '00000000000');
    header[156] = entry.linkTarget == null ? 0x30 : 0x32;
    if (entry.linkTarget case final target?) {
      put(157, target);
    }
    put(257, 'ustar');
    put(263, '00');
    header.fillRange(148, 156, 0x20);
    final checksum = header.fold<int>(0, (sum, byte) => sum + byte);
    put(148, checksum.toRadixString(8).padLeft(6, '0'));
    header[154] = 0;
    output
      ..add(header)
      ..add(entry.bytes)
      ..add(Uint8List((512 - entry.bytes.length % 512) % 512));
  }
  output.add(Uint8List(1024));
  return Uint8List.fromList(gzip.encode(output.takeBytes()));
}

enum _ZipFixtureKind { regular, symbolicLink }

final class _ZipFixtureEntry {
  const _ZipFixtureEntry._(this.name, this.bytes, this.kind);

  factory _ZipFixtureEntry.regular(String name, Uint8List bytes) =>
      _ZipFixtureEntry._(name, bytes, _ZipFixtureKind.regular);

  factory _ZipFixtureEntry.symlink(String name, String target) =>
      _ZipFixtureEntry._(
        name,
        Uint8List.fromList(utf8.encode(target)),
        _ZipFixtureKind.symbolicLink,
      );

  final String name;
  final Uint8List bytes;
  final _ZipFixtureKind kind;
}

Uint8List _zip(List<_ZipFixtureEntry> entries) {
  final output = BytesBuilder(copy: false);
  final central = BytesBuilder(copy: false);
  var localOffset = 0;
  for (final entry in entries) {
    final name = Uint8List.fromList(utf8.encode(entry.name));
    final crc = _fixtureCrc32(entry.bytes);
    final local = BytesBuilder(copy: false)
      ..add(_little32(0x04034b50))
      ..add(_little16(20))
      ..add(_little16(0x0800))
      ..add(_little16(0))
      ..add(_little16(0))
      ..add(_little16(0))
      ..add(_little32(crc))
      ..add(_little32(entry.bytes.length))
      ..add(_little32(entry.bytes.length))
      ..add(_little16(name.length))
      ..add(_little16(0))
      ..add(name)
      ..add(entry.bytes);
    final localBytes = local.takeBytes();
    output.add(localBytes);
    final mode = entry.kind == _ZipFixtureKind.symbolicLink ? 0xa1ff : 0x81a4;
    central
      ..add(_little32(0x02014b50))
      ..add(_little16((3 << 8) | 20))
      ..add(_little16(20))
      ..add(_little16(0x0800))
      ..add(_little16(0))
      ..add(_little16(0))
      ..add(_little16(0))
      ..add(_little32(crc))
      ..add(_little32(entry.bytes.length))
      ..add(_little32(entry.bytes.length))
      ..add(_little16(name.length))
      ..add(_little16(0))
      ..add(_little16(0))
      ..add(_little16(0))
      ..add(_little16(0))
      ..add(_little32(mode << 16))
      ..add(_little32(localOffset))
      ..add(name);
    localOffset += localBytes.length;
  }
  final centralBytes = central.takeBytes();
  output
    ..add(centralBytes)
    ..add(_little32(0x06054b50))
    ..add(_little16(0))
    ..add(_little16(0))
    ..add(_little16(entries.length))
    ..add(_little16(entries.length))
    ..add(_little32(centralBytes.length))
    ..add(_little32(localOffset))
    ..add(_little16(0));
  return output.takeBytes();
}

Uint8List _little16(int value) =>
    Uint8List.fromList([value & 0xff, (value >> 8) & 0xff]);

Uint8List _little32(int value) => Uint8List.fromList([
  value & 0xff,
  (value >> 8) & 0xff,
  (value >> 16) & 0xff,
  (value >> 24) & 0xff,
]);

int _fixtureCrc32(Uint8List bytes) {
  var crc = 0xffffffff;
  for (final byte in bytes) {
    var value = (crc ^ byte) & 0xff;
    for (var bit = 0; bit < 8; bit++) {
      value = value & 1 != 0 ? 0xedb88320 ^ (value >> 1) : value >> 1;
    }
    crc = value ^ (crc >> 8);
  }
  return (crc ^ 0xffffffff) & 0xffffffff;
}

Map<String, Object?> _object(Object? value) => value! as Map<String, Object?>;

List<Object?> _list(Object? value) => value! as List<Object?>;
