part of 'isolate_session.dart';

const int _ortWorkerProtocolVersion = 4;

const int _defaultWorkerMessageBytes = 64 * 1024 * 1024;

const int _maximumWorkerMessageBytes = 1024 * 1024 * 1024;

const int _maximumOutstandingWorkerInputBytes = 1024 * 1024 * 1024;

const int _maximumPendingWorkerRuns = 1024;

const int _maximumSessionPoolSize = 32;

const int _maximumWorkerCompositeChildren = 1024;

const int _maximumWorkerRequestId = 0x7fffffffffffffff;

const int _workerStringRetentionBytes = 8;

/// Round-trips session options through the versioned worker message contract.
///
/// This package-internal helper is used only by protocol tests; native handles
/// are never involved.
OrtSessionOptions roundTripOrtWorkerSessionOptionsForTesting(
  OrtSessionOptions options,
) => _decodeSessionOptions(_encodeSessionOptions(options));

void _validateWorkerBounds({
  required int maxPendingRuns,
  required int maxMessageBytes,
  required int maxOutstandingInputBytes,
  required Duration startupTimeout,
}) {
  if (maxPendingRuns < 1 || maxPendingRuns > _maximumPendingWorkerRuns) {
    throw RangeError.range(
      maxPendingRuns,
      1,
      _maximumPendingWorkerRuns,
      'maxPendingRuns',
    );
  }
  if (maxMessageBytes < 1 || maxMessageBytes > _maximumWorkerMessageBytes) {
    throw RangeError.range(
      maxMessageBytes,
      1,
      _maximumWorkerMessageBytes,
      'maxMessageBytes',
    );
  }
  if (maxOutstandingInputBytes < 1 ||
      maxOutstandingInputBytes > _maximumOutstandingWorkerInputBytes) {
    throw RangeError.range(
      maxOutstandingInputBytes,
      1,
      _maximumOutstandingWorkerInputBytes,
      'maxOutstandingInputBytes',
    );
  }
  if (startupTimeout <= Duration.zero ||
      startupTimeout > const Duration(minutes: 10)) {
    throw RangeError('startupTimeout must be in (zero, 10 minutes].');
  }
}

void _validateWorkerText(String value, String name, int maximumBytes) {
  if (value.isEmpty ||
      !hasWellFormedUtf16(value) ||
      value.contains('\u0000') ||
      value.contains('\n') ||
      value.contains('\r') ||
      utf8.encode(value).length > maximumBytes) {
    throw ArgumentError('$name must be bounded safe UTF-8 text.');
  }
}

Map<String, Object?> _encodeWorkerStartup({
  required OrtRuntimeSource runtimeSource,
  required OrtModelSource model,
  required OrtSessionOptions options,
  required OrtApiVersion requiredApi,
  required OrtLogSeverity logSeverity,
  required String logId,
  required int maxMessageBytes,
}) {
  _validateWorkerText(logId, 'logId', 128);
  return <String, Object?>{
    'version': _ortWorkerProtocolVersion,
    'type': 'startup',
    'runtimeSource': _encodeRuntimeSource(runtimeSource),
    'model': _encodeModelSource(model),
    'options': _encodeSessionOptions(options),
    'requiredApi': requiredApi.value,
    'logSeverity': logSeverity.index,
    'logId': logId,
    'maxMessageBytes': maxMessageBytes,
  };
}

Map<String, Object?> _encodeRuntimeSource(OrtRuntimeSource source) =>
    <String, Object?>{
      'kind': source.kind.name,
      if (source.libraryPath case final String path) 'libraryPath': path,
      if (source.allowedRoot case final String root) 'allowedRoot': root,
      if (source.preferredLibraryNames.isNotEmpty)
        'preferredLibraryNames': source.preferredLibraryNames,
    };

Map<String, Object?> _encodeModelSource(OrtModelSource model) =>
    switch (model) {
      OrtBytesModelSource() => <String, Object?>{
        'kind': 'bytes',
        'modelId': model.modelId,
        'bytes': TransferableTypedData.fromList(<TypedData>[model.bytes]),
        'externalData': <String, Object?>{
          for (final MapEntry<String, Uint8List> entry
              in model.externalData.entries)
            entry.key: TransferableTypedData.fromList(<TypedData>[entry.value]),
        },
      },
      OrtFileModelSource() => <String, Object?>{
        'kind': 'file',
        'modelId': model.modelId,
        'absolutePath': model.absolutePath,
        'allowedRoot': model.allowedRoot,
      },
    };

Map<String, Object?> _encodeSessionOptions(OrtSessionOptions options) =>
    <String, Object?>{
      'graphOptimization': options.graphOptimization.name,
      'executionMode': options.executionMode.name,
      'intraOpThreads': options.intraOpThreads,
      'interOpThreads': options.interOpThreads,
      'enableCpuMemoryArena': options.enableCpuMemoryArena,
      'enableMemoryPattern': options.enableMemoryPattern,
      'deterministicCompute': options.deterministicCompute,
      'enableProfiling': options.enableProfiling,
      'profilePathPrefix': options.profilePathPrefix,
      'optimizedModelPath': options.optimizedModelPath,
      'artifactRoot': options.artifactRoot,
      'optimizedModelOverwrite': options.optimizedModelOverwrite.name,
      'logSeverity': options.logSeverity.index,
      'logVerbosity': options.logVerbosity,
      'sessionLogId': options.sessionLogId,
      'providers': <Object?>[
        for (final OrtExecutionProvider provider in options.providers)
          <String, Object?>{
            'id': provider.id,
            'options': provider.options,
            'requirement': provider.requirement.name,
            'coreMlCache': switch (provider.coreMlCache) {
              null => null,
              final OrtCoreMlCacheConfiguration cache => <String, Object?>{
                'rootDirectory': cache.rootDirectory,
                'modelSha256': cache.modelSha256,
                'applicationSchema': cache.applicationSchema,
              },
            },
          },
      ],
      'fallbackPolicy': options.fallbackPolicy.name,
      'configEntries': options.configEntries,
      'limits': _encodeResourceLimits(options.limits),
    };

Map<String, Object?> _encodeResourceLimits(OrtResourceLimits limits) =>
    <String, Object?>{
      'maxModelBytes': limits.maxModelBytes,
      'maxTensorBytes': limits.maxTensorBytes,
      'maxTensorElements': limits.maxTensorElements,
      'maxRank': limits.maxRank,
      'maxDimension': limits.maxDimension,
      'maxProviders': limits.maxProviders,
      'maxProviderOptions': limits.maxProviderOptions,
      'maxConfigEntries': limits.maxConfigEntries,
      'maxDiagnosticsBytes': limits.maxDiagnosticsBytes,
      'maxTypeDepth': limits.maxTypeDepth,
      'maxTypeNodes': limits.maxTypeNodes,
    };

