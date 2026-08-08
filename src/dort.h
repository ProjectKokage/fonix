#ifndef FONIX_DORT_H_
#define FONIX_DORT_H_

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#if defined(FONIX_SHIM_STATIC)
#define DORT_API
#elif defined(FONIX_SHIM_BUILDING)
#define DORT_API __declspec(dllexport)
#else
#define DORT_API __declspec(dllimport)
#endif
#define DORT_CALL __cdecl
#else
#define DORT_API __attribute__((visibility("default")))
#define DORT_CALL
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define DORT_ABI_VERSION 1u
#define DORT_ORT_API_COMPATIBILITY_FLOOR 27u
#define DORT_RUNTIME_CONFIG_V1_SIZE ((uint32_t)sizeof(dort_runtime_config_t))
#define DORT_SESSION_CONFIG_V1_SIZE ((uint32_t)sizeof(dort_session_config_t))
#define DORT_STRING_PAIR_V1_SIZE ((uint32_t)sizeof(dort_string_pair_t))
#define DORT_PROVIDER_CONFIG_V1_SIZE ((uint32_t)sizeof(dort_provider_config_t))
#define DORT_NAMED_VALUE_V1_SIZE ((uint32_t)sizeof(dort_named_value_t))
#define DORT_UTF8_SPAN_V1_SIZE ((uint32_t)sizeof(dort_utf8_span_t))
#define DORT_EXTERNAL_DATA_V1_SIZE ((uint32_t)sizeof(dort_external_data_t))

typedef struct dort_status dort_status_t;
typedef struct dort_runtime dort_runtime_t;
typedef struct dort_session_options dort_session_options_t;
typedef struct dort_session dort_session_t;
typedef struct dort_run_options dort_run_options_t;
typedef struct dort_buffer dort_buffer_t;
typedef struct dort_value dort_value_t;
typedef struct dort_run_result dort_run_result_t;
typedef struct dort_data_lease dort_data_lease_t;

typedef enum dort_runtime_source_kind {
  DORT_RUNTIME_SOURCE_LINKED = 1,
  DORT_RUNTIME_SOURCE_BUNDLED = 2,
  DORT_RUNTIME_SOURCE_PROCESS = 3,
  DORT_RUNTIME_SOURCE_FILE = 4
} dort_runtime_source_kind_t;

typedef enum dort_log_severity {
  DORT_LOG_VERBOSE = 0,
  DORT_LOG_INFO = 1,
  DORT_LOG_WARNING = 2,
  DORT_LOG_ERROR = 3,
  DORT_LOG_FATAL = 4
} dort_log_severity_t;

typedef enum dort_error_domain {
  DORT_ERROR_DOMAIN_NONE = 0,
  DORT_ERROR_DOMAIN_SHIM = 1,
  DORT_ERROR_DOMAIN_LOADER = 2,
  DORT_ERROR_DOMAIN_ORT_API = 3,
  DORT_ERROR_DOMAIN_ALLOCATION = 4,
  DORT_ERROR_DOMAIN_UNSUPPORTED = 5,
  DORT_ERROR_DOMAIN_ORT_STATUS = 6,
  DORT_ERROR_DOMAIN_PROVIDER = 7
} dort_error_domain_t;

