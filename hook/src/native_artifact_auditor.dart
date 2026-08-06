import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as path;

import 'native_artifact_resolver.dart';

const int _maxNativeLibraryBytes = 512 * 1024 * 1024;
const int _maxSourceArchiveBytes = 256 * 1024 * 1024;
const int _maxManifestBytes = 1024 * 1024;
const int _maxElfProgramHeaders = 1024;
const int _maxElfHeaderTableBytes = 1024 * 1024;
const int _maxElfDynamicTableBytes = 1024 * 1024;
const int _maxElfDynamicEntries = 4096;
const int _maxElfStringTableBytes = 1024 * 1024;
const int _maxDynamicStringBytes = 4096;
const int _maxPeSections = 96;
const int _maxPeImportDirectoryBytes = 1024 * 1024;
const int _maxPeImports = 1024;
const int _maxPeImportNameBytes = 260;
const int _androidPageSize = 16 * 1024;

const int _ptLoad = 1;
const int _ptDynamic = 2;
const int _dtNull = 0;
const int _dtNeeded = 1;
const int _dtStrtab = 5;
const int _dtStrsz = 10;
const int _dtSoname = 14;
const int _dtRpath = 15;
const int _dtRunpath = 29;

/// A failure of the closed native-runtime staging or binary contract.
final class NativeArtifactAuditException implements Exception {
  const NativeArtifactAuditException(this.message);

  final String message;

  @override
  String toString() => 'NativeArtifactAuditException: $message';
}

/// Bounded metadata independently read from a staged native binary.
final class AuditedNativeBinary {
  const AuditedNativeBinary({
    required this.relativePath,
    required this.format,
    required this.machine,
    required this.dependencies,
    this.soname,
    this.runpath,
    this.android16KiBCompatible,
  });

  final String relativePath;
  final String format;
  final int machine;
  final Set<String> dependencies;
  final String? soname;
  final String? runpath;
  final bool? android16KiBCompatible;
}

/// Result of auditing one complete resolver-owned staging directory.
final class AuditedNativeArtifact {
  AuditedNativeArtifact({
    required this.artifactId,
    required this.target,
    required Map<String, AuditedNativeBinary> binaries,
  }) : binaries = Map.unmodifiable(binaries);

  final String artifactId;
  final NativeArtifactTarget target;
  final Map<String, AuditedNativeBinary> binaries;
}

/// Audits the final resolver-owned Linux, Windows, or Android runtime staging.
///
/// This check deliberately runs after archive/member verification. It closes
/// the emitted filename/layout contract and independently checks object format,
/// architecture, dynamic dependencies, SONAME/RUNPATH, and Android 16 KiB load
/// segment compatibility before any file is exposed as a bundled code asset.
AuditedNativeArtifact auditStagedNativeArtifact({
  required NativeArtifactTarget target,
  required StagedNativeArtifact staged,
}) {
  try {
    return _auditStagedNativeArtifact(target: target, staged: staged);
  } on NativeArtifactAuditException {
    rethrow;
  } on FileSystemException catch (error) {
    throw NativeArtifactAuditException(
      'Could not audit the staged native artifact: ${error.message}',
    );
  } on FormatException catch (error) {
    throw NativeArtifactAuditException(
      'The staged native artifact manifest is invalid: ${error.message}',
    );
  } on RangeError {
    throw const NativeArtifactAuditException(
      'A native binary metadata range is out of bounds.',
    );
  }
}

AuditedNativeArtifact _auditStagedNativeArtifact({
  required NativeArtifactTarget target,
  required StagedNativeArtifact staged,
}) {
  final policy = _policyFor(target);
  final stagingDirectory = staged.manifestFile.parent;
  if (_entityType(stagingDirectory.path) != FileSystemEntityType.directory) {
    _fail('The native artifact staging root is not a regular directory.');
  }
  if (_entityType(staged.manifestFile.path) != FileSystemEntityType.file ||
      path.basename(staged.manifestFile.path) !=
          fonixNativeArtifactManifestFileName) {
    _fail(
      'The staging root has no regular $fonixNativeArtifactManifestFileName.',
    );
  }

  final payloads = _relativeRegularFiles(
    stagingDirectory,
    staged.payloadFiles,
    kind: 'payload',
  );
  final notices = _relativeRegularFiles(
    stagingDirectory,
    staged.noticeFiles,
    kind: 'notice',
  );
  if (payloads.keys.toSet().difference(policy.payloadNames).isNotEmpty ||
      policy.payloadNames.difference(payloads.keys.toSet()).isNotEmpty) {
    _fail(
      'The ${target.identity} payload basenames/layout do not match the '
      'closed bundled-runtime contract.',
    );
  }
  if (notices.keys.toSet().difference(_noticePaths).isNotEmpty ||
      _noticePaths.difference(notices.keys.toSet()).isNotEmpty) {
    _fail('The staged artifact must contain the exact locked notice paths.');
  }

  final expectedFiles = <String>{
    ...policy.payloadNames,
    ..._noticePaths,
    fonixNativeArtifactManifestFileName,
  };
  final actualFiles = <String>{};
  for (final entity in stagingDirectory.listSync(
    recursive: true,
    followLinks: false,
  )) {
    final type = _entityType(entity.path);
    if (type == FileSystemEntityType.link ||
        (type != FileSystemEntityType.file &&
            type != FileSystemEntityType.directory)) {
      _fail('The staging directory contains a non-regular entity.');
    }
    if (type == FileSystemEntityType.file) {
      actualFiles.add(_relativePath(stagingDirectory, entity.path));
    }
  }
  if (actualFiles.difference(expectedFiles).isNotEmpty ||
      expectedFiles.difference(actualFiles).isNotEmpty) {
    _fail('The staging directory is not the expected closed file set.');
  }

  final manifest = _readManifest(staged.manifestFile);
  _auditManifest(
    manifest,
    target: target,
    staged: staged,
    payloads: payloads,
    notices: notices,
  );

  final binaries = <String, AuditedNativeBinary>{};
  for (final entry in payloads.entries) {
    final bytes = _readNativeLibrary(entry.value);
    final AuditedNativeBinary binary;
    if (target.operatingSystem == 'windows') {
      final metadata = _parsePe(bytes);
      binary = AuditedNativeBinary(
        relativePath: entry.key,
        format: 'pe32+',
        machine: metadata.machine,
        dependencies: Set.unmodifiable(metadata.imports),
      );
    } else {
      final metadata = _parseElf(bytes);
      binary = AuditedNativeBinary(
        relativePath: entry.key,
        format: 'elf64-le',
        machine: metadata.machine,
        dependencies: Set.unmodifiable(metadata.needed),
        soname: metadata.soname,
        runpath: metadata.runpath,
        android16KiBCompatible: target.operatingSystem == 'android'
            ? metadata.compatible16KiB
            : null,
      );
    }
    policy.validateBinary(binary);
    binaries[entry.key] = binary;
  }

  return AuditedNativeArtifact(
    artifactId: staged.artifactId,
    target: target,
    binaries: binaries,
  );
}

