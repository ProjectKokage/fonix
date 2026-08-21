import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as path;

import '../strict_json.dart';

/// Maximum UTF-8 size accepted for the native version lock.
const int nativeVersionsLockMaxBytes = 1024 * 1024;

const int _maxSourceInputBytes = 16 * 1024 * 1024;
const int _maxArtifacts = 256;
const int _maxExpectedFiles = 2048;
const int _maxNestedContainers = 8;
const int _maxArtifactNotices = 32;

enum NativeVersionsLockValidationMode { development, release }

enum NativeRuntimeMode { linked, bundled, process, aligned, file }

final class NativeVersionsLock {
  NativeVersionsLock._({
    required this.schema,
    required this.snapshotDate,
    required this.releaseState,
    required this.shim,
    required this.onnxRuntime,
    required List<NativeReleaseTarget> releaseTargets,
    required List<NativeArtifactLock> artifacts,
  }) : releaseTargets = List.unmodifiable(releaseTargets),
       artifacts = List.unmodifiable(artifacts);

  final int schema;
  final DateTime snapshotDate;
  final String releaseState;
  final NativeShimLock shim;
  final OnnxRuntimeLock onnxRuntime;
  final List<NativeReleaseTarget> releaseTargets;
  final List<NativeArtifactLock> artifacts;

  static NativeVersionsLock parse(
    String source, {
    NativeVersionsLockValidationMode mode =
        NativeVersionsLockValidationMode.development,
  }) {
    if (source.length > nativeVersionsLockMaxBytes) {
      throw const FormatException(
        'Native version lock exceeds the size limit.',
      );
    }
    final byteLength = utf8.encode(source).length;
    if (byteLength > nativeVersionsLockMaxBytes) {
      throw const FormatException(
        'Native version lock exceeds the UTF-8 size limit.',
      );
    }

    validateStrictJsonInternal(
      source,
      label: 'Native version lock',
      maximumDepth: 64,
      maximumValueCount: 100000,
    );

    final Object? decoded;
    try {
      decoded = jsonDecode(source);
    } on FormatException catch (error) {
      throw FormatException(
        'Native version lock must use the strict JSON subset of YAML 1.2: '
        '${error.message}',
      );
    }

    return _NativeLockDecoder(mode).decode(decoded);
  }

  /// Verifies vendored source inputs against their locked size and SHA-256.
  ///
  /// Artifact downloads are verified by the build resolver before extraction;
  /// this method covers only source inputs already present in the checkout.
  void verifyVendoredInputs(Directory packageRoot) {
    final canonicalRoot = packageRoot.resolveSymbolicLinksSync();
    for (final input in <LockedSourceInput>[
      onnxRuntime.compatibilityHeader,
      onnxRuntime.epHeader,
      onnxRuntime.license,
    ]) {
      _verifyLockedFile(
        packageRoot: canonicalRoot,
        relativePath: input.relativePath,
        expectedSize: input.sizeBytes,
        expectedSha256: input.sha256,
      );
    }
    for (final artifact in artifacts) {
      for (final license in artifact.licenses) {
        _verifyLockedFile(
          packageRoot: canonicalRoot,
          relativePath: license.noticePath,
          expectedSize: license.sizeBytes,
          expectedSha256: license.sha256,
        );
      }
    }
  }

  void _verifyLockedFile({
    required String packageRoot,
    required String relativePath,
    required int expectedSize,
    required String expectedSha256,
  }) {
    final unresolved = path.join(packageRoot, relativePath);
    if (FileSystemEntity.typeSync(unresolved, followLinks: false) ==
        FileSystemEntityType.link) {
      throw StateError('$relativePath must not be a symbolic link.');
    }
    final file = File(unresolved);
    if (!file.existsSync()) {
      throw StateError('Locked source input is missing: $relativePath');
    }
    final canonicalFile = file.resolveSymbolicLinksSync();
    if (!path.isWithin(packageRoot, canonicalFile)) {
      throw StateError(
        'Locked source input escapes the package root: $relativePath',
      );
    }
    final actualSize = file.lengthSync();
    if (actualSize != expectedSize) {
      throw StateError(
        '$relativePath has size $actualSize; expected $expectedSize.',
      );
    }
    if (actualSize > _maxSourceInputBytes) {
      throw StateError('$relativePath exceeds the source input limit.');
    }
    final actualDigest = sha256.convert(file.readAsBytesSync()).toString();
    if (actualDigest != expectedSha256) {
      throw StateError(
        '$relativePath has SHA-256 $actualDigest; expected $expectedSha256.',
      );
    }
  }
}

final class NativeShimLock {
  const NativeShimLock({
    required this.abi,
    required this.requiredOrtApi,
    required this.sourceRevision,
  });

  final int abi;
  final int requiredOrtApi;
  final String? sourceRevision;
}

