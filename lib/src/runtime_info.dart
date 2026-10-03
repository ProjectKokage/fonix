import 'dart:convert';

import 'runtime_source.dart';
import 'strict_json.dart';
import 'utf16.dart';
import 'version.dart';

const int _maximumInfoJsonBytes = 64 * 1024;
const int _maximumIdentityBytes = 512;
const int _maximumBuildIdBytes = 128;
const int _maximumArtifactValueBytes = 128;
const int _maximumRuntimeVersionBytes = 256;
const int _maximumLogIdBytes = 128;

/// The native shim profile selected by the build hook.
enum OrtRuntimeProfile {
  /// Runtime is supplied by the process or an explicit desktop file.
  external,

  /// Runtime is package-owned and loaded from an application-local artifact.
  bundled,

  /// Runtime is linked into the application image.
  linked,
}

/// The single ONNX Runtime owner selected for an Android application.
enum OrtAndroidRuntimeOwner {
  /// A sherpa_onnx-loaded process runtime is the only permitted source.
  sherpa,

  /// The application bundles and owns the exact lock-selected runtime.
  application,
}

/// Immutable, validated build identity reported by the project-owned shim.
final class OrtNativeBuildInfo {
  OrtNativeBuildInfo._({
    required this.schemaVersion,
    required this.nativeIdentity,
    required this.shimAbiVersion,
    required this.requiredOrtApiVersion,
    required this.runtimeProfile,
    required this.androidRuntimeOwner,
    required List<OrtRuntimeSourceKind> allowedRuntimeSources,
    required this.buildId,
    required this.artifact,
  }) : allowedRuntimeSources = List<OrtRuntimeSourceKind>.unmodifiable(
         allowedRuntimeSources,
       );

  final int schemaVersion;
  final String nativeIdentity;
  final int shimAbiVersion;
  final int requiredOrtApiVersion;
  final OrtRuntimeProfile runtimeProfile;
  final OrtAndroidRuntimeOwner? androidRuntimeOwner;

  /// The complete closed set of runtime sources accepted by this shim build.
  final List<OrtRuntimeSourceKind> allowedRuntimeSources;

  final String buildId;
  final OrtNativeArtifactIdentity? artifact;

  Map<String, Object?> toJson() => <String, Object?>{
    'schemaVersion': schemaVersion,
    'nativeIdentity': nativeIdentity,
    'shimAbiVersion': shimAbiVersion,
    'requiredOrtApiVersion': requiredOrtApiVersion,
    'runtimeProfile': runtimeProfile.name,
    'androidRuntimeOwner': androidRuntimeOwner?.name,
    'allowedRuntimeSources': allowedRuntimeSources
        .map((OrtRuntimeSourceKind source) => source.name)
        .toList(growable: false),
    'buildId': buildId,
    'artifact': artifact?.toJson(),
  };
}

/// Exact lock-selected native artifact identity embedded in the shim.
final class OrtNativeArtifactIdentity {
  OrtNativeArtifactIdentity._({
    required this.id,
    required this.lockSha256,
    required this.sourceSha256,
    required this.targetOs,
    required this.targetArchitecture,
    required this.targetVariant,
    required this.minimumOs,
    required this.flavor,
    required this.runtimeMode,
    required this.thirdPartyNoticesSha256,
    required Map<String, String?> providers,
  }) : providers = Map<String, String?>.unmodifiable(providers);

  final String id;
  final String lockSha256;
  final String sourceSha256;
  final String targetOs;
  final String targetArchitecture;
  final String targetVariant;
  final String minimumOs;
  final String flavor;
  final OrtRuntimeProfile runtimeMode;
  final String thirdPartyNoticesSha256;

  /// Exact compiled-provider inventory declared by the selected lock entry.
  final Map<String, String?> providers;

  Map<String, Object?> toJson() => <String, Object?>{
    'id': id,
    'lockSha256': lockSha256,
    'sourceSha256': sourceSha256,
    'targetOs': targetOs,
    'targetArchitecture': targetArchitecture,
    'targetVariant': targetVariant,
    'minimumOs': minimumOs,
    'flavor': flavor,
    'runtimeMode': runtimeMode.name,
    'thirdPartyNoticesSha256': thirdPartyNoticesSha256,
    'providers': <Object?>[
      for (final entry in providers.entries)
        <String, Object?>{'wrapperId': entry.key, 'reportedName': entry.value},
    ],
  };
}

