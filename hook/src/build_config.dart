import 'dart:io';

import 'package:code_assets/code_assets.dart';
import 'package:hooks/hooks.dart';
import 'package:native_toolchain_c/native_toolchain_c.dart';

const String fonixPackageName = 'fonix';
const String fonixShimName = 'fonix_shim';
const String fonixShimAssetId = 'package:fonix/fonix_shim';
const String fonixOrtRuntimeName = 'onnxruntime';
const String fonixOrtRuntimeAssetId = 'package:fonix/onnxruntime';
const String fonixOrtProviderSharedName = 'onnxruntime_providers_shared';
const String fonixOrtProviderSharedAssetId =
    'package:fonix/onnxruntime_providers_shared';
const String fonixRuntimeModeUserDefine = 'runtime_mode';
const String fonixAndroidRuntimeOwnerUserDefine = 'android_runtime_owner';
const String fonixArtifactCacheUserDefine = 'artifact_cache';
const String fonixArtifactMirrorUserDefine = 'artifact_mirror';
const String fonixApplicationMinimumOsUserDefine = 'application_minimum_os';
const String fonixExternalRuntimeMode = 'external';
const String fonixBundledRuntimeMode = 'bundled';
const String fonixLinkedRuntimeMode = 'linked';
const String fonixAndroidSherpaRuntimeOwner = 'sherpa';
const String fonixAndroidApplicationRuntimeOwner = 'application';
const int fonixRequiredOrtApiVersion = 27;
const int fonixAndroidLockedMinimumNdkApi = 24;
const String fonixIosLockedMinimumOs = '15.1';
const String fonixMacosLockedMinimumOs = '14.0';
const String fonixElfExportMapPath = 'src/fonix_exports.map';
const String fonixElfExportVersion = 'FONIX_DORT_1.0';

const List<String> fonixRequiredNativeInputPaths = <String>[
  'src/dort.h',
  fonixElfExportMapPath,
  'src/fonix_exports.apple',
  'third_party/onnxruntime/include/onnxruntime_c_api.h',
  'third_party/onnxruntime/include/onnxruntime_ep_c_api.h',
];

const Set<FonixRuntimeSource> fonixExternalRuntimeSources =
    <FonixRuntimeSource>{FonixRuntimeSource.process, FonixRuntimeSource.file};
const Set<FonixRuntimeSource> fonixBundledRuntimeSources = <FonixRuntimeSource>{
  FonixRuntimeSource.bundled,
};
const Set<FonixRuntimeSource> fonixLinkedRuntimeSources = <FonixRuntimeSource>{
  FonixRuntimeSource.linked,
};

enum FonixRuntimeSource { linked, bundled, process, file }

enum FonixAndroidRuntimeOwner {
  sherpa(fonixAndroidSherpaRuntimeOwner),
  application(fonixAndroidApplicationRuntimeOwner);

  const FonixAndroidRuntimeOwner(this.configurationName);

  final String configurationName;
}

const Set<String> fonixKnownUserDefineKeys = <String>{
  fonixRuntimeModeUserDefine,
  fonixAndroidRuntimeOwnerUserDefine,
  fonixArtifactCacheUserDefine,
  fonixArtifactMirrorUserDefine,
  fonixApplicationMinimumOsUserDefine,
};

final class FonixBuildOptions {
  const FonixBuildOptions({
    required this.targetOS,
    required this.targetArchitecture,
    required this.targetVariant,
    required this.runtimeMode,
    required this.androidRuntimeOwner,
    required this.applicationMinimumOs,
    required this.targetAndroidNdkApi,
  });

  final OS targetOS;
  final Architecture targetArchitecture;
  final String targetVariant;
  final String runtimeMode;
  final FonixAndroidRuntimeOwner? androidRuntimeOwner;
  final String? applicationMinimumOs;
  final int? targetAndroidNdkApi;