typedef enum dort_error_code {
  DORT_ERROR_NONE = 0,
  DORT_ERROR_INVALID_ARGUMENT = 1,
  DORT_ERROR_ABI_MISMATCH = 2,
  DORT_ERROR_API_REQUEST_UNSUPPORTED = 3,
  DORT_ERROR_SOURCE_UNSUPPORTED = 4,
  DORT_ERROR_PATH_NOT_ABSOLUTE = 5,
  DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT = 6,
  DORT_ERROR_RUNTIME_NOT_FOUND = 7,
  DORT_ERROR_SYMBOL_NOT_FOUND = 8,
  DORT_ERROR_ORT_API_UNAVAILABLE = 9,
  DORT_ERROR_ALLOCATION_FAILED = 10,
  DORT_ERROR_INVALID_UTF8 = 11,
  DORT_ERROR_LIMIT_EXCEEDED = 12,
  DORT_ERROR_PLATFORM = 13,
  DORT_ERROR_RUNTIME_IDENTITY_MISMATCH = 14,
  DORT_ERROR_MODEL_INVALID = 15,
  DORT_ERROR_TENSOR_INVALID = 16,
  DORT_ERROR_RUN_FAILED = 17,
  DORT_ERROR_BUFFER_TOO_SMALL = 18,
  DORT_ERROR_OVERFLOW = 19,
  DORT_ERROR_PROVIDER_UNSUPPORTED = 20,
  DORT_ERROR_NOT_TENSOR = 21,
  DORT_ERROR_VALUE_KIND_UNSUPPORTED = 22,
  DORT_ERROR_NOT_COMPOSITE = 23,
  DORT_ERROR_DATA_LEASE_UNSUPPORTED = 24,
  DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED = 25,
  DORT_ERROR_EXTERNAL_DATA_INVALID = 26,
  DORT_ERROR_CANCEL_TOKEN_UNKNOWN = 27,
  DORT_ERROR_CANCEL_REGISTRY_FULL = 28
} dort_error_code_t;

typedef enum dort_graph_optimization_level {
  DORT_GRAPH_OPTIMIZATION_DISABLED = 0,
  DORT_GRAPH_OPTIMIZATION_BASIC = 1,
  DORT_GRAPH_OPTIMIZATION_EXTENDED = 2,
  DORT_GRAPH_OPTIMIZATION_ALL = 3
} dort_graph_optimization_level_t;

typedef enum dort_execution_mode {
  DORT_EXECUTION_SEQUENTIAL = 0,
  DORT_EXECUTION_PARALLEL = 1
} dort_execution_mode_t;

/* Values intentionally match ONNXTensorElementDataType for API 27. */
typedef enum dort_tensor_element_type {
  DORT_TENSOR_FLOAT32 = 1,
  DORT_TENSOR_UINT8 = 2,
  DORT_TENSOR_INT8 = 3,
  DORT_TENSOR_UINT16 = 4,
  DORT_TENSOR_INT16 = 5,
  DORT_TENSOR_INT32 = 6,
  DORT_TENSOR_INT64 = 7,
  DORT_TENSOR_STRING = 8,
  DORT_TENSOR_BOOL = 9,
  DORT_TENSOR_FLOAT16 = 10,
  DORT_TENSOR_FLOAT64 = 11,
  DORT_TENSOR_UINT32 = 12,
  DORT_TENSOR_UINT64 = 13,
  DORT_TENSOR_BFLOAT16 = 16
} dort_tensor_element_type_t;

/* Values intentionally match supported ONNXType values for API 27. */
typedef enum dort_value_kind {
  DORT_VALUE_KIND_TENSOR = 1,
  DORT_VALUE_KIND_SEQUENCE = 2,
  DORT_VALUE_KIND_MAP = 3,
  DORT_VALUE_KIND_OPTIONAL = 6
} dort_value_kind_t;

typedef struct dort_string {
  uint32_t struct_size;
  const uint8_t* data;
  size_t length;
  void* private_owner;
} dort_string_t;

typedef struct dort_runtime_config {
  uint32_t struct_size;
  uint32_t shim_abi_version;
  uint32_t required_ort_api_version;
  uint32_t source_kind; /* dort_runtime_source_kind_t */
  uint32_t flags;       /* Must be zero for ABI v1. */
  uint32_t log_severity; /* dort_log_severity_t */
  uint32_t reserved0;    /* Must be zero. */

  /* UTF-8, non-empty, copied during the call. */
  const char* log_id_utf8;

  /* Required and absolute for FILE; forbidden for BUNDLED. */
  const char* library_path_utf8;

  /* Constrained leaf names for PROCESS mode; copied during the call. */
  const char* const* preferred_library_names_utf8;
  size_t preferred_library_name_count;

  /* Optional absolute directory constraining a FILE canonical path. */
  const char* allowed_root_utf8;
} dort_runtime_config_t;

typedef struct dort_string_pair {
  uint32_t struct_size;
  uint32_t reserved0; /* Must be zero. */
  const char* key_utf8;
  const char* value_utf8;
} dort_string_pair_t;