final class OnnxRuntimeLock {
  const OnnxRuntimeLock({
    required this.compatibilityApi,
    required this.compatibilityHeader,
    required this.epHeader,
    required this.license,
  });

  final int compatibilityApi;
  final LockedSourceInput compatibilityHeader;
  final LockedSourceInput epHeader;
  final LockedSourceInput license;
}

final class NativeReleaseTarget {
  const NativeReleaseTarget({
    required this.operatingSystem,
    required this.architecture,
    required this.variant,
    required this.flavor,
  });

  final String operatingSystem;
  final String architecture;
  final String variant;
  final String flavor;

  String get identity => '$operatingSystem/$architecture/$variant/$flavor';
}

final class LockedSourceInput {
  const LockedSourceInput({
    required this.version,
    required this.sourceRepository,
    required this.sourceRef,
    required this.relativePath,
    required this.sha256,
    required this.sizeBytes,
  });

  final String version;
  final Uri sourceRepository;
  final String sourceRef;
  final String relativePath;
  final String sha256;
  final int sizeBytes;
}

final class NativeArtifactLock {
  NativeArtifactLock({
    required this.id,
    required this.target,
    required this.flavor,
    required this.runtimeMode,
    required this.source,
    required List<LockedArtifactContainer> containers,
    required List<LockedArtifactFile> expectedFiles,
    required List<LockedArtifactSymlink> expectedSymlinks,
    required List<LockedArtifactNotice> notices,
    required List<LockedProvider> providers,
    required this.build,
    required List<LockedLicense> licenses,
  }) : containers = List.unmodifiable(containers),
       expectedFiles = List.unmodifiable(expectedFiles),
       expectedSymlinks = List.unmodifiable(expectedSymlinks),
       notices = List.unmodifiable(notices),
       providers = List.unmodifiable(providers),
       licenses = List.unmodifiable(licenses);

  final String id;
  final LockedTarget target;
  final String flavor;
  final NativeRuntimeMode runtimeMode;
  final LockedArtifactSource source;
  final List<LockedArtifactContainer> containers;
  final List<LockedArtifactFile> expectedFiles;
  final List<LockedArtifactSymlink> expectedSymlinks;
  final List<LockedArtifactNotice> notices;
  final List<LockedProvider> providers;
  final LockedBuild build;
  final List<LockedLicense> licenses;
}

final class LockedArtifactContainer {
  const LockedArtifactContainer({
    required this.relativePath,
    required this.sha256,
    required this.sizeBytes,
    required this.archive,
  });

  final String relativePath;
  final String sha256;
  final int sizeBytes;
  final String archive;
}

final class LockedTarget {
  const LockedTarget({
    required this.operatingSystem,
    required this.architecture,
    required this.variant,
    required this.minimumOs,
  });

  final String operatingSystem;
  final String architecture;
  final String variant;
  final String minimumOs;
}

final class LockedArtifactSource {
  const LockedArtifactSource({
    required this.url,
    required this.sourceRevision,
    required this.sha256,
    required this.sizeBytes,
    required this.archive,
  });

  final Uri url;
  final String sourceRevision;
  final String sha256;
  final int sizeBytes;
  final String archive;
}

final class LockedArtifactFile {
  const LockedArtifactFile({
    required this.relativePath,
    required this.stagedPath,
    required this.sha256,
    required this.sizeBytes,
  });

  final String relativePath;
  final String stagedPath;
  final String sha256;
  final int sizeBytes;
}

final class LockedArtifactNotice {
  const LockedArtifactNotice({
    required this.id,
    required this.containerDepth,
    required this.relativePath,
    required this.stagedPath,
    required this.sha256,
    required this.sizeBytes,
  });

  final String id;
  final int containerDepth;
  final String relativePath;
  final String stagedPath;
  final String sha256;
  final int sizeBytes;
}

final class LockedArtifactSymlink {
  const LockedArtifactSymlink({
    required this.relativePath,
    required this.target,
  });

  final String relativePath;
  final String target;
}

final class LockedProvider {
  const LockedProvider({required this.wrapperId, required this.reportedName});

  final String wrapperId;
  final String? reportedName;
}

final class LockedBuild {
  LockedBuild({
    required this.sourceBuilt,
    required this.toolchain,
    required List<String> flags,
    required List<LockedPatch> patches,
  }) : flags = List.unmodifiable(flags),
       patches = List.unmodifiable(patches);

  final bool sourceBuilt;
  final String toolchain;
  final List<String> flags;
  final List<LockedPatch> patches;
}

final class LockedPatch {
  const LockedPatch({required this.url, required this.sha256});

  final Uri url;
  final String sha256;
}

final class LockedLicense {
  const LockedLicense({
    required this.id,
    required this.noticePath,
    required this.sha256,
    required this.sizeBytes,
  });

