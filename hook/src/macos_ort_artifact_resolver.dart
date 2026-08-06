import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:fonix/src/build/native_versions_lock.dart';
import 'package:path/path.dart' as path;

const String fonixMacosOrtArtifactId = 'onnxruntime-1.27.1-macos-arm64-cpu';
const String fonixMacosOrtArchiveName = 'onnxruntime-osx-arm64-1.27.1.tgz';
const String fonixMacosOrtBundleFileName = 'libonnxruntime.1.dylib';
const String fonixMacosOrtManifestFileName =
    'fonix-native-artifact-manifest.json';

const int _maxCompressedArchiveBytes = 128 * 1024 * 1024;
const int _maxUncompressedArchiveBytes = 256 * 1024 * 1024;
const int _maxTarMembers = 4096;
const int _tarBlockSize = 512;

const String _expectedOrtVersion = '1.27.1';
const int _expectedOrtApi = 27;
const String _expectedOperatingSystem = 'macos';
const String _expectedArchitecture = 'arm64';
const String _expectedVariant = 'default';
const String _expectedFlavor = 'cpu';
const String _expectedMinimumOs = '14.0';
const String _knownArchiveSha256 =
    'e42b77a7281cc6e55141bf44fcfbac2c782b823a491bbb6ac33c781dd991f8a6';
const String _knownRuntimeSha256 =
    'a441a94433ebf38f1918009aba0199f88c6f877251b9946d89816427839964c8';

/// Whether an offline archive was selected from the cache or mirror.
enum MacosOrtArchiveOrigin { cache, mirror }

/// Result of validating and staging the pinned macOS arm64 ORT runtime.
final class StagedMacosOrtArtifact {
  const StagedMacosOrtArtifact({
    required this.artifactId,
    required this.archiveFile,
    required this.archiveOrigin,
    required this.runtimeFile,
    required this.manifestFile,
  });

  final String artifactId;
  final File archiveFile;
  final MacosOrtArchiveOrigin archiveOrigin;

  /// A regular file with the exact final Mach-O install-name basename.
  final File runtimeFile;
  final File manifestFile;
}

/// Actionable, content-free failure from offline artifact resolution.
final class MacosOrtArtifactResolutionException implements Exception {
  const MacosOrtArtifactResolutionException(this.message);

  final String message;

  @override
  String toString() => 'MacosOrtArtifactResolutionException: $message';
}

/// Resolves one exact lock tuple from explicit local inputs, then stages it.
///
/// This class performs no network access. It never writes to [packageRoot] or
/// either input root. [stagingDirectory] must be absent and outside the package
/// checkout. A cache hit takes deterministic precedence over a mirror hit, and
/// a corrupt cache entry fails instead of silently falling through.
final class MacosOrtArtifactResolver {
  const MacosOrtArtifactResolver();

  Future<StagedMacosOrtArtifact> stage({
    required Directory packageRoot,
    required Directory stagingDirectory,
    Directory? cacheDirectory,
    Directory? mirrorDirectory,
  }) async {
    if (cacheDirectory == null && mirrorDirectory == null) {
      throw const MacosOrtArtifactResolutionException(
        'Offline macOS ORT resolution requires an explicit cache directory, '
        'mirror directory, or both. Network fallback is disabled.',
      );
    }

    final canonicalPackageRoot = _canonicalPackageRoot(packageRoot);
    _validateFreshStagingDirectory(
      stagingDirectory,
      canonicalPackageRoot: canonicalPackageRoot,
    );

    final lockSource = _readLock(canonicalPackageRoot);
    final lock = _parseLock(lockSource.text);
    final artifact = _selectExactArtifact(lock);
    _validateExactArtifact(lock, artifact);

    final resolvedArchive = await _resolveArchive(
      artifact,
      cacheDirectory: cacheDirectory,
      mirrorDirectory: mirrorDirectory,
    );
    final archive = await _inspectArchive(
      resolvedArchive.file,
      artifact: artifact,
    );

    var createdStagingDirectory = false;
    try {
      stagingDirectory.createSync(recursive: true);
      createdStagingDirectory = true;

      final runtimeFile = _stageRuntime(
        stagingDirectory,
        artifact: artifact,
        archive: archive,
      );
      final manifestFile = _stageManifest(
        stagingDirectory,
        lock: lock,
        lockSha256: lockSource.sha256,
        artifact: artifact,
        archive: archive,
      );
      return StagedMacosOrtArtifact(
        artifactId: artifact.id,
        archiveFile: resolvedArchive.file,
        archiveOrigin: resolvedArchive.origin,
        runtimeFile: runtimeFile,
        manifestFile: manifestFile,
      );
    } on Object {
      if (createdStagingDirectory && stagingDirectory.existsSync()) {
        stagingDirectory.deleteSync(recursive: true);
      }
      rethrow;
    }
  }
}