/// Immutable runtime facts copied from native memory during open.
final class OrtRuntimeInfo {
  const OrtRuntimeInfo._({
    required this.schemaVersion,
    required this.nativeIdentity,
    required this.shimAbiVersion,
    required this.shimBuildId,
    required this.requiredOrtApiVersion,
    required this.negotiatedOrtApiVersion,
    required this.runtimeVersion,
    required this.runtimeSource,
    required this.runtimeProfile,
    required this.runtimeLibraryIdentity,
    required this.logSeverity,
    required this.logId,
  });

  final int schemaVersion;
  final String nativeIdentity;
  final int shimAbiVersion;
  final String shimBuildId;
  final int requiredOrtApiVersion;
  final int negotiatedOrtApiVersion;
  final String runtimeVersion;
  final OrtRuntimeSourceKind runtimeSource;
  final OrtRuntimeProfile runtimeProfile;

  /// A redacted native identity, never an absolute runtime path.
  final String runtimeLibraryIdentity;

  final OrtLogSeverity logSeverity;
  final String logId;

  Map<String, Object?> toJson() => <String, Object?>{
    'dartPackageVersion': fonixPackageVersion,
    'schemaVersion': schemaVersion,
    'nativeIdentity': nativeIdentity,
    'shimAbiVersion': shimAbiVersion,
    'shimBuildId': shimBuildId,
    'requiredOrtApiVersion': requiredOrtApiVersion,
    'negotiatedOrtApiVersion': negotiatedOrtApiVersion,
    'runtimeVersion': runtimeVersion,
    'runtimeSource': runtimeSource.name,
    'runtimeProfile': runtimeProfile.name,
    'runtimeLibraryIdentity': runtimeLibraryIdentity,
    'logSeverity': logSeverity.name,
    'logId': logId,
  };
}

