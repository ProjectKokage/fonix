#include <onnxruntime_c_api.h>

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum {
  FAKE_SESSION_NULL_METADATA = 0,
  FAKE_SESSION_OUTPUT_COUNT_ERROR = 1,
  FAKE_SESSION_OVERSIZED_INPUT_COUNT = 2,
  FAKE_SESSION_OVERSIZED_OUTPUT_COUNT = 3,
  FAKE_SESSION_METADATA_ERROR_WITH_OWNER = 4,
  FAKE_SESSION_VERIFY_METADATA_OWNER = 5,
  FAKE_SESSION_METADATA_KEYS_ERROR_WITH_OWNERS = 6,
  FAKE_SESSION_VERIFY_METADATA_KEY_OWNERS = 7,
  FAKE_SESSION_OVERSIZED_METADATA_KEY_COUNT = 8,
};

#define FAKE_MAX_TRACKED_KEY_ALLOCATIONS 4u

typedef struct fake_session {
  uint8_t scenario;
} fake_session_t;

typedef struct fake_metadata {
  uint8_t scenario;
} fake_metadata_t;

typedef struct fake_session_options {
  uint8_t cloned;
  uint8_t external_initializers_added;
} fake_session_options_t;

typedef struct fake_value {
  uint8_t kind;
} fake_value_t;

enum {
  FAKE_VALUE_TENSOR = 1u,
  FAKE_VALUE_COMPOSITE = 2u,
};

static size_t fake_metadata_owner_count = 0u;
static void* fake_tracked_key_allocations[FAKE_MAX_TRACKED_KEY_ALLOCATIONS];
static size_t fake_key_owner_count = 0u;
#if defined(FONIX_FAKE_ALLOCATION_EXTENSIONS)
static size_t fake_cloned_options_count = 0u;
static size_t fake_composite_owner_count = 0u;
#endif

static void* fake_allocate_tracked_key(size_t size) {
  void* allocation = NULL;
  size_t index = 0u;
  allocation = calloc(1u, size);
  if (allocation == NULL) {
    return NULL;
  }
  for (index = 0u; index < FAKE_MAX_TRACKED_KEY_ALLOCATIONS; ++index) {
    if (fake_tracked_key_allocations[index] == NULL) {
      fake_tracked_key_allocations[index] = allocation;
      ++fake_key_owner_count;
      return allocation;
    }
  }
  free(allocation);
  return NULL;
}

static void fake_forget_tracked_key(void* allocation) {
  size_t index = 0u;
  if (allocation == NULL) {
    return;
  }
  for (index = 0u; index < FAKE_MAX_TRACKED_KEY_ALLOCATIONS; ++index) {
    if (fake_tracked_key_allocations[index] == allocation) {
      fake_tracked_key_allocations[index] = NULL;
      --fake_key_owner_count;
      return;
    }
  }
}

static OrtStatus* ORT_API_CALL fake_create_env(
    OrtLoggingLevel severity,
    const char* log_id,
    OrtEnv** out) NO_EXCEPTION {
  (void)severity;
  (void)log_id;
  *out = (OrtEnv*)malloc(1u);
  return NULL;
}

static void ORT_API_CALL fake_release_env(OrtEnv* value) NO_EXCEPTION {
  free(value);
}

static OrtErrorCode ORT_API_CALL fake_get_error_code(
    const OrtStatus* status) NO_EXCEPTION {
  (void)status;
  return ORT_FAIL;
}

static const char* ORT_API_CALL fake_get_error_message(
    const OrtStatus* status) NO_EXCEPTION {
  (void)status;
  return "fake null-metadata error";
}

static void ORT_API_CALL fake_release_status(OrtStatus* status) NO_EXCEPTION {
  (void)status;
}

static OrtStatus* ORT_API_CALL fake_create_session_options(
    OrtSessionOptions** out) NO_EXCEPTION {
  *out = (OrtSessionOptions*)calloc(1u, sizeof(fake_session_options_t));
  return NULL;
}

