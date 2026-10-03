import 'dart:collection';
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as p;

import 'diagnostics.dart';
import 'disposable.dart';
import 'exceptions.dart';
import 'ffi/generated_native_asset_bindings.dart' as bindings;
import 'ffi/native_api.dart';
import 'metadata.dart';
import 'provider.dart';
import 'provider_discovery.dart';
import 'provider_evidence.dart';
import 'resource_limits.dart';
import 'runtime_info.dart';
import 'runtime_source.dart';
import 'session_options.dart';
import 'strict_json.dart';
import 'tensor_type.dart';
import 'utf16.dart';
import 'version.dart';

part 'model_source.dart';
part 'composite_value.dart';
part 'native_buffer.dart';
part 'run_options.dart';
part 'run_result.dart';
part 'session.dart';
part 'session_metadata_protocol.dart';
part 'tensor.dart';
part 'value.dart';

const int _nativeErrorAbiMismatch =
    bindings.dort_error_code.DORT_ERROR_ABI_MISMATCH;
const int _nativeErrorApiRequestUnsupported =
    bindings.dort_error_code.DORT_ERROR_API_REQUEST_UNSUPPORTED;
const int _nativeErrorSourceUnsupported =
    bindings.dort_error_code.DORT_ERROR_SOURCE_UNSUPPORTED;
const int _nativeErrorPathNotAbsolute =
    bindings.dort_error_code.DORT_ERROR_PATH_NOT_ABSOLUTE;
const int _nativeErrorPathOutsideAllowedRoot =
    bindings.dort_error_code.DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT;
const int _nativeErrorRuntimeNotFound =
    bindings.dort_error_code.DORT_ERROR_RUNTIME_NOT_FOUND;
const int _nativeErrorSymbolNotFound =
    bindings.dort_error_code.DORT_ERROR_SYMBOL_NOT_FOUND;
const int _nativeErrorInvalidUtf8 =
    bindings.dort_error_code.DORT_ERROR_INVALID_UTF8;
const int _nativeErrorLimitExceeded =
    bindings.dort_error_code.DORT_ERROR_LIMIT_EXCEEDED;
const int _nativeErrorPlatform = bindings.dort_error_code.DORT_ERROR_PLATFORM;
const int _nativeErrorRuntimeIdentityMismatch =
    bindings.dort_error_code.DORT_ERROR_RUNTIME_IDENTITY_MISMATCH;
const int _nativeErrorModelInvalid =
    bindings.dort_error_code.DORT_ERROR_MODEL_INVALID;
const int _nativeErrorTensorInvalid =
    bindings.dort_error_code.DORT_ERROR_TENSOR_INVALID;
const int _nativeErrorRunFailed =
    bindings.dort_error_code.DORT_ERROR_RUN_FAILED;
const int _nativeErrorBufferTooSmall =
    bindings.dort_error_code.DORT_ERROR_BUFFER_TOO_SMALL;
const int _nativeErrorOverflow = bindings.dort_error_code.DORT_ERROR_OVERFLOW;
const int _nativeErrorProviderUnsupported =
    bindings.dort_error_code.DORT_ERROR_PROVIDER_UNSUPPORTED;
const int _nativeErrorNotTensor =
    bindings.dort_error_code.DORT_ERROR_NOT_TENSOR;
const int _nativeErrorValueKindUnsupported =
    bindings.dort_error_code.DORT_ERROR_VALUE_KIND_UNSUPPORTED;
const int _nativeErrorNotComposite =
    bindings.dort_error_code.DORT_ERROR_NOT_COMPOSITE;
const int _nativeErrorDataLeaseUnsupported =
    bindings.dort_error_code.DORT_ERROR_DATA_LEASE_UNSUPPORTED;
const int _nativeErrorMemoryDomainUnsupported =
    bindings.dort_error_code.DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED;
const int _nativeErrorExternalDataInvalid =
    bindings.dort_error_code.DORT_ERROR_EXTERNAL_DATA_INVALID;
