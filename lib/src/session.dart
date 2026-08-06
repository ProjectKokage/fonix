part of 'runtime.dart';

final class _NativeSessionOptions extends _NativeOwner {
  factory _NativeSessionOptions.create(
    OrtRuntime runtime,
    OrtSessionOptions options, {
    required List<OrtExecutionProvider> resolvedProviders,
  }) {
    try {
      final Pointer<Void> handle = runtime._nativeApi.createSessionOptions(
        runtime._nativeHandle,
        options,
        resolvedProviders: resolvedProviders,
      );
      try {
        return _NativeSessionOptions._(runtime: runtime, handle: handle);
      } catch (_) {
        runtime._nativeApi.releaseSessionOptions(handle);
        rethrow;
      }
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
        privateValues: _sessionPrivateValues(options),
      );
    }
  }

  _NativeSessionOptions._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
  }) : super(
         nativeApi: runtime._nativeApi,
         nativeHandle: handle,
         releaseAddress: runtime._nativeApi.sessionOptionsReleaseAddress,
         release: runtime._nativeApi.releaseSessionOptions,
         debugName: '_NativeSessionOptions',
       );
}

/// A synchronous inference session created separately from every run.
final class OrtSession extends _NativeOwner {
  factory OrtSession.fromBytes({
    required OrtRuntime runtime,
    required Uint8List modelBytes,
    OrtSessionOptions? options,
    String? modelId,
  }) => OrtSession.fromModel(
    runtime: runtime,
    model: OrtModelSource.bytes(
      modelBytes,
      modelId: modelId,
      limits: (options ?? OrtSessionOptions()).limits,
    ),
    options: options,
  );

  factory OrtSession.fromFile({
    required OrtRuntime runtime,
    required String modelPath,
    String? allowedRoot,
    OrtSessionOptions? options,
    String? modelId,
  }) => OrtSession.fromModel(
    runtime: runtime,
    model: OrtModelSource.file(
      absolutePath: modelPath,
      allowedRoot: allowedRoot,
      modelId: modelId,
    ),
    options: options,
  );

