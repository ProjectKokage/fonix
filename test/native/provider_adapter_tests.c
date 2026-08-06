#include "dort.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition, message)                                                \
  do {                                                                           \
    if (!(condition)) {                                                          \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));      \
      return 1;                                                                  \
    }                                                                            \
  } while (0)

enum {
  FAKE_PROVIDER_CUDA = 1,
  FAKE_PROVIDER_TENSORRT = 2,
  FAKE_PROVIDER_DNNL = 3,
  FAKE_PROVIDER_MIGRAPHX = 4,
  FAKE_PROVIDER_DIRECTML = 5,
  FAKE_PROVIDER_OPENVINO = 6
};

enum {
  FAKE_FAILURE_CREATE = 1,
  FAKE_FAILURE_UPDATE = 2,
  FAKE_FAILURE_APPEND = 3,
  FAKE_FAILURE_CREATE_NULL = 4,
  FAKE_FAILURE_PROVIDER_API = 5
};

void fonix_fake_provider_reset(void);
void fonix_fake_provider_set_failure(int stage);
int fonix_fake_provider_live_options(void);
int fonix_fake_provider_live_session_options(void);
int fonix_fake_provider_create_count(void);
int fonix_fake_provider_update_count(void);
int fonix_fake_provider_append_count(void);
int fonix_fake_provider_release_count(void);
int fonix_fake_provider_api_query_count(void);
int fonix_fake_provider_last_provider(void);
int fonix_fake_provider_last_device_id(void);
int fonix_fake_provider_last_option_count(void);
int fonix_fake_provider_last_migraphx_fp16(void);
int fonix_fake_provider_last_migraphx_fp8(void);
int fonix_fake_provider_last_migraphx_int8(void);
int fonix_fake_provider_last_migraphx_exhaustive(void);
size_t fonix_fake_provider_last_migraphx_memory_limit(void);
int fonix_fake_provider_last_migraphx_arena_strategy(void);

static dort_runtime_config_t runtime_config(
    const char* library,
    const char* root) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = DORT_RUNTIME_SOURCE_FILE;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "provider-adapter-tests";
  config.library_path_utf8 = library;
  config.allowed_root_utf8 = root;
  return config;
}

static dort_string_pair_t pair(const char* key, const char* value) {
  dort_string_pair_t result;
  memset(&result, 0, sizeof(result));
  result.struct_size = DORT_STRING_PAIR_V1_SIZE;
  result.key_utf8 = key;
  result.value_utf8 = value;
  return result;
}

static dort_provider_config_t provider(
    const char* provider_id,
    const dort_string_pair_t* options,
    size_t option_count) {
  dort_provider_config_t result;
  memset(&result, 0, sizeof(result));
  result.struct_size = DORT_PROVIDER_CONFIG_V1_SIZE;
  result.provider_id_utf8 = provider_id;
  result.options = options;
  result.option_count = option_count;
  return result;
}

static dort_session_config_t session_config(
    const dort_provider_config_t* providers,
    size_t provider_count,
    uint32_t enable_memory_pattern) {
  dort_session_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_SESSION_CONFIG_V1_SIZE;
  config.graph_optimization_level = DORT_GRAPH_OPTIMIZATION_ALL;
  config.execution_mode = DORT_EXECUTION_SEQUENTIAL;
  config.enable_cpu_memory_arena = 1u;
  config.enable_memory_pattern = enable_memory_pattern;
  config.deterministic_compute = 1u;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "provider-adapter-session";
  config.providers = providers;
  config.provider_count = provider_count;
  return config;
}

static int expect_error(
    dort_status_t* status,
    int32_t expected_code,
    const char* context) {
  if (status == NULL) {
    fprintf(stderr, "FAIL: %s unexpectedly succeeded\n", context);
    return 1;
  }
  if (dort_status_code(status) != expected_code) {
    fprintf(
        stderr,
        "FAIL: %s returned code %" PRId32 " instead of %" PRId32 ": %s\n",
        context,
        dort_status_code(status),
        expected_code,
        dort_status_message(status));
    dort_status_release(status);
    return 1;
  }
  dort_status_release(status);
  return 0;
}