const int _nativeDomainOrtStatus =
    bindings.dort_error_domain.DORT_ERROR_DOMAIN_ORT_STATUS;
const int _nativeDomainProvider =
    bindings.dort_error_domain.DORT_ERROR_DOMAIN_PROVIDER;

typedef _NativeRelease = void Function(Pointer<Void> handle);

abstract base class _NativeOwner implements Disposable, Finalizable {
  _NativeOwner({
    required FonixNativeApi nativeApi,
    required Pointer<Void> nativeHandle,
    required Pointer<NativeFinalizerFunction> releaseAddress,
    required _NativeRelease release,
    required String debugName,
    int? externalSize,
  }) : _nativeApi = nativeApi,
       _handle = nativeHandle,
       _release = release,
       _debugName = debugName,
       _finalizer = NativeFinalizer(releaseAddress) {
    if (nativeHandle == nullptr) {
      throw StateError('$debugName received a null native handle.');
    }
    _finalizer.attach(
      this,
      nativeHandle,
      detach: _finalizerDetachToken,
      externalSize: externalSize,
    );
  }

  final FonixNativeApi _nativeApi;
  final _NativeRelease _release;
  final String _debugName;
  final NativeFinalizer _finalizer;
  final Object _finalizerDetachToken = Object();
  Pointer<Void> _handle;

  Pointer<Void> get _nativeHandle {
    _ensureOpen();
    return _handle;
  }

  @override
  bool get isDisposed => _handle == nullptr;

  @override
  void dispose() {
    final Pointer<Void> handle = _handle;
    if (handle == nullptr) {
      return;
    }
    _handle = nullptr;
    _finalizer.detach(_finalizerDetachToken);
    try {
      _disposeDependents();
    } finally {
      _release(handle);
    }
  }

  void _disposeDependents() {}

  void _ensureOpen() {
    if (isDisposed) {
      throw OrtDisposedException(_debugName);
    }
  }
}

/// An opened, negotiated ONNX Runtime identity.
///
/// The native runtime loader itself stays resident for process lifetime, while
/// each [OrtRuntime] owns one ref-counted native handle. Call [dispose]
/// deterministically; the native finalizer is only a safety net.
final class OrtRuntime extends _NativeOwner {
  factory OrtRuntime.open({
    required OrtRuntimeSource source,
    OrtApiVersion requiredApi = OrtApiVersion.v27,
    OrtLogSeverity logSeverity = OrtLogSeverity.warning,
    String logId = 'fonix',
  }) => _openOrtRuntime(
    nativeApi: FonixNativeApi.nativeAsset(),
    source: source,
    requiredApi: requiredApi,
    logSeverity: logSeverity,
    logId: logId,
  );

  OrtRuntime._({
    required super.nativeApi,
    required Pointer<Void> handle,
    required OrtNativeBuildInfo buildInfo,
    required OrtRuntimeInfo info,
    required OrtProviderDiscovery providerDiscovery,
  }) : _buildInfo = buildInfo,
       _info = info,
       _providerDiscovery = providerDiscovery,
       super(
         nativeHandle: handle,
         releaseAddress: nativeApi.runtimeReleaseAddress,
         release: nativeApi.releaseRuntime,
         debugName: 'OrtRuntime',
       );

  final OrtNativeBuildInfo _buildInfo;
  final OrtRuntimeInfo _info;
  final OrtProviderDiscovery _providerDiscovery;

  Map<String, String?>? get _compiledProviders =>
      _buildInfo.artifact?.providers;

  /// The validated native build identity.
  OrtNativeBuildInfo get buildInfo {
    _ensureOpen();
    return _buildInfo;
  }

  /// Runtime facts copied during open; no native pointer is exposed.
  OrtRuntimeInfo get info {
    _ensureOpen();
    return _info;
  }

  /// Provider names reported by the actual opened ONNX Runtime.
  ///
  /// Availability does not prove that dependencies load, registration
  /// succeeds, or any graph node is assigned to the provider.
  OrtProviderDiscovery get providerDiscovery {
    _ensureOpen();
    return _providerDiscovery;
  }

