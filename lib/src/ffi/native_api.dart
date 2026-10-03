import 'dart:convert';
import 'dart:ffi';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';

import '../provider.dart';
import '../runtime_source.dart';
import '../session_options.dart';
import '../version.dart';
import 'generated_native_asset_bindings.dart' as bindings;

const int _maximumNativeJsonBytes = 1024 * 1024;
const int _maximumProviderProfileBytes = 8 * 1024 * 1024;
const int _maximumStatusOperationBytes = 256;
const int _maximumStatusMessageBytes = 4096;
const List<String> _stableExportNames = <String>[
  'dort_get_abi_version',
  'dort_get_ort_api_compatibility_floor',
  'dort_get_build_manifest_json',
  'dort_string_release',
  'dort_status_domain',
  'dort_status_code',
  'dort_status_ort_code',
  'dort_status_operation',
  'dort_status_message',
  'dort_status_release',
  'dort_runtime_open',
  'dort_runtime_retain',
  'dort_runtime_release',
  'dort_runtime_info_json',
  'dort_runtime_available_providers_json',
  'dort_session_options_create',
  'dort_session_options_retain',
  'dort_session_options_release',
  'dort_session_create_from_bytes',
  'dort_session_create_from_file',
  'dort_session_create_from_bytes_with_external_data',
  'dort_session_retain',
  'dort_session_release',
  'dort_session_metadata_json',
  'dort_session_type_metadata_json',
  'dort_session_model_metadata_json',
  'dort_buffer_allocate',
  'dort_buffer_retain',
  'dort_buffer_release',
  'dort_buffer_byte_length',
  'dort_buffer_write',
  'dort_buffer_read',
  'dort_buffer_data_acquire',
  'dort_tensor_create_copy',
  'dort_tensor_create_with_buffer',
  'dort_tensor_create_strings_copy',
  'dort_value_retain',
  'dort_value_release',
  'dort_value_kind',
  'dort_value_child_count',
  'dort_value_child_get',
  'dort_sequence_create',
  'dort_map_create',
  'dort_optional_none_create',
  'dort_optional_some_create',
  'dort_tensor_info_json',
  'dort_tensor_copy_data',
  'dort_tensor_string_count',
  'dort_tensor_string_get',
  'dort_tensor_data_acquire',
  'dort_data_lease_retain',
  'dort_data_lease_release',
  'dort_run_options_create',
  'dort_run_options_retain',
  'dort_run_options_release',
  'dort_run_options_set_terminate',
  'dort_run_options_unset_terminate',
  'dort_run_options_profiling_start',
  'dort_run_options_profiling_finish',
  'dort_cancel_token_register',
  'dort_cancel_token_request',
  'dort_cancel_token_finish',
  'dort_session_run',
  'dort_run_result_retain',
  'dort_run_result_release',
  'dort_run_result_count',
  'dort_run_result_get',
];

/// Copied details from an owned `dort_status_t`.
///
/// This is internal package plumbing. No pointer into the native status is
/// retained after an instance has been constructed.
final class FonixNativeFailure implements Exception {
  const FonixNativeFailure({
    required this.domain,
    required this.code,
    required this.ortCode,
    required this.operation,
    required this.message,
  });

  final int domain;
  final int code;
  final int? ortCode;
  final String operation;
  final String message;
}

/// Validated values marshalled into `dort_runtime_config_t`.
final class FonixNativeRuntimeConfig {
  const FonixNativeRuntimeConfig({
    required this.source,
    required this.requiredApi,
    required this.logSeverity,
    required this.logId,
  });

  final OrtRuntimeSource source;
  final OrtApiVersion requiredApi;
  final OrtLogSeverity logSeverity;
  final String logId;
}

final class FonixNativeRunInput {
  const FonixNativeRunInput({required this.name, required this.value});

  final String name;
  final Pointer<Void> value;
}

final class FonixNativeNamedValue {
  const FonixNativeNamedValue({required this.name, required this.value});

  final String name;
  final Pointer<Void> value;
}

/// An acquired native data range and the lease that owns its lifetime.
final class FonixNativeDataLease {
  const FonixNativeDataLease({
    required this.lease,
    required this.data,
    required this.byteLength,
  });

  final Pointer<Void> lease;
  final Pointer<Void> data;
  final int byteLength;
}

/// A narrow adapter around generated bindings for the project-owned shim.
///
/// Production uses native-asset bindings. The dynamic-library constructor is
/// intentionally kept in this internal library for focused ABI tests and
/// native tooling.
final class FonixNativeApi {
  FonixNativeApi.nativeAsset() : _symbols = _NativeAssetSymbols();

  FonixNativeApi.dynamicLibrary(DynamicLibrary library)
    : _symbols = _DynamicLibrarySymbols(library);

  final _FonixSymbols _symbols;
  int _dataLeaseReleaseCount = 0;