OrtRuntimeSource _decodeRuntimeSource(Object? raw) {
  final Map<Object?, Object?> source = _workerMap(raw);
  final String kind = _workerString(source, 'kind');
  switch (kind) {
    case 'linked':
      _requireWorkerKeys(source, required: const <String>{'kind'});
      return const OrtRuntimeSource.linked();
    case 'bundled':
      _requireWorkerKeys(source, required: const <String>{'kind'});
      return const OrtRuntimeSource.bundled();
    case 'process':
      _requireWorkerKeys(
        source,
        required: const <String>{'kind'},
        optional: const <String>{'preferredLibraryNames'},
      );
      return OrtRuntimeSource.process(
        preferredLibraryNames: _workerStringList(
          source['preferredLibraryNames'] ?? const <String>[],
          maximum: 8,
        ),
      );
    case 'file':
      _requireWorkerKeys(
        source,
        required: const <String>{'kind', 'libraryPath'},
        optional: const <String>{'allowedRoot'},
      );
      return OrtRuntimeSource.file(
        absolutePath: _workerString(source, 'libraryPath'),
        allowedRoot: _workerNullableString(source, 'allowedRoot'),
      );
    default:
      throw const FormatException('Unknown runtime source kind.');
  }
}

OrtModelSource _decodeModelSource(Object? raw, OrtResourceLimits limits) {
  final Map<Object?, Object?> model = _workerMap(raw);
  final String kind = _workerString(model, 'kind');
  if (kind == 'bytes') {
    _requireWorkerKeys(
      model,
      required: const <String>{'kind', 'modelId', 'bytes', 'externalData'},
    );
  } else if (kind == 'file') {
    _requireWorkerKeys(
      model,
      required: const <String>{
        'kind',
        'modelId',
        'absolutePath',
        'allowedRoot',
      },
    );
  } else {
    throw const FormatException('Unknown model source kind.');
  }
  final String? modelId = _workerNullableString(model, 'modelId');
  return switch (kind) {
    'bytes' => OrtModelSource.bytes(
      _materializeWorkerBytes(model['bytes'], maximum: limits.maxModelBytes),
      externalData: _decodeExternalData(
        model['externalData'],
        maximumTotalBytes: limits.maxModelBytes,
      ),
      modelId: modelId,
      limits: limits,
    ),
    'file' => OrtModelSource.file(
      absolutePath: _workerString(model, 'absolutePath'),
      allowedRoot: _workerString(model, 'allowedRoot'),
      modelId: modelId,
    ),
    _ => throw const FormatException('Unknown model source kind.'),
  };
}

Map<String, Uint8List> _decodeExternalData(
  Object? raw, {
  required int maximumTotalBytes,
}) {
  final Map<Object?, Object?> encoded = _workerMap(raw);
  if (encoded.length > maximumOrtExternalDataFiles) {
    throw const FormatException('Too many external-data entries.');
  }
  final Map<String, Uint8List> result = <String, Uint8List>{};
  var totalBytes = 0;
  for (final MapEntry<Object?, Object?> entry in encoded.entries) {
    if (entry.key is! String) {
      throw const FormatException('External-data name is not a string.');
    }
    final Uint8List bytes = _materializeWorkerBytes(
      entry.value,
      maximum: maximumTotalBytes - totalBytes,
    );
    totalBytes += bytes.length;
    result[entry.key! as String] = bytes;
  }
  return result;
}

OrtSessionOptions _decodeSessionOptions(Object? raw) {
  final Map<Object?, Object?> options = _workerMap(raw);
  _requireWorkerKeys(
    options,
    required: const <String>{
      'graphOptimization',
      'executionMode',
      'intraOpThreads',
      'interOpThreads',
      'enableCpuMemoryArena',
      'enableMemoryPattern',
      'deterministicCompute',
      'enableProfiling',
      'profilePathPrefix',
      'optimizedModelPath',
      'artifactRoot',
      'optimizedModelOverwrite',
      'logSeverity',
      'logVerbosity',
      'sessionLogId',
      'providers',
      'fallbackPolicy',
      'configEntries',
      'limits',
    },
  );
  final OrtResourceLimits limits = _decodeResourceLimits(options['limits']);
  final Object? rawProviders = options['providers'];
  if (rawProviders is! List<Object?> ||
      rawProviders.length > limits.maxProviders) {
    throw const FormatException('Invalid worker provider list.');
  }
  final List<OrtExecutionProvider> providers = <OrtExecutionProvider>[];
  for (final Object? rawProvider in rawProviders) {
    final Map<Object?, Object?> provider = _workerMap(rawProvider);
    _requireWorkerKeys(
      provider,
      required: const <String>{'id', 'options', 'requirement', 'coreMlCache'},
    );
    providers.add(_decodeWorkerProvider(provider, limits));
  }
  return OrtSessionOptions(
    graphOptimization: _workerEnum(
      OrtGraphOptimization.values,
      options['graphOptimization'],
      'graph optimization',
    ),
    executionMode: _workerEnum(
      OrtExecutionMode.values,
      options['executionMode'],
      'execution mode',
    ),
    intraOpThreads: _workerInt(options, 'intraOpThreads'),
    interOpThreads: _workerInt(options, 'interOpThreads'),
    enableCpuMemoryArena: _workerBool(options, 'enableCpuMemoryArena'),
    enableMemoryPattern: _workerBool(options, 'enableMemoryPattern'),
    deterministicCompute: _workerBool(options, 'deterministicCompute'),
    enableProfiling: _workerBool(options, 'enableProfiling'),
    profilePathPrefix: _workerNullableString(options, 'profilePathPrefix'),
    optimizedModelPath: _workerNullableString(options, 'optimizedModelPath'),
    artifactRoot: _workerNullableString(options, 'artifactRoot'),
    optimizedModelOverwrite: _workerEnum(
      OrtOverwritePolicy.values,
      options['optimizedModelOverwrite'],
      'optimized model overwrite',
    ),
    logSeverity:
        OrtLogSeverity.values[_workerBoundedInt(
          options,
          'logSeverity',
          0,
          OrtLogSeverity.values.length - 1,
        )],
    logVerbosity: _workerInt(options, 'logVerbosity'),
    sessionLogId: _workerString(options, 'sessionLogId'),
    providers: providers,
    fallbackPolicy: _workerEnum(
      OrtFallbackPolicy.values,
      options['fallbackPolicy'],
      'fallback policy',
    ),
    configEntries: _workerStringMap(
      options['configEntries'],
      maximum: limits.maxConfigEntries,
    ),
    limits: limits,
  );
}