  Set<FonixRuntimeSource> get runtimeSources {
    if (androidRuntimeOwner == FonixAndroidRuntimeOwner.sherpa) {
      return const <FonixRuntimeSource>{FonixRuntimeSource.process};
    }
    return switch (runtimeMode) {
      fonixExternalRuntimeMode => fonixExternalRuntimeSources,
      fonixBundledRuntimeMode => fonixBundledRuntimeSources,
      fonixLinkedRuntimeMode => fonixLinkedRuntimeSources,
      _ => throw StateError('Unsupported resolved runtime mode: $runtimeMode'),
    };
  }

  bool get linksOnnxRuntime => runtimeMode == fonixLinkedRuntimeMode;
}

FonixBuildOptions resolveFonixBuildOptions({
  required OS targetOS,
  required Architecture targetArchitecture,
  IOSSdk? targetIOSSdk,
  int? targetIOSVersion,
  int? targetAndroidNdkApi,
  Object? runtimeMode,
  Object? androidRuntimeOwner,
  Object? applicationMinimumOs,
}) {
  if (runtimeMode != null && runtimeMode is! String) {
    throw BuildError(
      message:
          'hooks.user_defines.$fonixPackageName.'
          '$fonixRuntimeModeUserDefine must be a string.',
    );
  }

  final normalizedAndroidRuntimeOwner = _androidRuntimeOwner(
    targetOS: targetOS,
    value: androidRuntimeOwner,
  );
  final defaultMode = switch (normalizedAndroidRuntimeOwner) {
    FonixAndroidRuntimeOwner.sherpa => fonixExternalRuntimeMode,
    FonixAndroidRuntimeOwner.application => fonixBundledRuntimeMode,
    null =>
      targetOS == OS.iOS ? fonixLinkedRuntimeMode : fonixExternalRuntimeMode,
  };
  final normalizedMode = (runtimeMode as String? ?? defaultMode)
      .trim()
      .toLowerCase();
  _validateAndroidRuntimeOwnership(
    targetOS: targetOS,
    runtimeMode: normalizedMode,
    owner: normalizedAndroidRuntimeOwner,
  );
  switch (normalizedMode) {
    case fonixExternalRuntimeMode:
      _validateExternalTarget(targetOS, targetArchitecture);
    case fonixBundledRuntimeMode:
      _validateBundledTarget(targetOS, targetArchitecture);
    case fonixLinkedRuntimeMode:
      _validateLinkedTarget(
        targetOS,
        targetArchitecture,
        targetIOSSdk,
        targetIOSVersion,
      );
    default:
      throw BuildError(
        message:
            'Unsupported Fonix runtime mode "$normalizedMode". '
            'Implemented modes are "$fonixExternalRuntimeMode", '
            '"$fonixBundledRuntimeMode", and "$fonixLinkedRuntimeMode" on '
            'their locked targets.',
      );
  }
  final normalizedApplicationMinimumOs = _applicationMinimumOs(
    targetOS: targetOS,
    runtimeMode: normalizedMode,
    value: applicationMinimumOs,
  );
  _validateAndroidNdkApi(
    targetOS: targetOS,
    targetAndroidNdkApi: targetAndroidNdkApi,
  );

  return FonixBuildOptions(
    targetOS: targetOS,
    targetArchitecture: targetArchitecture,
    targetVariant: _targetVariant(targetOS, targetIOSSdk),
    runtimeMode: normalizedMode,
    androidRuntimeOwner: normalizedAndroidRuntimeOwner,
    applicationMinimumOs: normalizedApplicationMinimumOs,
    targetAndroidNdkApi: targetAndroidNdkApi,
  );
}

void _validateAndroidNdkApi({
  required OS targetOS,
  required int? targetAndroidNdkApi,
}) {
  if (targetOS != OS.android) {
    return;
  }
  if (targetAndroidNdkApi == null ||
      targetAndroidNdkApi < fonixAndroidLockedMinimumNdkApi) {
    throw BuildError(
      message:
          'Fonix Android runtime ownership requires native-assets '
          'target_ndk_api at least $fonixAndroidLockedMinimumNdkApi; received '
          '${targetAndroidNdkApi ?? 'no value'}. Raise the consuming '
          'application minSdk to the locked artifact floor.',
    );
  }
}