const Set<String> _noticePaths = <String>{
  'notices/LICENSE',
  'notices/ThirdPartyNotices.txt',
};

Map<String, File> _relativeRegularFiles(
  Directory root,
  List<File> files, {
  required String kind,
}) {
  final result = <String, File>{};
  for (final file in files) {
    if (_entityType(file.path) != FileSystemEntityType.file) {
      _fail('A staged $kind is missing or is not a regular file.');
    }
    final relative = _relativePath(root, file.path);
    if (result[relative] != null) {
      _fail('The staged artifact duplicates a $kind path.');
    }
    result[relative] = file;
  }
  return result;
}

String _relativePath(Directory root, String child) {
  final absoluteRoot = path.normalize(path.absolute(root.path));
  final absoluteChild = path.normalize(path.absolute(child));
  if (!path.isWithin(absoluteRoot, absoluteChild)) {
    _fail('A staged file escapes the resolver-owned staging directory.');
  }
  final relative = path.relative(absoluteChild, from: absoluteRoot);
  if (relative.isEmpty || relative == '.' || path.isAbsolute(relative)) {
    _fail('A staged file has an invalid relative path.');
  }
  return path.posix.joinAll(path.split(relative));
}

FileSystemEntityType _entityType(String filePath) =>
    FileSystemEntity.typeSync(filePath, followLinks: false);

Uint8List _readNativeLibrary(File file) {
  final size = file.lengthSync();
  if (size <= 0 || size > _maxNativeLibraryBytes) {
    _fail('A staged native library size is outside the accepted bound.');
  }
  return file.readAsBytesSync();
}

Map<String, Object?> _readManifest(File file) {
  final size = file.lengthSync();
  if (size <= 0 || size > _maxManifestBytes) {
    throw const FormatException('manifest size is outside the accepted bound');
  }
  final String source;
  try {
    source = utf8.decode(file.readAsBytesSync(), allowMalformed: false);
  } on FormatException catch (error) {
    throw FormatException('manifest is not valid UTF-8: ${error.message}');
  }
  _JsonDuplicateKeyScanner(source).scan();
  final decoded = jsonDecode(source);
  return _object(decoded, 'manifest');
}

final class _JsonDuplicateKeyScanner {
  _JsonDuplicateKeyScanner(this.source);

  static const int _maxDepth = 64;
  static const int _maxValues = 100000;

  final String source;
  var _offset = 0;
  var _valueCount = 0;

  void scan() {
    _skipWhitespace();
    _value(0);
    _skipWhitespace();
    if (_offset != source.length) {
      _fail('has trailing data');
    }
  }

  void _value(int depth) {
    _valueCount += 1;
    if (_valueCount > _maxValues) {
      _fail('contains too many JSON values');
    }
    if (depth > _maxDepth || _offset >= source.length) {
      _fail('is truncated or nested too deeply');
    }
    switch (source.codeUnitAt(_offset)) {
      case 0x7b:
        _objectValue(depth + 1);
      case 0x5b:
        _arrayValue(depth + 1);
      case 0x22:
        _stringToken();
      case 0x74:
        _literal('true');
      case 0x66:
        _literal('false');
      case 0x6e:
        _literal('null');
      default:
        _number();
    }
  }

  void _objectValue(int depth) {
    _offset += 1;
    _skipWhitespace();
    final keys = <String>{};
    if (_consume(0x7d)) {
      return;
    }
    while (true) {
      if (_offset >= source.length || source.codeUnitAt(_offset) != 0x22) {
        _fail('has an object key that is not a JSON string');
      }
      final keyToken = _stringToken();
      final String key;
      try {
        key = jsonDecode(keyToken) as String;
      } on Object {
        _fail('has an invalid JSON object key');
      }
      if (!keys.add(key)) {
        _fail('contains a duplicate JSON object key');
      }
      _skipWhitespace();
      if (!_consume(0x3a)) {
        _fail('has an object key without a value separator');
      }
      _skipWhitespace();
      _value(depth);
      _skipWhitespace();
      if (_consume(0x7d)) {
        return;
      }
      if (!_consume(0x2c)) {
        _fail('has an unterminated JSON object');
      }
      _skipWhitespace();
    }
  }

  void _arrayValue(int depth) {
    _offset += 1;
    _skipWhitespace();
    if (_consume(0x5d)) {
      return;
    }
    while (true) {
      _value(depth);
      _skipWhitespace();
      if (_consume(0x5d)) {
        return;
      }
      if (!_consume(0x2c)) {
        _fail('has an unterminated JSON array');
      }
      _skipWhitespace();
    }
  }

