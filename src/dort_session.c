#if !defined(_WIN32)
#define _POSIX_C_SOURCE 200809L
#define _XOPEN_SOURCE 700
#if defined(__APPLE__)
#define _DARWIN_C_SOURCE 1
#endif
#endif

#include "dort_internal.h"

#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#else
#include <fcntl.h>
#include <pthread.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#define DORT_SESSION_OPTIONS_MAGIC 0x44534f31u
#define DORT_SESSION_MAGIC 0x44534531u

typedef struct dort_json_builder {
  char* data;
  size_t length;
  size_t capacity;
} dort_json_builder_t;

struct dort_session_options {
  uint32_t magic;
  atomic_uint reference_count;
  dort_runtime_t* runtime;
  OrtSessionOptions* options;
  size_t max_model_bytes;
};

struct dort_session {
  uint32_t magic;
  atomic_uint reference_count;
  dort_runtime_t* runtime;
  OrtSession* session;
  char* metadata_json;
  char* type_metadata_json;
  char* model_metadata_json;
  char** input_names;
  size_t input_count;
  uint32_t* input_kinds;
  char** output_names;
  size_t output_count;
  uint32_t* output_kinds;
#if defined(_WIN32)
  SRWLOCK run_lock;
#else
  pthread_mutex_t run_lock;
#endif
};

static int dort_session_options_is_valid(const dort_session_options_t* options) {
  return options != NULL && options->magic == DORT_SESSION_OPTIONS_MAGIC &&
         dort_runtime_is_valid(options->runtime) && options->options != NULL;
}

int dort_session_internal_is_valid(const dort_session_t* session) {
  return session != NULL && session->magic == DORT_SESSION_MAGIC &&
         dort_runtime_is_valid(session->runtime) && session->session != NULL;
}

dort_runtime_t* dort_session_internal_runtime(const dort_session_t* session) {
  return dort_session_internal_is_valid(session) ? session->runtime : NULL;
}

OrtSession* dort_session_internal_ort(const dort_session_t* session) {
  return dort_session_internal_is_valid(session) ? session->session : NULL;
}

size_t dort_session_internal_input_count(const dort_session_t* session) {
  return dort_session_internal_is_valid(session) ? session->input_count : 0u;
}

size_t dort_session_internal_output_count(const dort_session_t* session) {
  return dort_session_internal_is_valid(session) ? session->output_count : 0u;
}

const char* dort_session_internal_input_name(
    const dort_session_t* session,
    size_t index) {
  return dort_session_internal_is_valid(session) && index < session->input_count
             ? session->input_names[index]
             : NULL;
}

const char* dort_session_internal_output_name(
    const dort_session_t* session,
    size_t index) {
  return dort_session_internal_is_valid(session) && index < session->output_count
             ? session->output_names[index]
             : NULL;
}

uint32_t dort_session_internal_input_kind(
    const dort_session_t* session,
    size_t index) {
  return dort_session_internal_is_valid(session) && index < session->input_count &&
                 session->input_kinds != NULL
             ? session->input_kinds[index]
             : 0u;
}

uint32_t dort_session_internal_output_kind(
    const dort_session_t* session,
    size_t index) {
  return dort_session_internal_is_valid(session) && index < session->output_count &&
                 session->output_kinds != NULL
             ? session->output_kinds[index]
             : 0u;
}

void dort_session_internal_lock(dort_session_t* session) {
#if defined(_WIN32)
  AcquireSRWLockExclusive(&session->run_lock);
#else
  (void)pthread_mutex_lock(&session->run_lock);
#endif
}

void dort_session_internal_unlock(dort_session_t* session) {
#if defined(_WIN32)
  ReleaseSRWLockExclusive(&session->run_lock);
#else
  (void)pthread_mutex_unlock(&session->run_lock);
#endif
}

static int dort_json_reserve(dort_json_builder_t* builder, size_t addition) {
  size_t required = 0u;
  size_t capacity = 0u;
  char* resized = NULL;
  if (!dort_checked_add_size(builder->length, addition, &required) ||
      !dort_checked_add_size(required, 1u, &required) ||
      required > DORT_MAX_METADATA_JSON_BYTES) {
    return 0;
  }
  if (required <= builder->capacity) {
    return 1;
  }
  capacity = builder->capacity == 0u ? 256u : builder->capacity;
  while (capacity < required) {
    size_t next = 0u;
    if (!dort_checked_multiply_size(capacity, 2u, &next) ||
        next > DORT_MAX_METADATA_JSON_BYTES) {
      capacity = DORT_MAX_METADATA_JSON_BYTES;
      break;
    }
    capacity = next;
  }
  if (capacity < required) {
    return 0;
  }
  resized = (char*)dort_memory_reallocate(builder->data, capacity);
  if (resized == NULL) {
    return 0;
  }
  builder->data = resized;
  builder->capacity = capacity;
  return 1;
}

static int dort_json_append_bytes(
    dort_json_builder_t* builder,
    const char* bytes,
    size_t length) {
  if (builder == NULL || (length > 0u && bytes == NULL)) {
    return 0;
  }
  if (!dort_json_reserve(builder, length)) {
    return 0;
  }
  if (length > 0u) {
    memcpy(builder->data + builder->length, bytes, length);
  }
  builder->length += length;
  builder->data[builder->length] = '\0';
  return 1;
}

static int dort_json_append(dort_json_builder_t* builder, const char* value) {
  if (value == NULL) {
    return 0;
  }
  return dort_json_append_bytes(builder, value, strlen(value));
}

static int dort_json_appendf(
    dort_json_builder_t* builder,
    const char* format,
    ...) {
  va_list arguments;
  va_list copy;
  int required = 0;
  int written = 0;
  va_start(arguments, format);
  va_copy(copy, arguments);
  required = vsnprintf(NULL, 0, format, copy);
  va_end(copy);
  if (required < 0 || !dort_json_reserve(builder, (size_t)required)) {
    va_end(arguments);
    return 0;
  }
  written = vsnprintf(
      builder->data + builder->length,
      builder->capacity - builder->length,
      format,
      arguments);
  va_end(arguments);
  if (written != required) {
    return 0;
  }
  builder->length += (size_t)written;
  return 1;
}

static int dort_json_append_string(
    dort_json_builder_t* builder,
    const char* value,
    size_t length) {
  char* escaped = dort_json_escape(value, length);
  int success = 0;
  if (escaped == NULL) {
    return 0;
  }
  success = dort_json_append(builder, "\"") &&
            dort_json_append(builder, escaped) &&
            dort_json_append(builder, "\"");
  free(escaped);
  return success;
}

static dort_status_t* dort_validate_pair_array(
    const dort_string_pair_t* pairs,
    size_t count,
    size_t maximum,
    const char* operation,
    const char* description) {
  size_t index = 0u;
  if (count > maximum) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The %s count exceeds the ABI limit.",
        description);
  }
  if ((count == 0u) != (pairs == NULL)) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        operation,
        "The %s pointer/count is inconsistent.",
        description);
  }
  for (index = 0u; index < count; ++index) {
    size_t key_length = 0u;
    size_t value_length = 0u;
    size_t prior = 0u;
    int validation = DORT_ERROR_NONE;
    if (pairs[index].struct_size < DORT_STRING_PAIR_V1_SIZE ||
        pairs[index].reserved0 != 0u) {
      return dort_status_createf(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          operation,
          "A %s entry has an invalid ABI size or reserved field.",
          description);
    }
    validation = dort_bounded_utf8_length(
        pairs[index].key_utf8, DORT_MAX_KEY_BYTES, 0, &key_length);
    if (validation == DORT_ERROR_NONE) {
      validation = dort_bounded_utf8_length(
          pairs[index].value_utf8, DORT_MAX_VALUE_BYTES, 1, &value_length);
    }
    if (validation != DORT_ERROR_NONE) {
      return dort_status_createf(
          DORT_ERROR_DOMAIN_SHIM,
          validation,
          0,
          operation,
          "A %s key or value is invalid UTF-8 or exceeds its ABI limit.",
          description);
    }
    for (prior = 0u; prior < index; ++prior) {
      if (strcmp(pairs[prior].key_utf8, pairs[index].key_utf8) == 0) {
        return dort_status_createf(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            operation,
            "The %s array contains a duplicate key.",
            description);
      }
    }
    (void)key_length;
    (void)value_length;
  }
  return NULL;
}

static const char* dort_provider_name(const char* provider_id) {
  if (strcmp(provider_id, "qnn") == 0) {
    return "QNN";
  }
  if (strcmp(provider_id, "openvino") == 0) {
    return "OpenVINO";
  }
  if (strcmp(provider_id, "xnnpack") == 0) {
    return "XNNPACK";
  }
  if (strcmp(provider_id, "webnn") == 0) {
    return "WEBNN";
  }
  if (strcmp(provider_id, "webgpu") == 0) {
    return "WebGPU";
  }
  if (strcmp(provider_id, "azure") == 0) {
    return "AZURE";
  }
  if (strcmp(provider_id, "js") == 0) {
    return "JS";
  }
  if (strcmp(provider_id, "vitisai") == 0) {
    return "VitisAI";
  }
  if (strcmp(provider_id, "coreml") == 0) {
    return "CoreML";
  }
  return NULL;
}

static int dort_provider_is_known(const char* provider_id) {
  return strcmp(provider_id, "cpu") == 0 ||
         strcmp(provider_id, "nnapi") == 0 ||
         strcmp(provider_id, "cuda") == 0 ||
         strcmp(provider_id, "tensorrt") == 0 ||
         strcmp(provider_id, "directml") == 0 ||
         strcmp(provider_id, "dnnl") == 0 ||
         strcmp(provider_id, "migraphx") == 0 ||
         dort_provider_name(provider_id) != NULL;
}

static int dort_option_key_is_one_of(
    const char* key,
    const char* const* allowed,
    size_t allowed_count) {
  size_t index = 0u;
  for (index = 0u; index < allowed_count; ++index) {
    if (strcmp(key, allowed[index]) == 0) {
      return 1;
    }
  }
  return 0;
}

static int dort_parse_decimal_size(const char* value, size_t* out_value) {
  size_t result = 0u;
  size_t index = 0u;
  size_t length = value == NULL ? 0u : strlen(value);
  if (out_value == NULL || length == 0u || length > 20u ||
      (length > 1u && value[0] == '0')) {
    return 0;
  }
  for (index = 0u; index < length; ++index) {
    size_t digit = 0u;
    if (value[index] < '0' || value[index] > '9') {
      return 0;
    }
    digit = (size_t)(value[index] - '0');
    if (result > (SIZE_MAX - digit) / 10u) {
      return 0;
    }
    result = result * 10u + digit;
  }
  *out_value = result;
  return 1;
}

static int dort_parse_nonnegative_int32(const char* value, int* out_value) {
  size_t parsed = 0u;
  if (out_value == NULL || !dort_parse_decimal_size(value, &parsed) ||
      parsed > (size_t)INT32_MAX) {
    return 0;
  }
  *out_value = (int)parsed;
  return 1;
}

static int dort_parse_zero_or_one(const char* value, int* out_value) {
  if (out_value == NULL || value == NULL || value[1] != '\0' ||
      (value[0] != '0' && value[0] != '1')) {
    return 0;
  }
  *out_value = value[0] == '1' ? 1 : 0;
  return 1;
}

static const char* dort_provider_option(
    const dort_provider_config_t* provider,
    const char* key) {
  size_t index = 0u;
  for (index = 0u; index < provider->option_count; ++index) {
    if (strcmp(provider->options[index].key_utf8, key) == 0) {
      return provider->options[index].value_utf8;
    }
  }
  return NULL;
}

static dort_status_t* dort_validate_closed_provider_options(
    const dort_provider_config_t* provider) {
  static const char* const xnnpack_keys[] = {"intra_op_num_threads"};
  static const char* const cuda_keys[] = {
      "device_id",
      "gpu_mem_limit",
      "arena_extend_strategy",
      "cudnn_conv_algo_search",
      "do_copy_in_default_stream",
      "cudnn_conv_use_max_workspace",
      "enable_cuda_graph",
      "cudnn_conv1d_pad_to_nc1d",
      "tunable_op_enable",
      "tunable_op_tuning_enable",
      "tunable_op_max_tuning_duration_ms",
      "prefer_nhwc",
      "use_ep_level_unified_stream",
      "use_tf32",
      "fuse_conv_bias"};
  static const char* const tensorrt_keys[] = {
      "device_id",
      "trt_max_partition_iterations",
      "trt_min_subgraph_size",
      "trt_max_workspace_size",
      "trt_fp16_enable",
      "trt_bf16_enable",
      "trt_int8_enable",
      "trt_dla_enable",
      "trt_dla_core",
      "trt_force_sequential_engine_build",
      "trt_context_memory_sharing_enable",
      "trt_layer_norm_fp32_fallback",
      "trt_detailed_build_log",
      "trt_build_heuristics_enable",
      "trt_sparsity_enable",
      "trt_builder_optimization_level",
      "trt_auxiliary_streams",
      "trt_cuda_graph_enable",
      "trt_engine_hw_compatible"};
  static const char* const openvino_keys[] = {
      "device_type",
      "num_of_threads",
      "num_streams",
      "model_priority",
      "enable_opencl_throttling",
      "enable_qdq_optimizer",
      "disable_dynamic_shapes"};
  static const char* const dnnl_keys[] = {"use_arena"};
  static const char* const directml_keys[] = {"device_id"};
  static const char* const migraphx_keys[] = {
      "device_id",
      "migraphx_fp16_enable",
      "migraphx_fp8_enable",
      "migraphx_int8_enable",
      "migraphx_exhaustive_tune",
      "migraphx_mem_limit",
      "migraphx_arena_extend_strategy"};
  const char* const* allowed = NULL;
  size_t allowed_count = 0u;
  size_t index = 0u;
  if (strcmp(provider->provider_id_utf8, "xnnpack") == 0) {
    allowed = xnnpack_keys;
    allowed_count = sizeof(xnnpack_keys) / sizeof(xnnpack_keys[0]);
  } else if (strcmp(provider->provider_id_utf8, "cuda") == 0) {
    allowed = cuda_keys;
    allowed_count = sizeof(cuda_keys) / sizeof(cuda_keys[0]);
  } else if (strcmp(provider->provider_id_utf8, "tensorrt") == 0) {
    allowed = tensorrt_keys;
    allowed_count = sizeof(tensorrt_keys) / sizeof(tensorrt_keys[0]);
  } else if (strcmp(provider->provider_id_utf8, "directml") == 0) {
    allowed = directml_keys;
    allowed_count = sizeof(directml_keys) / sizeof(directml_keys[0]);
  } else if (strcmp(provider->provider_id_utf8, "openvino") == 0) {
    allowed = openvino_keys;
    allowed_count = sizeof(openvino_keys) / sizeof(openvino_keys[0]);
  } else if (strcmp(provider->provider_id_utf8, "dnnl") == 0) {
    allowed = dnnl_keys;
    allowed_count = sizeof(dnnl_keys) / sizeof(dnnl_keys[0]);
  } else if (strcmp(provider->provider_id_utf8, "migraphx") == 0) {
    allowed = migraphx_keys;
    allowed_count = sizeof(migraphx_keys) / sizeof(migraphx_keys[0]);
  } else {
    return NULL;
  }
  if (strcmp(provider->provider_id_utf8, "xnnpack") == 0) {
    size_t ignored_thread_count = 0u;
    if (provider->option_count != 1u ||
        !dort_parse_decimal_size(
            dort_provider_option(provider, "intra_op_num_threads"),
            &ignored_thread_count) ||
        ignored_thread_count < 1u || ignored_thread_count > 1024u) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "XNNPACK accepts exactly one canonical intra_op_num_threads value "
          "in [1, 1024].");
    }
  }
  for (index = 0u; index < provider->option_count; ++index) {
    if (!dort_option_key_is_one_of(
            provider->options[index].key_utf8, allowed, allowed_count)) {
      return dort_status_createf(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "Provider '%s' contains an option outside its closed API-27 adapter.",
          provider->provider_id_utf8);
    }
  }
  if (strcmp(provider->provider_id_utf8, "directml") == 0) {
    int ignored_device_id = 0;
    if (provider->option_count != 1u ||
        !dort_parse_nonnegative_int32(
            dort_provider_option(provider, "device_id"),
            &ignored_device_id)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "DirectML accepts exactly one non-negative device_id.");
    }
  }
  if (strcmp(provider->provider_id_utf8, "dnnl") == 0) {
    int ignored_use_arena = 0;
    if (provider->option_count != 1u ||
        !dort_parse_zero_or_one(
            dort_provider_option(provider, "use_arena"),
            &ignored_use_arena)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "oneDNN accepts exactly one zero-or-one use_arena option.");
    }
  }
  return NULL;
}

static int dort_parse_nnapi_flags(const char* value, uint32_t* out_flags) {
  uint32_t flags = 0u;
  size_t index = 0u;
  size_t length = value == NULL ? 0u : strlen(value);
  if (out_flags == NULL || length == 0u || length > 2u ||
      (length > 1u && value[0] == '0')) {
    return 0;
  }
  for (index = 0u; index < length; ++index) {
    if (value[index] < '0' || value[index] > '9') {
      return 0;
    }
    flags = flags * 10u + (uint32_t)(value[index] - '0');
  }
  if (flags > 15u) {
    return 0;
  }
  *out_flags = flags;
  return 1;
}

