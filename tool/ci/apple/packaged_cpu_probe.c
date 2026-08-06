#include "dort.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(condition, message)                                             \
  do {                                                                        \
    if (!(condition)) {                                                       \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));   \
      return 1;                                                               \
    }                                                                         \
  } while (0)

static int check_ok(dort_status_t* status, const char* operation) {
  if (status == NULL) {
    return 0;
  }
  fprintf(
      stderr,
      "FAIL %s: domain=%u code=%d ort=%d operation=%s message=%s\n",
      operation,
      dort_status_domain(status),
      dort_status_code(status),
      dort_status_ort_code(status),
      dort_status_operation(status),
      dort_status_message(status));
  dort_status_release(status);
  return 1;
}

static int read_file(const char* file_path, uint8_t** out_bytes, size_t* out_size) {
  FILE* file = NULL;
  long file_size = 0;
  uint8_t* bytes = NULL;

  *out_bytes = NULL;
  *out_size = 0u;
  file = fopen(file_path, "rb");
  CHECK(file != NULL, "could not open the model fixture");
  CHECK(fseek(file, 0, SEEK_END) == 0, "could not seek the model fixture");
  file_size = ftell(file);
  CHECK(file_size > 0, "the model fixture is empty");
  CHECK(fseek(file, 0, SEEK_SET) == 0, "could not rewind the model fixture");
  bytes = (uint8_t*)malloc((size_t)file_size);
  CHECK(bytes != NULL, "could not allocate model bytes");
  CHECK(
      fread(bytes, 1u, (size_t)file_size, file) == (size_t)file_size,
      "could not read the model fixture");
  CHECK(fclose(file) == 0, "could not close the model fixture");
  *out_bytes = bytes;
  *out_size = (size_t)file_size;
  return 0;
}

static dort_runtime_config_t runtime_config(void) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = DORT_RUNTIME_SOURCE_BUNDLED;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "packaged-app-cpu-probe";
  return config;
}

static dort_provider_config_t cpu_provider(void) {
  dort_provider_config_t provider;
  memset(&provider, 0, sizeof(provider));
  provider.struct_size = DORT_PROVIDER_CONFIG_V1_SIZE;
  provider.provider_id_utf8 = "cpu";
  return provider;
}

static dort_session_config_t session_config(
    const dort_provider_config_t* provider) {
  dort_session_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_SESSION_CONFIG_V1_SIZE;
  config.graph_optimization_level = DORT_GRAPH_OPTIMIZATION_ALL;
  config.execution_mode = DORT_EXECUTION_SEQUENTIAL;
  config.enable_cpu_memory_arena = 1u;
  config.enable_memory_pattern = 1u;
  config.deterministic_compute = 1u;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "packaged-app-cpu-session";
  config.providers = provider;
  config.provider_count = 1u;
  return config;
}

int main(int argc, char** argv) {
  dort_runtime_t* runtime = NULL;
  dort_session_options_t* options = NULL;
  dort_session_t* session = NULL;
  dort_value_t* input = NULL;
  dort_value_t* output = NULL;
  dort_run_result_t* result = NULL;
  dort_string_t build_manifest;
  dort_string_t output_name;
  dort_runtime_config_t native_runtime_config = runtime_config();
  dort_provider_config_t provider = cpu_provider();
  dort_session_config_t native_session_config = session_config(&provider);
  dort_named_value_t named_input;
  const char* output_names[1] = {"Y"};
  uint8_t* model_bytes = NULL;
  size_t model_size = 0u;
  int64_t shape[2] = {3, 2};
  float input_values[6] = {1.0f, 2.0f, 3.0f, 4.0f, 5.0f, 6.0f};
  float output_values[6] = {0.0f};
  const float expected[6] = {1.0f, 4.0f, 9.0f, 16.0f, 25.0f, 36.0f};
  size_t output_size = 0u;
  size_t index = 0u;

  CHECK(argc == 2, "expected one ONNX model fixture path");
  memset(&build_manifest, 0, sizeof(build_manifest));
  CHECK(
      check_ok(
          dort_get_build_manifest_json(&build_manifest),
          "read packaged build manifest") == 0,
      "packaged build manifest failed");
  CHECK(
      build_manifest.data != NULL && build_manifest.length > 0u &&
          build_manifest.length <= 65535u,
      "packaged build manifest has an invalid size");
  printf(
      "FONIX_BUILD_MANIFEST=%.*s\n",
      (int)build_manifest.length,
      (const char*)build_manifest.data);
  dort_string_release(&build_manifest);
  CHECK(read_file(argv[1], &model_bytes, &model_size) == 0, "model read failed");
  CHECK(
      check_ok(
          dort_runtime_open(&native_runtime_config, &runtime),
          "open packaged bundled runtime") == 0,
      "packaged runtime open failed");
  CHECK(runtime != NULL, "packaged runtime open returned null");
  CHECK(
      check_ok(
          dort_session_options_create(runtime, &native_session_config, &options),
          "create CPU session options") == 0,
      "CPU session options failed");
  CHECK(
      check_ok(
          dort_session_create_from_bytes(
              runtime, options, model_bytes, model_size, &session),
          "create packaged CPU session") == 0,
      "packaged CPU session failed");
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              input_values,
              sizeof(input_values),
              shape,
              2u,
              DORT_TENSOR_FLOAT32,
              &input),
          "create CPU input") == 0,
      "CPU input failed");
  memset(&named_input, 0, sizeof(named_input));
  named_input.struct_size = DORT_NAMED_VALUE_V1_SIZE;
  named_input.name_utf8 = "X";
  named_input.value = input;
  CHECK(
      check_ok(
          dort_session_run(
              session,
              NULL,
              &named_input,
              1u,
              output_names,
              1u,
              &result),
          "run packaged CPU inference") == 0,
      "packaged CPU inference failed");
  CHECK(result != NULL && dort_run_result_count(result) == 1u, "bad result");
  memset(&output_name, 0, sizeof(output_name));
  CHECK(
      check_ok(
          dort_run_result_get(result, 0u, &output_name, &output),
          "read packaged CPU result") == 0,
      "packaged CPU result failed");
  CHECK(strcmp((const char*)output_name.data, "Y") == 0, "wrong output name");
  CHECK(
      check_ok(
          dort_tensor_copy_data(
              output, output_values, sizeof(output_values), &output_size),
          "copy packaged CPU output") == 0,
      "packaged CPU output copy failed");
  CHECK(output_size == sizeof(output_values), "wrong output byte count");
  for (index = 0u; index < 6u; ++index) {
    CHECK(output_values[index] == expected[index], "wrong CPU inference output");
  }

  dort_string_release(&output_name);
  dort_run_result_release(result);
  dort_value_release(input);
  dort_session_release(session);
  dort_session_options_release(options);
  dort_runtime_release(runtime);
  free(model_bytes);
  puts("Fonix packaged macOS CPU inference passed.");
  return 0;
}