  /// Immutable, redacted build/runtime/provider discovery diagnostics.
  OrtDiagnostics get diagnostics {
    _ensureOpen();
    return _diagnosticsSnapshot(
      providers: buildOrtProviderDiagnostics(
        requestedProviders: const <OrtExecutionProvider>[],
        discovery: _providerDiscovery,
        sessionCreated: false,
        compiledProviders: _compiledProviders,
      ),
    );
  }

  OrtDiagnostics _diagnosticsSnapshot({
    required List<OrtProviderDiagnostics> providers,
    String? modelId,
    OrtSessionDiagnostics? session,
  }) {
    final OrtNativeArtifactIdentity? artifact = _buildInfo.artifact;
    final List<String> abiParts = Abi.current().toString().split('_');
    return OrtDiagnostics(
      schemaVersion: 1,
      dartPackageVersion: fonixPackageVersion,
      shimAbiVersion: _buildInfo.shimAbiVersion,
      shimBuildId: _buildInfo.buildId,
      requiredOrtApiVersion: _buildInfo.requiredOrtApiVersion,
      negotiatedOrtApiVersion: _info.negotiatedOrtApiVersion,
      runtimeVersion: _info.runtimeVersion,
      runtimeOwner: switch (_buildInfo.androidRuntimeOwner) {
        OrtAndroidRuntimeOwner.sherpa => OrtRuntimeOwner.sherpa,
        OrtAndroidRuntimeOwner.application => OrtRuntimeOwner.application,
        null => switch (_info.runtimeSource) {
          OrtRuntimeSourceKind.linked ||
          OrtRuntimeSourceKind.bundled => OrtRuntimeOwner.wrapper,
          OrtRuntimeSourceKind.file => OrtRuntimeOwner.application,
          OrtRuntimeSourceKind.process => OrtRuntimeOwner.system,
        },
      },
      runtimeMode: switch (_info.runtimeSource) {
        OrtRuntimeSourceKind.linked => OrtRuntimeMode.linked,
        OrtRuntimeSourceKind.bundled => OrtRuntimeMode.bundled,
        OrtRuntimeSourceKind.process => OrtRuntimeMode.process,
        OrtRuntimeSourceKind.file => OrtRuntimeMode.file,
      },
      runtimeIdentity: _info.runtimeLibraryIdentity,
      platform: artifact?.targetOs ?? Platform.operatingSystem,
      architecture:
          artifact?.targetArchitecture ??
          (abiParts.length == 2 ? abiParts.last : Abi.current().toString()),
      artifactFlavor: artifact?.flavor ?? 'external',
      artifactSha256: artifact?.sourceSha256,
      providers: providers,
      modelId: modelId,
      session: session,
    );
  }
}

/// Opens through an explicit shim library for focused ABI tests.
///
/// This entry point is intentionally omitted from `package:fonix/fonix.dart`.
OrtRuntime openOrtRuntimeWithNativeApiForTesting({
  required FonixNativeApi nativeApi,
  required OrtRuntimeSource source,
  OrtApiVersion requiredApi = OrtApiVersion.v27,
  OrtLogSeverity logSeverity = OrtLogSeverity.warning,
  String logId = 'fonix-test',
}) => _openOrtRuntime(
  nativeApi: nativeApi,
  source: source,
  requiredApi: requiredApi,
  logSeverity: logSeverity,
  logId: logId,
);