static int dort_session_element_type_is_supported(
    ONNXTensorElementDataType element_type) {
  switch (element_type) {
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT16:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT16:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT32:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT16:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_DOUBLE:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT32:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT64:
    case ONNX_TENSOR_ELEMENT_DATA_TYPE_BFLOAT16:
      return 1;
    default:
      return 0;
  }
}

static dort_status_t* dort_validate_session_config(
    const dort_session_config_t* config) {
  size_t index = 0u;
  size_t cuda_index = SIZE_MAX;
  size_t tensorrt_index = SIZE_MAX;
  size_t log_id_length = 0u;
  int validation = DORT_ERROR_NONE;
  dort_status_t* status = NULL;
  const char* coreml_cache_directory = NULL;
  if (config == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "The session configuration is null.");
  }
  if (config->struct_size < DORT_SESSION_CONFIG_V1_SIZE ||
      config->reserved0 != 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "The session configuration has an invalid ABI size or reserved field.");
  }
  if (config->graph_optimization_level > DORT_GRAPH_OPTIMIZATION_ALL ||
      config->execution_mode > DORT_EXECUTION_PARALLEL ||
      config->log_severity > DORT_LOG_FATAL ||
      config->intra_op_thread_count < 0 ||
      config->inter_op_thread_count < 0 ||
      config->intra_op_thread_count > 1024 ||
      config->inter_op_thread_count > 1024 || config->log_verbosity < 0 ||
      config->log_verbosity > 4) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "A session enum, thread count, or logging value is outside its closed ABI domain.");
  }
  if (config->enable_cpu_memory_arena > 1u ||
      config->enable_memory_pattern > 1u ||
      config->deterministic_compute > 1u || config->enable_profiling > 1u ||
      config->optimized_model_overwrite > 1u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "Session boolean fields must use the closed zero-or-one domain.");
  }
  validation = dort_bounded_utf8_length(
      config->log_id_utf8, DORT_MAX_LOG_ID_BYTES, 0, &log_id_length);
  if (validation != DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        validation,
        0,
        "session_options_create",
        "The session log ID is invalid UTF-8 or exceeds its ABI limit.");
  }
  if (config->max_model_bytes > DORT_MAX_MODEL_BYTES_HARD) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "session_options_create",
        "The model byte limit exceeds the ABI hard maximum.");
  }
  if ((config->enable_profiling != 0u) !=
      (config->profile_path_prefix_utf8 != NULL)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "Profiling enablement and its path prefix must be provided together.");
  }
  if (config->provider_count > DORT_MAX_PROVIDER_COUNT ||
      (config->provider_count == 0u) != (config->providers == NULL)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        config->provider_count > DORT_MAX_PROVIDER_COUNT
            ? DORT_ERROR_LIMIT_EXCEEDED
            : DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "The provider pointer/count is inconsistent or exceeds the ABI limit.");
  }
  for (index = 0u; index < config->provider_count; ++index) {
    const dort_provider_config_t* provider = &config->providers[index];
    size_t id_length = 0u;
    size_t prior = 0u;
    if (provider->struct_size < DORT_PROVIDER_CONFIG_V1_SIZE ||
        provider->reserved0 != 0u) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "A provider entry has an invalid ABI size or reserved field.");
    }
    validation = dort_bounded_utf8_length(
        provider->provider_id_utf8, 32u, 0, &id_length);
    if (validation != DORT_ERROR_NONE ||
        !dort_provider_is_known(provider->provider_id_utf8)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          validation == DORT_ERROR_NONE ? DORT_ERROR_PROVIDER_UNSUPPORTED
                                        : validation,
          0,
          "session_options_create",
          "A provider ID is invalid or outside the API-27 closed provider map.");
    }
    for (prior = 0u; prior < index; ++prior) {
      if (strcmp(
              config->providers[prior].provider_id_utf8,
              provider->provider_id_utf8) == 0) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "The provider array contains a duplicate provider ID.");
      }
    }
    status = dort_validate_pair_array(
        provider->options,
        provider->option_count,
        DORT_MAX_CONFIG_ENTRIES,
        "session_options_create",
        "provider option");
    if (status != NULL) {
      return status;
    }
    status = dort_validate_closed_provider_options(provider);
    if (status != NULL) {
      return status;
    }
    if (strcmp(provider->provider_id_utf8, "xnnpack") == 0) {
      size_t thread_count = 0u;
      if (!dort_parse_decimal_size(
              dort_provider_option(provider, "intra_op_num_threads"),
              &thread_count)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "XNNPACK thread configuration is not canonical.");
      }
      if (thread_count > 1u && config->intra_op_thread_count != 1) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "XNNPACK thread counts above one require the ORT intra-op thread "
            "count to be exactly one.");
      }
    }
    if (strcmp(provider->provider_id_utf8, "cpu") == 0 &&
        provider->option_count != 0u) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The implicit CPU provider does not accept generic provider options.");
    }
    if (strcmp(provider->provider_id_utf8, "cpu") == 0 &&
        index + 1u != config->provider_count) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The implicit CPU fallback must be the last requested provider.");
    }
    if (strcmp(provider->provider_id_utf8, "nnapi") == 0) {
      uint32_t ignored_flags = 0u;
      if (provider->option_count != 1u ||
          strcmp(provider->options[0].key_utf8, "flags") != 0 ||
          !dort_parse_nnapi_flags(
              provider->options[0].value_utf8, &ignored_flags)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "The legacy NNAPI provider accepts exactly one closed flags value.");
      }
    }
    if (strcmp(provider->provider_id_utf8, "cuda") == 0) {
      cuda_index = index;
    } else if (strcmp(provider->provider_id_utf8, "tensorrt") == 0) {
      tensorrt_index = index;
    } else if (strcmp(provider->provider_id_utf8, "directml") == 0 &&
               (config->execution_mode != DORT_EXECUTION_SEQUENTIAL ||
                config->enable_memory_pattern != 0u)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "DirectML requires sequential execution and memory patterns disabled.");
    }
    {
      size_t option_index = 0u;
      for (option_index = 0u; option_index < provider->option_count;
           ++option_index) {
        if (strcmp(
                provider->options[option_index].key_utf8,
                "ModelCacheDirectory") == 0) {
          if (strcmp(provider->provider_id_utf8, "coreml") != 0 ||
              coreml_cache_directory != NULL) {
            return dort_status_create(
                DORT_ERROR_DOMAIN_PROVIDER,
                DORT_ERROR_INVALID_ARGUMENT,
                0,
                "session_options_create",
                "ModelCacheDirectory is valid only once on the Core ML provider.");
          }
          coreml_cache_directory =
              provider->options[option_index].value_utf8;
        }
      }
    }
    (void)id_length;
  }
  if (tensorrt_index != SIZE_MAX &&
      (cuda_index == SIZE_MAX || tensorrt_index >= cuda_index)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_PROVIDER,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "TensorRT requires a lower-priority CUDA provider in the same session.");
  }
  if ((config->profile_path_prefix_utf8 != NULL ||
       config->optimized_model_path_utf8 != NULL ||
       coreml_cache_directory != NULL) &&
      config->artifact_root_utf8 == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "Session artifacts and the Core ML cache require an explicit artifact root.");
  }
  status = dort_validate_pair_array(
      config->config_entries,
      config->config_entry_count,
      DORT_MAX_CONFIG_ENTRIES,
      "session_options_create",
      "session configuration entry");
  (void)log_id_length;
  return status;
}

static const char* dort_coreml_cache_directory(
    const dort_session_config_t* config) {
  size_t provider_index = 0u;
  for (provider_index = 0u; provider_index < config->provider_count;
       ++provider_index) {
    const dort_provider_config_t* provider = &config->providers[provider_index];
    size_t option_index = 0u;
    if (strcmp(provider->provider_id_utf8, "coreml") != 0) {
      continue;
    }
    for (option_index = 0u; option_index < provider->option_count;
         ++option_index) {
      if (strcmp(
              provider->options[option_index].key_utf8,
              "ModelCacheDirectory") == 0) {
        return provider->options[option_index].value_utf8;
      }
    }
  }
  return NULL;
}

#if defined(_WIN32)
static wchar_t* dort_utf8_to_wide_path(const char* path) {
  int required = 0;
  wchar_t* result = NULL;
  size_t ignored = 0u;
  if (dort_bounded_utf8_length(path, DORT_MAX_PATH_BYTES, 0, &ignored) !=
      DORT_ERROR_NONE) {
    return NULL;
  }
  required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
  if (required <= 0) {
    return NULL;
  }
  result = (wchar_t*)dort_memory_allocate_zeroed((size_t)required, sizeof(wchar_t));
  if (result == NULL ||
      MultiByteToWideChar(
          CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, result, required) != required) {
    free(result);
    return NULL;
  }
  return result;
}

static int dort_windows_is_path_separator(wchar_t value) {
  return value == L'\\' || value == L'/';
}

static int dort_windows_path_is_absolute(const wchar_t* path) {
  if (path == NULL) {
    return 0;
  }
  if (((path[0] >= L'A' && path[0] <= L'Z') ||
       (path[0] >= L'a' && path[0] <= L'z')) &&
      path[1] == L':' && dort_windows_is_path_separator(path[2])) {
    return 1;
  }
  if (dort_windows_is_path_separator(path[0]) &&
      dort_windows_is_path_separator(path[1])) {
    /* Device namespaces are not application filesystem paths. */
    return path[2] != L'?' && path[2] != L'.' && path[2] != L'\0' &&
           !dort_windows_is_path_separator(path[2]);
  }
  return 0;
}

static wchar_t* dort_windows_full_path(const wchar_t* path) {
  DWORD required = GetFullPathNameW(path, 0u, NULL, NULL);
  DWORD written = 0u;
  wchar_t* result = NULL;
  if (required == 0u || required >= DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)dort_memory_allocate_zeroed((size_t)required + 1u, sizeof(wchar_t));
  if (result == NULL) {
    return NULL;
  }
  written = GetFullPathNameW(path, required + 1u, result, NULL);
  if (written == 0u || written > required || written >= DORT_MAX_PATH_BYTES) {
    free(result);
    return NULL;
  }
  return result;
}

static wchar_t* dort_windows_final_path(HANDLE handle) {
  DWORD required = GetFinalPathNameByHandleW(
      handle, NULL, 0u, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  DWORD written = 0u;
  wchar_t* result = NULL;
  if (required == 0u || required >= DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)dort_memory_allocate_zeroed((size_t)required + 1u, sizeof(wchar_t));
  if (result == NULL) {
    return NULL;
  }
  written = GetFinalPathNameByHandleW(
      handle,
      result,
      required + 1u,
      FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  if (written == 0u || written > required || written >= DORT_MAX_PATH_BYTES) {
    free(result);
    return NULL;
  }
  return result;
}

static int dort_windows_path_is_within(
    const wchar_t* path,
    const wchar_t* root,
    int require_strict_child) {
  size_t root_length = wcslen(root);
  while (root_length > 0u &&
         dort_windows_is_path_separator(root[root_length - 1u])) {
    --root_length;
  }
  /* Canonical containment must also be safe for case-sensitive directories. */
  if (root_length == 0u || wcsncmp(path, root, root_length) != 0) {
    return 0;
  }
  if (path[root_length] == L'\0') {
    return require_strict_child == 0;
  }
  if (!dort_windows_is_path_separator(path[root_length])) {
    return 0;
  }
  return require_strict_child == 0 || path[root_length + 1u] != L'\0';
}

static const wchar_t* dort_windows_strict_relative_path(
    const wchar_t* path,
    const wchar_t* root) {
  size_t root_length = wcslen(root);
  while (root_length > 0u &&
         dort_windows_is_path_separator(root[root_length - 1u])) {
    --root_length;
  }
  if (root_length == 0u || _wcsnicmp(path, root, root_length) != 0 ||
      !dort_windows_is_path_separator(path[root_length]) ||
      path[root_length + 1u] == L'\0') {
    return NULL;
  }
  return path + root_length + 1u;
}

static wchar_t* dort_windows_copy_wide_string(
    const wchar_t* value,
    size_t length) {
  wchar_t* result = NULL;
  if (length >= DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)dort_memory_allocate_zeroed(length + 1u, sizeof(wchar_t));
  if (result != NULL) {
    memcpy(result, value, length * sizeof(wchar_t));
  }
  return result;
}

static char* dort_windows_wide_path_to_utf8(const wchar_t* path) {
  int required = WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, path, -1, NULL, 0, NULL, NULL);
  char* result = NULL;
  if (required <= 0 || (size_t)required > DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (char*)dort_memory_allocate((size_t)required);
  if (result == NULL ||
      WideCharToMultiByte(
          CP_UTF8,
          WC_ERR_INVALID_CHARS,
          path,
          -1,
          result,
          required,
          NULL,
          NULL) != required) {
    free(result);
    return NULL;
  }
  return result;
}

static int dort_windows_missing_path_error(DWORD error_code) {
  return error_code == ERROR_FILE_NOT_FOUND ||
         error_code == ERROR_PATH_NOT_FOUND;
}
#endif

#if !defined(_WIN32)
static int dort_path_is_within(const char* path, const char* root) {
  size_t root_length = strlen(root);
  if (root_length == 1u && root[0] == '/') {
    return path[0] == '/';
  }
  return strncmp(path, root, root_length) == 0 &&
         (path[root_length] == '\0' || path[root_length] == '/');
}
#endif

static dort_status_t* dort_prepare_artifact_path(
    const char* path,
    const char* root,
    int reject_existing,
    char** out_path) {
  size_t ignored = 0u;
  *out_path = NULL;
  if (dort_bounded_utf8_length(path, DORT_MAX_PATH_BYTES, 0, &ignored) !=
          DORT_ERROR_NONE ||
      dort_bounded_utf8_length(root, DORT_MAX_PATH_BYTES, 0, &ignored) !=
          DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_UTF8,
        0,
        "session_options_create",
        "A session artifact path is invalid UTF-8 or exceeds its ABI limit.");
  }
#if defined(_WIN32)
  {
    wchar_t* wide_path = dort_utf8_to_wide_path(path);
    wchar_t* wide_root = dort_utf8_to_wide_path(root);
    wchar_t* full_path = NULL;
    wchar_t* full_root = NULL;
    wchar_t* canonical_root = NULL;
    wchar_t* canonical_path = NULL;
    wchar_t* canonical_parent = NULL;
    wchar_t* leaf = NULL;
    wchar_t* separator = NULL;
    wchar_t* alternative_separator = NULL;
    HANDLE root_handle = INVALID_HANDLE_VALUE;
    HANDLE path_handle = INVALID_HANDLE_VALUE;
    HANDLE parent_handle = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION root_information;
    BY_HANDLE_FILE_INFORMATION path_information;
    BY_HANDLE_FILE_INFORMATION parent_information;
    DWORD path_error = ERROR_SUCCESS;
    dort_status_t* status = NULL;
    char* result = NULL;
    if (wide_path == NULL || wide_root == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "A session artifact path could not be normalized.");
      goto windows_artifact_cleanup;
    }
    if (!dort_windows_path_is_absolute(wide_path) ||
        !dort_windows_path_is_absolute(wide_root)) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_NOT_ABSOLUTE,
          0,
          "session_options_create",
          "Session artifact paths and roots must be absolute filesystem paths.");
      goto windows_artifact_cleanup;
    }
    full_path = dort_windows_full_path(wide_path);
    full_root = dort_windows_full_path(wide_root);
    if (full_path == NULL || full_root == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "A session artifact path could not be normalized as a bounded Windows path.");
      goto windows_artifact_cleanup;
    }
    root_handle = CreateFileW(
        full_root,
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        NULL);
    if (root_handle == INVALID_HANDLE_VALUE ||
        !GetFileInformationByHandle(root_handle, &root_information) ||
        (root_information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0u) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The session artifact root does not identify an existing directory.");
      goto windows_artifact_cleanup;
    }
    canonical_root = dort_windows_final_path(root_handle);
    if (canonical_root == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PLATFORM,
          0,
          "session_options_create",
          "The session artifact root could not be canonicalized.");
      goto windows_artifact_cleanup;
    }

    path_handle = CreateFileW(
        full_path,
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        NULL);
    if (path_handle != INVALID_HANDLE_VALUE) {
      canonical_path = dort_windows_final_path(path_handle);
      if (canonical_path == NULL ||
          !GetFileInformationByHandle(path_handle, &path_information) ||
          !dort_windows_path_is_within(canonical_path, canonical_root, 1)) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
            0,
            "session_options_create",
            "A session artifact path resolves outside its allowed root.");
        goto windows_artifact_cleanup;
      }
      if ((path_information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0u) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "A session artifact path identifies a directory rather than a file destination.");
        goto windows_artifact_cleanup;
      }
      if (reject_existing) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "The optimized-model destination already exists and overwrite is disabled.");
        goto windows_artifact_cleanup;
      }
      result = dort_windows_wide_path_to_utf8(canonical_path);
    } else {
      path_error = GetLastError();
      if (!dort_windows_missing_path_error(path_error)) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "A session artifact destination could not be inspected safely.");
        goto windows_artifact_cleanup;
      }
      separator = wcsrchr(full_path, L'\\');
      alternative_separator = wcsrchr(full_path, L'/');
      if (alternative_separator != NULL &&
          (separator == NULL || alternative_separator > separator)) {
        separator = alternative_separator;
      }
      if (separator == NULL || separator[1] == L'\0' ||
          wcscmp(separator + 1, L".") == 0 ||
          wcscmp(separator + 1, L"..") == 0) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "A session artifact destination has an invalid leaf name.");
        goto windows_artifact_cleanup;
      }
      leaf = dort_windows_copy_wide_string(separator + 1, wcslen(separator + 1));
      if (leaf == NULL) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "session_options_create",
            "Could not copy a session artifact leaf name.");
        goto windows_artifact_cleanup;
      }
      if (separator == full_path + 2 && full_path[1] == L':') {
        separator[1] = L'\0';
      } else {
        *separator = L'\0';
      }
      parent_handle = CreateFileW(
          full_path,
          FILE_READ_ATTRIBUTES,
          FILE_SHARE_READ | FILE_SHARE_WRITE,
          NULL,
          OPEN_EXISTING,
          FILE_FLAG_BACKUP_SEMANTICS,
          NULL);
      if (parent_handle == INVALID_HANDLE_VALUE ||
          !GetFileInformationByHandle(parent_handle, &parent_information) ||
          (parent_information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0u) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "A session artifact parent does not identify an existing directory.");
        goto windows_artifact_cleanup;
      }
      canonical_parent = dort_windows_final_path(parent_handle);
      if (canonical_parent == NULL ||
          !dort_windows_path_is_within(
              canonical_parent, canonical_root, 0)) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
            0,
            "session_options_create",
            "A session artifact parent resolves outside its allowed root.");
        goto windows_artifact_cleanup;
      }
      {
        size_t parent_length = wcslen(canonical_parent);
        size_t leaf_length = wcslen(leaf);
        size_t needs_separator =
            dort_windows_is_path_separator(canonical_parent[parent_length - 1u])
                ? 0u
                : 1u;
        size_t result_length = 0u;
        if (!dort_checked_add_size(parent_length, needs_separator, &result_length) ||
            !dort_checked_add_size(result_length, leaf_length, &result_length) ||
            !dort_checked_add_size(result_length, 1u, &result_length) ||
            result_length > DORT_MAX_PATH_BYTES) {
          status = dort_status_create(
              DORT_ERROR_DOMAIN_SHIM,
              DORT_ERROR_LIMIT_EXCEEDED,
              0,
              "session_options_create",
              "A canonical session artifact path exceeds the ABI limit.");
          goto windows_artifact_cleanup;
        }
        canonical_path =
            (wchar_t*)dort_memory_allocate_zeroed(result_length, sizeof(wchar_t));
        if (canonical_path != NULL) {
          memcpy(
              canonical_path,
              canonical_parent,
              parent_length * sizeof(wchar_t));
          if (needs_separator != 0u) {
            canonical_path[parent_length] = L'\\';
          }
          memcpy(
              canonical_path + parent_length + needs_separator,
              leaf,
              (leaf_length + 1u) * sizeof(wchar_t));
        }
        if (canonical_path == NULL) {
          status = dort_status_create(
              DORT_ERROR_DOMAIN_ALLOCATION,
              DORT_ERROR_ALLOCATION_FAILED,
              0,
              "session_options_create",
              "Could not allocate a canonical session artifact path.");
          goto windows_artifact_cleanup;
        }
      }
      result = dort_windows_wide_path_to_utf8(canonical_path);
    }
    if (result == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not copy a canonical session artifact path as bounded UTF-8.");
      goto windows_artifact_cleanup;
    }
    *out_path = result;
    result = NULL;