  factory OrtSession.fromModel({
    required OrtRuntime runtime,
    required OrtModelSource model,
    OrtSessionOptions? options,
  }) {
    runtime._ensureOpen();
    final OrtSessionOptions effectiveOptions = options ?? OrtSessionOptions();
    _validateSessionPolicy(effectiveOptions);
    final List<OrtExecutionProvider> resolvedProviders =
        _resolveSessionProviders(runtime, effectiveOptions);
    if (model is OrtBytesModelSource &&
        model.totalByteLength > effectiveOptions.limits.maxModelBytes) {
      throw RangeError.range(
        model.totalByteLength,
        1,
        effectiveOptions.limits.maxModelBytes,
        'model.totalByteLength',
      );
    }
    final List<String> privateValues = <String>[
      ..._sessionPrivateValues(effectiveOptions),
      if (model is OrtFileModelSource) model.absolutePath,
      if (model is OrtFileModelSource) model.allowedRoot,
    ];

    final _NativeSessionOptions nativeOptions = _NativeSessionOptions.create(
      runtime,
      effectiveOptions,
      resolvedProviders: resolvedProviders,
    );
    try {
      final Pointer<Void> handle;
      try {
        handle = switch (model) {
          OrtBytesModelSource() when model.hasExternalData =>
            runtime._nativeApi.createSessionFromBytesWithExternalData(
              runtime: runtime._nativeHandle,
              options: nativeOptions._nativeHandle,
              modelBytes: model._modelBytes,
              externalData: model._externalDataBytes,
            ),
          OrtBytesModelSource() => runtime._nativeApi.createSessionFromBytes(
            runtime: runtime._nativeHandle,
            options: nativeOptions._nativeHandle,
            modelBytes: model._modelBytes,
          ),
          OrtFileModelSource() => runtime._nativeApi.createSessionFromFile(
            runtime: runtime._nativeHandle,
            options: nativeOptions._nativeHandle,
            modelPath: model.absolutePath,
            allowedRoot: model.allowedRoot,
          ),
        };
      } on FonixNativeFailure catch (failure) {
        throw _translateNativeFailure(
          failure,
          runtimeSource: runtime._info.runtimeSource,
          requiredApi: OrtApiVersion.v27,
          privateValues: privateValues,
        );
      }

      try {
        final OrtSessionMetadata metadata;
        final OrtModelMetadata modelMetadata;
        try {
          metadata = _parseOrtRecursiveTypeMetadata(
            runtime._nativeApi.getSessionTypeMetadataJson(handle),
            limits: effectiveOptions.limits,
          );
          modelMetadata = _parseOrtModelMetadata(
            runtime._nativeApi.getSessionModelMetadataJson(handle),
            limits: effectiveOptions.limits,
          );
        } on FonixNativeFailure catch (failure) {
          throw _translateNativeFailure(
            failure,
            runtimeSource: runtime._info.runtimeSource,
            requiredApi: OrtApiVersion.v27,
            privateValues: privateValues,
          );
        } on FormatException {
          throw OrtNativePackagingException(
            operation: 'session_metadata_parse',
            code: _nativeErrorPlatform,
            message: 'The native shim returned incompatible session metadata.',
          );
        } on StateError {
          throw OrtNativePackagingException(
            operation: 'session_metadata_copy',
            code: _nativeErrorPlatform,
            message: 'The native shim returned invalid session metadata.',
          );
        }
        return OrtSession._(
          runtime: runtime,
          handle: handle,
          options: effectiveOptions,
          metadata: metadata,
          modelMetadata: modelMetadata,
          modelId: model.modelId,
          privateValues: privateValues,
          registeredProviders: resolvedProviders,
        );
      } catch (_) {
        runtime._nativeApi.releaseSession(handle);
        rethrow;
      }
    } finally {
      nativeOptions.dispose();
    }
  }

  OrtSession._({
    required OrtRuntime runtime,
    required Pointer<Void> handle,
    required OrtSessionOptions options,
    required OrtSessionMetadata metadata,
    required OrtModelMetadata modelMetadata,
    required String? modelId,
    required List<String> privateValues,
    required List<OrtExecutionProvider> registeredProviders,
  }) : _runtime = runtime,
       _options = options,
       _metadata = metadata,
       _modelMetadata = modelMetadata,
       _modelId = modelId,
       _privateValues = List<String>.unmodifiable(privateValues),
       _registeredProviders = List<OrtExecutionProvider>.unmodifiable(
         registeredProviders,
       ),
       super(
         nativeApi: runtime._nativeApi,
         nativeHandle: handle,
         releaseAddress: runtime._nativeApi.sessionReleaseAddress,
         release: runtime._nativeApi.releaseSession,
         debugName: 'OrtSession',
       );

  final OrtRuntime _runtime;
  final OrtSessionOptions _options;
  final OrtSessionMetadata _metadata;
  final OrtModelMetadata _modelMetadata;
  final String? _modelId;
  final List<String> _privateValues;
  final List<OrtExecutionProvider> _registeredProviders;

  OrtSessionOptions get options {
    _ensureOpen();
    return _options;
  }

  OrtSessionMetadata get metadata {
    _ensureOpen();
    return _metadata;
  }

  /// Model metadata copied during session creation.
  ///
  /// This snapshot remains usable after the session is disposed.
  OrtModelMetadata get modelMetadata => _modelMetadata;

  List<OrtValueInfo> get inputs {
    _ensureOpen();
    return _metadata.inputs;
  }

  List<OrtValueInfo> get outputs {
    _ensureOpen();
    return _metadata.outputs;
  }

  String? get modelId {
    _ensureOpen();
    return _modelId;
  }