  String _stringToken() {
    final start = _offset;
    _offset += 1;
    while (_offset < source.length) {
      final value = source.codeUnitAt(_offset++);
      if (value == 0x22) {
        return source.substring(start, _offset);
      }
      if (value <= 0x1f) {
        _fail('contains an unescaped control character');
      }
      if (value == 0x5c) {
        if (_offset >= source.length) {
          _fail('has a truncated JSON escape');
        }
        final escaped = source.codeUnitAt(_offset++);
        if (escaped == 0x75) {
          if (_offset + 4 > source.length) {
            _fail('has a truncated Unicode escape');
          }
          for (var index = 0; index < 4; index++) {
            final digit = source.codeUnitAt(_offset + index);
            final isHex =
                (digit >= 0x30 && digit <= 0x39) ||
                (digit >= 0x41 && digit <= 0x46) ||
                (digit >= 0x61 && digit <= 0x66);
            if (!isHex) {
              _fail('has an invalid Unicode escape');
            }
          }
          _offset += 4;
        } else if (!const {
          0x22,
          0x5c,
          0x2f,
          0x62,
          0x66,
          0x6e,
          0x72,
          0x74,
        }.contains(escaped)) {
          _fail('has an invalid JSON escape');
        }
      }
    }
    _fail('has an unterminated JSON string');
  }

  void _literal(String literal) {
    if (!source.startsWith(literal, _offset)) {
      _fail('has an invalid JSON literal');
    }
    _offset += literal.length;
  }

  void _number() {
    final start = _offset;
    _consume(0x2d);
    if (_consume(0x30)) {
      if (_offset < source.length && _isDigit(source.codeUnitAt(_offset))) {
        _fail('has a non-canonical JSON number');
      }
    } else {
      _digits(required: true);
    }
    if (_consume(0x2e)) {
      _digits(required: true);
    }
    if (_offset < source.length &&
        (source.codeUnitAt(_offset) == 0x65 ||
            source.codeUnitAt(_offset) == 0x45)) {
      _offset += 1;
      if (_offset < source.length &&
          (source.codeUnitAt(_offset) == 0x2b ||
              source.codeUnitAt(_offset) == 0x2d)) {
        _offset += 1;
      }
      _digits(required: true);
    }
    if (_offset == start) {
      _fail('has an invalid JSON value');
    }
  }

  void _digits({required bool required}) {
    final start = _offset;
    while (_offset < source.length && _isDigit(source.codeUnitAt(_offset))) {
      _offset += 1;
    }
    if (required && _offset == start) {
      _fail('has an incomplete JSON number');
    }
  }

  bool _isDigit(int value) => value >= 0x30 && value <= 0x39;

  bool _consume(int value) {
    if (_offset < source.length && source.codeUnitAt(_offset) == value) {
      _offset += 1;
      return true;
    }
    return false;
  }

  void _skipWhitespace() {
    while (_offset < source.length &&
        const {0x20, 0x09, 0x0a, 0x0d}.contains(source.codeUnitAt(_offset))) {
      _offset += 1;
    }
  }

  Never _fail(String message) {
    throw FormatException('staged native artifact manifest $message');
  }
}