windows_artifact_cleanup:
    if (parent_handle != INVALID_HANDLE_VALUE) {
      CloseHandle(parent_handle);
    }
    if (path_handle != INVALID_HANDLE_VALUE) {
      CloseHandle(path_handle);
    }
    if (root_handle != INVALID_HANDLE_VALUE) {
      CloseHandle(root_handle);
    }
    free(result);
    free(leaf);
    free(canonical_parent);
    free(canonical_path);
    free(canonical_root);
    free(full_path);
    free(full_root);
    free(wide_path);
    free(wide_root);
    return status;
  }
#else
  {
    char* canonical_root = NULL;
    char* canonical_existing = NULL;
    char* path_copy = NULL;
    char* slash = NULL;
    char* leaf_copy = NULL;
    char* canonical_parent = NULL;
    char* result = NULL;
    size_t result_length = 0u;
    struct stat root_stat;
    struct stat path_stat;
    if (path[0] != '/' || root[0] != '/') {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_NOT_ABSOLUTE,
          0,
          "session_options_create",
          "Session artifact paths and roots must be absolute.");
    }
    canonical_root = dort_memory_realpath(root);
    if (canonical_root == NULL) {
      if (errno == ENOMEM) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "session_options_create",
            "Could not allocate the canonical session artifact root.");
      }
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The session artifact root does not identify an existing directory.");
    }
    if (stat(canonical_root, &root_stat) != 0 ||
        !S_ISDIR(root_stat.st_mode)) {
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The session artifact root does not identify an existing directory.");
    }
    canonical_existing = dort_memory_realpath(path);
    if (canonical_existing == NULL && errno == ENOMEM) {
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not allocate a canonical existing session artifact path.");
    }
    if (canonical_existing != NULL) {
      if (!dort_path_is_within(canonical_existing, canonical_root)) {
        free(canonical_existing);
        free(canonical_root);
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
            0,
            "session_options_create",
            "A session artifact path is outside its allowed root.");
      }
      if (reject_existing) {
        free(canonical_existing);
        free(canonical_root);
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "The optimized-model destination already exists and overwrite is disabled.");
      }
      if (stat(canonical_existing, &path_stat) != 0 ||
          S_ISDIR(path_stat.st_mode)) {
        free(canonical_existing);
        free(canonical_root);
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "A session artifact path identifies an invalid destination.");
      }
      *out_path = canonical_existing;
      free(canonical_root);
      return NULL;
    }
    path_copy = dort_copy_c_string(path, strlen(path));
    if (path_copy == NULL) {
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not copy the session artifact destination path.");
    }
    slash = strrchr(path_copy, '/');
    if (slash == NULL || slash[1] == '\0' || strcmp(slash + 1, ".") == 0 ||
        strcmp(slash + 1, "..") == 0) {
      free(path_copy);
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "A session artifact destination has an invalid leaf name.");
    }
    leaf_copy = dort_copy_c_string(slash + 1, strlen(slash + 1));
    if (leaf_copy == NULL) {
      free(path_copy);
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not copy a session artifact leaf name.");
    }
    if (slash == path_copy) {
      slash[1] = '\0';
    } else {
      *slash = '\0';
    }
    canonical_parent = dort_memory_realpath(path_copy);
    if (canonical_parent == NULL && errno == ENOMEM) {
      free(path_copy);
      free(leaf_copy);
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not allocate the canonical session artifact parent.");
    }
    if (canonical_parent == NULL ||
        !dort_path_is_within(canonical_parent, canonical_root)) {
      free(path_copy);
      free(leaf_copy);
      free(canonical_parent);
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
          0,
          "session_options_create",
          "A session artifact parent is missing or outside its allowed root.");
    }
    if (!dort_checked_add_size(
            strlen(canonical_parent), strlen(leaf_copy), &result_length) ||
        !dort_checked_add_size(result_length, 2u, &result_length) ||
        result_length > DORT_MAX_PATH_BYTES) {
      free(path_copy);
      free(leaf_copy);
      free(canonical_parent);
      free(canonical_root);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          "session_options_create",
          "A normalized session artifact path exceeds the ABI limit.");
    }
    result = (char*)dort_memory_allocate(result_length);
    if (result != NULL) {
      (void)snprintf(
          result,
          result_length,
          "%s%s%s",
          canonical_parent,
          strcmp(canonical_parent, "/") == 0 ? "" : "/",
          leaf_copy);
    }
    free(path_copy);
    free(leaf_copy);
    free(canonical_parent);
    free(canonical_root);
    if (result == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not allocate a normalized session artifact path.");
    }
    *out_path = result;
    return NULL;
  }
#endif
}

#if !defined(_WIN32)
static int dort_is_lower_sha256(const char* value) {
  size_t index = 0u;
  if (value == NULL || strlen(value) != 64u) {
    return 0;
  }
  for (index = 0u; index < 64u; ++index) {
    if (!((value[index] >= '0' && value[index] <= '9') ||
          (value[index] >= 'a' && value[index] <= 'f'))) {
      return 0;
    }
  }
  return 1;
}
#endif

static dort_status_t* dort_prepare_coreml_cache_directory(
    const char* path,
    const char* root,
    char** out_path) {
  size_t ignored = 0u;
  *out_path = NULL;
  if (dort_bounded_utf8_length(path, DORT_MAX_PATH_BYTES, 0, &ignored) !=
          DORT_ERROR_NONE ||
      dort_bounded_utf8_length(root, DORT_MAX_PATH_BYTES, 0, &ignored) !=
          DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_PROVIDER,
        DORT_ERROR_INVALID_UTF8,
        0,
        "session_options_create",
        "The Core ML cache path is invalid or exceeds its ABI limit.");
  }
#if defined(_WIN32)
  (void)path;
  (void)root;
  return dort_status_create(
      DORT_ERROR_DOMAIN_PROVIDER,
      DORT_ERROR_PROVIDER_UNSUPPORTED,
      0,
      "session_options_create",
      "The Core ML cache directory is supported only on Apple platforms.");
#else
  {
    struct stat root_stat;
    struct stat cache_stat;
    char* canonical_root = NULL;
    char* canonical_cache = NULL;
    char* cache_path = NULL;
    const char* leaf = NULL;
    size_t root_length = strlen(root);
    int created = 0;
    dort_status_t* status = NULL;
    if (path[0] != '/' || root[0] != '/' || strcmp(root, "/") == 0 ||
        lstat(root, &root_stat) != 0 || S_ISLNK(root_stat.st_mode) ||
        !S_ISDIR(root_stat.st_mode)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The Core ML cache root must be an existing non-symlink directory.");
    }
    canonical_root = dort_memory_realpath(root);
    if (canonical_root == NULL && errno == ENOMEM) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not allocate the canonical Core ML cache root.");
    }
    if (canonical_root == NULL || strncmp(path, root, root_length) != 0 ||
        path[root_length] != '/' ||
        path[root_length + 1u] == '\0' ||
        strchr(path + root_length + 1u, '/') != NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
          0,
          "session_options_create",
          "The Core ML cache must be one direct scoped child of its canonical root.");
      goto coreml_cache_cleanup;
    }
    leaf = path + root_length + 1u;
    if (!dort_is_lower_sha256(leaf)) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The Core ML cache directory leaf is not a closed Fonix cache identity.");
      goto coreml_cache_cleanup;
    }
    {
      size_t cache_path_length = 0u;
      if (!dort_checked_add_size(
              strlen(canonical_root), strlen(leaf), &cache_path_length) ||
          !dort_checked_add_size(cache_path_length, 2u, &cache_path_length) ||
          cache_path_length > DORT_MAX_PATH_BYTES) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_LIMIT_EXCEEDED,
            0,
            "session_options_create",
            "The scoped Core ML cache path exceeds its ABI limit.");
        goto coreml_cache_cleanup;
      }
      cache_path = (char*)dort_memory_allocate(cache_path_length);
      if (cache_path == NULL) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "session_options_create",
            "The scoped Core ML cache path could not be allocated.");
        goto coreml_cache_cleanup;
      }
      (void)snprintf(
          cache_path, cache_path_length, "%s/%s", canonical_root, leaf);
    }
    if (lstat(cache_path, &cache_stat) != 0) {
      if (errno != ENOENT) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_PLATFORM,
            0,
            "session_options_create",
            "The scoped Core ML cache directory could not be created.");
        goto coreml_cache_cleanup;
      }
      if (mkdir(cache_path, S_IRWXU) == 0) {
        created = 1;
      } else if (errno != EEXIST) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_PLATFORM,
            0,
            "session_options_create",
            "The scoped Core ML cache directory could not be created.");
        goto coreml_cache_cleanup;
      }
      if (lstat(cache_path, &cache_stat) != 0) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_PLATFORM,
            0,
            "session_options_create",
            "The scoped Core ML cache directory could not be inspected.");
        goto coreml_cache_cleanup;
      }
    }
    if (S_ISLNK(cache_stat.st_mode) || !S_ISDIR(cache_stat.st_mode)) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The scoped Core ML cache destination is not a non-symlink directory.");
      goto coreml_cache_cleanup;
    }
    canonical_cache = dort_memory_realpath(cache_path);
    if (canonical_cache == NULL && errno == ENOMEM) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_options_create",
          "Could not allocate the canonical Core ML cache path.");
      goto coreml_cache_cleanup;
    }
    if (canonical_cache == NULL || strcmp(canonical_cache, cache_path) != 0 ||
        !dort_path_is_within(canonical_cache, canonical_root)) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
          0,
          "session_options_create",
          "The scoped Core ML cache directory resolves outside its root.");
      goto coreml_cache_cleanup;
    }
    *out_path = canonical_cache;
    canonical_cache = NULL;

coreml_cache_cleanup:
    if (status != NULL && created) {
      (void)rmdir(cache_path);
    }
    free(cache_path);
    free(canonical_cache);
    free(canonical_root);
    return status;
  }
#endif
}

#if defined(_WIN32) && defined(FONIX_WINDOWS_PATH_TESTING)
dort_status_t* dort_windows_test_prepare_artifact_path(
    const char* path,
    const char* root,
    int reject_existing,
    char** out_path) {
  return dort_prepare_artifact_path(path, root, reject_existing, out_path);
}
#endif

static GraphOptimizationLevel dort_ort_optimization_level(uint32_t level) {
  switch (level) {
    case DORT_GRAPH_OPTIMIZATION_DISABLED:
      return ORT_DISABLE_ALL;
    case DORT_GRAPH_OPTIMIZATION_BASIC:
      return ORT_ENABLE_BASIC;
    case DORT_GRAPH_OPTIMIZATION_EXTENDED:
      return ORT_ENABLE_EXTENDED;
    default:
      return ORT_ENABLE_ALL;
  }
}

static dort_status_t* dort_provider_unavailable(
    const char* provider_id,
    const char* reason) {
  return dort_status_createf(
      DORT_ERROR_DOMAIN_PROVIDER,
      DORT_ERROR_PROVIDER_UNSUPPORTED,
      0,
      "session_options_create",
      "Provider '%s' is unavailable: %s.",
      provider_id,
      reason);
}

static dort_status_t* dort_provider_option_vectors(
    const dort_provider_config_t* provider,
    const char* coreml_cache_directory,
    const char*** out_keys,
    const char*** out_values) {
  const char** keys = NULL;
  const char** values = NULL;
  size_t index = 0u;
  *out_keys = NULL;
  *out_values = NULL;
  if (provider->option_count == 0u) {
    return NULL;
  }
  keys = (const char**)dort_memory_allocate_zeroed(provider->option_count, sizeof(*keys));
  values = (const char**)dort_memory_allocate_zeroed(provider->option_count, sizeof(*values));
  if (keys == NULL || values == NULL) {
    free(keys);
    free(values);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_options_create",
        "Could not allocate provider option vectors.");
  }
  for (index = 0u; index < provider->option_count; ++index) {
    keys[index] = provider->options[index].key_utf8;
    values[index] =
        coreml_cache_directory != NULL &&
                strcmp(provider->provider_id_utf8, "coreml") == 0 &&
                strcmp(
                    provider->options[index].key_utf8,
                    "ModelCacheDirectory") == 0
            ? coreml_cache_directory
            : provider->options[index].value_utf8;
  }
  *out_keys = keys;
  *out_values = values;
  return NULL;
}

static dort_status_t* dort_apply_cuda_provider(
    dort_runtime_t* runtime,
    OrtSessionOptions* options,
    const dort_provider_config_t* provider) {
#if defined(FONIX_DESKTOP_PROVIDER_TESTING) || defined(_WIN32) || \
    (defined(__linux__) && !defined(__ANDROID__))
  const OrtApi* api = dort_runtime_api(runtime);
  const char** keys = NULL;
  const char** values = NULL;
  OrtCUDAProviderOptionsV2* cuda_options = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  if (api->CreateCUDAProviderOptions == NULL ||
      api->UpdateCUDAProviderOptions == NULL ||
      api->SessionOptionsAppendExecutionProvider_CUDA_V2 == NULL ||
      api->ReleaseCUDAProviderOptions == NULL) {
    return dort_provider_unavailable(
        "cuda", "the loaded API-27 table lacks the CUDA V2 option API");
  }
  status = dort_provider_option_vectors(provider, NULL, &keys, &values);
  if (status != NULL) {
    return status;
  }
  ort_status = api->CreateCUDAProviderOptions(&cuda_options);
  if (ort_status != NULL) {
    if (cuda_options != NULL) {
      api->ReleaseCUDAProviderOptions(cuda_options);
    }
    free(keys);
    free(values);
    return dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        "session_options_create");
  }
  if (cuda_options == NULL) {
    free(keys);
    free(values);
    return dort_provider_unavailable(
        "cuda", "the loaded runtime returned no CUDA V2 options object");
  }
  ort_status = api->UpdateCUDAProviderOptions(
      cuda_options, keys, values, provider->option_count);
  if (ort_status == NULL) {
    ort_status = api->SessionOptionsAppendExecutionProvider_CUDA_V2(
        options, cuda_options);
  }
  api->ReleaseCUDAProviderOptions(cuda_options);
  free(keys);
  free(values);
  return dort_status_from_ort(
      runtime,
      ort_status,
      DORT_ERROR_PROVIDER_UNSUPPORTED,
      "session_options_create");
#else
  (void)runtime;
  (void)options;
  (void)provider;
  return dort_provider_unavailable(
      "cuda", "this native target is not a supported CUDA desktop target");
#endif
}

