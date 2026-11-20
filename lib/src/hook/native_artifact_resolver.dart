import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:fonix/src/build/native_versions_lock.dart';
import 'package:path/path.dart' as path;

const String fonixNativeArtifactManifestFileName =
    'fonix-native-artifact-manifest.json';

const int _maxSourceArchiveBytes = 256 * 1024 * 1024;
const int _maxArchiveMembers = 8192;
const int _maxMemberCompressedBytes = 256 * 1024 * 1024;
const int _maxMemberUncompressedBytes = 512 * 1024 * 1024;
const int _maxArchiveUncompressedBytes = 768 * 1024 * 1024;
const int _tarBlockSize = 512;

enum NativeArtifactArchiveOrigin { cache, mirror }

final class NativeArtifactTarget {
  const NativeArtifactTarget({
    required this.operatingSystem,
    required this.architecture,
    required this.variant,
    this.flavor = 'cpu',
  });

  final String operatingSystem;
  final String architecture;
  final String variant;
  final String flavor;

  String get identity => '$operatingSystem/$architecture/$variant/$flavor';
}

final class StagedNativeArtifact {
  StagedNativeArtifact({
    required this.artifactId,
    required this.identity,
    required this.archiveFile,
    required this.archiveOrigin,
    required List<File> payloadFiles,
    required List<File> noticeFiles,
    required this.manifestFile,
  }) : payloadFiles = List.unmodifiable(payloadFiles),
       noticeFiles = List.unmodifiable(noticeFiles);

  final String artifactId;
  final NativeArtifactIdentity identity;
  final File archiveFile;
  final NativeArtifactArchiveOrigin archiveOrigin;
  final List<File> payloadFiles;
  final List<File> noticeFiles;
  final File manifestFile;
}

/// Closed identity compiled into the shim for final-artifact diagnostics.
///
/// Every value comes from the validated lock entry selected before archive
/// extraction. The notice digest identifies the exact upstream
/// `ThirdPartyNotices.txt` bytes that the consuming application must package.
final class NativeArtifactIdentity {
  NativeArtifactIdentity({
    required this.artifactId,
    required this.lockSha256,
    required this.sourceSha256,
    required this.operatingSystem,
    required this.architecture,
    required this.variant,
    required this.minimumOs,
    required this.flavor,
    required this.runtimeMode,
    required this.thirdPartyNoticesSha256,
    required Map<String, String?> providers,
  }) : providers = Map<String, String?>.unmodifiable(providers);

  final String artifactId;
  final String lockSha256;
  final String sourceSha256;
  final String operatingSystem;
  final String architecture;
  final String variant;
  final String minimumOs;
  final String flavor;
  final String runtimeMode;
  final String thirdPartyNoticesSha256;

  /// Exact provider inventory declared by the selected lock entry.
  final Map<String, String?> providers;
}

final class NativeArtifactResolutionException implements Exception {
  const NativeArtifactResolutionException(this.message);

  final String message;

  @override
  String toString() => 'NativeArtifactResolutionException: $message';
}

/// Resolves one exact native target from an explicit offline cache or mirror.
///
/// Every archive layer and selected member is checked against the lock before
/// use. Only locked payload and notice members are written, to a fresh staging
/// directory outside the package checkout. The resolver performs no network
/// access and never mutates the checkout or either input directory.
final class NativeArtifactResolver {
  const NativeArtifactResolver();

  Future<StagedNativeArtifact> stage({
    required Directory packageRoot,
    required Directory stagingDirectory,
    required NativeArtifactTarget target,
    Directory? cacheDirectory,
    Directory? mirrorDirectory,
  }) async {
    if (cacheDirectory == null && mirrorDirectory == null) {
      throw const NativeArtifactResolutionException(
        'Offline native artifact resolution requires an explicit cache '
        'directory, mirror directory, or both. Network fallback is disabled.',
      );
    }

    final canonicalPackageRoot = _canonicalPackageRoot(packageRoot);
    _validateFreshStagingDirectory(
      stagingDirectory,
      canonicalPackageRoot: canonicalPackageRoot,
    );
    final lockSource = _readLock(canonicalPackageRoot);
    final lock = _parseLock(lockSource.text);
    final artifact = _selectExactArtifact(lock, target);
    _validateArtifactBounds(artifact);
    final resolved = await _resolveArchive(
      artifact,
      cacheDirectory: cacheDirectory,
      mirrorDirectory: mirrorDirectory,
    );

    final outerBytes = await _readBoundedFile(
      resolved.file,
      maximumBytes: _maxSourceArchiveBytes,
    );
    final payloadBytes = <LockedArtifactFile, Uint8List>{};
    final noticeBytes = <LockedArtifactNotice, Uint8List>{};
    final inspections = <_ArchiveInspection>[];
    var containerBytes = outerBytes;
    var archiveFormat = artifact.source.archive;

    for (var depth = 0; depth <= artifact.containers.length; depth++) {
      final regularMembers = <String, _LockedRegularMember>{};
      for (final notice in artifact.notices.where(
        (notice) => notice.containerDepth == depth,
      )) {
        regularMembers[notice.relativePath] = _LockedRegularMember(
          relativePath: notice.relativePath,
          sha256: notice.sha256,
          sizeBytes: notice.sizeBytes,
          onBytes: (bytes) => noticeBytes[notice] = bytes,
        );
      }

      LockedArtifactContainer? nextContainer;
      if (depth < artifact.containers.length) {
        nextContainer = artifact.containers[depth];
        regularMembers[nextContainer.relativePath] = _LockedRegularMember(
          relativePath: nextContainer.relativePath,
          sha256: nextContainer.sha256,
          sizeBytes: nextContainer.sizeBytes,
          onBytes: (bytes) => containerBytes = bytes,
        );
      } else {
        for (final file in artifact.expectedFiles) {
          regularMembers[file.relativePath] = _LockedRegularMember(
            relativePath: file.relativePath,
            sha256: file.sha256,
            sizeBytes: file.sizeBytes,
            onBytes: (bytes) => payloadBytes[file] = bytes,
          );
        }
      }

      final inspection = await _inspectArchive(
        bytes: containerBytes,
        format: archiveFormat,
        regularMembers: regularMembers,
        expectedSymlinks: depth == artifact.containers.length
            ? {
                for (final symlink in artifact.expectedSymlinks)
                  symlink.relativePath: symlink,
              }
            : const {},
      );
      inspections.add(inspection);
      if (nextContainer != null) {
        archiveFormat = nextContainer.archive;
      }
    }

    if (payloadBytes.length != artifact.expectedFiles.length ||
        noticeBytes.length != artifact.notices.length) {
      throw const NativeArtifactResolutionException(
        'The verified artifact did not yield the complete locked payload.',
      );
    }

    var created = false;
    try {
      stagingDirectory.createSync(recursive: true);
      created = true;
      final payloadFiles = _materializePayload(stagingDirectory, payloadBytes);
      final noticeFiles = _materializeNotices(stagingDirectory, noticeBytes);
      final identity = _nativeArtifactIdentity(
        artifact,
        lockSha256: lockSource.sha256,
      );
      final manifestFile = _materializeManifest(
        stagingDirectory,
        lock: lock,
        lockSha256: lockSource.sha256,
        artifact: artifact,
        inspections: inspections,
      );
      return StagedNativeArtifact(
        artifactId: artifact.id,
        identity: identity,
        archiveFile: resolved.file,
        archiveOrigin: resolved.origin,
        payloadFiles: payloadFiles,
        noticeFiles: noticeFiles,
        manifestFile: manifestFile,
      );
    } on Object {
      if (created && stagingDirectory.existsSync()) {
        stagingDirectory.deleteSync(recursive: true);
      }
      rethrow;
    }
  }
}