typedef struct dort_provider_config {
  uint32_t struct_size;
  uint32_t reserved0; /* Must be zero. */
  /*
   * Closed ABI-v1 ID. "cpu" is implicit/default and accepts no options.
   * Generic API-27 IDs are xnnpack, coreml, qnn, openvino, webnn, webgpu,
   * azure, js, and vitisai. Provider-specific API-27 adapters are cuda,
   * tensorrt, directml, dnnl, migraphx, and Android-only nnapi. Every adapter
   * accepts only its documented closed option map and target/runtime support
   * remains discoverable rather than implied by the ID.
   */
  const char* provider_id_utf8;
  const dort_string_pair_t* options;
  size_t option_count;
} dort_provider_config_t;

typedef struct dort_session_config {
  uint32_t struct_size;
  uint32_t graph_optimization_level; /* dort_graph_optimization_level_t */
  uint32_t execution_mode;           /* dort_execution_mode_t */
  int32_t intra_op_thread_count;     /* Zero uses ORT's default. */
  int32_t inter_op_thread_count;     /* Zero uses ORT's default. */
  uint32_t enable_cpu_memory_arena;  /* Closed boolean. */
  uint32_t enable_memory_pattern;    /* Closed boolean. */
  uint32_t deterministic_compute;    /* Closed boolean. */
  uint32_t enable_profiling;         /* Closed boolean. */
  uint32_t log_severity;             /* dort_log_severity_t */
  int32_t log_verbosity; /* Closed range 0..4. */
  uint32_t optimized_model_overwrite; /* Closed boolean. */
  uint32_t reserved0;                 /* Must be zero. */
  const char* log_id_utf8;
  const char* profile_path_prefix_utf8;
  const char* optimized_model_path_utf8;
  /*
   * Required when either output path is present. Must be an absolute existing
   * directory; output paths are canonicalized through an existing parent and
   * rejected when a symlink/junction resolves outside this root.
   */
  const char* artifact_root_utf8;
  /* Zero selects the ABI's bounded default. */
  size_t max_model_bytes;
  const dort_provider_config_t* providers;
  size_t provider_count;
  const dort_string_pair_t* config_entries;
  size_t config_entry_count;
} dort_session_config_t;

typedef struct dort_named_value {
  uint32_t struct_size;
  uint32_t reserved0; /* Must be zero. */
  const char* name_utf8;
  const dort_value_t* value;
} dort_named_value_t;

/*
 * Borrowed explicit-length UTF-8. A null data pointer is valid only at length
 * zero. String tensor elements must not contain embedded NUL bytes.
 */
typedef struct dort_utf8_span {
  uint32_t struct_size;
  uint32_t reserved0; /* Must be zero. */
  const uint8_t* data;
  size_t length;
} dort_utf8_span_t;

/*
 * Borrowed only for session creation; the shim copies names and bytes before
 * calling ORT. Names use forward-slash-separated safe relative components.
 */
typedef struct dort_external_data {
  uint32_t struct_size;
  uint32_t reserved0; /* Must be zero. */
  const uint8_t* relative_name_utf8;
  size_t relative_name_length;
  const void* data;
  size_t data_length;
} dort_external_data_t;

/* ABI and build information. */
DORT_API uint32_t DORT_CALL dort_get_abi_version(void);
DORT_API uint32_t DORT_CALL dort_get_ort_api_compatibility_floor(void);
/*
 * Build-manifest JSON schema 3 reports the closed runtime profile, nullable
 * Android owner, exact allowed runtime-source array, build ID, and optional
 * lock-selected artifact identity with its compiled-provider inventory.
 * Callers must reject unknown/missing fields and contradictory
 * owner/profile/source/artifact combinations.
 */
DORT_API dort_status_t* DORT_CALL dort_get_build_manifest_json(
    dort_string_t* out_json);

/* Owned string result. Safe on NULL or a zero-initialized value. */
DORT_API void DORT_CALL dort_string_release(dort_string_t* string_value);