/// Rejects hook configuration keys that this package does not own.
///
/// Build-hook user defines are a package-scoped protocol. Silently ignoring a
/// misspelled ownership or artifact key could select a different native
/// composition than the application intended.
void validateFonixUserDefineKeys(Iterable<String> keys) {
  final unknown =
      keys
          .where((key) => !fonixKnownUserDefineKeys.contains(key))
          .toSet()
          .toList(growable: false)
        ..sort();
  if (unknown.isNotEmpty) {
    throw BuildError(
      message:
          'Unsupported hooks.user_defines.$fonixPackageName fields: '
          '${unknown.join(', ')}. Accepted fields are '
          '${(fonixKnownUserDefineKeys.toList()..sort()).join(', ')}.',
    );
  }
}

FonixAndroidRuntimeOwner? _androidRuntimeOwner({
  required OS targetOS,
  required Object? value,
}) {
  if (targetOS != OS.android) {
    return null;
  }
  if (value == null) {
    throw BuildError(
      message:
          'Fonix Android builds require an explicit '
          'hooks.user_defines.$fonixPackageName.'
          '$fonixAndroidRuntimeOwnerUserDefine value: '
          '"$fonixAndroidSherpaRuntimeOwner" for a shim-only process runtime '
          'or "$fonixAndroidApplicationRuntimeOwner" for the exact '
          'lock-selected bundled runtime.',
    );
  }
  if (value is! String) {
    throw BuildError(
      message:
          'hooks.user_defines.$fonixPackageName.'
          '$fonixAndroidRuntimeOwnerUserDefine must be a string.',
    );
  }
  return switch (value.trim().toLowerCase()) {
    fonixAndroidSherpaRuntimeOwner => FonixAndroidRuntimeOwner.sherpa,
    fonixAndroidApplicationRuntimeOwner => FonixAndroidRuntimeOwner.application,
    final unsupported => throw BuildError(
      message:
          'Unsupported Fonix Android runtime owner "$unsupported". '
          'Implemented owners are "$fonixAndroidSherpaRuntimeOwner" and '
          '"$fonixAndroidApplicationRuntimeOwner".',
    ),
  };
}

void _validateAndroidRuntimeOwnership({
  required OS targetOS,
  required String runtimeMode,
  required FonixAndroidRuntimeOwner? owner,
}) {
  if (targetOS != OS.android) {
    return;
  }
  final androidOwner = owner;
  if (androidOwner == null) {
    throw StateError('Android owner validation did not run.');
  }
  final requiredMode = switch (androidOwner) {
    FonixAndroidRuntimeOwner.sherpa => fonixExternalRuntimeMode,
    FonixAndroidRuntimeOwner.application => fonixBundledRuntimeMode,
  };
  if (runtimeMode != requiredMode) {
    throw BuildError(
      message:
          'Fonix Android owner "${androidOwner.configurationName}" requires '
          'runtime_mode "$requiredMode", but received "$runtimeMode". '
          'Ownership never falls back to another runtime source.',
    );
  }
}

List<String> discoverFonixNativeSources(Uri packageRoot) {
  final sourceDirectory = Directory.fromUri(packageRoot.resolve('src/'));
  if (!sourceDirectory.existsSync()) {
    throw BuildError(message: 'Fonix native source directory is missing: src/');
  }

  final sources = <String>[];
  for (final entity in sourceDirectory.listSync(
    recursive: false,
    followLinks: false,
  )) {
    if (FileSystemEntity.typeSync(entity.path, followLinks: false) !=
        FileSystemEntityType.file) {
      continue;
    }
    final pathSegments = entity.uri.pathSegments
        .where((segment) => segment.isNotEmpty)
        .toList(growable: false);
    final fileName = pathSegments.last;
    if (fileName.endsWith('.c')) {
      sources.add('src/$fileName');
    }
  }
  sources.sort();

  if (sources.isEmpty) {
    throw BuildError(
      message:
          'Fonix native source directory contains no regular src/*.c files.',
    );
  }
  return List<String>.unmodifiable(sources);
}