NativeArtifactIdentity _nativeArtifactIdentity(
  NativeArtifactLock artifact, {
  required String lockSha256,
}) {
  final thirdPartyNotices = artifact.notices
      .where(
        (notice) =>
            notice.id == 'ThirdPartyNotices' &&
            notice.stagedPath == 'notices/ThirdPartyNotices.txt',
      )
      .toList(growable: false);
  if (thirdPartyNotices.length != 1) {
    throw NativeArtifactResolutionException(
      'Artifact ${artifact.id} must lock exactly one canonical '
      'ThirdPartyNotices.txt payload.',
    );
  }
  return NativeArtifactIdentity(
    artifactId: artifact.id,
    lockSha256: lockSha256,
    sourceSha256: artifact.source.sha256,
    operatingSystem: artifact.target.operatingSystem,
    architecture: artifact.target.architecture,
    variant: artifact.target.variant,
    minimumOs: artifact.target.minimumOs,
    flavor: artifact.flavor,
    runtimeMode: artifact.runtimeMode.name,
    thirdPartyNoticesSha256: thirdPartyNotices.single.sha256,
    providers: Map<String, String?>.unmodifiable(<String, String?>{
      for (final provider in artifact.providers)
        provider.wrapperId: provider.reportedName,
    }),
  );
}

({String text, String sha256}) _readLock(String canonicalPackageRoot) {
  final lockFile = File(
    path.join(canonicalPackageRoot, 'native', 'versions.lock.yaml'),
  );
  if (FileSystemEntity.typeSync(lockFile.path, followLinks: false) !=
      FileSystemEntityType.file) {
    throw const NativeArtifactResolutionException(
      'native/versions.lock.yaml must be a regular, non-symbolic-link file.',
    );
  }
  if (lockFile.lengthSync() > nativeVersionsLockMaxBytes) {
    throw const NativeArtifactResolutionException(
      'native/versions.lock.yaml exceeds the bounded lockfile size.',
    );
  }
  final bytes = lockFile.readAsBytesSync();
  final String text;
  try {
    text = utf8.decode(bytes, allowMalformed: false);
  } on FormatException catch (error) {
    throw NativeArtifactResolutionException(
      'native/versions.lock.yaml is not valid UTF-8: ${error.message}',
    );
  }
  return (text: text, sha256: sha256.convert(bytes).toString());
}

NativeVersionsLock _parseLock(String source) {
  try {
    return NativeVersionsLock.parse(source);
  } on FormatException catch (error) {
    throw NativeArtifactResolutionException(
      'native/versions.lock.yaml is invalid: ${error.message}',
    );
  }
}

NativeArtifactLock _selectExactArtifact(
  NativeVersionsLock lock,
  NativeArtifactTarget target,
) {
  final matches = lock.artifacts
      .where(
        (artifact) =>
            artifact.target.operatingSystem == target.operatingSystem &&
            artifact.target.architecture == target.architecture &&
            artifact.target.variant == target.variant &&
            artifact.flavor == target.flavor,
      )
      .toList(growable: false);
  if (matches.length != 1) {
    throw NativeArtifactResolutionException(
      matches.isEmpty
          ? 'The lock has no artifact for exact tuple ${target.identity}.'
          : 'The lock ambiguously declares ${matches.length} artifacts for '
                'exact tuple ${target.identity}.',
    );
  }
  return matches.single;
}

void _validateArtifactBounds(NativeArtifactLock artifact) {
  if (artifact.source.sizeBytes > _maxSourceArchiveBytes) {
    throw const NativeArtifactResolutionException(
      'The locked source archive exceeds the 256 MiB input bound.',
    );
  }
  for (final container in artifact.containers) {
    if (container.sizeBytes > _maxMemberUncompressedBytes) {
      throw NativeArtifactResolutionException(
        'Nested container ${container.relativePath} exceeds the 512 MiB '
        'member bound.',
      );
    }
  }
  for (final file in artifact.expectedFiles) {
    if (file.sizeBytes > _maxMemberUncompressedBytes) {
      throw NativeArtifactResolutionException(
        'Payload ${file.relativePath} exceeds the 512 MiB member bound.',
      );
    }
  }
  for (final notice in artifact.notices) {
    if (notice.sizeBytes > _maxMemberUncompressedBytes) {
      throw NativeArtifactResolutionException(
        'Notice ${notice.relativePath} exceeds the 512 MiB member bound.',
      );
    }
  }
}

Future<_ResolvedArchive> _resolveArchive(
  NativeArtifactLock artifact, {
  required Directory? cacheDirectory,
  required Directory? mirrorDirectory,
}) async {
  final archiveName = path.posix.basename(artifact.source.url.path);
  if (archiveName.isEmpty ||
      archiveName == '.' ||
      archiveName == '..' ||
      archiveName.contains('\\') ||
      path.basename(archiveName) != archiveName ||
      archiveName.codeUnits.any(
        (value) => value <= 0x1f || (value >= 0x7f && value <= 0x9f),
      )) {
    throw const NativeArtifactResolutionException(
      'The locked source URL has no safe archive basename.',
    );
  }
  final candidates = <_ArchiveCandidate>[
    if (cacheDirectory != null)
      _ArchiveCandidate(
        origin: NativeArtifactArchiveOrigin.cache,
        file: File(path.join(cacheDirectory.path, archiveName)),
      ),
    if (mirrorDirectory != null)
      _ArchiveCandidate(
        origin: NativeArtifactArchiveOrigin.mirror,
        file: File(path.join(mirrorDirectory.path, archiveName)),
      ),
  ];
  for (final candidate in candidates) {
    final type = FileSystemEntity.typeSync(
      candidate.file.path,
      followLinks: false,
    );
    if (type == FileSystemEntityType.notFound) {
      continue;
    }
    if (type != FileSystemEntityType.file) {
      throw NativeArtifactResolutionException(
        '${candidate.origin.name} archive must be a regular, '
        'non-symbolic-link file: ${candidate.file.path}',
      );
    }
    await _verifyArchiveFile(candidate.file, artifact.source);
    return _ResolvedArchive(file: candidate.file, origin: candidate.origin);
  }
  throw NativeArtifactResolutionException(
    'Offline native artifact miss for $archiveName. No network request was '
    'attempted.',
  );
}

