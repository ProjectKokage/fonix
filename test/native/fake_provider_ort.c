#include <onnxruntime_c_api.h>

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define FONIX_TEST_EXPORT __declspec(dllexport)
#else
#define FONIX_TEST_EXPORT __attribute__((visibility("default")))
#endif

enum {
  FAKE_PROVIDER_NONE = 0,
  FAKE_PROVIDER_CUDA = 1,
  FAKE_PROVIDER_TENSORRT = 2,
  FAKE_PROVIDER_DNNL = 3,
  FAKE_PROVIDER_MIGRAPHX = 4,
  FAKE_PROVIDER_DIRECTML = 5,
  FAKE_PROVIDER_OPENVINO = 6
};

enum {
  FAKE_FAILURE_NONE = 0,
  FAKE_FAILURE_CREATE = 1,
  FAKE_FAILURE_UPDATE = 2,
  FAKE_FAILURE_APPEND = 3,
  FAKE_FAILURE_CREATE_NULL = 4,
  FAKE_FAILURE_PROVIDER_API = 5
};

typedef struct fake_provider_options {
  int kind;
} fake_provider_options_t;

typedef struct fake_dml_api {
  OrtStatus*(ORT_API_CALL* SessionOptionsAppendExecutionProvider_DML)(
      OrtSessionOptions* options,
      int device_id);
} fake_dml_api_t;

static int fake_failure_stage = FAKE_FAILURE_NONE;
static int fake_live_provider_options = 0;
static int fake_live_session_options = 0;
static int fake_create_count = 0;
static int fake_update_count = 0;
static int fake_append_count = 0;
static int fake_release_count = 0;
static int fake_provider_api_query_count = 0;
static int fake_last_provider = FAKE_PROVIDER_NONE;
static int fake_last_device_id = -1;
static int fake_last_option_count = -1;
static int fake_last_migraphx_fp16 = -1;
static int fake_last_migraphx_fp8 = -1;
static int fake_last_migraphx_int8 = -1;
static int fake_last_migraphx_exhaustive = -1;
static size_t fake_last_migraphx_memory_limit = 0u;
static int fake_last_migraphx_arena_strategy = -1;

static OrtStatus* fake_error_status(void) {
  return (OrtStatus*)malloc(1u);
}

FONIX_TEST_EXPORT void fonix_fake_provider_reset(void) {
  fake_failure_stage = FAKE_FAILURE_NONE;
  fake_create_count = 0;
  fake_update_count = 0;
  fake_append_count = 0;
  fake_release_count = 0;
  fake_provider_api_query_count = 0;
  fake_last_provider = FAKE_PROVIDER_NONE;
  fake_last_device_id = -1;
  fake_last_option_count = -1;
  fake_last_migraphx_fp16 = -1;
  fake_last_migraphx_fp8 = -1;
  fake_last_migraphx_int8 = -1;
  fake_last_migraphx_exhaustive = -1;
  fake_last_migraphx_memory_limit = 0u;
  fake_last_migraphx_arena_strategy = -1;
}

FONIX_TEST_EXPORT void fonix_fake_provider_set_failure(int stage) {
  fake_failure_stage = stage;
}

FONIX_TEST_EXPORT int fonix_fake_provider_live_options(void) {
  return fake_live_provider_options;
}

FONIX_TEST_EXPORT int fonix_fake_provider_live_session_options(void) {
  return fake_live_session_options;
}

FONIX_TEST_EXPORT int fonix_fake_provider_create_count(void) {
  return fake_create_count;
}

FONIX_TEST_EXPORT int fonix_fake_provider_update_count(void) {
  return fake_update_count;
}

FONIX_TEST_EXPORT int fonix_fake_provider_append_count(void) {
  return fake_append_count;
}

FONIX_TEST_EXPORT int fonix_fake_provider_release_count(void) {
  return fake_release_count;
}