/// Parses the bounded build-info protocol used between the shim and Dart.
///
/// This function is deliberately not exported by `package:fonix/fonix.dart`;
/// it remains visible to focused protocol tests.
OrtNativeBuildInfo parseOrtNativeBuildInfo(String json) {
  final Map<String, Object?> object = _parseObject(json, 'build manifest');
  _requireExactKeys(object, const <String>{
    'schemaVersion',
    'nativeIdentity',
    'shimAbiVersion',
    'requiredOrtApiVersion',
    'runtimeProfile',
    'androidRuntimeOwner',
    'allowedRuntimeSources',
    'buildId',
    'artifact',
  });

  final int schemaVersion = _requiredInt(object, 'schemaVersion');
  final String nativeIdentity = _requiredString(
    object,
    'nativeIdentity',
    maximumBytes: _maximumIdentityBytes,
  );
  final int shimAbiVersion = _requiredInt(object, 'shimAbiVersion');
  final int requiredOrtApiVersion = _requiredInt(
    object,
    'requiredOrtApiVersion',
  );
  final OrtRuntimeProfile runtimeProfile = _parseProfile(
    _requiredString(
      object,
      'runtimeProfile',
      maximumBytes: _maximumBuildIdBytes,
    ),
  );
  final OrtAndroidRuntimeOwner? androidRuntimeOwner = _parseAndroidRuntimeOwner(
    object,
  );
  final List<OrtRuntimeSourceKind> allowedRuntimeSources =
      _parseAllowedRuntimeSources(object['allowedRuntimeSources']);
  final String buildId = _requiredString(
    object,
    'buildId',
    maximumBytes: _maximumBuildIdBytes,
  );
  final OrtNativeArtifactIdentity? artifact = _parseArtifactIdentity(
    object['artifact'],
  );

  if (schemaVersion != 3 || nativeIdentity != 'fonix_shim') {
    throw const FormatException('Unsupported native build-info protocol.');
  }
  if (shimAbiVersion != fonixShimAbiVersion ||
      requiredOrtApiVersion != OrtApiVersion.v27.value) {
    throw const FormatException(
      'Native build information does not match the Dart ABI/API contract.',
    );
  }
  if (!_buildId.hasMatch(buildId)) {
    throw const FormatException('Native build ID contains unsafe characters.');
  }
  if ((runtimeProfile == OrtRuntimeProfile.external && artifact != null) ||
      (runtimeProfile != OrtRuntimeProfile.external && artifact == null) ||
      (artifact != null && artifact.runtimeMode != runtimeProfile)) {
    throw const FormatException(
      'Native artifact identity does not match the runtime profile.',
    );
  }
  final bool isExactProcessBuild = RegExp(
    r'^process-(?:macos-lib[A-Za-z0-9._-]+\.dylib|'
    r'linux-lib[A-Za-z0-9._-]+\.so(?:\.[0-9]+)*|'
    r'windows-[A-Za-z0-9._-]+\.dll)'
    r'(?:-buildinfo-[0-9a-f]{64})?$',
  ).hasMatch(buildId);
  final List<OrtRuntimeSourceKind> expectedSources =
      switch (androidRuntimeOwner) {
        OrtAndroidRuntimeOwner.sherpa => const <OrtRuntimeSourceKind>[
          OrtRuntimeSourceKind.process,
        ],
        OrtAndroidRuntimeOwner.application => const <OrtRuntimeSourceKind>[
          OrtRuntimeSourceKind.bundled,
        ],
        null when isExactProcessBuild => const <OrtRuntimeSourceKind>[
          OrtRuntimeSourceKind.process,
        ],
        null => switch (runtimeProfile) {
          OrtRuntimeProfile.external => const <OrtRuntimeSourceKind>[
            OrtRuntimeSourceKind.process,
            OrtRuntimeSourceKind.file,
          ],
          OrtRuntimeProfile.bundled => const <OrtRuntimeSourceKind>[
            OrtRuntimeSourceKind.bundled,
          ],
          OrtRuntimeProfile.linked => const <OrtRuntimeSourceKind>[
            OrtRuntimeSourceKind.linked,
          ],
        },
      };
  if (!_sameRuntimeSources(allowedRuntimeSources, expectedSources)) {
    throw const FormatException(
      'Native runtime-source policy contradicts the build profile or owner.',
    );
  }
  switch (androidRuntimeOwner) {
    case OrtAndroidRuntimeOwner.sherpa:
      if (runtimeProfile != OrtRuntimeProfile.external || artifact != null) {
        throw const FormatException(
          'A sherpa-owned Android build must use only the process runtime.',
        );
      }
      break;
    case OrtAndroidRuntimeOwner.application:
      if (runtimeProfile != OrtRuntimeProfile.bundled ||
          artifact == null ||
          artifact.targetOs != 'android') {
        throw const FormatException(
          'An application-owned Android build must name its bundled artifact.',
        );
      }
      break;
    case null:
      if (artifact?.targetOs == 'android') {
        throw const FormatException(
          'An Android artifact requires an explicit runtime owner.',
        );
      }
      break;
  }

  return OrtNativeBuildInfo._(
    schemaVersion: schemaVersion,
    nativeIdentity: nativeIdentity,
    shimAbiVersion: shimAbiVersion,
    requiredOrtApiVersion: requiredOrtApiVersion,
    runtimeProfile: runtimeProfile,
    androidRuntimeOwner: androidRuntimeOwner,
    allowedRuntimeSources: allowedRuntimeSources,
    buildId: buildId,
    artifact: artifact,
  );
}

OrtAndroidRuntimeOwner? _parseAndroidRuntimeOwner(Map<String, Object?> object) {
  if (object['androidRuntimeOwner'] == null) {
    return null;
  }
  return switch (_requiredString(
    object,
    'androidRuntimeOwner',
    maximumBytes: _maximumBuildIdBytes,
  )) {
    'sherpa' => OrtAndroidRuntimeOwner.sherpa,
    'application' => OrtAndroidRuntimeOwner.application,
    _ => throw const FormatException('Unknown Android runtime owner.'),
  };
}