  /// Immutable provider facts established at session creation.
  ///
  /// Assignment remains null here because a session is not evidence that a
  /// provider executed a node. Each [OrtRunResult] carries its own run-level
  /// evidence and diagnostics snapshot when requested by policy.
  List<OrtProviderDiagnostics> get providerDiagnostics {
    _ensureOpen();
    return buildOrtProviderDiagnostics(
      requestedProviders: _registeredProviders,
      discovery: _runtime._providerDiscovery,
      sessionCreated: true,
      compiledProviders: _runtime._compiledProviders,
    );
  }

  /// Immutable, redacted runtime and session-creation diagnostics.
  ///
  /// Provider assignment remains unknown until a profiled run. Use the
  /// corresponding [OrtRunResult.diagnostics] for run-level active states.
  OrtDiagnostics get diagnostics {
    _ensureOpen();
    return _diagnosticsSnapshot(providerDiagnostics);
  }

  OrtDiagnostics _diagnosticsSnapshot(List<OrtProviderDiagnostics> providers) =>
      _runtime._diagnosticsSnapshot(
        providers: providers,
        modelId: _modelId,
        session: OrtSessionDiagnostics(
          executionMode: _options.executionMode.name,
          graphOptimization: _options.graphOptimization.name,
          intraOpThreads: _options.intraOpThreads,
          interOpThreads: _options.interOpThreads,
          memoryPattern: _options.enableMemoryPattern,
          fallbackPolicy: _options.fallbackPolicy.name,
        ),
      );

  OrtRunResult run({
    required Map<String, OrtValue> inputs,
    List<String>? outputNames,
    OrtRunOptions? runOptions,
  }) {
    _ensureOpen();
    final List<String> requestedOutputs = _validateOutputs(outputNames);
    if (runOptions != null) {
      runOptions._ensureOpen();
      if (!identical(runOptions._runtime, _runtime)) {
        throw ArgumentError(
          'Run options and session belong to different runtime owners.',
        );
      }
    }
    final List<FonixNativeRunInput> nativeInputs = _validateInputs(inputs);
    OrtRunOptions? ownedRunOptions;
    Pointer<Void>? nativeResult;
    var profilingStarted = false;
    var resultOwnershipTransferred = false;
    try {
      final bool needsEvidence = _options.requiresProviderEvidence;
      final OrtRunOptions? effectiveRunOptions;
      if (needsEvidence && runOptions == null) {
        ownedRunOptions = OrtRunOptions(runtime: _runtime);
        effectiveRunOptions = ownedRunOptions;
      } else {
        effectiveRunOptions = runOptions;
      }
      if (needsEvidence) {
        final String? artifactRoot = _options.artifactRoot;
        if (artifactRoot == null || effectiveRunOptions == null) {
          throw StateError(
            'Provider evidence requires owned run options and artifactRoot.',
          );
        }
        _nativeApi.startRunProfiling(
          effectiveRunOptions._nativeHandle,
          artifactRoot,
        );
        profilingStarted = true;
      }
      try {
        nativeResult = _nativeApi.runSession(
          session: _nativeHandle,
          runOptions: effectiveRunOptions?._nativeHandle,
          inputs: nativeInputs,
          outputNames: requestedOutputs,
        );
      } catch (_) {
        if (profilingStarted) {
          try {
            _nativeApi.finishRunProfiling(effectiveRunOptions!._nativeHandle);
          } catch (_) {
            // Preserve the authoritative Run failure. Native finish still
            // retires the private profile directory before returning.
          }
          profilingStarted = false;
        }
        rethrow;
      }
      OrtProviderRunEvidence? evidence;
      if (needsEvidence) {
        final String profileJson = _nativeApi.finishRunProfiling(
          effectiveRunOptions!._nativeHandle,
        );
        profilingStarted = false;
        try {
          evidence = OrtProviderRunEvidence.fromOrtProfileJson(
            profileJson,
            reportedProviderNames:
                _runtime._providerDiscovery.evidenceProviderNames,
          );
        } on FormatException catch (cause) {
          throw OrtProviderEvidenceException(
            message:
                'ONNX Runtime returned malformed or ambiguous provider '
                'assignment evidence.',
            cause: cause,
          );
        }
        evidence.enforce(
          providers: _registeredProviders,
          fallbackPolicy: _options.fallbackPolicy,
        );
      }
      final List<OrtProviderDiagnostics> resultDiagnostics =
          buildOrtProviderDiagnostics(
            requestedProviders: _registeredProviders,
            discovery: _runtime._providerDiscovery,
            sessionCreated: true,
            compiledProviders: _runtime._compiledProviders,
            runEvidence: evidence,
          );
      resultOwnershipTransferred = true;
      return _runResultFromNative(
        session: this,
        handle: nativeResult,
        requestedOutputNames: requestedOutputs,
        providerEvidence: evidence,
        providerDiagnostics: resultDiagnostics,
      );
    } on FonixNativeFailure catch (failure) {
      throw _translateNativeFailure(
        failure,
        runtimeSource: _runtime._info.runtimeSource,
        requiredApi: OrtApiVersion.v27,
        privateValues: _privateValues,
      );
    } finally {
      if (profilingStarted) {
        final OrtRunOptions? effectiveRunOptions =
            runOptions ?? ownedRunOptions;
        if (effectiveRunOptions != null) {
          try {
            _nativeApi.finishRunProfiling(effectiveRunOptions._nativeHandle);
          } catch (_) {
            // The primary failure is authoritative; finish performs cleanup.
          }
        }
      }
      if (nativeResult != null && !resultOwnershipTransferred) {
        _nativeApi.releaseRunResult(nativeResult);
      }
      ownedRunOptions?.dispose();
      for (final FonixNativeRunInput input in nativeInputs) {
        _nativeApi.releaseValue(input.value);
      }
    }
  }