OrtRuntime _openOrtRuntime({
  required FonixNativeApi nativeApi,
  required OrtRuntimeSource source,
  required OrtApiVersion requiredApi,
  required OrtLogSeverity logSeverity,
  required String logId,
}) {
  _validateRuntimeConfiguration(source: source, logId: logId);

  try {
    final int actualShimAbi = nativeApi.shimAbiVersion;
    if (actualShimAbi != fonixShimAbiVersion) {
      throw OrtApiIncompatibleException(
        operation: 'shim_abi_check',
        code: _nativeErrorAbiMismatch,
        message:
            'Fonix requires shim ABI $fonixShimAbiVersion, but the loaded '
            'native asset reports ABI $actualShimAbi.',
        context: <String, Object?>{
          'requiredShimAbi': fonixShimAbiVersion,
          'actualShimAbi': actualShimAbi,
        },
      );
    }

    final int actualApiFloor = nativeApi.ortApiCompatibilityFloor;
    if (actualApiFloor != requiredApi.value) {
      throw OrtApiIncompatibleException(
        operation: 'ort_api_floor_check',
        code: _nativeErrorApiRequestUnsupported,
        message:
            'Fonix requested ONNX Runtime C API ${requiredApi.value}, but '
            'the loaded shim supports API $actualApiFloor.',
        context: <String, Object?>{
          'requiredOrtApi': requiredApi.value,
          'shimOrtApiFloor': actualApiFloor,
        },
      );
    }

    final OrtNativeBuildInfo buildInfo;
    try {
      buildInfo = parseOrtNativeBuildInfo(nativeApi.getBuildManifestJson());
    } on FormatException {
      throw OrtNativePackagingException(
        operation: 'build_manifest_parse',
        code: _nativeErrorPlatform,
        message:
            'The loaded Fonix shim returned incompatible build information.',
      );
    } on StateError {
      throw OrtNativePackagingException(
        operation: 'build_manifest_copy',
        code: _nativeErrorPlatform,
        message: 'The loaded Fonix shim returned invalid build information.',
      );
    }
    _checkSourcePolicy(source.kind, buildInfo);

    final FonixNativeRuntimeConfig nativeConfig = FonixNativeRuntimeConfig(
      source: source,
      requiredApi: requiredApi,
      logSeverity: logSeverity,
      logId: logId,
    );
    final Pointer<Void> handle = nativeApi.openRuntime(nativeConfig);
    try {
      final OrtRuntimeInfo runtimeInfo;
      final OrtProviderDiscovery providerDiscovery;
      try {
        runtimeInfo = parseOrtRuntimeInfo(
          nativeApi.getRuntimeInfoJson(handle),
          buildInfo: buildInfo,
          requestedSource: source.kind,
          requestedApi: requiredApi,
          requestedLogSeverity: logSeverity,
          requestedLogId: logId,
        );
        providerDiscovery = OrtProviderDiscovery.fromNativeJson(
          nativeApi.getRuntimeAvailableProvidersJson(handle),
        );
      } on FormatException {
        throw OrtNativePackagingException(
          operation: 'runtime_info_parse',
          code: _nativeErrorPlatform,
          message:
              'The loaded Fonix shim returned incompatible runtime '
              'information.',
        );
      } on StateError {
        throw OrtNativePackagingException(
          operation: 'runtime_info_copy',
          code: _nativeErrorPlatform,
          message:
              'The loaded Fonix shim returned invalid runtime information.',
        );
      }

      return OrtRuntime._(
        nativeApi: nativeApi,
        handle: handle,
        buildInfo: buildInfo,
        info: runtimeInfo,
        providerDiscovery: providerDiscovery,
      );
    } catch (_) {
      nativeApi.releaseRuntime(handle);
      rethrow;
    }
  } on OrtException {
    rethrow;
  } on FonixNativeFailure catch (failure) {
    throw _translateNativeFailure(
      failure,
      runtimeSource: source.kind,
      requiredApi: requiredApi,
      privateValues: <String>[
        if (source.libraryPath case final String path) path,
        if (source.allowedRoot case final String root) root,
      ],
    );
  } on ArgumentError {
    throw OrtNativePackagingException(
      operation: 'native_asset_resolve',
      code: _nativeErrorPlatform,
      message:
          'The Fonix native asset is missing or does not export the required '
          'shim ABI.',
      context: <String, Object?>{'runtimeSource': source.kind.name},
    );
  } on StateError {
    throw OrtNativePackagingException(
      operation: 'native_protocol',
      code: _nativeErrorPlatform,
      message: 'The Fonix native shim violated its bounded data contract.',
      context: <String, Object?>{'runtimeSource': source.kind.name},
    );
  }
}

