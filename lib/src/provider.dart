import 'dart:convert';

import 'package:path/path.dart' as p;

import 'resource_limits.dart';

enum OrtProviderRequirement {
  preferred,
  required,
  requireActive,
  requireFullAssignment,
}

enum OrtFallbackPolicy { allow, report, rejectCpu, rejectAny }

/// Core ML graph format selected by the typed provider adapter.
enum OrtCoreMlModelFormat { neuralNetwork, mlProgram }

/// Core ML compute-unit policy.
///
/// This selects what Core ML may use. It is not evidence that a graph or node
/// actually executed on the GPU or Apple Neural Engine.
enum OrtCoreMlComputeUnits { all, cpuOnly, cpuAndGpu, cpuAndNeuralEngine }

/// Legacy Android NNAPI flags.
///
/// NNAPI is opt-in and is never selected by Fonix's default CPU policy.
enum OrtNnapiFlag { useFp16, useNchw, cpuDisabled, cpuOnly }

/// Native-memory arena growth used by CUDA and MIGraphX.
enum OrtMemoryArenaExtendStrategy { nextPowerOfTwo, sameAsRequested }

/// cuDNN convolution algorithm search policy used by CUDA.
enum OrtCudaConvolutionAlgorithmSearch {
  exhaustive,
  heuristic,
  defaultAlgorithm,
}

/// Physical OpenVINO device class.
enum OrtOpenVinoDevice { cpu, gpu, npu }

/// OpenVINO device-selection mode.
enum OrtOpenVinoDeviceMode { single, auto, hetero, multi }

/// OpenVINO model-priority hint.
enum OrtOpenVinoModelPriority { low, medium, high, defaultPriority }

/// Application-approved root and caller-declared model identity for Core ML's
/// compiled-model cache.
///
/// Fonix does not claim to hash an unread model file. [modelSha256] is an exact
/// lowercase digest declared by the caller. At session creation Fonix derives
/// a non-reversible scoped cache key that also includes the actual ORT/runtime
/// build, native artifact identity when available, target OS/architecture,
/// normalized non-path Core ML options, this wrapper's cache schema, and
/// [applicationSchema]. The caller supplies only the root; it cannot choose the
/// final `ModelCacheDirectory` passed to ORT.
final class OrtCoreMlCacheConfiguration {
  factory OrtCoreMlCacheConfiguration({
    required String rootDirectory,
    required String modelSha256,
    String applicationSchema = '1',
  }) {
    _validateText(rootDirectory, 'Core ML cache root', 4096);
    if (!p.isAbsolute(rootDirectory)) {
      throw ArgumentError('Core ML cache root must be absolute.');
    }
    if (!_sha256.hasMatch(modelSha256)) {
      throw ArgumentError(
        'modelSha256 must be an exact lowercase SHA-256 digest.',
      );
    }
    if (!_cacheSchema.hasMatch(applicationSchema)) {
      throw ArgumentError(
        'applicationSchema must be a bounded opaque schema token.',
      );
    }
    return OrtCoreMlCacheConfiguration._(
      p.normalize(rootDirectory),
      modelSha256,
      applicationSchema,
    );
  }

  const OrtCoreMlCacheConfiguration._(
    this.rootDirectory,
    this.modelSha256,
    this.applicationSchema,
  );

  final String rootDirectory;
  final String modelSha256;
  final String applicationSchema;

  static final RegExp _sha256 = RegExp(r'^[0-9a-f]{64}$');
  static final RegExp _cacheSchema = RegExp(
    r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$',
  );
}

/// An ordered, normalized execution-provider request.
final class OrtExecutionProvider {
  factory OrtExecutionProvider.named(
    String id, {
    Map<String, String> options = const <String, String>{},
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
    OrtResourceLimits limits = OrtResourceLimits.defaults,
  }) {
    if (!_idPattern.hasMatch(id)) {
      throw ArgumentError.value(id, 'id', 'is not a stable provider ID');
    }
    if (options.length > limits.maxProviderOptions) {
      throw RangeError.range(
        options.length,
        0,
        limits.maxProviderOptions,
        'options.length',
      );
    }
    final normalized = <String, String>{};
    for (final entry in options.entries) {
      _validateText(entry.key, 'provider option key', 128);
      _validateText(
        entry.value,
        'provider option value',
        4096,
        allowEmpty: true,
      );
      normalized[entry.key] = entry.value;
    }
    _validateClosedProviderOptions(id, normalized);
    if (id == 'coreml' && normalized.containsKey('ModelCacheDirectory')) {
      throw ArgumentError(
        'Core ML ModelCacheDirectory is available only through the typed '
        'cache configuration.',
      );
    }
    return OrtExecutionProvider._(
      id,
      Map<String, String>.unmodifiable(normalized),
      requirement,
    );
  }