  List<FonixNativeRunInput> _validateInputs(Map<String, OrtValue> supplied) {
    final Set<String> known = <String>{
      for (final OrtValueInfo input in _metadata.inputs) input.name,
    };
    var suppliedCount = 0;
    for (final String name in supplied.keys) {
      suppliedCount += 1;
      if (suppliedCount > _metadata.inputs.length || !known.contains(name)) {
        throw ArgumentError('An unknown or excess named input was supplied.');
      }
    }
    final List<FonixNativeRunInput> result = <FonixNativeRunInput>[];
    try {
      for (final OrtValueInfo expected in _metadata.inputs) {
        final OrtValue? value = supplied[expected.name];
        if (value == null && expected.type.kind == OrtValueKind.optional) {
          continue;
        }
        if (value == null) {
          throw ArgumentError('A required named input is missing.');
        }
        value._ensureValueOpen();
        if (!identical(value._runtime, _runtime)) {
          throw ArgumentError(
            'An input value and session belong to different runtime owners.',
          );
        }
        _validateInputValue(expected.type, value);
        final Pointer<Void> handle = value._nativeHandle;
        _nativeApi.retainValue(handle);
        try {
          result.add(FonixNativeRunInput(name: expected.name, value: handle));
        } catch (_) {
          _nativeApi.releaseValue(handle);
          rethrow;
        }
      }
      return result;
    } catch (_) {
      for (final FonixNativeRunInput input in result) {
        _nativeApi.releaseValue(input.value);
      }
      rethrow;
    }
  }

  List<String> _validateOutputs(List<String>? supplied) {
    final List<String> selected;
    if (supplied == null) {
      selected = <String>[
        for (final OrtValueInfo output in _metadata.outputs) output.name,
      ];
    } else {
      if (supplied.isEmpty || supplied.length > _metadata.outputs.length) {
        throw ArgumentError('At least one known output must be requested.');
      }
      selected = List<String>.generate(
        supplied.length,
        (int index) => supplied[index],
        growable: false,
      );
    }
    if (selected.isEmpty || selected.length > _metadata.outputs.length) {
      throw ArgumentError('At least one known output must be requested.');
    }
    final Set<String> known = <String>{
      for (final OrtValueInfo output in _metadata.outputs) output.name,
    };
    final Set<String> seen = <String>{};
    for (final String name in selected) {
      if (!known.contains(name) || !seen.add(name)) {
        throw ArgumentError('Output names must be known and unique.');
      }
    }
    return List<String>.unmodifiable(selected);
  }
}