void _validateRuntimeConfiguration({
  required OrtRuntimeSource source,
  required String logId,
}) {
  if (!hasWellFormedUtf16(logId) ||
      logId.isEmpty ||
      utf8.encode(logId).length > 128 ||
      logId.contains('/') ||
      logId.contains(r'\') ||
      logId.runes.any((int rune) => rune < 0x20 || rune == 0x7f)) {
    throw ArgumentError.value(
      logId,
      'logId',
      'must be non-empty safe UTF-8 of at most 128 bytes',
    );
  }

  final Iterable<String> nativeStrings = <String>[
    if (source.libraryPath case final String value) value,
    if (source.allowedRoot case final String value) value,
    ...source.preferredLibraryNames,
  ];
  for (final String value in nativeStrings) {
    if (!hasWellFormedUtf16(value) || utf8.encode(value).length > 4096) {
      throw ArgumentError.value(
        '<redacted>',
        'source',
        'contains invalid UTF-8 or exceeds the native byte limit',
      );
    }
  }
}

void _checkSourcePolicy(
  OrtRuntimeSourceKind source,
  OrtNativeBuildInfo buildInfo,
) {
  if (!buildInfo.allowedRuntimeSources.contains(source)) {
    throw OrtNativePackagingException(
      operation: 'runtime_source_check',
      code: _nativeErrorSourceUnsupported,
      message:
          'Runtime source "${source.name}" is unavailable in the '
          '"${buildInfo.runtimeProfile.name}" native build policy.',
      context: <String, Object?>{
        'runtimeSource': source.name,
        'runtimeProfile': buildInfo.runtimeProfile.name,
        'androidRuntimeOwner': buildInfo.androidRuntimeOwner?.name,
        'allowedRuntimeSources': buildInfo.allowedRuntimeSources
            .map((OrtRuntimeSourceKind value) => value.name)
            .toList(growable: false),
      },
    );
  }
}

OrtException _translateNativeFailure(
  FonixNativeFailure failure, {
  required OrtRuntimeSourceKind runtimeSource,
  required OrtApiVersion requiredApi,
  Iterable<String> privateValues = const <String>[],
}) {
  final String operation = _safeOperation(failure.operation);
  final String message = _redactPrivateText(failure.message, privateValues);
  final Map<String, Object?> context = <String, Object?>{
    'nativeDomain': failure.domain,
    'runtimeSource': runtimeSource.name,
    'requiredOrtApi': requiredApi.value,
  };

  if (failure.code == _nativeErrorExternalDataInvalid) {
    return OrtModelLoadException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }
  if (failure.code == _nativeErrorValueKindUnsupported) {
    return OrtUnsupportedValueException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }
  if (failure.code == _nativeErrorDataLeaseUnsupported ||
      failure.code == _nativeErrorMemoryDomainUnsupported) {
    return OrtDataAccessException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }
  if (failure.code == _nativeErrorNotComposite) {
    return OrtInvalidArgumentException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }

  if (failure.code == _nativeErrorAbiMismatch ||
      failure.code == _nativeErrorApiRequestUnsupported ||
      failure.domain == bindings.dort_error_domain.DORT_ERROR_DOMAIN_ORT_API) {
    return OrtApiIncompatibleException(
      operation: operation,
      code: failure.code,
      message: message,
      context: context,
    );
  }
  if (failure.code == _nativeErrorRuntimeNotFound ||
      failure.code == _nativeErrorSymbolNotFound) {
    return OrtRuntimeNotFoundException(
      operation: operation,
      code: failure.code,
      message: message,
      context: context,
    );
  }
  if (failure.code == _nativeErrorProviderUnsupported ||
      failure.domain == _nativeDomainProvider) {
    return OrtProviderUnavailableException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }
  if (failure.code == _nativeErrorModelInvalid ||
      failure.code == _nativeErrorExternalDataInvalid ||
      (failure.domain == _nativeDomainOrtStatus &&
          operation.startsWith('session_create'))) {
    return OrtModelLoadException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }
  if (failure.code == _nativeErrorRunFailed ||
      (failure.domain == _nativeDomainOrtStatus &&
          operation == 'session_run')) {
    return OrtRunException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }
  if (failure.code == _nativeErrorSourceUnsupported ||
      failure.code == _nativeErrorPlatform ||
      failure.domain ==
          bindings.dort_error_domain.DORT_ERROR_DOMAIN_UNSUPPORTED) {
    return OrtNativePackagingException(
      operation: operation,
      code: failure.code,
      message: message,
      context: context,
    );
  }
  if (failure.code == bindings.dort_error_code.DORT_ERROR_INVALID_ARGUMENT ||
      failure.code == _nativeErrorPathNotAbsolute ||
      failure.code == _nativeErrorPathOutsideAllowedRoot ||
      failure.code == _nativeErrorInvalidUtf8 ||
      failure.code == _nativeErrorLimitExceeded ||
      failure.code == _nativeErrorRuntimeIdentityMismatch ||
      failure.code == _nativeErrorTensorInvalid ||
      failure.code == _nativeErrorBufferTooSmall ||
      failure.code == _nativeErrorOverflow ||
      failure.code == _nativeErrorNotTensor ||
      failure.code == _nativeErrorNotComposite) {
    return OrtInvalidArgumentException(
      operation: operation,
      code: failure.code,
      message: message,
      ortCode: failure.ortCode,
      context: context,
    );
  }

  return OrtException(
    operation: operation,
    domain: _mapNativeDomain(failure.domain),
    code: failure.code,
    ortCode: failure.ortCode,
    message: message,
    context: context,
  );
}

OrtErrorDomain _mapNativeDomain(int domain) => switch (domain) {
  bindings.dort_error_domain.DORT_ERROR_DOMAIN_LOADER => OrtErrorDomain.loader,
  bindings.dort_error_domain.DORT_ERROR_DOMAIN_ORT_API => OrtErrorDomain.ortApi,
  bindings.dort_error_domain.DORT_ERROR_DOMAIN_ALLOCATION =>
    OrtErrorDomain.allocation,
  bindings.dort_error_domain.DORT_ERROR_DOMAIN_UNSUPPORTED =>
    OrtErrorDomain.unsupported,
  _nativeDomainOrtStatus => OrtErrorDomain.ortStatus,
  _nativeDomainProvider => OrtErrorDomain.provider,
  _ => OrtErrorDomain.shim,
};

String _safeOperation(String value) {
  if (value.isEmpty ||
      utf8.encode(value).length > 256 ||
      value.runes.any((int rune) => rune < 0x20 || rune == 0x7f) ||
      value.contains('/') ||
      value.contains(r'\')) {
    return 'native_call';
  }
  return value;
}

String _redactPrivateText(String value, Iterable<String> privateValues) {
  var redacted = value;
  final List<String> orderedPrivateValues =
      privateValues
          .where((String item) => item.isNotEmpty)
          .toSet()
          .toList(growable: false)
        ..sort(
          (String left, String right) => right.length.compareTo(left.length),
        );
  for (final String privateValue in orderedPrivateValues) {
    redacted = redacted.replaceAll(privateValue, '<redacted-path>');
  }
  redacted = redacted
      .replaceAll(RegExp(r'[A-Za-z]:[\\/][^\s"(),;]+'), '<redacted-path>')
      .replaceAll(RegExp(r'/(?:[^\s"(),;:]+/?)+'), '<redacted-path>')
      .replaceAll(RegExp(r'[\u0000-\u001f\u007f]'), ' ');
  if (redacted.trim().isEmpty) {
    return 'The native operation failed without safe diagnostic details.';
  }
  if (utf8.encode(redacted).length > 4096) {
    return 'The native operation failed; details exceeded the Dart limit.';
  }
  return redacted;
}