  /// Number of data leases released through this adapter instance.
  ///
  /// This internal diagnostic exists only for deterministic finalizer tests.
  int get dataLeaseReleaseCountForTesting => _dataLeaseReleaseCount;

  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_session_config_t>,
    Pointer<Pointer<bindings.dort_session_options_t>>,
  )
  _sessionOptionsCreate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_session_config_t>,
            Pointer<Pointer<bindings.dort_session_options_t>>,
          )
        >
      >('dort_session_options_create')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_string_t>,
  )
  _runtimeAvailableProvidersJson = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_string_t>,
          )
        >
      >('dort_runtime_available_providers_json')
      .asFunction();
  late final void Function(Pointer<bindings.dort_session_options_t>)
  _sessionOptionsRelease = _symbols
      .lookup<
        NativeFunction<Void Function(Pointer<bindings.dort_session_options_t>)>
      >('dort_session_options_release')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_session_options_t>,
    Pointer<Void>,
    int,
    Pointer<Pointer<bindings.dort_session_t>>,
  )
  _sessionCreateFromBytes = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_session_options_t>,
            Pointer<Void>,
            Size,
            Pointer<Pointer<bindings.dort_session_t>>,
          )
        >
      >('dort_session_create_from_bytes')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_session_options_t>,
    Pointer<Char>,
    Pointer<Char>,
    Pointer<Pointer<bindings.dort_session_t>>,
  )
  _sessionCreateFromFile = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_session_options_t>,
            Pointer<Char>,
            Pointer<Char>,
            Pointer<Pointer<bindings.dort_session_t>>,
          )
        >
      >('dort_session_create_from_file')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_session_options_t>,
    Pointer<Void>,
    int,
    Pointer<bindings.dort_external_data_t>,
    int,
    Pointer<Pointer<bindings.dort_session_t>>,
  )
  _sessionCreateFromBytesWithExternalData = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_session_options_t>,
            Pointer<Void>,
            Size,
            Pointer<bindings.dort_external_data_t>,
            Size,
            Pointer<Pointer<bindings.dort_session_t>>,
          )
        >
      >('dort_session_create_from_bytes_with_external_data')
      .asFunction();
  late final void Function(Pointer<bindings.dort_session_t>)
  _sessionRelease = _symbols
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_session_t>)>>(
        'dort_session_release',
      )
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_session_t>,
    Pointer<bindings.dort_string_t>,
  )
  _sessionMetadataJson = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_session_t>,
            Pointer<bindings.dort_string_t>,
          )
        >
      >('dort_session_metadata_json')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_session_t>,
    Pointer<bindings.dort_string_t>,
  )
  _sessionTypeMetadataJson = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_session_t>,
            Pointer<bindings.dort_string_t>,
          )
        >
      >('dort_session_type_metadata_json')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_session_t>,
    Pointer<bindings.dort_string_t>,
  )
  _sessionModelMetadataJson = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_session_t>,
            Pointer<bindings.dort_string_t>,
          )
        >
      >('dort_session_model_metadata_json')
      .asFunction();

  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    int,
    int,
    Pointer<Pointer<bindings.dort_buffer_t>>,
  )
  _bufferAllocate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Size,
            Size,
            Pointer<Pointer<bindings.dort_buffer_t>>,
          )
        >
      >('dort_buffer_allocate')
      .asFunction();
  late final void Function(Pointer<bindings.dort_buffer_t>)
  _bufferRelease = _symbols
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_buffer_t>)>>(
        'dort_buffer_release',
      )
      .asFunction();
  late final int Function(Pointer<bindings.dort_buffer_t>)
  _bufferByteLength = _symbols
      .lookup<NativeFunction<Size Function(Pointer<bindings.dort_buffer_t>)>>(
        'dort_buffer_byte_length',
      )
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_buffer_t>,
    int,
    Pointer<Void>,
    int,
  )
  _bufferWrite = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_buffer_t>,
            Size,
            Pointer<Void>,
            Size,
          )
        >
      >('dort_buffer_write')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_buffer_t>,
    int,
    Pointer<Void>,
    int,
  )
  _bufferRead = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_buffer_t>,
            Size,
            Pointer<Void>,
            Size,
          )
        >
      >('dort_buffer_read')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_buffer_t>,
    Pointer<Pointer<bindings.dort_data_lease_t>>,
    Pointer<Pointer<Void>>,
    Pointer<Size>,
  )
  _bufferDataAcquire = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_buffer_t>,
            Pointer<Pointer<bindings.dort_data_lease_t>>,
            Pointer<Pointer<Void>>,
            Pointer<Size>,
          )
        >
      >('dort_buffer_data_acquire')
      .asFunction();

  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<Void>,
    int,
    Pointer<Int64>,
    int,
    int,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _tensorCreateCopy = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<Void>,
            Size,
            Pointer<Int64>,
            Size,
            Uint32,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_tensor_create_copy')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_buffer_t>,
    int,
    int,
    Pointer<Int64>,
    int,
    int,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _tensorCreateWithBuffer = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_buffer_t>,
            Size,
            Size,
            Pointer<Int64>,
            Size,
            Uint32,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_tensor_create_with_buffer')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_utf8_span_t>,
    int,
    Pointer<Int64>,
    int,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _tensorCreateStringsCopy = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_utf8_span_t>,
            Size,
            Pointer<Int64>,
            Size,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_tensor_create_strings_copy')
      .asFunction();
  late final void Function(Pointer<bindings.dort_value_t>)
  _valueRetain = _symbols
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_value_t>)>>(
        'dort_value_retain',
      )
      .asFunction();
  late final void Function(Pointer<bindings.dort_value_t>)
  _valueRelease = _symbols
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_value_t>)>>(
        'dort_value_release',
      )
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    Pointer<Uint32>,
  )
  _valueKind = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Pointer<Uint32>,
          )
        >
      >('dort_value_kind')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    Pointer<Size>,
  )
  _valueChildCount = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Pointer<Size>,
          )
        >
      >('dort_value_child_count')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    int,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _valueChildGet = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Size,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_value_child_get')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<Pointer<bindings.dort_value_t>>,
    int,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _sequenceCreate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<Pointer<bindings.dort_value_t>>,
            Size,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_sequence_create')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_value_t>,
    Pointer<bindings.dort_value_t>,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _mapCreate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<bindings.dort_value_t>,
            Pointer<bindings.dort_value_t>,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_map_create')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _optionalNoneCreate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_optional_none_create')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _optionalSomeCreate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_optional_some_create')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    Pointer<bindings.dort_string_t>,
  )
  _tensorInfoJson = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Pointer<bindings.dort_string_t>,
          )
        >
      >('dort_tensor_info_json')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    Pointer<Void>,
    int,
    Pointer<Size>,
  )
  _tensorCopyData = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Pointer<Void>,
            Size,
            Pointer<Size>,
          )
        >
      >('dort_tensor_copy_data')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    Pointer<Size>,
  )
  _tensorStringCount = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Pointer<Size>,
          )
        >
      >('dort_tensor_string_count')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    int,
    Pointer<bindings.dort_string_t>,
  )
  _tensorStringGet = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Size,
            Pointer<bindings.dort_string_t>,
          )
        >
      >('dort_tensor_string_get')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_value_t>,
    Pointer<Pointer<bindings.dort_data_lease_t>>,
    Pointer<Pointer<Void>>,
    Pointer<Size>,
  )
  _tensorDataAcquire = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_value_t>,
            Pointer<Pointer<bindings.dort_data_lease_t>>,
            Pointer<Pointer<Void>>,
            Pointer<Size>,
          )
        >
      >('dort_tensor_data_acquire')
      .asFunction();
  late final void Function(Pointer<bindings.dort_data_lease_t>)
  _dataLeaseRelease = _symbols
      .lookup<
        NativeFunction<Void Function(Pointer<bindings.dort_data_lease_t>)>
      >('dort_data_lease_release')
      .asFunction();

  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<Pointer<bindings.dort_run_options_t>>,
  )
  _runOptionsCreate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_runtime_t>,
            Pointer<Pointer<bindings.dort_run_options_t>>,
          )
        >
      >('dort_run_options_create')
      .asFunction();
  late final void Function(Pointer<bindings.dort_run_options_t>)
  _runOptionsRelease = _symbols
      .lookup<
        NativeFunction<Void Function(Pointer<bindings.dort_run_options_t>)>
      >('dort_run_options_release')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_run_options_t>,
  )
  _runOptionsSetTerminate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_run_options_t>,
          )
        >
      >('dort_run_options_set_terminate')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_run_options_t>,
  )
  _runOptionsUnsetTerminate = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_run_options_t>,
          )
        >
      >('dort_run_options_unset_terminate')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_run_options_t>,
    Pointer<Char>,
  )
  _runOptionsProfilingStart = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_run_options_t>,
            Pointer<Char>,
          )
        >
      >('dort_run_options_profiling_start')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_run_options_t>,
    Pointer<bindings.dort_string_t>,
  )
  _runOptionsProfilingFinish = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_run_options_t>,
            Pointer<bindings.dort_string_t>,
          )
        >
      >('dort_run_options_profiling_finish')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_run_options_t>,
    Pointer<Uint64>,
  )
  _cancelTokenRegister = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_run_options_t>,
            Pointer<Uint64>,
          )
        >
      >('dort_cancel_token_register')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(int, Pointer<Uint32>)
  _cancelTokenRequest = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(Uint64, Pointer<Uint32>)
        >
      >('dort_cancel_token_request')
      .asFunction();
  late final Pointer<bindings.dort_status_t> Function(int, Pointer<Uint32>)
  _cancelTokenFinish = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(Uint64, Pointer<Uint32>)
        >
      >('dort_cancel_token_finish')
      .asFunction();

  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_session_t>,
    Pointer<bindings.dort_run_options_t>,
    Pointer<bindings.dort_named_value_t>,
    int,
    Pointer<Pointer<Char>>,
    int,
    Pointer<Pointer<bindings.dort_run_result_t>>,
  )
  _sessionRun = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_session_t>,
            Pointer<bindings.dort_run_options_t>,
            Pointer<bindings.dort_named_value_t>,
            Size,
            Pointer<Pointer<Char>>,
            Size,
            Pointer<Pointer<bindings.dort_run_result_t>>,
          )
        >
      >('dort_session_run')
      .asFunction();
  late final void Function(Pointer<bindings.dort_run_result_t>)
  _runResultRelease = _symbols
      .lookup<
        NativeFunction<Void Function(Pointer<bindings.dort_run_result_t>)>
      >('dort_run_result_release')
      .asFunction();
  late final int Function(Pointer<bindings.dort_run_result_t>) _runResultCount =
      _symbols
          .lookup<
            NativeFunction<Size Function(Pointer<bindings.dort_run_result_t>)>
          >('dort_run_result_count')
          .asFunction();
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_run_result_t>,
    int,
    Pointer<bindings.dort_string_t>,
    Pointer<Pointer<bindings.dort_value_t>>,
  )
  _runResultGet = _symbols
      .lookup<
        NativeFunction<
          Pointer<bindings.dort_status_t> Function(
            Pointer<bindings.dort_run_result_t>,
            Size,
            Pointer<bindings.dort_string_t>,
            Pointer<Pointer<bindings.dort_value_t>>,
          )
        >
      >('dort_run_result_get')
      .asFunction();

  int get shimAbiVersion => _symbols.getAbiVersion();

  int get ortApiCompatibilityFloor => _symbols.getOrtApiCompatibilityFloor();

  /// Resolves every stable export through this adapter backend for tests.
  int verifyStableExportsForTesting() {
    for (final String symbol in _stableExportNames) {
      _symbols.lookup<NativeFunction<Void Function()>>(symbol);
    }
    return _stableExportNames.length;
  }

  Pointer<NativeFinalizerFunction> get runtimeReleaseAddress =>
      _symbols.runtimeReleaseAddress.cast<NativeFinalizerFunction>();

  String getBuildManifestJson() {
    final Pointer<bindings.dort_string_t> output =
        calloc<bindings.dort_string_t>();
    try {
      final Pointer<bindings.dort_status_t> status = _symbols
          .getBuildManifestJson(output);
      _throwIfStatus(status);
      return _copyNativeString(output);
    } finally {
      _symbols.stringRelease(output);
      calloc.free(output);
    }
  }

  Pointer<Void> openRuntime(FonixNativeRuntimeConfig runtimeConfig) {
    final Arena arena = Arena();
    try {
      final Pointer<bindings.dort_runtime_config_t> config =
          arena<bindings.dort_runtime_config_t>();
      final Pointer<Pointer<bindings.dort_runtime_t>> output =
          arena<Pointer<bindings.dort_runtime_t>>();
      final OrtRuntimeSource source = runtimeConfig.source;
      final List<String> names = source.preferredLibraryNames;

      config.ref
        ..struct_size = sizeOf<bindings.dort_runtime_config_t>()
        ..shim_abi_version = bindings.DORT_ABI_VERSION
        ..required_ort_api_version = runtimeConfig.requiredApi.value
        ..source_kind = _sourceKind(source.kind)
        ..flags = 0
        ..log_severity = runtimeConfig.logSeverity.nativeValue
        ..reserved0 = 0
        ..log_id_utf8 = _nativeUtf8(runtimeConfig.logId, arena)
        ..library_path_utf8 = _optionalNativeUtf8(source.libraryPath, arena)
        ..preferred_library_names_utf8 = _nativeNames(names, arena)
        ..preferred_library_name_count = names.length
        ..allowed_root_utf8 = _optionalNativeUtf8(source.allowedRoot, arena);

      final Pointer<bindings.dort_status_t> status = _symbols.runtimeOpen(
        config,
        output,
      );
      _throwIfStatus(status);
      final Pointer<bindings.dort_runtime_t> handle = output.value;
      if (handle == nullptr) {
        throw const FonixNativeFailure(
          domain: bindings.dort_error_domain.DORT_ERROR_DOMAIN_SHIM,
          code: bindings.dort_error_code.DORT_ERROR_PLATFORM,
          ortCode: null,
          operation: 'runtime_open',
          message: 'The native shim returned a null runtime without a status.',
        );
      }
      return handle.cast<Void>();
    } finally {
      arena.releaseAll();
    }
  }

  String getRuntimeInfoJson(Pointer<Void> runtime) {
    final Pointer<bindings.dort_string_t> output =
        calloc<bindings.dort_string_t>();
    try {
      final Pointer<bindings.dort_status_t> status = _symbols.runtimeInfoJson(
        runtime.cast<bindings.dort_runtime_t>(),
        output,
      );
      _throwIfStatus(status);
      return _copyNativeString(output);
    } finally {
      _symbols.stringRelease(output);
      calloc.free(output);
    }
  }

  String getRuntimeAvailableProvidersJson(Pointer<Void> runtime) =>
      _ownedNativeString(
        (Pointer<bindings.dort_string_t> output) =>
            _runtimeAvailableProvidersJson(
              runtime.cast<bindings.dort_runtime_t>(),
              output,
            ),
      );

  void releaseRuntime(Pointer<Void> runtime) {
    _symbols.runtimeRelease(runtime.cast<bindings.dort_runtime_t>());
  }

  Pointer<NativeFinalizerFunction> get sessionOptionsReleaseAddress => _symbols
      .lookup<
        NativeFunction<Void Function(Pointer<bindings.dort_session_options_t>)>
      >('dort_session_options_release')
      .cast<NativeFinalizerFunction>();

  Pointer<NativeFinalizerFunction> get sessionReleaseAddress => _symbols
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_session_t>)>>(
        'dort_session_release',
      )
      .cast<NativeFinalizerFunction>();

  Pointer<NativeFinalizerFunction> get bufferReleaseAddress => _symbols
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_buffer_t>)>>(
        'dort_buffer_release',
      )
      .cast<NativeFinalizerFunction>();

  Pointer<NativeFinalizerFunction> get valueReleaseAddress => _symbols
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_value_t>)>>(
        'dort_value_release',
      )
      .cast<NativeFinalizerFunction>();

  Pointer<NativeFinalizerFunction> get runOptionsReleaseAddress => _symbols
      .lookup<
        NativeFunction<Void Function(Pointer<bindings.dort_run_options_t>)>
      >('dort_run_options_release')
      .cast<NativeFinalizerFunction>();

  Pointer<NativeFinalizerFunction> get runResultReleaseAddress => _symbols
      .lookup<
        NativeFunction<Void Function(Pointer<bindings.dort_run_result_t>)>
      >('dort_run_result_release')
      .cast<NativeFinalizerFunction>();

  Pointer<Void> createSessionOptions(
    Pointer<Void> runtime,
    OrtSessionOptions options, {
    List<OrtExecutionProvider>? resolvedProviders,
  }) {
    final Arena arena = Arena();
    try {
      final List<OrtExecutionProvider> providerRequests =
          resolvedProviders ?? options.providers;
      final Pointer<bindings.dort_session_config_t> config =
          arena<bindings.dort_session_config_t>();
      final Pointer<bindings.dort_provider_config_t> nativeProviders =
          providerRequests.isEmpty
          ? nullptr
          : arena<bindings.dort_provider_config_t>(providerRequests.length);
      for (var index = 0; index < providerRequests.length; index += 1) {
        final provider = providerRequests[index];
        final Pointer<bindings.dort_string_pair_t> nativeOptions =
            _nativeStringPairs(provider.options, arena);
        nativeProviders[index]
          ..struct_size = sizeOf<bindings.dort_provider_config_t>()
          ..reserved0 = 0
          ..provider_id_utf8 = _nativeUtf8(provider.id, arena)
          ..options = nativeOptions
          ..option_count = provider.options.length;
      }
      final Pointer<bindings.dort_string_pair_t> configEntries =
          _nativeStringPairs(options.configEntries, arena);

      config.ref
        ..struct_size = sizeOf<bindings.dort_session_config_t>()
        ..graph_optimization_level = options.graphOptimization.index
        ..execution_mode = options.executionMode.index
        ..intra_op_thread_count = options.intraOpThreads
        ..inter_op_thread_count = options.interOpThreads
        ..enable_cpu_memory_arena = options.enableCpuMemoryArena ? 1 : 0
        ..enable_memory_pattern = options.enableMemoryPattern ? 1 : 0
        ..deterministic_compute = options.deterministicCompute ? 1 : 0
        ..enable_profiling = options.enableProfiling ? 1 : 0
        ..log_severity = options.logSeverity.nativeValue
        ..log_verbosity = options.logVerbosity
        ..optimized_model_overwrite =
            options.optimizedModelOverwrite == OrtOverwritePolicy.replace
            ? 1
            : 0
        ..reserved0 = 0
        ..log_id_utf8 = _nativeUtf8(options.sessionLogId, arena)
        ..profile_path_prefix_utf8 = _optionalNativeUtf8(
          options.profilePathPrefix,
          arena,
        )
        ..optimized_model_path_utf8 = _optionalNativeUtf8(
          options.optimizedModelPath,
          arena,
        )
        ..artifact_root_utf8 = _optionalNativeUtf8(options.artifactRoot, arena)
        ..max_model_bytes = options.limits.maxModelBytes
        ..providers = nativeProviders
        ..provider_count = providerRequests.length
        ..config_entries = configEntries
        ..config_entry_count = options.configEntries.length;

      final Pointer<Pointer<bindings.dort_session_options_t>> output =
          arena<Pointer<bindings.dort_session_options_t>>();
      _throwIfStatus(
        _sessionOptionsCreate(
          runtime.cast<bindings.dort_runtime_t>(),
          config,
          output,
        ),
      );
      return _requiredHandle(output.value, 'session_options_create');
    } finally {
      arena.releaseAll();
    }
  }

  void releaseSessionOptions(Pointer<Void> options) {
    _sessionOptionsRelease(options.cast<bindings.dort_session_options_t>());
  }

  Pointer<Void> createSessionFromBytes({
    required Pointer<Void> runtime,
    required Pointer<Void> options,
    required Uint8List modelBytes,
  }) {
    final Arena arena = Arena();
    try {
      final Pointer<Uint8> nativeBytes = modelBytes.isEmpty
          ? nullptr
          : arena<Uint8>(modelBytes.length);
      if (modelBytes.isNotEmpty) {
        nativeBytes.asTypedList(modelBytes.length).setAll(0, modelBytes);
      }
      final Pointer<Pointer<bindings.dort_session_t>> output =
          arena<Pointer<bindings.dort_session_t>>();
      _throwIfStatus(
        _sessionCreateFromBytes(
          runtime.cast<bindings.dort_runtime_t>(),
          options.cast<bindings.dort_session_options_t>(),
          nativeBytes.cast<Void>(),
          modelBytes.length,
          output,
        ),
      );
      return _requiredHandle(output.value, 'session_create_from_bytes');
    } finally {
      arena.releaseAll();
    }
  }

  Pointer<Void> createSessionFromBytesWithExternalData({
    required Pointer<Void> runtime,
    required Pointer<Void> options,
    required Uint8List modelBytes,
    required Map<String, Uint8List> externalData,
  }) {
    final Arena arena = Arena();
    try {
      final Pointer<Uint8> nativeBytes = modelBytes.isEmpty
          ? nullptr
          : arena<Uint8>(modelBytes.length);
      if (modelBytes.isNotEmpty) {
        nativeBytes.asTypedList(modelBytes.length).setAll(0, modelBytes);
      }
      final Pointer<bindings.dort_external_data_t> nativeExternal =
          externalData.isEmpty
          ? nullptr
          : arena<bindings.dort_external_data_t>(externalData.length);
      var index = 0;
      for (final MapEntry<String, Uint8List> entry in externalData.entries) {
        final Uint8List nameBytes = Uint8List.fromList(utf8.encode(entry.key));
        final Pointer<Uint8> nativeName = arena<Uint8>(nameBytes.length);
        nativeName.asTypedList(nameBytes.length).setAll(0, nameBytes);
        final Pointer<Uint8> nativeData = entry.value.isEmpty
            ? nullptr
            : arena<Uint8>(entry.value.length);
        if (entry.value.isNotEmpty) {
          nativeData.asTypedList(entry.value.length).setAll(0, entry.value);
        }
        nativeExternal[index]
          ..struct_size = sizeOf<bindings.dort_external_data_t>()
          ..reserved0 = 0
          ..relative_name_utf8 = nativeName
          ..relative_name_length = nameBytes.length
          ..data = nativeData.cast<Void>()
          ..data_length = entry.value.length;
        index += 1;
      }
      final Pointer<Pointer<bindings.dort_session_t>> output =
          arena<Pointer<bindings.dort_session_t>>();
      _throwIfStatus(
        _sessionCreateFromBytesWithExternalData(
          runtime.cast<bindings.dort_runtime_t>(),
          options.cast<bindings.dort_session_options_t>(),
          nativeBytes.cast<Void>(),
          modelBytes.length,
          nativeExternal,
          externalData.length,
          output,
        ),
      );
      return _requiredHandle(
        output.value,
        'session_create_from_bytes_with_external_data',
      );
    } finally {
      arena.releaseAll();
    }
  }

  Pointer<Void> createSessionFromFile({
    required Pointer<Void> runtime,
    required Pointer<Void> options,
    required String modelPath,
    required String allowedRoot,
  }) {
    final Arena arena = Arena();
    try {
      final Pointer<Pointer<bindings.dort_session_t>> output =
          arena<Pointer<bindings.dort_session_t>>();
      _throwIfStatus(
        _sessionCreateFromFile(
          runtime.cast<bindings.dort_runtime_t>(),
          options.cast<bindings.dort_session_options_t>(),
          _nativeUtf8(modelPath, arena),
          _nativeUtf8(allowedRoot, arena),
          output,
        ),
      );
      return _requiredHandle(output.value, 'session_create_from_file');
    } finally {
      arena.releaseAll();
    }
  }

  void releaseSession(Pointer<Void> session) {
    _sessionRelease(session.cast<bindings.dort_session_t>());
  }

  String getSessionMetadataJson(Pointer<Void> session) => _ownedNativeString(
    (Pointer<bindings.dort_string_t> output) =>
        _sessionMetadataJson(session.cast<bindings.dort_session_t>(), output),
  );

  String getSessionTypeMetadataJson(Pointer<Void> session) =>
      _ownedNativeString(
        (Pointer<bindings.dort_string_t> output) => _sessionTypeMetadataJson(
          session.cast<bindings.dort_session_t>(),
          output,
        ),
      );

  String getSessionModelMetadataJson(Pointer<Void> session) =>
      _ownedNativeString(
        (Pointer<bindings.dort_string_t> output) => _sessionModelMetadataJson(
          session.cast<bindings.dort_session_t>(),
          output,
        ),
      );

  Pointer<Void> allocateBuffer({
    required Pointer<Void> runtime,
    required int byteLength,
    required int alignment,
  }) {
    final Pointer<Pointer<bindings.dort_buffer_t>> output =
        calloc<Pointer<bindings.dort_buffer_t>>();
    try {
      _throwIfStatus(
        _bufferAllocate(
          runtime.cast<bindings.dort_runtime_t>(),
          byteLength,
          alignment,
          output,
        ),
      );
      return _requiredHandle(output.value, 'buffer_allocate');
    } finally {
      calloc.free(output);
    }
  }

  void releaseBuffer(Pointer<Void> buffer) {
    _bufferRelease(buffer.cast<bindings.dort_buffer_t>());
  }

  int bufferByteLength(Pointer<Void> buffer) =>
      _bufferByteLength(buffer.cast<bindings.dort_buffer_t>());

  void writeBuffer(Pointer<Void> buffer, int offset, Uint8List bytes) {
    final Arena arena = Arena();
    try {
      final Pointer<Uint8> source = bytes.isEmpty
          ? nullptr
          : arena<Uint8>(bytes.length);
      if (bytes.isNotEmpty) {
        source.asTypedList(bytes.length).setAll(0, bytes);
      }
      _throwIfStatus(
        _bufferWrite(
          buffer.cast<bindings.dort_buffer_t>(),
          offset,
          source.cast<Void>(),
          bytes.length,
        ),
      );
    } finally {
      arena.releaseAll();
    }
  }

  Uint8List readBuffer(Pointer<Void> buffer, int offset, int byteLength) {
    if (byteLength == 0) {
      return Uint8List(0);
    }
    final Pointer<Uint8> destination = calloc<Uint8>(byteLength);
    try {
      _throwIfStatus(
        _bufferRead(
          buffer.cast<bindings.dort_buffer_t>(),
          offset,
          destination.cast<Void>(),
          byteLength,
        ),
      );
      return Uint8List.fromList(destination.asTypedList(byteLength));
    } finally {
      calloc.free(destination);
    }
  }

  FonixNativeDataLease acquireBufferData(Pointer<Void> buffer) =>
      _acquireDataLease(
        (
          Pointer<Pointer<bindings.dort_data_lease_t>> lease,
          Pointer<Pointer<Void>> data,
          Pointer<Size> byteLength,
        ) => _bufferDataAcquire(
          buffer.cast<bindings.dort_buffer_t>(),
          lease,
          data,
          byteLength,
        ),
        'buffer_data_acquire',
      );

  Pointer<Void> createTensorCopy({
    required Pointer<Void> runtime,
    required Uint8List bytes,
    required List<int> dimensions,
    required int elementType,
  }) {
    final Arena arena = Arena();
    try {
      final Pointer<Uint8> data = bytes.isEmpty
          ? nullptr
          : arena<Uint8>(bytes.length);
      if (bytes.isNotEmpty) {
        data.asTypedList(bytes.length).setAll(0, bytes);
      }
      final Pointer<Int64> shape = _nativeDimensions(dimensions, arena);
      final Pointer<Pointer<bindings.dort_value_t>> output =
          arena<Pointer<bindings.dort_value_t>>();
      _throwIfStatus(
        _tensorCreateCopy(
          runtime.cast<bindings.dort_runtime_t>(),
          data.cast<Void>(),
          bytes.length,
          shape,
          dimensions.length,
          elementType,
          output,
        ),
      );
      return _requiredHandle(output.value, 'tensor_create_copy');
    } finally {
      arena.releaseAll();
    }
  }

  Pointer<Void> createTensorWithBuffer({
    required Pointer<Void> runtime,
    required Pointer<Void> buffer,
    required int byteOffset,
    required int byteLength,
    required List<int> dimensions,
    required int elementType,
  }) {
    final Arena arena = Arena();
    try {
      final Pointer<Int64> shape = _nativeDimensions(dimensions, arena);
      final Pointer<Pointer<bindings.dort_value_t>> output =
          arena<Pointer<bindings.dort_value_t>>();
      _throwIfStatus(
        _tensorCreateWithBuffer(
          runtime.cast<bindings.dort_runtime_t>(),
          buffer.cast<bindings.dort_buffer_t>(),
          byteOffset,
          byteLength,
          shape,
          dimensions.length,
          elementType,
          output,
        ),
      );
      return _requiredHandle(output.value, 'tensor_create_with_buffer');
    } finally {
      arena.releaseAll();
    }
  }

  Pointer<Void> createStringTensorCopy({
    required Pointer<Void> runtime,
    required List<String> values,
    required List<int> dimensions,
  }) {
    final Arena arena = Arena();
    try {
      final Pointer<bindings.dort_utf8_span_t> nativeStrings = values.isEmpty
          ? nullptr
          : arena<bindings.dort_utf8_span_t>(values.length);
      for (var index = 0; index < values.length; index += 1) {
        final Uint8List bytes = Uint8List.fromList(utf8.encode(values[index]));
        final Pointer<Uint8> data = bytes.isEmpty
            ? nullptr
            : arena<Uint8>(bytes.length);
        if (bytes.isNotEmpty) {
          data.asTypedList(bytes.length).setAll(0, bytes);
        }
        nativeStrings[index]
          ..struct_size = sizeOf<bindings.dort_utf8_span_t>()
          ..reserved0 = 0
          ..data = data
          ..length = bytes.length;
      }
      final Pointer<Int64> shape = _nativeDimensions(dimensions, arena);
      final Pointer<Pointer<bindings.dort_value_t>> output =
          arena<Pointer<bindings.dort_value_t>>();
      _throwIfStatus(
        _tensorCreateStringsCopy(
          runtime.cast<bindings.dort_runtime_t>(),
          nativeStrings,
          values.length,
          shape,
          dimensions.length,
          output,
        ),
      );
      return _requiredHandle(output.value, 'tensor_create_strings_copy');
    } finally {
      arena.releaseAll();
    }
  }

  void releaseValue(Pointer<Void> value) {
    _valueRelease(value.cast<bindings.dort_value_t>());
  }

  void retainValue(Pointer<Void> value) {
    _valueRetain(value.cast<bindings.dort_value_t>());
  }

  int valueKind(Pointer<Void> value) {
    final Pointer<Uint32> output = calloc<Uint32>();
    try {
      _throwIfStatus(_valueKind(value.cast<bindings.dort_value_t>(), output));
      return output.value;
    } finally {
      calloc.free(output);
    }
  }

  int valueChildCount(Pointer<Void> value) {
    final Pointer<Size> output = calloc<Size>();
    try {
      _throwIfStatus(
        _valueChildCount(value.cast<bindings.dort_value_t>(), output),
      );
      return output.value;
    } finally {
      calloc.free(output);
    }
  }

  Pointer<Void> getValueChild(Pointer<Void> value, int index) {
    final Pointer<Pointer<bindings.dort_value_t>> output =
        calloc<Pointer<bindings.dort_value_t>>();
    try {
      _throwIfStatus(
        _valueChildGet(value.cast<bindings.dort_value_t>(), index, output),
      );
      return _requiredHandle(output.value, 'value_child_get');
    } finally {
      calloc.free(output);
    }
  }

  Pointer<Void> createSequence(
    Pointer<Void> runtime,
    List<Pointer<Void>> values,
  ) {
    final Arena arena = Arena();
    try {
      final Pointer<Pointer<bindings.dort_value_t>> nativeValues =
          values.isEmpty
          ? nullptr
          : arena<Pointer<bindings.dort_value_t>>(values.length);
      for (var index = 0; index < values.length; index += 1) {
        nativeValues[index] = values[index].cast<bindings.dort_value_t>();
      }
      final Pointer<Pointer<bindings.dort_value_t>> output =
          arena<Pointer<bindings.dort_value_t>>();
      _throwIfStatus(
        _sequenceCreate(
          runtime.cast<bindings.dort_runtime_t>(),
          nativeValues,
          values.length,
          output,
        ),
      );
      return _requiredHandle(output.value, 'sequence_create');
    } finally {
      arena.releaseAll();
    }
  }

  Pointer<Void> createMap(
    Pointer<Void> runtime,
    Pointer<Void> keys,
    Pointer<Void> values,
  ) {
    final Pointer<Pointer<bindings.dort_value_t>> output =
        calloc<Pointer<bindings.dort_value_t>>();
    try {
      _throwIfStatus(
        _mapCreate(
          runtime.cast<bindings.dort_runtime_t>(),
          keys.cast<bindings.dort_value_t>(),
          values.cast<bindings.dort_value_t>(),
          output,
        ),
      );
      return _requiredHandle(output.value, 'map_create');
    } finally {
      calloc.free(output);
    }
  }

  Pointer<Void> createOptionalNone(Pointer<Void> runtime) {
    final Pointer<Pointer<bindings.dort_value_t>> output =
        calloc<Pointer<bindings.dort_value_t>>();
    try {
      _throwIfStatus(
        _optionalNoneCreate(runtime.cast<bindings.dort_runtime_t>(), output),
      );
      return _requiredHandle(output.value, 'optional_none_create');
    } finally {
      calloc.free(output);
    }
  }

  Pointer<Void> createOptionalSome(Pointer<Void> containedValue) {
    final Pointer<Pointer<bindings.dort_value_t>> output =
        calloc<Pointer<bindings.dort_value_t>>();
    try {
      _throwIfStatus(
        _optionalSomeCreate(
          containedValue.cast<bindings.dort_value_t>(),
          output,
        ),
      );
      return _requiredHandle(output.value, 'optional_some_create');
    } finally {
      calloc.free(output);
    }
  }

  String getTensorInfoJson(Pointer<Void> value) => _ownedNativeString(
    (Pointer<bindings.dort_string_t> output) =>
        _tensorInfoJson(value.cast<bindings.dort_value_t>(), output),
  );

  Uint8List copyTensorData(Pointer<Void> value, int maximumBytes) {
    final Pointer<Size> required = calloc<Size>();
    try {
      _throwIfStatus(
        _tensorCopyData(
          value.cast<bindings.dort_value_t>(),
          nullptr,
          0,
          required,
        ),
      );
      final int byteLength = required.value;
      if (byteLength < 0 || byteLength > maximumBytes) {
        throw StateError('Native tensor data exceeds the Dart copy limit.');
      }
      if (byteLength == 0) {
        return Uint8List(0);
      }
      final Pointer<Uint8> destination = calloc<Uint8>(byteLength);
      try {
        _throwIfStatus(
          _tensorCopyData(
            value.cast<bindings.dort_value_t>(),
            destination.cast<Void>(),
            byteLength,
            required,
          ),
        );
        if (required.value != byteLength) {
          throw StateError('Native tensor size changed during a copied read.');
        }
        return Uint8List.fromList(destination.asTypedList(byteLength));
      } finally {
        calloc.free(destination);
      }
    } finally {
      calloc.free(required);
    }
  }

  List<String> copyTensorStrings(
    Pointer<Void> value,
    int maximumCount,
    int maximumBytes,
  ) {
    final Pointer<Size> count = calloc<Size>();
    try {
      _throwIfStatus(
        _tensorStringCount(value.cast<bindings.dort_value_t>(), count),
      );
      if (count.value != maximumCount) {
        throw StateError(
          'Native string tensor count differs from its concrete shape.',
        );
      }
      final List<String> result = <String>[];
      var totalBytes = 0;
      for (var index = 0; index < count.value; index += 1) {
        final String copied = _ownedNativeString(
          (Pointer<bindings.dort_string_t> output) => _tensorStringGet(
            value.cast<bindings.dort_value_t>(),
            index,
            output,
          ),
        );
        if (copied.contains('\u0000')) {
          throw StateError(
            'Native string tensor contains an embedded NUL character.',
          );
        }
        final int byteLength = utf8.encode(copied).length;
        if (byteLength > maximumBytes - totalBytes) {
          throw StateError('Native string tensor exceeds the Dart byte limit.');
        }
        totalBytes += byteLength;
        result.add(copied);
      }
      if (totalBytes != maximumBytes) {
        throw StateError(
          'Native string tensor byte length changed during copy.',
        );
      }
      return List<String>.unmodifiable(result);
    } finally {
      calloc.free(count);
    }
  }

  FonixNativeDataLease acquireTensorData(Pointer<Void> value) =>
      _acquireDataLease(
        (
          Pointer<Pointer<bindings.dort_data_lease_t>> lease,
          Pointer<Pointer<Void>> data,
          Pointer<Size> byteLength,
        ) => _tensorDataAcquire(
          value.cast<bindings.dort_value_t>(),
          lease,
          data,
          byteLength,
        ),
        'tensor_data_acquire',
      );

  void releaseDataLease(Pointer<Void> lease) {
    _dataLeaseRelease(lease.cast<bindings.dort_data_lease_t>());
    _dataLeaseReleaseCount += 1;
  }

  Pointer<Void> createRunOptions(Pointer<Void> runtime) {
    final Pointer<Pointer<bindings.dort_run_options_t>> output =
        calloc<Pointer<bindings.dort_run_options_t>>();
    try {
      _throwIfStatus(
        _runOptionsCreate(runtime.cast<bindings.dort_runtime_t>(), output),
      );
      return _requiredHandle(output.value, 'run_options_create');
    } finally {
      calloc.free(output);
    }
  }

  void releaseRunOptions(Pointer<Void> options) {
    _runOptionsRelease(options.cast<bindings.dort_run_options_t>());
  }

  void setRunTermination(Pointer<Void> options) {
    _throwIfStatus(
      _runOptionsSetTerminate(options.cast<bindings.dort_run_options_t>()),
    );
  }

  void unsetRunTermination(Pointer<Void> options) {
    _throwIfStatus(
      _runOptionsUnsetTerminate(options.cast<bindings.dort_run_options_t>()),
    );
  }

  void startRunProfiling(Pointer<Void> options, String artifactRoot) {
    final Arena arena = Arena();
    try {
      _throwIfStatus(
        _runOptionsProfilingStart(
          options.cast<bindings.dort_run_options_t>(),
          _nativeUtf8(artifactRoot, arena),
        ),
      );
    } finally {
      arena.releaseAll();
    }
  }

  String finishRunProfiling(Pointer<Void> options) {
    final Pointer<bindings.dort_string_t> output =
        calloc<bindings.dort_string_t>();
    try {
      _throwIfStatus(
        _runOptionsProfilingFinish(
          options.cast<bindings.dort_run_options_t>(),
          output,
        ),
      );
      return _copyNativeString(
        output,
        maximumBytes: _maximumProviderProfileBytes,
      );
    } finally {
      _symbols.stringRelease(output);
      calloc.free(output);
    }
  }

  int registerCancelToken(Pointer<Void> options) {
    final Pointer<Uint64> output = calloc<Uint64>();
    try {
      _throwIfStatus(
        _cancelTokenRegister(
          options.cast<bindings.dort_run_options_t>(),
          output,
        ),
      );
      final int token = output.value;
      if (token <= 0) {
        throw StateError('cancel_token_register returned an invalid token.');
      }
      return token;
    } finally {
      calloc.free(output);
    }
  }

  bool requestCancelToken(int token) {
    final Pointer<Uint32> output = calloc<Uint32>();
    try {
      _throwIfStatus(_cancelTokenRequest(token, output));
      if (output.value > 1) {
        throw StateError('cancel_token_request returned an invalid boolean.');
      }
      return output.value == 1;
    } finally {
      calloc.free(output);
    }
  }

  bool finishCancelToken(int token) {
    final Pointer<Uint32> output = calloc<Uint32>();
    try {
      _throwIfStatus(_cancelTokenFinish(token, output));
      if (output.value > 1) {
        throw StateError('cancel_token_finish returned an invalid boolean.');
      }
      return output.value == 1;
    } finally {
      calloc.free(output);
    }
  }

  Pointer<Void> runSession({
    required Pointer<Void> session,
    required Pointer<Void>? runOptions,
    required List<FonixNativeRunInput> inputs,
    required List<String> outputNames,
  }) {
    final Arena arena = Arena();
    try {
      final Pointer<bindings.dort_named_value_t> nativeInputs = inputs.isEmpty
          ? nullptr
          : arena<bindings.dort_named_value_t>(inputs.length);
      for (var index = 0; index < inputs.length; index += 1) {
        nativeInputs[index]
          ..struct_size = sizeOf<bindings.dort_named_value_t>()
          ..reserved0 = 0
          ..name_utf8 = _nativeUtf8(inputs[index].name, arena)
          ..value = inputs[index].value.cast<bindings.dort_value_t>();
      }
      final Pointer<Pointer<Char>> nativeOutputNames = _nativeNames(
        outputNames,
        arena,
      );
      final Pointer<Pointer<bindings.dort_run_result_t>> output =
          arena<Pointer<bindings.dort_run_result_t>>();
      _throwIfStatus(
        _sessionRun(
          session.cast<bindings.dort_session_t>(),
          runOptions == null
              ? nullptr
              : runOptions.cast<bindings.dort_run_options_t>(),
          nativeInputs,
          inputs.length,
          nativeOutputNames,
          outputNames.length,
          output,
        ),
      );
      return _requiredHandle(output.value, 'session_run');
    } finally {
      arena.releaseAll();
    }
  }

  void releaseRunResult(Pointer<Void> result) {
    _runResultRelease(result.cast<bindings.dort_run_result_t>());
  }

  int runResultCount(Pointer<Void> result) =>
      _runResultCount(result.cast<bindings.dort_run_result_t>());

  FonixNativeNamedValue getRunResultValue(Pointer<Void> result, int index) {
    final Pointer<bindings.dort_string_t> name =
        calloc<bindings.dort_string_t>();
    final Pointer<Pointer<bindings.dort_value_t>> value =
        calloc<Pointer<bindings.dort_value_t>>();
    var succeeded = false;
    try {
      _throwIfStatus(
        _runResultGet(
          result.cast<bindings.dort_run_result_t>(),
          index,
          name,
          value,
        ),
      );
      final Pointer<Void> handle = _requiredHandle(
        value.value,
        'run_result_get',
      );
      final String copiedName = _copyNativeString(name);
      succeeded = true;
      return FonixNativeNamedValue(name: copiedName, value: handle);
    } finally {
      if (!succeeded && value.value != nullptr) {
        _valueRelease(value.value);
      }
      _symbols.stringRelease(name);
      calloc.free(name);
      calloc.free(value);
    }
  }

  FonixNativeDataLease _acquireDataLease(
    Pointer<bindings.dort_status_t> Function(
      Pointer<Pointer<bindings.dort_data_lease_t>> lease,
      Pointer<Pointer<Void>> data,
      Pointer<Size> byteLength,
    )
    call,
    String operation,
  ) {
    final Pointer<Pointer<bindings.dort_data_lease_t>> lease =
        calloc<Pointer<bindings.dort_data_lease_t>>();
    final Pointer<Pointer<Void>> data = calloc<Pointer<Void>>();
    final Pointer<Size> byteLength = calloc<Size>();
    var succeeded = false;
    try {
      _throwIfStatus(call(lease, data, byteLength));
      final Pointer<Void> leaseHandle = _requiredHandle(lease.value, operation);
      if (byteLength.value != 0 && data.value == nullptr) {
        throw StateError('The native lease returned no data pointer.');
      }
      succeeded = true;
      return FonixNativeDataLease(
        lease: leaseHandle,
        data: data.value,
        byteLength: byteLength.value,
      );
    } finally {
      if (!succeeded && lease.value != nullptr) {
        _dataLeaseRelease(lease.value);
      }
      calloc.free(lease);
      calloc.free(data);
      calloc.free(byteLength);
    }
  }

  String _ownedNativeString(
    Pointer<bindings.dort_status_t> Function(
      Pointer<bindings.dort_string_t> output,
    )
    call,
  ) {
    final Pointer<bindings.dort_string_t> output =
        calloc<bindings.dort_string_t>();
    try {
      _throwIfStatus(call(output));
      return _copyNativeString(output);
    } finally {
      _symbols.stringRelease(output);
      calloc.free(output);
    }
  }

  void _throwIfStatus(Pointer<bindings.dort_status_t> status) {
    if (status == nullptr) {
      return;
    }

    try {
      final int ortCode = _symbols.statusOrtCode(status);
      throw FonixNativeFailure(
        domain: _symbols.statusDomain(status),
        code: _symbols.statusCode(status),
        ortCode: ortCode == 0 ? null : ortCode,
        operation: _copyNullTerminatedUtf8(
          _symbols.statusOperation(status),
          _maximumStatusOperationBytes,
          'native status operation',
        ),
        message: _copyNullTerminatedUtf8(
          _symbols.statusMessage(status),
          _maximumStatusMessageBytes,
          'native status message',
        ),
      );
    } finally {
      _symbols.statusRelease(status);
    }
  }

  String _copyNativeString(
    Pointer<bindings.dort_string_t> value, {
    int maximumBytes = _maximumNativeJsonBytes,
  }) {
    final bindings.dort_string_t native = value.ref;
    if (native.struct_size != sizeOf<bindings.dort_string_t>()) {
      throw StateError(
        'The native string result has an incompatible struct size.',
      );
    }
    if (native.length < 0 || native.length > maximumBytes) {
      throw StateError('The native string result exceeds the Dart limit.');
    }
    if (native.length != 0 && native.data == nullptr) {
      throw StateError('The native string result has no data pointer.');
    }
    if (native.length == 0) {
      return '';
    }
    final List<int> bytes = List<int>.of(
      native.data.asTypedList(native.length),
      growable: false,
    );
    return const Utf8Decoder(allowMalformed: false).convert(bytes);
  }
}