OrtExecutionProvider _decodeWorkerProvider(
  Map<Object?, Object?> provider,
  OrtResourceLimits limits,
) {
  final String id = _workerString(provider, 'id');
  final Map<String, String> providerOptions = _workerStringMap(
    provider['options'],
    maximum: limits.maxProviderOptions,
  );
  final OrtProviderRequirement requirement = _workerEnum(
    OrtProviderRequirement.values,
    provider['requirement'],
    'provider requirement',
  );
  final Object? rawCache = provider['coreMlCache'];
  if (rawCache == null) {
    return OrtExecutionProvider.named(
      id,
      options: providerOptions,
      requirement: requirement,
      limits: limits,
    );
  }
  if (id != 'coreml') {
    throw const FormatException(
      'Only a Core ML worker provider may carry cache configuration.',
    );
  }
  final Map<Object?, Object?> cache = _workerMap(rawCache);
  _requireWorkerKeys(
    cache,
    required: const <String>{
      'rootDirectory',
      'modelSha256',
      'applicationSchema',
    },
  );
  const Set<String> expectedOptionKeys = <String>{
    'ModelFormat',
    'MLComputeUnits',
    'RequireStaticInputShapes',
    'EnableOnSubgraphs',
  };
  if (providerOptions.length != expectedOptionKeys.length ||
      !providerOptions.keys.toSet().containsAll(expectedOptionKeys)) {
    throw const FormatException(
      'Core ML worker options do not match the typed cache protocol.',
    );
  }
  final OrtCoreMlModelFormat modelFormat =
      switch (providerOptions['ModelFormat']) {
        'NeuralNetwork' => OrtCoreMlModelFormat.neuralNetwork,
        'MLProgram' => OrtCoreMlModelFormat.mlProgram,
        _ => throw const FormatException('Unknown Core ML model format.'),
      };
  final OrtCoreMlComputeUnits computeUnits =
      switch (providerOptions['MLComputeUnits']) {
        'ALL' => OrtCoreMlComputeUnits.all,
        'CPUOnly' => OrtCoreMlComputeUnits.cpuOnly,
        'CPUAndGPU' => OrtCoreMlComputeUnits.cpuAndGpu,
        'CPUAndNeuralEngine' => OrtCoreMlComputeUnits.cpuAndNeuralEngine,
        _ => throw const FormatException(
          'Unknown Core ML compute-unit policy.',
        ),
      };
  bool flag(String key) => switch (providerOptions[key]) {
    '0' => false,
    '1' => true,
    _ => throw FormatException('Core ML option $key is not Boolean.'),
  };

  return OrtExecutionProvider.coreMl(
    modelFormat: modelFormat,
    computeUnits: computeUnits,
    requireStaticInputShapes: flag('RequireStaticInputShapes'),
    enableOnSubgraphs: flag('EnableOnSubgraphs'),
    cache: OrtCoreMlCacheConfiguration(
      rootDirectory: _workerString(cache, 'rootDirectory'),
      modelSha256: _workerString(cache, 'modelSha256'),
      applicationSchema: _workerString(cache, 'applicationSchema'),
    ),
    requirement: requirement,
  );
}

OrtResourceLimits _decodeResourceLimits(Object? raw) {
  final Map<Object?, Object?> limits = _workerMap(raw);
  _requireWorkerKeys(
    limits,
    required: const <String>{
      'maxModelBytes',
      'maxTensorBytes',
      'maxTensorElements',
      'maxRank',
      'maxDimension',
      'maxProviders',
      'maxProviderOptions',
      'maxConfigEntries',
      'maxDiagnosticsBytes',
      'maxTypeDepth',
      'maxTypeNodes',
    },
  );
  return OrtResourceLimits(
    maxModelBytes: _workerInt(limits, 'maxModelBytes'),
    maxTensorBytes: _workerInt(limits, 'maxTensorBytes'),
    maxTensorElements: _workerInt(limits, 'maxTensorElements'),
    maxRank: _workerInt(limits, 'maxRank'),
    maxDimension: _workerInt(limits, 'maxDimension'),
    maxProviders: _workerInt(limits, 'maxProviders'),
    maxProviderOptions: _workerInt(limits, 'maxProviderOptions'),
    maxConfigEntries: _workerInt(limits, 'maxConfigEntries'),
    maxDiagnosticsBytes: _workerInt(limits, 'maxDiagnosticsBytes'),
    maxTypeDepth: _workerInt(limits, 'maxTypeDepth'),
    maxTypeNodes: _workerInt(limits, 'maxTypeNodes'),
  );
}

Map<Object?, Object?> _workerMap(Object? raw) {
  if (raw is! Map<Object?, Object?> || raw.length > 4096) {
    throw const FormatException('Worker protocol value is not a bounded map.');
  }
  return raw;
}

void _requireWorkerKeys(
  Map<Object?, Object?> map, {
  required Set<String> required,
  Set<String> optional = const <String>{},
}) {
  final Set<String> allowed = <String>{...required, ...optional};
  for (final Object? key in map.keys) {
    if (key is! String || !allowed.contains(key)) {
      throw const FormatException('Worker map contains an unknown field.');
    }
  }
  for (final String key in required) {
    if (!map.containsKey(key)) {
      throw FormatException('Worker map is missing required field $key.');
    }
  }
}

void _requireWorkerStartupReplyKeys(
  Map<Object?, Object?> message,
  String type,
) {
  switch (type) {
    case 'ownership':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'commandPort'},
      );
    case 'ready':
      _requireWorkerKeys(
        message,
        required: const <String>{
          'version',
          'type',
          'commandPort',
          'inputNames',
          'outputNames',
          'diagnostics',
        },
      );
    case 'startupError':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'message'},
      );
    default:
      throw const FormatException('Unknown worker startup reply type.');
  }
}