({String text, String sha256}) _readLock(String canonicalPackageRoot) {
  final lockFile = File(
    path.join(canonicalPackageRoot, 'native', 'versions.lock.yaml'),
  );
  final type = FileSystemEntity.typeSync(lockFile.path, followLinks: false);
  if (type != FileSystemEntityType.file) {
    throw const MacosOrtArtifactResolutionException(
      'native/versions.lock.yaml must be a regular, non-symbolic-link file.',
    );
  }
  final size = lockFile.lengthSync();
  if (size > nativeVersionsLockMaxBytes) {
    throw const MacosOrtArtifactResolutionException(
      'native/versions.lock.yaml exceeds the bounded lockfile size.',
    );
  }
  final bytes = lockFile.readAsBytesSync();
  final String text;
  try {
    text = utf8.decode(bytes, allowMalformed: false);
  } on FormatException catch (error) {
    throw MacosOrtArtifactResolutionException(
      'native/versions.lock.yaml is not valid UTF-8: ${error.message}',
    );
  }
  return (text: text, sha256: sha256.convert(bytes).toString());
}

NativeVersionsLock _parseLock(String source) {
  try {
    return NativeVersionsLock.parse(source);
  } on FormatException catch (error) {
    throw MacosOrtArtifactResolutionException(
      'native/versions.lock.yaml is invalid: ${error.message}',
    );
  }
}

NativeArtifactLock _selectExactArtifact(NativeVersionsLock lock) {
  final matches = lock.artifacts
      .where(
        (artifact) =>
            artifact.target.operatingSystem == _expectedOperatingSystem &&
            artifact.target.architecture == _expectedArchitecture &&
            artifact.target.variant == _expectedVariant &&
            artifact.flavor == _expectedFlavor,
      )
      .toList(growable: false);
  if (matches.isEmpty) {
    throw const MacosOrtArtifactResolutionException(
      'native/versions.lock.yaml has no artifact for the exact tuple '
      'macos/arm64/default/cpu.',
    );
  }
  if (matches.length != 1) {
    throw MacosOrtArtifactResolutionException(
      'native/versions.lock.yaml ambiguously declares ${matches.length} '
      'artifacts for the exact tuple macos/arm64/default/cpu.',
    );
  }
  return matches.single;
}