static dort_status_t* dort_apply_tensorrt_provider(
    dort_runtime_t* runtime,
    OrtSessionOptions* options,
    const dort_provider_config_t* provider) {
#if defined(FONIX_DESKTOP_PROVIDER_TESTING) || defined(_WIN32) || \
    (defined(__linux__) && !defined(__ANDROID__))
  const OrtApi* api = dort_runtime_api(runtime);
  const char** keys = NULL;
  const char** values = NULL;
  OrtTensorRTProviderOptionsV2* tensorrt_options = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  if (api->CreateTensorRTProviderOptions == NULL ||
      api->UpdateTensorRTProviderOptions == NULL ||
      api->SessionOptionsAppendExecutionProvider_TensorRT_V2 == NULL ||
      api->ReleaseTensorRTProviderOptions == NULL) {
    return dort_provider_unavailable(
        "tensorrt", "the loaded API-27 table lacks the TensorRT V2 option API");
  }
  status = dort_provider_option_vectors(provider, NULL, &keys, &values);
  if (status != NULL) {
    return status;
  }
  ort_status = api->CreateTensorRTProviderOptions(&tensorrt_options);
  if (ort_status != NULL) {
    if (tensorrt_options != NULL) {
      api->ReleaseTensorRTProviderOptions(tensorrt_options);
    }
    free(keys);
    free(values);
    return dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        "session_options_create");
  }
  if (tensorrt_options == NULL) {
    free(keys);
    free(values);
    return dort_provider_unavailable(
        "tensorrt", "the loaded runtime returned no TensorRT V2 options object");
  }
  ort_status = api->UpdateTensorRTProviderOptions(
      tensorrt_options, keys, values, provider->option_count);
  if (ort_status == NULL) {
    ort_status = api->SessionOptionsAppendExecutionProvider_TensorRT_V2(
        options, tensorrt_options);
  }
  api->ReleaseTensorRTProviderOptions(tensorrt_options);
  free(keys);
  free(values);
  return dort_status_from_ort(
      runtime,
      ort_status,
      DORT_ERROR_PROVIDER_UNSUPPORTED,
      "session_options_create");
#else
  (void)runtime;
  (void)options;
  (void)provider;
  return dort_provider_unavailable(
      "tensorrt",
      "this native target is not a supported TensorRT desktop target");
#endif
}

static dort_status_t* dort_apply_dnnl_provider(
    dort_runtime_t* runtime,
    OrtSessionOptions* options,
    const dort_provider_config_t* provider) {
#if defined(FONIX_DESKTOP_PROVIDER_TESTING) || defined(_WIN32) || \
    (defined(__linux__) && !defined(__ANDROID__))
  const OrtApi* api = dort_runtime_api(runtime);
  const char** keys = NULL;
  const char** values = NULL;
  OrtDnnlProviderOptions* dnnl_options = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  if (api->CreateDnnlProviderOptions == NULL ||
      api->UpdateDnnlProviderOptions == NULL ||
      api->SessionOptionsAppendExecutionProvider_Dnnl == NULL ||
      api->ReleaseDnnlProviderOptions == NULL) {
    return dort_provider_unavailable(
        "dnnl", "the loaded API-27 table lacks the oneDNN option API");
  }
  status = dort_provider_option_vectors(provider, NULL, &keys, &values);
  if (status != NULL) {
    return status;
  }
  ort_status = api->CreateDnnlProviderOptions(&dnnl_options);
  if (ort_status != NULL) {
    if (dnnl_options != NULL) {
      api->ReleaseDnnlProviderOptions(dnnl_options);
    }
    free(keys);
    free(values);
    return dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        "session_options_create");
  }
  if (dnnl_options == NULL) {
    free(keys);
    free(values);
    return dort_provider_unavailable(
        "dnnl", "the loaded runtime returned no oneDNN options object");
  }
  ort_status = api->UpdateDnnlProviderOptions(
      dnnl_options, keys, values, provider->option_count);
  if (ort_status == NULL) {
    ort_status = api->SessionOptionsAppendExecutionProvider_Dnnl(
        options, dnnl_options);
  }
  api->ReleaseDnnlProviderOptions(dnnl_options);
  free(keys);
  free(values);
  return dort_status_from_ort(
      runtime,
      ort_status,
      DORT_ERROR_PROVIDER_UNSUPPORTED,
      "session_options_create");
#else
  (void)runtime;
  (void)options;
  (void)provider;
  return dort_provider_unavailable(
      "dnnl", "this native target is not a supported oneDNN desktop target");
#endif
}

static dort_status_t* dort_apply_migraphx_provider(
    dort_runtime_t* runtime,
    OrtSessionOptions* options,
    const dort_provider_config_t* provider) {
#if defined(FONIX_DESKTOP_PROVIDER_TESTING) || \
    (defined(__linux__) && !defined(__ANDROID__))
  const OrtApi* api = dort_runtime_api(runtime);
  OrtMIGraphXProviderOptions migraphx_options = {0};
  OrtStatus* ort_status = NULL;
  size_t index = 0u;
  migraphx_options.migraphx_mem_limit = SIZE_MAX;
  if (api->SessionOptionsAppendExecutionProvider_MIGraphX == NULL) {
    return dort_provider_unavailable(
        "migraphx", "the loaded API-27 table lacks the MIGraphX struct API");
  }
  for (index = 0u; index < provider->option_count; ++index) {
    const char* key = provider->options[index].key_utf8;
    const char* value = provider->options[index].value_utf8;
    int parsed = 0;
    if (strcmp(key, "device_id") == 0) {
      if (!dort_parse_nonnegative_int32(value, &migraphx_options.device_id)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "MIGraphX device_id is outside its closed non-negative domain.");
      }
    } else if (strcmp(key, "migraphx_fp16_enable") == 0) {
      if (!dort_parse_zero_or_one(value, &migraphx_options.migraphx_fp16_enable)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "MIGraphX boolean options must use zero or one.");
      }
    } else if (strcmp(key, "migraphx_fp8_enable") == 0) {
      if (!dort_parse_zero_or_one(value, &migraphx_options.migraphx_fp8_enable)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "MIGraphX boolean options must use zero or one.");
      }
    } else if (strcmp(key, "migraphx_int8_enable") == 0) {
      if (!dort_parse_zero_or_one(value, &migraphx_options.migraphx_int8_enable)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "MIGraphX boolean options must use zero or one.");
      }
    } else if (strcmp(key, "migraphx_exhaustive_tune") == 0) {
      if (!dort_parse_zero_or_one(value, &parsed)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "MIGraphX boolean options must use zero or one.");
      }
      migraphx_options.migraphx_exhaustive_tune = parsed != 0;
    } else if (strcmp(key, "migraphx_mem_limit") == 0) {
      if (!dort_parse_decimal_size(value, &migraphx_options.migraphx_mem_limit)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "MIGraphX memory limit is outside its closed decimal domain.");
      }
    } else if (strcmp(key, "migraphx_arena_extend_strategy") == 0) {
      if (strcmp(value, "kNextPowerOfTwo") == 0) {
        migraphx_options.migraphx_arena_extend_strategy = 0;
      } else if (strcmp(value, "kSameAsRequested") == 0) {
        migraphx_options.migraphx_arena_extend_strategy = 1;
      } else {
        return dort_status_create(
            DORT_ERROR_DOMAIN_PROVIDER,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "session_options_create",
            "MIGraphX arena strategy is outside its closed domain.");
      }
    }
  }
  ort_status = api->SessionOptionsAppendExecutionProvider_MIGraphX(
      options, &migraphx_options);
  return dort_status_from_ort(
      runtime,
      ort_status,
      DORT_ERROR_PROVIDER_UNSUPPORTED,
      "session_options_create");
#else
  (void)runtime;
  (void)options;
  (void)provider;
  return dort_provider_unavailable(
      "migraphx", "this native target is not a supported MIGraphX target");
#endif
}

typedef struct dort_dml_api {
  OrtStatus*(ORT_API_CALL* SessionOptionsAppendExecutionProvider_DML)(
      OrtSessionOptions* options,
      int device_id);
} dort_dml_api_t;

static dort_status_t* dort_apply_directml_provider(
    dort_runtime_t* runtime,
    OrtSessionOptions* options,
    const dort_provider_config_t* provider) {
#if defined(FONIX_DESKTOP_PROVIDER_TESTING) || defined(_WIN32)
  const OrtApi* api = dort_runtime_api(runtime);
  const void* raw_provider_api = NULL;
  const dort_dml_api_t* dml_api = NULL;
  OrtStatus* ort_status = NULL;
  int device_id = 0;
  if (api->GetExecutionProviderApi == NULL) {
    return dort_provider_unavailable(
        "directml", "the loaded API-27 table lacks provider API discovery");
  }
  ort_status = api->GetExecutionProviderApi(
      "DML", DORT_ORT_API_COMPATIBILITY_FLOOR, &raw_provider_api);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        "session_options_create");
  }
  dml_api = (const dort_dml_api_t*)raw_provider_api;
  if (dml_api == NULL ||
      dml_api->SessionOptionsAppendExecutionProvider_DML == NULL) {
    return dort_provider_unavailable(
        "directml", "the loaded runtime returned no compatible DML provider API");
  }
  if (!dort_parse_nonnegative_int32(
          dort_provider_option(provider, "device_id"), &device_id)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_PROVIDER,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "DirectML device_id is outside its closed non-negative domain.");
  }
  ort_status = dml_api->SessionOptionsAppendExecutionProvider_DML(
      options, device_id);
  return dort_status_from_ort(
      runtime,
      ort_status,
      DORT_ERROR_PROVIDER_UNSUPPORTED,
      "session_options_create");
#else
  (void)runtime;
  (void)options;
  (void)provider;
  return dort_provider_unavailable(
      "directml", "DirectML registration is available only on Windows");
#endif
}

static dort_status_t* dort_apply_provider(
    dort_runtime_t* runtime,
    OrtSessionOptions* options,
    const dort_provider_config_t* provider,
    const char* coreml_cache_directory) {
  const OrtApi* api = dort_runtime_api(runtime);
  const char* provider_name = NULL;
  const char** keys = NULL;
  const char** values = NULL;
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
  if (strcmp(provider->provider_id_utf8, "cpu") == 0) {
    return NULL;
  }
  if (strcmp(provider->provider_id_utf8, "nnapi") == 0) {
#if defined(__ANDROID__)
    typedef OrtStatus*(ORT_API_CALL* dort_append_nnapi_fn)(
        OrtSessionOptions*,
        uint32_t);
    void* symbol = dort_runtime_find_optional_symbol(
        runtime, "OrtSessionOptionsAppendExecutionProvider_Nnapi");
    dort_append_nnapi_fn append_nnapi = NULL;
    uint32_t flags = 0u;
    if (symbol == NULL ||
        !dort_parse_nnapi_flags(provider->options[0].value_utf8, &flags)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_PROVIDER,
          DORT_ERROR_PROVIDER_UNSUPPORTED,
          0,
          "session_options_create",
          "The selected Android runtime does not expose legacy NNAPI registration.");
    }
    _Static_assert(
        sizeof(append_nnapi) == sizeof(symbol),
        "Android data and function pointers must have matching sizes");
    memcpy(&append_nnapi, &symbol, sizeof(symbol));
    ort_status = append_nnapi(options, flags);
    return dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        "session_options_create");
#else
    return dort_status_create(
        DORT_ERROR_DOMAIN_PROVIDER,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        0,
        "session_options_create",
        "The legacy NNAPI provider is available only on Android.");
#endif
  }
  if (strcmp(provider->provider_id_utf8, "cuda") == 0) {
    return dort_apply_cuda_provider(runtime, options, provider);
  }
  if (strcmp(provider->provider_id_utf8, "tensorrt") == 0) {
    return dort_apply_tensorrt_provider(runtime, options, provider);
  }
  if (strcmp(provider->provider_id_utf8, "directml") == 0) {
    return dort_apply_directml_provider(runtime, options, provider);
  }
  if (strcmp(provider->provider_id_utf8, "dnnl") == 0) {
    return dort_apply_dnnl_provider(runtime, options, provider);
  }
  if (strcmp(provider->provider_id_utf8, "migraphx") == 0) {
    return dort_apply_migraphx_provider(runtime, options, provider);
  }
  if (strcmp(provider->provider_id_utf8, "openvino") == 0) {
#if !defined(FONIX_DESKTOP_PROVIDER_TESTING) && !defined(_WIN32) && \
    !(defined(__linux__) && !defined(__ANDROID__))
    return dort_provider_unavailable(
        "openvino", "this native target is not a supported OpenVINO target");
#endif
  }
  provider_name = dort_provider_name(provider->provider_id_utf8);
  if (provider_name == NULL) {
    return dort_provider_unavailable(
        provider->provider_id_utf8,
        "the provider has no supported API-27 registration mechanism");
  }
  status = dort_provider_option_vectors(
      provider, coreml_cache_directory, &keys, &values);
  if (status != NULL) {
    return status;
  }
  ort_status = api->SessionOptionsAppendExecutionProvider(
      options, provider_name, keys, values, provider->option_count);
  free(keys);
  free(values);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        "session_options_create");
  }
  return NULL;
}

dort_status_t* DORT_CALL dort_session_options_create(
    dort_runtime_t* runtime,
    const dort_session_config_t* config,
    dort_session_options_t** out_options) {
  const OrtApi* api = NULL;
  OrtSessionOptions* ort_options = NULL;
  dort_session_options_t* options = NULL;
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
  char* profile_path = NULL;
  char* optimized_path = NULL;
  char* coreml_cache_path = NULL;
  size_t index = 0u;

  if (out_options == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "The output session-options pointer is null.");
  }
  *out_options = NULL;
  if (!dort_runtime_is_valid(runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_options_create",
        "The runtime handle is null or invalid.");
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_SESSION_OPTIONS, "session_options_create");
  if (status != NULL) {
    return status;
  }
  status = dort_validate_session_config(config);
  if (status != NULL) {
    return status;
  }
  if (config->profile_path_prefix_utf8 != NULL) {
    status = dort_prepare_artifact_path(
        config->profile_path_prefix_utf8,
        config->artifact_root_utf8,
        0,
        &profile_path);
    if (status != NULL) {
      return status;
    }
  }
  if (config->optimized_model_path_utf8 != NULL) {
    status = dort_prepare_artifact_path(
        config->optimized_model_path_utf8,
        config->artifact_root_utf8,
        config->optimized_model_overwrite == 0u,
        &optimized_path);
    if (status != NULL) {
      free(profile_path);
      return status;
    }
  }
  if (dort_coreml_cache_directory(config) != NULL) {
    status = dort_prepare_coreml_cache_directory(
        dort_coreml_cache_directory(config),
        config->artifact_root_utf8,
        &coreml_cache_path);
    if (status != NULL) {
      free(profile_path);
      free(optimized_path);
      return status;
    }
  }
  api = dort_runtime_api(runtime);
  ort_status = api->CreateSessionOptions(&ort_options);
  if (ort_status != NULL || ort_options == NULL) {
    free(profile_path);
    free(optimized_path);
    free(coreml_cache_path);
    if (ort_options != NULL) {
      api->ReleaseSessionOptions(ort_options);
    }
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_ORT_API_UNAVAILABLE,
                     0,
                     "session_options_create",
                     "ONNX Runtime did not create session options.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_MODEL_INVALID,
                     "session_options_create");
  }
#define DORT_APPLY(call_expression)                                             \
  do {                                                                          \
    ort_status = (call_expression);                                             \
    if (ort_status != NULL) {                                                    \
      status = dort_status_from_ort(                                             \
          runtime,                                                              \
          ort_status,                                                           \
          DORT_ERROR_MODEL_INVALID,                                             \
          "session_options_create");                                           \
      goto cleanup;                                                             \
    }                                                                           \
  } while (0)

  DORT_APPLY(api->SetSessionGraphOptimizationLevel(
      ort_options,
      dort_ort_optimization_level(config->graph_optimization_level)));
  DORT_APPLY(api->SetSessionExecutionMode(
      ort_options,
      config->execution_mode == DORT_EXECUTION_PARALLEL ? ORT_PARALLEL
                                                        : ORT_SEQUENTIAL));
  DORT_APPLY(api->SetIntraOpNumThreads(
      ort_options, (int)config->intra_op_thread_count));
  DORT_APPLY(api->SetInterOpNumThreads(
      ort_options, (int)config->inter_op_thread_count));
  DORT_APPLY(config->enable_cpu_memory_arena != 0u
                 ? api->EnableCpuMemArena(ort_options)
                 : api->DisableCpuMemArena(ort_options));
  DORT_APPLY(config->enable_memory_pattern != 0u
                 ? api->EnableMemPattern(ort_options)
                 : api->DisableMemPattern(ort_options));
  DORT_APPLY(api->SetDeterministicCompute(
      ort_options, config->deterministic_compute != 0u));
  DORT_APPLY(api->SetSessionLogId(ort_options, config->log_id_utf8));
  DORT_APPLY(api->SetSessionLogSeverityLevel(
      ort_options, (int)config->log_severity));
  DORT_APPLY(api->SetSessionLogVerbosityLevel(
      ort_options, (int)config->log_verbosity));
