import 'dart:convert';
import 'dart:io';

import 'package:code_assets/code_assets.dart';
import 'package:hooks/hooks.dart';
import 'package:native_toolchain_c/native_toolchain_c.dart';
import 'package:path/path.dart' as path;

import 'src/build_config.dart';
import 'src/native_artifact_auditor.dart';
import 'src/native_artifact_resolver.dart';

Future<void> main(List<String> arguments) async {
  await build(arguments, buildFonixShim);
}

Future<void> buildFonixShim(BuildInput input, BuildOutputBuilder output) async {
  if (!input.config.buildCodeAssets) {
    return;
  }

  final configuredUserDefines = _configuredUserDefines(input);
  validateFonixUserDefineKeys(configuredUserDefines.keys);
  final code = input.config.code;
  final options = resolveFonixBuildOptions(
    targetOS: code.targetOS,
    targetArchitecture: code.targetArchitecture,
    targetIOSSdk: code.targetOS == OS.iOS ? code.iOS.targetSdk : null,
    targetIOSVersion: code.targetOS == OS.iOS ? code.iOS.targetVersion : null,
    targetAndroidNdkApi: code.targetOS == OS.android
        ? code.android.targetNdkApi
        : null,
    runtimeMode: configuredUserDefines[fonixRuntimeModeUserDefine],
    androidRuntimeOwner:
        configuredUserDefines[fonixAndroidRuntimeOwnerUserDefine],
    applicationMinimumOs:
        configuredUserDefines[fonixApplicationMinimumOsUserDefine],
  );
  validateFonixAndroidOwnershipUserDefines(
    options,
    configuredUserDefines: configuredUserDefines,
  );
  final sources = discoverFonixNativeSources(input.packageRoot);
  final dependencies = validateFonixNativeInputs(input.packageRoot, sources);
  output.dependencies.addAll(dependencies);

  final stagedRuntime = await stageFonixNativeRuntime(
    input: input,
    output: output,
    options: options,
  );
  final builder = _createConfiguredShimBuilder(
    input: input,
    options: options,
    sources: sources,
    stagedRuntime: stagedRuntime,
  );
  await builder.run(input: input, output: output);
  if (stagedRuntime != null && !options.linksOnnxRuntime) {
    _emitBundledRuntimeAssets(
      output: output,
      options: options,
      stagedRuntime: stagedRuntime,
    );
  }
}

/// Resolves the exact runtime artifact required by a non-external build.
Future<StagedNativeArtifact?> stageFonixNativeRuntime({
  required BuildInput input,
  required BuildOutputBuilder output,
  required FonixBuildOptions options,
}) async {
  if (options.runtimeMode == fonixExternalRuntimeMode) {
    return null;
  }

  final cacheDirectory = _configuredDirectory(
    input,
    fonixArtifactCacheUserDefine,
  );
  final mirrorDirectory = _configuredDirectory(
    input,
    fonixArtifactMirrorUserDefine,
  );
  if (cacheDirectory == null && mirrorDirectory == null) {
    throw BuildError(
      message:
          'Fonix bundled runtime mode is offline-only. Configure '
          'hooks.user_defines.fonix.$fonixArtifactCacheUserDefine or '
          'hooks.user_defines.fonix.$fonixArtifactMirrorUserDefine with a '
          'directory containing the exact lock-pinned upstream archive.',
    );
  }

  final stagingDirectory = Directory.fromUri(
    input.outputDirectory.resolve(
      'onnxruntime-${_targetOsName(options)}-'
      '${_targetArchitectureName(options)}-${options.targetVariant}/',
    ),
  );
  _resetOwnedStagingDirectory(stagingDirectory);

  final artifactTarget = NativeArtifactTarget(
    operatingSystem: _targetOsName(options),
    architecture: _targetArchitectureName(options),
    variant: options.targetVariant,
  );
  final StagedNativeArtifact staged;
  try {
    staged = await const NativeArtifactResolver().stage(
      packageRoot: Directory.fromUri(input.packageRoot),
      stagingDirectory: stagingDirectory,
      target: artifactTarget,
      cacheDirectory: cacheDirectory,
      mirrorDirectory: mirrorDirectory,
    );
  } on NativeArtifactResolutionException catch (error) {
    throw BuildError(message: error.message);
  }
  if (options.targetOS == OS.android ||
      options.targetOS == OS.linux ||
      options.targetOS == OS.windows) {
    try {
      auditStagedNativeArtifact(target: artifactTarget, staged: staged);
    } on NativeArtifactAuditException catch (error) {
      throw BuildError(message: error.message);
    }
  }

  output.dependencies.addAll(<Uri>[
    input.packageRoot.resolve('native/versions.lock.yaml'),
    staged.archiveFile.uri,
    if (cacheDirectory != null) cacheDirectory.uri,
    if (mirrorDirectory != null) mirrorDirectory.uri,
  ]);
  return staged;
}