Future<void> _verifyArchiveFile(
  File archive,
  LockedArtifactSource expected,
) async {
  final actualSize = archive.lengthSync();
  if (actualSize != expected.sizeBytes) {
    throw NativeArtifactResolutionException(
      '${archive.path} has size $actualSize; expected ${expected.sizeBytes}.',
    );
  }
  final actualDigest = (await sha256.bind(archive.openRead()).first).toString();
  if (actualDigest != expected.sha256) {
    throw NativeArtifactResolutionException(
      '${archive.path} has SHA-256 $actualDigest; expected ${expected.sha256}.',
    );
  }
}

Future<Uint8List> _readBoundedFile(
  File file, {
  required int maximumBytes,
}) async {
  final builder = BytesBuilder(copy: false);
  var total = 0;
  await for (final chunk in file.openRead()) {
    total += chunk.length;
    if (total > maximumBytes) {
      throw const NativeArtifactResolutionException(
        'The source archive exceeds the bounded input size.',
      );
    }
    builder.add(chunk);
  }
  return builder.takeBytes();
}

List<File> _materializePayload(
  Directory stagingDirectory,
  Map<LockedArtifactFile, Uint8List> bytesByFile,
) {
  final entries = bytesByFile.entries.toList(
    growable: false,
  )..sort((left, right) => left.key.stagedPath.compareTo(right.key.stagedPath));
  return [
    for (final entry in entries)
      _writeStagedFile(stagingDirectory, entry.key.stagedPath, entry.value),
  ];
}

List<File> _materializeNotices(
  Directory stagingDirectory,
  Map<LockedArtifactNotice, Uint8List> bytesByNotice,
) {
  final entries = bytesByNotice.entries.toList(
    growable: false,
  )..sort((left, right) => left.key.stagedPath.compareTo(right.key.stagedPath));
  return [
    for (final entry in entries)
      _writeStagedFile(stagingDirectory, entry.key.stagedPath, entry.value),
  ];
}

File _writeStagedFile(
  Directory stagingDirectory,
  String relativePath,
  Uint8List bytes,
) {
  final file = File(
    path.joinAll(<String>[stagingDirectory.path, ...relativePath.split('/')]),
  );
  file.parent.createSync(recursive: true);
  if (FileSystemEntity.typeSync(file.path, followLinks: false) !=
      FileSystemEntityType.notFound) {
    throw NativeArtifactResolutionException(
      'A staged path unexpectedly already exists: $relativePath',
    );
  }
  file.writeAsBytesSync(bytes, flush: true);
  return file;
}

File _materializeManifest(
  Directory stagingDirectory, {
  required NativeVersionsLock lock,
  required String lockSha256,
  required NativeArtifactLock artifact,
  required List<_ArchiveInspection> inspections,
}) {
  final containers = artifact.containers.toList(growable: false);
  final payloads = artifact.expectedFiles.toList(growable: false)
    ..sort((left, right) => left.stagedPath.compareTo(right.stagedPath));
  final notices = artifact.notices.toList(growable: false)
    ..sort((left, right) => left.stagedPath.compareTo(right.stagedPath));
  final symlinks = artifact.expectedSymlinks.toList(growable: false)
    ..sort((left, right) => left.relativePath.compareTo(right.relativePath));
  final manifest = <String, Object?>{
    'schema': 2,
    'artifactId': artifact.id,
    'lock': <String, Object?>{
      'path': 'native/versions.lock.yaml',
      'sha256': lockSha256,
      'snapshotDate': _date(lock.snapshotDate),
      'releaseState': lock.releaseState,
    },
    'target': <String, Object?>{
      'os': artifact.target.operatingSystem,
      'architecture': artifact.target.architecture,
      'variant': artifact.target.variant,
      'minimumOs': artifact.target.minimumOs,
      'flavor': artifact.flavor,
      'runtimeMode': artifact.runtimeMode.name,
    },
    'source': <String, Object?>{
      'url': artifact.source.url.toString(),
      'sourceRevision': artifact.source.sourceRevision,
      'archive': artifact.source.archive,
      'sha256': artifact.source.sha256,
      'sizeBytes': artifact.source.sizeBytes,
    },
    'containers': <Object?>[
      for (var index = 0; index < containers.length; index++)
        <String, Object?>{
          'depth': index + 1,
          'path': containers[index].relativePath,
          'archive': containers[index].archive,
          'sha256': containers[index].sha256,
          'sizeBytes': containers[index].sizeBytes,
        },
    ],
    'payloadFiles': <Object?>[
      for (final file in payloads)
        <String, Object?>{
          'archivePath': file.relativePath,
          'stagedPath': file.stagedPath,
          'sha256': file.sha256,
          'sizeBytes': file.sizeBytes,
        },
    ],
    'verifiedSymlinks': <Object?>[
      for (final symlink in symlinks)
        <String, Object?>{
          'path': symlink.relativePath,
          'target': symlink.target,
        },
    ],
    'notices': <Object?>[
      for (final notice in notices)
        <String, Object?>{
          'id': notice.id,
          'containerDepth': notice.containerDepth,
          'archivePath': notice.relativePath,
          'stagedPath': notice.stagedPath,
          'sha256': notice.sha256,
          'sizeBytes': notice.sizeBytes,
        },
    ],
    'archiveInspections': <Object?>[
      for (var index = 0; index < inspections.length; index++)
        <String, Object?>{
          'depth': index,
          'format': inspections[index].format,
          'memberCount': inspections[index].memberCount,
          'regularFileCount': inspections[index].regularFileCount,
          'directoryCount': inspections[index].directoryCount,
          'symbolicLinkCount': inspections[index].symbolicLinkCount,
          'compressedBytes': inspections[index].compressedBytes,
          'uncompressedBytes': inspections[index].uncompressedBytes,
        },
    ],
    'claimBoundary':
        'Successful staging proves exact archive and member bytes only. It '
        'does not prove loading, linking, provider registration, inference, '
        'packaging, signing, or target-device support.',
  };
  final manifestFile = File(
    path.join(stagingDirectory.path, fonixNativeArtifactManifestFileName),
  );
  manifestFile.writeAsStringSync(
    '${const JsonEncoder.withIndent('  ').convert(manifest)}\n',
    flush: true,
  );
  return manifestFile;
}

String _canonicalPackageRoot(Directory packageRoot) {
  if (FileSystemEntity.typeSync(packageRoot.path, followLinks: true) !=
      FileSystemEntityType.directory) {
    throw const NativeArtifactResolutionException(
      'The Fonix package root must be an existing directory.',
    );
  }
  return packageRoot.resolveSymbolicLinksSync();
}

void _validateFreshStagingDirectory(
  Directory stagingDirectory, {
  required String canonicalPackageRoot,
}) {
  if (FileSystemEntity.typeSync(stagingDirectory.path, followLinks: false) !=
      FileSystemEntityType.notFound) {
    throw const NativeArtifactResolutionException(
      'The native artifact staging directory must not already exist.',
    );
  }
  final prospectivePath = _canonicalProspectivePath(stagingDirectory.path);
  if (path.equals(prospectivePath, canonicalPackageRoot) ||
      path.isWithin(canonicalPackageRoot, prospectivePath)) {
    throw const NativeArtifactResolutionException(
      'The native artifact staging directory must be outside the checkout.',
    );
  }
}