List<Uri> validateFonixNativeInputs(Uri packageRoot, List<String> sources) {
  final relativePaths = <String>{
    ...sources,
    ...fonixRequiredNativeInputPaths,
  }.toList(growable: false)..sort();
  final dependencies = <Uri>[];
  for (final relativePath in relativePaths) {
    final uri = packageRoot.resolve(relativePath);
    if (FileSystemEntity.typeSync(uri.toFilePath(), followLinks: false) !=
        FileSystemEntityType.file) {
      throw BuildError(
        message:
            'Required regular Fonix native input is missing: $relativePath',
      );
    }
    dependencies.add(uri);
  }
  final ortApiVersion = readFonixOrtApiVersion(packageRoot);
  if (ortApiVersion != fonixRequiredOrtApiVersion) {
    throw BuildError(
      message:
          'Fonix shim ABI 1 requires exact ONNX Runtime C API '
          '$fonixRequiredOrtApiVersion headers, but the vendored header '
          'declares API $ortApiVersion.',
    );
  }
  return List<Uri>.unmodifiable(dependencies);
}

/// Returns the closed ELF export-version contract for Linux shim links.
///
/// Android retains its existing native package policy. Its final-package
/// auditor owns that platform's export checks independently, while Linux uses
/// the GNU version script as a link input for every external or bundled shim.
List<String> fonixLinuxVersionScriptFlags(OS targetOS, Uri packageRoot) {
  if (targetOS != OS.linux) {
    return const <String>[];
  }
  final exportMap = packageRoot.resolve(fonixElfExportMapPath);
  if (!exportMap.isScheme('file')) {
    throw BuildError(
      message:
          'The Fonix package root must resolve Linux linker inputs from '
          'a local filesystem URI.',
    );
  }
  return <String>['-Xlinker', '--version-script=${exportMap.toFilePath()}'];
}

int readFonixOrtApiVersion(Uri packageRoot) {
  final header = File.fromUri(
    packageRoot.resolve('third_party/onnxruntime/include/onnxruntime_c_api.h'),
  );
  final match = RegExp(
    r'^\s*#\s*define\s+ORT_API_VERSION\s+([0-9]+)\s*$',
    multiLine: true,
  ).firstMatch(header.readAsStringSync());
  if (match == null) {
    throw BuildError(
      message: 'Could not read ORT_API_VERSION from the vendored header.',
    );
  }
  return int.parse(match.group(1)!);
}

CBuilder createFonixShimBuilder(
  FonixBuildOptions options,
  List<String> sources, {
  List<String> frameworks = const <String>['Foundation'],
  List<String> additionalLibraries = const <String>[],
  List<String> libraryDirectories = const <String>['.'],
  List<String> additionalFlags = const <String>[],
  Map<String, String?> artifactDefines = const <String, String?>{},
  Language language = Language.c,
}) => CBuilder.library(
  name: fonixShimName,
  packageName: fonixPackageName,
  assetName: fonixShimName,
  sources: List<String>.unmodifiable(sources),
  includes: const <String>['src', 'third_party/onnxruntime/include'],
  frameworks: frameworks,
  libraries: <String>[
    ...fonixSystemLibraries(options.targetOS),
    ...additionalLibraries,
  ],
  libraryDirectories: libraryDirectories,
  flags: <String>[...fonixCompilerFlags(options.targetOS), ...additionalFlags],
  defines: <String, String?>{
    'FONIX_SHIM_BUILDING': '1',
    'FONIX_RUNTIME_PROFILE_${options.runtimeMode.toUpperCase()}': '1',
    'FONIX_RUNTIME_PROFILE': '"${options.runtimeMode}"',
    'FONIX_SHIM_BUILD_ID': '"development"',
    ...fonixPlatformDefines(options.targetOS),
    ...fonixAndroidOwnershipDefines(options),
    ...artifactDefines,
  },
  std: 'c11',
  language: language,
  linkModePreference: LinkModePreference.dynamic,
  optimizationLevel: OptimizationLevel.o2,
  buildMode: BuildMode.release,
);