CBuilder _createConfiguredShimBuilder({
  required BuildInput input,
  required FonixBuildOptions options,
  required List<String> sources,
  required StagedNativeArtifact? stagedRuntime,
}) {
  final appleExportList = input.packageRoot.resolve('src/fonix_exports.apple');
  final lockedMinimumOs = stagedRuntime?.identity.minimumOs;
  _validateStagedAndroidOwnership(options, stagedRuntime);
  final artifactDefines = <String, String?>{
    ..._artifactIdentityDefines(stagedRuntime),
    ...fonixAndroidOwnershipDefines(
      options,
      artifactId: options.targetOS == OS.android
          ? stagedRuntime?.identity.artifactId
          : null,
    ),
  };
  if (!options.linksOnnxRuntime) {
    return createFonixShimBuilder(
      options,
      sources,
      artifactDefines: artifactDefines,
      additionalFlags: <String>[
        if (lockedMinimumOs != null)
          ...fonixPinnedAppleDeploymentFlags(
            options,
            lockedMinimumOs: lockedMinimumOs,
          ),
        if (options.targetOS == OS.macOS)
          '-Wl,-exported_symbols_list,${appleExportList.toFilePath()}',
      ],
    );
  }
  if (options.targetOS != OS.iOS || stagedRuntime == null) {
    throw BuildError(
      message: 'The linked runtime profile is missing its pinned iOS artifact.',
    );
  }
  final frameworkDirectory = stagedRuntime.manifestFile.parent;
  final frameworkBinary = File(
    path.join(frameworkDirectory.path, 'onnxruntime.framework', 'onnxruntime'),
  );
  final frameworkInfo = File(
    path.join(frameworkDirectory.path, 'onnxruntime.framework', 'Info.plist'),
  );
  if (FileSystemEntity.typeSync(frameworkBinary.path, followLinks: false) !=
          FileSystemEntityType.file ||
      FileSystemEntity.typeSync(frameworkInfo.path, followLinks: false) !=
          FileSystemEntityType.file) {
    throw BuildError(
      message:
          'The verified iOS artifact did not stage a complete static '
          'onnxruntime.framework.',
    );
  }
  return createFonixShimBuilder(
    options,
    sources,
    artifactDefines: artifactDefines,
    frameworks: const <String>['Foundation', 'CoreML', 'onnxruntime'],
    additionalLibraries: const <String>['c++'],
    additionalFlags: <String>[
      ...fonixPinnedAppleDeploymentFlags(
        options,
        lockedMinimumOs: stagedRuntime.identity.minimumOs,
      ),
      '-F${frameworkDirectory.path}',
      '-Wl,-exported_symbols_list,${appleExportList.toFilePath()}',
    ],
    language: Language.objectiveC,
  );
}

Map<String, Object?> _configuredUserDefines(BuildInput input) {
  final userDefines = input.json['user_defines'];
  if (userDefines == null) {
    return const <String, Object?>{};
  }
  if (userDefines is! Map<String, Object?>) {
    throw BuildError(
      message: 'The build-hook user-defines envelope is invalid.',
    );
  }
  final workspace = userDefines['workspace_pubspec'];
  if (workspace == null) {
    return const <String, Object?>{};
  }
  if (workspace is! Map<String, Object?>) {
    throw BuildError(
      message: 'The build-hook workspace user-defines envelope is invalid.',
    );
  }
  final defines = workspace['defines'];
  if (defines is! Map<String, Object?>) {
    throw BuildError(
      message: 'The build-hook user-defines field set is invalid.',
    );
  }
  return Map<String, Object?>.unmodifiable(defines);
}

void _validateStagedAndroidOwnership(
  FonixBuildOptions options,
  StagedNativeArtifact? stagedRuntime,
) {
  if (options.targetOS != OS.android) {
    return;
  }
  switch (options.androidRuntimeOwner) {
    case FonixAndroidRuntimeOwner.sherpa:
      if (stagedRuntime != null) {
        throw BuildError(
          message: 'A sherpa-owned Android build unexpectedly staged ORT.',
        );
      }
    case FonixAndroidRuntimeOwner.application:
      if (stagedRuntime == null) {
        throw BuildError(
          message:
              'An application-owned Android build is missing its exact '
              'lock-selected ORT artifact.',
        );
      }
    case null:
      throw BuildError(message: 'The Android runtime owner is unresolved.');
  }
}

Map<String, String?> _artifactIdentityDefines(
  StagedNativeArtifact? stagedRuntime,
) {
  if (stagedRuntime == null) {
    return const <String, String?>{};
  }
  final identity = stagedRuntime.identity;
  final providersJson = jsonEncode(<Object?>[
    for (final entry in identity.providers.entries)
      <String, Object?>{'wrapperId': entry.key, 'reportedName': entry.value},
  ]);
  return <String, String?>{
    'FONIX_SHIM_BUILD_ID': '"${identity.artifactId}"',
    'FONIX_ARTIFACT_ID': '"${identity.artifactId}"',
    'FONIX_ARTIFACT_LOCK_SHA256': '"${identity.lockSha256}"',
    'FONIX_ARTIFACT_SOURCE_SHA256': '"${identity.sourceSha256}"',
    'FONIX_ARTIFACT_TARGET_OS': '"${identity.operatingSystem}"',
    'FONIX_ARTIFACT_TARGET_ARCHITECTURE': '"${identity.architecture}"',
    'FONIX_ARTIFACT_TARGET_VARIANT': '"${identity.variant}"',
    'FONIX_ARTIFACT_MINIMUM_OS': '"${identity.minimumOs}"',
    'FONIX_ARTIFACT_FLAVOR': '"${identity.flavor}"',
    'FONIX_ARTIFACT_RUNTIME_MODE': '"${identity.runtimeMode}"',
    'FONIX_ARTIFACT_THIRD_PARTY_NOTICES_SHA256':
        '"${identity.thirdPartyNoticesSha256}"',
    // jsonEncode of the JSON text produces one portable C string literal;
    // every provider value was already validated by the strict lock parser.
    'FONIX_ARTIFACT_PROVIDERS_JSON': jsonEncode(providersJson),
  };
}