String _canonicalProspectivePath(String candidate) {
  var cursor = path.normalize(path.absolute(candidate));
  final missingSegments = <String>[];
  while (FileSystemEntity.typeSync(cursor, followLinks: false) ==
      FileSystemEntityType.notFound) {
    final parent = path.dirname(cursor);
    if (parent == cursor) {
      break;
    }
    missingSegments.add(path.basename(cursor));
    cursor = parent;
  }
  if (FileSystemEntity.typeSync(cursor, followLinks: true) !=
      FileSystemEntityType.directory) {
    throw const NativeArtifactResolutionException(
      'The staging path must have an existing directory ancestor.',
    );
  }
  var resolved = Directory(cursor).resolveSymbolicLinksSync();
  for (final segment in missingSegments.reversed) {
    resolved = path.join(resolved, segment);
  }
  return path.normalize(resolved);
}

String _date(DateTime value) {
  final year = value.year.toString().padLeft(4, '0');
  final month = value.month.toString().padLeft(2, '0');
  final day = value.day.toString().padLeft(2, '0');
  return '$year-$month-$day';
}

Future<_ArchiveInspection> _inspectArchive({
  required Uint8List bytes,
  required String format,
  required Map<String, _LockedRegularMember> regularMembers,
  required Map<String, LockedArtifactSymlink> expectedSymlinks,
}) async {
  switch (format) {
    case 'zip':
      return await _ZipInspector(
        regularMembers: regularMembers,
        expectedSymlinks: expectedSymlinks,
      ).inspect(bytes);
    case 'tgz':
    case 'tar.gz':
      final tarBytes = await _decodeGzipBounded(bytes);
      return _TarInspector(
        regularMembers: regularMembers,
        expectedSymlinks: expectedSymlinks,
      ).inspect(tarBytes).withCompressedBytes(bytes.length);
    default:
      throw NativeArtifactResolutionException(
        'Unsupported locked archive format "$format".',
      );
  }
}

Future<Uint8List> _decodeGzipBounded(Uint8List bytes) async {
  final builder = BytesBuilder(copy: false);
  var total = 0;
  try {
    await for (final chunk in gzip.decoder.bind(Stream.value(bytes))) {
      total += chunk.length;
      if (total > _maxArchiveUncompressedBytes) {
        throw const NativeArtifactResolutionException(
          'A gzip container exceeds the 768 MiB uncompressed bound.',
        );
      }
      builder.add(chunk);
    }
  } on NativeArtifactResolutionException {
    rethrow;
  } on Object catch (error) {
    throw NativeArtifactResolutionException(
      'A locked tgz is not a valid gzip stream: $error',
    );
  }
  return builder.takeBytes();
}

final class _ArchiveCandidate {
  const _ArchiveCandidate({required this.origin, required this.file});

  final NativeArtifactArchiveOrigin origin;
  final File file;
}

final class _ResolvedArchive {
  const _ResolvedArchive({required this.file, required this.origin});

  final File file;
  final NativeArtifactArchiveOrigin origin;
}

final class _LockedRegularMember {
  const _LockedRegularMember({
    required this.relativePath,
    required this.sha256,
    required this.sizeBytes,
    required this.onBytes,
  });

  final String relativePath;
  final String sha256;
  final int sizeBytes;
  final void Function(Uint8List bytes) onBytes;
}

final class _ArchiveInspection {
  const _ArchiveInspection({
    required this.format,
    required this.memberCount,
    required this.regularFileCount,
    required this.directoryCount,
    required this.symbolicLinkCount,
    required this.compressedBytes,
    required this.uncompressedBytes,
  });

  final String format;
  final int memberCount;
  final int regularFileCount;
  final int directoryCount;
  final int symbolicLinkCount;
  final int compressedBytes;
  final int uncompressedBytes;

  _ArchiveInspection withCompressedBytes(int value) => _ArchiveInspection(
    format: format,
    memberCount: memberCount,
    regularFileCount: regularFileCount,
    directoryCount: directoryCount,
    symbolicLinkCount: symbolicLinkCount,
    compressedBytes: value,
    uncompressedBytes: uncompressedBytes,
  );
}

final class _TarInspector {
  const _TarInspector({
    required this.regularMembers,
    required this.expectedSymlinks,
  });

  final Map<String, _LockedRegularMember> regularMembers;
  final Map<String, LockedArtifactSymlink> expectedSymlinks;

  _ArchiveInspection inspect(Uint8List bytes) {
    if (bytes.length > _maxArchiveUncompressedBytes ||
        bytes.length % _tarBlockSize != 0) {
      throw const NativeArtifactResolutionException(
        'The tar stream is oversized or not 512-byte aligned.',
      );
    }
    final seenPaths = <String>{};
    final admittedMembers = <String>{};
    final admittedSymlinks = <String>{};
    var offset = 0;
    var zeroBlocks = 0;
    var memberCount = 0;
    var regularFileCount = 0;
    var directoryCount = 0;
    var symbolicLinkCount = 0;
    var totalRegularBytes = 0;

    while (offset < bytes.length) {
      if (_isZeroBlock(bytes, offset)) {
        zeroBlocks += 1;
        offset += _tarBlockSize;
        if (zeroBlocks == 2) {
          break;
        }
        continue;
      }
      if (zeroBlocks != 0) {
        throw const NativeArtifactResolutionException(
          'The tar stream has data after an incomplete end marker.',
        );
      }
      memberCount += 1;
      if (memberCount > _maxArchiveMembers) {
        throw const NativeArtifactResolutionException(
          'The tar stream exceeds the 8192-member bound.',
        );
      }
      _verifyTarChecksum(bytes, offset);
      final memberPath = _tarMemberPath(bytes, offset);
      if (!seenPaths.add(memberPath)) {
        throw NativeArtifactResolutionException(
          'The tar stream duplicates member "$memberPath".',
        );
      }
      final size = _parseTarNumber(bytes, offset + 124, 12, 'member size');
      if (size > _maxMemberUncompressedBytes) {
        throw NativeArtifactResolutionException(
          'Tar member $memberPath exceeds the 512 MiB member bound.',
        );
      }
      final dataStart = offset + _tarBlockSize;
      final paddedSize =
          ((size + _tarBlockSize - 1) ~/ _tarBlockSize) * _tarBlockSize;
      final nextOffset = dataStart + paddedSize;
      if (nextOffset > bytes.length) {
        throw NativeArtifactResolutionException(
          'Tar member $memberPath exceeds its container bounds.',
        );
      }

      switch (bytes[offset + 156]) {
        case 0:
        case 48:
          regularFileCount += 1;
          totalRegularBytes += size;
          if (totalRegularBytes > _maxArchiveUncompressedBytes) {
            throw const NativeArtifactResolutionException(
              'Tar regular members exceed the 768 MiB aggregate bound.',
            );
          }
          final expected = regularMembers[memberPath];
          if (expected != null) {
            final memberBytes = Uint8List.sublistView(
              bytes,
              dataStart,
              dataStart + size,
            );
            _admitRegular(expected, memberBytes);
            admittedMembers.add(memberPath);
          }
        case 50:
          symbolicLinkCount += 1;
          if (size != 0) {
            throw NativeArtifactResolutionException(
              'Tar symbolic link $memberPath has a non-zero payload.',
            );
          }
          final target = _tarString(bytes, offset + 157, 100, 'link target');
          _validateSymlinkTarget(memberPath, target);
          final expected = expectedSymlinks[memberPath];
          if (expected != null) {
            if (target != expected.target) {
              throw NativeArtifactResolutionException(
                'Tar symbolic link $memberPath targets "$target"; expected '
                '"${expected.target}".',
              );
            }
            admittedSymlinks.add(memberPath);
          }
        case 53:
          directoryCount += 1;
          if (size != 0) {
            throw NativeArtifactResolutionException(
              'Tar directory $memberPath has a non-zero payload.',
            );
          }
          if (regularMembers.containsKey(memberPath) ||
              expectedSymlinks.containsKey(memberPath)) {
            throw NativeArtifactResolutionException(
              'Expected tar payload $memberPath is a directory.',
            );
          }
        default:
          throw NativeArtifactResolutionException(
            'The tar stream contains unsupported member type '
            '${bytes[offset + 156]} at $memberPath.',
          );
      }
      offset = nextOffset;
    }

    if (zeroBlocks != 2) {
      throw const NativeArtifactResolutionException(
        'The tar stream is missing its two-block end marker.',
      );
    }
    for (var index = offset; index < bytes.length; index++) {
      if (bytes[index] != 0) {
        throw const NativeArtifactResolutionException(
          'The tar stream contains non-zero trailing data.',
        );
      }
    }
    _checkMissing(regularMembers.keys, admittedMembers, kind: 'regular files');
    _checkMissing(
      expectedSymlinks.keys,
      admittedSymlinks,
      kind: 'symbolic links',
    );
    return _ArchiveInspection(
      format: 'tar',
      memberCount: memberCount,
      regularFileCount: regularFileCount,
      directoryCount: directoryCount,
      symbolicLinkCount: symbolicLinkCount,
      compressedBytes: 0,
      uncompressedBytes: totalRegularBytes,
    );
  }
}