FONIX_TEST_EXPORT int fonix_fake_provider_api_query_count(void) {
  return fake_provider_api_query_count;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_provider(void) {
  return fake_last_provider;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_device_id(void) {
  return fake_last_device_id;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_option_count(void) {
  return fake_last_option_count;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_migraphx_fp16(void) {
  return fake_last_migraphx_fp16;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_migraphx_fp8(void) {
  return fake_last_migraphx_fp8;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_migraphx_int8(void) {
  return fake_last_migraphx_int8;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_migraphx_exhaustive(void) {
  return fake_last_migraphx_exhaustive;
}

FONIX_TEST_EXPORT size_t fonix_fake_provider_last_migraphx_memory_limit(void) {
  return fake_last_migraphx_memory_limit;
}

FONIX_TEST_EXPORT int fonix_fake_provider_last_migraphx_arena_strategy(void) {
  return fake_last_migraphx_arena_strategy;
}

static OrtStatus* ORT_API_CALL fake_create_env(
    OrtLoggingLevel log_severity_level,
    const char* log_id,
    OrtEnv** out) NO_EXCEPTION {
  (void)log_severity_level;
  (void)log_id;
  if (out == NULL) {
    return fake_error_status();
  }
  *out = (OrtEnv*)malloc(1u);
  return *out == NULL ? fake_error_status() : NULL;
}

static void ORT_API_CALL fake_release_env(OrtEnv* environment) NO_EXCEPTION {
  free(environment);
}

static OrtErrorCode ORT_API_CALL fake_get_error_code(
    const OrtStatus* status) NO_EXCEPTION {
  (void)status;
  return ORT_FAIL;
}

static const char* ORT_API_CALL fake_get_error_message(
    const OrtStatus* status) NO_EXCEPTION {
  (void)status;
  return "fake provider failure";
}

static void ORT_API_CALL fake_release_status(OrtStatus* status) NO_EXCEPTION {
  free(status);
}

static OrtStatus* ORT_API_CALL fake_create_session_options(
    OrtSessionOptions** out) NO_EXCEPTION {
  if (out == NULL) {
    return fake_error_status();
  }
  *out = (OrtSessionOptions*)malloc(1u);
  if (*out == NULL) {
    return fake_error_status();
  }
  ++fake_live_session_options;
  return NULL;
}

static void ORT_API_CALL fake_release_session_options(
    OrtSessionOptions* options) NO_EXCEPTION {
  if (options != NULL) {
    --fake_live_session_options;
    free(options);
  }
}

static OrtStatus* ORT_API_CALL fake_set_optimized_model_file_path(
    OrtSessionOptions* options,
    const ORTCHAR_T* path) NO_EXCEPTION {
  (void)options;
  (void)path;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_execution_mode(
    OrtSessionOptions* options,
    ExecutionMode mode) NO_EXCEPTION {
  (void)options;
  (void)mode;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_enable_profiling(
    OrtSessionOptions* options,
    const ORTCHAR_T* path) NO_EXCEPTION {
  (void)options;
  (void)path;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_session_options_only(
    OrtSessionOptions* options) NO_EXCEPTION {
  (void)options;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_session_log_id(
    OrtSessionOptions* options,
    const char* log_id) NO_EXCEPTION {
  (void)options;
  (void)log_id;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_session_int(
    OrtSessionOptions* options,
    int value) NO_EXCEPTION {
  (void)options;
  (void)value;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_graph_optimization(
    OrtSessionOptions* options,
    GraphOptimizationLevel level) NO_EXCEPTION {
  (void)options;
  (void)level;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_deterministic_compute(
    OrtSessionOptions* options,
    bool value) NO_EXCEPTION {
  (void)options;
  (void)value;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_add_session_config_entry(
    OrtSessionOptions* options,
    const char* key,
    const char* value) NO_EXCEPTION {
  (void)options;
  (void)key;
  (void)value;
  return NULL;
}

static OrtStatus* fake_create_provider_options(
    int kind,
    void** out_options) {
  fake_provider_options_t* created = NULL;
  ++fake_create_count;
  if (out_options == NULL) {
    return fake_error_status();
  }
  *out_options = NULL;
  if (fake_failure_stage == FAKE_FAILURE_CREATE_NULL) {
    return NULL;
  }
  created = (fake_provider_options_t*)calloc(1u, sizeof(*created));
  if (created == NULL) {
    return fake_error_status();
  }
  created->kind = kind;
  *out_options = created;
  ++fake_live_provider_options;
  return fake_failure_stage == FAKE_FAILURE_CREATE ? fake_error_status() : NULL;
}

static OrtStatus* fake_update_provider_options(
    const void* provider_options,
    const char* const* keys,
    const char* const* values,
    size_t count) {
  (void)provider_options;
  ++fake_update_count;
  fake_last_option_count = (int)count;
  if (count > 0u && (keys == NULL || values == NULL)) {
    return fake_error_status();
  }
  return fake_failure_stage == FAKE_FAILURE_UPDATE ? fake_error_status() : NULL;
}

static OrtStatus* fake_append_provider_options(
    int kind,
    const void* provider_options) {
  ++fake_append_count;
  fake_last_provider = kind;
  if (provider_options == NULL) {
    return fake_error_status();
  }
  return fake_failure_stage == FAKE_FAILURE_APPEND ? fake_error_status() : NULL;
}

static void fake_release_provider_options(void* provider_options) {
  if (provider_options != NULL) {
    ++fake_release_count;
    --fake_live_provider_options;
    free(provider_options);
  }
}

static OrtStatus* ORT_API_CALL fake_create_cuda_options(
    OrtCUDAProviderOptionsV2** out) NO_EXCEPTION {
  return fake_create_provider_options(FAKE_PROVIDER_CUDA, (void**)out);
}

static OrtStatus* ORT_API_CALL fake_update_cuda_options(
    OrtCUDAProviderOptionsV2* provider_options,
    const char* const* keys,
    const char* const* values,
    size_t count) NO_EXCEPTION {
  return fake_update_provider_options(provider_options, keys, values, count);
}

static OrtStatus* ORT_API_CALL fake_append_cuda(
    OrtSessionOptions* options,
    const OrtCUDAProviderOptionsV2* provider_options) NO_EXCEPTION {
  (void)options;
  return fake_append_provider_options(FAKE_PROVIDER_CUDA, provider_options);
}

static void ORT_API_CALL fake_release_cuda_options(
    OrtCUDAProviderOptionsV2* provider_options) NO_EXCEPTION {
  fake_release_provider_options(provider_options);
}

static OrtStatus* ORT_API_CALL fake_create_tensorrt_options(
    OrtTensorRTProviderOptionsV2** out) NO_EXCEPTION {
  return fake_create_provider_options(FAKE_PROVIDER_TENSORRT, (void**)out);
}

static OrtStatus* ORT_API_CALL fake_update_tensorrt_options(
    OrtTensorRTProviderOptionsV2* provider_options,
    const char* const* keys,
    const char* const* values,
    size_t count) NO_EXCEPTION {
  return fake_update_provider_options(provider_options, keys, values, count);
}

static OrtStatus* ORT_API_CALL fake_append_tensorrt(
    OrtSessionOptions* options,
    const OrtTensorRTProviderOptionsV2* provider_options) NO_EXCEPTION {
  (void)options;
  return fake_append_provider_options(FAKE_PROVIDER_TENSORRT, provider_options);
}

static void ORT_API_CALL fake_release_tensorrt_options(
    OrtTensorRTProviderOptionsV2* provider_options) NO_EXCEPTION {
  fake_release_provider_options(provider_options);
}

static OrtStatus* ORT_API_CALL fake_create_dnnl_options(
    OrtDnnlProviderOptions** out) NO_EXCEPTION {
  return fake_create_provider_options(FAKE_PROVIDER_DNNL, (void**)out);
}

static OrtStatus* ORT_API_CALL fake_update_dnnl_options(
    OrtDnnlProviderOptions* provider_options,
    const char* const* keys,
    const char* const* values,
    size_t count) NO_EXCEPTION {
  return fake_update_provider_options(provider_options, keys, values, count);
}

static OrtStatus* ORT_API_CALL fake_append_dnnl(
    OrtSessionOptions* options,
    const OrtDnnlProviderOptions* provider_options) NO_EXCEPTION {
  (void)options;
  return fake_append_provider_options(FAKE_PROVIDER_DNNL, provider_options);
}

static void ORT_API_CALL fake_release_dnnl_options(
    OrtDnnlProviderOptions* provider_options) NO_EXCEPTION {
  fake_release_provider_options(provider_options);
}

static OrtStatus* ORT_API_CALL fake_append_migraphx(
    OrtSessionOptions* options,
    const OrtMIGraphXProviderOptions* provider_options) NO_EXCEPTION {
  (void)options;
  ++fake_append_count;
  fake_last_provider = FAKE_PROVIDER_MIGRAPHX;
  if (provider_options == NULL) {
    return fake_error_status();
  }
  fake_last_device_id = provider_options->device_id;
  fake_last_migraphx_fp16 = provider_options->migraphx_fp16_enable;
  fake_last_migraphx_fp8 = provider_options->migraphx_fp8_enable;
  fake_last_migraphx_int8 = provider_options->migraphx_int8_enable;
  fake_last_migraphx_exhaustive =
      provider_options->migraphx_exhaustive_tune ? 1 : 0;
  fake_last_migraphx_memory_limit = provider_options->migraphx_mem_limit;
  fake_last_migraphx_arena_strategy =
      provider_options->migraphx_arena_extend_strategy;
  return fake_failure_stage == FAKE_FAILURE_APPEND ? fake_error_status() : NULL;
}

static OrtStatus* ORT_API_CALL fake_append_directml(
    OrtSessionOptions* options,
    int device_id) NO_EXCEPTION {
  (void)options;
  ++fake_append_count;
  fake_last_provider = FAKE_PROVIDER_DIRECTML;
  fake_last_device_id = device_id;
  return fake_failure_stage == FAKE_FAILURE_APPEND ? fake_error_status() : NULL;
}

static const fake_dml_api_t fake_dml_api = {
    fake_append_directml,
};

static OrtStatus* ORT_API_CALL fake_get_execution_provider_api(
    const char* provider_name,
    uint32_t version,
    const void** out_provider_api) NO_EXCEPTION {
  ++fake_provider_api_query_count;
  if (fake_failure_stage == FAKE_FAILURE_PROVIDER_API) {
    return fake_error_status();
  }
  if (provider_name == NULL || strcmp(provider_name, "DML") != 0 ||
      version != 27u || out_provider_api == NULL) {
    return fake_error_status();
  }
  *out_provider_api = &fake_dml_api;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_append_generic_provider(
    OrtSessionOptions* options,
    const char* provider_name,
    const char* const* keys,
    const char* const* values,
    size_t count) NO_EXCEPTION {
  (void)options;
  ++fake_append_count;
  fake_last_option_count = (int)count;
  if (provider_name == NULL || strcmp(provider_name, "OpenVINO") != 0 ||
      (count > 0u && (keys == NULL || values == NULL))) {
    return fake_error_status();
  }
  fake_last_provider = FAKE_PROVIDER_OPENVINO;
  return fake_failure_stage == FAKE_FAILURE_APPEND ? fake_error_status() : NULL;
}

static const OrtApi fake_api = {
    .CreateEnv = fake_create_env,
    .ReleaseEnv = fake_release_env,
    .GetErrorCode = fake_get_error_code,
    .GetErrorMessage = fake_get_error_message,
    .ReleaseStatus = fake_release_status,
    .CreateSessionOptions = fake_create_session_options,
    .ReleaseSessionOptions = fake_release_session_options,
    .SetOptimizedModelFilePath = fake_set_optimized_model_file_path,
    .SetSessionExecutionMode = fake_set_execution_mode,
    .EnableProfiling = fake_enable_profiling,
    .DisableProfiling = fake_session_options_only,
    .EnableMemPattern = fake_session_options_only,
    .DisableMemPattern = fake_session_options_only,
    .EnableCpuMemArena = fake_session_options_only,
    .DisableCpuMemArena = fake_session_options_only,
    .SetSessionLogId = fake_set_session_log_id,
    .SetSessionLogVerbosityLevel = fake_set_session_int,
    .SetSessionLogSeverityLevel = fake_set_session_int,
    .SetSessionGraphOptimizationLevel = fake_set_graph_optimization,
    .SetIntraOpNumThreads = fake_set_session_int,
    .SetInterOpNumThreads = fake_set_session_int,
    .AddSessionConfigEntry = fake_add_session_config_entry,
    .SessionOptionsAppendExecutionProvider = fake_append_generic_provider,
    .CreateTensorRTProviderOptions = fake_create_tensorrt_options,
    .UpdateTensorRTProviderOptions = fake_update_tensorrt_options,
    .ReleaseTensorRTProviderOptions = fake_release_tensorrt_options,
    .SessionOptionsAppendExecutionProvider_TensorRT_V2 = fake_append_tensorrt,
    .GetExecutionProviderApi = fake_get_execution_provider_api,
    .SessionOptionsAppendExecutionProvider_CUDA_V2 = fake_append_cuda,
    .CreateCUDAProviderOptions = fake_create_cuda_options,
    .UpdateCUDAProviderOptions = fake_update_cuda_options,
    .ReleaseCUDAProviderOptions = fake_release_cuda_options,
    .SessionOptionsAppendExecutionProvider_MIGraphX = fake_append_migraphx,
    .SessionOptionsAppendExecutionProvider_Dnnl = fake_append_dnnl,
    .CreateDnnlProviderOptions = fake_create_dnnl_options,
    .UpdateDnnlProviderOptions = fake_update_dnnl_options,
    .ReleaseDnnlProviderOptions = fake_release_dnnl_options,
    .SetDeterministicCompute = fake_set_deterministic_compute,
};

static const OrtApi* ORT_API_CALL fake_get_api(uint32_t version) NO_EXCEPTION {
  return version == 27u ? &fake_api : NULL;
}

static const char* ORT_API_CALL fake_get_version(void) NO_EXCEPTION {
  return "1.27.99-fonix-provider-test";
}

static const OrtApiBase fake_api_base = {
    fake_get_api,
    fake_get_version,
};

FONIX_TEST_EXPORT const OrtApiBase* ORT_API_CALL OrtGetApiBase(void) NO_EXCEPTION {
  return &fake_api_base;
}
