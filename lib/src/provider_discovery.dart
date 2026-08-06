import 'dart:convert';

import 'diagnostics.dart';
import 'provider.dart';
import 'provider_evidence.dart';
import 'strict_json.dart';

const int _maximumProviderDiscoveryBytes = 64 * 1024;
const int _maximumDiscoveredProviders = 16;

/// One provider name reported by the opened ONNX Runtime.
///
/// [wrapperId] is null when the runtime reports a provider that Fonix's closed
/// API-27 adapter does not recognize. Such providers remain visible but cannot
/// be configured or accepted as assignment evidence by inference APIs.
final class OrtDiscoveredProvider {
  const OrtDiscoveredProvider._({
    required this.reportedName,
    required this.wrapperId,
    required this.registrationMechanism,
    required this.registrationName,
  });

  final String reportedName;
  final String? wrapperId;
  final OrtProviderRegistrationMechanism registrationMechanism;
  final String? registrationName;

  bool get isKnownToFonix => wrapperId != null;

  Map<String, Object?> toJson() => <String, Object?>{
    'reportedName': reportedName,
    'wrapperId': wrapperId,
    'registrationMechanism': switch (registrationMechanism) {
      OrtProviderRegistrationMechanism.implicit => 'implicit',
      OrtProviderRegistrationMechanism.generic => 'generic',
      OrtProviderRegistrationMechanism.providerSpecific => 'provider-specific',
      OrtProviderRegistrationMechanism.plugin => 'plugin',
      OrtProviderRegistrationMechanism.unknown => 'unknown',
    },
    'registrationName': registrationName,
  };
}

/// Bounded provider discovery copied from one negotiated runtime identity.
///
/// ONNX Runtime explicitly does not guarantee that a provider returned by its
/// availability API can load all system dependencies. This snapshot therefore
/// proves discoverability only; session registration and run assignment are
/// tracked separately.
final class OrtProviderDiscovery {
  OrtProviderDiscovery._(List<OrtDiscoveredProvider> providers)
    : providers = List<OrtDiscoveredProvider>.unmodifiable(providers);

  /// Parses the closed shim protocol returned by
  /// `dort_runtime_available_providers_json`.
  factory OrtProviderDiscovery.fromNativeJson(String source) {
    final int byteLength = utf8.encode(source).length;
    if (byteLength == 0 || byteLength > _maximumProviderDiscoveryBytes) {
      throw const FormatException(
        'Provider discovery is empty or exceeds its byte limit.',
      );
    }
    validateStrictJsonInternal(source, label: 'Provider discovery');
    final Object? decoded = jsonDecode(source);
    if (decoded is! Map<Object?, Object?>) {
      throw const FormatException('Provider discovery must be an object.');
    }
    final Map<String, Object?> object = <String, Object?>{};
    for (final MapEntry<Object?, Object?> entry in decoded.entries) {
      if (entry.key is! String) {
        throw const FormatException(
          'Provider discovery contains a non-string key.',
        );
      }
      object[entry.key! as String] = entry.value;
    }
    if (object.length != 2 ||
        !object.containsKey('schemaVersion') ||
        !object.containsKey('reportedNames') ||
        object['schemaVersion'] != 1) {
      throw const FormatException(
        'Provider discovery does not match schema version 1.',
      );
    }
    final Object? rawNames = object['reportedNames'];
    if (rawNames is! List<Object?> ||
        rawNames.length > _maximumDiscoveredProviders) {
      throw const FormatException(
        'Provider discovery must contain a bounded name array.',
      );
    }
    final Set<String> seen = <String>{};
    final List<OrtDiscoveredProvider> providers = <OrtDiscoveredProvider>[];
    for (final Object? rawName in rawNames) {
      if (rawName is! String ||
          !_reportedProviderName.hasMatch(rawName) ||
          !seen.add(rawName)) {
        throw const FormatException(
          'Provider discovery contains an invalid or duplicate name.',
        );
      }
      final _ProviderAdapter? adapter = _reportedAdapters[rawName];
      providers.add(
        OrtDiscoveredProvider._(
          reportedName: rawName,
          wrapperId: adapter?.wrapperId,
          registrationMechanism:
              adapter?.registrationMechanism ??
              OrtProviderRegistrationMechanism.unknown,
          registrationName: adapter?.registrationName,
        ),
      );
    }
    return OrtProviderDiscovery._(providers);
  }

