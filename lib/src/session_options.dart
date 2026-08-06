import 'dart:convert';

import 'package:path/path.dart' as p;

import 'provider.dart';
import 'resource_limits.dart';
import 'version.dart';

enum OrtGraphOptimization { disabled, basic, extended, all }

enum OrtExecutionMode { sequential, parallel }

enum OrtOverwritePolicy { reject, replace }

/// Immutable, locally validated ONNX Runtime session configuration.
final class OrtSessionOptions {
  factory OrtSessionOptions({
    OrtGraphOptimization graphOptimization = OrtGraphOptimization.all,
    OrtExecutionMode executionMode = OrtExecutionMode.sequential,
    int intraOpThreads = 0,
    int interOpThreads = 0,
    bool enableCpuMemoryArena = true,
    bool enableMemoryPattern = true,
    bool deterministicCompute = false,
    bool enableProfiling = false,
    String? profilePathPrefix,
    String? optimizedModelPath,
    String? artifactRoot,
    OrtOverwritePolicy optimizedModelOverwrite = OrtOverwritePolicy.reject,
    OrtLogSeverity logSeverity = OrtLogSeverity.warning,
    int logVerbosity = 0,
    String sessionLogId = 'fonix-session',
    List<OrtExecutionProvider> providers = const <OrtExecutionProvider>[],
    OrtFallbackPolicy fallbackPolicy = OrtFallbackPolicy.report,
    Map<String, String> configEntries = const <String, String>{},
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    _threadCount(intraOpThreads, 'intraOpThreads');
    _threadCount(interOpThreads, 'interOpThreads');
    if (logVerbosity < 0 || logVerbosity > 4) {
      throw RangeError.range(logVerbosity, 0, 4, 'logVerbosity');
    }
    _text(sessionLogId, 'sessionLogId', 128);
    if (enableProfiling != (profilePathPrefix != null)) {
      throw ArgumentError(
        'enableProfiling and profilePathPrefix must be supplied together.',
      );
    }
    final String? normalizedProfilePath = profilePathPrefix == null
        ? null
        : _absolutePath(profilePathPrefix, 'profilePathPrefix');
    final String? normalizedOptimizedModelPath = optimizedModelPath == null
        ? null
        : _absolutePath(optimizedModelPath, 'optimizedModelPath');
    final bool hasCoreMlCache = providers.any(
      (OrtExecutionProvider provider) => provider.coreMlCache != null,
    );
    final bool requiresProviderEvidence =
        providers.any(
          (OrtExecutionProvider provider) =>
              provider.requirement == OrtProviderRequirement.requireActive ||
              provider.requirement ==
                  OrtProviderRequirement.requireFullAssignment,
        ) ||
        fallbackPolicy == OrtFallbackPolicy.rejectAny ||
        ((fallbackPolicy == OrtFallbackPolicy.report ||
                fallbackPolicy == OrtFallbackPolicy.rejectCpu) &&
            providers.any(
              (OrtExecutionProvider provider) => provider.id != 'cpu',
            ));
    final bool hasArtifactUse =
        normalizedProfilePath != null ||
        normalizedOptimizedModelPath != null ||
        hasCoreMlCache ||
        requiresProviderEvidence;
    if (hasArtifactUse != (artifactRoot != null)) {
      throw ArgumentError(
        'artifactRoot is required exactly when profiling, optimized-model, '
        'Core ML cache output, or run-level provider evidence is configured.',
      );
    }
    final String? normalizedArtifactRoot = artifactRoot == null
        ? null
        : _absolutePath(artifactRoot, 'artifactRoot');
    if (normalizedArtifactRoot != null) {
      for (final String path in <String>[
        ?normalizedProfilePath,
        ?normalizedOptimizedModelPath,
      ]) {
        if (path != normalizedArtifactRoot &&
            !p.isWithin(normalizedArtifactRoot, path)) {
          throw ArgumentError(
            'Session output paths must be contained by artifactRoot.',
          );
        }
      }
      for (final OrtExecutionProvider provider in providers) {
        final OrtCoreMlCacheConfiguration? cache = provider.coreMlCache;
        if (cache != null && cache.rootDirectory != normalizedArtifactRoot) {
          throw ArgumentError(
            'The Core ML cache root must exactly equal artifactRoot.',
          );
        }
      }
    }
    if (providers.length > limits.maxProviders) {
      throw RangeError.range(
        providers.length,
        0,
        limits.maxProviders,
        'providers.length',
      );
    }
    final effectiveProviders = providers.isEmpty
        ? <OrtExecutionProvider>[OrtExecutionProvider.cpu()]
        : List<OrtExecutionProvider>.of(providers);
    final providerIds = <String>{};
    for (final provider in effectiveProviders) {
      if (!providerIds.add(provider.id)) {
        throw ArgumentError.value(
          provider.id,
          'providers',
          'contains a duplicate provider ID',
        );
      }
    }
    final int cpuIndex = effectiveProviders.indexWhere(
      (OrtExecutionProvider provider) => provider.id == 'cpu',
    );
    if (cpuIndex >= 0 && cpuIndex != effectiveProviders.length - 1) {
      throw ArgumentError(
        'The implicit CPU fallback must be the last requested provider.',
      );
    }
    final int tensorRtIndex = effectiveProviders.indexWhere(
      (OrtExecutionProvider provider) => provider.id == 'tensorrt',
    );
    final int cudaIndex = effectiveProviders.indexWhere(
      (OrtExecutionProvider provider) => provider.id == 'cuda',
    );
    if (tensorRtIndex >= 0 && (cudaIndex < 0 || tensorRtIndex >= cudaIndex)) {
      throw ArgumentError(
        'TensorRT requires a lower-priority CUDA provider in the same '
        'session (TensorRT -> CUDA -> optional CPU).',
      );
    }
    if (providerIds.contains('directml') &&
        (executionMode != OrtExecutionMode.sequential || enableMemoryPattern)) {
      throw ArgumentError(
        'DirectML requires sequential execution and memory patterns disabled.',
      );
    }
    final xnnpack = effectiveProviders
        .where((provider) => provider.id == 'xnnpack')
        .firstOrNull;
    final xnnpackThreads = xnnpack == null
        ? null
        : int.tryParse(xnnpack.options['intra_op_num_threads'] ?? '');
    if (xnnpackThreads != null && xnnpackThreads > 1 && intraOpThreads != 1) {
      throw ArgumentError(
        'XNNPACK thread counts above one require ORT intraOpThreads exactly '
        'one to prevent oversubscription.',
      );
    }
    if (configEntries.length > limits.maxConfigEntries) {
      throw RangeError.range(
        configEntries.length,
        0,
        limits.maxConfigEntries,
        'configEntries.length',
      );
    }
    final normalizedEntries = <String, String>{};
    for (final entry in configEntries.entries) {
      _text(entry.key, 'config entry key', 256);
      _text(entry.value, 'config entry value', 4096, allowEmpty: true);
      normalizedEntries[entry.key] = entry.value;
    }
    return OrtSessionOptions._(
      graphOptimization: graphOptimization,
      executionMode: executionMode,
      intraOpThreads: intraOpThreads,
      interOpThreads: interOpThreads,
      enableCpuMemoryArena: enableCpuMemoryArena,
      enableMemoryPattern: enableMemoryPattern,
      deterministicCompute: deterministicCompute,
      enableProfiling: enableProfiling,
      profilePathPrefix: normalizedProfilePath,
      optimizedModelPath: normalizedOptimizedModelPath,
      artifactRoot: normalizedArtifactRoot,
      optimizedModelOverwrite: optimizedModelOverwrite,
      logSeverity: logSeverity,
      logVerbosity: logVerbosity,
      sessionLogId: sessionLogId,
      providers: List<OrtExecutionProvider>.unmodifiable(effectiveProviders),
      fallbackPolicy: fallbackPolicy,
      configEntries: Map<String, String>.unmodifiable(normalizedEntries),
      limits: limits,
    );
  }

  const OrtSessionOptions._({
    required this.graphOptimization,
    required this.executionMode,
    required this.intraOpThreads,
    required this.interOpThreads,
    required this.enableCpuMemoryArena,
    required this.enableMemoryPattern,
    required this.deterministicCompute,
    required this.enableProfiling,
    required this.profilePathPrefix,
    required this.optimizedModelPath,
    required this.artifactRoot,
    required this.optimizedModelOverwrite,
    required this.logSeverity,
    required this.logVerbosity,
    required this.sessionLogId,
    required this.providers,
    required this.fallbackPolicy,
    required this.configEntries,
    required this.limits,
  });

  final OrtGraphOptimization graphOptimization;
  final OrtExecutionMode executionMode;
  final int intraOpThreads;
  final int interOpThreads;
  final bool enableCpuMemoryArena;
  final bool enableMemoryPattern;
  final bool deterministicCompute;
  final bool enableProfiling;
  final String? profilePathPrefix;
  final String? optimizedModelPath;
  final String? artifactRoot;
  final OrtOverwritePolicy optimizedModelOverwrite;
  final OrtLogSeverity logSeverity;
  final int logVerbosity;
  final String sessionLogId;
  final List<OrtExecutionProvider> providers;
  final OrtFallbackPolicy fallbackPolicy;
  final Map<String, String> configEntries;
  final OrtResourceLimits limits;

  /// Whether this policy needs one validated per-run assignment profile.
  bool get requiresProviderEvidence =>
      providers.any(
        (OrtExecutionProvider provider) =>
            provider.requirement == OrtProviderRequirement.requireActive ||
            provider.requirement ==
                OrtProviderRequirement.requireFullAssignment,
      ) ||
      fallbackPolicy == OrtFallbackPolicy.rejectAny ||
      ((fallbackPolicy == OrtFallbackPolicy.report ||
              fallbackPolicy == OrtFallbackPolicy.rejectCpu) &&
          providers.any(
            (OrtExecutionProvider provider) => provider.id != 'cpu',
          ));
}

void _threadCount(int value, String name) {
  if (value < 0 || value > 1024) {
    throw RangeError.range(value, 0, 1024, name);
  }
}

String _absolutePath(String value, String name) {
  _text(value, name, 4096);
  if (!p.isAbsolute(value)) {
    throw ArgumentError('$name must be absolute.');
  }
  return p.normalize(value);
}

void _text(
  String value,
  String name,
  int maxLength, {
  bool allowEmpty = false,
}) {
  if ((!allowEmpty && value.isEmpty) ||
      value.length > maxLength ||
      utf8.encode(value).length > maxLength ||
      value.contains('\u0000') ||
      value.contains('\n') ||
      value.contains('\r')) {
    throw ArgumentError('$name is not bounded UTF-8 text.');
  }
}

extension<T> on Iterable<T> {
  T? get firstOrNull {
    final iterator = this.iterator;
    return iterator.moveNext() ? iterator.current : null;
  }
}