/* Status strings remain valid until dort_status_release. */
DORT_API uint32_t DORT_CALL dort_status_domain(const dort_status_t* status);
DORT_API int32_t DORT_CALL dort_status_code(const dort_status_t* status);
DORT_API int32_t DORT_CALL dort_status_ort_code(const dort_status_t* status);
DORT_API const char* DORT_CALL dort_status_operation(const dort_status_t* status);
DORT_API const char* DORT_CALL dort_status_message(const dort_status_t* status);
DORT_API void DORT_CALL dort_status_release(dort_status_t* status);

/* Runtime. Successful loader handles are retained for process lifetime. */
DORT_API dort_status_t* DORT_CALL dort_runtime_open(
    const dort_runtime_config_t* config,
    dort_runtime_t** out_runtime);
DORT_API void DORT_CALL dort_runtime_retain(dort_runtime_t* runtime);
DORT_API void DORT_CALL dort_runtime_release(dort_runtime_t* runtime);
DORT_API dort_status_t* DORT_CALL dort_runtime_info_json(
    const dort_runtime_t* runtime,
    dort_string_t* out_json);
DORT_API dort_status_t* DORT_CALL dort_runtime_available_providers_json(
    const dort_runtime_t* runtime,
    dort_string_t* out_json);

/* Session options and sessions. All child handles retain their runtime. */
DORT_API dort_status_t* DORT_CALL dort_session_options_create(
    dort_runtime_t* runtime,
    const dort_session_config_t* config,
    dort_session_options_t** out_options);
DORT_API void DORT_CALL dort_session_options_retain(
    dort_session_options_t* options);
DORT_API void DORT_CALL dort_session_options_release(
    dort_session_options_t* options);
DORT_API dort_status_t* DORT_CALL dort_session_create_from_bytes(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const void* model_data,
    size_t model_data_length,
    dort_session_t** out_session);
DORT_API dort_status_t* DORT_CALL dort_session_create_from_file(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* model_path_utf8,
    const char* allowed_root_utf8,
    dort_session_t** out_session);
DORT_API dort_status_t* DORT_CALL
dort_session_create_from_bytes_with_external_data(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const void* model_data,
    size_t model_data_length,
    const dort_external_data_t* external_data,
    size_t external_data_count,
    dort_session_t** out_session);
DORT_API void DORT_CALL dort_session_retain(dort_session_t* session);
DORT_API void DORT_CALL dort_session_release(dort_session_t* session);
DORT_API dort_status_t* DORT_CALL dort_session_metadata_json(
    const dort_session_t* session,
    dort_string_t* out_json);
DORT_API dort_status_t* DORT_CALL dort_session_type_metadata_json(
    const dort_session_t* session,
    dort_string_t* out_json);
DORT_API dort_status_t* DORT_CALL dort_session_model_metadata_json(
    const dort_session_t* session,
    dort_string_t* out_json);
/*
 * File creation requires an absolute model path strictly below an absolute,
 * non-symlink/reparse allowed root. POSIX traversal is descriptor-relative and
 * rejects symlink/dot components. Windows traversal opens every component
 * without following reparse points and verifies its canonical root identity.
 * The file is copied into a bounded byte buffer and passed to
 * CreateSessionFromArray; ABI v1 never follows external-data sidecars
 * implicitly.
 *
 * Enumerated in-memory external data accepts no absolute, drive, UNC,
 * backslash, dot, dot-dot, empty, control-byte, or duplicate names. The shim
 * clones session options, registers only the copied entries, and never falls
 * back to loading a model or sidecar path for this entry point.
 *
 * Legacy flat metadata is schemaVersion 2. Each tensor entry has the exact
 * keys name, kind, elementType, hasShape, dimensions, and
 * symbolicDimensions. Unknown rank uses hasShape=false with two empty arrays;
 * a scalar uses hasShape=true with two empty arrays. Flat metadata returns
 * DORT_ERROR_VALUE_KIND_UNSUPPORTED for composite model I/O.
 *
 * Recursive type metadata is schemaVersion 1. Its tensor nodes use the same
 * hasShape rule and omit only name; sequence, map, and optional nodes contain
 * recursively typed element/value nodes. Model metadata schemaVersion 1 owns
 * copied producerName, graphName, domain, description, graphDescription,
 * version, and key-sorted customMetadata fields.
 */