void _requireWorkerReplyKeys(Map<Object?, Object?> message, String type) {
  switch (type) {
    case 'closed':
      _requireWorkerKeys(message, required: const <String>{'version', 'type'});
    case 'fatalProtocol':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'message'},
      );
    case 'started':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'requestId', 'cancelToken'},
      );
    case 'result':
      _requireWorkerKeys(
        message,
        required: const <String>{
          'version',
          'type',
          'requestId',
          'outputs',
          'providerEvidence',
          'providerDiagnostics',
          'diagnostics',
          'wasTerminationRequested',
        },
      );
    case 'ortError':
      _requireWorkerKeys(
        message,
        required: const <String>{
          'version',
          'type',
          'requestId',
          'error',
          'wasTerminationRequested',
        },
      );
    case 'workerError':
    case 'fatalWorkerError':
      _requireWorkerKeys(
        message,
        required: const <String>{'version', 'type', 'requestId', 'message'},
      );
    default:
      throw const FormatException('Unknown worker reply type.');
  }
}

void _requireWorkerCommandKeys(Map<Object?, Object?> command, String type) {
  switch (type) {
    case 'close':
    case 'retire':
      _requireWorkerKeys(command, required: const <String>{'version', 'type'});
    case 'run':
      _requireWorkerKeys(
        command,
        required: const <String>{
          'version',
          'type',
          'requestId',
          'inputs',
          'outputNames',
          'maxMessageBytes',
        },
      );
    default:
      throw const FormatException('Unknown worker command type.');
  }
}

void _requireWorkerVersion(Map<Object?, Object?> message) {
  if (message['version'] != _ortWorkerProtocolVersion) {
    throw const FormatException('Unsupported worker protocol version.');
  }
}

String _workerString(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value is! String ||
      value.isEmpty ||
      value.length > 4096 ||
      utf8.encode(value).length > 4096 ||
      value.contains('\u0000')) {
    throw FormatException('Worker field $key is not bounded text.');
  }
  return value;
}

String? _workerNullableString(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value == null) return null;
  return _workerString(<Object?, Object?>{key: value}, key);
}

int _workerInt(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value is! int) throw FormatException('Worker field $key is not an int.');
  return value;
}

int _workerPositiveInt(Map<Object?, Object?> map, String key) {
  final int value = _workerInt(map, key);
  if (value <= 0 || value > 0x7fffffffffffffff) {
    throw FormatException(
      'Worker field $key is outside the positive int64 range.',
    );
  }
  return value;
}

int _workerBoundedInt(
  Map<Object?, Object?> map,
  String key,
  int minimum,
  int maximum,
) {
  final int value = _workerInt(map, key);
  if (value < minimum || value > maximum) {
    throw FormatException('Worker field $key is outside its closed range.');
  }
  return value;
}

bool _workerBool(Map<Object?, Object?> map, String key) {
  final Object? value = map[key];
  if (value is! bool) throw FormatException('Worker field $key is not a bool.');
  return value;
}

T _workerEnum<T extends Enum>(List<T> values, Object? raw, String label) {
  if (raw is String) {
    for (final T value in values) {
      if (value.name == raw) return value;
    }
  }
  throw FormatException('Worker $label is outside its closed enum.');
}

List<String> _workerStringList(Object? raw, {required int maximum}) {
  if (raw is! List<Object?> || raw.length > maximum) {
    throw const FormatException('Worker value is not a bounded string list.');
  }
  final List<String> result = <String>[];
  final Set<String> seen = <String>{};
  for (final Object? value in raw) {
    if (value is! String ||
        value.isEmpty ||
        value.length > 1024 ||
        utf8.encode(value).length > 1024 ||
        value.contains('\u0000') ||
        !seen.add(value)) {
      throw const FormatException('Worker string list is invalid.');
    }
    result.add(value);
  }
  return List<String>.unmodifiable(result);
}

Map<String, String> _workerStringMap(Object? raw, {required int maximum}) {
  final Map<Object?, Object?> map = _workerMap(raw);
  if (map.length > maximum) {
    throw const FormatException('Worker string map exceeds its bound.');
  }
  final Map<String, String> result = <String, String>{};
  for (final MapEntry<Object?, Object?> entry in map.entries) {
    if (entry.key is! String || entry.value is! String) {
      throw const FormatException('Worker string map contains another type.');
    }
    result[entry.key! as String] = entry.value! as String;
  }
  return result;
}

Uint8List _materializeWorkerBytes(Object? raw, {required int maximum}) {
  if (maximum < 0 || raw is! TransferableTypedData) {
    throw const FormatException('Worker bytes are not transferable data.');
  }
  final ByteBuffer buffer = raw.materialize();
  if (buffer.lengthInBytes > maximum) {
    throw const FormatException('Worker bytes exceed their configured bound.');
  }
  return Uint8List.fromList(buffer.asUint8List());
}

String _boundedWorkerText(Object? raw, String fallback) {
  if (raw is! String || raw.isEmpty || raw.contains('\u0000')) return fallback;
  final List<int> bytes = utf8.encode(raw);
  if (bytes.length <= 1024) return raw;
  return utf8.decode(bytes.sublist(0, 1024), allowMalformed: true);
}

String _boundedWorkerCrash(Object? raw) {
  Object? error = raw;
  if (raw is List<Object?> && raw.isNotEmpty) error = raw.first;
  return _boundedWorkerText(error?.toString(), 'unreported worker error');
}

final class _ValidatedWorkerInputs {
  const _ValidatedWorkerInputs({required this.values, required this.bytes});

  final Map<String, OrtIsolateValue> values;
  final int bytes;
}

_ValidatedWorkerInputs _validateWorkerRunInputs({
  required Map<String, OrtIsolateValue> supplied,
  required List<String> knownNames,
  required int maxMessageBytes,
}) {
  if (supplied.length > knownNames.length) {
    throw ArgumentError('Too many isolate-session inputs were supplied.');
  }
  final Set<String> known = knownNames.toSet();
  final _WorkerMessageBudget budget = _WorkerMessageBudget(
    maxBytes: maxMessageBytes,
  );
  final Map<String, OrtIsolateValue> copied = <String, OrtIsolateValue>{};
  for (final MapEntry<String, OrtIsolateValue> entry in supplied.entries) {
    if (!known.contains(entry.key) || copied.containsKey(entry.key)) {
      throw ArgumentError('An isolate-session input name is unknown.');
    }
    budget.addUtf8(entry.key);
    _measureIsolateValue(entry.value, budget: budget, depth: 0);
    copied[entry.key] = entry.value;
  }
  return _ValidatedWorkerInputs(
    values: Map<String, OrtIsolateValue>.unmodifiable(copied),
    bytes: budget.bytes,
  );
}