void _validateInputValue(OrtTypeInfo expected, OrtValue actual) {
  if (expected.kind == OrtValueKind.optional && actual is! OrtOptional) {
    if (!_valueMatchesMetadata(expected.optionalElement!, actual)) {
      throw ArgumentError('Input value type does not match session metadata.');
    }
    return;
  }
  if (!_valueMatchesMetadata(expected, actual)) {
    throw ArgumentError('Input value type does not match session metadata.');
  }
}

bool _valueMatchesMetadata(OrtTypeInfo expected, OrtValue actual) {
  if (actual.kind != expected.kind) return false;
  return switch (expected.kind) {
    OrtValueKind.tensor => _tensorMatchesMetadata(expected, switch (actual) {
      OrtTensor() => actual._info,
      OrtStringTensor() => actual._info,
      _ => throw StateError('Tensor value has no concrete tensor metadata.'),
    }),
    OrtValueKind.sequence =>
      actual is OrtSequence &&
          _typeInfoCompatible(expected, actual.type) &&
          actual.elements.every(
            (OrtValue child) =>
                _valueMatchesMetadata(expected.sequenceElement!, child),
          ),
    OrtValueKind.map =>
      actual is OrtMap &&
          _typeInfoCompatible(expected, actual.type) &&
          actual.keys.type.tensorElementType == expected.mapKeyType &&
          _valueMatchesMetadata(expected.mapValueType!, actual.values),
    OrtValueKind.optional =>
      actual is OrtOptional &&
          _typeInfoCompatible(expected, actual.type) &&
          (switch (actual.value) {
            final OrtValue value => _valueMatchesMetadata(
              expected.optionalElement!,
              value,
            ),
            null => _typeInfoCompatible(
              expected.optionalElement!,
              actual.type.optionalElement!,
            ),
          }),
    _ => false,
  };
}

bool _typeInfoCompatible(OrtTypeInfo expected, OrtTypeInfo actual) {
  if (expected.kind != actual.kind) return false;
  return switch (expected.kind) {
    OrtValueKind.tensor =>
      expected.tensorElementType == actual.tensorElementType &&
          (!expected.hasShape ||
              (actual.hasShape &&
                  expected.dimensions.length == actual.dimensions.length &&
                  List<bool>.generate(
                    expected.dimensions.length,
                    (int index) =>
                        expected.dimensions[index].value == null ||
                        expected.dimensions[index].value ==
                            actual.dimensions[index].value,
                    growable: false,
                  ).every((bool matches) => matches))),
    OrtValueKind.sequence => _typeInfoCompatible(
      expected.sequenceElement!,
      actual.sequenceElement!,
    ),
    OrtValueKind.map =>
      expected.mapKeyType == actual.mapKeyType &&
          _typeInfoCompatible(expected.mapValueType!, actual.mapValueType!),
    OrtValueKind.optional => _typeInfoCompatible(
      expected.optionalElement!,
      actual.optionalElement!,
    ),
    _ => false,
  };
}

bool _tensorMatchesMetadata(OrtTypeInfo expected, OrtTensorInfo actual) {
  if (expected.kind != OrtValueKind.tensor ||
      expected.tensorElementType != actual.elementType) {
    return false;
  }
  if (!expected.hasShape) return true;
  if (expected.dimensions.length != actual.shape.rank) return false;
  for (var index = 0; index < expected.dimensions.length; index += 1) {
    final int? fixed = expected.dimensions[index].value;
    if (fixed != null && fixed != actual.shape.dimensions[index]) {
      return false;
    }
  }
  return true;
}

void _validateSessionPolicy(OrtSessionOptions options) {
  for (final OrtExecutionProvider provider in options.providers) {
    if (provider.id == 'cpu' && provider.options.isNotEmpty) {
      throw ArgumentError('The implicit CPU provider accepts no options.');
    }
  }
}