  final List<OrtDiscoveredProvider> providers;

  Set<String> get knownWrapperIds => Set<String>.unmodifiable(
    providers
        .map((OrtDiscoveredProvider provider) => provider.wrapperId)
        .whereType<String>(),
  );

  bool isDiscoverable(String wrapperId) => providers.any(
    (OrtDiscoveredProvider provider) => provider.wrapperId == wrapperId,
  );

  String? reportedNameFor(String wrapperId) {
    for (final OrtDiscoveredProvider provider in providers) {
      if (provider.wrapperId == wrapperId) return provider.reportedName;
    }
    return null;
  }

  Map<String, String> get evidenceProviderNames =>
      Map<String, String>.unmodifiable(<String, String>{
        for (final OrtDiscoveredProvider provider in providers)
          if (provider.wrapperId case final String id)
            id: provider.reportedName,
      });

  Map<String, Object?> toJson() => <String, Object?>{
    'schemaVersion': 1,
    'providers': <Object?>[
      for (final OrtDiscoveredProvider provider in providers) provider.toJson(),
    ],
  };
}

/// Builds an immutable provider-state snapshot from independently proven
/// build, discovery, registration, assignment, and qualification facts.
///
/// This is internal package plumbing and intentionally not exported from the
/// primary library. A null [compiledProviders] records that the opened shim
/// has no embedded provider build inventory; discovery is not substituted for
/// that missing evidence.
List<OrtProviderDiagnostics> buildOrtProviderDiagnostics({
  required List<OrtExecutionProvider> requestedProviders,
  required OrtProviderDiscovery discovery,
  required bool sessionCreated,
  Map<String, String?>? compiledProviders,
  OrtProviderRunEvidence? runEvidence,
  Set<String> qualifiedProviderIds = const <String>{},
}) {
  final Set<String> ids = <String>{
    ...requestedProviders.map((OrtExecutionProvider provider) => provider.id),
    ...discovery.knownWrapperIds,
    ...?compiledProviders?.keys,
    ...qualifiedProviderIds,
  };
  final Map<String, OrtExecutionProvider> requested =
      <String, OrtExecutionProvider>{
        for (final OrtExecutionProvider provider in requestedProviders)
          provider.id: provider,
      };
  final List<OrtProviderDiagnostics> result = <OrtProviderDiagnostics>[];
  for (final String id in ids) {
    final _ProviderAdapter? adapter = _wrapperAdapters[id];
    final OrtExecutionProvider? request = requested[id];
    final bool? active = runEvidence?.isActive(id);
    result.add(
      OrtProviderDiagnostics(
        wrapperId: id,
        registrationMechanism:
            adapter?.registrationMechanism ??
            OrtProviderRegistrationMechanism.unknown,
        registrationName: adapter?.registrationName,
        reportedName:
            discovery.reportedNameFor(id) ??
            compiledProviders?[id] ??
            adapter?.reportedName,
        compiled: compiledProviders?.containsKey(id),
        discoverable: discovery.isDiscoverable(id),
        registered: request == null ? false : sessionCreated,
        active: active,
        qualified: qualifiedProviderIds.contains(id) ? true : null,
        options: request?.redactedOptions ?? const <String, String>{},
        assignmentEvidence: switch (runEvidence) {
          null => null,
          final OrtProviderRunEvidence evidence =>
            'ort-run-profile-v1:${evidence.nodeExecutionCount}',
        },
        fallbackReason: request != null && active == false
            ? 'No node assignment was observed in this profiled run.'
            : null,
      ),
    );
  }
  return List<OrtProviderDiagnostics>.unmodifiable(result);
}