List<String> _validateWorkerOutputNames({
  required List<String>? supplied,
  required List<String> knownNames,
}) {
  if (supplied != null &&
      (supplied.isEmpty || supplied.length > knownNames.length)) {
    throw ArgumentError('At least one known worker output is required.');
  }
  final List<String> selected = supplied == null
      ? List<String>.of(knownNames)
      : List<String>.of(supplied);
  if (selected.isEmpty || selected.length > knownNames.length) {
    throw ArgumentError('At least one known worker output is required.');
  }
  final Set<String> known = knownNames.toSet();
  final Set<String> seen = <String>{};
  for (final String name in selected) {
    if (!known.contains(name) || !seen.add(name)) {
      throw ArgumentError('Worker output names must be known and unique.');
    }
  }
  return List<String>.unmodifiable(selected);
}

void _validateWorkerRequestMessageBytes({
  required int inputBytes,
  required List<String> outputNames,
  required int maxMessageBytes,
}) {
  final _WorkerMessageBudget budget = _WorkerMessageBudget(
    maxBytes: maxMessageBytes,
  )..addBytes(inputBytes);
  for (final String outputName in outputNames) {
    budget.addUtf8(outputName);
  }
}

final class _WorkerMessageBudget {
  _WorkerMessageBudget({required this.maxBytes});

  final int maxBytes;
  int bytes = 0;
  int nodes = 0;

  int get remainingBytes => maxBytes - bytes;

  void addNode(int depth) {
    if (depth > 8 || ++nodes > 4096) {
      throw OrtWorkerMessageTooLargeException(
        message: 'The worker value tree exceeds its depth/node bound.',
        context: <String, Object?>{'maxDepth': 8, 'maxNodes': 4096},
      );
    }
  }

  void addBytes(int count) {
    if (count < 0 || count > maxBytes - bytes) {
      throw OrtWorkerMessageTooLargeException(
        message: 'The worker message exceeds its configured byte bound.',
        context: <String, Object?>{'maxMessageBytes': maxBytes},
      );
    }
    bytes += count;
  }

  void addUtf8(String value) => addBytes(utf8.encode(value).length);
}

void _addWorkerStringRetention(_WorkerMessageBudget budget, int count) {
  budget.addBytes(count * _workerStringRetentionBytes);
}

void _measureIsolateValue(
  OrtIsolateValue value, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  switch (value) {
    case OrtIsolateTensor():
      budget.addBytes(value.shape.rank * 8);
      if (value.isString) {
        final List<String> strings = value.copyStrings();
        _addWorkerStringRetention(budget, strings.length);
        for (final String string in strings) {
          budget.addUtf8(string);
        }
      } else {
        budget.addBytes(value.byteLength);
      }
    case OrtIsolateSequence():
      for (final OrtIsolateValue child in value.elements) {
        _measureIsolateValue(child, budget: budget, depth: depth + 1);
      }
    case OrtIsolateMap():
      _measureIsolateValue(value.keys, budget: budget, depth: depth + 1);
      _measureIsolateValue(value.values, budget: budget, depth: depth + 1);
    case OrtIsolateOptional():
      _measureWorkerType(value.elementType, budget: budget, depth: depth + 1);
      if (value.value case final OrtIsolateValue child) {
        _measureIsolateValue(child, budget: budget, depth: depth + 1);
      }
  }
}

Map<String, Object?> _encodeIsolateValue(
  OrtIsolateValue value, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  return switch (value) {
    OrtIsolateTensor() when value.isString => <String, Object?>{
      'kind': 'tensor',
      'elementType': value.elementType.nativeValue,
      'shape': _workerShapeForTransfer(value, budget),
      'strings': _workerStringsForTransfer(value, budget),
    },
    OrtIsolateTensor() => <String, Object?>{
      'kind': 'tensor',
      'elementType': value.elementType.nativeValue,
      'shape': _workerShapeForTransfer(value, budget),
      'data': _transferWorkerTensor(value, budget),
    },
    OrtIsolateSequence() => <String, Object?>{
      'kind': 'sequence',
      'elements': <Object?>[
        for (final OrtIsolateValue child in value.elements)
          _encodeIsolateValue(child, budget: budget, depth: depth + 1),
      ],
    },
    OrtIsolateMap() => <String, Object?>{
      'kind': 'map',
      'keys': _encodeIsolateValue(value.keys, budget: budget, depth: depth + 1),
      'values': _encodeIsolateValue(
        value.values,
        budget: budget,
        depth: depth + 1,
      ),
    },
    OrtIsolateOptional() => <String, Object?>{
      'kind': 'optional',
      'elementType': _encodeWorkerType(
        value.elementType,
        budget: budget,
        depth: depth + 1,
      ),
      'value': value.value == null
          ? null
          : _encodeIsolateValue(value.value!, budget: budget, depth: depth + 1),
    },
  };
}

List<int> _workerShapeForTransfer(
  OrtIsolateTensor value,
  _WorkerMessageBudget budget,
) {
  budget.addBytes(value.shape.rank * 8);
  return value.shape.dimensions;
}

List<String> _workerStringsForTransfer(
  OrtIsolateTensor value,
  _WorkerMessageBudget budget,
) {
  final List<String> strings = value.copyStrings();
  _addWorkerStringRetention(budget, strings.length);
  for (final String string in strings) {
    budget.addUtf8(string);
  }
  return strings;
}

TransferableTypedData _transferWorkerTensor(
  OrtIsolateTensor value,
  _WorkerMessageBudget budget,
) {
  final Uint8List bytes = value.copyBytes();
  budget.addBytes(bytes.length);
  return TransferableTypedData.fromList(<TypedData>[bytes]);
}

void _measureWorkerType(
  OrtTypeInfo type, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  switch (type.kind) {
    case OrtValueKind.tensor:
      budget.addBytes(type.dimensions.length * 8);
      for (final OrtDimension dimension in type.dimensions) {
        if (dimension.symbol case final String symbol) budget.addUtf8(symbol);
      }
    case OrtValueKind.sequence:
      _measureWorkerType(
        type.sequenceElement!,
        budget: budget,
        depth: depth + 1,
      );
    case OrtValueKind.map:
      _measureWorkerType(type.mapValueType!, budget: budget, depth: depth + 1);
    case OrtValueKind.optional:
      _measureWorkerType(
        type.optionalElement!,
        budget: budget,
        depth: depth + 1,
      );
    case OrtValueKind.unknown:
    case OrtValueKind.opaque:
    case OrtValueKind.sparseTensor:
      throw UnsupportedError('Unsupported isolate type ${type.kind.name}.');
  }
}