/// Compile-time ownership identity included in the shim build manifest.
///
/// [artifactId] is supplied only after an application-owned runtime has been
/// selected and verified against `native/versions.lock.yaml`. The build ID is
/// deliberately machine-readable for diagnostics; schema 3 carries ownership
/// and the exact allowed runtime-source set as independent typed fields.
Map<String, String?> fonixAndroidOwnershipDefines(
  FonixBuildOptions options, {
  String? artifactId,
}) {
  final owner = options.androidRuntimeOwner;
  if (owner == null) {
    if (artifactId != null) {
      throw StateError(
        'An Android artifact ID was supplied for a non-Android build.',
      );
    }
    return const <String, String?>{};
  }
  final source = switch (owner) {
    FonixAndroidRuntimeOwner.sherpa => 'process',
    FonixAndroidRuntimeOwner.application => 'bundled',
  };
  if (owner == FonixAndroidRuntimeOwner.sherpa && artifactId != null) {
    throw StateError(
      'A sherpa-owned Android shim cannot name a bundled artifact.',
    );
  }
  if (artifactId != null &&
      !RegExp(r'^[a-z0-9][a-z0-9._-]{0,127}$').hasMatch(artifactId)) {
    throw StateError('The Android artifact ID is not a closed identity token.');
  }
  if (owner == FonixAndroidRuntimeOwner.application && artifactId == null) {
    // The builder-level contract can be inspected before staging. The real
    // hook replaces this value after the exact lock artifact is selected.
    artifactId = 'pending-lock-artifact';
  }
  final artifactSuffix = artifactId == null ? '' : '-artifact-$artifactId';
  final buildId =
      'android-owner-${owner.configurationName}-source-$source'
      '$artifactSuffix';
  if (buildId.length > 128) {
    throw StateError(
      'The Android ownership build identity exceeds its ABI limit.',
    );
  }
  return <String, String?>{
    'FONIX_ANDROID_RUNTIME_OWNER': '"${owner.configurationName}"',
    'FONIX_ANDROID_ALLOWED_RUNTIME_SOURCE': '"$source"',
    'FONIX_ANDROID_RUNTIME_OWNER_${owner.name.toUpperCase()}': '1',
    'FONIX_SHIM_BUILD_ID': '"$buildId"',
  };
}

Map<String, String?> fonixPlatformDefines(OS targetOS) {
  if (targetOS == OS.macOS || targetOS == OS.iOS) {
    return const <String, String?>{'_DARWIN_C_SOURCE': '1'};
  }
  if (targetOS == OS.android || targetOS == OS.linux) {
    return const <String, String?>{'_GNU_SOURCE': '1'};
  }
  return const <String, String?>{};
}

List<String> fonixSystemLibraries(OS targetOS) {
  if (targetOS == OS.android || targetOS == OS.linux) {
    return const <String>['dl'];
  }
  if (targetOS == OS.windows) {
    return const <String>['bcrypt', 'advapi32'];
  }
  return const <String>[];
}

List<String> fonixCompilerFlags(OS targetOS) {
  if (targetOS == OS.windows) {
    return const <String>['/W4', '/WX', '/utf-8'];
  }

  return <String>[
    '-Wall',
    '-Wextra',
    '-Wpedantic',
    '-Wconversion',
    '-Wshadow',
    '-Wstrict-prototypes',
    '-Wmissing-prototypes',
    '-Werror',
    '-fvisibility=hidden',
    '-fstack-protector-strong',
    if (targetOS == OS.android || targetOS == OS.linux) '-pthread',
    if (targetOS == OS.android || targetOS == OS.linux) '-Wl,--no-undefined',
    if (targetOS == OS.android || targetOS == OS.linux) '-Wl,-z,relro',
    if (targetOS == OS.android || targetOS == OS.linux) '-Wl,-z,now',
    if (targetOS == OS.android || targetOS == OS.linux) '-Wl,-z,noexecstack',
    if (targetOS == OS.linux) '-fuse-ld=lld',
    if (targetOS == OS.linux) '-Wl,--build-id=sha1',
    if (targetOS == OS.android || targetOS == OS.linux)
      '-Wl,-soname,libfonix_shim.so',
    if (targetOS == OS.android) '-Wl,-z,max-page-size=16384',
  ];
}