int _sourceKind(OrtRuntimeSourceKind kind) => switch (kind) {
  OrtRuntimeSourceKind.linked =>
    bindings.dort_runtime_source_kind.DORT_RUNTIME_SOURCE_LINKED,
  OrtRuntimeSourceKind.bundled =>
    bindings.dort_runtime_source_kind.DORT_RUNTIME_SOURCE_BUNDLED,
  OrtRuntimeSourceKind.process =>
    bindings.dort_runtime_source_kind.DORT_RUNTIME_SOURCE_PROCESS,
  OrtRuntimeSourceKind.file =>
    bindings.dort_runtime_source_kind.DORT_RUNTIME_SOURCE_FILE,
};

Pointer<Char> _nativeUtf8(String value, Allocator allocator) =>
    value.toNativeUtf8(allocator: allocator).cast<Char>();

Pointer<Char> _optionalNativeUtf8(String? value, Allocator allocator) =>
    value == null ? nullptr : _nativeUtf8(value, allocator);

Pointer<Pointer<Char>> _nativeNames(List<String> names, Allocator allocator) {
  if (names.isEmpty) {
    return nullptr;
  }
  final Pointer<Pointer<Char>> output = allocator<Pointer<Char>>(names.length);
  for (var index = 0; index < names.length; index += 1) {
    output[index] = _nativeUtf8(names[index], allocator);
  }
  return output;
}