Map<String, Object?> _encodeWorkerType(
  OrtTypeInfo type, {
  required _WorkerMessageBudget budget,
  required int depth,
}) {
  budget.addNode(depth);
  return switch (type.kind) {
    OrtValueKind.tensor => <String, Object?>{
      'kind': 'tensor',
      'elementType': type.tensorElementType!.nativeValue,
      'hasShape': type.hasShape,
      'dimensions': <Object?>[
        for (final OrtDimension dimension in type.dimensions)
          _encodeWorkerDimension(dimension, budget),
      ],
    },
    OrtValueKind.sequence => <String, Object?>{
      'kind': 'sequence',
      'element': _encodeWorkerType(
        type.sequenceElement!,
        budget: budget,
        depth: depth + 1,
      ),
    },
    OrtValueKind.map => <String, Object?>{
      'kind': 'map',
      'keyElementType': type.mapKeyType!.nativeValue,
      'value': _encodeWorkerType(
        type.mapValueType!,
        budget: budget,
        depth: depth + 1,
      ),
    },
    OrtValueKind.optional => <String, Object?>{
      'kind': 'optional',
      'element': _encodeWorkerType(
        type.optionalElement!,
        budget: budget,
        depth: depth + 1,
      ),
    },
    _ => throw UnsupportedError('Unsupported isolate type ${type.kind.name}.'),
  };
}

Map<String, Object?> _encodeWorkerDimension(
  OrtDimension dimension,
  _WorkerMessageBudget budget,
) {
  budget.addBytes(8);
  if (dimension.symbol case final String symbol) budget.addUtf8(symbol);
  return <String, Object?>{
    'value': dimension.value,
    'symbol': dimension.symbol,
  };
}

OrtTypeInfo _decodeWorkerType(
  Object? raw, {
  required _WorkerMessageBudget budget,
  required int depth,
  required OrtResourceLimits limits,
}) {
  budget.addNode(depth);
  final Map<Object?, Object?> type = _workerMap(raw);
  final String kind = _workerString(type, 'kind');
  switch (kind) {
    case 'tensor':
      _requireWorkerKeys(
        type,
        required: const <String>{
          'kind',
          'elementType',
          'hasShape',
          'dimensions',
        },
      );
      final Object? rawDimensions = type['dimensions'];
      if (rawDimensions is! List<Object?> ||
          rawDimensions.length > limits.maxRank) {
        throw const FormatException('Worker type dimensions are invalid.');
      }
      return OrtTypeInfo.tensor(
        elementType: OrtTensorElementType.fromNativeValue(
          _workerInt(type, 'elementType'),
        ),
        hasShape: _workerBool(type, 'hasShape'),
        dimensions: <OrtDimension>[
          for (final Object? rawDimension in rawDimensions)
            _decodeWorkerDimension(rawDimension, budget),
        ],
        limits: limits,
      );
    case 'sequence':
      _requireWorkerKeys(type, required: const <String>{'kind', 'element'});
      return OrtTypeInfo.sequence(
        _decodeWorkerType(
          type['element'],
          budget: budget,
          depth: depth + 1,
          limits: limits,
        ),
        limits: limits,
      );
    case 'map':
      _requireWorkerKeys(
        type,
        required: const <String>{'kind', 'keyElementType', 'value'},
      );
      return OrtTypeInfo.map(
        keyElementType: OrtTensorElementType.fromNativeValue(
          _workerInt(type, 'keyElementType'),
        ),
        value: _decodeWorkerType(
          type['value'],
          budget: budget,
          depth: depth + 1,
          limits: limits,
        ),
        limits: limits,
      );
    case 'optional':
      _requireWorkerKeys(type, required: const <String>{'kind', 'element'});
      return OrtTypeInfo.optional(
        _decodeWorkerType(
          type['element'],
          budget: budget,
          depth: depth + 1,
          limits: limits,
        ),
        limits: limits,
      );
    default:
      throw const FormatException('Unknown worker type kind.');
  }
}

OrtDimension _decodeWorkerDimension(Object? raw, _WorkerMessageBudget budget) {
  final Map<Object?, Object?> dimension = _workerMap(raw);
  _requireWorkerKeys(dimension, required: const <String>{'value', 'symbol'});
  budget.addBytes(8);
  final Object? rawValue = dimension['value'];
  final Object? rawSymbol = dimension['symbol'];
  if (rawValue is int && rawSymbol == null) {
    return OrtDimension.fixed(rawValue);
  }
  if (rawValue == null && (rawSymbol == null || rawSymbol is String)) {
    if (rawSymbol is String) budget.addUtf8(rawSymbol);
    return OrtDimension.dynamic(rawSymbol as String?);
  }
  throw const FormatException('Worker dimension is inconsistent.');
}