/// Compiler floors owned by the exact pinned Apple runtime artifacts.
///
/// Flutter currently supplies fixed integer native-assets target versions
/// rather than the consuming application's deployment target. These flags
/// make the shim compatible with the locked runtime bytes; a separate final
/// application audit must prove that the application declares at least the
/// same floor.
List<String> fonixPinnedAppleDeploymentFlags(
  FonixBuildOptions options, {
  required String lockedMinimumOs,
}) {
  if (options.targetOS != OS.iOS && options.targetOS != OS.macOS) {
    return const <String>[];
  }
  validateFonixApplicationMinimumOs(options, lockedMinimumOs);
  if (options.targetOS == OS.iOS && options.linksOnnxRuntime) {
    return <String>['-mios-version-min=$lockedMinimumOs'];
  }
  if (options.targetOS == OS.macOS &&
      options.runtimeMode == fonixBundledRuntimeMode) {
    return <String>['-mmacos-version-min=$lockedMinimumOs'];
  }
  return const <String>[];
}

void validateFonixApplicationMinimumOs(
  FonixBuildOptions options,
  String lockedMinimumOs,
) {
  final declared = options.applicationMinimumOs;
  if (declared == null) {
    return;
  }
  final locked = _parseDecimalVersion(
    lockedMinimumOs,
    label: 'selected lock minimum OS',
  );
  final application = _parseDecimalVersion(
    declared,
    label:
        'hooks.user_defines.$fonixPackageName.'
        '$fonixApplicationMinimumOsUserDefine',
  );
  if (_compareVersion(application, locked) < 0) {
    throw BuildError(
      message:
          'Fonix $fonixApplicationMinimumOsUserDefine $declared is below '
          'the selected locked artifact minimum $lockedMinimumOs for '
          '${options.targetOS.name}/${options.targetArchitecture.name}. '
          'Raise the consuming application deployment target and keep this '
          'declaration in sync; Flutter native-assets targetVersion is not '
          'accepted as the application floor.',
    );
  }
}

String? _applicationMinimumOs({
  required OS targetOS,
  required String runtimeMode,
  required Object? value,
}) {
  final required =
      (targetOS == OS.iOS && runtimeMode == fonixLinkedRuntimeMode) ||
      (targetOS == OS.macOS && runtimeMode == fonixBundledRuntimeMode);
  if (!required) {
    return null;
  }
  if (value == null) {
    throw BuildError(
      message:
          'Fonix $runtimeMode mode on ${targetOS.name} requires '
          'hooks.user_defines.$fonixPackageName.'
          '$fonixApplicationMinimumOsUserDefine. Declare the consuming '
          'application deployment target explicitly; native-assets '
          'targetVersion is compiler metadata only.',
    );
  }
  if (value is! String) {
    throw BuildError(
      message:
          'hooks.user_defines.$fonixPackageName.'
          '$fonixApplicationMinimumOsUserDefine must be a decimal version '
          'string.',
    );
  }
  _parseDecimalVersion(
    value,
    label:
        'hooks.user_defines.$fonixPackageName.'
        '$fonixApplicationMinimumOsUserDefine',
  );
  return value;
}