Pointer<bindings.dort_string_pair_t> _nativeStringPairs(
  Map<String, String> values,
  Allocator allocator,
) {
  if (values.isEmpty) {
    return nullptr;
  }
  final Pointer<bindings.dort_string_pair_t> output =
      allocator<bindings.dort_string_pair_t>(values.length);
  var index = 0;
  for (final MapEntry<String, String> entry in values.entries) {
    output[index]
      ..struct_size = sizeOf<bindings.dort_string_pair_t>()
      ..reserved0 = 0
      ..key_utf8 = _nativeUtf8(entry.key, allocator)
      ..value_utf8 = _nativeUtf8(entry.value, allocator);
    index += 1;
  }
  return output;
}

Pointer<Int64> _nativeDimensions(List<int> dimensions, Allocator allocator) {
  if (dimensions.isEmpty) {
    return nullptr;
  }
  final Pointer<Int64> output = allocator<Int64>(dimensions.length);
  for (var index = 0; index < dimensions.length; index += 1) {
    output[index] = dimensions[index];
  }
  return output;
}

Pointer<Void> _requiredHandle<T extends NativeType>(
  Pointer<T> handle,
  String operation,
) {
  if (handle == nullptr) {
    throw FonixNativeFailure(
      domain: bindings.dort_error_domain.DORT_ERROR_DOMAIN_SHIM,
      code: bindings.dort_error_code.DORT_ERROR_PLATFORM,
      ortCode: null,
      operation: operation,
      message: 'The native shim returned a null handle without a status.',
    );
  }
  return handle.cast<Void>();
}