  final String id;
  final String noticePath;
  final String sha256;
  final int sizeBytes;
}

final class _NativeLockDecoder {
  _NativeLockDecoder(this.mode);

  static final RegExp _placeholder = RegExp(
    r'(replace|placeholder|todo|tbd|<[^>]+>)',
    caseSensitive: false,
  );
  static final RegExp _sha256 = RegExp(r'^[0-9a-fA-F]{64}$');
  static final RegExp _revision = RegExp(
    r'^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$',
  );
  static final RegExp _identifier = RegExp(r'^[a-z0-9][a-z0-9._-]{0,127}$');
  static final RegExp _providerId = RegExp(r'^[a-z][a-z0-9_-]{0,63}$');
  static final RegExp _minimumOs = RegExp(
    r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$',
  );

  static const Map<String, Set<String>> _architectures = {
    'android': {'arm64-v8a', 'x86_64', 'armeabi-v7a', 'x86'},
    'ios': {'arm64', 'x86_64'},
    'macos': {'arm64', 'x86_64'},
    'linux': {'arm64', 'x86_64'},
    'windows': {'arm64', 'x64'},
  };
  static const Set<String> _requiredReleaseTargetIdentities = {
    'ios/arm64/device/cpu',
    'ios/arm64/simulator/cpu',
    'ios/x86_64/simulator/cpu',
    'macos/arm64/default/cpu',
    'android/arm64-v8a/default/cpu',
    'android/x86_64/default/cpu',
    'linux/x86_64/default/cpu',
    'linux/arm64/default/cpu',
    'windows/x64/default/cpu',
  };

  final NativeVersionsLockValidationMode mode;

  NativeVersionsLock decode(Object? value) {
    final root = _object(value, r'$');
    _fields(
      root,
      r'$',
      required: const {
        'schema',
        'snapshot_date',
        'release_state',
        'shim',
        'onnxruntime',
        'release_targets',
        'artifacts',
      },
    );

    final schema = _integer(
      root['schema'],
      r'$.schema',
      minimum: 2,
      maximum: 2,
    );
    final snapshotDate = _date(root['snapshot_date'], r'$.snapshot_date');
    final releaseState = _enumValue(
      root['release_state'],
      r'$.release_state',
      const {'unreleased-preview', 'release'},
    );
    final shim = _shim(root['shim']);
    final onnxRuntime = _onnxRuntime(root['onnxruntime']);
    final releaseTargets = _releaseTargets(root['release_targets']);
    final artifacts = _artifacts(root['artifacts']);

    if (shim.requiredOrtApi != onnxRuntime.compatibilityApi) {
      _fail(
        r'$.shim.required_ort_api',
        'must equal onnxruntime.compatibility_floor.c_api',
      );
    }
    if (mode == NativeVersionsLockValidationMode.release) {
      if (releaseState != 'release') {
        _fail(r'$.release_state', 'must be "release" for release validation');
      }
      if (shim.sourceRevision == null) {
        _fail(r'$.shim.source_revision', 'is required for release validation');
      }
      for (final artifact in artifacts) {
        for (final provider in artifact.providers) {
          if (provider.reportedName == null) {
            _fail(
              r'$.artifacts',
              'must record every reported provider name for a release',
            );
          }
        }
      }
      final coveredTargets = artifacts
          .map(
            (artifact) =>
                '${artifact.target.operatingSystem}/'
                '${artifact.target.architecture}/'
                '${artifact.target.variant}/${artifact.flavor}',
          )
          .toSet();
      final missingTargets = _requiredReleaseTargetIdentities.difference(
        coveredTargets,
      );
      if (missingTargets.isNotEmpty) {
        final sortedMissing = missingTargets.toList()..sort();
        _fail(
          r'$.artifacts',
          'does not cover release targets: ${sortedMissing.join(', ')}',
        );
      }
    }

    return NativeVersionsLock._(
      schema: schema,
      snapshotDate: snapshotDate,
      releaseState: releaseState,
      shim: shim,
      onnxRuntime: onnxRuntime,
      releaseTargets: releaseTargets,
      artifacts: artifacts,
    );
  }