  factory OrtExecutionProvider.cpu({
    OrtProviderRequirement requirement = OrtProviderRequirement.required,
  }) => OrtExecutionProvider.named('cpu', requirement: requirement);

  factory OrtExecutionProvider.xnnpack({
    int intraOpThreads = 1,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    if (intraOpThreads < 1 || intraOpThreads > 1024) {
      throw RangeError.range(intraOpThreads, 1, 1024, 'intraOpThreads');
    }
    return OrtExecutionProvider.named(
      'xnnpack',
      options: <String, String>{
        'intra_op_num_threads': intraOpThreads.toString(),
      },
      requirement: requirement,
    );
  }

  factory OrtExecutionProvider.coreMl({
    OrtCoreMlModelFormat modelFormat = OrtCoreMlModelFormat.neuralNetwork,
    OrtCoreMlComputeUnits computeUnits = OrtCoreMlComputeUnits.all,
    bool requireStaticInputShapes = false,
    bool enableOnSubgraphs = false,
    OrtCoreMlCacheConfiguration? cache,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    final OrtExecutionProvider normalized = OrtExecutionProvider.named(
      'coreml',
      options: <String, String>{
        'ModelFormat': switch (modelFormat) {
          OrtCoreMlModelFormat.neuralNetwork => 'NeuralNetwork',
          OrtCoreMlModelFormat.mlProgram => 'MLProgram',
        },
        'MLComputeUnits': switch (computeUnits) {
          OrtCoreMlComputeUnits.all => 'ALL',
          OrtCoreMlComputeUnits.cpuOnly => 'CPUOnly',
          OrtCoreMlComputeUnits.cpuAndGpu => 'CPUAndGPU',
          OrtCoreMlComputeUnits.cpuAndNeuralEngine => 'CPUAndNeuralEngine',
        },
        'RequireStaticInputShapes': requireStaticInputShapes ? '1' : '0',
        'EnableOnSubgraphs': enableOnSubgraphs ? '1' : '0',
      },
      requirement: requirement,
    );
    return OrtExecutionProvider._(
      normalized.id,
      normalized.options,
      normalized.requirement,
      coreMlCache: cache,
    );
  }

  /// Configures Android's deprecated NNAPI compatibility provider.
  ///
  /// The selected runtime must contain NNAPI support. In particular, this
  /// constructor does not make a standalone CPU artifact NNAPI-capable.
  factory OrtExecutionProvider.nnapi({
    Set<OrtNnapiFlag> flags = const <OrtNnapiFlag>{},
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    if (flags.contains(OrtNnapiFlag.cpuDisabled) &&
        flags.contains(OrtNnapiFlag.cpuOnly)) {
      throw ArgumentError(
        'NNAPI cpuDisabled and cpuOnly flags are mutually exclusive.',
      );
    }
    var bits = 0;
    for (final OrtNnapiFlag flag in flags) {
      bits |= switch (flag) {
        OrtNnapiFlag.useFp16 => 0x001,
        OrtNnapiFlag.useNchw => 0x002,
        OrtNnapiFlag.cpuDisabled => 0x004,
        OrtNnapiFlag.cpuOnly => 0x008,
      };
    }
    return OrtExecutionProvider.named(
      'nnapi',
      options: <String, String>{'flags': bits.toString()},
      requirement: requirement,
    );
  }

  factory OrtExecutionProvider.qnn({
    Map<String, String> options = const <String, String>{},
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) => OrtExecutionProvider.named(
    'qnn',
    options: options,
    requirement: requirement,
  );

  factory OrtExecutionProvider.cuda({
    int deviceId = 0,
    int? gpuMemoryLimitBytes,
    OrtMemoryArenaExtendStrategy arenaExtendStrategy =
        OrtMemoryArenaExtendStrategy.nextPowerOfTwo,
    OrtCudaConvolutionAlgorithmSearch convolutionAlgorithmSearch =
        OrtCudaConvolutionAlgorithmSearch.exhaustive,
    bool copyInDefaultStream = true,
    bool useMaximumConvolutionWorkspace = true,
    bool enableCudaGraph = false,
    bool padConv1dToNc1d = false,
    bool enableTunableOps = false,
    bool tuneTunableOps = false,
    int maximumTuningDurationMilliseconds = 0,
    bool preferNhwc = false,
    bool useUnifiedStream = false,
    bool useTf32 = true,
    bool fuseConvolutionBias = false,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    _nonNegativeInt32(deviceId, 'deviceId');
    if (gpuMemoryLimitBytes != null) {
      _positiveInt64(gpuMemoryLimitBytes, 'gpuMemoryLimitBytes');
    }
    _nonNegativeInt32(
      maximumTuningDurationMilliseconds,
      'maximumTuningDurationMilliseconds',
    );
    if (tuneTunableOps && !enableTunableOps) {
      throw ArgumentError('Tunable-op tuning requires tunable ops enabled.');
    }
    if (maximumTuningDurationMilliseconds != 0 && !tuneTunableOps) {
      throw ArgumentError(
        'A tunable-op duration requires tunable-op tuning enabled.',
      );
    }
    return OrtExecutionProvider.named(
      'cuda',
      options: <String, String>{
        'device_id': deviceId.toString(),
        if (gpuMemoryLimitBytes != null)
          'gpu_mem_limit': gpuMemoryLimitBytes.toString(),
        'arena_extend_strategy': _arenaExtendStrategy(arenaExtendStrategy),
        'cudnn_conv_algo_search': switch (convolutionAlgorithmSearch) {
          OrtCudaConvolutionAlgorithmSearch.exhaustive => 'EXHAUSTIVE',
          OrtCudaConvolutionAlgorithmSearch.heuristic => 'HEURISTIC',
          OrtCudaConvolutionAlgorithmSearch.defaultAlgorithm => 'DEFAULT',
        },
        'do_copy_in_default_stream': _ortBool(copyInDefaultStream),
        'cudnn_conv_use_max_workspace': _ortBool(
          useMaximumConvolutionWorkspace,
        ),
        'enable_cuda_graph': _ortBool(enableCudaGraph),
        'cudnn_conv1d_pad_to_nc1d': _ortBool(padConv1dToNc1d),
        'tunable_op_enable': _ortBool(enableTunableOps),
        'tunable_op_tuning_enable': _ortBool(tuneTunableOps),
        'tunable_op_max_tuning_duration_ms': maximumTuningDurationMilliseconds
            .toString(),
        'prefer_nhwc': _ortBool(preferNhwc),
        'use_ep_level_unified_stream': _ortBool(useUnifiedStream),
        'use_tf32': _ortBool(useTf32),
        'fuse_conv_bias': _ortBool(fuseConvolutionBias),
      },
      requirement: requirement,
    );
  }

  factory OrtExecutionProvider.tensorRt({
    int deviceId = 0,
    int maximumPartitionIterations = 1000,
    int minimumSubgraphSize = 1,
    int maximumWorkspaceSizeBytes = 0,
    bool enableFp16 = false,
    bool enableBf16 = false,
    bool enableInt8 = false,
    bool enableDla = false,
    int dlaCore = 0,
    bool forceSequentialEngineBuild = false,
    bool shareContextMemory = false,
    bool layerNormFp32Fallback = false,
    bool enableDetailedBuildLog = false,
    bool enableBuildHeuristics = false,
    bool enableSparsity = false,
    int builderOptimizationLevel = 3,
    int auxiliaryStreams = -1,
    bool enableCudaGraph = false,
    bool enableHardwareCompatibleEngines = false,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    _nonNegativeInt32(deviceId, 'deviceId');
    _positiveInt32(maximumPartitionIterations, 'maximumPartitionIterations');
    _positiveInt32(minimumSubgraphSize, 'minimumSubgraphSize');
    _nonNegativeInt64(maximumWorkspaceSizeBytes, 'maximumWorkspaceSizeBytes');
    if (dlaCore < 0 || dlaCore > 31) {
      throw RangeError.range(dlaCore, 0, 31, 'dlaCore');
    }
    if (!enableDla && dlaCore != 0) {
      throw ArgumentError('A nonzero DLA core requires DLA enabled.');
    }
    if (enableDla && !enableFp16 && !enableInt8) {
      throw ArgumentError('TensorRT DLA requires FP16 or INT8 enabled.');
    }
    if (builderOptimizationLevel < 0 || builderOptimizationLevel > 5) {
      throw RangeError.range(
        builderOptimizationLevel,
        0,
        5,
        'builderOptimizationLevel',
      );
    }
    if (auxiliaryStreams < -1 || auxiliaryStreams > 32) {
      throw RangeError.range(auxiliaryStreams, -1, 32, 'auxiliaryStreams');
    }
    return OrtExecutionProvider.named(
      'tensorrt',
      options: <String, String>{
        'device_id': deviceId.toString(),
        'trt_max_partition_iterations': maximumPartitionIterations.toString(),
        'trt_min_subgraph_size': minimumSubgraphSize.toString(),
        'trt_max_workspace_size': maximumWorkspaceSizeBytes.toString(),
        'trt_fp16_enable': _ortBool(enableFp16),
        'trt_bf16_enable': _ortBool(enableBf16),
        'trt_int8_enable': _ortBool(enableInt8),
        'trt_dla_enable': _ortBool(enableDla),
        'trt_dla_core': dlaCore.toString(),
        'trt_force_sequential_engine_build': _ortBool(
          forceSequentialEngineBuild,
        ),
        'trt_context_memory_sharing_enable': _ortBool(shareContextMemory),
        'trt_layer_norm_fp32_fallback': _ortBool(layerNormFp32Fallback),
        'trt_detailed_build_log': _ortBool(enableDetailedBuildLog),
        'trt_build_heuristics_enable': _ortBool(enableBuildHeuristics),
        'trt_sparsity_enable': _ortBool(enableSparsity),
        'trt_builder_optimization_level': builderOptimizationLevel.toString(),
        'trt_auxiliary_streams': auxiliaryStreams.toString(),
        'trt_cuda_graph_enable': _ortBool(enableCudaGraph),
        'trt_engine_hw_compatible': _ortBool(enableHardwareCompatibleEngines),
      },
      requirement: requirement,
    );
  }

  factory OrtExecutionProvider.directMl({
    int deviceId = 0,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    _nonNegativeInt32(deviceId, 'deviceId');
    return OrtExecutionProvider.named(
      'directml',
      options: <String, String>{'device_id': deviceId.toString()},
      requirement: requirement,
    );
  }

  factory OrtExecutionProvider.openVino({
    OrtOpenVinoDeviceMode deviceMode = OrtOpenVinoDeviceMode.single,
    List<OrtOpenVinoDevice> devices = const <OrtOpenVinoDevice>[
      OrtOpenVinoDevice.cpu,
    ],
    int numberOfThreads = 1,
    int numberOfStreams = 1,
    OrtOpenVinoModelPriority modelPriority =
        OrtOpenVinoModelPriority.defaultPriority,
    bool enableOpenClThrottling = false,
    bool enableQdqOptimizer = false,
    bool disableDynamicShapes = false,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    final List<OrtOpenVinoDevice> copiedDevices = List<OrtOpenVinoDevice>.of(
      devices,
    );
    if (copiedDevices.toSet().length != copiedDevices.length) {
      throw ArgumentError('OpenVINO device selection contains duplicates.');
    }
    switch (deviceMode) {
      case OrtOpenVinoDeviceMode.single:
        if (copiedDevices.length != 1) {
          throw ArgumentError(
            'OpenVINO single-device mode requires exactly one device.',
          );
        }
        break;
      case OrtOpenVinoDeviceMode.auto:
        break;
      case OrtOpenVinoDeviceMode.hetero:
      case OrtOpenVinoDeviceMode.multi:
        if (copiedDevices.isEmpty) {
          throw ArgumentError(
            'OpenVINO HETERO and MULTI modes require at least one device.',
          );
        }
    }
    _positiveInt32(numberOfThreads, 'numberOfThreads');
    _positiveInt32(numberOfStreams, 'numberOfStreams');
    if (enableOpenClThrottling &&
        !copiedDevices.contains(OrtOpenVinoDevice.gpu)) {
      throw ArgumentError('OpenCL throttling requires an OpenVINO GPU.');
    }
    if (enableQdqOptimizer && !copiedDevices.contains(OrtOpenVinoDevice.npu)) {
      throw ArgumentError('The OpenVINO QDQ optimizer requires an NPU.');
    }
    final String deviceType = _openVinoDeviceType(deviceMode, copiedDevices);
    return OrtExecutionProvider.named(
      'openvino',
      options: <String, String>{
        'device_type': deviceType,
        'num_of_threads': numberOfThreads.toString(),
        'num_streams': numberOfStreams.toString(),
        'model_priority': switch (modelPriority) {
          OrtOpenVinoModelPriority.low => 'LOW',
          OrtOpenVinoModelPriority.medium => 'MEDIUM',
          OrtOpenVinoModelPriority.high => 'HIGH',
          OrtOpenVinoModelPriority.defaultPriority => 'DEFAULT',
        },
        'enable_opencl_throttling': enableOpenClThrottling ? 'true' : 'false',
        'enable_qdq_optimizer': enableQdqOptimizer ? 'true' : 'false',
        'disable_dynamic_shapes': disableDynamicShapes ? 'true' : 'false',
      },
      requirement: requirement,
    );
  }

  /// Configures the oneDNN execution provider (reported by ORT as DNNL).
  factory OrtExecutionProvider.oneDnn({
    bool useArena = true,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) => OrtExecutionProvider.named(
    'dnnl',
    options: <String, String>{'use_arena': _ortBool(useArena)},
    requirement: requirement,
  );

  factory OrtExecutionProvider.miGraphX({
    int deviceId = 0,
    bool enableFp16 = false,
    bool enableFp8 = false,
    bool enableInt8 = false,
    bool exhaustiveTune = false,
    int? memoryLimitBytes,
    OrtMemoryArenaExtendStrategy arenaExtendStrategy =
        OrtMemoryArenaExtendStrategy.nextPowerOfTwo,
    OrtProviderRequirement requirement = OrtProviderRequirement.preferred,
  }) {
    _nonNegativeInt32(deviceId, 'deviceId');
    if (memoryLimitBytes != null) {
      _positiveInt64(memoryLimitBytes, 'memoryLimitBytes');
    }
    return OrtExecutionProvider.named(
      'migraphx',
      options: <String, String>{
        'device_id': deviceId.toString(),
        'migraphx_fp16_enable': _ortBool(enableFp16),
        'migraphx_fp8_enable': _ortBool(enableFp8),
        'migraphx_int8_enable': _ortBool(enableInt8),
        'migraphx_exhaustive_tune': _ortBool(exhaustiveTune),
        if (memoryLimitBytes != null)
          'migraphx_mem_limit': memoryLimitBytes.toString(),
        'migraphx_arena_extend_strategy': _arenaExtendStrategy(
          arenaExtendStrategy,
        ),
      },
      requirement: requirement,
    );
  }

  const OrtExecutionProvider._(
    this.id,
    this.options,
    this.requirement, {
    this.coreMlCache,
    this.coreMlCacheIdentity,
  });

  static final RegExp _idPattern = RegExp(r'^[a-z][a-z0-9_-]{0,63}$');

  final String id;
  final Map<String, String> options;
  final OrtProviderRequirement requirement;
  final OrtCoreMlCacheConfiguration? coreMlCache;

  /// Non-reversible scoped identity after session-time cache resolution.
  ///
  /// Null on caller-created provider values and providers without a cache.
  final String? coreMlCacheIdentity;

  /// Returns options safe for default diagnostics.
  Map<String, String> get redactedOptions => Map<String, String>.unmodifiable({
    for (final entry in options.entries)
      entry.key: _sensitiveOption.hasMatch(entry.key)
          ? '<redacted>'
          : entry.value,
    if (coreMlCacheIdentity case final String identity)
      'fonix_cache_identity': identity,
  });

  static final RegExp _sensitiveOption = RegExp(
    r'(path|directory|cache|token|secret|credential|password|api[_-]?key)',
    caseSensitive: false,
  );
  static final RegExp _sha256 = RegExp(r'^[0-9a-f]{64}$');
}

/// Creates the native-only resolved Core ML request.
///
/// This package-internal helper is omitted from the primary library export.
OrtExecutionProvider resolveOrtCoreMlCacheForInternal(
  OrtExecutionProvider provider, {
  required String directory,
  required String identity,
}) {
  if (provider.id != 'coreml' || provider.coreMlCache == null) {
    throw StateError('Only a typed Core ML cache request can be resolved.');
  }
  if (!p.isAbsolute(directory) ||
      !OrtExecutionProvider._sha256.hasMatch(identity)) {
    throw ArgumentError('Resolved Core ML cache identity is invalid.');
  }
  return OrtExecutionProvider._(
    provider.id,
    Map<String, String>.unmodifiable(<String, String>{
      ...provider.options,
      'ModelCacheDirectory': p.normalize(directory),
    }),
    provider.requirement,
    coreMlCache: provider.coreMlCache,
    coreMlCacheIdentity: identity,
  );
}

void _validateText(
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

void _validateClosedProviderOptions(String id, Map<String, String> options) {
  switch (id) {
    case 'xnnpack':
      _requireClosedKeys(id, options, const <String>{'intra_op_num_threads'});
      if (options.length != 1) {
        _closedProviderError(id);
      }
      _optionalClosedInt(options, 'intra_op_num_threads', 1, 1024);
      break;
    case 'cuda':
      _requireClosedKeys(id, options, _cudaOptionKeys);
      _optionalClosedInt(options, 'device_id', 0, 0x7fffffff);
      _optionalClosedInt(options, 'gpu_mem_limit', 1, 0x7fffffffffffffff);
      _optionalClosedEnum(options, 'arena_extend_strategy', const <String>{
        'kNextPowerOfTwo',
        'kSameAsRequested',
      });
      _optionalClosedEnum(options, 'cudnn_conv_algo_search', const <String>{
        'EXHAUSTIVE',
        'HEURISTIC',
        'DEFAULT',
      });
      for (final String key in _cudaBooleanKeys) {
        _optionalClosedEnum(options, key, const <String>{'0', '1'});
      }
      _optionalClosedInt(
        options,
        'tunable_op_max_tuning_duration_ms',
        0,
        0x7fffffff,
      );
      final bool tunable = options['tunable_op_enable'] == '1';
      final bool tuning = options['tunable_op_tuning_enable'] == '1';
      final int duration =
          int.tryParse(options['tunable_op_max_tuning_duration_ms'] ?? '0') ??
          -1;
      if ((tuning && !tunable) || (duration != 0 && !tuning)) {
        _closedProviderError(id);
      }
      break;
    case 'tensorrt':
      _requireClosedKeys(id, options, _tensorRtOptionKeys);
      _optionalClosedInt(options, 'device_id', 0, 0x7fffffff);
      _optionalClosedInt(
        options,
        'trt_max_partition_iterations',
        1,
        0x7fffffff,
      );
      _optionalClosedInt(options, 'trt_min_subgraph_size', 1, 0x7fffffff);
      _optionalClosedInt(
        options,
        'trt_max_workspace_size',
        0,
        0x7fffffffffffffff,
      );
      for (final String key in _tensorRtBooleanKeys) {
        _optionalClosedEnum(options, key, const <String>{'0', '1'});
      }
      _optionalClosedInt(options, 'trt_dla_core', 0, 31);
      _optionalClosedInt(options, 'trt_builder_optimization_level', 0, 5);
      _optionalClosedInt(options, 'trt_auxiliary_streams', -1, 32);
      final bool dla = options['trt_dla_enable'] == '1';
      final bool fp16 = options['trt_fp16_enable'] == '1';
      final bool int8 = options['trt_int8_enable'] == '1';
      final int dlaCore = int.tryParse(options['trt_dla_core'] ?? '0') ?? -1;
      if ((!dla && dlaCore != 0) || (dla && !fp16 && !int8)) {
        _closedProviderError(id);
      }
      break;
    case 'directml':
      _requireClosedKeys(id, options, const <String>{'device_id'});
      if (options.length != 1) {
        _closedProviderError(id);
      }
      _optionalClosedInt(options, 'device_id', 0, 0x7fffffff);
      break;
    case 'openvino':
      _requireClosedKeys(id, options, _openVinoOptionKeys);
      final String? deviceType = options['device_type'];
      final Set<String> devices = deviceType == null
          ? const <String>{}
          : _openVinoDevices(deviceType, id);
      _optionalClosedInt(options, 'num_of_threads', 1, 0x7fffffff);
      _optionalClosedInt(options, 'num_streams', 1, 0x7fffffff);
      _optionalClosedEnum(options, 'model_priority', const <String>{
        'LOW',
        'MEDIUM',
        'HIGH',
        'DEFAULT',
      });
      for (final String key in _openVinoBooleanKeys) {
        _optionalClosedEnum(options, key, const <String>{'true', 'false'});
      }
      if ((options['enable_opencl_throttling'] == 'true' &&
              !devices.contains('GPU')) ||
          (options['enable_qdq_optimizer'] == 'true' &&
              !devices.contains('NPU'))) {
        _closedProviderError(id);
      }
      break;
    case 'dnnl':
      _requireClosedKeys(id, options, const <String>{'use_arena'});
      if (options.length != 1) {
        _closedProviderError(id);
      }
      _optionalClosedEnum(options, 'use_arena', const <String>{'0', '1'});
      break;
    case 'migraphx':
      _requireClosedKeys(id, options, _miGraphXOptionKeys);
      _optionalClosedInt(options, 'device_id', 0, 0x7fffffff);
      for (final String key in _miGraphXBooleanKeys) {
        _optionalClosedEnum(options, key, const <String>{'0', '1'});
      }
      _optionalClosedInt(options, 'migraphx_mem_limit', 1, 0x7fffffffffffffff);
      _optionalClosedEnum(
        options,
        'migraphx_arena_extend_strategy',
        const <String>{'kNextPowerOfTwo', 'kSameAsRequested'},
      );
      break;
  }
}

void _requireClosedKeys(
  String id,
  Map<String, String> options,
  Set<String> allowed,
) {
  if (options.keys.any((String key) => !allowed.contains(key))) {
    _closedProviderError(id);
  }
}

void _optionalClosedInt(
  Map<String, String> options,
  String key,
  int minimum,
  int maximum,
) {
  final String? value = options[key];
  if (value == null) {
    return;
  }
  final int? parsed = int.tryParse(value);
  if (parsed == null ||
      parsed.toString() != value ||
      parsed < minimum ||
      parsed > maximum) {
    _closedProviderValueError();
  }
}

void _optionalClosedEnum(
  Map<String, String> options,
  String key,
  Set<String> allowed,
) {
  final String? value = options[key];
  if (value != null && !allowed.contains(value)) {
    _closedProviderValueError();
  }
}

Set<String> _openVinoDevices(String deviceType, String id) {
  String deviceList = deviceType;
  var hasModePrefix = false;
  if (deviceType == 'AUTO') {
    return const <String>{};
  }
  for (final String prefix in const <String>['AUTO:', 'HETERO:', 'MULTI:']) {
    if (deviceType.startsWith(prefix)) {
      deviceList = deviceType.substring(prefix.length);
      hasModePrefix = true;
      break;
    }
  }
  final List<String> devices = deviceList.split(',');
  const Set<String> allowed = <String>{'CPU', 'GPU', 'NPU'};
  if (devices.isEmpty ||
      devices.any((String value) => !allowed.contains(value)) ||
      devices.toSet().length != devices.length ||
      (!hasModePrefix && devices.length != 1) ||
      (deviceType.contains(':') &&
          !const <String>[
            'AUTO:',
            'HETERO:',
            'MULTI:',
          ].any(deviceType.startsWith))) {
    _closedProviderError(id);
  }
  return devices.toSet();
}

Never _closedProviderError(String id) {
  throw ArgumentError("Provider '$id' options are outside its closed domain.");
}

Never _closedProviderValueError() {
  throw ArgumentError('Provider option value is not canonical.');
}

const Set<String> _cudaOptionKeys = <String>{
  'device_id',
  'gpu_mem_limit',
  'arena_extend_strategy',
  'cudnn_conv_algo_search',
  'do_copy_in_default_stream',
  'cudnn_conv_use_max_workspace',
  'enable_cuda_graph',
  'cudnn_conv1d_pad_to_nc1d',
  'tunable_op_enable',
  'tunable_op_tuning_enable',
  'tunable_op_max_tuning_duration_ms',
  'prefer_nhwc',
  'use_ep_level_unified_stream',
  'use_tf32',
  'fuse_conv_bias',
};

const Set<String> _cudaBooleanKeys = <String>{
  'do_copy_in_default_stream',
  'cudnn_conv_use_max_workspace',
  'enable_cuda_graph',
  'cudnn_conv1d_pad_to_nc1d',
  'tunable_op_enable',
  'tunable_op_tuning_enable',
  'prefer_nhwc',
  'use_ep_level_unified_stream',
  'use_tf32',
  'fuse_conv_bias',
};

const Set<String> _tensorRtOptionKeys = <String>{
  'device_id',
  'trt_max_partition_iterations',
  'trt_min_subgraph_size',
  'trt_max_workspace_size',
  'trt_fp16_enable',
  'trt_bf16_enable',
  'trt_int8_enable',
  'trt_dla_enable',
  'trt_dla_core',
  'trt_force_sequential_engine_build',
  'trt_context_memory_sharing_enable',
  'trt_layer_norm_fp32_fallback',
  'trt_detailed_build_log',
  'trt_build_heuristics_enable',
  'trt_sparsity_enable',
  'trt_builder_optimization_level',
  'trt_auxiliary_streams',
  'trt_cuda_graph_enable',
  'trt_engine_hw_compatible',
};

const Set<String> _tensorRtBooleanKeys = <String>{
  'trt_fp16_enable',
  'trt_bf16_enable',
  'trt_int8_enable',
  'trt_dla_enable',
  'trt_force_sequential_engine_build',
  'trt_context_memory_sharing_enable',
  'trt_layer_norm_fp32_fallback',
  'trt_detailed_build_log',
  'trt_build_heuristics_enable',
  'trt_sparsity_enable',
  'trt_cuda_graph_enable',
  'trt_engine_hw_compatible',
};

const Set<String> _openVinoOptionKeys = <String>{
  'device_type',
  'num_of_threads',
  'num_streams',
  'model_priority',
  'enable_opencl_throttling',
  'enable_qdq_optimizer',
  'disable_dynamic_shapes',
};

const Set<String> _openVinoBooleanKeys = <String>{
  'enable_opencl_throttling',
  'enable_qdq_optimizer',
  'disable_dynamic_shapes',
};

const Set<String> _miGraphXOptionKeys = <String>{
  'device_id',
  'migraphx_fp16_enable',
  'migraphx_fp8_enable',
  'migraphx_int8_enable',
  'migraphx_exhaustive_tune',
  'migraphx_mem_limit',
  'migraphx_arena_extend_strategy',
};

const Set<String> _miGraphXBooleanKeys = <String>{
  'migraphx_fp16_enable',
  'migraphx_fp8_enable',
  'migraphx_int8_enable',
  'migraphx_exhaustive_tune',
};

String _ortBool(bool value) => value ? '1' : '0';

String _arenaExtendStrategy(OrtMemoryArenaExtendStrategy value) =>
    switch (value) {
      OrtMemoryArenaExtendStrategy.nextPowerOfTwo => 'kNextPowerOfTwo',
      OrtMemoryArenaExtendStrategy.sameAsRequested => 'kSameAsRequested',
    };

String _openVinoDeviceType(
  OrtOpenVinoDeviceMode mode,
  List<OrtOpenVinoDevice> devices,
) {
  final String deviceList = devices
      .map(
        (OrtOpenVinoDevice device) => switch (device) {
          OrtOpenVinoDevice.cpu => 'CPU',
          OrtOpenVinoDevice.gpu => 'GPU',
          OrtOpenVinoDevice.npu => 'NPU',
        },
      )
      .join(',');
  return switch (mode) {
    OrtOpenVinoDeviceMode.single => deviceList,
    OrtOpenVinoDeviceMode.auto =>
      deviceList.isEmpty ? 'AUTO' : 'AUTO:$deviceList',
    OrtOpenVinoDeviceMode.hetero => 'HETERO:$deviceList',
    OrtOpenVinoDeviceMode.multi => 'MULTI:$deviceList',
  };
}

void _positiveInt32(int value, String name) {
  if (value < 1 || value > 0x7fffffff) {
    throw RangeError.range(value, 1, 0x7fffffff, name);
  }
}

void _nonNegativeInt32(int value, String name) {
  if (value < 0 || value > 0x7fffffff) {
    throw RangeError.range(value, 0, 0x7fffffff, name);
  }
}

void _positiveInt64(int value, String name) {
  if (value < 1 || value > 0x7fffffffffffffff) {
    throw RangeError.range(value, 1, 0x7fffffffffffffff, name);
  }
}

void _nonNegativeInt64(int value, String name) {
  if (value < 0 || value > 0x7fffffffffffffff) {
    throw RangeError.range(value, 0, 0x7fffffffffffffff, name);
  }
}