String _copyNullTerminatedUtf8(
  Pointer<Char> value,
  int maximumBytes,
  String field,
) {
  if (value == nullptr) {
    throw StateError('$field is null.');
  }
  final Pointer<Uint8> bytes = value.cast<Uint8>();
  final List<int> copy = <int>[];
  for (var index = 0; index <= maximumBytes; index += 1) {
    final int byte = bytes[index];
    if (byte == 0) {
      return const Utf8Decoder(allowMalformed: false).convert(copy);
    }
    if (index == maximumBytes) {
      break;
    }
    copy.add(byte);
  }
  throw StateError('$field is not terminated within its ABI limit.');
}

abstract interface class _FonixSymbols {
  Pointer<T> lookup<T extends NativeType>(String symbol);

  int getAbiVersion();

  int getOrtApiCompatibilityFloor();

  Pointer<bindings.dort_status_t> getBuildManifestJson(
    Pointer<bindings.dort_string_t> output,
  );

  void stringRelease(Pointer<bindings.dort_string_t> value);

  int statusDomain(Pointer<bindings.dort_status_t> status);

  int statusCode(Pointer<bindings.dort_status_t> status);

  int statusOrtCode(Pointer<bindings.dort_status_t> status);

  Pointer<Char> statusOperation(Pointer<bindings.dort_status_t> status);