bool _isZeroBlock(Uint8List bytes, int offset) {
  if (offset + _tarBlockSize > bytes.length) {
    return false;
  }
  for (var index = offset; index < offset + _tarBlockSize; index++) {
    if (bytes[index] != 0) {
      return false;
    }
  }
  return true;
}

void _verifyTarChecksum(Uint8List bytes, int offset) {
  if (offset + _tarBlockSize > bytes.length) {
    throw const NativeArtifactResolutionException(
      'The tar stream ends inside a header block.',
    );
  }
  final expected = _parseTarNumber(bytes, offset + 148, 8, 'checksum');
  var actual = 0;
  for (var index = 0; index < _tarBlockSize; index++) {
    actual += index >= 148 && index < 156 ? 32 : bytes[offset + index];
  }
  if (actual != expected) {
    throw NativeArtifactResolutionException(
      'The tar header checksum is $actual; expected $expected.',
    );
  }
}

String _tarMemberPath(Uint8List bytes, int offset) {
  final name = _tarString(bytes, offset, 100, 'member name');
  final magic = _tarString(bytes, offset + 257, 6, 'ustar magic');
  final version = Uint8List.sublistView(bytes, offset + 263, offset + 265);
  final isPosixUstar = magic == 'ustar' && version[0] == 48 && version[1] == 48;
  final isGnuUstar = magic == 'ustar ' && version[0] == 32 && version[1] == 0;
  if (!isPosixUstar && !isGnuUstar) {
    throw const NativeArtifactResolutionException(
      'Tar containers must use bounded POSIX or GNU ustar headers.',
    );
  }
  final prefix = isPosixUstar
      ? _tarString(bytes, offset + 345, 155, 'member prefix')
      : '';
  var result = prefix.isEmpty ? name : '$prefix/$name';
  while (result.endsWith('/')) {
    result = result.substring(0, result.length - 1);
  }
  return _validateArchivePath(result, kind: 'tar member');
}

String _tarString(Uint8List bytes, int offset, int length, String fieldName) {
  var end = offset;
  final limit = offset + length;
  while (end < limit && bytes[end] != 0) {
    end += 1;
  }
  for (var index = end; index < limit; index++) {
    if (bytes[index] != 0) {
      throw NativeArtifactResolutionException(
        'The tar $fieldName contains data after its NUL terminator.',
      );
    }
  }
  try {
    return utf8.decode(bytes.sublist(offset, end), allowMalformed: false);
  } on FormatException {
    throw NativeArtifactResolutionException(
      'The tar $fieldName is not strict UTF-8.',
    );
  }
}

int _parseTarNumber(Uint8List bytes, int offset, int length, String fieldName) {
  if (bytes[offset] & 0x80 != 0) {
    throw NativeArtifactResolutionException(
      'The tar $fieldName uses unsupported base-256 encoding.',
    );
  }
  var value = 0;
  var sawDigit = false;
  var ended = false;
  for (var index = offset; index < offset + length; index++) {
    final byte = bytes[index];
    if (byte == 0 || byte == 32) {
      if (sawDigit) {
        ended = true;
      }
      continue;
    }
    if (ended || byte < 48 || byte > 55) {
      throw NativeArtifactResolutionException(
        'The tar $fieldName is not a canonical octal value.',
      );
    }
    sawDigit = true;
    value = (value << 3) + byte - 48;
    if (value > _maxArchiveUncompressedBytes) {
      throw NativeArtifactResolutionException(
        'The tar $fieldName exceeds the numeric bound.',
      );
    }
  }
  return value;
}

final class _ZipInspector {
  const _ZipInspector({
    required this.regularMembers,
    required this.expectedSymlinks,
  });

  final Map<String, _LockedRegularMember> regularMembers;
  final Map<String, LockedArtifactSymlink> expectedSymlinks;