void _auditManifest(
  Map<String, Object?> manifest, {
  required NativeArtifactTarget target,
  required StagedNativeArtifact staged,
  required Map<String, File> payloads,
  required Map<String, File> notices,
}) {
  _exactKeys(manifest, const <String>{
    'schema',
    'artifactId',
    'lock',
    'target',
    'source',
    'containers',
    'payloadFiles',
    'verifiedSymlinks',
    'notices',
    'archiveInspections',
    'claimBoundary',
  }, 'manifest');
  if (_integer(manifest['schema'], 'manifest.schema') != 2) {
    throw const FormatException('manifest.schema must be 2');
  }
  final artifactId = _string(manifest['artifactId'], 'manifest.artifactId');
  if (artifactId != staged.artifactId) {
    throw const FormatException('manifest artifact ID does not match staging');
  }
  final identity = staged.identity;
  if (staged.artifactId != identity.artifactId) {
    throw const FormatException('staged artifact identity is inconsistent');
  }

  final lock = _object(manifest['lock'], 'manifest.lock');
  _exactKeys(lock, const <String>{
    'path',
    'sha256',
    'snapshotDate',
    'releaseState',
  }, 'manifest.lock');
  if (_string(lock['path'], 'manifest.lock.path') !=
      'native/versions.lock.yaml') {
    throw const FormatException('manifest lock path is not canonical');
  }
  final lockSha256 = _sha256(lock['sha256'], 'manifest.lock.sha256');
  if (lockSha256 != identity.lockSha256) {
    throw const FormatException(
      'manifest lock digest does not match staged identity',
    );
  }
  _nonEmptyString(lock['snapshotDate'], 'manifest.lock.snapshotDate');
  _nonEmptyString(lock['releaseState'], 'manifest.lock.releaseState');

  final targetObject = _object(manifest['target'], 'manifest.target');
  _exactKeys(targetObject, const <String>{
    'os',
    'architecture',
    'variant',
    'minimumOs',
    'flavor',
    'runtimeMode',
  }, 'manifest.target');
  final targetValues = <String, String>{
    'os': identity.operatingSystem,
    'architecture': identity.architecture,
    'variant': identity.variant,
    'flavor': identity.flavor,
    'runtimeMode': identity.runtimeMode,
    'minimumOs': identity.minimumOs,
  };
  for (final entry in targetValues.entries) {
    if (_string(targetObject[entry.key], 'manifest.target.${entry.key}') !=
        entry.value) {
      throw FormatException(
        'manifest target ${entry.key} does not match ${target.identity}',
      );
    }
  }
  if (identity.operatingSystem != target.operatingSystem ||
      identity.architecture != target.architecture ||
      identity.variant != target.variant ||
      identity.flavor != target.flavor ||
      identity.runtimeMode != 'bundled' ||
      identity.minimumOs !=
          switch (target.operatingSystem) {
            'android' => '24',
            'linux' => 'glibc-2.27',
            'windows' => '10.0',
            _ => throw const NativeArtifactAuditException(
              'No portable manifest policy exists for this target.',
            ),
          }) {
    throw const FormatException(
      'staged identity does not match the selected portable target policy',
    );
  }

  final source = _object(manifest['source'], 'manifest.source');
  _exactKeys(source, const <String>{
    'url',
    'sourceRevision',
    'archive',
    'sha256',
    'sizeBytes',
  }, 'manifest.source');
  final sourceUrl = Uri.tryParse(
    _nonEmptyString(source['url'], 'manifest.source.url'),
  );
  if (sourceUrl == null || !sourceUrl.hasScheme) {
    throw const FormatException('manifest source URL is invalid');
  }
  final revision = source['sourceRevision'];
  if (revision != null) {
    _nonEmptyString(revision, 'manifest.source.sourceRevision');
  }
  final archive = _string(source['archive'], 'manifest.source.archive');
  if (archive != 'zip' && archive != 'tgz') {
    throw const FormatException('manifest source archive is unsupported');
  }
  final sourceSha256 = _sha256(source['sha256'], 'manifest.source.sha256');
  final sourceSize = _integer(source['sizeBytes'], 'manifest.source.sizeBytes');
  if (sourceSize <= 0 || sourceSize > _maxSourceArchiveBytes) {
    throw const FormatException('manifest source size must be positive');
  }
  if (_entityType(staged.archiveFile.path) != FileSystemEntityType.file ||
      staged.archiveFile.lengthSync() != sourceSize) {
    throw const FormatException(
      'manifest source size does not match the resolved archive',
    );
  }
  final archiveSha256 = sha256
      .convert(staged.archiveFile.readAsBytesSync())
      .toString();
  if (sourceSha256 != identity.sourceSha256 || archiveSha256 != sourceSha256) {
    throw const FormatException(
      'manifest source digest does not match staged identity and archive',
    );
  }

  _list(manifest['containers'], 'manifest.containers');
  _list(manifest['verifiedSymlinks'], 'manifest.verifiedSymlinks');
  _list(manifest['archiveInspections'], 'manifest.archiveInspections');
  _nonEmptyString(manifest['claimBoundary'], 'manifest.claimBoundary');

  _auditManifestFiles(
    _list(manifest['payloadFiles'], 'manifest.payloadFiles'),
    actual: payloads,
    expectedPaths: payloads.keys.toSet(),
    kind: 'payload',
  );
  _auditManifestFiles(
    _list(manifest['notices'], 'manifest.notices'),
    actual: notices,
    expectedPaths: _noticePaths,
    kind: 'notice',
  );
  final thirdPartyNotices = notices['notices/ThirdPartyNotices.txt']!;
  if (sha256.convert(thirdPartyNotices.readAsBytesSync()).toString() !=
      identity.thirdPartyNoticesSha256) {
    throw const FormatException(
      'ThirdPartyNotices digest does not match staged identity',
    );
  }
}

void _auditManifestFiles(
  List<Object?> entries, {
  required Map<String, File> actual,
  required Set<String> expectedPaths,
  required String kind,
}) {
  final seen = <String>{};
  for (final value in entries) {
    final entry = _object(value, 'manifest $kind entry');
    final expectedKeys = kind == 'notice'
        ? const <String>{
            'id',
            'containerDepth',
            'archivePath',
            'stagedPath',
            'sha256',
            'sizeBytes',
          }
        : const <String>{'archivePath', 'stagedPath', 'sha256', 'sizeBytes'};
    _exactKeys(entry, expectedKeys, 'manifest $kind entry');
    _safeArchivePath(
      _string(entry['archivePath'], 'manifest $kind archivePath'),
    );
    final stagedPath = _safeArchivePath(
      _string(entry['stagedPath'], 'manifest $kind stagedPath'),
    );
    if (!seen.add(stagedPath)) {
      throw FormatException('manifest duplicates a $kind staged path');
    }
    if (kind == 'notice') {
      final expectedId = switch (stagedPath) {
        'notices/LICENSE' => 'MIT',
        'notices/ThirdPartyNotices.txt' => 'ThirdPartyNotices',
        _ => throw const FormatException(
          'manifest names an unexpected notice staged path',
        ),
      };
      if (_string(entry['id'], 'manifest notice id') != expectedId ||
          _integer(entry['containerDepth'], 'manifest notice containerDepth') !=
              0) {
        throw const FormatException(
          'manifest notice ID or container depth is not the closed value',
        );
      }
    }
    final file = actual[stagedPath];
    if (file == null) {
      throw FormatException('manifest names an unexpected $kind staged path');
    }
    final expectedSize = _integer(
      entry['sizeBytes'],
      'manifest $kind sizeBytes',
    );
    if (expectedSize <= 0 || file.lengthSync() != expectedSize) {
      throw FormatException('manifest $kind size does not match staged bytes');
    }
    final expectedHash = _sha256(entry['sha256'], 'manifest $kind sha256');
    final actualHash = sha256.convert(file.readAsBytesSync()).toString();
    if (actualHash != expectedHash) {
      throw FormatException('manifest $kind hash does not match staged bytes');
    }
  }
  if (seen.difference(expectedPaths).isNotEmpty ||
      expectedPaths.difference(seen).isNotEmpty) {
    throw FormatException('manifest does not list the exact staged $kind set');
  }
}

Map<String, Object?> _object(Object? value, String field) {
  if (value is! Map<String, Object?>) {
    throw FormatException('$field must be an object');
  }
  return value;
}