final RegExp _reportedProviderName = RegExp(r'^[A-Za-z][A-Za-z0-9_]{0,127}$');

final class _ProviderAdapter {
  const _ProviderAdapter({
    required this.wrapperId,
    required this.reportedName,
    required this.registrationMechanism,
    required this.registrationName,
  });

  final String wrapperId;
  final String reportedName;
  final OrtProviderRegistrationMechanism registrationMechanism;
  final String? registrationName;
}

const List<_ProviderAdapter> _adapters = <_ProviderAdapter>[
  _ProviderAdapter(
    wrapperId: 'cpu',
    reportedName: 'CPUExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.implicit,
    registrationName: null,
  ),
  _ProviderAdapter(
    wrapperId: 'xnnpack',
    reportedName: 'XnnpackExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'XNNPACK',
  ),
  _ProviderAdapter(
    wrapperId: 'coreml',
    reportedName: 'CoreMLExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'CoreML',
  ),
  _ProviderAdapter(
    wrapperId: 'nnapi',
    reportedName: 'NnapiExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.providerSpecific,
    registrationName: 'OrtSessionOptionsAppendExecutionProvider_Nnapi',
  ),
  _ProviderAdapter(
    wrapperId: 'qnn',
    reportedName: 'QNNExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'QNN',
  ),
  _ProviderAdapter(
    wrapperId: 'cuda',
    reportedName: 'CUDAExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.providerSpecific,
    registrationName: 'SessionOptionsAppendExecutionProvider_CUDA_V2',
  ),
  _ProviderAdapter(
    wrapperId: 'tensorrt',
    reportedName: 'TensorrtExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.providerSpecific,
    registrationName: 'SessionOptionsAppendExecutionProvider_TensorRT_V2',
  ),
  _ProviderAdapter(
    wrapperId: 'directml',
    reportedName: 'DmlExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.providerSpecific,
    registrationName: 'OrtDmlApi.SessionOptionsAppendExecutionProvider_DML',
  ),
  _ProviderAdapter(
    wrapperId: 'openvino',
    reportedName: 'OpenVINOExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'OpenVINO',
  ),
  _ProviderAdapter(
    wrapperId: 'dnnl',
    reportedName: 'DnnlExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.providerSpecific,
    registrationName: 'SessionOptionsAppendExecutionProvider_Dnnl',
  ),
  _ProviderAdapter(
    wrapperId: 'migraphx',
    reportedName: 'MIGraphXExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.providerSpecific,
    registrationName: 'SessionOptionsAppendExecutionProvider_MIGraphX',
  ),
  _ProviderAdapter(
    wrapperId: 'webnn',
    reportedName: 'WebNNExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'WEBNN',
  ),
  _ProviderAdapter(
    wrapperId: 'webgpu',
    reportedName: 'WebGpuExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'WebGPU',
  ),
  _ProviderAdapter(
    wrapperId: 'azure',
    reportedName: 'AzureExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'AZURE',
  ),
  _ProviderAdapter(
    wrapperId: 'js',
    reportedName: 'JsExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'JS',
  ),
  _ProviderAdapter(
    wrapperId: 'vitisai',
    reportedName: 'VitisAIExecutionProvider',
    registrationMechanism: OrtProviderRegistrationMechanism.generic,
    registrationName: 'VitisAI',
  ),
];

final Map<String, _ProviderAdapter> _wrapperAdapters =
    <String, _ProviderAdapter>{
      for (final _ProviderAdapter adapter in _adapters)
        adapter.wrapperId: adapter,
    };

final Map<String, _ProviderAdapter> _reportedAdapters =
    <String, _ProviderAdapter>{
      for (final _ProviderAdapter adapter in _adapters)
        adapter.reportedName: adapter,
    };