void _emitBundledRuntimeAssets({
  required BuildOutputBuilder output,
  required FonixBuildOptions options,
  required StagedNativeArtifact stagedRuntime,
}) {
  final runtimeName = switch (options.targetOS) {
    OS.macOS => 'libonnxruntime.1.dylib',
    OS.android => 'libonnxruntime.so',
    OS.linux => 'libonnxruntime.so.1',
    OS.windows => 'onnxruntime.dll',
    _ => throw BuildError(
      message:
          'No bundled runtime filename is defined for ${options.targetOS.name}.',
    ),
  };
  final runtimeFiles = stagedRuntime.payloadFiles
      .where((file) => path.basename(file.path) == runtimeName)
      .toList(growable: false);
  if (runtimeFiles.length != 1) {
    throw BuildError(
      message:
          'The verified artifact did not stage exactly one $runtimeName file.',
    );
  }
  output.assets.code.add(
    CodeAsset(
      package: fonixPackageName,
      name: fonixOrtRuntimeName,
      linkMode: DynamicLoadingBundled(),
      file: runtimeFiles.single.uri,
    ),
  );
  final providerFiles = stagedRuntime.payloadFiles
      .where(
        (file) =>
            path.basename(file.path).contains('onnxruntime_providers_shared'),
      )
      .toList(growable: false);
  if (providerFiles.length > 1) {
    throw BuildError(
      message:
          'The verified artifact staged multiple provider-shared libraries.',
    );
  }
  if (providerFiles case [final providerFile]) {
    output.assets.code.add(
      CodeAsset(
        package: fonixPackageName,
        name: fonixOrtProviderSharedName,
        linkMode: DynamicLoadingBundled(),
        file: providerFile.uri,
      ),
    );
  }
}

String _targetOsName(FonixBuildOptions options) => switch (options.targetOS) {
  OS.iOS => 'ios',
  OS.macOS => 'macos',
  OS.android => 'android',
  OS.linux => 'linux',
  OS.windows => 'windows',
  _ => throw BuildError(
    message:
        'Fonix has no native artifact lock target for ${options.targetOS.name}.',
  ),
};

String _targetArchitectureName(FonixBuildOptions options) {
  if (options.targetOS == OS.android) {
    return switch (options.targetArchitecture) {
      Architecture.arm64 => 'arm64-v8a',
      Architecture.x64 => 'x86_64',
      _ => throw BuildError(
        message:
            'Fonix has no Android artifact for '
            '${options.targetArchitecture.name}.',
      ),
    };
  }
  if (options.targetOS == OS.linux) {
    return switch (options.targetArchitecture) {
      Architecture.arm64 => 'arm64',
      Architecture.x64 => 'x86_64',
      _ => throw BuildError(
        message:
            'Fonix has no Linux artifact for '
            '${options.targetArchitecture.name}.',
      ),
    };
  }
  return switch (options.targetArchitecture) {
    Architecture.arm64 => 'arm64',
    Architecture.x64 => 'x64',
    _ => throw BuildError(
      message:
          'Fonix has no native artifact for '
          '${options.targetArchitecture.name}.',
    ),
  };
}

Directory? _configuredDirectory(BuildInput input, String key) {
  final value = input.userDefines[key];
  if (value == null) {
    return null;
  }
  if (value is! String || value.trim().isEmpty) {
    throw BuildError(
      message:
          'hooks.user_defines.$fonixPackageName.$key must be a non-empty '
          'path string.',
    );
  }
  final resolved = input.userDefines.path(key);
  if (resolved == null || !resolved.isScheme('file')) {
    throw BuildError(
      message:
          'hooks.user_defines.$fonixPackageName.$key must resolve to a local '
          'filesystem directory.',
    );
  }
  return Directory.fromUri(resolved);
}

void _resetOwnedStagingDirectory(Directory directory) {
  final type = FileSystemEntity.typeSync(directory.path, followLinks: false);
  switch (type) {
    case FileSystemEntityType.notFound:
      return;
    case FileSystemEntityType.directory:
      directory.deleteSync(recursive: true);
    case FileSystemEntityType.file:
    case FileSystemEntityType.link:
    case FileSystemEntityType.unixDomainSock:
    case FileSystemEntityType.pipe:
      throw BuildError(
        message:
            'The Fonix build output staging path is not a regular directory: '
            '${directory.path}',
      );
  }
}