/* Ref-counted, aligned native byte buffers. */
DORT_API dort_status_t* DORT_CALL dort_buffer_allocate(
    dort_runtime_t* runtime,
    size_t byte_length,
    size_t alignment,
    dort_buffer_t** out_buffer);
DORT_API void DORT_CALL dort_buffer_retain(dort_buffer_t* buffer);
DORT_API void DORT_CALL dort_buffer_release(dort_buffer_t* buffer);
DORT_API size_t DORT_CALL dort_buffer_byte_length(const dort_buffer_t* buffer);
DORT_API dort_status_t* DORT_CALL dort_buffer_write(
    dort_buffer_t* buffer,
    size_t offset,
    const void* source,
    size_t byte_length);
DORT_API dort_status_t* DORT_CALL dort_buffer_read(
    const dort_buffer_t* buffer,
    size_t offset,
    void* destination,
    size_t byte_length);
DORT_API dort_status_t* DORT_CALL dort_buffer_data_acquire(
    dort_buffer_t* buffer,
    dort_data_lease_t** out_lease,
    void** out_data,
    size_t* out_byte_length);
/*
 * Alignment is a power of two in [sizeof(void*), 4096]. A buffer becomes
 * immutable while one or more tensor values retain it; writes then fail until
 * the final attached value is released.
 */

/* Dense CPU tensors plus bounded API-27 sequence/map/optional values. */
DORT_API dort_status_t* DORT_CALL dort_tensor_create_copy(
    dort_runtime_t* runtime,
    const void* data,
    size_t byte_length,
    const int64_t* dimensions,
    size_t dimension_count,
    uint32_t element_type,
    dort_value_t** out_value);
DORT_API dort_status_t* DORT_CALL dort_tensor_create_with_buffer(
    dort_runtime_t* runtime,
    dort_buffer_t* buffer,
    size_t byte_offset,
    size_t byte_length,
    const int64_t* dimensions,
    size_t dimension_count,
    uint32_t element_type,
    dort_value_t** out_value);
DORT_API dort_status_t* DORT_CALL dort_tensor_create_strings_copy(
    dort_runtime_t* runtime,
    const dort_utf8_span_t* strings,
    size_t string_count,
    const int64_t* dimensions,
    size_t dimension_count,
    dort_value_t** out_value);
DORT_API void DORT_CALL dort_value_retain(dort_value_t* value);
DORT_API void DORT_CALL dort_value_release(dort_value_t* value);
DORT_API dort_status_t* DORT_CALL dort_value_kind(
    const dort_value_t* value,
    uint32_t* out_kind);
DORT_API dort_status_t* DORT_CALL dort_value_child_count(
    const dort_value_t* value,
    size_t* out_count);
DORT_API dort_status_t* DORT_CALL dort_value_child_get(
    const dort_value_t* value,
    size_t index,
    dort_value_t** out_child);
DORT_API dort_status_t* DORT_CALL dort_sequence_create(
    dort_runtime_t* runtime,
    const dort_value_t* const* values,
    size_t value_count,
    dort_value_t** out_value);
DORT_API dort_status_t* DORT_CALL dort_map_create(
    dort_runtime_t* runtime,
    const dort_value_t* keys,
    const dort_value_t* values,
    dort_value_t** out_value);
DORT_API dort_status_t* DORT_CALL dort_optional_none_create(
    dort_runtime_t* runtime,
    dort_value_t** out_value);
DORT_API dort_status_t* DORT_CALL dort_optional_some_create(
    const dort_value_t* contained_value,
    dort_value_t** out_value);
DORT_API dort_status_t* DORT_CALL dort_tensor_info_json(
    const dort_value_t* value,
    dort_string_t* out_json);
/* A null destination with zero capacity queries the required byte count. */
DORT_API dort_status_t* DORT_CALL dort_tensor_copy_data(
    const dort_value_t* value,
    void* destination,
    size_t destination_capacity,
    size_t* out_required_bytes);
DORT_API dort_status_t* DORT_CALL dort_tensor_string_count(
    const dort_value_t* value,
    size_t* out_count);