#if defined(_WIN32)
  if (profile_path != NULL) {
    wchar_t* wide = dort_utf8_to_wide_path(profile_path);
    if (wide == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The normalized profiling path could not be converted.");
      goto cleanup;
    }
    ort_status = api->EnableProfiling(ort_options, wide);
    free(wide);
    if (ort_status != NULL) {
      status = dort_status_from_ort(
          runtime,
          ort_status,
          DORT_ERROR_MODEL_INVALID,
          "session_options_create");
      goto cleanup;
    }
  } else {
    DORT_APPLY(api->DisableProfiling(ort_options));
  }
  if (optimized_path != NULL) {
    wchar_t* wide = dort_utf8_to_wide_path(optimized_path);
    if (wide == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_options_create",
          "The normalized optimized-model path could not be converted.");
      goto cleanup;
    }
    ort_status = api->SetOptimizedModelFilePath(ort_options, wide);
    free(wide);
    if (ort_status != NULL) {
      status = dort_status_from_ort(
          runtime,
          ort_status,
          DORT_ERROR_MODEL_INVALID,
          "session_options_create");
      goto cleanup;
    }
  }
#else
  DORT_APPLY(profile_path != NULL ? api->EnableProfiling(ort_options, profile_path)
                                  : api->DisableProfiling(ort_options));
  if (optimized_path != NULL) {
    DORT_APPLY(api->SetOptimizedModelFilePath(ort_options, optimized_path));
  }
#endif
  for (index = 0u; index < config->config_entry_count; ++index) {
    DORT_APPLY(api->AddSessionConfigEntry(
        ort_options,
        config->config_entries[index].key_utf8,
        config->config_entries[index].value_utf8));
  }
  for (index = 0u; index < config->provider_count; ++index) {
    status = dort_apply_provider(
        runtime,
        ort_options,
        &config->providers[index],
        coreml_cache_path);
    if (status != NULL) {
      goto cleanup;
    }
  }
  options = (dort_session_options_t*)dort_memory_allocate_zeroed(1u, sizeof(*options));
  if (options == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_options_create",
        "Could not allocate the session-options handle.");
    goto cleanup;
  }
  dort_runtime_retain(runtime);
  options->magic = DORT_SESSION_OPTIONS_MAGIC;
  atomic_init(&options->reference_count, 1u);
  options->runtime = runtime;
  options->options = ort_options;
  options->max_model_bytes = config->max_model_bytes == 0u
                                 ? DORT_MAX_MODEL_BYTES_DEFAULT
                                 : config->max_model_bytes;
  ort_options = NULL;
  *out_options = options;

cleanup:
  free(profile_path);
  free(optimized_path);
  free(coreml_cache_path);
  if (ort_options != NULL) {
    api->ReleaseSessionOptions(ort_options);
  }
#undef DORT_APPLY
  return status;
}

void DORT_CALL dort_session_options_retain(dort_session_options_t* options) {
  unsigned int references = 0u;
  if (!dort_session_options_is_valid(options)) {
    return;
  }
  references = atomic_load_explicit(
      &options->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &options->reference_count,
            &references,
            references + 1u,
            memory_order_relaxed,
            memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_session_options_release(dort_session_options_t* options) {
  unsigned int previous = 0u;
  if (!dort_session_options_is_valid(options)) {
    return;
  }
  previous = atomic_fetch_sub_explicit(
      &options->reference_count, 1u, memory_order_acq_rel);
  if (previous == 1u) {
    dort_runtime_t* runtime = options->runtime;
    OrtSessionOptions* ort_options = options->options;
    options->magic = 0u;
    options->runtime = NULL;
    options->options = NULL;
    dort_runtime_api(runtime)->ReleaseSessionOptions(ort_options);
    free(options);
    dort_runtime_release(runtime);
  }
}

static void dort_free_names(char** names, size_t count) {
  size_t index = 0u;
  if (names == NULL) {
    return;
  }
  for (index = 0u; index < count; ++index) {
    free(names[index]);
  }
  free(names);
}

static dort_status_t* dort_append_recursive_type_info(
    dort_session_t* session,
    const OrtTypeInfo* type_info,
    dort_json_builder_t* builder,
    uint32_t depth,
    size_t* inout_nodes,
    uint32_t* out_kind) {
  const OrtApi* api = dort_runtime_api(session->runtime);
  ONNXType onnx_type = ONNX_TYPE_UNKNOWN;
  OrtStatus* ort_status = NULL;
  *out_kind = 0u;
  if (depth > DORT_MAX_VALUE_NESTING_DEPTH ||
      *inout_nodes >= DORT_MAX_VALUE_NODES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "session_type_metadata",
        "The model type tree exceeds the recursive depth or node-count limit.");
  }
  ++*inout_nodes;
  ort_status = api->GetOnnxTypeFromTypeInfo(type_info, &onnx_type);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        session->runtime,
        ort_status,
        DORT_ERROR_MODEL_INVALID,
        "session_type_metadata");
  }
  *out_kind = (uint32_t)onnx_type;
  if (onnx_type == ONNX_TYPE_TENSOR) {
    const OrtTensorTypeAndShapeInfo* tensor_info = NULL;
    ONNXTensorElementDataType element_type =
        ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
    int64_t dimensions[DORT_MAX_TENSOR_RANK];
    const char* symbolic[DORT_MAX_TENSOR_RANK] = {NULL};
    size_t dimension_count = 0u;
    size_t index = 0u;
    int has_shape = 0;
    memset(dimensions, 0, sizeof(dimensions));
    ort_status = api->CastTypeInfoToTensorInfo(type_info, &tensor_info);
    if (ort_status == NULL && tensor_info == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_type_metadata",
          "ONNX Runtime returned null tensor type metadata.");
    }
    if (ort_status == NULL) {
      ort_status = api->GetTensorElementType(tensor_info, &element_type);
    }
    if (ort_status == NULL) {
      has_shape = api->TensorTypeAndShape_HasShape(tensor_info) ? 1 : 0;
    }
    if (ort_status == NULL && has_shape) {
      ort_status = api->GetDimensionsCount(tensor_info, &dimension_count);
    }
    if (ort_status == NULL && dimension_count > DORT_MAX_TENSOR_RANK) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          "session_type_metadata",
          "A model tensor rank exceeds the ABI limit.");
    }
    if (ort_status == NULL && dimension_count > 0u) {
      ort_status = api->GetDimensions(
          tensor_info, dimensions, dimension_count);
    }
    if (ort_status == NULL && dimension_count > 0u) {
      ort_status = api->GetSymbolicDimensions(
          tensor_info, symbolic, dimension_count);
    }
    if (ort_status != NULL) {
      return dort_status_from_ort(
          session->runtime,
          ort_status,
          DORT_ERROR_MODEL_INVALID,
          "session_type_metadata");
    }
    if (!dort_session_element_type_is_supported(element_type)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_UNSUPPORTED,
          DORT_ERROR_VALUE_KIND_UNSUPPORTED,
          0,
          "session_type_metadata",
          "A model tensor uses an unsupported element type.");
    }
    if (!dort_json_appendf(
            builder,
            "{\"kind\":\"tensor\",\"elementType\":%u,\"hasShape\":%s,\"dimensions\":",
            (unsigned int)element_type,
            has_shape ? "true" : "false")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    if (!has_shape) {
      if (!dort_json_append(
              builder,
              "[],\"symbolicDimensions\":[]}")) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "session_type_metadata",
            "The bounded recursive type JSON could not be allocated.");
      }
      return NULL;
    }
    if (!dort_json_append(builder, "[")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    for (index = 0u; index < dimension_count; ++index) {
      int appended = 0;
      if (dimensions[index] < -1 ||
          (dimensions[index] >= 0 &&
           (uint64_t)dimensions[index] >
               (uint64_t)DORT_MAX_TENSOR_DIMENSION)) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_LIMIT_EXCEEDED,
            0,
            "session_type_metadata",
            "A model tensor dimension is invalid or exceeds the ABI limit.");
      }
      appended = dimensions[index] < 0
                     ? dort_json_append(
                           builder, index == 0u ? "null" : ",null")
                     : dort_json_appendf(
                           builder,
                           index == 0u ? "%" PRId64 : ",%" PRId64,
                           dimensions[index]);
      if (!appended) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "session_type_metadata",
            "The bounded recursive type JSON could not be allocated.");
      }
    }
    if (!dort_json_append(builder, "],\"symbolicDimensions\":[")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    for (index = 0u; index < dimension_count; ++index) {
      const char* symbol = symbolic[index] == NULL ? "" : symbolic[index];
      size_t symbol_length = 0u;
      int validation = dort_bounded_utf8_length(
          symbol, DORT_MAX_NAME_BYTES, 1, &symbol_length);
      if (validation != DORT_ERROR_NONE ||
          !dort_json_append(builder, index == 0u ? "" : ",") ||
          (symbol_length == 0u
               ? !dort_json_append(builder, "null")
               : !dort_json_append_string(builder, symbol, symbol_length))) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            validation == DORT_ERROR_NONE ? DORT_ERROR_LIMIT_EXCEEDED
                                          : validation,
            0,
            "session_type_metadata",
            "A symbolic dimension is invalid or recursive metadata is oversized.");
      }
    }
    if (!dort_json_append(builder, "]}")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    return NULL;
  }
  if (onnx_type == ONNX_TYPE_SEQUENCE) {
    const OrtSequenceTypeInfo* sequence_info = NULL;
    OrtTypeInfo* element_info = NULL;
    uint32_t ignored_kind = 0u;
    dort_status_t* status = NULL;
    ort_status = api->CastTypeInfoToSequenceTypeInfo(type_info, &sequence_info);
    if (ort_status == NULL && sequence_info == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_type_metadata",
          "ONNX Runtime returned null sequence type metadata.");
    }
    if (ort_status == NULL) {
      ort_status = api->GetSequenceElementType(sequence_info, &element_info);
    }
    if (ort_status != NULL || element_info == NULL) {
      if (element_info != NULL) {
        api->ReleaseTypeInfo(element_info);
      }
      return ort_status == NULL
                 ? dort_status_create(
                       DORT_ERROR_DOMAIN_ORT_API,
                       DORT_ERROR_MODEL_INVALID,
                       0,
                       "session_type_metadata",
                       "ONNX Runtime returned null sequence element metadata.")
                 : dort_status_from_ort(
                       session->runtime,
                       ort_status,
                       DORT_ERROR_MODEL_INVALID,
                       "session_type_metadata");
    }
    if (!dort_json_append(builder, "{\"kind\":\"sequence\",\"element\":")) {
      api->ReleaseTypeInfo(element_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    status = dort_append_recursive_type_info(
        session,
        element_info,
        builder,
        depth + 1u,
        inout_nodes,
        &ignored_kind);
    api->ReleaseTypeInfo(element_info);
    if (status != NULL) {
      return status;
    }
    if (!dort_json_append(builder, "}")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    return NULL;
  }
  if (onnx_type == ONNX_TYPE_MAP) {
    const OrtMapTypeInfo* map_info = NULL;
    OrtTypeInfo* value_info = NULL;
    ONNXTensorElementDataType key_type =
        ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
    uint32_t ignored_kind = 0u;
    dort_status_t* status = NULL;
    ort_status = api->CastTypeInfoToMapTypeInfo(type_info, &map_info);
    if (ort_status == NULL && map_info == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_type_metadata",
          "ONNX Runtime returned null map type metadata.");
    }
    if (ort_status == NULL) {
      ort_status = api->GetMapKeyType(map_info, &key_type);
    }
    if (ort_status == NULL) {
      ort_status = api->GetMapValueType(map_info, &value_info);
    }
    if (ort_status != NULL || value_info == NULL) {
      if (value_info != NULL) {
        api->ReleaseTypeInfo(value_info);
      }
      return ort_status == NULL
                 ? dort_status_create(
                       DORT_ERROR_DOMAIN_ORT_API,
                       DORT_ERROR_MODEL_INVALID,
                       0,
                       "session_type_metadata",
                       "ONNX Runtime returned null map value metadata.")
                 : dort_status_from_ort(
                       session->runtime,
                       ort_status,
                       DORT_ERROR_MODEL_INVALID,
                       "session_type_metadata");
    }
    if (key_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING &&
        key_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64) {
      api->ReleaseTypeInfo(value_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_UNSUPPORTED,
          DORT_ERROR_VALUE_KIND_UNSUPPORTED,
          0,
          "session_type_metadata",
          "The map key element type is unsupported by API-27 ONNX-ML values.");
    }
    if (!dort_json_appendf(
            builder,
            "{\"kind\":\"map\",\"keyElementType\":%u,\"value\":",
            (unsigned int)key_type)) {
      api->ReleaseTypeInfo(value_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    status = dort_append_recursive_type_info(
        session,
        value_info,
        builder,
        depth + 1u,
        inout_nodes,
        &ignored_kind);
    api->ReleaseTypeInfo(value_info);
    if (status != NULL) {
      return status;
    }
    if (!dort_json_append(builder, "}")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    return NULL;
  }
  if (onnx_type == ONNX_TYPE_OPTIONAL) {
    const OrtOptionalTypeInfo* optional_info = NULL;
    OrtTypeInfo* contained_info = NULL;
    uint32_t ignored_kind = 0u;
    dort_status_t* status = NULL;
    ort_status = api->CastTypeInfoToOptionalTypeInfo(type_info, &optional_info);
    if (ort_status == NULL && optional_info == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_type_metadata",
          "ONNX Runtime returned null optional type metadata.");
    }
    if (ort_status == NULL) {
      ort_status = api->GetOptionalContainedTypeInfo(
          optional_info, &contained_info);
    }
    if (ort_status != NULL || contained_info == NULL) {
      if (contained_info != NULL) {
        api->ReleaseTypeInfo(contained_info);
      }
      return ort_status == NULL
                 ? dort_status_create(
                       DORT_ERROR_DOMAIN_ORT_API,
                       DORT_ERROR_MODEL_INVALID,
                       0,
                       "session_type_metadata",
                       "ONNX Runtime returned null optional contained metadata.")
                 : dort_status_from_ort(
                       session->runtime,
                       ort_status,
                       DORT_ERROR_MODEL_INVALID,
                       "session_type_metadata");
    }
    if (!dort_json_append(builder, "{\"kind\":\"optional\",\"element\":")) {
      api->ReleaseTypeInfo(contained_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    status = dort_append_recursive_type_info(
        session,
        contained_info,
        builder,
        depth + 1u,
        inout_nodes,
        &ignored_kind);
    api->ReleaseTypeInfo(contained_info);
    if (status != NULL) {
      return status;
    }
    if (!dort_json_append(builder, "}")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive type JSON could not be allocated.");
    }
    return NULL;
  }
  return dort_status_create(
      DORT_ERROR_DOMAIN_UNSUPPORTED,
      DORT_ERROR_VALUE_KIND_UNSUPPORTED,
      0,
      "session_type_metadata",
      "Sparse, opaque, unknown, and other unsupported model value kinds are rejected.");
}

static dort_status_t* dort_append_recursive_io_metadata(
    dort_session_t* session,
    int is_input,
    dort_json_builder_t* builder,
    int* inout_all_tensors,
    size_t* inout_nodes) {
  const OrtApi* api = dort_runtime_api(session->runtime);
  size_t count = is_input ? session->input_count : session->output_count;
  char** names = is_input ? session->input_names : session->output_names;
  uint32_t* kinds = is_input ? session->input_kinds : session->output_kinds;
  OrtAllocator* allocator = NULL;
  OrtStatus* ort_status = api->GetAllocatorWithDefaultOptions(&allocator);
  size_t index = 0u;
  if (ort_status != NULL || allocator == NULL) {
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_ORT_API_UNAVAILABLE,
                     0,
                     "session_type_metadata",
                     "ONNX Runtime did not return its default allocator.")
               : dort_status_from_ort(
                     session->runtime,
                     ort_status,
                     DORT_ERROR_MODEL_INVALID,
                     "session_type_metadata");
  }
  for (index = 0u; index < count; ++index) {
    char* ort_name = NULL;
    OrtTypeInfo* type_info = NULL;
    size_t name_length = 0u;
    int validation = DORT_ERROR_NONE;
    dort_status_t* status = NULL;
    ort_status = is_input
                     ? api->SessionGetInputName(
                           session->session, index, allocator, &ort_name)
                     : api->SessionGetOutputName(
                           session->session, index, allocator, &ort_name);
    if (ort_status != NULL || ort_name == NULL) {
      if (ort_name != NULL) {
        OrtStatus* free_status = api->AllocatorFree(allocator, ort_name);
        if (free_status != NULL) {
          api->ReleaseStatus(free_status);
        }
      }
      return ort_status == NULL
                 ? dort_status_create(
                       DORT_ERROR_DOMAIN_ORT_API,
                       DORT_ERROR_MODEL_INVALID,
                       0,
                       "session_type_metadata",
                       "ONNX Runtime returned a null model I/O name.")
                 : dort_status_from_ort(
                       session->runtime,
                       ort_status,
                       DORT_ERROR_MODEL_INVALID,
                       "session_type_metadata");
    }
    validation = dort_bounded_utf8_length(
        ort_name, DORT_MAX_NAME_BYTES, 0, &name_length);
    if (validation == DORT_ERROR_NONE) {
      names[index] = dort_copy_c_string(ort_name, name_length);
    }
    ort_status = api->AllocatorFree(allocator, ort_name);
    if (ort_status != NULL) {
      return dort_status_from_ort(
          session->runtime,
          ort_status,
          DORT_ERROR_MODEL_INVALID,
          "session_type_metadata");
    }
    if (validation != DORT_ERROR_NONE || names[index] == NULL) {
      return dort_status_create(
          validation == DORT_ERROR_NONE ? DORT_ERROR_DOMAIN_ALLOCATION
                                        : DORT_ERROR_DOMAIN_SHIM,
          validation == DORT_ERROR_NONE ? DORT_ERROR_ALLOCATION_FAILED
                                        : validation,
          0,
          "session_type_metadata",
          "A model I/O name is invalid, oversized, or could not be copied.");
    }
    ort_status = is_input
                     ? api->SessionGetInputTypeInfo(
                           session->session, index, &type_info)
                     : api->SessionGetOutputTypeInfo(
                           session->session, index, &type_info);
    if (ort_status != NULL || type_info == NULL) {
      if (type_info != NULL) {
        api->ReleaseTypeInfo(type_info);
      }
      return ort_status == NULL
                 ? dort_status_create(
                       DORT_ERROR_DOMAIN_ORT_API,
                       DORT_ERROR_MODEL_INVALID,
                       0,
                       "session_type_metadata",
                       "ONNX Runtime returned null model I/O type information.")
                 : dort_status_from_ort(
                       session->runtime,
                       ort_status,
                       DORT_ERROR_MODEL_INVALID,
                       "session_type_metadata");
    }
    if (!dort_json_append(builder, index == 0u ? "" : ",") ||
        !dort_json_append(builder, "{\"name\":") ||
        !dort_json_append_string(builder, names[index], name_length) ||
        !dort_json_append(builder, ",\"type\":")) {
      api->ReleaseTypeInfo(type_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive session metadata JSON could not be allocated.");
    }
    status = dort_append_recursive_type_info(
        session, type_info, builder, 1u, inout_nodes, &kinds[index]);
    api->ReleaseTypeInfo(type_info);
    if (status != NULL) {
      return status;
    }
    if (kinds[index] != DORT_VALUE_KIND_TENSOR) {
      *inout_all_tensors = 0;
    }
    if (!dort_json_append(builder, "}")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_type_metadata",
          "The bounded recursive session metadata JSON could not be allocated.");
    }
  }
  return NULL;
}

static dort_status_t* dort_append_io_metadata(
    dort_session_t* session,
    int is_input,
    dort_json_builder_t* builder) {
  const OrtApi* api = dort_runtime_api(session->runtime);
  size_t count = is_input ? session->input_count : session->output_count;
  char** names = is_input ? session->input_names : session->output_names;
  size_t index = 0u;
  OrtStatus* ort_status = NULL;
  for (index = 0u; index < count; ++index) {
    size_t name_length = strlen(names[index]);
    OrtTypeInfo* type_info = NULL;
    const OrtTensorTypeAndShapeInfo* tensor_info = NULL;
    ONNXType onnx_type = ONNX_TYPE_UNKNOWN;
    ONNXTensorElementDataType element_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
    int64_t dimensions[DORT_MAX_TENSOR_RANK];
    const char* symbolic[DORT_MAX_TENSOR_RANK] = {NULL};
    size_t dimension_count = 0u;
    size_t dimension_index = 0u;
    int validation = DORT_ERROR_NONE;
    int has_shape = 0;
    memset(dimensions, 0, sizeof(dimensions));
    ort_status = is_input
                     ? api->SessionGetInputTypeInfo(
                           session->session, index, &type_info)
                     : api->SessionGetOutputTypeInfo(
                           session->session, index, &type_info);
    if (ort_status == NULL && type_info == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_metadata",
          "ONNX Runtime returned null model I/O type information.");
    }
    if (ort_status == NULL) {
      ort_status = api->GetOnnxTypeFromTypeInfo(type_info, &onnx_type);
    }
    if (ort_status == NULL && onnx_type == ONNX_TYPE_TENSOR) {
      ort_status = api->CastTypeInfoToTensorInfo(type_info, &tensor_info);
    }
    if (ort_status == NULL &&
        (onnx_type != ONNX_TYPE_TENSOR || tensor_info == NULL)) {
      if (type_info != NULL) {
        api->ReleaseTypeInfo(type_info);
      }
      return dort_status_create(
          DORT_ERROR_DOMAIN_UNSUPPORTED,
          DORT_ERROR_NOT_TENSOR,
          0,
          "session_metadata",
          "ABI v1 accepts only models with dense tensor inputs and outputs.");
    }
    if (ort_status == NULL) {
      ort_status = api->GetTensorElementType(tensor_info, &element_type);
    }
    if (ort_status == NULL) {
      has_shape = api->TensorTypeAndShape_HasShape(tensor_info) ? 1 : 0;
    }
    if (ort_status == NULL && has_shape) {
      ort_status = api->GetDimensionsCount(tensor_info, &dimension_count);
    }
    if (ort_status == NULL &&
        !dort_session_element_type_is_supported(element_type)) {
      if (type_info != NULL) {
        api->ReleaseTypeInfo(type_info);
      }
      return dort_status_create(
          DORT_ERROR_DOMAIN_UNSUPPORTED,
          DORT_ERROR_TENSOR_INVALID,
          0,
          "session_metadata",
          "A model I/O tensor uses an unsupported non-fixed-width element type.");
    }
    if (ort_status == NULL && dimension_count > DORT_MAX_TENSOR_RANK) {
      api->ReleaseTypeInfo(type_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          "session_metadata",
          "A model I/O tensor rank exceeds the ABI limit.");
    }
    if (ort_status == NULL && dimension_count > 0u) {
      ort_status = api->GetDimensions(
          tensor_info, dimensions, dimension_count);
    }
    if (ort_status == NULL && dimension_count > 0u) {
      ort_status = api->GetSymbolicDimensions(
          tensor_info, symbolic, dimension_count);
    }
    if (ort_status != NULL) {
      if (type_info != NULL) {
        api->ReleaseTypeInfo(type_info);
      }
      return dort_status_from_ort(
          session->runtime,
          ort_status,
          DORT_ERROR_MODEL_INVALID,
          "session_metadata");
    }
    for (dimension_index = 0u; dimension_index < dimension_count;
         ++dimension_index) {
      if (dimensions[dimension_index] < -1 ||
          (dimensions[dimension_index] >= 0 &&
           (uint64_t)dimensions[dimension_index] >
               (uint64_t)DORT_MAX_TENSOR_DIMENSION)) {
        api->ReleaseTypeInfo(type_info);
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_LIMIT_EXCEEDED,
            0,
            "session_metadata",
            "A model I/O dimension is invalid or exceeds the ABI limit.");
      }
    }
    if (!dort_json_append(builder, index == 0u ? "" : ",") ||
        !dort_json_append(builder, "{\"name\":") ||
        !dort_json_append_string(builder, names[index], name_length) ||
        !dort_json_appendf(
            builder,
            ",\"kind\":\"tensor\",\"elementType\":%u,\"hasShape\":%s,\"dimensions\":",
            (unsigned int)element_type,
            has_shape ? "true" : "false")) {
      api->ReleaseTypeInfo(type_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_metadata",
          "The bounded model metadata JSON could not be allocated.");
    }
    if (!has_shape) {
      api->ReleaseTypeInfo(type_info);
      if (!dort_json_append(
              builder,
              "[],\"symbolicDimensions\":[]}")) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "session_metadata",
            "The bounded model metadata JSON could not be allocated.");
      }
      continue;
    }
    if (!dort_json_append(builder, "[")) {
      api->ReleaseTypeInfo(type_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_metadata",
          "The bounded model metadata JSON could not be allocated.");
    }
    for (dimension_index = 0u; dimension_index < dimension_count;
         ++dimension_index) {
      int appended = 0;
      if (dimensions[dimension_index] < 0) {
        appended = dort_json_append(
            builder, dimension_index == 0u ? "null" : ",null");
      } else {
        appended = dort_json_appendf(
            builder,
            dimension_index == 0u ? "%" PRId64 : ",%" PRId64,
            dimensions[dimension_index]);
      }
      if (!appended) {
        api->ReleaseTypeInfo(type_info);
        return dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "session_metadata",
            "The bounded model metadata JSON could not be allocated.");
      }
    }
    if (!dort_json_append(builder, "],\"symbolicDimensions\":[")) {
      api->ReleaseTypeInfo(type_info);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_metadata",
          "The bounded model metadata JSON could not be allocated.");
    }
    for (dimension_index = 0u; dimension_index < dimension_count;
         ++dimension_index) {
      const char* symbol = symbolic[dimension_index] == NULL
                               ? ""
                               : symbolic[dimension_index];
      size_t symbol_length = 0u;
      validation = dort_bounded_utf8_length(
          symbol, DORT_MAX_NAME_BYTES, 1, &symbol_length);
      if (validation != DORT_ERROR_NONE ||
          !dort_json_append(
              builder, dimension_index == 0u ? "" : ",") ||
          (symbol_length == 0u
               ? !dort_json_append(builder, "null")
               : !dort_json_append_string(builder, symbol, symbol_length))) {
        api->ReleaseTypeInfo(type_info);
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            validation == DORT_ERROR_NONE ? DORT_ERROR_LIMIT_EXCEEDED
                                          : validation,
            0,
            "session_metadata",
            "A symbolic dimension is invalid or the metadata exceeds its ABI limit.");
      }
    }
    api->ReleaseTypeInfo(type_info);
    if (!dort_json_append(builder, "]}")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_metadata",
          "The bounded model metadata JSON could not be allocated.");
    }
  }
  return NULL;
}