void _validateExactArtifact(
  NativeVersionsLock lock,
  NativeArtifactLock artifact,
) {
  final header = lock.onnxRuntime.compatibilityHeader;
  final epHeader = lock.onnxRuntime.epHeader;
  if (lock.onnxRuntime.compatibilityApi != _expectedOrtApi ||
      lock.shim.requiredOrtApi != _expectedOrtApi ||
      header.version != _expectedOrtVersion ||
      epHeader.version != _expectedOrtVersion) {
    throw const MacosOrtArtifactResolutionException(
      'The macOS resolver accepts only the pinned ONNX Runtime 1.27.1/API-27 '
      'compatibility tuple.',
    );
  }
  if (artifact.id != fonixMacosOrtArtifactId ||
      artifact.target.minimumOs != _expectedMinimumOs ||
      artifact.runtimeMode != NativeRuntimeMode.bundled ||
      artifact.source.archive != 'tgz') {
    throw const MacosOrtArtifactResolutionException(
      'The selected macOS artifact does not match the exact pinned artifact '
      'identity, minimum OS, bundled mode, and tgz format.',
    );
  }

  final expectedUrl = Uri.https(
    'github.com',
    '/microsoft/onnxruntime/releases/download/v$_expectedOrtVersion/'
        '$fonixMacosOrtArchiveName',
  );
  if (artifact.source.url != expectedUrl) {
    throw MacosOrtArtifactResolutionException(
      'The selected macOS artifact URL must be exactly $expectedUrl.',
    );
  }
  if (artifact.source.sizeBytes > _maxCompressedArchiveBytes) {
    throw const MacosOrtArtifactResolutionException(
      'The selected macOS ORT archive exceeds the compressed size bound.',
    );
  }

  final archiveRoot = 'onnxruntime-osx-arm64-$_expectedOrtVersion';
  final expectedFiles = <String>{
    '$archiveRoot/lib/libonnxruntime.$_expectedOrtVersion.dylib',
    '$archiveRoot/lib/libonnxruntime.dylib',
  };
  final actualFiles = artifact.expectedFiles
      .map((file) => file.relativePath)
      .toSet();
  if (actualFiles.length != expectedFiles.length ||
      !actualFiles.containsAll(expectedFiles)) {
    throw const MacosOrtArtifactResolutionException(
      'The macOS artifact expected-file allowlist does not match the pinned '
      '1.27.1 runtime layout.',
    );
  }

  final symlinks = artifact.expectedSymlinks;
  final expectedSymlinkPath = '$archiveRoot/lib/$fonixMacosOrtBundleFileName';
  final expectedSymlinkTarget = 'libonnxruntime.$_expectedOrtVersion.dylib';
  if (symlinks.length != 1 ||
      symlinks.single.relativePath != expectedSymlinkPath ||
      symlinks.single.target != expectedSymlinkTarget) {
    throw const MacosOrtArtifactResolutionException(
      'The macOS artifact symlink allowlist does not preserve the exact '
      'libonnxruntime.1.dylib install-name alias.',
    );
  }

  final cpuProviders = artifact.providers.where(
    (provider) =>
        provider.wrapperId == 'cpu' &&
        provider.reportedName == 'CPUExecutionProvider',
  );
  if (cpuProviders.length != 1) {
    throw const MacosOrtArtifactResolutionException(
      'The pinned tuple must retain its CPUExecutionProvider declaration. '
      'Additional observed providers are represented separately in the '
      'staged audit metadata.',
    );
  }
}

Future<_ResolvedArchive> _resolveArchive(
  NativeArtifactLock artifact, {
  required Directory? cacheDirectory,
  required Directory? mirrorDirectory,
}) async {
  final candidates = <_ArchiveCandidate>[
    if (cacheDirectory != null)
      _ArchiveCandidate(
        origin: MacosOrtArchiveOrigin.cache,
        file: File(path.join(cacheDirectory.path, fonixMacosOrtArchiveName)),
      ),
    if (mirrorDirectory != null)
      _ArchiveCandidate(
        origin: MacosOrtArchiveOrigin.mirror,
        file: File(path.join(mirrorDirectory.path, fonixMacosOrtArchiveName)),
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
      throw MacosOrtArtifactResolutionException(
        '${candidate.origin.name} archive must be a regular, '
        'non-symbolic-link file: ${candidate.file.path}',
      );
    }
    await _verifyArchiveFile(candidate.file, artifact.source);
    return _ResolvedArchive(file: candidate.file, origin: candidate.origin);
  }

  final searched = candidates
      .map((candidate) => candidate.file.path)
      .join(', ');
  throw MacosOrtArtifactResolutionException(
    'Offline macOS ORT archive miss. Expected $fonixMacosOrtArchiveName at: '
    '$searched. No network request was attempted.',
  );
}