  NativeShimLock _shim(Object? value) {
    const objectPath = r'$.shim';
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {'abi', 'required_ort_api', 'source_revision'},
    );
    final revisionValue = object['source_revision'];
    final String? revision;
    if (revisionValue == null) {
      revision = null;
    } else {
      revision = _string(revisionValue, '$objectPath.source_revision');
      if (!_revision.hasMatch(revision)) {
        _fail(
          '$objectPath.source_revision',
          'must be a 40- or 64-character hexadecimal revision',
        );
      }
    }
    return NativeShimLock(
      abi: _integer(object['abi'], '$objectPath.abi', minimum: 1),
      requiredOrtApi: _integer(
        object['required_ort_api'],
        '$objectPath.required_ort_api',
        minimum: 1,
      ),
      sourceRevision: revision?.toLowerCase(),
    );
  }

  OnnxRuntimeLock _onnxRuntime(Object? value) {
    const objectPath = r'$.onnxruntime';
    final object = _object(value, objectPath);
    _fields(object, objectPath, required: const {'compatibility_floor'});
    final floorPath = '$objectPath.compatibility_floor';
    final floor = _object(object['compatibility_floor'], floorPath);
    _fields(
      floor,
      floorPath,
      required: const {'c_api', 'header', 'ep_header', 'license'},
    );
    return OnnxRuntimeLock(
      compatibilityApi: _integer(
        floor['c_api'],
        '$floorPath.c_api',
        minimum: 1,
      ),
      compatibilityHeader: _sourceInput(floor['header'], '$floorPath.header'),
      epHeader: _sourceInput(floor['ep_header'], '$floorPath.ep_header'),
      license: _sourceInput(floor['license'], '$floorPath.license'),
    );
  }

  LockedSourceInput _sourceInput(Object? value, String objectPath) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {
        'version',
        'source_repository',
        'source_ref',
        'path',
        'sha256',
        'size_bytes',
      },
    );
    return LockedSourceInput(
      version: _string(
        object['version'],
        '$objectPath.version',
        maxLength: 128,
      ),
      sourceRepository: _httpsUrl(
        object['source_repository'],
        '$objectPath.source_repository',
      ),
      sourceRef: _string(
        object['source_ref'],
        '$objectPath.source_ref',
        maxLength: 256,
      ),
      relativePath: _relativePath(object['path'], '$objectPath.path'),
      sha256: _digest(object['sha256'], '$objectPath.sha256'),
      sizeBytes: _integer(
        object['size_bytes'],
        '$objectPath.size_bytes',
        minimum: 1,
        maximum: _maxSourceInputBytes,
      ),
    );
  }

  List<NativeReleaseTarget> _releaseTargets(Object? value) {
    final list = _nonEmptyList(value, r'$.release_targets', maximum: 32);
    final identities = <String>{};
    final targets = <NativeReleaseTarget>[];
    for (var index = 0; index < list.length; index++) {
      final objectPath =
          r'$.release_targets'
          '[$index]';
      final object = _object(list[index], objectPath);
      _fields(
        object,
        objectPath,
        required: const {'os', 'architecture', 'variant', 'flavor'},
      );
      final operatingSystem = _enumValue(
        object['os'],
        '$objectPath.os',
        _architectures.keys.toSet(),
      );
      final architecture = _enumValue(
        object['architecture'],
        '$objectPath.architecture',
        _architectures[operatingSystem]!,
      );
      final target = NativeReleaseTarget(
        operatingSystem: operatingSystem,
        architecture: architecture,
        variant: _variant(
          object['variant'],
          '$objectPath.variant',
          operatingSystem,
        ),
        flavor: _enumValue(object['flavor'], '$objectPath.flavor', const {
          'cpu',
        }),
      );
      if (!identities.add(target.identity)) {
        _fail(objectPath, 'duplicates release target ${target.identity}');
      }
      targets.add(target);
    }
    if (identities.length != _requiredReleaseTargetIdentities.length ||
        !identities.containsAll(_requiredReleaseTargetIdentities)) {
      _fail(
        r'$.release_targets',
        'must exactly declare the schema-v2 Tier-1 CPU matrix',
      );
    }
    return targets;
  }

  List<NativeArtifactLock> _artifacts(Object? value) {
    final list = _list(value, r'$.artifacts', maximum: _maxArtifacts);
    final ids = <String>{};
    return [
      for (var index = 0; index < list.length; index++)
        _artifact(
          list[index],
          r'$.artifacts'
          '[$index]',
          ids,
        ),
    ];
  }

  NativeArtifactLock _artifact(
    Object? value,
    String objectPath,
    Set<String> ids,
  ) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {
        'id',
        'target',
        'flavor',
        'runtime_mode',
        'source',
        'containers',
        'expected_files',
        'expected_symlinks',
        'notices',
        'providers',
        'build',
        'licenses',
      },
    );
    final id = _matchingString(
      object['id'],
      '$objectPath.id',
      _identifier,
      'a lowercase artifact identifier',
    );
    if (!ids.add(id)) {
      _fail('$objectPath.id', 'duplicates artifact id "$id"');
    }

    final containers = _containers(
      object['containers'],
      '$objectPath.containers',
    );
    final expectedFiles = _expectedFiles(
      object['expected_files'],
      '$objectPath.expected_files',
    );
    final expectedFilePaths = expectedFiles
        .map((file) => file.relativePath)
        .toSet();
    final expectedSymlinks = _expectedSymlinks(
      object['expected_symlinks'],
      '$objectPath.expected_symlinks',
    );
    final expectedSymlinksByPath = {
      for (final symlink in expectedSymlinks) symlink.relativePath: symlink,
    };
    final notices = _notices(
      object['notices'],
      '$objectPath.notices',
      maximumContainerDepth: containers.length,
    );
    final selectedMemberPaths = <int, Set<String>>{};
    void addSelectedMember(int depth, String relativePath, String valuePath) {
      final paths = selectedMemberPaths.putIfAbsent(depth, () => <String>{});
      if (!paths.add(relativePath)) {
        _fail(
          valuePath,
          'overlaps another selected member at container depth $depth',
        );
      }
    }

    for (var index = 0; index < containers.length; index++) {
      addSelectedMember(
        index,
        containers[index].relativePath,
        '$objectPath.containers[$index].path',
      );
    }
    for (var index = 0; index < expectedFiles.length; index++) {
      addSelectedMember(
        containers.length,
        expectedFiles[index].relativePath,
        '$objectPath.expected_files[$index].path',
      );
    }
    for (var index = 0; index < expectedSymlinks.length; index++) {
      addSelectedMember(
        containers.length,
        expectedSymlinks[index].relativePath,
        '$objectPath.expected_symlinks[$index].path',
      );
    }
    for (var index = 0; index < notices.length; index++) {
      addSelectedMember(
        notices[index].containerDepth,
        notices[index].relativePath,
        '$objectPath.notices[$index].path',
      );
    }
    final stagedPaths = <String>{};
    for (var index = 0; index < expectedFiles.length; index++) {
      if (!stagedPaths.add(expectedFiles[index].stagedPath)) {
        _fail(
          '$objectPath.expected_files[$index].staged_path',
          'duplicates staged path "${expectedFiles[index].stagedPath}"',
        );
      }
    }
    for (var index = 0; index < notices.length; index++) {
      if (!stagedPaths.add(notices[index].stagedPath)) {
        _fail(
          '$objectPath.notices[$index].staged_path',
          'overlaps another staged path "${notices[index].stagedPath}"',
        );
      }
    }
    for (var index = 0; index < expectedSymlinks.length; index++) {
      final symlink = expectedSymlinks[index];
      if (expectedFilePaths.contains(symlink.relativePath)) {
        _fail(
          '$objectPath.expected_symlinks[$index].path',
          'must not overlap an expected regular file',
        );
      }
      var resolvedTarget = path.posix.normalize(
        path.posix.join(
          path.posix.dirname(symlink.relativePath),
          symlink.target,
        ),
      );
      final visited = <String>{symlink.relativePath};
      while (!expectedFilePaths.contains(resolvedTarget)) {
        final next = expectedSymlinksByPath[resolvedTarget];
        if (next == null || !visited.add(resolvedTarget)) {
          break;
        }
        resolvedTarget = path.posix.normalize(
          path.posix.join(path.posix.dirname(next.relativePath), next.target),
        );
      }
      if (!expectedFilePaths.contains(resolvedTarget)) {
        _fail(
          '$objectPath.expected_symlinks[$index].target',
          'must resolve through an acyclic declared chain to an expected regular file',
        );
      }
    }

    return NativeArtifactLock(
      id: id,
      target: _target(object['target'], '$objectPath.target'),
      flavor: _matchingString(
        object['flavor'],
        '$objectPath.flavor',
        _identifier,
        'a lowercase flavor identifier',
        maxLength: 64,
      ),
      runtimeMode: NativeRuntimeMode.values.byName(
        _enumValue(
          object['runtime_mode'],
          '$objectPath.runtime_mode',
          NativeRuntimeMode.values.map((mode) => mode.name).toSet(),
        ),
      ),
      source: _artifactSource(object['source'], '$objectPath.source'),
      containers: containers,
      expectedFiles: expectedFiles,
      expectedSymlinks: expectedSymlinks,
      notices: notices,
      providers: _providers(object['providers'], '$objectPath.providers'),
      build: _build(object['build'], '$objectPath.build'),
      licenses: _licenses(object['licenses'], '$objectPath.licenses'),
    );
  }

  List<LockedArtifactContainer> _containers(Object? value, String listPath) {
    final list = _list(value, listPath, maximum: _maxNestedContainers);
    return [
      for (var index = 0; index < list.length; index++)
        _container(list[index], '$listPath[$index]'),
    ];
  }

  LockedArtifactContainer _container(Object? value, String objectPath) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {'path', 'sha256', 'size_bytes', 'archive'},
    );
    return LockedArtifactContainer(
      relativePath: _relativePath(object['path'], '$objectPath.path'),
      sha256: _digest(object['sha256'], '$objectPath.sha256'),
      sizeBytes: _integer(
        object['size_bytes'],
        '$objectPath.size_bytes',
        minimum: 1,
      ),
      archive: _archive(object['archive'], '$objectPath.archive'),
    );
  }

  LockedTarget _target(Object? value, String objectPath) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {'os', 'architecture', 'variant', 'min_os'},
    );
    final operatingSystem = _enumValue(
      object['os'],
      '$objectPath.os',
      _architectures.keys.toSet(),
    );
    final architecture = _enumValue(
      object['architecture'],
      '$objectPath.architecture',
      _architectures[operatingSystem]!,
    );
    return LockedTarget(
      operatingSystem: operatingSystem,
      architecture: architecture,
      variant: _variant(
        object['variant'],
        '$objectPath.variant',
        operatingSystem,
      ),
      minimumOs: _matchingString(
        object['min_os'],
        '$objectPath.min_os',
        _minimumOs,
        'a concrete minimum OS/toolchain baseline',
        maxLength: 64,
      ),
    );
  }

  LockedArtifactSource _artifactSource(Object? value, String objectPath) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {
        'url',
        'source_revision',
        'sha256',
        'size_bytes',
        'archive',
      },
    );
    return LockedArtifactSource(
      url: _httpsUrl(object['url'], '$objectPath.url'),
      sourceRevision: _matchingString(
        object['source_revision'],
        '$objectPath.source_revision',
        _revision,
        'a 40- or 64-character hexadecimal revision',
        maxLength: 64,
      ).toLowerCase(),
      sha256: _digest(object['sha256'], '$objectPath.sha256'),
      sizeBytes: _integer(
        object['size_bytes'],
        '$objectPath.size_bytes',
        minimum: 1,
      ),
      archive: _archive(object['archive'], '$objectPath.archive'),
    );
  }

  String _archive(Object? value, String valuePath) =>
      _enumValue(value, valuePath, const {'zip', 'tar.gz', 'tgz'});

  String _variant(Object? value, String valuePath, String operatingSystem) {
    final allowed = operatingSystem == 'ios'
        ? const {'device', 'simulator'}
        : const {'default'};
    return _enumValue(value, valuePath, allowed);
  }

  List<LockedArtifactFile> _expectedFiles(Object? value, String listPath) {
    final list = _nonEmptyList(value, listPath, maximum: _maxExpectedFiles);
    final paths = <String>{};
    return [
      for (var index = 0; index < list.length; index++)
        _expectedFile(list[index], '$listPath[$index]', paths),
    ];
  }

  LockedArtifactFile _expectedFile(
    Object? value,
    String objectPath,
    Set<String> paths,
  ) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {'path', 'staged_path', 'sha256', 'size_bytes'},
    );
    final relativePath = _relativePath(object['path'], '$objectPath.path');
    if (!paths.add(relativePath)) {
      _fail('$objectPath.path', 'duplicates expected path "$relativePath"');
    }
    return LockedArtifactFile(
      relativePath: relativePath,
      stagedPath: _relativePath(
        object['staged_path'],
        '$objectPath.staged_path',
      ),
      sha256: _digest(object['sha256'], '$objectPath.sha256'),
      sizeBytes: _integer(
        object['size_bytes'],
        '$objectPath.size_bytes',
        minimum: 1,
      ),
    );
  }

  List<LockedArtifactNotice> _notices(
    Object? value,
    String listPath, {
    required int maximumContainerDepth,
  }) {
    final list = _nonEmptyList(value, listPath, maximum: _maxArtifactNotices);
    final identities = <String>{};
    return [
      for (var index = 0; index < list.length; index++)
        _notice(
          list[index],
          '$listPath[$index]',
          identities,
          maximumContainerDepth: maximumContainerDepth,
        ),
    ];
  }

  LockedArtifactNotice _notice(
    Object? value,
    String objectPath,
    Set<String> identities, {
    required int maximumContainerDepth,
  }) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {
        'id',
        'container_depth',
        'path',
        'staged_path',
        'sha256',
        'size_bytes',
      },
    );
    final id = _string(object['id'], '$objectPath.id', maxLength: 128);
    final containerDepth = _integer(
      object['container_depth'],
      '$objectPath.container_depth',
      minimum: 0,
      maximum: maximumContainerDepth,
    );
    final relativePath = _relativePath(object['path'], '$objectPath.path');
    final identity = '$containerDepth/$relativePath';
    if (!identities.add(identity)) {
      _fail('$objectPath.path', 'duplicates notice member "$identity"');
    }
    return LockedArtifactNotice(
      id: id,
      containerDepth: containerDepth,
      relativePath: relativePath,
      stagedPath: _relativePath(
        object['staged_path'],
        '$objectPath.staged_path',
      ),
      sha256: _digest(object['sha256'], '$objectPath.sha256'),
      sizeBytes: _integer(
        object['size_bytes'],
        '$objectPath.size_bytes',
        minimum: 1,
      ),
    );
  }

  List<LockedArtifactSymlink> _expectedSymlinks(
    Object? value,
    String listPath,
  ) {
    final list = _list(value, listPath, maximum: 256);
    final paths = <String>{};
    return [
      for (var index = 0; index < list.length; index++)
        _expectedSymlink(list[index], '$listPath[$index]', paths),
    ];
  }

  LockedArtifactSymlink _expectedSymlink(
    Object? value,
    String objectPath,
    Set<String> paths,
  ) {
    final object = _object(value, objectPath);
    _fields(object, objectPath, required: const {'path', 'target'});
    final relativePath = _relativePath(object['path'], '$objectPath.path');
    if (!paths.add(relativePath)) {
      _fail('$objectPath.path', 'duplicates expected symlink "$relativePath"');
    }
    return LockedArtifactSymlink(
      relativePath: relativePath,
      target: _relativePath(object['target'], '$objectPath.target'),
    );
  }

  List<LockedProvider> _providers(Object? value, String listPath) {
    final list = _nonEmptyList(value, listPath, maximum: 64);
    final ids = <String>{};
    return [
      for (var index = 0; index < list.length; index++)
        _provider(list[index], '$listPath[$index]', ids),
    ];
  }

  LockedProvider _provider(Object? value, String objectPath, Set<String> ids) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {'wrapper_id', 'reported_name'},
    );
    final id = _matchingString(
      object['wrapper_id'],
      '$objectPath.wrapper_id',
      _providerId,
      'a lowercase wrapper provider id',
      maxLength: 64,
    );
    if (!ids.add(id)) {
      _fail('$objectPath.wrapper_id', 'duplicates provider id "$id"');
    }
    final reportedNameValue = object['reported_name'];
    return LockedProvider(
      wrapperId: id,
      reportedName: reportedNameValue == null
          ? null
          : _string(
              reportedNameValue,
              '$objectPath.reported_name',
              maxLength: 128,
            ),
    );
  }

  LockedBuild _build(Object? value, String objectPath) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {'source_built', 'toolchain', 'flags', 'patches'},
    );
    final flags = _list(object['flags'], '$objectPath.flags', maximum: 128)
        .indexed
        .map(
          (entry) => _string(
            entry.$2,
            '$objectPath.flags[${entry.$1}]',
            maxLength: 512,
          ),
        )
        .toList(growable: false);
    final patchValues = _list(
      object['patches'],
      '$objectPath.patches',
      maximum: 64,
    );
    return LockedBuild(
      sourceBuilt: _boolean(object['source_built'], '$objectPath.source_built'),
      toolchain: _string(
        object['toolchain'],
        '$objectPath.toolchain',
        maxLength: 256,
      ),
      flags: flags,
      patches: [
        for (var index = 0; index < patchValues.length; index++)
          _patch(patchValues[index], '$objectPath.patches[$index]'),
      ],
    );
  }

  LockedPatch _patch(Object? value, String objectPath) {
    final object = _object(value, objectPath);
    _fields(object, objectPath, required: const {'url', 'sha256'});
    return LockedPatch(
      url: _httpsUrl(object['url'], '$objectPath.url'),
      sha256: _digest(object['sha256'], '$objectPath.sha256'),
    );
  }

  List<LockedLicense> _licenses(Object? value, String listPath) {
    final list = _nonEmptyList(value, listPath, maximum: 128);
    final ids = <String>{};
    return [
      for (var index = 0; index < list.length; index++)
        _license(list[index], '$listPath[$index]', ids),
    ];
  }

  LockedLicense _license(Object? value, String objectPath, Set<String> ids) {
    final object = _object(value, objectPath);
    _fields(
      object,
      objectPath,
      required: const {'id', 'notice_path', 'sha256', 'size_bytes'},
    );
    final id = _string(object['id'], '$objectPath.id', maxLength: 128);
    if (!ids.add(id)) {
      _fail('$objectPath.id', 'duplicates license id "$id"');
    }
    return LockedLicense(
      id: id,
      noticePath: _relativePath(
        object['notice_path'],
        '$objectPath.notice_path',
      ),
      sha256: _digest(object['sha256'], '$objectPath.sha256'),
      sizeBytes: _integer(
        object['size_bytes'],
        '$objectPath.size_bytes',
        minimum: 1,
        maximum: _maxSourceInputBytes,
      ),
    );
  }

  Map<String, Object?> _object(Object? value, String objectPath) {
    if (value is! Map<String, Object?>) {
      _fail(objectPath, 'must be an object');
    }
    return value;
  }

  List<Object?> _list(Object? value, String listPath, {required int maximum}) {
    if (value is! List<Object?>) {
      _fail(listPath, 'must be an array');
    }
    if (value.length > maximum) {
      _fail(listPath, 'must contain at most $maximum entries');
    }
    return value;
  }

  List<Object?> _nonEmptyList(
    Object? value,
    String listPath, {
    required int maximum,
  }) {
    final list = _list(value, listPath, maximum: maximum);
    if (list.isEmpty) {
      _fail(listPath, 'must not be empty');
    }
    return list;
  }

  void _fields(
    Map<String, Object?> object,
    String objectPath, {
    required Set<String> required,
  }) {
    final missing = required.difference(object.keys.toSet()).toList()..sort();
    if (missing.isNotEmpty) {
      _fail(objectPath, 'is missing required fields: ${missing.join(', ')}');
    }
    final unknown = object.keys.toSet().difference(required).toList()..sort();
    if (unknown.isNotEmpty) {
      _fail(objectPath, 'contains unknown fields: ${unknown.join(', ')}');
    }
  }

  String _string(Object? value, String valuePath, {int maxLength = 2048}) {
    if (value is! String || value.isEmpty) {
      _fail(valuePath, 'must be a non-empty string');
    }
    for (var index = 0; index < value.length; index++) {
      final codeUnit = value.codeUnitAt(index);
      if (codeUnit <= 0x1f || (codeUnit >= 0x7f && codeUnit <= 0x9f)) {
        _fail(valuePath, 'must not contain C0 or C1 control characters');
      }
      if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
        if (index + 1 >= value.length) {
          _fail(valuePath, 'must contain well-formed Unicode text');
        }
        final trailing = value.codeUnitAt(index + 1);
        if (trailing < 0xdc00 || trailing > 0xdfff) {
          _fail(valuePath, 'must contain well-formed Unicode text');
        }
        index++;
      } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
        _fail(valuePath, 'must contain well-formed Unicode text');
      }
    }
    if (utf8.encode(value).length > maxLength) {
      _fail(valuePath, 'must contain at most $maxLength UTF-8 bytes');
    }
    if (_placeholder.hasMatch(value)) {
      _fail(valuePath, 'contains a forbidden placeholder marker');
    }
    return value;
  }

  String _matchingString(
    Object? value,
    String valuePath,
    RegExp expression,
    String expectation, {
    int maxLength = 128,
  }) {
    final result = _string(value, valuePath, maxLength: maxLength);
    if (!expression.hasMatch(result)) {
      _fail(valuePath, 'must be $expectation');
    }
    return result;
  }

  String _enumValue(Object? value, String valuePath, Set<String> allowed) {
    final result = _string(value, valuePath, maxLength: 128);
    if (!allowed.contains(result)) {
      final choices = allowed.toList()..sort();
      _fail(valuePath, 'must be one of: ${choices.join(', ')}');
    }
    return result;
  }

  int _integer(
    Object? value,
    String valuePath, {
    required int minimum,
    int maximum = 0xFFFFFFFF,
  }) {
    if (value is! int || value < minimum || value > maximum) {
      _fail(valuePath, 'must be an integer from $minimum through $maximum');
    }
    return value;
  }

  bool _boolean(Object? value, String valuePath) {
    if (value is! bool) {
      _fail(valuePath, 'must be a boolean');
    }
    return value;
  }

  String _digest(Object? value, String valuePath) {
    final digest = _string(value, valuePath, maxLength: 64);
    if (!_sha256.hasMatch(digest)) {
      _fail(valuePath, 'must be a 64-character hexadecimal SHA-256');
    }
    return digest.toLowerCase();
  }

  Uri _httpsUrl(Object? value, String valuePath) {
    final text = _string(value, valuePath);
    final uri = Uri.tryParse(text);
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.hasFragment ||
        uri.hasQuery ||
        uri.userInfo.isNotEmpty ||
        !uri.isAbsolute) {
      _fail(
        valuePath,
        'must be an absolute HTTPS URL without credentials, query, or fragment',
      );
    }
    return uri;
  }

  String _relativePath(Object? value, String valuePath) {
    final text = _string(value, valuePath, maxLength: 1024);
    if (text.contains('\\') ||
        text.startsWith('/') ||
        path.isAbsolute(text) ||
        text
            .split('/')
            .any((part) => part.isEmpty || part == '.' || part == '..') ||
        path.posix.normalize(text) != text) {
      _fail(valuePath, 'must be a canonical relative POSIX path');
    }
    return text;
  }

  DateTime _date(Object? value, String valuePath) {
    final text = _string(value, valuePath, maxLength: 10);
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(text)) {
      _fail(valuePath, 'must use YYYY-MM-DD');
    }
    final parsed = DateTime.tryParse('${text}T00:00:00Z');
    if (parsed == null || parsed.toIso8601String().substring(0, 10) != text) {
      _fail(valuePath, 'must be a valid calendar date');
    }
    return parsed;
  }

  Never _fail(String valuePath, String message) {
    throw FormatException('Native version lock $valuePath $message.');
  }
}