static void dort_allocator_free_ignored(
    const OrtApi* api,
    OrtAllocator* allocator,
    void* allocation) {
  OrtStatus* free_status = NULL;
  if (allocation == NULL) {
    return;
  }
  free_status = api->AllocatorFree(allocator, allocation);
  if (free_status != NULL) {
    api->ReleaseStatus(free_status);
  }
}

static dort_status_t* dort_append_owned_model_string(
    dort_session_t* session,
    OrtAllocator* allocator,
    dort_json_builder_t* builder,
    const char* json_prefix,
    char** inout_owned_value,
    size_t maximum,
    const char* field_name) {
  const OrtApi* api = dort_runtime_api(session->runtime);
  char* value = *inout_owned_value;
  size_t length = 0u;
  int validation = DORT_ERROR_NONE;
  int appended = 0;
  *inout_owned_value = NULL;
  if (value == NULL) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_MODEL_INVALID,
        0,
        "session_model_metadata",
        "ONNX Runtime returned null %s metadata.",
        field_name);
  }
  validation = dort_bounded_utf8_length(value, maximum, 1, &length);
  if (validation == DORT_ERROR_NONE) {
    appended = dort_json_append(builder, json_prefix) &&
               dort_json_append_string(builder, value, length);
  }
  dort_allocator_free_ignored(api, allocator, value);
  if (validation != DORT_ERROR_NONE) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_SHIM,
        validation,
        0,
        "session_model_metadata",
        "The %s metadata is invalid UTF-8 or exceeds its ABI limit.",
        field_name);
  }
  if (!appended) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_model_metadata",
        "Could not allocate bounded model metadata JSON.");
  }
  return NULL;
}

static int dort_compare_c_string_pointers(
    const void* left_pointer,
    const void* right_pointer) {
  const char* const left = *(const char* const*)left_pointer;
  const char* const right = *(const char* const*)right_pointer;
  return strcmp(left, right);
}

static void dort_free_model_metadata_keys(
    const OrtApi* api,
    OrtAllocator* allocator,
    char** keys,
    size_t count) {
  size_t index = 0u;
  if (keys == NULL) {
    return;
  }
  for (index = 0u; index < count; ++index) {
    dort_allocator_free_ignored(api, allocator, keys[index]);
  }
  dort_allocator_free_ignored(api, allocator, keys);
}

static dort_status_t* dort_initialize_model_metadata(dort_session_t* session) {
  const OrtApi* api = dort_runtime_api(session->runtime);
  OrtAllocator* allocator = NULL;
  OrtModelMetadata* metadata = NULL;
  OrtStatus* ort_status = NULL;
  dort_json_builder_t builder;
  dort_status_t* status = NULL;
  char* value = NULL;
  char** keys = NULL;
  int64_t key_count_signed = 0;
  int64_t version = 0;
  size_t key_count = 0u;
  size_t key_cleanup_count = 0u;
  size_t index = 0u;
  memset(&builder, 0, sizeof(builder));

  ort_status = api->GetAllocatorWithDefaultOptions(&allocator);
  if (ort_status != NULL || allocator == NULL) {
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_ORT_API_UNAVAILABLE,
                     0,
                     "session_model_metadata",
                     "ONNX Runtime did not return its default allocator.")
               : dort_status_from_ort(
                     session->runtime,
                     ort_status,
                     DORT_ERROR_MODEL_INVALID,
                     "session_model_metadata");
  }
  ort_status = api->SessionGetModelMetadata(session->session, &metadata);
  if (ort_status != NULL) {
    status = dort_status_from_ort(
        session->runtime,
        ort_status,
        DORT_ERROR_MODEL_INVALID,
        "session_model_metadata");
    goto cleanup;
  }
  if (metadata == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_MODEL_INVALID,
        0,
        "session_model_metadata",
        "ONNX Runtime returned null model metadata.");
    goto cleanup;
  }
  if (!dort_json_append(&builder, "{\"schemaVersion\":1")) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_model_metadata",
        "Could not allocate bounded model metadata JSON.");
    goto cleanup;
  }

#define DORT_APPEND_MODEL_FIELD(call, prefix, field_name)                      \
  do {                                                                         \
    value = NULL;                                                              \
    ort_status = (call);                                                       \
    if (ort_status != NULL) {                                                  \
      status = dort_status_from_ort(                                           \
          session->runtime,                                                    \
          ort_status,                                                          \
          DORT_ERROR_MODEL_INVALID,                                            \
          "session_model_metadata");                                         \
      goto cleanup;                                                            \
    }                                                                          \
    status = dort_append_owned_model_string(                                   \
        session,                                                               \
        allocator,                                                             \
        &builder,                                                              \
        prefix,                                                                \
        &value,                                                                \
        DORT_MAX_OWNED_STRING_BYTES,                                           \
        field_name);                                                           \
    if (status != NULL) {                                                      \
      goto cleanup;                                                            \
    }                                                                          \
  } while (0)

  DORT_APPEND_MODEL_FIELD(
      api->ModelMetadataGetProducerName(metadata, allocator, &value),
      ",\"producerName\":",
      "producer-name");
  DORT_APPEND_MODEL_FIELD(
      api->ModelMetadataGetGraphName(metadata, allocator, &value),
      ",\"graphName\":",
      "graph-name");
  DORT_APPEND_MODEL_FIELD(
      api->ModelMetadataGetDomain(metadata, allocator, &value),
      ",\"domain\":",
      "domain");
  DORT_APPEND_MODEL_FIELD(
      api->ModelMetadataGetDescription(metadata, allocator, &value),
      ",\"description\":",
      "description");
  DORT_APPEND_MODEL_FIELD(
      api->ModelMetadataGetGraphDescription(metadata, allocator, &value),
      ",\"graphDescription\":",
      "graph-description");
#undef DORT_APPEND_MODEL_FIELD

  ort_status = api->ModelMetadataGetVersion(metadata, &version);
  if (ort_status != NULL) {
    status = dort_status_from_ort(
        session->runtime,
        ort_status,
        DORT_ERROR_MODEL_INVALID,
        "session_model_metadata");
    goto cleanup;
  }
  if (!dort_json_appendf(
          &builder,
          ",\"version\":%" PRId64 ",\"customMetadata\":{",
          version)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_model_metadata",
        "Could not allocate bounded model metadata JSON.");
    goto cleanup;
  }

  ort_status = api->ModelMetadataGetCustomMetadataMapKeys(
      metadata, allocator, &keys, &key_count_signed);
  if (key_count_signed > 0 &&
      (uint64_t)key_count_signed <= (uint64_t)SIZE_MAX &&
      (uint64_t)key_count_signed <= DORT_MAX_CUSTOM_METADATA_ENTRIES &&
      keys != NULL) {
    key_cleanup_count = (size_t)key_count_signed;
  }
  if (ort_status != NULL) {
    status = dort_status_from_ort(
        session->runtime,
        ort_status,
        DORT_ERROR_MODEL_INVALID,
        "session_model_metadata");
    goto cleanup;
  }
  if (key_count_signed < 0 ||
      (uint64_t)key_count_signed > (uint64_t)SIZE_MAX) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_MODEL_INVALID,
        0,
        "session_model_metadata",
        "ONNX Runtime returned an unrepresentable custom metadata entry count.");
    goto cleanup;
  }
  key_count = (size_t)key_count_signed;
  if (key_count > DORT_MAX_CUSTOM_METADATA_ENTRIES) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "session_model_metadata",
        "The custom metadata entry count exceeds the ABI limit.");
    goto cleanup;
  }
  if ((key_count == 0u && keys != NULL) ||
      (key_count > 0u && keys == NULL)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_MODEL_INVALID,
        0,
        "session_model_metadata",
        "ONNX Runtime returned an inconsistent custom metadata key array.");
    goto cleanup;
  }
  for (index = 0u; index < key_count; ++index) {
    size_t key_length = 0u;
    int validation = keys[index] == NULL
                         ? DORT_ERROR_INVALID_ARGUMENT
                         : dort_bounded_utf8_length(
                               keys[index],
                               DORT_MAX_KEY_BYTES,
                               0,
                               &key_length);
    if (validation != DORT_ERROR_NONE) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          validation == DORT_ERROR_INVALID_ARGUMENT
              ? DORT_ERROR_MODEL_INVALID
              : validation,
          0,
          "session_model_metadata",
          "A custom metadata key is null, invalid UTF-8, or oversized.");
      goto cleanup;
    }
  }
  if (key_count > 1u) {
    qsort(
        keys,
        key_count,
        sizeof(*keys),
        dort_compare_c_string_pointers);
  }
  for (index = 1u; index < key_count; ++index) {
    if (strcmp(keys[index - 1u], keys[index]) == 0) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_model_metadata",
          "ONNX Runtime returned duplicate custom metadata keys.");
      goto cleanup;
    }
  }
  for (index = 0u; index < key_count; ++index) {
    size_t key_length = 0u;
    value = NULL;
    (void)dort_bounded_utf8_length(
        keys[index], DORT_MAX_KEY_BYTES, 0, &key_length);
    ort_status = api->ModelMetadataLookupCustomMetadataMap(
        metadata, allocator, keys[index], &value);
    if (ort_status != NULL) {
      status = dort_status_from_ort(
          session->runtime,
          ort_status,
          DORT_ERROR_MODEL_INVALID,
          "session_model_metadata");
      goto cleanup;
    }
    if (!dort_json_append(&builder, index == 0u ? "" : ",") ||
        !dort_json_append_string(&builder, keys[index], key_length)) {
      dort_allocator_free_ignored(api, allocator, value);
      value = NULL;
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_model_metadata",
          "Could not allocate bounded model metadata JSON.");
      goto cleanup;
    }
    status = dort_append_owned_model_string(
        session,
        allocator,
        &builder,
        ":",
        &value,
        DORT_MAX_CUSTOM_METADATA_VALUE_BYTES,
        "custom-metadata value");
    if (status != NULL) {
      goto cleanup;
    }
  }
  if (!dort_json_append(&builder, "}}")) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_model_metadata",
        "Could not allocate bounded model metadata JSON.");
    goto cleanup;
  }
  session->model_metadata_json = builder.data;
  builder.data = NULL;