Future<void> _verifyArchiveFile(
  File archive,
  LockedArtifactSource expected,
) async {
  final actualSize = archive.lengthSync();
  if (actualSize != expected.sizeBytes) {
    throw MacosOrtArtifactResolutionException(
      '${archive.path} has size $actualSize; expected '
      '${expected.sizeBytes}. The cache/mirror entry was rejected.',
    );
  }
  final actualDigest = (await sha256.bind(archive.openRead()).first).toString();
  if (actualDigest != expected.sha256) {
    throw MacosOrtArtifactResolutionException(
      '${archive.path} has SHA-256 $actualDigest; expected '
      '${expected.sha256}. The cache/mirror entry was rejected.',
    );
  }
}

Future<_InspectedArchive> _inspectArchive(
  File archiveFile, {
  required NativeArtifactLock artifact,
}) async {
  final builder = BytesBuilder(copy: false);
  var totalBytes = 0;
  try {
    await for (final chunk in gzip.decoder.bind(archiveFile.openRead())) {
      totalBytes += chunk.length;
      if (totalBytes > _maxUncompressedArchiveBytes) {
        throw const MacosOrtArtifactResolutionException(
          'The decompressed macOS ORT archive exceeds the 256 MiB bound.',
        );
      }
      builder.add(chunk);
    }
  } on MacosOrtArtifactResolutionException {
    rethrow;
  } on Object catch (error) {
    throw MacosOrtArtifactResolutionException(
      'The verified macOS ORT archive is not a valid gzip stream: $error',
    );
  }

  return _TarInspector(artifact).inspect(builder.takeBytes());
}

File _stageRuntime(
  Directory stagingDirectory, {
  required NativeArtifactLock artifact,
  required _InspectedArchive archive,
}) {
  final alias = artifact.expectedSymlinks.single;
  final resolvedSourcePath = path.posix.normalize(
    path.posix.join(path.posix.dirname(alias.relativePath), alias.target),
  );
  final bytes = archive.expectedFileBytes[resolvedSourcePath];
  if (bytes == null) {
    throw const MacosOrtArtifactResolutionException(
      'The verified install-name alias target was not admitted from the '
      'archive.',
    );
  }
  final runtimeFile = File(
    path.join(stagingDirectory.path, fonixMacosOrtBundleFileName),
  );
  runtimeFile.writeAsBytesSync(bytes, flush: true);
  return runtimeFile;
}