OrtIsolateValue _decodeIsolateValue(
  Object? raw, {
  required _WorkerMessageBudget budget,
  required int depth,
  required OrtResourceLimits limits,
}) {
  budget.addNode(depth);
  final Map<Object?, Object?> value = _workerMap(raw);
  final String kind = _workerString(value, 'kind');
  switch (kind) {
    case 'tensor':
      final OrtTensorElementType elementType =
          OrtTensorElementType.fromNativeValue(
            _workerInt(value, 'elementType'),
          );
      final List<int> shape = _workerIntList(
        value['shape'],
        maximum: limits.maxRank,
      );
      budget.addBytes(shape.length * 8);
      if (elementType == OrtTensorElementType.string) {
        _requireWorkerKeys(
          value,
          required: const <String>{'kind', 'elementType', 'shape', 'strings'},
        );
        final List<String> strings = _workerStringListAllowEmpty(
          value['strings'],
          maximum: limits.maxTensorElements < maximumOrtStringTensorElements
              ? limits.maxTensorElements
              : maximumOrtStringTensorElements,
          budget: budget,
        );
        return OrtIsolateTensor.fromStrings(
          values: strings,
          shape: shape,
          limits: limits,
        );
      }
      _requireWorkerKeys(
        value,
        required: const <String>{'kind', 'elementType', 'shape', 'data'},
      );
      final Uint8List bytes = _materializeWorkerBytes(
        value['data'],
        maximum: budget.maxBytes - budget.bytes,
      );
      budget.addBytes(bytes.length);
      return OrtIsolateTensor.fromBytes(
        elementType: elementType,
        bytes: bytes,
        shape: shape,
        limits: limits,
      );
    case 'sequence':
      _requireWorkerKeys(value, required: const <String>{'kind', 'elements'});
      final Object? rawElements = value['elements'];
      if (rawElements is! List<Object?> ||
          rawElements.isEmpty ||
          rawElements.length > _maximumWorkerCompositeChildren) {
        throw const FormatException('Invalid worker sequence payload.');
      }
      return OrtIsolateSequence(<OrtIsolateValue>[
        for (final Object? child in rawElements)
          _decodeIsolateValue(
            child,
            budget: budget,
            depth: depth + 1,
            limits: limits,
          ),
      ]);
    case 'map':
      _requireWorkerKeys(
        value,
        required: const <String>{'kind', 'keys', 'values'},
      );
      final OrtIsolateValue keys = _decodeIsolateValue(
        value['keys'],
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      final OrtIsolateValue values = _decodeIsolateValue(
        value['values'],
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      if (keys is! OrtIsolateTensor || values is! OrtIsolateTensor) {
        throw const FormatException('Invalid worker map payload.');
      }
      return OrtIsolateMap(keys: keys, values: values);
    case 'optional':
      _requireWorkerKeys(
        value,
        required: const <String>{'kind', 'elementType', 'value'},
      );
      final OrtTypeInfo elementType = _decodeWorkerType(
        value['elementType'],
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      final Object? contained = value['value'];
      if (contained == null) {
        return OrtIsolateOptional.none(elementType: elementType);
      }
      final OrtIsolateValue decoded = _decodeIsolateValue(
        contained,
        budget: budget,
        depth: depth + 1,
        limits: limits,
      );
      if (decoded.type != elementType) {
        throw const FormatException('Optional value type does not match.');
      }
      return OrtIsolateOptional.some(decoded);
    default:
      throw const FormatException('Unknown worker value kind.');
  }
}

List<int> _workerIntList(Object? raw, {required int maximum}) {
  if (raw is! List<Object?> || raw.length > maximum) {
    throw const FormatException('Worker value is not a bounded int list.');
  }
  return List<int>.unmodifiable(<int>[
    for (final Object? value in raw)
      value is int
          ? value
          : throw const FormatException('Worker int list is invalid.'),
  ]);
}

List<String> _workerStringListAllowEmpty(
  Object? raw, {
  required int maximum,
  required _WorkerMessageBudget budget,
}) {
  if (raw is! List<Object?> || raw.length > maximum) {
    throw const FormatException('Worker string tensor list is invalid.');
  }
  _addWorkerStringRetention(budget, raw.length);
  final List<String> result = <String>[];
  for (final Object? value in raw) {
    if (value is! String ||
        value.contains('\u0000') ||
        !hasWellFormedUtf16(value)) {
      throw const FormatException('Worker string tensor text is invalid.');
    }
    budget.addUtf8(value);
    result.add(value);
  }
  return List<String>.unmodifiable(result);
}

OrtDiagnostics _decodeWorkerFullDiagnostics(
  Object? raw,
  OrtResourceLimits limits,
) {
  final Map<Object?, Object?> object = _workerMap(raw);
  try {
    return OrtDiagnostics.fromJsonString(jsonEncode(object), limits: limits);
  } on FormatException {
    rethrow;
  } on Object catch (error) {
    throw FormatException('Worker diagnostics are invalid.', error);
  }
}

OrtProviderRunEvidence? _decodeWorkerProviderEvidence(
  Object? raw,
  _WorkerMessageBudget budget,
) {
  if (raw == null) return null;
  final Map<Object?, Object?> object = _workerMap(raw);
  _requireWorkerKeys(
    object,
    required: const <String>{
      'schemaVersion',
      'nodeExecutionCount',
      'nodeExecutionsByProvider',
    },
  );
  if (_workerInt(object, 'schemaVersion') != 1) {
    throw const FormatException('Worker provider evidence schema is invalid.');
  }
  final int nodeExecutionCount = _workerPositiveInt(
    object,
    'nodeExecutionCount',
  );
  budget.addBytes(16);
  final Map<Object?, Object?> rawCounts = _workerMap(
    object['nodeExecutionsByProvider'],
  );
  if (rawCounts.isEmpty || rawCounts.length > 16) {
    throw const FormatException('Worker provider evidence is out of bounds.');
  }
  final Map<String, int> counts = <String, int>{};
  for (final MapEntry<Object?, Object?> entry in rawCounts.entries) {
    if (entry.key is! String || entry.value is! int) {
      throw const FormatException('Worker provider evidence is malformed.');
    }
    final String id = entry.key! as String;
    budget
      ..addUtf8(id)
      ..addBytes(8);
    counts[id] = entry.value! as int;
  }
  return providerRunEvidenceFromCountsInternal(
    nodeExecutionCount: nodeExecutionCount,
    nodeExecutionsByProvider: counts,
  );
}

List<OrtProviderDiagnostics> _decodeWorkerProviderDiagnostics(
  Object? raw, {
  required OrtProviderRunEvidence? evidence,
  required _WorkerMessageBudget budget,
  required OrtResourceLimits limits,
}) {
  if (raw is! List<Object?> || raw.length > 16) {
    throw const FormatException('Worker provider diagnostics are invalid.');
  }
  final Set<String> seen = <String>{};
  final List<OrtProviderDiagnostics> diagnostics = <OrtProviderDiagnostics>[];
  for (final Object? rawDiagnostic in raw) {
    final Map<Object?, Object?> object = _workerMap(rawDiagnostic);
    _requireWorkerKeys(
      object,
      required: const <String>{
        'wrapperId',
        'registrationMechanism',
        'registrationName',
        'reportedName',
        'compiled',
        'discoverable',
        'registered',
        'active',
        'qualified',
        'assignmentEvidence',
        'fallbackReason',
        'options',
      },
    );
    final String wrapperId = _workerString(object, 'wrapperId');
    final String registration = _workerString(object, 'registrationMechanism');
    if (!RegExp(r'^[a-z][a-z0-9_-]{0,63}$').hasMatch(wrapperId) ||
        !seen.add(wrapperId)) {
      throw const FormatException(
        'Worker provider diagnostics contain an invalid ID.',
      );
    }
    budget
      ..addUtf8(wrapperId)
      ..addUtf8(registration);
    final bool? active = _workerNullableBool(object['active'], 'active');
    if ((evidence == null && active != null) ||
        (evidence != null && active != evidence.isActive(wrapperId))) {
      throw const FormatException(
        'Worker provider diagnostics conflict with assignment evidence.',
      );
    }
    final Map<Object?, Object?> rawOptions = _workerMap(object['options']);
    if (rawOptions.length > limits.maxProviderOptions) {
      throw const FormatException('Worker provider options are out of bounds.');
    }
    final Map<String, String> options = <String, String>{};
    for (final MapEntry<Object?, Object?> entry in rawOptions.entries) {
      if (entry.key is! String || entry.value is! String) {
        throw const FormatException('Worker provider options are malformed.');
      }
      final String key = entry.key! as String;
      final String value = entry.value! as String;
      budget
        ..addUtf8(key)
        ..addUtf8(value);
      options[key] = value;
    }
    final String? registrationName = _workerNullableText(
      object['registrationName'],
      'registrationName',
      budget,
    );
    final String? reportedName = _workerNullableText(
      object['reportedName'],
      'reportedName',
      budget,
    );
    final String? assignmentEvidence = _workerNullableText(
      object['assignmentEvidence'],
      'assignmentEvidence',
      budget,
    );
    final String? fallbackReason = _workerNullableText(
      object['fallbackReason'],
      'fallbackReason',
      budget,
    );
    diagnostics.add(
      OrtProviderDiagnostics(
        wrapperId: wrapperId,
        registrationMechanism: switch (registration) {
          'implicit' => OrtProviderRegistrationMechanism.implicit,
          'generic' => OrtProviderRegistrationMechanism.generic,
          'provider-specific' =>
            OrtProviderRegistrationMechanism.providerSpecific,
          'plugin' => OrtProviderRegistrationMechanism.plugin,
          'unknown' => OrtProviderRegistrationMechanism.unknown,
          _ => throw const FormatException(
            'Worker provider registration mechanism is invalid.',
          ),
        },
        registrationName: registrationName,
        reportedName: reportedName,
        compiled: _workerNullableBool(object['compiled'], 'compiled'),
        discoverable: _workerNullableBool(
          object['discoverable'],
          'discoverable',
        ),
        registered: _workerNullableBool(object['registered'], 'registered'),
        active: active,
        qualified: _workerNullableBool(object['qualified'], 'qualified'),
        options: options,
        assignmentEvidence: assignmentEvidence,
        fallbackReason: fallbackReason,
      ),
    );
  }
  return List<OrtProviderDiagnostics>.unmodifiable(diagnostics);
}

bool? _workerNullableBool(Object? raw, String field) {
  if (raw == null || raw is bool) return raw as bool?;
  throw FormatException('Worker $field must be a boolean or null.');
}

String? _workerNullableText(
  Object? raw,
  String field,
  _WorkerMessageBudget budget,
) {
  if (raw == null) return null;
  if (raw is! String ||
      raw.length > 4096 ||
      raw.contains('\u0000') ||
      !hasWellFormedUtf16(raw)) {
    throw FormatException('Worker $field is invalid.');
  }
  budget.addUtf8(raw);
  return raw;
}

Map<String, Object?> _encodeWorkerOrtError(OrtException error) =>
    <String, Object?>{
      'operation': _boundedWorkerText(error.operation, 'worker_run'),
      'domain': error.domain.name,
      'code': error.code,
      'ortCode': error.ortCode,
      'message': _boundedWorkerText(error.message, 'ONNX Runtime run failed.'),
      'context': _messageSafeWorkerContext(error.context),
    };

OrtException _decodeWorkerOrtError(Object? raw) {
  final Map<Object?, Object?> encoded = _workerMap(raw);
  _requireWorkerKeys(
    encoded,
    required: const <String>{
      'operation',
      'domain',
      'code',
      'ortCode',
      'message',
      'context',
    },
  );
  final String operation = _workerString(encoded, 'operation');
  final OrtErrorDomain domain = _workerEnum(
    OrtErrorDomain.values,
    encoded['domain'],
    'error domain',
  );
  final int code = _workerInt(encoded, 'code');
  final Object? rawOrtCode = encoded['ortCode'];
  if (rawOrtCode != null && rawOrtCode is! int) {
    throw const FormatException('Worker ORT code is invalid.');
  }
  final String message = _workerString(encoded, 'message');
  final Map<String, Object?> context = _decodeWorkerContext(encoded['context']);
  return switch (domain) {
    OrtErrorDomain.loader => OrtRuntimeNotFoundException(
      operation: operation,
      code: code,
      message: message,
      context: context,
    ),
    OrtErrorDomain.ortApi => OrtApiIncompatibleException(
      operation: operation,
      code: code,
      message: message,
      context: context,
    ),
    OrtErrorDomain.provider => OrtProviderUnavailableException(
      operation: operation,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
    OrtErrorDomain.ortStatus => OrtRunException(
      operation: operation,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
    OrtErrorDomain.unsupported => OrtUnsupportedValueException(
      operation: operation,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
    OrtErrorDomain.packaging => OrtNativePackagingException(
      operation: operation,
      code: code,
      message: message,
      context: context,
    ),
    _ => OrtException(
      operation: operation,
      domain: domain,
      code: code,
      message: message,
      ortCode: rawOrtCode as int?,
      context: context,
    ),
  };
}

Map<String, Object?> _messageSafeWorkerContext(Map<String, Object?> context) {
  final Map<String, Object?> result = <String, Object?>{};
  var count = 0;
  for (final MapEntry<String, Object?> entry in context.entries) {
    if (++count > 32) break;
    final Object? value = entry.value;
    if (value == null || value is bool || value is int || value is double) {
      result[entry.key] = value;
    } else if (value is String) {
      result[entry.key] = _boundedWorkerText(value, '<redacted>');
    }
  }
  return result;
}

Map<String, Object?> _decodeWorkerContext(Object? raw) {
  final Map<Object?, Object?> encoded = _workerMap(raw);
  if (encoded.length > 32) {
    throw const FormatException('Worker error context exceeds its bound.');
  }
  final Map<String, Object?> result = <String, Object?>{};
  for (final MapEntry<Object?, Object?> entry in encoded.entries) {
    final Object? value = entry.value;
    if (entry.key is! String ||
        !(value == null ||
            value is bool ||
            value is int ||
            value is double ||
            value is String)) {
      throw const FormatException('Worker error context is not serializable.');
    }
    result[entry.key! as String] = value;
  }
  return result;
}