cleanup:
  dort_allocator_free_ignored(api, allocator, value);
  dort_free_model_metadata_keys(api, allocator, keys, key_cleanup_count);
  if (metadata != NULL) {
    api->ReleaseModelMetadata(metadata);
  }
  free(builder.data);
  return status;
}

static dort_status_t* dort_initialize_session_metadata(dort_session_t* session) {
  const OrtApi* api = dort_runtime_api(session->runtime);
  dort_json_builder_t type_builder;
  dort_json_builder_t legacy_builder;
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
  size_t input_count = 0u;
  size_t output_count = 0u;
  size_t nodes = 0u;
  int all_tensors = 1;
  memset(&type_builder, 0, sizeof(type_builder));
  memset(&legacy_builder, 0, sizeof(legacy_builder));
  ort_status = api->SessionGetInputCount(session->session, &input_count);
  if (ort_status == NULL) {
    ort_status = api->SessionGetOutputCount(session->session, &output_count);
  }
  if (ort_status != NULL) {
    return dort_status_from_ort(
        session->runtime,
        ort_status,
        DORT_ERROR_MODEL_INVALID,
        "session_metadata");
  }
  if (input_count > DORT_MAX_IO_COUNT || output_count > DORT_MAX_IO_COUNT) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "session_metadata",
        "The model input or output count exceeds the ABI limit.");
  }
  session->input_count = input_count;
  session->output_count = output_count;
  if (session->input_count > 0u) {
    session->input_names =
        (char**)dort_memory_allocate_zeroed(
            session->input_count, sizeof(*session->input_names));
    session->input_kinds =
        (uint32_t*)dort_memory_allocate_zeroed(
            session->input_count, sizeof(*session->input_kinds));
  }
  if (session->output_count > 0u) {
    session->output_names =
        (char**)dort_memory_allocate_zeroed(
            session->output_count, sizeof(*session->output_names));
    session->output_kinds =
        (uint32_t*)dort_memory_allocate_zeroed(
            session->output_count, sizeof(*session->output_kinds));
  }
  if ((session->input_count > 0u &&
       (session->input_names == NULL || session->input_kinds == NULL)) ||
      (session->output_count > 0u &&
       (session->output_names == NULL || session->output_kinds == NULL))) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_metadata",
        "Could not allocate bounded model I/O metadata.");
  }
  if (!dort_json_append(
          &type_builder, "{\"schemaVersion\":1,\"inputs\":[")) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_type_metadata",
        "Could not allocate recursive session type metadata JSON.");
  }
  status = dort_append_recursive_io_metadata(
      session, 1, &type_builder, &all_tensors, &nodes);
  if (status == NULL &&
      dort_json_append(&type_builder, "],\"outputs\":[")) {
    status = dort_append_recursive_io_metadata(
        session, 0, &type_builder, &all_tensors, &nodes);
  } else if (status == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_type_metadata",
        "Could not allocate recursive session type metadata JSON.");
  }
  if (status == NULL && !dort_json_append(&type_builder, "]}")) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_type_metadata",
        "Could not allocate recursive session type metadata JSON.");
  }
  if (status != NULL) {
    free(type_builder.data);
    return status;
  }
  session->type_metadata_json = type_builder.data;

  if (all_tensors) {
    if (!dort_json_append(
            &legacy_builder, "{\"schemaVersion\":2,\"inputs\":[")) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_metadata",
          "Could not allocate legacy tensor metadata JSON.");
    }
    status = dort_append_io_metadata(session, 1, &legacy_builder);
    if (status == NULL &&
        dort_json_append(&legacy_builder, "],\"outputs\":[")) {
      status = dort_append_io_metadata(session, 0, &legacy_builder);
    } else if (status == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_metadata",
          "Could not allocate legacy tensor metadata JSON.");
    }
    if (status == NULL && !dort_json_append(&legacy_builder, "]}")) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_metadata",
          "Could not allocate legacy tensor metadata JSON.");
    }
    if (status != NULL) {
      free(legacy_builder.data);
      return status;
    }
    session->metadata_json = legacy_builder.data;
  }
  return dort_initialize_model_metadata(session);
}

static dort_status_t* dort_wrap_session(
    dort_runtime_t* runtime,
    OrtSession* ort_session,
    dort_session_t** out_session) {
  dort_session_t* session = NULL;
  dort_status_t* status = NULL;
  session = (dort_session_t*)dort_memory_allocate_zeroed(1u, sizeof(*session));
  if (session == NULL) {
    dort_runtime_api(runtime)->ReleaseSession(ort_session);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_create",
        "Could not allocate the session handle.");
  }
  dort_runtime_retain(runtime);
  session->magic = DORT_SESSION_MAGIC;
  atomic_init(&session->reference_count, 1u);
  session->runtime = runtime;
  session->session = ort_session;
#if defined(_WIN32)
  InitializeSRWLock(&session->run_lock);
#else
  if (pthread_mutex_init(&session->run_lock, NULL) != 0) {
    session->magic = 0u;
    dort_runtime_api(runtime)->ReleaseSession(ort_session);
    free(session);
    dort_runtime_release(runtime);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_PLATFORM,
        0,
        "session_create",
        "Could not initialize the session run lock.");
  }
#endif
  status = dort_initialize_session_metadata(session);
  if (status != NULL) {
#if !defined(_WIN32)
    (void)pthread_mutex_destroy(&session->run_lock);
#endif
    dort_free_names(session->input_names, session->input_count);
    dort_free_names(session->output_names, session->output_count);
    free(session->input_kinds);
    free(session->output_kinds);
    free(session->metadata_json);
    free(session->type_metadata_json);
    free(session->model_metadata_json);
    dort_runtime_api(runtime)->ReleaseSession(ort_session);
    session->magic = 0u;
    free(session);
    dort_runtime_release(runtime);
    return status;
  }
  *out_session = session;
  return NULL;
}

dort_status_t* DORT_CALL dort_session_create_from_bytes(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const void* model_data,
    size_t model_data_length,
    dort_session_t** out_session) {
  OrtSession* ort_session = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  if (out_session == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_create",
        "The output session pointer is null.");
  }
  *out_session = NULL;
  if (!dort_runtime_is_valid(runtime) ||
      !dort_session_options_is_valid(options)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_create",
        "The runtime or session-options handle is invalid.");
  }
  if (!dort_runtime_has_same_identity(runtime, options->runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_RUNTIME_IDENTITY_MISMATCH,
        0,
        "session_create",
        "The session options belong to a different runtime identity.");
  }
  status = dort_runtime_require_api(
      runtime,
      DORT_API_FEATURE_SESSION | DORT_API_FEATURE_RECURSIVE_METADATA |
          DORT_API_FEATURE_MODEL_METADATA,
      "session_create");
  if (status != NULL) {
    return status;
  }
  if (model_data == NULL || model_data_length == 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_MODEL_INVALID,
        0,
        "session_create",
        "A session requires non-empty model bytes.");
  }
  if (model_data_length > options->max_model_bytes) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "session_create",
        "The model bytes exceed the configured bounded limit.");
  }
  ort_status = dort_runtime_api(runtime)->CreateSessionFromArray(
      dort_runtime_env(runtime),
      model_data,
      model_data_length,
      options->options,
      &ort_session);
  if (ort_status != NULL || ort_session == NULL) {
    if (ort_session != NULL) {
      dort_runtime_api(runtime)->ReleaseSession(ort_session);
    }
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_MODEL_INVALID,
                     0,
                     "session_create",
                     "ONNX Runtime did not create a session from the model bytes.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_MODEL_INVALID,
                     "session_create");
  }
  return dort_wrap_session(runtime, ort_session, out_session);
}

static int dort_external_name_is_safe(
    const uint8_t* name,
    size_t length) {
  size_t component_start = 0u;
  size_t index = 0u;
  if (length == 0u || length > DORT_MAX_EXTERNAL_DATA_NAME_BYTES ||
      dort_validate_utf8_bytes(name, length, 1) != DORT_ERROR_NONE ||
      name[0] == (uint8_t)'/' || name[0] == (uint8_t)'\\') {
    return 0;
  }
  for (index = 0u; index <= length; ++index) {
    if (index < length && name[index] != (uint8_t)'/') {
      if (name[index] < 0x20u || name[index] == 0x7fu ||
          name[index] == (uint8_t)'\\' || name[index] == (uint8_t)':') {
        return 0;
      }
      continue;
    }
    {
      size_t component_length = index - component_start;
      if (component_length == 0u ||
          (component_length == 1u &&
           name[component_start] == (uint8_t)'.') ||
          (component_length == 2u &&
           name[component_start] == (uint8_t)'.' &&
           name[component_start + 1u] == (uint8_t)'.')) {
        return 0;
      }
    }
    component_start = index + 1u;
  }
  return 1;
}

dort_status_t* DORT_CALL dort_session_create_from_bytes_with_external_data(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const void* model_data,
    size_t model_data_length,
    const dort_external_data_t* external_data,
    size_t external_data_count,
    dort_session_t** out_session) {
  const OrtApi* api = NULL;
  OrtSessionOptions* cloned_options = NULL;
  OrtSession* ort_session = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  char** names = NULL;
  char** buffers = NULL;
  size_t* lengths = NULL;
  size_t* name_lengths = NULL;
  size_t total_length = model_data_length;
  size_t index = 0u;
#if defined(_WIN32)
  wchar_t** native_names = NULL;
#endif

  if (out_session == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_create_external_data",
        "The output session pointer is null.");
  }
  *out_session = NULL;
  if (!dort_runtime_is_valid(runtime) ||
      !dort_session_options_is_valid(options)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_create_external_data",
        "The runtime or session-options handle is invalid.");
  }
  if (!dort_runtime_has_same_identity(runtime, options->runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_RUNTIME_IDENTITY_MISMATCH,
        0,
        "session_create_external_data",
        "The session options belong to a different runtime identity.");
  }
  if (external_data_count > DORT_MAX_EXTERNAL_DATA_FILES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "session_create_external_data",
        "The external-data file count exceeds the ABI limit.");
  }
  if ((external_data_count == 0u) != (external_data == NULL)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_EXTERNAL_DATA_INVALID,
        0,
        "session_create_external_data",
        "The external-data pointer/count is inconsistent.");
  }
  if (external_data_count == 0u) {
    return dort_session_create_from_bytes(
        runtime, options, model_data, model_data_length, out_session);
  }
  status = dort_runtime_require_api(
      runtime,
      DORT_API_FEATURE_SESSION | DORT_API_FEATURE_RECURSIVE_METADATA |
          DORT_API_FEATURE_MODEL_METADATA | DORT_API_FEATURE_EXTERNAL_DATA,
      "session_create_external_data");
  if (status != NULL) {
    return status;
  }
  if (model_data == NULL || model_data_length == 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_MODEL_INVALID,
        0,
        "session_create_external_data",
        "A session requires non-empty model bytes.");
  }

  names = (char**)dort_memory_allocate_zeroed(external_data_count, sizeof(*names));
  buffers = (char**)dort_memory_allocate_zeroed(external_data_count, sizeof(*buffers));
  lengths = (size_t*)dort_memory_allocate_zeroed(external_data_count, sizeof(*lengths));
  name_lengths =
      (size_t*)dort_memory_allocate_zeroed(external_data_count, sizeof(*name_lengths));
#if defined(_WIN32)
  native_names =
      (wchar_t**)dort_memory_allocate_zeroed(external_data_count, sizeof(*native_names));
#endif
  if (names == NULL || buffers == NULL || lengths == NULL ||
      name_lengths == NULL
#if defined(_WIN32)
      || native_names == NULL
#endif
  ) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "session_create_external_data",
        "Could not allocate bounded external-data staging arrays.");
    goto cleanup;
  }
  for (index = 0u; index < external_data_count; ++index) {
    const dort_external_data_t* entry = &external_data[index];
    size_t prior = 0u;
    if (entry->struct_size < DORT_EXTERNAL_DATA_V1_SIZE ||
        entry->reserved0 != 0u ||
        !dort_external_name_is_safe(
            entry->relative_name_utf8, entry->relative_name_length) ||
        (entry->data_length > 0u && entry->data == NULL)) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_EXTERNAL_DATA_INVALID,
          0,
          "session_create_external_data",
          "An external-data entry has an invalid ABI field, unsafe relative name, or null byte buffer.");
      goto cleanup;
    }
    for (prior = 0u; prior < index; ++prior) {
      if (name_lengths[prior] == entry->relative_name_length &&
          memcmp(
              names[prior],
              entry->relative_name_utf8,
              entry->relative_name_length) == 0) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_EXTERNAL_DATA_INVALID,
            0,
            "session_create_external_data",
            "The external-data array contains duplicate relative names.");
        goto cleanup;
      }
    }
    if (!dort_checked_add_size(
            total_length, entry->data_length, &total_length) ||
        total_length > options->max_model_bytes) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          "session_create_external_data",
          "The model and external-data bytes exceed the configured bounded limit.");
      goto cleanup;
    }
    names[index] =
        (char*)dort_memory_allocate(entry->relative_name_length + 1u);
    buffers[index] =
        (char*)dort_memory_allocate(entry->data_length == 0u ? 1u : entry->data_length);
    if (names[index] == NULL || buffers[index] == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_create_external_data",
          "Could not copy bounded external-data names and bytes.");
      goto cleanup;
    }
    memcpy(
        names[index],
        entry->relative_name_utf8,
        entry->relative_name_length);
    names[index][entry->relative_name_length] = '\0';
    if (entry->data_length > 0u) {
      memcpy(buffers[index], entry->data, entry->data_length);
    } else {
      buffers[index][0] = '\0';
    }
    lengths[index] = entry->data_length;
    name_lengths[index] = entry->relative_name_length;
#if defined(_WIN32)
    native_names[index] = dort_utf8_to_wide_path(names[index]);
    if (native_names[index] == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_EXTERNAL_DATA_INVALID,
          0,
          "session_create_external_data",
          "An external-data relative name could not be converted to ORTCHAR_T.");
      goto cleanup;
    }
#endif
  }

  api = dort_runtime_api(runtime);
  ort_status = api->CloneSessionOptions(options->options, &cloned_options);
  if (ort_status == NULL && cloned_options == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_EXTERNAL_DATA_INVALID,
        0,
        "session_create_external_data",
        "ONNX Runtime returned null cloned session options.");
    goto cleanup;
  }
  if (ort_status != NULL) {
    status = dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_EXTERNAL_DATA_INVALID,
        "session_create_external_data");
    goto cleanup;
  }
  ort_status = api->AddExternalInitializersFromFilesInMemory(
      cloned_options,
#if defined(_WIN32)
      (const ORTCHAR_T* const*)native_names,
#else
      (const ORTCHAR_T* const*)names,
#endif
      buffers,
      lengths,
      external_data_count);
  if (ort_status != NULL) {
    status = dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_EXTERNAL_DATA_INVALID,
        "session_create_external_data");
    goto cleanup;
  }
  ort_status = api->CreateSessionFromArray(
      dort_runtime_env(runtime),
      model_data,
      model_data_length,
      cloned_options,
      &ort_session);
  if (ort_status != NULL || ort_session == NULL) {
    if (ort_session != NULL) {
      api->ReleaseSession(ort_session);
      ort_session = NULL;
    }
    status = ort_status == NULL
                 ? dort_status_create(
                       DORT_ERROR_DOMAIN_ORT_API,
                       DORT_ERROR_EXTERNAL_DATA_INVALID,
                       0,
                       "session_create_external_data",
                       "ONNX Runtime did not create a session from the model and enumerated external data.")
                 : dort_status_from_ort(
                       runtime,
                       ort_status,
                       DORT_ERROR_EXTERNAL_DATA_INVALID,
                       "session_create_external_data");
    goto cleanup;
  }
  status = dort_wrap_session(runtime, ort_session, out_session);
  ort_session = NULL;