File _stageManifest(
  Directory stagingDirectory, {
  required NativeVersionsLock lock,
  required String lockSha256,
  required NativeArtifactLock artifact,
  required _InspectedArchive archive,
}) {
  final expectedFiles = artifact.expectedFiles.toList(growable: false)
    ..sort((left, right) => left.relativePath.compareTo(right.relativePath));
  final expectedSymlinks = artifact.expectedSymlinks.toList(growable: false)
    ..sort((left, right) => left.relativePath.compareTo(right.relativePath));
  final providers = artifact.providers.toList(growable: false)
    ..sort((left, right) => left.wrapperId.compareTo(right.wrapperId));
  final licenses = artifact.licenses.toList(growable: false)
    ..sort((left, right) => left.id.compareTo(right.id));
  final alias = expectedSymlinks.single;
  final materializedSource = path.posix.normalize(
    path.posix.join(path.posix.dirname(alias.relativePath), alias.target),
  );
  final materializedFile = expectedFiles.singleWhere(
    (file) => file.relativePath == materializedSource,
  );

  final manifest = <String, Object?>{
    'schema': 1,
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
    },
    'flavor': artifact.flavor,
    'runtimeMode': artifact.runtimeMode.name,
    'source': <String, Object?>{
      'url': artifact.source.url.toString(),
      'sourceRevision': artifact.source.sourceRevision,
      'archive': artifact.source.archive,
      'archiveSha256': artifact.source.sha256,
      'archiveSizeBytes': artifact.source.sizeBytes,
    },
    'verifiedArchiveMembers': <String, Object?>{
      'regularFiles': <Object?>[
        for (final file in expectedFiles)
          <String, Object?>{
            'path': file.relativePath,
            'sha256': file.sha256,
            'sizeBytes': file.sizeBytes,
          },
      ],
      'symbolicLinks': <Object?>[
        for (final symlink in expectedSymlinks)
          <String, Object?>{
            'path': symlink.relativePath,
            'target': symlink.target,
          },
      ],
      'ignoredSafeRegularFileCount': archive.ignoredRegularFileCount,
      'directoryCount': archive.directoryCount,
    },
    'bundleFile': <String, Object?>{
      'basename': fonixMacosOrtBundleFileName,
      'kind': 'regular-file-materialized-from-verified-symlink-target',
      'archiveAlias': alias.relativePath,
      'archiveSource': materializedFile.relativePath,
      'sha256': materializedFile.sha256,
      'sizeBytes': materializedFile.sizeBytes,
      'codeAsset': <String, Object?>{
        'package': 'fonix',
        'name': 'onnxruntime',
        'linkMode': 'dynamic-loading-bundled',
      },
    },
    'providers': <String, Object?>{
      'declaredByLock': <Object?>[
        for (final provider in providers)
          <String, Object?>{
            'wrapperId': provider.wrapperId,
            'reportedName': provider.reportedName,
          },
      ],
      'claimBoundary':
          'Lock declarations are copied metadata, not provider activation or '
          'qualification evidence.',
    },
    'knownBinaryAudit': _knownBinaryAudit(artifact, materializedFile),
    'licenses': <Object?>[
      for (final license in licenses)
        <String, Object?>{
          'id': license.id,
          'noticePath': license.noticePath,
          'sha256': license.sha256,
          'sizeBytes': license.sizeBytes,
        },
    ],
  };

  final manifestFile = File(
    path.join(stagingDirectory.path, fonixMacosOrtManifestFileName),
  );
  manifestFile.writeAsStringSync(
    '${const JsonEncoder.withIndent('  ').convert(manifest)}\n',
    flush: true,
  );
  return manifestFile;
}

Map<String, Object?> _knownBinaryAudit(
  NativeArtifactLock artifact,
  LockedArtifactFile runtimeFile,
) {
  if (artifact.source.sha256 != _knownArchiveSha256 ||
      runtimeFile.sha256 != _knownRuntimeSha256) {
    return <String, Object?>{
      'status': 'not-recorded-for-digest',
      'providerCapabilityClaimsAllowed': false,
    };
  }

  final declaredNames =
      artifact.providers
          .map((provider) => provider.reportedName)
          .whereType<String>()
          .toList(growable: false)
        ..sort();
  const availableProviders = <String>[
    'CoreMLExecutionProvider',
    'WebGpuExecutionProvider',
    'CPUExecutionProvider',
  ];
  final declaredSet = declaredNames.toSet();
  final observedSet = availableProviders.toSet();
  final metadataMatches =
      declaredSet.length == observedSet.length &&
      declaredSet.containsAll(observedSet);

  return <String, Object?>{
    'status': 'recorded-for-exact-digest',
    'objectFormat': 'Mach-O arm64',
    'minimumOs': '14.0',
    'installName': '@rpath/libonnxruntime.1.dylib',
    'rpaths': <Object?>['@loader_path'],
    'exportedSymbols': <Object?>[
      '_OrtGetApiBase',
      '_OrtSessionOptionsAppendExecutionProvider_CPU',
      '_OrtSessionOptionsAppendExecutionProvider_CoreML',
    ],
    'availableProvidersFromApi27Probe': <Object?>[
      for (final provider in availableProviders) provider,
    ],
    'providerQualificationStatus': 'not-qualified',
    'lockProviderMetadataMatchesProbe': metadataMatches,
    'cpuOnlyClaimAllowed': false,
    'mismatchStatus': metadataMatches ? 'none' : 'unresolved',
    'claimBoundary':
        'Availability and exported registration symbols do not establish '
        'successful registration, graph assignment, acceleration, or '
        'qualification.',
  };
}