  Future<_ArchiveInspection> inspect(Uint8List bytes) async {
    final eocdOffset = _findZipEocd(bytes);
    final diskNumber = _u16(bytes, eocdOffset + 4);
    final centralDisk = _u16(bytes, eocdOffset + 6);
    final diskEntries = _u16(bytes, eocdOffset + 8);
    final totalEntries = _u16(bytes, eocdOffset + 10);
    final centralSize = _u32(bytes, eocdOffset + 12);
    final centralOffset = _u32(bytes, eocdOffset + 16);
    final commentLength = _u16(bytes, eocdOffset + 20);
    if (diskNumber != 0 ||
        centralDisk != 0 ||
        diskEntries != totalEntries ||
        totalEntries == 0xffff ||
        centralSize == 0xffffffff ||
        centralOffset == 0xffffffff) {
      throw const NativeArtifactResolutionException(
        'Multi-disk and Zip64 containers are unsupported.',
      );
    }
    if (totalEntries > _maxArchiveMembers) {
      throw const NativeArtifactResolutionException(
        'The zip container exceeds the 8192-member bound.',
      );
    }
    if (eocdOffset + 22 + commentLength != bytes.length ||
        centralOffset + centralSize != eocdOffset ||
        commentLength != 0) {
      throw const NativeArtifactResolutionException(
        'The zip end record or central-directory bounds are inconsistent.',
      );
    }

    final entries = <_ZipEntry>[];
    final seenPaths = <String>{};
    var centralCursor = centralOffset;
    var compressedTotal = 0;
    var uncompressedTotal = 0;
    for (var index = 0; index < totalEntries; index++) {
      if (_u32(bytes, centralCursor) != 0x02014b50) {
        throw const NativeArtifactResolutionException(
          'The zip central directory has an invalid entry signature.',
        );
      }
      final madeBy = _u16(bytes, centralCursor + 4);
      final requiredVersion = _u16(bytes, centralCursor + 6);
      final flags = _u16(bytes, centralCursor + 8);
      final method = _u16(bytes, centralCursor + 10);
      final crc = _u32(bytes, centralCursor + 16);
      final compressedSize = _u32(bytes, centralCursor + 20);
      final uncompressedSize = _u32(bytes, centralCursor + 24);
      final nameLength = _u16(bytes, centralCursor + 28);
      final extraLength = _u16(bytes, centralCursor + 30);
      final fileCommentLength = _u16(bytes, centralCursor + 32);
      final startDisk = _u16(bytes, centralCursor + 34);
      final externalAttributes = _u32(bytes, centralCursor + 38);
      final localOffset = _u32(bytes, centralCursor + 42);
      final recordEnd =
          centralCursor + 46 + nameLength + extraLength + fileCommentLength;
      if (recordEnd > eocdOffset) {
        throw const NativeArtifactResolutionException(
          'A zip central-directory entry exceeds its bounds.',
        );
      }
      if (requiredVersion > 63 ||
          startDisk != 0 ||
          compressedSize == 0xffffffff ||
          uncompressedSize == 0xffffffff ||
          localOffset == 0xffffffff) {
        throw const NativeArtifactResolutionException(
          'A zip entry requires unsupported extraction or Zip64 features.',
        );
      }
      _validateZipFlags(flags);
      if (method != 0 && method != 8) {
        throw NativeArtifactResolutionException(
          'Zip compression method $method is unsupported.',
        );
      }
      if (compressedSize > _maxMemberCompressedBytes ||
          uncompressedSize > _maxMemberUncompressedBytes) {
        throw const NativeArtifactResolutionException(
          'A zip member exceeds the compressed or uncompressed member bound.',
        );
      }
      if (method == 0 && compressedSize != uncompressedSize) {
        throw const NativeArtifactResolutionException(
          'A stored zip member has inconsistent sizes.',
        );
      }
      compressedTotal += compressedSize;
      uncompressedTotal += uncompressedSize;
      if (uncompressedTotal > _maxArchiveUncompressedBytes) {
        throw const NativeArtifactResolutionException(
          'Zip members exceed the 768 MiB aggregate uncompressed bound.',
        );
      }
      final rawName = Uint8List.sublistView(
        bytes,
        centralCursor + 46,
        centralCursor + 46 + nameLength,
      );
      final decodedName = _decodeZipName(rawName, flags);
      final trailingSlash = decodedName.endsWith('/');
      final memberPath = _validateArchivePath(
        trailingSlash
            ? decodedName.substring(0, decodedName.length - 1)
            : decodedName,
        kind: 'zip member',
      );
      if (!seenPaths.add(memberPath)) {
        throw NativeArtifactResolutionException(
          'The zip container duplicates member "$memberPath".',
        );
      }
      _validateZipExtras(bytes, centralCursor + 46 + nameLength, extraLength);
      if (fileCommentLength != 0) {
        throw const NativeArtifactResolutionException(
          'Per-entry zip comments are unsupported.',
        );
      }
      final kind = _zipMemberKind(
        madeBy: madeBy,
        externalAttributes: externalAttributes,
        trailingSlash: trailingSlash,
      );
      if (kind == _ZipMemberKind.directory &&
          (compressedSize != 0 || uncompressedSize != 0)) {
        throw NativeArtifactResolutionException(
          'Zip directory $memberPath has a non-zero payload.',
        );
      }
      entries.add(
        _ZipEntry(
          rawName: Uint8List.fromList(rawName),
          relativePath: memberPath,
          flags: flags,
          method: method,
          crc32: crc,
          compressedSize: compressedSize,
          uncompressedSize: uncompressedSize,
          localOffset: localOffset,
          kind: kind,
        ),
      );
      centralCursor = recordEnd;
    }
    if (centralCursor != eocdOffset) {
      throw const NativeArtifactResolutionException(
        'The zip central directory contains unparsed trailing bytes.',
      );
    }

    _validateZipLocalRecords(bytes, entries, centralOffset);
    final admittedMembers = <String>{};
    final admittedSymlinks = <String>{};
    var regularFileCount = 0;
    var directoryCount = 0;
    var symbolicLinkCount = 0;
    for (final entry in entries) {
      switch (entry.kind) {
        case _ZipMemberKind.regular:
          regularFileCount += 1;
          final expected = regularMembers[entry.relativePath];
          if (expected != null) {
            final memberBytes = await _extractZipEntry(bytes, entry);
            _admitRegular(expected, memberBytes);
            admittedMembers.add(entry.relativePath);
          }
        case _ZipMemberKind.directory:
          directoryCount += 1;
          if (regularMembers.containsKey(entry.relativePath) ||
              expectedSymlinks.containsKey(entry.relativePath)) {
            throw NativeArtifactResolutionException(
              'Expected zip payload ${entry.relativePath} is a directory.',
            );
          }
        case _ZipMemberKind.symbolicLink:
          symbolicLinkCount += 1;
          final targetBytes = await _extractZipEntry(bytes, entry);
          final String target;
          try {
            target = utf8.decode(targetBytes, allowMalformed: false);
          } on FormatException {
            throw NativeArtifactResolutionException(
              'Zip symbolic link ${entry.relativePath} has a non-UTF-8 target.',
            );
          }
          _validateSymlinkTarget(entry.relativePath, target);
          final expected = expectedSymlinks[entry.relativePath];
          if (expected != null) {
            if (target != expected.target) {
              throw NativeArtifactResolutionException(
                'Zip symbolic link ${entry.relativePath} targets "$target"; '
                'expected "${expected.target}".',
              );
            }
            admittedSymlinks.add(entry.relativePath);
          }
      }
    }
    _checkMissing(regularMembers.keys, admittedMembers, kind: 'regular files');
    _checkMissing(
      expectedSymlinks.keys,
      admittedSymlinks,
      kind: 'symbolic links',
    );
    return _ArchiveInspection(
      format: 'zip',
      memberCount: entries.length,
      regularFileCount: regularFileCount,
      directoryCount: directoryCount,
      symbolicLinkCount: symbolicLinkCount,
      compressedBytes: compressedTotal,
      uncompressedBytes: uncompressedTotal,
    );
  }
}

enum _ZipMemberKind { regular, directory, symbolicLink }

