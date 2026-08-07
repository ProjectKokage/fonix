import 'dart:collection';
import 'dart:convert';

import 'package:fonix/fonix.dart';
import 'package:test/test.dart';

final class _UnreadableList<T> extends ListBase<T> {
  _UnreadableList(this._length);

  final int _length;
  int elementReads = 0;

  @override
  int get length => _length;

  @override
  set length(int value) => throw UnsupportedError('immutable test list');

  @override
  T operator [](int index) {
    elementReads += 1;
    throw StateError('oversized list elements must not be read');
  }

  @override
  void operator []=(int index, T value) =>
      throw UnsupportedError('immutable test list');
}

void main() {
  group('desktop provider options', () {
    test('CUDA options are closed and canonical', () {
      final OrtExecutionProvider provider = OrtExecutionProvider.cuda(
        deviceId: 2,
        gpuMemoryLimitBytes: 4096,
        arenaExtendStrategy: OrtMemoryArenaExtendStrategy.sameAsRequested,
        convolutionAlgorithmSearch: OrtCudaConvolutionAlgorithmSearch.heuristic,
        copyInDefaultStream: false,
        useMaximumConvolutionWorkspace: false,
        enableCudaGraph: true,
        padConv1dToNc1d: true,
        enableTunableOps: true,
        tuneTunableOps: true,
        maximumTuningDurationMilliseconds: 25,
        preferNhwc: true,
        useUnifiedStream: true,
        useTf32: false,
        fuseConvolutionBias: true,
      );

      expect(provider.id, 'cuda');
      expect(provider.options, <String, String>{
        'device_id': '2',
        'gpu_mem_limit': '4096',
        'arena_extend_strategy': 'kSameAsRequested',
        'cudnn_conv_algo_search': 'HEURISTIC',
        'do_copy_in_default_stream': '0',
        'cudnn_conv_use_max_workspace': '0',
        'enable_cuda_graph': '1',
        'cudnn_conv1d_pad_to_nc1d': '1',
        'tunable_op_enable': '1',
        'tunable_op_tuning_enable': '1',
        'tunable_op_max_tuning_duration_ms': '25',
        'prefer_nhwc': '1',
        'use_ep_level_unified_stream': '1',
        'use_tf32': '0',
        'fuse_conv_bias': '1',
      });
      expect(
        () => OrtExecutionProvider.cuda(tuneTunableOps: true),
        throwsArgumentError,
      );
      expect(
        () => OrtExecutionProvider.cuda(
          enableTunableOps: true,
          maximumTuningDurationMilliseconds: 1,
        ),
        throwsArgumentError,
      );
      expect(() => OrtExecutionProvider.cuda(deviceId: -1), throwsRangeError);
      expect(
        () => OrtExecutionProvider.named(
          'cuda',
          options: const <String, String>{'user_compute_stream': '1234'},
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtExecutionProvider.named(
          'cuda',
          options: const <String, String>{'enable_cuda_graph': 'true'},
        ),
        throwsArgumentError,
      );
    });

    test('TensorRT options and fallback order fail closed', () {
      final OrtExecutionProvider provider = OrtExecutionProvider.tensorRt(
        deviceId: 1,
        maximumPartitionIterations: 200,
        minimumSubgraphSize: 3,
        maximumWorkspaceSizeBytes: 8192,
        enableFp16: true,
        enableBf16: true,
        enableInt8: true,
        enableDla: true,
        dlaCore: 1,
        forceSequentialEngineBuild: true,
        shareContextMemory: true,
        layerNormFp32Fallback: true,
        enableDetailedBuildLog: true,
        enableBuildHeuristics: true,
        enableSparsity: true,
        builderOptimizationLevel: 4,
        auxiliaryStreams: 2,
        enableCudaGraph: true,
        enableHardwareCompatibleEngines: true,
      );
      expect(provider.options, <String, String>{
        'device_id': '1',
        'trt_max_partition_iterations': '200',
        'trt_min_subgraph_size': '3',
        'trt_max_workspace_size': '8192',
        'trt_fp16_enable': '1',
        'trt_bf16_enable': '1',
        'trt_int8_enable': '1',
        'trt_dla_enable': '1',
        'trt_dla_core': '1',
        'trt_force_sequential_engine_build': '1',
        'trt_context_memory_sharing_enable': '1',
        'trt_layer_norm_fp32_fallback': '1',
        'trt_detailed_build_log': '1',
        'trt_build_heuristics_enable': '1',
        'trt_sparsity_enable': '1',
        'trt_builder_optimization_level': '4',
        'trt_auxiliary_streams': '2',
        'trt_cuda_graph_enable': '1',
        'trt_engine_hw_compatible': '1',
      });
      expect(
        () => OrtExecutionProvider.tensorRt(enableDla: true),
        throwsArgumentError,
      );
      expect(
        () => OrtExecutionProvider.tensorRt(dlaCore: 1),
        throwsArgumentError,
      );
      expect(
        () => OrtSessionOptions(
          fallbackPolicy: OrtFallbackPolicy.allow,
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.tensorRt(),
            OrtExecutionProvider.cpu(),
          ],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtSessionOptions(
          fallbackPolicy: OrtFallbackPolicy.allow,
          providers: <OrtExecutionProvider>[
            OrtExecutionProvider.cuda(),
            OrtExecutionProvider.tensorRt(),
          ],
        ),
        throwsArgumentError,
      );
      final OrtSessionOptions valid = OrtSessionOptions(
        fallbackPolicy: OrtFallbackPolicy.allow,
        providers: <OrtExecutionProvider>[
          OrtExecutionProvider.tensorRt(),
          OrtExecutionProvider.cuda(),
          OrtExecutionProvider.cpu(),
        ],
      );
      expect(
        valid.providers.map((OrtExecutionProvider value) => value.id),
        <String>['tensorrt', 'cuda', 'cpu'],
      );
    });

    test('DirectML, OpenVINO, oneDNN, and MIGraphX normalize options', () {
      expect(
        OrtExecutionProvider.directMl(deviceId: 3).options,
        <String, String>{'device_id': '3'},
      );

      final OrtExecutionProvider openVino = OrtExecutionProvider.openVino(
        deviceMode: OrtOpenVinoDeviceMode.hetero,
        devices: const <OrtOpenVinoDevice>[
          OrtOpenVinoDevice.npu,
          OrtOpenVinoDevice.gpu,
          OrtOpenVinoDevice.cpu,
        ],
        numberOfThreads: 4,
        numberOfStreams: 2,
        modelPriority: OrtOpenVinoModelPriority.high,
        enableOpenClThrottling: true,
        enableQdqOptimizer: true,
        disableDynamicShapes: true,
      );
      expect(openVino.options, <String, String>{
        'device_type': 'HETERO:NPU,GPU,CPU',
        'num_of_threads': '4',
        'num_streams': '2',
        'model_priority': 'HIGH',
        'enable_opencl_throttling': 'true',
        'enable_qdq_optimizer': 'true',
        'disable_dynamic_shapes': 'true',
      });
      expect(
        () => OrtExecutionProvider.openVino(
          deviceMode: OrtOpenVinoDeviceMode.single,
          devices: const <OrtOpenVinoDevice>[],
        ),
        throwsArgumentError,
      );
      expect(
        () => OrtExecutionProvider.openVino(
          devices: const <OrtOpenVinoDevice>[OrtOpenVinoDevice.cpu],
          enableOpenClThrottling: true,
        ),
        throwsArgumentError,
      );
      final _UnreadableList<OrtOpenVinoDevice> excessDevices =
          _UnreadableList<OrtOpenVinoDevice>(4);
      expect(
        () => OrtExecutionProvider.openVino(devices: excessDevices),
        throwsArgumentError,
      );
      expect(excessDevices.elementReads, 0);

      expect(
        OrtExecutionProvider.oneDnn(useArena: false).options,
        <String, String>{'use_arena': '0'},
      );
      expect(
        OrtExecutionProvider.miGraphX(
          deviceId: 1,
          enableFp16: true,
          enableFp8: true,
          enableInt8: true,
          exhaustiveTune: true,
          memoryLimitBytes: 16384,
          arenaExtendStrategy: OrtMemoryArenaExtendStrategy.sameAsRequested,
        ).options,
        <String, String>{
          'device_id': '1',
          'migraphx_fp16_enable': '1',
          'migraphx_fp8_enable': '1',
          'migraphx_int8_enable': '1',
          'migraphx_exhaustive_tune': '1',
          'migraphx_mem_limit': '16384',
          'migraphx_arena_extend_strategy': 'kSameAsRequested',
        },
      );
    });

    test('rejects excess providers before reading caller elements', () {
      final _UnreadableList<OrtExecutionProvider> providers =
          _UnreadableList<OrtExecutionProvider>(
            OrtResourceLimits.defaults.maxProviders + 1,
          );
      expect(() => OrtSessionOptions(providers: providers), throwsRangeError);
      expect(providers.elementReads, 0);
    });
  });

  test('desktop provider discovery uses exact ORT-reported identities', () {
    final OrtProviderDiscovery discovery = OrtProviderDiscovery.fromNativeJson(
      jsonEncode(<String, Object?>{
        'schemaVersion': 1,
        'reportedNames': <String>[
          'CUDAExecutionProvider',
          'TensorrtExecutionProvider',
          'DmlExecutionProvider',
          'OpenVINOExecutionProvider',
          'DnnlExecutionProvider',
          'MIGraphXExecutionProvider',
        ],
      }),
    );

    expect(discovery.knownWrapperIds, <String>{
      'cuda',
      'tensorrt',
      'directml',
      'openvino',
      'dnnl',
      'migraphx',
    });
    expect(
      discovery.providers
          .where((OrtDiscoveredProvider value) => value.wrapperId == 'directml')
          .single
          .registrationName,
      'OrtDmlApi.SessionOptionsAppendExecutionProvider_DML',
    );
  });
}