List<OrtExecutionProvider> _resolveSessionProviders(
  OrtRuntime runtime,
  OrtSessionOptions options,
) {
  final List<OrtExecutionProvider> resolved = <OrtExecutionProvider>[];
  for (final OrtExecutionProvider provider in options.providers) {
    final OrtCoreMlCacheConfiguration? cache = provider.coreMlCache;
    if (cache == null) {
      resolved.add(provider);
      continue;
    }
    final String? artifactRoot = options.artifactRoot;
    if (provider.id != 'coreml' || artifactRoot == null) {
      throw StateError('A Core ML cache requires its exact artifact root.');
    }
    final OrtNativeArtifactIdentity? artifact = runtime._buildInfo.artifact;
    final String key = coreMlCacheIdentityForTesting(
      buildInfo: runtime._buildInfo.toJson(),
      runtimeVersion: runtime._info.runtimeVersion,
      operatingSystem: artifact?.targetOs ?? Platform.operatingSystem,
      architecture: artifact?.targetArchitecture ?? Abi.current().toString(),
      applicationSchema: cache.applicationSchema,
      modelSha256: cache.modelSha256,
      providerId: provider.id,
      providerOptions: provider.options,
    );
    final String directory = p.join(artifactRoot, key);
    if (utf8.encode(directory).length > 4096) {
      throw ArgumentError('The resolved Core ML cache path is too long.');
    }
    resolved.add(
      resolveOrtCoreMlCacheForInternal(
        provider,
        directory: directory,
        identity: key,
      ),
    );
  }
  return List<OrtExecutionProvider>.unmodifiable(resolved);
}

/// Deterministic package-internal cache-key primitive used by focused tests.
///
/// This is omitted from the primary `package:fonix/fonix.dart` export.
String coreMlCacheIdentityForTesting({
  required Map<String, Object?> buildInfo,
  required String runtimeVersion,
  required String operatingSystem,
  required String architecture,
  required String applicationSchema,
  required String modelSha256,
  required String providerId,
  required Map<String, String> providerOptions,
}) {
  final SplayTreeMap<String, String> normalizedOptions =
      SplayTreeMap<String, String>.of(providerOptions);
  final Map<String, Object?> identity = <String, Object?>{
    'wrapperCacheSchema': 1,
    'applicationSchema': applicationSchema,
    'modelSha256': modelSha256,
    'runtimeVersion': runtimeVersion,
    'buildInfo': buildInfo,
    'target': <String, Object?>{
      'operatingSystem': operatingSystem,
      'architecture': architecture,
    },
    'provider': <String, Object?>{
      'id': providerId,
      'options': normalizedOptions,
    },
  };
  return sha256.convert(utf8.encode(jsonEncode(identity))).toString();
}

List<String> _sessionPrivateValues(OrtSessionOptions options) {
  final List<String> values = <String>[
    if (options.profilePathPrefix case final String value) value,
    if (options.optimizedModelPath case final String value) value,
    if (options.artifactRoot case final String value) value,
  ];
  final RegExp sensitive = RegExp(
    r'(path|directory|cache|token|secret|credential|password|api[_-]?key)',
    caseSensitive: false,
  );
  for (final OrtExecutionProvider provider in options.providers) {
    for (final MapEntry<String, String> entry in provider.options.entries) {
      if (sensitive.hasMatch(entry.key) ||
          p.isAbsolute(entry.value) ||
          entry.value.contains('/') ||
          entry.value.contains(r'\')) {
        values.add(entry.value);
      }
    }
  }
  for (final MapEntry<String, String> entry in options.configEntries.entries) {
    if (sensitive.hasMatch(entry.key) ||
        p.isAbsolute(entry.value) ||
        entry.value.contains('/') ||
        entry.value.contains(r'\')) {
      values.add(entry.value);
    }
  }
  return values;
}