  Pointer<Char> statusMessage(Pointer<bindings.dort_status_t> status);

  void statusRelease(Pointer<bindings.dort_status_t> status);

  Pointer<bindings.dort_status_t> runtimeOpen(
    Pointer<bindings.dort_runtime_config_t> config,
    Pointer<Pointer<bindings.dort_runtime_t>> output,
  );

  void runtimeRelease(Pointer<bindings.dort_runtime_t> runtime);

  Pointer<bindings.dort_status_t> runtimeInfoJson(
    Pointer<bindings.dort_runtime_t> runtime,
    Pointer<bindings.dort_string_t> output,
  );

  Pointer<NativeFunction<Void Function(Pointer<bindings.dort_runtime_t>)>>
  get runtimeReleaseAddress;
}

final class _NativeAssetSymbols implements _FonixSymbols {
  @override
  Pointer<T> lookup<T extends NativeType>(String symbol) => switch (symbol) {
    'dort_get_abi_version' => bindings.addresses.dort_get_abi_version.cast<T>(),
    'dort_get_ort_api_compatibility_floor' =>
      bindings.addresses.dort_get_ort_api_compatibility_floor.cast<T>(),
    'dort_get_build_manifest_json' =>
      bindings.addresses.dort_get_build_manifest_json.cast<T>(),
    'dort_string_release' => bindings.addresses.dort_string_release.cast<T>(),
    'dort_status_domain' => bindings.addresses.dort_status_domain.cast<T>(),
    'dort_status_code' => bindings.addresses.dort_status_code.cast<T>(),
    'dort_status_ort_code' => bindings.addresses.dort_status_ort_code.cast<T>(),
    'dort_status_operation' =>
      bindings.addresses.dort_status_operation.cast<T>(),
    'dort_status_message' => bindings.addresses.dort_status_message.cast<T>(),
    'dort_status_release' => bindings.addresses.dort_status_release.cast<T>(),
    'dort_runtime_open' => bindings.addresses.dort_runtime_open.cast<T>(),
    'dort_runtime_retain' => bindings.addresses.dort_runtime_retain.cast<T>(),
    'dort_runtime_release' => bindings.addresses.dort_runtime_release.cast<T>(),
    'dort_runtime_info_json' =>
      bindings.addresses.dort_runtime_info_json.cast<T>(),
    'dort_runtime_available_providers_json' =>
      bindings.addresses.dort_runtime_available_providers_json.cast<T>(),
    'dort_session_options_create' =>
      bindings.addresses.dort_session_options_create.cast<T>(),
    'dort_session_options_retain' =>
      bindings.addresses.dort_session_options_retain.cast<T>(),
    'dort_session_options_release' =>
      bindings.addresses.dort_session_options_release.cast<T>(),
    'dort_session_create_from_bytes' =>
      bindings.addresses.dort_session_create_from_bytes.cast<T>(),
    'dort_session_create_from_file' =>
      bindings.addresses.dort_session_create_from_file.cast<T>(),
    'dort_session_create_from_bytes_with_external_data' =>
      bindings.addresses.dort_session_create_from_bytes_with_external_data
          .cast<T>(),
    'dort_session_retain' => bindings.addresses.dort_session_retain.cast<T>(),
    'dort_session_release' => bindings.addresses.dort_session_release.cast<T>(),
    'dort_session_metadata_json' =>
      bindings.addresses.dort_session_metadata_json.cast<T>(),
    'dort_session_type_metadata_json' =>
      bindings.addresses.dort_session_type_metadata_json.cast<T>(),
    'dort_session_model_metadata_json' =>
      bindings.addresses.dort_session_model_metadata_json.cast<T>(),
    'dort_buffer_allocate' => bindings.addresses.dort_buffer_allocate.cast<T>(),
    'dort_buffer_retain' => bindings.addresses.dort_buffer_retain.cast<T>(),
    'dort_buffer_release' => bindings.addresses.dort_buffer_release.cast<T>(),
    'dort_buffer_byte_length' =>
      bindings.addresses.dort_buffer_byte_length.cast<T>(),
    'dort_buffer_write' => bindings.addresses.dort_buffer_write.cast<T>(),
    'dort_buffer_read' => bindings.addresses.dort_buffer_read.cast<T>(),
    'dort_buffer_data_acquire' =>
      bindings.addresses.dort_buffer_data_acquire.cast<T>(),
    'dort_tensor_create_copy' =>
      bindings.addresses.dort_tensor_create_copy.cast<T>(),
    'dort_tensor_create_with_buffer' =>
      bindings.addresses.dort_tensor_create_with_buffer.cast<T>(),
    'dort_tensor_create_strings_copy' =>
      bindings.addresses.dort_tensor_create_strings_copy.cast<T>(),
    'dort_value_retain' => bindings.addresses.dort_value_retain.cast<T>(),
    'dort_value_release' => bindings.addresses.dort_value_release.cast<T>(),
    'dort_value_kind' => bindings.addresses.dort_value_kind$1.cast<T>(),
    'dort_value_child_count' =>
      bindings.addresses.dort_value_child_count.cast<T>(),
    'dort_value_child_get' => bindings.addresses.dort_value_child_get.cast<T>(),
    'dort_sequence_create' => bindings.addresses.dort_sequence_create.cast<T>(),
    'dort_map_create' => bindings.addresses.dort_map_create.cast<T>(),
    'dort_optional_none_create' =>
      bindings.addresses.dort_optional_none_create.cast<T>(),
    'dort_optional_some_create' =>
      bindings.addresses.dort_optional_some_create.cast<T>(),
    'dort_tensor_info_json' =>
      bindings.addresses.dort_tensor_info_json.cast<T>(),
    'dort_tensor_copy_data' =>
      bindings.addresses.dort_tensor_copy_data.cast<T>(),
    'dort_tensor_string_count' =>
      bindings.addresses.dort_tensor_string_count.cast<T>(),
    'dort_tensor_string_get' =>
      bindings.addresses.dort_tensor_string_get.cast<T>(),
    'dort_tensor_data_acquire' =>
      bindings.addresses.dort_tensor_data_acquire.cast<T>(),
    'dort_data_lease_retain' =>
      bindings.addresses.dort_data_lease_retain.cast<T>(),
    'dort_data_lease_release' =>
      bindings.addresses.dort_data_lease_release.cast<T>(),
    'dort_run_options_create' =>
      bindings.addresses.dort_run_options_create.cast<T>(),
    'dort_run_options_retain' =>
      bindings.addresses.dort_run_options_retain.cast<T>(),
    'dort_run_options_release' =>
      bindings.addresses.dort_run_options_release.cast<T>(),
    'dort_run_options_set_terminate' =>
      bindings.addresses.dort_run_options_set_terminate.cast<T>(),
    'dort_run_options_unset_terminate' =>
      bindings.addresses.dort_run_options_unset_terminate.cast<T>(),
    'dort_run_options_profiling_start' =>
      bindings.addresses.dort_run_options_profiling_start.cast<T>(),
    'dort_run_options_profiling_finish' =>
      bindings.addresses.dort_run_options_profiling_finish.cast<T>(),
    'dort_cancel_token_register' =>
      bindings.addresses.dort_cancel_token_register.cast<T>(),
    'dort_cancel_token_request' =>
      bindings.addresses.dort_cancel_token_request.cast<T>(),
    'dort_cancel_token_finish' =>
      bindings.addresses.dort_cancel_token_finish.cast<T>(),
    'dort_session_run' => bindings.addresses.dort_session_run.cast<T>(),
    'dort_run_result_retain' =>
      bindings.addresses.dort_run_result_retain.cast<T>(),
    'dort_run_result_release' =>
      bindings.addresses.dort_run_result_release.cast<T>(),
    'dort_run_result_count' =>
      bindings.addresses.dort_run_result_count.cast<T>(),
    'dort_run_result_get' => bindings.addresses.dort_run_result_get.cast<T>(),
    _ => throw ArgumentError.value(symbol, 'symbol', 'unknown Fonix symbol'),
  };