List<OrtRuntimeSourceKind> _parseAllowedRuntimeSources(Object? value) {
  if (value is! List<Object?> || value.isEmpty || value.length > 4) {
    throw const FormatException(
      'Native allowed runtime sources must be a bounded non-empty array.',
    );
  }
  return List<OrtRuntimeSourceKind>.unmodifiable(
    value.map((Object? entry) {
      if (entry is! String ||
          entry.isEmpty ||
          utf8.encode(entry).length > _maximumBuildIdBytes) {
        throw const FormatException(
          'Native allowed runtime source is not a bounded string.',
        );
      }
      return _parseSource(entry);
    }),
  );
}

bool _sameRuntimeSources(
  List<OrtRuntimeSourceKind> actual,
  List<OrtRuntimeSourceKind> expected,
) {
  if (actual.length != expected.length) {
    return false;
  }
  for (var index = 0; index < actual.length; index += 1) {
    if (actual[index] != expected[index]) {
      return false;
    }
  }
  return true;
}

OrtNativeArtifactIdentity? _parseArtifactIdentity(Object? value) {
  if (value == null) {
    return null;
  }
  if (value is! Map<String, Object?>) {
    throw const FormatException(
      'Native artifact identity must be an object or null.',
    );
  }
  _requireExactKeys(value, const <String>{
    'id',
    'lockSha256',
    'sourceSha256',
    'targetOs',
    'targetArchitecture',
    'targetVariant',
    'minimumOs',
    'flavor',
    'runtimeMode',
    'thirdPartyNoticesSha256',
    'providers',
  });
  final id = _artifactToken(value, 'id');
  final lockSha256 = _artifactDigest(value, 'lockSha256');
  final sourceSha256 = _artifactDigest(value, 'sourceSha256');
  final targetOs = _artifactToken(value, 'targetOs');
  final targetArchitecture = _artifactToken(value, 'targetArchitecture');
  final targetVariant = _artifactToken(value, 'targetVariant');
  final minimumOs = _requiredString(
    value,
    'minimumOs',
    maximumBytes: _maximumArtifactValueBytes,
  );
  final flavor = _artifactToken(value, 'flavor');
  final runtimeMode = _parseProfile(_artifactToken(value, 'runtimeMode'));
  final thirdPartyNoticesSha256 = _artifactDigest(
    value,
    'thirdPartyNoticesSha256',
  );
  final providers = _parseArtifactProviders(value['providers']);
  if (!_artifactMinimumOs.hasMatch(minimumOs)) {
    throw const FormatException(
      'Native artifact minimum OS contains unsafe characters.',
    );
  }
  return OrtNativeArtifactIdentity._(
    id: id,
    lockSha256: lockSha256,
    sourceSha256: sourceSha256,
    targetOs: targetOs,
    targetArchitecture: targetArchitecture,
    targetVariant: targetVariant,
    minimumOs: minimumOs,
    flavor: flavor,
    runtimeMode: runtimeMode,
    thirdPartyNoticesSha256: thirdPartyNoticesSha256,
    providers: providers,
  );
}

Map<String, String?> _parseArtifactProviders(Object? value) {
  if (value is! List<Object?> || value.isEmpty || value.length > 64) {
    throw const FormatException(
      'Native artifact provider inventory must be a bounded non-empty array.',
    );
  }
  final result = <String, String?>{};
  for (var index = 0; index < value.length; index += 1) {
    final rawProvider = value[index];
    if (rawProvider is! Map<String, Object?>) {
      throw FormatException(
        'Native artifact provider inventory entry $index is not an object.',
      );
    }
    _requireExactKeys(rawProvider, const <String>{'wrapperId', 'reportedName'});
    final wrapperId = _artifactToken(rawProvider, 'wrapperId');
    final rawReportedName = rawProvider['reportedName'];
    final reportedName = rawReportedName == null
        ? null
        : _requiredString(
            rawProvider,
            'reportedName',
            maximumBytes: _maximumArtifactValueBytes,
          );
    if (result.containsKey(wrapperId)) {
      throw FormatException(
        'Native artifact provider inventory duplicates "$wrapperId".',
      );
    }
    result[wrapperId] = reportedName;
  }
  return Map<String, String?>.unmodifiable(result);
}