final class _ZipEntry {
  _ZipEntry({
    required this.rawName,
    required this.relativePath,
    required this.flags,
    required this.method,
    required this.crc32,
    required this.compressedSize,
    required this.uncompressedSize,
    required this.localOffset,
    required this.kind,
  });

  final Uint8List rawName;
  final String relativePath;
  final int flags;
  final int method;
  final int crc32;
  final int compressedSize;
  final int uncompressedSize;
  final int localOffset;
  final _ZipMemberKind kind;
  int dataOffset = 0;
  int dataEnd = 0;
}

int _findZipEocd(Uint8List bytes) {
  if (bytes.length < 22) {
    throw const NativeArtifactResolutionException(
      'The zip container is shorter than an end record.',
    );
  }
  final minimum = bytes.length > 22 + 0xffff ? bytes.length - 22 - 0xffff : 0;
  for (var offset = bytes.length - 22; offset >= minimum; offset--) {
    if (_u32(bytes, offset) == 0x06054b50) {
      return offset;
    }
  }
  throw const NativeArtifactResolutionException(
    'The zip container has no bounded end record.',
  );
}

void _validateZipFlags(int flags) {
  const allowed = 0x0806;
  if (flags & 0x0001 != 0 || flags & ~allowed != 0) {
    throw NativeArtifactResolutionException(
      'Zip general-purpose flags 0x${flags.toRadixString(16)} are unsupported.',
    );
  }
}

String _decodeZipName(Uint8List bytes, int flags) {
  if (bytes.isEmpty) {
    throw const NativeArtifactResolutionException(
      'A zip member has an empty name.',
    );
  }
  if (flags & 0x0800 == 0 && bytes.any((byte) => byte > 0x7f)) {
    throw const NativeArtifactResolutionException(
      'Non-ASCII legacy zip member names are unsupported; use UTF-8.',
    );
  }
  try {
    return utf8.decode(bytes, allowMalformed: false);
  } on FormatException {
    throw const NativeArtifactResolutionException(
      'A zip member name is not strict UTF-8.',
    );
  }
}

void _validateZipExtras(
  Uint8List bytes,
  int offset,
  int length, {
  bool allowRedundantZip64 = false,
  int? expectedUncompressedSize,
  int? expectedCompressedSize,
}) {
  final end = offset + length;
  if (offset < 0 || end > bytes.length) {
    throw const NativeArtifactResolutionException(
      'A zip extra field exceeds its record bounds.',
    );
  }
  final seen = <int>{};
  var cursor = offset;
  while (cursor < end) {
    if (cursor + 4 > end) {
      throw const NativeArtifactResolutionException(
        'A zip extra field has a truncated header.',
      );
    }
    final id = _u16(bytes, cursor);
    final dataLength = _u16(bytes, cursor + 2);
    cursor += 4;
    if (cursor + dataLength > end) {
      throw const NativeArtifactResolutionException(
        'A zip extra field has a truncated payload.',
      );
    }
    if (!seen.add(id)) {
      throw NativeArtifactResolutionException(
        'A zip record duplicates extra-field ID 0x${id.toRadixString(16)}.',
      );
    }
    if (id == 0x0001) {
      if (!allowRedundantZip64 ||
          dataLength != 16 ||
          _u64(bytes, cursor) != expectedUncompressedSize ||
          _u64(bytes, cursor + 8) != expectedCompressedSize) {
        throw const NativeArtifactResolutionException(
          'A zip record has unsupported or inconsistent Zip64 metadata.',
        );
      }
    } else if (id != 0x000a && id != 0x5455 && id != 0x7875) {
      throw NativeArtifactResolutionException(
        'Zip extra-field ID 0x${id.toRadixString(16)} is unsupported.',
      );
    }
    cursor += dataLength;
  }
}

_ZipMemberKind _zipMemberKind({
  required int madeBy,
  required int externalAttributes,
  required bool trailingSlash,
}) {
  final origin = madeBy >> 8;
  if (origin == 3) {
    final fileType = (externalAttributes >> 16) & 0xf000;
    final kind = switch (fileType) {
      0x4000 => _ZipMemberKind.directory,
      0x8000 => _ZipMemberKind.regular,
      0xa000 => _ZipMemberKind.symbolicLink,
      0 => trailingSlash ? _ZipMemberKind.directory : _ZipMemberKind.regular,
      _ => throw NativeArtifactResolutionException(
        'A zip member has unsupported Unix file type '
        '0x${fileType.toRadixString(16)}.',
      ),
    };
    if (trailingSlash != (kind == _ZipMemberKind.directory)) {
      throw const NativeArtifactResolutionException(
        'A zip member name and Unix file type disagree about directory type.',
      );
    }
    return kind;
  }
  if (origin != 0 && origin != 10) {
    throw NativeArtifactResolutionException(
      'Zip creator platform $origin is unsupported.',
    );
  }
  final dosDirectory = externalAttributes & 0x10 != 0;
  if (trailingSlash != dosDirectory) {
    throw const NativeArtifactResolutionException(
      'A zip member name and DOS attributes disagree about directory type.',
    );
  }
  return trailingSlash ? _ZipMemberKind.directory : _ZipMemberKind.regular;
}

void _validateZipLocalRecords(
  Uint8List bytes,
  List<_ZipEntry> entries,
  int centralOffset,
) {
  final byOffset = entries.toList(growable: false)
    ..sort((left, right) => left.localOffset.compareTo(right.localOffset));
  if (byOffset.isNotEmpty && byOffset.first.localOffset != 0) {
    throw const NativeArtifactResolutionException(
      'The zip container has unparsed bytes before its first local record.',
    );
  }
  for (var index = 0; index < byOffset.length; index++) {
    final entry = byOffset[index];
    final offset = entry.localOffset;
    if (offset >= centralOffset || _u32(bytes, offset) != 0x04034b50) {
      throw NativeArtifactResolutionException(
        'Zip member ${entry.relativePath} has an invalid local header.',
      );
    }
    final flags = _u16(bytes, offset + 6);
    final method = _u16(bytes, offset + 8);
    final localCrc = _u32(bytes, offset + 14);
    final localCompressed = _u32(bytes, offset + 18);
    final localUncompressed = _u32(bytes, offset + 22);
    final nameLength = _u16(bytes, offset + 26);
    final extraLength = _u16(bytes, offset + 28);
    final headerEnd = offset + 30 + nameLength + extraLength;
    if (headerEnd > centralOffset ||
        flags != entry.flags ||
        method != entry.method) {
      throw NativeArtifactResolutionException(
        'Zip member ${entry.relativePath} has inconsistent local metadata.',
      );
    }
    final localName = Uint8List.sublistView(
      bytes,
      offset + 30,
      offset + 30 + nameLength,
    );
    if (!_bytesEqual(localName, entry.rawName)) {
      throw NativeArtifactResolutionException(
        'Zip member ${entry.relativePath} has mismatched local and central names.',
      );
    }
    _validateZipExtras(
      bytes,
      offset + 30 + nameLength,
      extraLength,
      allowRedundantZip64: true,
      expectedUncompressedSize: entry.uncompressedSize,
      expectedCompressedSize: entry.compressedSize,
    );
    if (localCrc != entry.crc32 ||
        localCompressed != entry.compressedSize ||
        localUncompressed != entry.uncompressedSize) {
      throw NativeArtifactResolutionException(
        'Zip member ${entry.relativePath} has mismatched local sizes or CRC.',
      );
    }
    entry.dataOffset = headerEnd;
    entry.dataEnd = headerEnd + entry.compressedSize;
    final nextBoundary = index + 1 < byOffset.length
        ? byOffset[index + 1].localOffset
        : centralOffset;
    if (entry.dataEnd != nextBoundary) {
      throw NativeArtifactResolutionException(
        'Zip member ${entry.relativePath} overlaps another record or leaves '
        'unparsed bytes.',
      );
    }
  }
}