  @override
  int getAbiVersion() => bindings.dort_get_abi_version();

  @override
  int getOrtApiCompatibilityFloor() =>
      bindings.dort_get_ort_api_compatibility_floor();

  @override
  Pointer<bindings.dort_status_t> getBuildManifestJson(
    Pointer<bindings.dort_string_t> output,
  ) => bindings.dort_get_build_manifest_json(output);

  @override
  void stringRelease(Pointer<bindings.dort_string_t> value) {
    bindings.dort_string_release(value);
  }

  @override
  int statusDomain(Pointer<bindings.dort_status_t> status) =>
      bindings.dort_status_domain(status);

  @override
  int statusCode(Pointer<bindings.dort_status_t> status) =>
      bindings.dort_status_code(status);

  @override
  int statusOrtCode(Pointer<bindings.dort_status_t> status) =>
      bindings.dort_status_ort_code(status);

  @override
  Pointer<Char> statusOperation(Pointer<bindings.dort_status_t> status) =>
      bindings.dort_status_operation(status);

  @override
  Pointer<Char> statusMessage(Pointer<bindings.dort_status_t> status) =>
      bindings.dort_status_message(status);

  @override
  void statusRelease(Pointer<bindings.dort_status_t> status) {
    bindings.dort_status_release(status);
  }