cleanup:
  if (ort_session != NULL && api != NULL) {
    api->ReleaseSession(ort_session);
  }
  if (cloned_options != NULL && api != NULL) {
    api->ReleaseSessionOptions(cloned_options);
  }
  for (index = 0u; index < external_data_count; ++index) {
    free(names == NULL ? NULL : names[index]);
    free(buffers == NULL ? NULL : buffers[index]);
#if defined(_WIN32)
    free(native_names == NULL ? NULL : native_names[index]);
#endif
  }
  free(names);
  free(buffers);
  free(lengths);
  free(name_lengths);
#if defined(_WIN32)
  free(native_names);
#endif
  return status;
}

static dort_status_t* dort_read_model_file(
    const char* path,
    const char* allowed_root,
    size_t maximum,
    uint8_t** out_bytes,
    size_t* out_length) {
  size_t ignored = 0u;
  *out_bytes = NULL;
  *out_length = 0u;
  if (dort_bounded_utf8_length(path, DORT_MAX_PATH_BYTES, 0, &ignored) !=
          DORT_ERROR_NONE ||
      dort_bounded_utf8_length(
          allowed_root, DORT_MAX_PATH_BYTES, 0, &ignored) != DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_UTF8,
        0,
        "session_create_file",
        "The model path or allowed root is invalid UTF-8 or exceeds its ABI limit.");
  }
#if defined(_WIN32)
  {
    wchar_t* wide_path = dort_utf8_to_wide_path(path);
    wchar_t* wide_root = dort_utf8_to_wide_path(allowed_root);
    wchar_t* full_path = NULL;
    wchar_t* full_root = NULL;
    wchar_t* canonical_root = NULL;
    wchar_t* canonical_component = NULL;
    wchar_t* traversal = NULL;
    const wchar_t* relative = NULL;
    const wchar_t* cursor = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    HANDLE root_handle = INVALID_HANDLE_VALUE;
    HANDLE component_handle = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION root_information;
    BY_HANDLE_FILE_INFORMATION component_information;
    LARGE_INTEGER file_size;
    uint8_t* bytes = NULL;
    size_t offset = 0u;
    dort_status_t* status = NULL;
    if (wide_path == NULL || wide_root == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "session_create_file",
          "The model path or allowed root could not be converted.");
      goto windows_model_cleanup;
    }
    if (!dort_windows_path_is_absolute(wide_path) ||
        !dort_windows_path_is_absolute(wide_root)) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_NOT_ABSOLUTE,
          0,
          "session_create_file",
          "The model path and allowed root must be absolute filesystem paths.");
      goto windows_model_cleanup;
    }
    full_path = dort_windows_full_path(wide_path);
    full_root = dort_windows_full_path(wide_root);
    if (full_path == NULL || full_root == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_create_file",
          "The model path or allowed root could not be normalized as a bounded Windows path.");
      goto windows_model_cleanup;
    }
    root_handle = CreateFileW(
        full_root,
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        NULL);
    if (root_handle == INVALID_HANDLE_VALUE ||
        !GetFileInformationByHandle(root_handle, &root_information) ||
        (root_information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0u ||
        (root_information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0u) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_create_file",
          "The allowed model root must be an existing non-reparse directory.");
      goto windows_model_cleanup;
    }
    canonical_root = dort_windows_final_path(root_handle);
    if (canonical_root == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PLATFORM,
          0,
          "session_create_file",
          "The allowed model root could not be canonicalized.");
      goto windows_model_cleanup;
    }
    relative = dort_windows_strict_relative_path(full_path, full_root);
    if (relative == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
          0,
          "session_create_file",
          "The model path must identify a strict child of its explicit allowed root.");
      goto windows_model_cleanup;
    }
    traversal =
        (wchar_t*)dort_memory_allocate_zeroed(DORT_MAX_PATH_BYTES, sizeof(wchar_t));
    if (traversal != NULL) {
      memcpy(
          traversal,
          full_root,
          (wcslen(full_root) + 1u) * sizeof(wchar_t));
    }
    if (traversal == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_create_file",
          "Could not allocate bounded model traversal state.");
      goto windows_model_cleanup;
    }
    cursor = relative;
    while (*cursor != L'\0') {
      const wchar_t* separator = wcschr(cursor, L'\\');
      const wchar_t* alternative_separator = wcschr(cursor, L'/');
      size_t component_length = 0u;
      size_t traversal_length = wcslen(traversal);
      size_t needs_separator =
          dort_windows_is_path_separator(traversal[traversal_length - 1u])
              ? 0u
              : 1u;
      int is_final = 0;
      if (alternative_separator != NULL &&
          (separator == NULL || alternative_separator < separator)) {
        separator = alternative_separator;
      }
      component_length =
          separator == NULL ? wcslen(cursor) : (size_t)(separator - cursor);
      is_final = separator == NULL;
      if (component_length == 0u || component_length >= 256u ||
          (component_length == 1u && cursor[0] == L'.') ||
          (component_length == 2u && cursor[0] == L'.' &&
           cursor[1] == L'.') ||
          traversal_length + needs_separator + component_length + 1u >
              DORT_MAX_PATH_BYTES) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "The model path contains an unsafe or oversized component.");
        goto windows_model_cleanup;
      }
      if (needs_separator != 0u) {
        traversal[traversal_length] = L'\\';
        ++traversal_length;
      }
      memcpy(
          traversal + traversal_length,
          cursor,
          component_length * sizeof(wchar_t));
      traversal[traversal_length + component_length] = L'\0';

      component_handle = CreateFileW(
          traversal,
          is_final ? GENERIC_READ : FILE_READ_ATTRIBUTES,
          is_final ? FILE_SHARE_READ : (FILE_SHARE_READ | FILE_SHARE_WRITE),
          NULL,
          OPEN_EXISTING,
          FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS |
              (is_final ? FILE_FLAG_SEQUENTIAL_SCAN : 0u),
          NULL);
      if (component_handle == INVALID_HANDLE_VALUE ||
          !GetFileInformationByHandle(
              component_handle, &component_information)) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "The model path could not be traversed below its allowed root.");
        goto windows_model_cleanup;
      }
      if ((component_information.dwFileAttributes &
           FILE_ATTRIBUTE_REPARSE_POINT) != 0u) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "The model path contains a reparse-point component.");
        goto windows_model_cleanup;
      }
      if (!is_final &&
          (component_information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ==
              0u) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "A model parent component is not a directory.");
        goto windows_model_cleanup;
      }
      if (is_final) {
        if ((component_information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) !=
                0u ||
            GetFileType(component_handle) != FILE_TYPE_DISK) {
          status = dort_status_create(
              DORT_ERROR_DOMAIN_SHIM,
              DORT_ERROR_MODEL_INVALID,
              0,
              "session_create_file",
              "The model path does not identify a regular disk file.");
          goto windows_model_cleanup;
        }
      }
      canonical_component = dort_windows_final_path(component_handle);
      if (canonical_component == NULL ||
          !dort_windows_path_is_within(
              canonical_component, canonical_root, 1)) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
            0,
            "session_create_file",
            "The model path resolved outside its canonical allowed root.");
        goto windows_model_cleanup;
      }
      if (is_final) {
        file = component_handle;
        component_handle = INVALID_HANDLE_VALUE;
      } else {
        CloseHandle(component_handle);
        component_handle = INVALID_HANDLE_VALUE;
      }
      free(canonical_component);
      canonical_component = NULL;
      if (is_final) {
        break;
      }
      cursor = separator + 1u;
    }
    if (file == INVALID_HANDLE_VALUE || !GetFileSizeEx(file, &file_size) ||
        file_size.QuadPart <= 0 ||
        (uint64_t)file_size.QuadPart > (uint64_t)maximum) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_create_file",
          "The model file is empty, oversized, or could not be measured.");
      goto windows_model_cleanup;
    }
    bytes = (uint8_t*)dort_memory_allocate((size_t)file_size.QuadPart);
    if (bytes == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_create_file",
          "Could not allocate the bounded model byte buffer.");
      goto windows_model_cleanup;
    }
    while (offset < (size_t)file_size.QuadPart) {
      DWORD chunk = (DWORD)(((size_t)file_size.QuadPart - offset) > UINT32_MAX
                                ? UINT32_MAX
                                : ((size_t)file_size.QuadPart - offset));
      DWORD read_count = 0u;
      if (!ReadFile(file, bytes + offset, chunk, &read_count, NULL) ||
          read_count == 0u) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "The bounded model file could not be read completely.");
        goto windows_model_cleanup;
      }
      offset += (size_t)read_count;
    }
    *out_bytes = bytes;
    *out_length = (size_t)file_size.QuadPart;
    bytes = NULL;

windows_model_cleanup:
    if (component_handle != INVALID_HANDLE_VALUE) {
      CloseHandle(component_handle);
    }
    if (file != INVALID_HANDLE_VALUE) {
      CloseHandle(file);
    }
    if (root_handle != INVALID_HANDLE_VALUE) {
      CloseHandle(root_handle);
    }
    free(bytes);
    free(traversal);
    free(canonical_component);
    free(canonical_root);
    free(full_path);
    free(full_root);
    free(wide_path);
    free(wide_root);
    return status;
  }
#else
  {
    struct stat file_stat;
    struct stat root_stat;
    int current_descriptor = -1;
    int descriptor = -1;
    uint8_t* bytes = NULL;
    size_t offset = 0u;
    size_t root_length = strlen(allowed_root);
    const char* relative = NULL;
    const char* cursor = NULL;
    if (path[0] != '/' || allowed_root[0] != '/') {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_NOT_ABSOLUTE,
          0,
          "session_create_file",
          "The model path and allowed root must be absolute.");
    }
    while (root_length > 1u && allowed_root[root_length - 1u] == '/') {
      --root_length;
    }
    if ((root_length == 1u && allowed_root[0] == '/') ? path[0] != '/'
        : (strncmp(path, allowed_root, root_length) != 0 ||
           path[root_length] != '/')) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
          0,
          "session_create_file",
          "The model path is outside its explicit allowed root.");
    }
    relative = root_length == 1u ? path + 1 : path + root_length + 1u;
    if (*relative == '\0') {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_create_file",
          "The model path does not identify a file below its allowed root.");
    }
    current_descriptor = open(
        allowed_root, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (current_descriptor < 0 || fstat(current_descriptor, &root_stat) != 0 ||
        !S_ISDIR(root_stat.st_mode)) {
      if (current_descriptor >= 0) {
        (void)close(current_descriptor);
      }
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_create_file",
          "The allowed model root must be an existing non-symlink directory.");
    }
    cursor = relative;
    while (*cursor != '\0') {
      const char* separator = strchr(cursor, '/');
      size_t component_length =
          separator == NULL ? strlen(cursor) : (size_t)(separator - cursor);
      char component[256];
      int next_descriptor = -1;
      int is_final = separator == NULL;
      if (component_length == 0u || component_length >= sizeof(component) ||
          (component_length == 1u && cursor[0] == '.') ||
          (component_length == 2u && cursor[0] == '.' && cursor[1] == '.')) {
        (void)close(current_descriptor);
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "The model path contains an unsafe or oversized component.");
      }
      memcpy(component, cursor, component_length);
      component[component_length] = '\0';
      next_descriptor = openat(
          current_descriptor,
          component,
          O_RDONLY | O_CLOEXEC | O_NOFOLLOW |
              (is_final ? 0 : O_DIRECTORY));
      (void)close(current_descriptor);
      if (next_descriptor < 0) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "The model path could not be traversed below its allowed root without symlinks.");
      }
      current_descriptor = next_descriptor;
      if (is_final) {
        descriptor = current_descriptor;
        break;
      }
      cursor = separator + 1;
    }
    if (descriptor < 0 || fstat(descriptor, &file_stat) != 0 ||
        !S_ISREG(file_stat.st_mode) || file_stat.st_size <= 0 ||
        (uint64_t)file_stat.st_size > (uint64_t)maximum) {
      if (descriptor >= 0) {
        (void)close(descriptor);
      } else if (current_descriptor >= 0) {
        (void)close(current_descriptor);
      }
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_MODEL_INVALID,
          0,
          "session_create_file",
          "The model file is not a bounded non-empty regular file.");
    }
    bytes = (uint8_t*)dort_memory_allocate((size_t)file_stat.st_size);
    if (bytes == NULL) {
      (void)close(descriptor);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "session_create_file",
          "Could not allocate the bounded model byte buffer.");
    }
    while (offset < (size_t)file_stat.st_size) {
      ssize_t read_count = read(
          descriptor, bytes + offset, (size_t)file_stat.st_size - offset);
      if (read_count < 0 && errno == EINTR) {
        continue;
      }
      if (read_count <= 0) {
        free(bytes);
        (void)close(descriptor);
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_MODEL_INVALID,
            0,
            "session_create_file",
            "The bounded model file could not be read completely.");
      }
      offset += (size_t)read_count;
    }
    if (close(descriptor) != 0) {
      free(bytes);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PLATFORM,
          0,
          "session_create_file",
          "The model file could not be closed after its bounded read.");
    }
    *out_bytes = bytes;
    *out_length = (size_t)file_stat.st_size;
    return NULL;
  }
#endif
}

#if defined(_WIN32) && defined(FONIX_WINDOWS_PATH_TESTING)
dort_status_t* dort_windows_test_read_model_file(
    const char* path,
    const char* allowed_root,
    size_t maximum,
    uint8_t** out_bytes,
    size_t* out_length) {
  return dort_read_model_file(
      path, allowed_root, maximum, out_bytes, out_length);
}
#endif

dort_status_t* DORT_CALL dort_session_create_from_file(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* model_path_utf8,
    const char* allowed_root_utf8,
    dort_session_t** out_session) {
  uint8_t* model_bytes = NULL;
  size_t model_length = 0u;
  dort_status_t* status = NULL;
  if (out_session == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_create_file",
        "The output session pointer is null.");
  }
  *out_session = NULL;
  if (!dort_runtime_is_valid(runtime) ||
      !dort_session_options_is_valid(options)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_create_file",
        "The runtime or session-options handle is invalid.");
  }
  if (!dort_runtime_has_same_identity(runtime, options->runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_RUNTIME_IDENTITY_MISMATCH,
        0,
        "session_create_file",
        "The session options belong to a different runtime identity.");
  }
  status = dort_read_model_file(
      model_path_utf8,
      allowed_root_utf8,
      options->max_model_bytes,
      &model_bytes,
      &model_length);
  if (status != NULL) {
    return status;
  }
  status = dort_session_create_from_bytes(
      runtime, options, model_bytes, model_length, out_session);
  free(model_bytes);
  return status;
}

void DORT_CALL dort_session_retain(dort_session_t* session) {
  unsigned int references = 0u;
  if (!dort_session_internal_is_valid(session)) {
    return;
  }
  references = atomic_load_explicit(
      &session->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &session->reference_count,
            &references,
            references + 1u,
            memory_order_relaxed,
            memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_session_release(dort_session_t* session) {
  unsigned int previous = 0u;
  if (!dort_session_internal_is_valid(session)) {
    return;
  }
  previous = atomic_fetch_sub_explicit(
      &session->reference_count, 1u, memory_order_acq_rel);
  if (previous == 1u) {
    dort_runtime_t* runtime = session->runtime;
    OrtSession* ort_session = session->session;
    session->magic = 0u;
    session->runtime = NULL;
    session->session = NULL;
    dort_runtime_api(runtime)->ReleaseSession(ort_session);
#if !defined(_WIN32)
    (void)pthread_mutex_destroy(&session->run_lock);
#endif
    dort_free_names(session->input_names, session->input_count);
    dort_free_names(session->output_names, session->output_count);
    free(session->input_kinds);
    free(session->output_kinds);
    free(session->metadata_json);
    free(session->type_metadata_json);
    free(session->model_metadata_json);
    free(session);
    dort_runtime_release(runtime);
  }
}

dort_status_t* DORT_CALL dort_session_metadata_json(
    const dort_session_t* session,
    dort_string_t* out_json) {
  if (out_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_metadata",
        "The output metadata string pointer is null.");
  }
  memset(out_json, 0, sizeof(*out_json));
  out_json->struct_size = (uint32_t)sizeof(*out_json);
  if (!dort_session_internal_is_valid(session)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_metadata",
        "The session handle is null or invalid.");
  }
  if (session->metadata_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        "session_metadata",
        "Legacy flat session metadata is unavailable for composite model I/O; use recursive type metadata.");
  }
  return dort_string_copy(session->metadata_json, out_json);
}

dort_status_t* DORT_CALL dort_session_type_metadata_json(
    const dort_session_t* session,
    dort_string_t* out_json) {
  if (out_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_type_metadata",
        "The output type-metadata string pointer is null.");
  }
  memset(out_json, 0, sizeof(*out_json));
  out_json->struct_size = (uint32_t)sizeof(*out_json);
  if (!dort_session_internal_is_valid(session) ||
      session->type_metadata_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_type_metadata",
        "The session handle is null, invalid, or has no type metadata.");
  }
  return dort_string_copy(session->type_metadata_json, out_json);
}

dort_status_t* DORT_CALL dort_session_model_metadata_json(
    const dort_session_t* session,
    dort_string_t* out_json) {
  if (out_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_model_metadata",
        "The output model-metadata string pointer is null.");
  }
  memset(out_json, 0, sizeof(*out_json));
  out_json->struct_size = (uint32_t)sizeof(*out_json);
  if (!dort_session_internal_is_valid(session) ||
      session->model_metadata_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "session_model_metadata",
        "The session handle is null, invalid, or has no model metadata.");
  }
  return dort_string_copy(session->model_metadata_json, out_json);
}