Future<Uint8List> _extractZipEntry(Uint8List archive, _ZipEntry entry) async {
  final compressed = Uint8List.sublistView(
    archive,
    entry.dataOffset,
    entry.dataEnd,
  );
  final Uint8List result;
  if (entry.method == 0) {
    result = Uint8List.fromList(compressed);
  } else {
    final builder = BytesBuilder(copy: false);
    var total = 0;
    try {
      await for (final chunk in ZLibDecoder(
        raw: true,
      ).bind(Stream.value(compressed))) {
        total += chunk.length;
        if (total > entry.uncompressedSize ||
            total > _maxMemberUncompressedBytes) {
          throw NativeArtifactResolutionException(
            'Zip member ${entry.relativePath} exceeds its declared size.',
          );
        }
        builder.add(chunk);
      }
    } on NativeArtifactResolutionException {
      rethrow;
    } on Object catch (error) {
      throw NativeArtifactResolutionException(
        'Zip member ${entry.relativePath} has invalid deflate data: $error',
      );
    }
    result = builder.takeBytes();
  }
  if (result.length != entry.uncompressedSize) {
    throw NativeArtifactResolutionException(
      'Zip member ${entry.relativePath} has ${result.length} bytes; expected '
      '${entry.uncompressedSize}.',
    );
  }
  final actualCrc = _crc32(result);
  if (actualCrc != entry.crc32) {
    throw NativeArtifactResolutionException(
      'Zip member ${entry.relativePath} has CRC-32 '
      '0x${actualCrc.toRadixString(16)}; expected '
      '0x${entry.crc32.toRadixString(16)}.',
    );
  }
  return result;
}

void _admitRegular(_LockedRegularMember expected, Uint8List bytes) {
  if (bytes.length != expected.sizeBytes) {
    throw NativeArtifactResolutionException(
      'Archive member ${expected.relativePath} has size ${bytes.length}; '
      'expected ${expected.sizeBytes}.',
    );
  }
  final actualDigest = sha256.convert(bytes).toString();
  if (actualDigest != expected.sha256) {
    throw NativeArtifactResolutionException(
      'Archive member ${expected.relativePath} has SHA-256 $actualDigest; '
      'expected ${expected.sha256}.',
    );
  }
  expected.onBytes(bytes);
}

void _checkMissing(
  Iterable<String> expected,
  Set<String> admitted, {
  required String kind,
}) {
  final missing = expected.toSet()..removeAll(admitted);
  if (missing.isNotEmpty) {
    final sorted = missing.toList()..sort();
    throw NativeArtifactResolutionException(
      'The archive is missing expected $kind: ${sorted.join(', ')}',
    );
  }
}

String _validateArchivePath(String value, {required String kind}) {
  if (value.isEmpty ||
      value.startsWith('/') ||
      value.contains('\\') ||
      RegExp(r'^[A-Za-z]:').hasMatch(value) ||
      value
          .split('/')
          .any(
            (segment) => segment.isEmpty || segment == '.' || segment == '..',
          ) ||
      path.posix.normalize(value) != value) {
    throw NativeArtifactResolutionException(
      'The $kind path is unsafe or non-canonical: "$value".',
    );
  }
  for (final codeUnit in value.codeUnits) {
    if (codeUnit <= 0x1f || (codeUnit >= 0x7f && codeUnit <= 0x9f)) {
      throw NativeArtifactResolutionException(
        'The $kind path contains control characters.',
      );
    }
  }
  return value;
}

void _validateSymlinkTarget(String memberPath, String target) {
  if (target.isEmpty ||
      target.startsWith('/') ||
      target.contains('\\') ||
      RegExp(r'^[A-Za-z]:').hasMatch(target) ||
      path.posix.normalize(target) != target ||
      target
          .split('/')
          .any(
            (segment) => segment.isEmpty || segment == '.' || segment == '..',
          ) ||
      target.codeUnits.any(
        (value) => value <= 0x1f || (value >= 0x7f && value <= 0x9f),
      )) {
    throw NativeArtifactResolutionException(
      'Symbolic link $memberPath has an unsafe target.',
    );
  }
  final resolved = path.posix.normalize(
    path.posix.join(path.posix.dirname(memberPath), target),
  );
  if (resolved == '..' ||
      resolved.startsWith('../') ||
      resolved.startsWith('/')) {
    throw NativeArtifactResolutionException(
      'Symbolic link $memberPath escapes its archive.',
    );
  }
}

bool _bytesEqual(Uint8List left, Uint8List right) {
  if (left.length != right.length) {
    return false;
  }
  for (var index = 0; index < left.length; index++) {
    if (left[index] != right[index]) {
      return false;
    }
  }
  return true;
}

int _u16(Uint8List bytes, int offset) {
  if (offset < 0 || offset + 2 > bytes.length) {
    throw const NativeArtifactResolutionException(
      'A zip integer exceeds the container bounds.',
    );
  }
  return bytes[offset] | (bytes[offset + 1] << 8);
}

int _u32(Uint8List bytes, int offset) {
  if (offset < 0 || offset + 4 > bytes.length) {
    throw const NativeArtifactResolutionException(
      'A zip integer exceeds the container bounds.',
    );
  }
  return (bytes[offset] |
          (bytes[offset + 1] << 8) |
          (bytes[offset + 2] << 16) |
          (bytes[offset + 3] << 24)) &
      0xffffffff;
}

int _u64(Uint8List bytes, int offset) {
  if (offset < 0 || offset + 8 > bytes.length) {
    throw const NativeArtifactResolutionException(
      'A zip integer exceeds the container bounds.',
    );
  }
  var value = 0;
  for (var index = 7; index >= 0; index--) {
    value = (value << 8) | bytes[offset + index];
  }
  return value;
}

int _crc32(Uint8List bytes) {
  var crc = 0xffffffff;
  for (final byte in bytes) {
    crc = _crc32Table[(crc ^ byte) & 0xff] ^ (crc >> 8);
  }
  return (crc ^ 0xffffffff) & 0xffffffff;
}

final List<int> _crc32Table = List<int>.generate(256, (index) {
  var value = index;
  for (var bit = 0; bit < 8; bit++) {
    value = value & 1 != 0 ? 0xedb88320 ^ (value >> 1) : value >> 1;
  }
  return value & 0xffffffff;
}, growable: false);