String _canonicalPackageRoot(Directory packageRoot) {
  final type = FileSystemEntity.typeSync(packageRoot.path, followLinks: true);
  if (type != FileSystemEntityType.directory) {
    throw const MacosOrtArtifactResolutionException(
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
    throw const MacosOrtArtifactResolutionException(
      'The macOS ORT staging directory must not already exist.',
    );
  }
  final prospectivePath = _canonicalProspectivePath(stagingDirectory.path);
  if (path.equals(prospectivePath, canonicalPackageRoot) ||
      path.isWithin(canonicalPackageRoot, prospectivePath)) {
    throw const MacosOrtArtifactResolutionException(
      'The macOS ORT staging directory must be outside the Fonix checkout.',
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
  final existingType = FileSystemEntity.typeSync(cursor, followLinks: true);
  if (existingType != FileSystemEntityType.directory) {
    throw const MacosOrtArtifactResolutionException(
      'The macOS ORT staging directory must have an existing directory '
      'ancestor.',
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

final class _ArchiveCandidate {
  const _ArchiveCandidate({required this.origin, required this.file});

  final MacosOrtArchiveOrigin origin;
  final File file;
}

final class _ResolvedArchive {
  const _ResolvedArchive({required this.file, required this.origin});

  final File file;
  final MacosOrtArchiveOrigin origin;
}

final class _InspectedArchive {
  _InspectedArchive({
    required Map<String, Uint8List> expectedFileBytes,
    required this.ignoredRegularFileCount,
    required this.directoryCount,
  }) : expectedFileBytes = Map.unmodifiable(expectedFileBytes);

  final Map<String, Uint8List> expectedFileBytes;
  final int ignoredRegularFileCount;
  final int directoryCount;
}

final class _TarInspector {
  _TarInspector(this.artifact)
    : expectedFiles = <String, LockedArtifactFile>{
        for (final file in artifact.expectedFiles) file.relativePath: file,
      },
      expectedSymlinks = <String, LockedArtifactSymlink>{
        for (final symlink in artifact.expectedSymlinks)
          symlink.relativePath: symlink,
      },
      archiveRoot = path.posix.dirname(
        path.posix.dirname(artifact.expectedFiles.first.relativePath),
      );

  final NativeArtifactLock artifact;
  final Map<String, LockedArtifactFile> expectedFiles;
  final Map<String, LockedArtifactSymlink> expectedSymlinks;
  final String archiveRoot;

  _InspectedArchive inspect(Uint8List bytes) {
    if (bytes.length % _tarBlockSize != 0) {
      throw const MacosOrtArtifactResolutionException(
        'The decompressed ORT tar stream is not 512-byte aligned.',
      );
    }

    final admittedFiles = <String, Uint8List>{};
    final admittedSymlinks = <String>{};
    final seenPaths = <String>{};
    var offset = 0;
    var memberCount = 0;
    var zeroBlocks = 0;
    var ignoredRegularFileCount = 0;
    var directoryCount = 0;

    while (offset < bytes.length) {
      final headerEnd = offset + _tarBlockSize;
      if (headerEnd > bytes.length) {
        throw const MacosOrtArtifactResolutionException(
          'The ORT tar stream ends inside a header block.',
        );
      }
      if (_isZeroBlock(bytes, offset)) {
        zeroBlocks += 1;
        offset = headerEnd;
        if (zeroBlocks == 2) {
          break;
        }
        continue;
      }
      if (zeroBlocks != 0) {
        throw const MacosOrtArtifactResolutionException(
          'The ORT tar stream has data after an incomplete end marker.',
        );
      }

      memberCount += 1;
      if (memberCount > _maxTarMembers) {
        throw const MacosOrtArtifactResolutionException(
          'The ORT tar stream exceeds the 4096-member bound.',
        );
      }
      _verifyTarChecksum(bytes, offset);
      final memberPath = _memberPath(bytes, offset);
      if (!seenPaths.add(memberPath)) {
        throw MacosOrtArtifactResolutionException(
          'The ORT tar stream duplicates member "$memberPath".',
        );
      }
      if (memberPath != archiveRoot &&
          !path.posix.isWithin(archiveRoot, memberPath)) {
        throw MacosOrtArtifactResolutionException(
          'The ORT tar member escapes the exact archive root: $memberPath',
        );
      }

      final size = _parseTarNumber(bytes, offset + 124, 12, 'member size');
      final dataStart = headerEnd;
      final paddedSize =
          ((size + _tarBlockSize - 1) ~/ _tarBlockSize) * _tarBlockSize;
      final nextOffset = dataStart + paddedSize;
      if (nextOffset > bytes.length) {
        throw MacosOrtArtifactResolutionException(
          'The ORT tar member "$memberPath" exceeds the archive bounds.',
        );
      }

      final type = bytes[offset + 156];
      switch (type) {
        case 0:
        case 48:
          final expected = expectedFiles[memberPath];
          if (expected == null) {
            ignoredRegularFileCount += 1;
          } else {
            if (size != expected.sizeBytes) {
              throw MacosOrtArtifactResolutionException(
                'Archive member $memberPath has size $size; expected '
                '${expected.sizeBytes}.',
              );
            }
            final memberBytes = Uint8List.sublistView(
              bytes,
              dataStart,
              dataStart + size,
            );
            final actualDigest = sha256.convert(memberBytes).toString();
            if (actualDigest != expected.sha256) {
              throw MacosOrtArtifactResolutionException(
                'Archive member $memberPath has SHA-256 $actualDigest; '
                'expected ${expected.sha256}.',
              );
            }
            admittedFiles[memberPath] = memberBytes;
          }
        case 50:
          final expected = expectedSymlinks[memberPath];
          if (expected == null) {
            throw MacosOrtArtifactResolutionException(
              'The ORT tar stream contains an unexpected symbolic link: '
              '$memberPath',
            );
          }
          if (size != 0) {
            throw MacosOrtArtifactResolutionException(
              'Symbolic link $memberPath has a non-zero payload.',
            );
          }
          final target = _tarString(bytes, offset + 157, 100, 'link target');
          if (target != expected.target) {
            throw MacosOrtArtifactResolutionException(
              'Symbolic link $memberPath targets "$target"; expected '
              '"${expected.target}".',
            );
          }
          admittedSymlinks.add(memberPath);
        case 53:
          if (size != 0) {
            throw MacosOrtArtifactResolutionException(
              'Directory member $memberPath has a non-zero payload.',
            );
          }
          if (expectedFiles.containsKey(memberPath) ||
              expectedSymlinks.containsKey(memberPath)) {
            throw MacosOrtArtifactResolutionException(
              'Expected payload member $memberPath is a directory.',
            );
          }
          directoryCount += 1;
        default:
          throw MacosOrtArtifactResolutionException(
            'The ORT tar stream contains unsupported member type '
            '${String.fromCharCode(type)} at $memberPath. Hard links, device '
            'nodes, FIFOs, and extended metadata are rejected.',
          );
      }
      offset = nextOffset;
    }

    if (zeroBlocks != 2) {
      throw const MacosOrtArtifactResolutionException(
        'The ORT tar stream is missing its two-block end marker.',
      );
    }
    for (var index = offset; index < bytes.length; index += 1) {
      if (bytes[index] != 0) {
        throw const MacosOrtArtifactResolutionException(
          'The ORT tar stream contains non-zero trailing data.',
        );
      }
    }

    final missingFiles = expectedFiles.keys.toSet()
      ..removeAll(admittedFiles.keys);
    if (missingFiles.isNotEmpty) {
      final sorted = missingFiles.toList()..sort();
      throw MacosOrtArtifactResolutionException(
        'The ORT tar stream is missing expected regular files: '
        '${sorted.join(', ')}',
      );
    }
    final missingSymlinks = expectedSymlinks.keys.toSet()
      ..removeAll(admittedSymlinks);
    if (missingSymlinks.isNotEmpty) {
      final sorted = missingSymlinks.toList()..sort();
      throw MacosOrtArtifactResolutionException(
        'The ORT tar stream is missing expected symbolic links: '
        '${sorted.join(', ')}',
      );
    }

    return _InspectedArchive(
      expectedFileBytes: admittedFiles,
      ignoredRegularFileCount: ignoredRegularFileCount,
      directoryCount: directoryCount,
    );
  }
}

bool _isZeroBlock(Uint8List bytes, int offset) {
  for (var index = offset; index < offset + _tarBlockSize; index += 1) {
    if (bytes[index] != 0) {
      return false;
    }
  }
  return true;
}

void _verifyTarChecksum(Uint8List bytes, int offset) {
  final expected = _parseTarNumber(bytes, offset + 148, 8, 'header checksum');
  var actual = 0;
  for (var index = 0; index < _tarBlockSize; index += 1) {
    if (index >= 148 && index < 156) {
      actual += 32;
    } else {
      actual += bytes[offset + index];
    }
  }
  if (actual != expected) {
    throw MacosOrtArtifactResolutionException(
      'The ORT tar stream has header checksum $actual; expected $expected.',
    );
  }
}

String _memberPath(Uint8List bytes, int offset) {
  final name = _tarString(bytes, offset, 100, 'member name');
  final magic = _tarString(bytes, offset + 257, 6, 'ustar magic');
  if (magic != 'ustar') {
    throw const MacosOrtArtifactResolutionException(
      'The ORT tar stream must use the bounded POSIX ustar format.',
    );
  }
  final prefix = _tarString(bytes, offset + 345, 155, 'member prefix');
  var combined = prefix.isEmpty ? name : '$prefix/$name';
  while (combined.endsWith('/')) {
    combined = combined.substring(0, combined.length - 1);
  }
  if (combined.isEmpty ||
      combined.startsWith('/') ||
      combined.contains('\\') ||
      RegExp(r'^[A-Za-z]:').hasMatch(combined)) {
    throw MacosOrtArtifactResolutionException(
      'The ORT tar stream contains an unsafe member path: "$combined".',
    );
  }
  final segments = combined.split('/');
  if (segments.any(
    (segment) => segment.isEmpty || segment == '.' || segment == '..',
  )) {
    throw MacosOrtArtifactResolutionException(
      'The ORT tar stream contains path traversal: "$combined".',
    );
  }
  final normalized = path.posix.normalize(combined);
  if (normalized != combined) {
    throw MacosOrtArtifactResolutionException(
      'The ORT tar stream contains a non-canonical member path: "$combined".',
    );
  }
  return combined;
}

int _parseTarNumber(Uint8List bytes, int offset, int length, String fieldName) {
  if (bytes[offset] & 0x80 != 0) {
    throw MacosOrtArtifactResolutionException(
      'The ORT tar $fieldName uses unsupported base-256 encoding.',
    );
  }
  var value = 0;
  var sawDigit = false;
  var ended = false;
  for (var index = offset; index < offset + length; index += 1) {
    final byte = bytes[index];
    if (byte == 0 || byte == 32) {
      if (sawDigit) {
        ended = true;
      }
      continue;
    }
    if (ended || byte < 48 || byte > 55) {
      throw MacosOrtArtifactResolutionException(
        'The ORT tar $fieldName is not a canonical octal value.',
      );
    }
    sawDigit = true;
    value = (value * 8) + byte - 48;
  }
  return value;
}

String _tarString(Uint8List bytes, int offset, int length, String fieldName) {
  var end = offset;
  final limit = offset + length;
  while (end < limit && bytes[end] != 0) {
    end += 1;
  }
  try {
    return utf8.decode(
      Uint8List.sublistView(bytes, offset, end),
      allowMalformed: false,
    );
  } on FormatException catch (error) {
    throw MacosOrtArtifactResolutionException(
      'The ORT tar $fieldName is not valid UTF-8: ${error.message}',
    );
  }
}