static int create_and_release(
    dort_runtime_t* runtime,
    const dort_session_config_t* config) {
  dort_session_options_t* options = NULL;
  dort_status_t* status = dort_session_options_create(runtime, config, &options);
  CHECK(status == NULL, "provider adapter unexpectedly failed");
  CHECK(options != NULL, "provider adapter returned no session options");
  CHECK(
      fonix_fake_provider_live_session_options() == 1,
      "successful session options were not owned exactly once");
  dort_session_options_release(options);
  CHECK(
      fonix_fake_provider_live_session_options() == 0,
      "successful session options leaked");
  return 0;
}

static int test_success_paths(dort_runtime_t* runtime) {
  dort_string_pair_t cuda_options[] = {
      pair("device_id", "2"),
      pair("enable_cuda_graph", "1"),
  };
  dort_provider_config_t cuda =
      provider("cuda", cuda_options, sizeof(cuda_options) / sizeof(cuda_options[0]));
  dort_session_config_t config = session_config(&cuda, 1u, 1u);

  fonix_fake_provider_reset();
  CHECK(create_and_release(runtime, &config) == 0, "CUDA success path failed");
  CHECK(fonix_fake_provider_create_count() == 1, "CUDA create count mismatch");
  CHECK(fonix_fake_provider_update_count() == 1, "CUDA update count mismatch");
  CHECK(fonix_fake_provider_append_count() == 1, "CUDA append count mismatch");
  CHECK(fonix_fake_provider_release_count() == 1, "CUDA release count mismatch");
  CHECK(fonix_fake_provider_live_options() == 0, "CUDA options leaked");
  CHECK(fonix_fake_provider_last_provider() == FAKE_PROVIDER_CUDA, "wrong CUDA path");
  CHECK(fonix_fake_provider_last_option_count() == 2, "CUDA option count mismatch");

  {
    dort_string_pair_t tensorrt_options[] = {
        pair("device_id", "1"),
        pair("trt_fp16_enable", "1"),
    };
    dort_provider_config_t providers[] = {
        provider(
            "tensorrt",
            tensorrt_options,
            sizeof(tensorrt_options) / sizeof(tensorrt_options[0])),
        provider("cuda", NULL, 0u),
    };
    config = session_config(providers, 2u, 1u);
    fonix_fake_provider_reset();
    CHECK(create_and_release(runtime, &config) == 0, "TensorRT success path failed");
    CHECK(fonix_fake_provider_create_count() == 2, "TensorRT/CUDA create count mismatch");
    CHECK(fonix_fake_provider_update_count() == 2, "TensorRT/CUDA update count mismatch");
    CHECK(fonix_fake_provider_append_count() == 2, "TensorRT/CUDA append count mismatch");
    CHECK(fonix_fake_provider_release_count() == 2, "TensorRT/CUDA release count mismatch");
    CHECK(fonix_fake_provider_live_options() == 0, "TensorRT/CUDA options leaked");
  }

  {
    dort_string_pair_t directml_options[] = {pair("device_id", "3")};
    dort_provider_config_t directml = provider("directml", directml_options, 1u);
    config = session_config(&directml, 1u, 0u);
    fonix_fake_provider_reset();
    CHECK(create_and_release(runtime, &config) == 0, "DirectML success path failed");
    CHECK(fonix_fake_provider_api_query_count() == 1, "DirectML API was not queried");
    CHECK(fonix_fake_provider_append_count() == 1, "DirectML append count mismatch");
    CHECK(
        fonix_fake_provider_last_provider() == FAKE_PROVIDER_DIRECTML,
        "wrong DirectML path");
    CHECK(fonix_fake_provider_last_device_id() == 3, "DirectML device mismatch");
  }

  {
    dort_string_pair_t openvino_options[] = {
        pair("device_type", "AUTO:GPU,CPU"),
        pair("enable_opencl_throttling", "true"),
    };
    dort_provider_config_t openvino = provider("openvino", openvino_options, 2u);
    config = session_config(&openvino, 1u, 1u);
    fonix_fake_provider_reset();
    CHECK(create_and_release(runtime, &config) == 0, "OpenVINO success path failed");
    CHECK(
        fonix_fake_provider_last_provider() == FAKE_PROVIDER_OPENVINO,
        "wrong OpenVINO path");
    CHECK(fonix_fake_provider_create_count() == 0, "OpenVINO used an option object");
    CHECK(fonix_fake_provider_last_option_count() == 2, "OpenVINO option count mismatch");
  }

  {
    dort_string_pair_t dnnl_options[] = {pair("use_arena", "0")};
    dort_provider_config_t dnnl = provider("dnnl", dnnl_options, 1u);
    config = session_config(&dnnl, 1u, 1u);
    fonix_fake_provider_reset();
    CHECK(create_and_release(runtime, &config) == 0, "oneDNN success path failed");
    CHECK(
        fonix_fake_provider_last_provider() == FAKE_PROVIDER_DNNL,
        "wrong oneDNN path");
    CHECK(fonix_fake_provider_release_count() == 1, "oneDNN options were not released");
  }

  {
    dort_string_pair_t migraphx_options[] = {
        pair("device_id", "4"),
        pair("migraphx_fp16_enable", "1"),
        pair("migraphx_fp8_enable", "1"),
        pair("migraphx_int8_enable", "1"),
        pair("migraphx_exhaustive_tune", "1"),
        pair("migraphx_mem_limit", "16384"),
        pair("migraphx_arena_extend_strategy", "kSameAsRequested"),
    };
    dort_provider_config_t migraphx = provider(
        "migraphx",
        migraphx_options,
        sizeof(migraphx_options) / sizeof(migraphx_options[0]));
    config = session_config(&migraphx, 1u, 1u);
    fonix_fake_provider_reset();
    CHECK(create_and_release(runtime, &config) == 0, "MIGraphX success path failed");
    CHECK(
        fonix_fake_provider_last_provider() == FAKE_PROVIDER_MIGRAPHX,
        "wrong MIGraphX path");
    CHECK(fonix_fake_provider_last_device_id() == 4, "MIGraphX device mismatch");
    CHECK(fonix_fake_provider_last_migraphx_fp16() == 1, "MIGraphX FP16 mismatch");
    CHECK(fonix_fake_provider_last_migraphx_fp8() == 1, "MIGraphX FP8 mismatch");
    CHECK(fonix_fake_provider_last_migraphx_int8() == 1, "MIGraphX INT8 mismatch");
    CHECK(
        fonix_fake_provider_last_migraphx_exhaustive() == 1,
        "MIGraphX tuning mismatch");
    CHECK(
        fonix_fake_provider_last_migraphx_memory_limit() == 16384u,
        "MIGraphX memory limit mismatch");
    CHECK(
        fonix_fake_provider_last_migraphx_arena_strategy() == 1,
        "MIGraphX arena mismatch");
  }
  return 0;
}