List<Object?> _list(Object? value, String field) {
  if (value is! List<Object?>) {
    throw FormatException('$field must be an array');
  }
  return value;
}

String _string(Object? value, String field) {
  if (value is! String) {
    throw FormatException('$field must be a string');
  }
  return value;
}

String _nonEmptyString(Object? value, String field) {
  final result = _string(value, field);
  if (result.isEmpty || result.length > 4096) {
    throw FormatException('$field is empty or exceeds the accepted bound');
  }
  return result;
}

int _integer(Object? value, String field) {
  if (value is! int) {
    throw FormatException('$field must be an integer');
  }
  return value;
}

String _sha256(Object? value, String field) {
  final result = _string(value, field);
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(result)) {
    throw FormatException('$field must be a lowercase SHA-256 digest');
  }
  return result;
}

void _exactKeys(
  Map<String, Object?> value,
  Set<String> expected,
  String field,
) {
  final actual = value.keys.toSet();
  if (actual.difference(expected).isNotEmpty ||
      expected.difference(actual).isNotEmpty) {
    throw FormatException('$field has an unexpected field set');
  }
}

String _safeArchivePath(String value) {
  if (value.isEmpty ||
      value.startsWith('/') ||
      value.contains(r'\') ||
      RegExp(r'^[A-Za-z]:').hasMatch(value) ||
      value
          .split('/')
          .any(
            (segment) => segment.isEmpty || segment == '.' || segment == '..',
          ) ||
      path.posix.normalize(value) != value ||
      value.codeUnits.any(
        (unit) => unit <= 0x1f || (unit >= 0x7f && unit <= 0x9f),
      )) {
    throw const FormatException('manifest contains an unsafe archive path');
  }
  return value;
}

final class _ArtifactPolicy {
  const _ArtifactPolicy({
    required this.target,
    required this.payloadNames,
    required this.binaryPolicies,
  });

  final NativeArtifactTarget target;
  final Set<String> payloadNames;
  final Map<String, _BinaryPolicy> binaryPolicies;

  void validateBinary(AuditedNativeBinary binary) {
    final policy = binaryPolicies[binary.relativePath];
    if (policy == null) {
      _fail('No native binary policy exists for ${binary.relativePath}.');
    }
    policy.validate(binary, target: target);
  }
}

final class _BinaryPolicy {
  const _BinaryPolicy({
    required this.format,
    required this.machine,
    required this.dependencies,
    this.soname,
    this.runpath,
    this.requireAndroid16KiB = false,
  });

  final String format;
  final int machine;
  final Set<String> dependencies;
  final String? soname;
  final String? runpath;
  final bool requireAndroid16KiB;

  void validate(
    AuditedNativeBinary binary, {
    required NativeArtifactTarget target,
  }) {
    if (binary.format != format || binary.machine != machine) {
      _fail(
        '${binary.relativePath} does not match the object format and machine '
        'for ${target.identity}.',
      );
    }
    final actualDependencies = format == 'pe32+'
        ? binary.dependencies.map((name) => name.toLowerCase()).toSet()
        : binary.dependencies;
    final expectedDependencies = format == 'pe32+'
        ? dependencies.map((name) => name.toLowerCase()).toSet()
        : dependencies;
    if (actualDependencies.difference(expectedDependencies).isNotEmpty ||
        expectedDependencies.difference(actualDependencies).isNotEmpty) {
      _fail('${binary.relativePath} has an unexpected native dependency set.');
    }
    if (binary.soname != soname || binary.runpath != runpath) {
      _fail('${binary.relativePath} has an unexpected SONAME or RUNPATH.');
    }
    if (requireAndroid16KiB && binary.android16KiBCompatible != true) {
      _fail('${binary.relativePath} is not Android 16 KiB PT_LOAD compatible.');
    }
  }
}

_ArtifactPolicy _policyFor(NativeArtifactTarget target) {
  if (target.variant != 'default' || target.flavor != 'cpu') {
    _fail('No portable native artifact policy exists for ${target.identity}.');
  }
  if (target.operatingSystem == 'android') {
    final machine = switch (target.architecture) {
      'arm64-v8a' => 183,
      'x86_64' => 62,
      _ => _unsupportedTarget(target),
    };
    return _ArtifactPolicy(
      target: target,
      payloadNames: const <String>{'libonnxruntime.so'},
      binaryPolicies: <String, _BinaryPolicy>{
        'libonnxruntime.so': _BinaryPolicy(
          format: 'elf64-le',
          machine: machine,
          dependencies: const <String>{
            'libdl.so',
            'liblog.so',
            'libandroid.so',
            'libm.so',
            'libc.so',
          },
          soname: 'libonnxruntime.so',
          runpath: r'$ORIGIN',
          requireAndroid16KiB: true,
        ),
      },
    );
  }
  if (target.operatingSystem == 'linux') {
    final (machine, loader) = switch (target.architecture) {
      'x86_64' => (62, 'ld-linux-x86-64.so.2'),
      'arm64' => (183, 'ld-linux-aarch64.so.1'),
      _ => _unsupportedTarget(target),
    };
    return _ArtifactPolicy(
      target: target,
      payloadNames: const <String>{
        'libonnxruntime.so.1',
        'libonnxruntime_providers_shared.so',
      },
      binaryPolicies: <String, _BinaryPolicy>{
        'libonnxruntime.so.1': _BinaryPolicy(
          format: 'elf64-le',
          machine: machine,
          dependencies: <String>{
            'libdl.so.2',
            'librt.so.1',
            'libpthread.so.0',
            'libstdc++.so.6',
            'libm.so.6',
            'libgcc_s.so.1',
            'libc.so.6',
            loader,
          },
          soname: 'libonnxruntime.so.1',
          runpath: r'$ORIGIN',
        ),
        'libonnxruntime_providers_shared.so': _BinaryPolicy(
          format: 'elf64-le',
          machine: machine,
          dependencies: const <String>{
            'libstdc++.so.6',
            'libm.so.6',
            'libgcc_s.so.1',
            'libc.so.6',
          },
          soname: 'libonnxruntime_providers_shared.so',
        ),
      },
    );
  }
  if (target.operatingSystem == 'windows' && target.architecture == 'x64') {
    return _ArtifactPolicy(
      target: target,
      payloadNames: const <String>{
        'onnxruntime.dll',
        'onnxruntime_providers_shared.dll',
      },
      binaryPolicies: const <String, _BinaryPolicy>{
        'onnxruntime.dll': _BinaryPolicy(
          format: 'pe32+',
          machine: 0x8664,
          dependencies: <String>{
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
          },
        ),
        'onnxruntime_providers_shared.dll': _BinaryPolicy(
          format: 'pe32+',
          machine: 0x8664,
          dependencies: <String>{
            'VCRUNTIME140.dll',
            'api-ms-win-crt-runtime-l1-1-0.dll',
            'KERNEL32.dll',
          },
        ),
      },
    );
  }
  return _unsupportedTarget(target);
}

Never _unsupportedTarget(NativeArtifactTarget target) =>
    _fail('No portable native artifact policy exists for ${target.identity}.');

final class _ElfMetadata {
  const _ElfMetadata({
    required this.machine,
    required this.soname,
    required this.needed,
    required this.runpath,
    required this.compatible16KiB,
  });

  final int machine;
  final String? soname;
  final Set<String> needed;
  final String? runpath;
  final bool compatible16KiB;
}

final class _ElfSegment {
  const _ElfSegment({
    required this.index,
    required this.type,
    required this.offset,
    required this.virtualAddress,
    required this.fileSize,
    required this.memorySize,
    required this.alignment,
  });

  final int index;
  final int type;
  final int offset;
  final int virtualAddress;
  final int fileSize;
  final int memorySize;
  final int alignment;
}

_ElfMetadata _parseElf(Uint8List bytes) {
  if (bytes.length < 64 ||
      bytes[0] != 0x7f ||
      bytes[1] != 0x45 ||
      bytes[2] != 0x4c ||
      bytes[3] != 0x46) {
    _fail('A staged Linux/Android payload is not an ELF file.');
  }
  if (bytes[4] != 2 || bytes[5] != 1 || bytes[6] != 1) {
    _fail('A staged payload is not a little-endian ELF64 version-1 image.');
  }
  final data = ByteData.sublistView(bytes);
  if (_u16(data, 16) != 3 || _u32(data, 20) != 1) {
    _fail('A staged ELF payload is not an ET_DYN version-1 image.');
  }
  final machine = _u16(data, 18);
  final programHeaderOffset = _u64(data, 32);
  final headerSize = _u16(data, 52);
  final programHeaderSize = _u16(data, 54);
  final programHeaderCount = _u16(data, 56);
  if (headerSize != 64 || programHeaderSize != 56) {
    _fail('A staged ELF payload has unsupported header sizes.');
  }
  if (programHeaderCount < 1 || programHeaderCount > _maxElfProgramHeaders) {
    _fail('A staged ELF program-header count is outside the accepted bound.');
  }
  final tableSize = programHeaderSize * programHeaderCount;
  _boundedRange(
    bytes.length,
    programHeaderOffset,
    tableSize,
    maximumSize: _maxElfHeaderTableBytes,
    field: 'ELF program-header table',
  );

  final segments = <_ElfSegment>[];
  for (var index = 0; index < programHeaderCount; index++) {
    final offset = programHeaderOffset + index * programHeaderSize;
    final fileOffset = _u64(data, offset + 8);
    final fileSize = _u64(data, offset + 32);
    final memorySize = _u64(data, offset + 40);
    _boundedRange(
      bytes.length,
      fileOffset,
      fileSize,
      field: 'ELF program segment',
    );
    if (fileSize > memorySize) {
      _fail('An ELF segment has a file size larger than its memory size.');
    }
    segments.add(
      _ElfSegment(
        index: index,
        type: _u32(data, offset),
        offset: fileOffset,
        virtualAddress: _u64(data, offset + 16),
        fileSize: fileSize,
        memorySize: memorySize,
        alignment: _u64(data, offset + 48),
      ),
    );
  }
  final loads = segments
      .where((segment) => segment.type == _ptLoad)
      .toList(growable: false);
  if (loads.isEmpty) {
    _fail('A staged ELF payload has no PT_LOAD segment.');
  }
  for (final segment in loads) {
    final alignment = segment.alignment;
    if (alignment != 0 && alignment != 1 && !_isPowerOfTwo(alignment)) {
      _fail('An ELF PT_LOAD segment has non-power-of-two alignment.');
    }
    if (alignment > 1 &&
        (segment.offset - segment.virtualAddress) % alignment != 0) {
      _fail('An ELF PT_LOAD segment has incongruent offset and address.');
    }
  }
  final compatible16KiB = loads.every(
    (segment) =>
        segment.alignment >= _androidPageSize &&
        (segment.offset - segment.virtualAddress) % _androidPageSize == 0,
  );

  final dynamic = segments
      .where((segment) => segment.type == _ptDynamic)
      .toList(growable: false);
  if (dynamic.length != 1) {
    _fail('A staged ELF payload must have exactly one PT_DYNAMIC segment.');
  }
  final dynamicSegment = dynamic.single;
  if (dynamicSegment.fileSize <= 0 ||
      dynamicSegment.fileSize > _maxElfDynamicTableBytes ||
      dynamicSegment.fileSize % 16 != 0 ||
      dynamicSegment.fileSize ~/ 16 > _maxElfDynamicEntries) {
    _fail('An ELF PT_DYNAMIC table size is outside the accepted bound.');
  }
  final mappedDynamic = _mapElfAddress(
    dynamicSegment.virtualAddress,
    dynamicSegment.fileSize,
    loads,
  );
  if (mappedDynamic != dynamicSegment.offset) {
    _fail('The ELF PT_DYNAMIC file offset does not match its address.');
  }

  int? stringTableAddress;
  int? stringTableSize;
  int? sonameOffset;
  int? runpathOffset;
  int? rpathOffset;
  final neededOffsets = <int>[];
  var terminated = false;
  for (var offset = 0; offset < dynamicSegment.fileSize; offset += 16) {
    final entry = dynamicSegment.offset + offset;
    final tag = _u64(data, entry);
    final value = _u64(data, entry + 8);
    if (tag == _dtNull) {
      terminated = true;
      break;
    }
    switch (tag) {
      case _dtStrtab:
        if (stringTableAddress != null) {
          _fail('The ELF dynamic table duplicates DT_STRTAB.');
        }
        stringTableAddress = value;
      case _dtStrsz:
        if (stringTableSize != null) {
          _fail('The ELF dynamic table duplicates DT_STRSZ.');
        }
        stringTableSize = value;
      case _dtSoname:
        if (sonameOffset != null) {
          _fail('The ELF dynamic table duplicates DT_SONAME.');
        }
        sonameOffset = value;
      case _dtRunpath:
        if (runpathOffset != null) {
          _fail('The ELF dynamic table duplicates DT_RUNPATH.');
        }
        runpathOffset = value;
      case _dtRpath:
        if (rpathOffset != null) {
          _fail('The ELF dynamic table duplicates DT_RPATH.');
        }
        rpathOffset = value;
      case _dtNeeded:
        neededOffsets.add(value);
    }
  }
  if (!terminated || stringTableAddress == null || stringTableSize == null) {
    _fail('The ELF dynamic table is incomplete or unterminated.');
  }
  if (stringTableSize <= 0 || stringTableSize > _maxElfStringTableBytes) {
    _fail('The ELF dynamic string table size is outside the accepted bound.');
  }
  final stringTableOffset = _mapElfAddress(
    stringTableAddress,
    stringTableSize,
    loads,
  );
  final stringTable = Uint8List.sublistView(
    bytes,
    stringTableOffset,
    stringTableOffset + stringTableSize,
  );
  final soname = sonameOffset == null
      ? null
      : _dynamicString(stringTable, sonameOffset, 'DT_SONAME');
  final runpath = runpathOffset == null
      ? null
      : _dynamicString(stringTable, runpathOffset, 'DT_RUNPATH');
  if (rpathOffset != null) {
    _dynamicString(stringTable, rpathOffset, 'DT_RPATH');
    _fail('The staged ELF payload uses DT_RPATH instead of DT_RUNPATH.');
  }
  final needed = <String>{};
  for (final offset in neededOffsets) {
    if (!needed.add(_dynamicString(stringTable, offset, 'DT_NEEDED'))) {
      _fail('The ELF dynamic table duplicates a DT_NEEDED entry.');
    }
  }
  return _ElfMetadata(
    machine: machine,
    soname: soname,
    needed: needed,
    runpath: runpath,
    compatible16KiB: compatible16KiB,
  );
}

int _mapElfAddress(int address, int size, List<_ElfSegment> loads) {
  final candidates = <int>[];
  for (final segment in loads) {
    if (address < segment.virtualAddress) {
      continue;
    }
    final delta = address - segment.virtualAddress;
    if (delta <= segment.fileSize && size <= segment.fileSize - delta) {
      candidates.add(segment.offset + delta);
    }
  }
  if (candidates.length != 1) {
    _fail('An ELF virtual address does not map to one PT_LOAD segment.');
  }
  return candidates.single;
}

String _dynamicString(Uint8List table, int offset, String field) {
  if (offset < 0 || offset >= table.length) {
    _fail('An ELF $field string offset is out of bounds.');
  }
  var end = offset;
  while (end < table.length && table[end] != 0) {
    end += 1;
    if (end - offset > _maxDynamicStringBytes) {
      _fail('An ELF $field string exceeds the accepted bound.');
    }
  }
  if (end >= table.length || end == offset) {
    _fail('An ELF $field string is empty or unterminated.');
  }
  final String value;
  try {
    value = utf8.decode(
      Uint8List.sublistView(table, offset, end),
      allowMalformed: false,
    );
  } on FormatException {
    _fail('An ELF $field string is not valid UTF-8.');
  }
  if (value.codeUnits.any((unit) => unit <= 0x1f || unit == 0x7f)) {
    _fail('An ELF $field string contains control characters.');
  }
  return value;
}

final class _PeMetadata {
  const _PeMetadata({required this.machine, required this.imports});

  final int machine;
  final Set<String> imports;
}

final class _PeSection {
  const _PeSection({
    required this.virtualAddress,
    required this.virtualSize,
    required this.rawOffset,
    required this.rawSize,
  });

  final int virtualAddress;
  final int virtualSize;
  final int rawOffset;
  final int rawSize;
}

final class _PeMapping {
  const _PeMapping(this.offset, this.available);

  final int offset;
  final int available;
}

_PeMetadata _parsePe(Uint8List bytes) {
  if (bytes.length < 0x40 || bytes[0] != 0x4d || bytes[1] != 0x5a) {
    _fail('A staged Windows payload is not a PE file.');
  }
  final data = ByteData.sublistView(bytes);
  final peOffset = _u32(data, 0x3c);
  _boundedRange(bytes.length, peOffset, 24, field: 'PE signature/header');
  if (_u32(data, peOffset) != 0x00004550) {
    _fail('A staged Windows payload has no PE signature.');
  }
  final machine = _u16(data, peOffset + 4);
  final sectionCount = _u16(data, peOffset + 6);
  final optionalSize = _u16(data, peOffset + 20);
  final characteristics = _u16(data, peOffset + 22);
  if (sectionCount < 1 || sectionCount > _maxPeSections) {
    _fail('A PE section count is outside the accepted bound.');
  }
  if ((characteristics & 0x2000) == 0) {
    _fail('A staged Windows payload is not marked as a DLL.');
  }
  final optionalOffset = peOffset + 24;
  _boundedRange(
    bytes.length,
    optionalOffset,
    optionalSize,
    field: 'PE optional header',
  );
  if (optionalSize < 128 || _u16(data, optionalOffset) != 0x20b) {
    _fail('A staged Windows payload is not a PE32+ image.');
  }
  final sizeOfHeaders = _u32(data, optionalOffset + 60);
  if (sizeOfHeaders <= 0 || sizeOfHeaders > bytes.length) {
    _fail('A PE SizeOfHeaders value is out of bounds.');
  }
  final directoryCount = _u32(data, optionalOffset + 108);
  if (directoryCount < 2 || optionalSize < 128) {
    _fail('A PE image has no import data directory.');
  }
  final importRva = _u32(data, optionalOffset + 120);
  final importSize = _u32(data, optionalOffset + 124);
  if (importRva == 0 ||
      importSize < 20 ||
      importSize > _maxPeImportDirectoryBytes) {
    _fail('A PE import directory is outside the accepted bound.');
  }

  final sectionTable = optionalOffset + optionalSize;
  _boundedRange(
    bytes.length,
    sectionTable,
    sectionCount * 40,
    field: 'PE section table',
  );
  final sections = <_PeSection>[];
  for (var index = 0; index < sectionCount; index++) {
    final offset = sectionTable + index * 40;
    final rawSize = _u32(data, offset + 16);
    final rawOffset = _u32(data, offset + 20);
    if (rawSize > 0) {
      _boundedRange(
        bytes.length,
        rawOffset,
        rawSize,
        field: 'PE section bytes',
      );
    }
    sections.add(
      _PeSection(
        virtualAddress: _u32(data, offset + 12),
        virtualSize: _u32(data, offset + 8),
        rawOffset: rawOffset,
        rawSize: rawSize,
      ),
    );
  }
  final importMapping = _mapPeRva(
    importRva,
    importSize,
    sizeOfHeaders: sizeOfHeaders,
    sections: sections,
  );
  final imports = <String>{};
  var terminated = false;
  for (var index = 0; index < _maxPeImports; index++) {
    final descriptorOffset = importMapping.offset + index * 20;
    if (descriptorOffset + 20 >
        importMapping.offset + importMapping.available) {
      break;
    }
    final fields = <int>[
      for (var field = 0; field < 5; field++)
        _u32(data, descriptorOffset + field * 4),
    ];
    if (fields.every((value) => value == 0)) {
      terminated = true;
      break;
    }
    final nameRva = fields[3];
    if (nameRva == 0) {
      _fail('A PE import descriptor has no DLL name RVA.');
    }
    final nameMapping = _mapPeRva(
      nameRva,
      1,
      sizeOfHeaders: sizeOfHeaders,
      sections: sections,
    );
    final name = _peString(bytes, nameMapping);
    if (name.contains('/') || name.contains(r'\') || !imports.add(name)) {
      _fail('A PE import DLL name is unsafe or duplicated.');
    }
  }
  if (!terminated || imports.isEmpty) {
    _fail('The PE import directory is empty or unterminated.');
  }
  return _PeMetadata(machine: machine, imports: imports);
}

_PeMapping _mapPeRva(
  int rva,
  int size, {
  required int sizeOfHeaders,
  required List<_PeSection> sections,
}) {
  final candidates = <_PeMapping>[];
  if (rva < sizeOfHeaders && size <= sizeOfHeaders - rva) {
    candidates.add(_PeMapping(rva, sizeOfHeaders - rva));
  }
  for (final section in sections) {
    if (rva < section.virtualAddress) {
      continue;
    }
    final delta = rva - section.virtualAddress;
    if (delta <= section.rawSize && size <= section.rawSize - delta) {
      candidates.add(
        _PeMapping(section.rawOffset + delta, section.rawSize - delta),
      );
    }
  }
  if (candidates.length != 1) {
    _fail('A PE RVA does not map to one file-backed region.');
  }
  return candidates.single;
}

String _peString(Uint8List bytes, _PeMapping mapping) {
  final maximum = mapping.available < _maxPeImportNameBytes + 1
      ? mapping.available
      : _maxPeImportNameBytes + 1;
  var end = mapping.offset;
  while (end < mapping.offset + maximum && bytes[end] != 0) {
    end += 1;
  }
  if (end == mapping.offset || end >= mapping.offset + maximum) {
    _fail('A PE import DLL name is empty, too long, or unterminated.');
  }
  final nameBytes = Uint8List.sublistView(bytes, mapping.offset, end);
  if (nameBytes.any((value) => value < 0x20 || value > 0x7e)) {
    _fail('A PE import DLL name is not printable ASCII.');
  }
  return ascii.decode(nameBytes);
}

bool _isPowerOfTwo(int value) => value > 0 && (value & (value - 1)) == 0;

void _boundedRange(
  int total,
  int offset,
  int size, {
  int? maximumSize,
  required String field,
}) {
  if (offset < 0 ||
      size < 0 ||
      offset > total ||
      size > total - offset ||
      (maximumSize != null && size > maximumSize)) {
    _fail('$field is out of bounds.');
  }
}

int _u16(ByteData data, int offset) => data.getUint16(offset, Endian.little);

int _u32(ByteData data, int offset) => data.getUint32(offset, Endian.little);

int _u64(ByteData data, int offset) => data.getUint64(offset, Endian.little);

Never _fail(String message) => throw NativeArtifactAuditException(message);