DORT_API dort_status_t* DORT_CALL dort_tensor_string_get(
    const dort_value_t* value,
    size_t index,
    dort_string_t* out_string);
DORT_API dort_status_t* DORT_CALL dort_tensor_data_acquire(
    const dort_value_t* value,
    dort_data_lease_t** out_lease,
    const void** out_data,
    size_t* out_byte_length);

/*
 * A data lease retains the exact buffer or tensor owner for pointer lifetime.
 * Tensor acquisition supports only dense fixed-width CPU-host tensors.
 * Buffer leases are exclusive and mutable; buffer transfer and tensor attach
 * remain blocked until the final retained lease is released.
 */
DORT_API void DORT_CALL dort_data_lease_retain(dort_data_lease_t* lease);
DORT_API void DORT_CALL dort_data_lease_release(dort_data_lease_t* lease);

/*
 * Synchronous named runs. Optional Some inputs pass their contained value;
 * Optional None inputs and omitted optional names are omitted from ORT feeds.
 */
DORT_API dort_status_t* DORT_CALL dort_run_options_create(
    dort_runtime_t* runtime,
    dort_run_options_t** out_options);
DORT_API void DORT_CALL dort_run_options_retain(dort_run_options_t* options);
DORT_API void DORT_CALL dort_run_options_release(dort_run_options_t* options);
DORT_API dort_status_t* DORT_CALL dort_run_options_set_terminate(
    dort_run_options_t* options);
DORT_API dort_status_t* DORT_CALL dort_run_options_unset_terminate(
    dort_run_options_t* options);
/*
 * Enables one bounded run profile inside an existing app-owned artifact root.
 * finish disables profiling, copies exactly one regular profile file, and
 * publishes it only after the temporary native artifacts are removed. On
 * POSIX, the root must be an existing absolute non-/, non-symlink directory
 * owned by the effective user, with owner read/write/search permission and no
 * group/other write permission. A failed POSIX retirement publishes no bytes,
 * blocks reuse, and may be retried with finish after the owned root is
 * repaired. finish must be called after the associated run returns, including
 * when that run failed.
 */
DORT_API dort_status_t* DORT_CALL dort_run_options_profiling_start(
    dort_run_options_t* options,
    const char* artifact_root_utf8);
DORT_API dort_status_t* DORT_CALL dort_run_options_profiling_finish(
    dort_run_options_t* options,
    dort_string_t* out_profile_json);
/*
 * Process-local cancellation registry for safe cross-isolate termination.
 * Only the opaque integer token may cross an isolate boundary. Registration
 * retains run_options. finish must be called by the owning worker only after
 * its run has returned; it clears termination before releasing that retain.
 * A repeated request succeeds with out_did_request set to zero. Unknown and
 * already-finished tokens fail closed with DORT_ERROR_CANCEL_TOKEN_UNKNOWN.
 */
DORT_API dort_status_t* DORT_CALL dort_cancel_token_register(
    dort_run_options_t* run_options,
    uint64_t* out_token);
DORT_API dort_status_t* DORT_CALL dort_cancel_token_request(
    uint64_t token,
    uint32_t* out_did_request);
DORT_API dort_status_t* DORT_CALL dort_cancel_token_finish(
    uint64_t token,
    uint32_t* out_was_requested);
DORT_API dort_status_t* DORT_CALL dort_session_run(
    dort_session_t* session,
    dort_run_options_t* run_options,
    const dort_named_value_t* inputs,
    size_t input_count,
    const char* const* output_names_utf8,
    size_t output_count,
    dort_run_result_t** out_result);
DORT_API void DORT_CALL dort_run_result_retain(dort_run_result_t* result);
DORT_API void DORT_CALL dort_run_result_release(dort_run_result_t* result);
DORT_API size_t DORT_CALL dort_run_result_count(
    const dort_run_result_t* result);
DORT_API dort_status_t* DORT_CALL dort_run_result_get(
    const dort_run_result_t* result,
    size_t index,
    dort_string_t* out_name,
    dort_value_t** out_value);
/* run_result_get returns an owned string and a separately retained value. */

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* FONIX_DORT_H_ */