String _artifactToken(Map<String, Object?> object, String key) {
  final value = _requiredString(
    object,
    key,
    maximumBytes: _maximumArtifactValueBytes,
  );
  if (!_artifactTokenPattern.hasMatch(value)) {
    throw FormatException(
      'Native artifact identity field "$key" contains unsafe characters.',
    );
  }
  return value;
}

String _artifactDigest(Map<String, Object?> object, String key) {
  final value = _requiredString(object, key, maximumBytes: 64);
  if (!_sha256.hasMatch(value)) {
    throw FormatException(
      'Native artifact identity field "$key" is not lowercase SHA-256.',
    );
  }
  return value;
}

/// Parses and cross-checks a bounded runtime-info protocol payload.
///
/// This function is deliberately internal to the package public surface.
OrtRuntimeInfo parseOrtRuntimeInfo(
  String json, {
  required OrtNativeBuildInfo buildInfo,
  required OrtRuntimeSourceKind requestedSource,
  required OrtApiVersion requestedApi,
  required OrtLogSeverity requestedLogSeverity,
  required String requestedLogId,
}) {
  final Map<String, Object?> object = _parseObject(json, 'runtime info');
  _requireExactKeys(object, const <String>{
    'schemaVersion',
    'nativeIdentity',
    'shimAbiVersion',
    'shimBuildId',
    'requiredOrtApiVersion',
    'negotiatedOrtApiVersion',
    'runtimeVersion',
    'runtimeSource',
    'runtimeProfile',
    'runtimeLibraryIdentity',
    'logSeverity',
    'logId',
  });

  final int schemaVersion = _requiredInt(object, 'schemaVersion');
  final String nativeIdentity = _requiredString(
    object,
    'nativeIdentity',
    maximumBytes: _maximumIdentityBytes,
  );
  final int shimAbiVersion = _requiredInt(object, 'shimAbiVersion');
  final String shimBuildId = _requiredString(
    object,
    'shimBuildId',
    maximumBytes: _maximumBuildIdBytes,
  );
  final int requiredOrtApiVersion = _requiredInt(
    object,
    'requiredOrtApiVersion',
  );
  final int negotiatedOrtApiVersion = _requiredInt(
    object,
    'negotiatedOrtApiVersion',
  );
  final String runtimeVersion = _requiredString(
    object,
    'runtimeVersion',
    maximumBytes: _maximumRuntimeVersionBytes,
  );
  final OrtRuntimeSourceKind runtimeSource = _parseSource(
    _requiredString(
      object,
      'runtimeSource',
      maximumBytes: _maximumBuildIdBytes,
    ),
  );
  final OrtRuntimeProfile runtimeProfile = _parseProfile(
    _requiredString(
      object,
      'runtimeProfile',
      maximumBytes: _maximumBuildIdBytes,
    ),
  );
  final String runtimeLibraryIdentity = _requiredString(
    object,
    'runtimeLibraryIdentity',
    maximumBytes: _maximumIdentityBytes,
  );
  final OrtLogSeverity logSeverity = _parseLogSeverity(
    _requiredString(object, 'logSeverity', maximumBytes: _maximumBuildIdBytes),
  );
  final String logId = _requiredString(
    object,
    'logId',
    maximumBytes: _maximumLogIdBytes,
  );

  if (schemaVersion != 1 ||
      nativeIdentity != buildInfo.nativeIdentity ||
      shimAbiVersion != buildInfo.shimAbiVersion ||
      shimBuildId != buildInfo.buildId ||
      requiredOrtApiVersion != buildInfo.requiredOrtApiVersion ||
      requiredOrtApiVersion != requestedApi.value ||
      negotiatedOrtApiVersion != requestedApi.value ||
      runtimeSource != requestedSource ||
      runtimeProfile != buildInfo.runtimeProfile ||
      logSeverity != requestedLogSeverity ||
      logId != requestedLogId) {
    throw const FormatException(
      'Native runtime information does not match the requested configuration.',
    );
  }
  if (runtimeLibraryIdentity.contains('/') ||
      runtimeLibraryIdentity.contains(r'\')) {
    throw const FormatException(
      'Native runtime information exposed an unredacted library path.',
    );
  }
  if (runtimeVersion.contains('/') || runtimeVersion.contains(r'\')) {
    throw const FormatException(
      'Native runtime information contains an unsafe version string.',
    );
  }

  return OrtRuntimeInfo._(
    schemaVersion: schemaVersion,
    nativeIdentity: nativeIdentity,
    shimAbiVersion: shimAbiVersion,
    shimBuildId: shimBuildId,
    requiredOrtApiVersion: requiredOrtApiVersion,
    negotiatedOrtApiVersion: negotiatedOrtApiVersion,
    runtimeVersion: runtimeVersion,
    runtimeSource: runtimeSource,
    runtimeProfile: runtimeProfile,
    runtimeLibraryIdentity: runtimeLibraryIdentity,
    logSeverity: logSeverity,
    logId: logId,
  );
}

final RegExp _buildId = RegExp(r'^[A-Za-z0-9._-]+$');
final RegExp _artifactTokenPattern = RegExp(r'^[a-z0-9][a-z0-9._-]{0,127}$');
final RegExp _artifactMinimumOs = RegExp(r'^[a-z0-9][a-z0-9._-]{0,127}$');
final RegExp _sha256 = RegExp(r'^[0-9a-f]{64}$');

Map<String, Object?> _parseObject(String json, String label) {
  if (json.length > _maximumInfoJsonBytes ||
      utf8.encode(json).length > _maximumInfoJsonBytes) {
    throw FormatException('Native $label exceeds the Dart limit.');
  }
  validateStrictJsonInternal(json, label: 'Native $label');
  final Object? decoded;
  try {
    decoded = jsonDecode(json);
  } on FormatException {
    throw FormatException('Native $label is not valid JSON.');
  }
  if (decoded is! Map<String, Object?>) {
    throw FormatException('Native $label must be a JSON object.');
  }
  return decoded;
}

void _requireExactKeys(Map<String, Object?> object, Set<String> required) {
  final Set<String> actual = object.keys.toSet();
  if (actual.length != required.length || !actual.containsAll(required)) {
    throw const FormatException(
      'Native information contains missing or unknown fields.',
    );
  }
}

int _requiredInt(Map<String, Object?> object, String key) {
  final Object? value = object[key];
  if (value is! int || value < 0 || value > 0xffffffff) {
    throw FormatException('Native information field "$key" is not uint32.');
  }
  return value;
}

String _requiredString(
  Map<String, Object?> object,
  String key, {
  required int maximumBytes,
}) {
  final Object? value = object[key];
  if (value is! String ||
      value.isEmpty ||
      !hasWellFormedUtf16(value) ||
      utf8.encode(value).length > maximumBytes ||
      value.runes.any((int rune) => rune < 0x20 || rune == 0x7f)) {
    throw FormatException(
      'Native information field "$key" is not a bounded safe string.',
    );
  }
  return value;
}

OrtRuntimeProfile _parseProfile(String value) => switch (value) {
  'external' => OrtRuntimeProfile.external,
  'bundled' => OrtRuntimeProfile.bundled,
  'linked' => OrtRuntimeProfile.linked,
  _ => throw const FormatException('Unknown native runtime profile.'),
};

OrtRuntimeSourceKind _parseSource(String value) => switch (value) {
  'linked' => OrtRuntimeSourceKind.linked,
  'bundled' => OrtRuntimeSourceKind.bundled,
  'process' => OrtRuntimeSourceKind.process,
  'file' => OrtRuntimeSourceKind.file,
  _ => throw const FormatException('Unknown native runtime source.'),
};

OrtLogSeverity _parseLogSeverity(String value) => switch (value) {
  'verbose' => OrtLogSeverity.verbose,
  'info' => OrtLogSeverity.info,
  'warning' => OrtLogSeverity.warning,
  'error' => OrtLogSeverity.error,
  'fatal' => OrtLogSeverity.fatal,
  _ => throw const FormatException('Unknown native log severity.'),
};