static void ORT_API_CALL fake_release_session_options(
    OrtSessionOptions* options) NO_EXCEPTION {
#if defined(FONIX_FAKE_ALLOCATION_EXTENSIONS)
  const fake_session_options_t* fake = (const fake_session_options_t*)options;
  if (fake != NULL && fake->cloned != 0u && fake_cloned_options_count > 0u) {
    --fake_cloned_options_count;
  }
#endif
  free(options);
}

#if defined(FONIX_FAKE_ALLOCATION_EXTENSIONS)
static OrtStatus* ORT_API_CALL fake_clone_session_options(
    const OrtSessionOptions* input,
    OrtSessionOptions** out) NO_EXCEPTION {
  fake_session_options_t* clone = NULL;
  *out = NULL;
  if (input == NULL || fake_cloned_options_count != 0u) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  clone = (fake_session_options_t*)calloc(1u, sizeof(*clone));
  if (clone == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  clone->cloned = 1u;
  ++fake_cloned_options_count;
  *out = (OrtSessionOptions*)clone;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_add_external_initializers(
    OrtSessionOptions* options,
    const ORTCHAR_T* const* names,
    char* const* buffers,
    const size_t* lengths,
    size_t count) NO_EXCEPTION {
  fake_session_options_t* fake = (fake_session_options_t*)options;
  if (fake == NULL || fake->cloned == 0u || names == NULL ||
      buffers == NULL || lengths == NULL || count != 2u ||
      names[0] == NULL || names[1] == NULL || buffers[0] == NULL ||
      buffers[1] == NULL || strcmp(names[0], "a.bin") != 0 ||
      strcmp(names[1], "nested/b.bin") != 0 || lengths[0] != 2u ||
      lengths[1] != 3u || memcmp(buffers[0], "ab", 2u) != 0 ||
      memcmp(buffers[1], "cde", 3u) != 0) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  fake->external_initializers_added = 1u;
  return NULL;
}
#endif

static OrtStatus* ORT_API_CALL fake_set_graph_optimization(
    OrtSessionOptions* options,
    GraphOptimizationLevel level) NO_EXCEPTION {
  (void)options;
  (void)level;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_execution_mode(
    OrtSessionOptions* options,
    ExecutionMode mode) NO_EXCEPTION {
  (void)options;
  (void)mode;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_threads(
    OrtSessionOptions* options,
    int count) NO_EXCEPTION {
  (void)options;
  (void)count;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_toggle_options(
    OrtSessionOptions* options) NO_EXCEPTION {
  (void)options;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_deterministic(
    OrtSessionOptions* options,
    bool enabled) NO_EXCEPTION {
  (void)options;
  (void)enabled;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_log_id(
    OrtSessionOptions* options,
    const char* log_id) NO_EXCEPTION {
  (void)options;
  (void)log_id;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_log_level(
    OrtSessionOptions* options,
    int level) NO_EXCEPTION {
  (void)options;
  (void)level;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_enable_profiling(
    OrtSessionOptions* options,
    const ORTCHAR_T* prefix) NO_EXCEPTION {
  (void)options;
  (void)prefix;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_optimized_path(
    OrtSessionOptions* options,
    const ORTCHAR_T* path) NO_EXCEPTION {
  (void)options;
  (void)path;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_add_config_entry(
    OrtSessionOptions* options,
    const char* key,
    const char* value) NO_EXCEPTION {
  (void)options;
  (void)key;
  (void)value;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_append_provider(
    OrtSessionOptions* options,
    const char* provider,
    const char* const* keys,
    const char* const* values,
    size_t count) NO_EXCEPTION {
  (void)options;
  (void)provider;
  (void)keys;
  (void)values;
  (void)count;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_create_session_from_array(
    const OrtEnv* environment,
    const void* model_data,
    size_t model_length,
    const OrtSessionOptions* options,
    OrtSession** out) NO_EXCEPTION {
  fake_session_t* session = NULL;
  const fake_session_options_t* fake_options =
      (const fake_session_options_t*)options;
  (void)environment;
  *out = NULL;
  if (fake_options != NULL && fake_options->cloned != 0u &&
      fake_options->external_initializers_added == 0u) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  session = (fake_session_t*)calloc(1u, sizeof(*session));
  if (session != NULL && model_data != NULL && model_length > 0u) {
    session->scenario = ((const uint8_t*)model_data)[0];
  }
  *out = (OrtSession*)session;
  return NULL;
}

static void ORT_API_CALL fake_release_session(OrtSession* session) NO_EXCEPTION {
  free(session);
}

static OrtStatus* ORT_API_CALL fake_input_count(
    const OrtSession* session,
    size_t* out) NO_EXCEPTION {
  const fake_session_t* fake = (const fake_session_t*)session;
  *out = fake->scenario == FAKE_SESSION_OVERSIZED_INPUT_COUNT ? 257u : 1u;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_output_count(
    const OrtSession* session,
    size_t* out) NO_EXCEPTION {
  const fake_session_t* fake = (const fake_session_t*)session;
  *out = fake->scenario == FAKE_SESSION_OVERSIZED_OUTPUT_COUNT ? 257u : 0u;
  if (fake->scenario == FAKE_SESSION_OUTPUT_COUNT_ERROR) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_input_name(
    const OrtSession* session,
    size_t index,
    OrtAllocator* allocator,
    char** out) NO_EXCEPTION {
  (void)session;
  (void)index;
  (void)allocator;
  *out = (char*)malloc(2u);
  if (*out != NULL) {
    memcpy(*out, "X", 2u);
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_output_name(
    const OrtSession* session,
    size_t index,
    OrtAllocator* allocator,
    char** out) NO_EXCEPTION {
  (void)session;
  (void)index;
  (void)allocator;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_input_type(
    const OrtSession* session,
    size_t index,
    OrtTypeInfo** out) NO_EXCEPTION {
  (void)session;
  (void)index;
  *out = (OrtTypeInfo*)malloc(1u);
  if (*out != NULL) {
    *(uint8_t*)*out = 0u;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_output_type(
    const OrtSession* session,
    size_t index,
    OrtTypeInfo** out) NO_EXCEPTION {
  (void)session;
  (void)index;
  *out = NULL;
  return NULL;
}

static void ORT_API_CALL fake_release_type_info(OrtTypeInfo* info) NO_EXCEPTION {
  free(info);
}

static OrtStatus* ORT_API_CALL fake_get_default_allocator(
    OrtAllocator** out) NO_EXCEPTION {
  *out = (OrtAllocator*)(uintptr_t)1u;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_allocator_free(
    OrtAllocator* allocator,
    void* allocation) NO_EXCEPTION {
  (void)allocator;
  fake_forget_tracked_key(allocation);
  free(allocation);
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_create_cpu_memory_info(
    OrtAllocatorType allocator_type,
    OrtMemType memory_type,
    OrtMemoryInfo** out) NO_EXCEPTION {
  (void)allocator_type;
  (void)memory_type;
  *out = (OrtMemoryInfo*)malloc(1u);
  return NULL;
}

static void ORT_API_CALL fake_release_memory_info(
    OrtMemoryInfo* info) NO_EXCEPTION {
  free(info);
}

static OrtStatus* ORT_API_CALL fake_create_tensor_with_data(
    const OrtMemoryInfo* info,
    void* data,
    size_t data_length,
    const int64_t* shape,
    size_t shape_length,
    ONNXTensorElementDataType element_type,
    OrtValue** out) NO_EXCEPTION {
  (void)info;
  (void)data;
  (void)data_length;
  (void)shape;
  (void)shape_length;
  (void)element_type;
  {
    fake_value_t* value = (fake_value_t*)calloc(1u, sizeof(*value));
    if (value != NULL) {
      value->kind = (uint8_t)FAKE_VALUE_TENSOR;
    }
    *out = (OrtValue*)value;
  }
  return NULL;
}

static void ORT_API_CALL fake_release_value(OrtValue* value) NO_EXCEPTION {
#if defined(FONIX_FAKE_ALLOCATION_EXTENSIONS)
  const fake_value_t* fake = (const fake_value_t*)value;
  if (fake != NULL && fake->kind == FAKE_VALUE_COMPOSITE &&
      fake_composite_owner_count > 0u) {
    --fake_composite_owner_count;
  }
#endif
  free(value);
}

static OrtStatus* ORT_API_CALL fake_has_value(
    const OrtValue* value,
    int* out) NO_EXCEPTION {
  (void)value;
  *out = 1;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_get_value_type(
    const OrtValue* value,
    ONNXType* out) NO_EXCEPTION {
  (void)value;
  *out = ONNX_TYPE_TENSOR;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_get_value_count(
    const OrtValue* value,
    size_t* out) NO_EXCEPTION {
  (void)value;
  *out = 0u;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_get_value(
    const OrtValue* value,
    int index,
    OrtAllocator* allocator,
    OrtValue** out) NO_EXCEPTION {
  (void)value;
  (void)index;
  (void)allocator;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_create_value(
    const OrtValue* const* values,
    size_t value_count,
    ONNXType value_type,
    OrtValue** out) NO_EXCEPTION {
  (void)values;
  (void)value_count;
  (void)value_type;
#if defined(FONIX_FAKE_ALLOCATION_EXTENSIONS)
  *out = NULL;
  if (values == NULL || value_count != 2u ||
      value_type != ONNX_TYPE_SEQUENCE || fake_composite_owner_count != 0u) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  {
    fake_value_t* value = (fake_value_t*)calloc(1u, sizeof(*value));
    if (value == NULL) {
      return (OrtStatus*)(uintptr_t)1u;
    }
    value->kind = (uint8_t)FAKE_VALUE_COMPOSITE;
    ++fake_composite_owner_count;
    *out = (OrtValue*)value;
  }
#else
  *out = NULL;
#endif
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_get_tensor_type_and_shape(
    const OrtValue* value,
    OrtTensorTypeAndShapeInfo** out) NO_EXCEPTION {
  (void)value;
  *out = (OrtTensorTypeAndShapeInfo*)malloc(1u);
  if (*out != NULL) {
    *(uint8_t*)*out = 1u;
  }
  return NULL;
}

static void ORT_API_CALL fake_release_tensor_type_and_shape(
    OrtTensorTypeAndShapeInfo* info) NO_EXCEPTION {
  free(info);
}

static OrtStatus* ORT_API_CALL fake_get_tensor_size(
    const OrtValue* value,
    size_t* out) NO_EXCEPTION {
  (void)value;
  *out = sizeof(float);
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_get_tensor_memory_info(
    const OrtValue* value,
    const OrtMemoryInfo** out) NO_EXCEPTION {
  (void)value;
  *out = (const OrtMemoryInfo*)(uintptr_t)1u;
  return NULL;
}

static void ORT_API_CALL fake_memory_info_device_type(
    const OrtMemoryInfo* info,
    OrtMemoryInfoDeviceType* out) NO_EXCEPTION {
  (void)info;
  *out = OrtMemoryInfoDeviceType_GPU;
}

static OrtStatus* ORT_API_CALL fake_get_tensor_data(
    const OrtValue* value,
    const void** out) NO_EXCEPTION {
  (void)value;
  *out = (const void*)(uintptr_t)1u;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_get_onnx_type(
    const OrtTypeInfo* info,
    ONNXType* out) NO_EXCEPTION {
  (void)info;
  *out = ONNX_TYPE_TENSOR;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_cast_tensor_info(
    const OrtTypeInfo* info,
    const OrtTensorTypeAndShapeInfo** out) NO_EXCEPTION {
  *out = (const OrtTensorTypeAndShapeInfo*)info;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_get_element_type(
    const OrtTensorTypeAndShapeInfo* info,
    ONNXTensorElementDataType* out) NO_EXCEPTION {
  (void)info;
  *out = ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;
  return NULL;
}

static bool ORT_API_CALL fake_has_shape(
    const OrtTensorTypeAndShapeInfo* info) NO_EXCEPTION {
  (void)info;
  return true;
}

static OrtStatus* ORT_API_CALL fake_dimensions_count(
    const OrtTensorTypeAndShapeInfo* info,
    size_t* out) NO_EXCEPTION {
  (void)info;
  *out = 1u;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_dimensions(
    const OrtTensorTypeAndShapeInfo* info,
    int64_t* values,
    size_t count) NO_EXCEPTION {
  if (count > 0u) {
    values[0] = *(const uint8_t*)info == 0u ? -1 : 1;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_symbolic_dimensions(
    const OrtTensorTypeAndShapeInfo* info,
    const char* values[],
    size_t count) NO_EXCEPTION {
  (void)info;
  if (count > 0u) {
    values[0] = NULL;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_cast_sequence(
    const OrtTypeInfo* info,
    const OrtSequenceTypeInfo** out) NO_EXCEPTION {
  (void)info;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_sequence_element(
    const OrtSequenceTypeInfo* info,
    OrtTypeInfo** out) NO_EXCEPTION {
  (void)info;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_cast_map(
    const OrtTypeInfo* info,
    const OrtMapTypeInfo** out) NO_EXCEPTION {
  (void)info;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_map_key(
    const OrtMapTypeInfo* info,
    ONNXTensorElementDataType* out) NO_EXCEPTION {
  (void)info;
  *out = ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_map_value(
    const OrtMapTypeInfo* info,
    OrtTypeInfo** out) NO_EXCEPTION {
  (void)info;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_cast_optional(
    const OrtTypeInfo* info,
    const OrtOptionalTypeInfo** out) NO_EXCEPTION {
  (void)info;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_optional_element(
    const OrtOptionalTypeInfo* info,
    OrtTypeInfo** out) NO_EXCEPTION {
  (void)info;
  *out = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_session_metadata(
    const OrtSession* session,
    OrtModelMetadata** out) NO_EXCEPTION {
  const fake_session_t* fake = (const fake_session_t*)session;
  fake_metadata_t* metadata = NULL;
  *out = NULL;
  if ((fake->scenario == FAKE_SESSION_VERIFY_METADATA_OWNER &&
       fake_metadata_owner_count != 0u) ||
      (fake->scenario == FAKE_SESSION_VERIFY_METADATA_KEY_OWNERS &&
       fake_key_owner_count != 0u)) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  metadata = (fake_metadata_t*)malloc(sizeof(*metadata));
  if (metadata == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  metadata->scenario = fake->scenario;
  ++fake_metadata_owner_count;
  *out = (OrtModelMetadata*)metadata;
  if (fake->scenario == FAKE_SESSION_METADATA_ERROR_WITH_OWNER) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_null_metadata_string(
    const OrtModelMetadata* metadata,
    OrtAllocator* allocator,
    char** out) NO_EXCEPTION {
  const fake_metadata_t* fake = (const fake_metadata_t*)metadata;
  (void)allocator;
  if (fake->scenario == FAKE_SESSION_NULL_METADATA) {
    *out = NULL;
    return NULL;
  }
  *out = (char*)calloc(1u, 1u);
  if (*out == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_metadata_version(
    const OrtModelMetadata* metadata,
    int64_t* out) NO_EXCEPTION {
  (void)metadata;
  *out = 0;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_metadata_keys(
    const OrtModelMetadata* metadata,
    OrtAllocator* allocator,
    char*** keys,
    int64_t* count) NO_EXCEPTION {
  const fake_metadata_t* fake = (const fake_metadata_t*)metadata;
  char** owned_keys = NULL;
  (void)allocator;
  *keys = NULL;
  *count = 0;
  if (fake->scenario == FAKE_SESSION_METADATA_KEYS_ERROR_WITH_OWNERS) {
    owned_keys = (char**)fake_allocate_tracked_key(2u * sizeof(*owned_keys));
    if (owned_keys == NULL) {
      return (OrtStatus*)(uintptr_t)1u;
    }
    owned_keys[0] = (char*)fake_allocate_tracked_key(2u);
    owned_keys[1] = (char*)fake_allocate_tracked_key(2u);
    if (owned_keys[0] == NULL || owned_keys[1] == NULL) {
      fake_allocator_free(allocator, owned_keys[0]);
      fake_allocator_free(allocator, owned_keys[1]);
      fake_allocator_free(allocator, owned_keys);
      return (OrtStatus*)(uintptr_t)1u;
    }
    memcpy(owned_keys[0], "a", 2u);
    memcpy(owned_keys[1], "b", 2u);
    *keys = owned_keys;
    *count = 2;
    return (OrtStatus*)(uintptr_t)1u;
  }
  if (fake->scenario == FAKE_SESSION_OVERSIZED_METADATA_KEY_COUNT) {
    owned_keys = (char**)fake_allocate_tracked_key(sizeof(*owned_keys));
    if (owned_keys == NULL) {
      return (OrtStatus*)(uintptr_t)1u;
    }
    *keys = owned_keys;
    *count = INT64_MAX;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_metadata_lookup(
    const OrtModelMetadata* metadata,
    OrtAllocator* allocator,
    const char* key,
    char** value) NO_EXCEPTION {
  (void)metadata;
  (void)allocator;
  (void)key;
  *value = NULL;
  return NULL;
}

static void ORT_API_CALL fake_release_metadata(
    OrtModelMetadata* metadata) NO_EXCEPTION {
  if (metadata != NULL && fake_metadata_owner_count > 0u) {
    --fake_metadata_owner_count;
  }
  free(metadata);
}

static const OrtApi fake_api = {
    .CreateEnv = fake_create_env,
    .ReleaseEnv = fake_release_env,
    .GetErrorCode = fake_get_error_code,
    .GetErrorMessage = fake_get_error_message,
    .ReleaseStatus = fake_release_status,
    .CreateSessionOptions = fake_create_session_options,
    .ReleaseSessionOptions = fake_release_session_options,
#if defined(FONIX_FAKE_ALLOCATION_EXTENSIONS)
    .CloneSessionOptions = fake_clone_session_options,
#endif
    .SetSessionGraphOptimizationLevel = fake_set_graph_optimization,
    .SetSessionExecutionMode = fake_set_execution_mode,
    .SetIntraOpNumThreads = fake_set_threads,
    .SetInterOpNumThreads = fake_set_threads,
    .EnableCpuMemArena = fake_toggle_options,
    .DisableCpuMemArena = fake_toggle_options,
    .EnableMemPattern = fake_toggle_options,
    .DisableMemPattern = fake_toggle_options,
    .SetDeterministicCompute = fake_set_deterministic,
    .SetSessionLogId = fake_set_log_id,
    .SetSessionLogSeverityLevel = fake_set_log_level,
    .SetSessionLogVerbosityLevel = fake_set_log_level,
    .EnableProfiling = fake_enable_profiling,
    .DisableProfiling = fake_toggle_options,
    .SetOptimizedModelFilePath = fake_set_optimized_path,
    .AddSessionConfigEntry = fake_add_config_entry,
    .SessionOptionsAppendExecutionProvider = fake_append_provider,
#if defined(FONIX_FAKE_ALLOCATION_EXTENSIONS)
    .AddExternalInitializersFromFilesInMemory = fake_add_external_initializers,
#endif
    .CreateSessionFromArray = fake_create_session_from_array,
    .ReleaseSession = fake_release_session,
    .SessionGetInputCount = fake_input_count,
    .SessionGetOutputCount = fake_output_count,
    .SessionGetInputName = fake_input_name,
    .SessionGetOutputName = fake_output_name,
    .SessionGetInputTypeInfo = fake_input_type,
    .SessionGetOutputTypeInfo = fake_output_type,
    .GetAllocatorWithDefaultOptions = fake_get_default_allocator,
    .AllocatorFree = fake_allocator_free,
    .CreateCpuMemoryInfo = fake_create_cpu_memory_info,
    .ReleaseMemoryInfo = fake_release_memory_info,
    .CreateTensorWithDataAsOrtValue = fake_create_tensor_with_data,
    .ReleaseValue = fake_release_value,
    .HasValue = fake_has_value,
    .GetValueType = fake_get_value_type,
    .GetValueCount = fake_get_value_count,
    .GetValue = fake_get_value,
    .CreateValue = fake_create_value,
    .GetTensorTypeAndShape = fake_get_tensor_type_and_shape,
    .ReleaseTensorTypeAndShapeInfo = fake_release_tensor_type_and_shape,
    .GetTensorSizeInBytes = fake_get_tensor_size,
    .GetTensorMemoryInfo = fake_get_tensor_memory_info,
    .MemoryInfoGetDeviceType = fake_memory_info_device_type,
    .GetTensorData = fake_get_tensor_data,
    .GetOnnxTypeFromTypeInfo = fake_get_onnx_type,
    .CastTypeInfoToTensorInfo = fake_cast_tensor_info,
    .GetTensorElementType = fake_get_element_type,
    .TensorTypeAndShape_HasShape = fake_has_shape,
    .GetDimensionsCount = fake_dimensions_count,
    .GetDimensions = fake_dimensions,
    .GetSymbolicDimensions = fake_symbolic_dimensions,
    .CastTypeInfoToSequenceTypeInfo = fake_cast_sequence,
    .GetSequenceElementType = fake_sequence_element,
    .CastTypeInfoToMapTypeInfo = fake_cast_map,
    .GetMapKeyType = fake_map_key,
    .GetMapValueType = fake_map_value,
    .CastTypeInfoToOptionalTypeInfo = fake_cast_optional,
    .GetOptionalContainedTypeInfo = fake_optional_element,
    .ReleaseTypeInfo = fake_release_type_info,
    .SessionGetModelMetadata = fake_session_metadata,
    .ModelMetadataGetProducerName = fake_null_metadata_string,
    .ModelMetadataGetGraphName = fake_null_metadata_string,
    .ModelMetadataGetDomain = fake_null_metadata_string,
    .ModelMetadataGetDescription = fake_null_metadata_string,
    .ModelMetadataGetGraphDescription = fake_null_metadata_string,
    .ModelMetadataGetVersion = fake_metadata_version,
    .ModelMetadataGetCustomMetadataMapKeys = fake_metadata_keys,
    .ModelMetadataLookupCustomMetadataMap = fake_metadata_lookup,
    .ReleaseModelMetadata = fake_release_metadata,
};

static const OrtApi* ORT_API_CALL fake_get_api(uint32_t version) NO_EXCEPTION {
  return version == 27u ? &fake_api : NULL;
}

static const char* ORT_API_CALL fake_get_version(void) NO_EXCEPTION {
  return "1.27.99-fonix-null-metadata";
}

static const OrtApiBase fake_api_base = {fake_get_api, fake_get_version};

#if defined(_WIN32)
__declspec(dllexport)
#else
__attribute__((visibility("default")))
#endif
const OrtApiBase* ORT_API_CALL OrtGetApiBase(void) NO_EXCEPTION {
  return &fake_api_base;
}