  @override
  Pointer<bindings.dort_status_t> runtimeOpen(
    Pointer<bindings.dort_runtime_config_t> config,
    Pointer<Pointer<bindings.dort_runtime_t>> output,
  ) => bindings.dort_runtime_open(config, output);

  @override
  void runtimeRelease(Pointer<bindings.dort_runtime_t> runtime) {
    bindings.dort_runtime_release(runtime);
  }

  @override
  Pointer<bindings.dort_status_t> runtimeInfoJson(
    Pointer<bindings.dort_runtime_t> runtime,
    Pointer<bindings.dort_string_t> output,
  ) => bindings.dort_runtime_info_json(runtime, output);

  @override
  Pointer<NativeFunction<Void Function(Pointer<bindings.dort_runtime_t>)>>
  get runtimeReleaseAddress => bindings.addresses.dort_runtime_release;
}

final class _DynamicLibrarySymbols implements _FonixSymbols {
  _DynamicLibrarySymbols(this._library);

  final DynamicLibrary _library;

  @override
  Pointer<T> lookup<T extends NativeType>(String symbol) =>
      _library.lookup<T>(symbol);

  late final int Function() _getAbiVersion = _library
      .lookupFunction<Uint32 Function(), int Function()>(
        'dort_get_abi_version',
      );
  late final int Function() _getOrtApiCompatibilityFloor = _library
      .lookupFunction<Uint32 Function(), int Function()>(
        'dort_get_ort_api_compatibility_floor',
      );
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_string_t>,
  )
  _getBuildManifestJson = _library
      .lookupFunction<
        Pointer<bindings.dort_status_t> Function(
          Pointer<bindings.dort_string_t>,
        ),
        Pointer<bindings.dort_status_t> Function(
          Pointer<bindings.dort_string_t>,
        )
      >('dort_get_build_manifest_json');
  late final void Function(Pointer<bindings.dort_string_t>) _stringRelease =
      _library.lookupFunction<
        Void Function(Pointer<bindings.dort_string_t>),
        void Function(Pointer<bindings.dort_string_t>)
      >('dort_string_release');
  late final int Function(Pointer<bindings.dort_status_t>) _statusDomain =
      _library.lookupFunction<
        Uint32 Function(Pointer<bindings.dort_status_t>),
        int Function(Pointer<bindings.dort_status_t>)
      >('dort_status_domain');
  late final int Function(Pointer<bindings.dort_status_t>) _statusCode =
      _library.lookupFunction<
        Int32 Function(Pointer<bindings.dort_status_t>),
        int Function(Pointer<bindings.dort_status_t>)
      >('dort_status_code');
  late final int Function(Pointer<bindings.dort_status_t>) _statusOrtCode =
      _library.lookupFunction<
        Int32 Function(Pointer<bindings.dort_status_t>),
        int Function(Pointer<bindings.dort_status_t>)
      >('dort_status_ort_code');
  late final Pointer<Char> Function(Pointer<bindings.dort_status_t>)
  _statusOperation = _library
      .lookupFunction<
        Pointer<Char> Function(Pointer<bindings.dort_status_t>),
        Pointer<Char> Function(Pointer<bindings.dort_status_t>)
      >('dort_status_operation');
  late final Pointer<Char> Function(Pointer<bindings.dort_status_t>)
  _statusMessage = _library
      .lookupFunction<
        Pointer<Char> Function(Pointer<bindings.dort_status_t>),
        Pointer<Char> Function(Pointer<bindings.dort_status_t>)
      >('dort_status_message');
  late final void Function(Pointer<bindings.dort_status_t>) _statusRelease =
      _library.lookupFunction<
        Void Function(Pointer<bindings.dort_status_t>),
        void Function(Pointer<bindings.dort_status_t>)
      >('dort_status_release');
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_config_t>,
    Pointer<Pointer<bindings.dort_runtime_t>>,
  )
  _runtimeOpen = _library
      .lookupFunction<
        Pointer<bindings.dort_status_t> Function(
          Pointer<bindings.dort_runtime_config_t>,
          Pointer<Pointer<bindings.dort_runtime_t>>,
        ),
        Pointer<bindings.dort_status_t> Function(
          Pointer<bindings.dort_runtime_config_t>,
          Pointer<Pointer<bindings.dort_runtime_t>>,
        )
      >('dort_runtime_open');
  late final void Function(Pointer<bindings.dort_runtime_t>) _runtimeRelease =
      _library.lookupFunction<
        Void Function(Pointer<bindings.dort_runtime_t>),
        void Function(Pointer<bindings.dort_runtime_t>)
      >('dort_runtime_release');
  late final Pointer<bindings.dort_status_t> Function(
    Pointer<bindings.dort_runtime_t>,
    Pointer<bindings.dort_string_t>,
  )
  _runtimeInfoJson = _library
      .lookupFunction<
        Pointer<bindings.dort_status_t> Function(
          Pointer<bindings.dort_runtime_t>,
          Pointer<bindings.dort_string_t>,
        ),
        Pointer<bindings.dort_status_t> Function(
          Pointer<bindings.dort_runtime_t>,
          Pointer<bindings.dort_string_t>,
        )
      >('dort_runtime_info_json');

  @override
  int getAbiVersion() => _getAbiVersion();

  @override
  int getOrtApiCompatibilityFloor() => _getOrtApiCompatibilityFloor();

  @override
  Pointer<bindings.dort_status_t> getBuildManifestJson(
    Pointer<bindings.dort_string_t> output,
  ) => _getBuildManifestJson(output);

  @override
  void stringRelease(Pointer<bindings.dort_string_t> value) {
    _stringRelease(value);
  }

  @override
  int statusDomain(Pointer<bindings.dort_status_t> status) =>
      _statusDomain(status);

  @override
  int statusCode(Pointer<bindings.dort_status_t> status) => _statusCode(status);

  @override
  int statusOrtCode(Pointer<bindings.dort_status_t> status) =>
      _statusOrtCode(status);

  @override
  Pointer<Char> statusOperation(Pointer<bindings.dort_status_t> status) =>
      _statusOperation(status);

  @override
  Pointer<Char> statusMessage(Pointer<bindings.dort_status_t> status) =>
      _statusMessage(status);

  @override
  void statusRelease(Pointer<bindings.dort_status_t> status) {
    _statusRelease(status);
  }

  @override
  Pointer<bindings.dort_status_t> runtimeOpen(
    Pointer<bindings.dort_runtime_config_t> config,
    Pointer<Pointer<bindings.dort_runtime_t>> output,
  ) => _runtimeOpen(config, output);

  @override
  void runtimeRelease(Pointer<bindings.dort_runtime_t> runtime) {
    _runtimeRelease(runtime);
  }

  @override
  Pointer<bindings.dort_status_t> runtimeInfoJson(
    Pointer<bindings.dort_runtime_t> runtime,
    Pointer<bindings.dort_string_t> output,
  ) => _runtimeInfoJson(runtime, output);

  @override
  Pointer<NativeFunction<Void Function(Pointer<bindings.dort_runtime_t>)>>
  get runtimeReleaseAddress => _library
      .lookup<NativeFunction<Void Function(Pointer<bindings.dort_runtime_t>)>>(
        'dort_runtime_release',
      );
}