List<int> _parseDecimalVersion(String value, {required String label}) {
  final match = RegExp(
    r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$',
  ).firstMatch(value);
  if (match == null) {
    throw BuildError(
      message:
          '$label must use strict major.minor or major.minor.patch decimal '
          'syntax without whitespace or leading zeroes.',
    );
  }
  return <int>[
    int.parse(match.group(1)!),
    int.parse(match.group(2)!),
    int.parse(match.group(3) ?? '0'),
  ];
}

int _compareVersion(List<int> left, List<int> right) {
  for (var index = 0; index < 3; index++) {
    final comparison = left[index].compareTo(right[index]);
    if (comparison != 0) {
      return comparison;
    }
  }
  return 0;
}

void _validateExternalTarget(OS targetOS, Architecture targetArchitecture) {
  if (targetOS == OS.iOS) {
    throw BuildError(
      message:
          'Fonix external runtime mode is unsupported on iOS. Select the '
          'pinned linked runtime mode.',
    );
  }

  final supportedOS =
      targetOS == OS.macOS ||
      targetOS == OS.linux ||
      targetOS == OS.windows ||
      targetOS == OS.android;
  if (!supportedOS) {
    throw BuildError(
      message:
          'Fonix external runtime mode does not support target OS '
          '"${targetOS.name}".',
    );
  }

  final supportedArchitecture =
      targetArchitecture == Architecture.arm64 ||
      targetArchitecture == Architecture.x64;
  if (!supportedArchitecture) {
    throw BuildError(
      message:
          'Fonix does not support ${targetOS.name}/'
          '${targetArchitecture.name} in the external runtime profile. '
          'Supported architectures are arm64 and x64.',
    );
  }
}

void _validateBundledTarget(OS targetOS, Architecture targetArchitecture) {
  final supported =
      (targetOS == OS.macOS && targetArchitecture == Architecture.arm64) ||
      (targetOS == OS.linux &&
          (targetArchitecture == Architecture.x64 ||
              targetArchitecture == Architecture.arm64)) ||
      (targetOS == OS.windows && targetArchitecture == Architecture.x64) ||
      (targetOS == OS.android &&
          (targetArchitecture == Architecture.arm64 ||
              targetArchitecture == Architecture.x64));
  if (supported) {
    return;
  }
  throw BuildError(
    message:
        'Fonix bundled runtime mode has no baseline lock tuple for '
        '${targetOS.name}/${targetArchitecture.name}.',
  );
}

void _validateLinkedTarget(
  OS targetOS,
  Architecture targetArchitecture,
  IOSSdk? targetIOSSdk,
  int? targetIOSVersion,
) {
  if (targetOS != OS.iOS) {
    throw BuildError(
      message:
          'Fonix linked runtime mode is pinned only for iOS arm64 device, '
          'arm64 simulator, and x64 simulator targets. Mac Catalyst is not '
          'supported.',
    );
  }
  if (targetIOSSdk == null) {
    throw BuildError(
      message:
          'Fonix iOS linked mode requires an explicit device or simulator SDK.',
    );
  }
  final supportedArchitecture =
      targetArchitecture == Architecture.arm64 ||
      (targetArchitecture == Architecture.x64 &&
          targetIOSSdk == IOSSdk.iPhoneSimulator);
  if (!supportedArchitecture) {
    throw BuildError(
      message:
          'Fonix linked runtime mode is pinned only for iOS arm64 device, '
          'arm64 simulator, and x64 simulator targets. iOS x64 device and '
          'Mac Catalyst targets are not supported.',
    );
  }
  if (targetIOSVersion == null || targetIOSVersion <= 0) {
    throw BuildError(
      message:
          'Fonix iOS linked mode requires native-assets target-version '
          'metadata. This tool value is not accepted as the application '
          'deployment floor; the final application must be audited against '
          'the locked ORT minimum ($fonixIosLockedMinimumOs).',
    );
  }
}

String _targetVariant(OS targetOS, IOSSdk? targetIOSSdk) {
  if (targetOS != OS.iOS) {
    return 'default';
  }
  return targetIOSSdk == IOSSdk.iPhoneSimulator ? 'simulator' : 'device';
}