static int test_option_object_failures(dort_runtime_t* runtime) {
  const char* provider_ids[] = {"cuda", "tensorrt", "dnnl"};
  int stages[] = {
      FAKE_FAILURE_CREATE,
      FAKE_FAILURE_UPDATE,
      FAKE_FAILURE_APPEND,
      FAKE_FAILURE_CREATE_NULL,
  };
  size_t provider_index = 0u;
  size_t stage_index = 0u;
  for (provider_index = 0u;
       provider_index < sizeof(provider_ids) / sizeof(provider_ids[0]);
       ++provider_index) {
    for (stage_index = 0u; stage_index < sizeof(stages) / sizeof(stages[0]);
         ++stage_index) {
      dort_string_pair_t target_option =
          strcmp(provider_ids[provider_index], "dnnl") == 0
              ? pair("use_arena", "1")
              : pair("device_id", "0");
      dort_provider_config_t providers[2];
      dort_session_config_t config;
      dort_session_options_t* options =
          (dort_session_options_t*)(uintptr_t)1u;
      dort_status_t* status = NULL;
      size_t provider_count = 1u;
      providers[0] = provider(provider_ids[provider_index], &target_option, 1u);
      if (strcmp(provider_ids[provider_index], "tensorrt") == 0) {
        providers[1] = provider("cuda", NULL, 0u);
        provider_count = 2u;
      }
      config = session_config(providers, provider_count, 1u);
      fonix_fake_provider_reset();
      fonix_fake_provider_set_failure(stages[stage_index]);
      status = dort_session_options_create(runtime, &config, &options);
      CHECK(options == NULL, "failing option-object path returned session options");
      CHECK(
          expect_error(
              status,
              DORT_ERROR_PROVIDER_UNSUPPORTED,
              provider_ids[provider_index]) == 0,
          "unexpected provider failure code");
      CHECK(
          fonix_fake_provider_live_options() == 0,
          "provider options leaked after failure");
      CHECK(
          fonix_fake_provider_live_session_options() == 0,
          "session options leaked after provider failure");
      CHECK(fonix_fake_provider_create_count() == 1, "provider create count mismatch");
      CHECK(
          fonix_fake_provider_release_count() ==
              (stages[stage_index] == FAKE_FAILURE_CREATE_NULL ? 0 : 1),
          "provider release count mismatch");
    }
  }
  return 0;
}

static int test_fail_closed_validation(dort_runtime_t* runtime) {
  dort_string_pair_t unsafe_cuda_option = pair("user_compute_stream", "1234");
  dort_provider_config_t unsafe_cuda =
      provider("cuda", &unsafe_cuda_option, 1u);
  dort_session_config_t config = session_config(&unsafe_cuda, 1u, 1u);
  dort_session_options_t* options = (dort_session_options_t*)(uintptr_t)1u;
  dort_status_t* status = NULL;

  fonix_fake_provider_reset();
  status = dort_session_options_create(runtime, &config, &options);
  CHECK(options == NULL, "unsafe CUDA option returned session options");
  CHECK(
      expect_error(status, DORT_ERROR_INVALID_ARGUMENT, "unsafe CUDA option") == 0,
      "unsafe CUDA option code mismatch");
  CHECK(fonix_fake_provider_create_count() == 0, "unsafe CUDA option reached ORT");
  CHECK(
      fonix_fake_provider_live_session_options() == 0,
      "unsafe CUDA option allocated session options");

  {
    dort_provider_config_t providers[] = {
        provider("cuda", NULL, 0u),
        provider("tensorrt", NULL, 0u),
    };
    config = session_config(providers, 2u, 1u);
    options = (dort_session_options_t*)(uintptr_t)1u;
    fonix_fake_provider_reset();
    status = dort_session_options_create(runtime, &config, &options);
    CHECK(options == NULL, "invalid TensorRT order returned session options");
    CHECK(
        expect_error(status, DORT_ERROR_INVALID_ARGUMENT, "TensorRT order") == 0,
        "TensorRT order code mismatch");
    CHECK(fonix_fake_provider_create_count() == 0, "invalid order reached ORT");
  }

  {
    dort_provider_config_t providers[] = {
        provider("cpu", NULL, 0u),
        provider("cuda", NULL, 0u),
    };
    config = session_config(providers, 2u, 1u);
    options = (dort_session_options_t*)(uintptr_t)1u;
    fonix_fake_provider_reset();
    status = dort_session_options_create(runtime, &config, &options);
    CHECK(options == NULL, "invalid CPU fallback order returned session options");
    CHECK(
        expect_error(status, DORT_ERROR_INVALID_ARGUMENT, "CPU order") == 0,
        "CPU order code mismatch");
    CHECK(fonix_fake_provider_create_count() == 0, "invalid CPU order reached ORT");
  }

  {
    dort_string_pair_t directml_option = pair("device_id", "0");
    dort_provider_config_t directml = provider("directml", &directml_option, 1u);
    config = session_config(&directml, 1u, 1u);
    options = (dort_session_options_t*)(uintptr_t)1u;
    fonix_fake_provider_reset();
    status = dort_session_options_create(runtime, &config, &options);
    CHECK(options == NULL, "invalid DirectML memory pattern returned options");
    CHECK(
        expect_error(status, DORT_ERROR_INVALID_ARGUMENT, "DirectML constraints") == 0,
        "DirectML constraint code mismatch");
    CHECK(fonix_fake_provider_api_query_count() == 0, "invalid DirectML reached ORT");
  }

  {
    dort_string_pair_t migraphx_option = pair("migraphx_fp16_enable", "true");
    dort_provider_config_t migraphx = provider("migraphx", &migraphx_option, 1u);
    config = session_config(&migraphx, 1u, 1u);
    options = (dort_session_options_t*)(uintptr_t)1u;
    fonix_fake_provider_reset();
    status = dort_session_options_create(runtime, &config, &options);
    CHECK(options == NULL, "invalid MIGraphX boolean returned session options");
    CHECK(
        expect_error(status, DORT_ERROR_INVALID_ARGUMENT, "MIGraphX boolean") == 0,
        "MIGraphX boolean code mismatch");
    CHECK(fonix_fake_provider_append_count() == 0, "invalid MIGraphX reached ORT");
    CHECK(
        fonix_fake_provider_live_session_options() == 0,
        "invalid MIGraphX leaked session options");
  }

  {
    dort_string_pair_t directml_option = pair("device_id", "0");
    dort_provider_config_t directml = provider("directml", &directml_option, 1u);
    config = session_config(&directml, 1u, 0u);
    options = (dort_session_options_t*)(uintptr_t)1u;
    fonix_fake_provider_reset();
    fonix_fake_provider_set_failure(FAKE_FAILURE_PROVIDER_API);
    status = dort_session_options_create(runtime, &config, &options);
    CHECK(options == NULL, "failing DirectML API query returned options");
    CHECK(
        expect_error(status, DORT_ERROR_PROVIDER_UNSUPPORTED, "DirectML API") == 0,
        "DirectML API failure code mismatch");
    CHECK(fonix_fake_provider_api_query_count() == 1, "DirectML API query mismatch");
    CHECK(
        fonix_fake_provider_live_session_options() == 0,
        "DirectML API failure leaked session options");
  }
  return 0;
}

int main(int argc, char** argv) {
  dort_runtime_t* runtime = NULL;
  dort_status_t* status = NULL;
  dort_runtime_config_t config;
  CHECK(argc == 3, "expected fake provider runtime library and root paths");
  config = runtime_config(argv[1], argv[2]);
  status = dort_runtime_open(&config, &runtime);
  CHECK(status == NULL && runtime != NULL, "could not open provider fake runtime");

  CHECK(test_success_paths(runtime) == 0, "provider success tests failed");
  CHECK(
      test_option_object_failures(runtime) == 0,
      "provider cleanup tests failed");
  CHECK(
      test_fail_closed_validation(runtime) == 0,
      "provider validation tests failed");

  dort_runtime_release(runtime);
  CHECK(fonix_fake_provider_live_options() == 0, "final provider options leak");
  CHECK(
      fonix_fake_provider_live_session_options() == 0,
      "final session-options leak");
  printf("Fonix desktop provider adapter tests passed.\n");
  return 0;
}
