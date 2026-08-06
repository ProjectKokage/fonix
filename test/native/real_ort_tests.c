#if defined(__APPLE__)
#define _DARWIN_C_SOURCE 1
#endif
#define _POSIX_C_SOURCE 200809L

#include "dort.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define CHECK(condition, message)                                                \
  do {                                                                           \
    if (!(condition)) {                                                          \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));      \
      return 1;                                                                  \
    }                                                                            \
  } while (0)

static const uint8_t dynamic_mul_model[] = {
    0x08, 0x03, 0x12, 0x06, 0x63, 0x68, 0x65, 0x6e, 0x74, 0x61, 0x3a, 0x7a,
    0x0a, 0x15, 0x0a, 0x01, 0x58, 0x0a, 0x01, 0x57, 0x12, 0x01, 0x59, 0x1a,
    0x05, 0x6d, 0x75, 0x6c, 0x5f, 0x31, 0x22, 0x03, 0x4d, 0x75, 0x6c, 0x12,
    0x08, 0x6d, 0x75, 0x6c, 0x20, 0x74, 0x65, 0x73, 0x74, 0x2a, 0x23, 0x08,
    0x03, 0x08, 0x02, 0x10, 0x01, 0x22, 0x18, 0x00, 0x00, 0x80, 0x3f, 0x00,
    0x00, 0x00, 0x40, 0x00, 0x00, 0x40, 0x40, 0x00, 0x00, 0x80, 0x40, 0x00,
    0x00, 0xa0, 0x40, 0x00, 0x00, 0xc0, 0x40, 0x42, 0x01, 0x57, 0x5a, 0x18,
    0x0a, 0x01, 0x58, 0x12, 0x13, 0x0a, 0x11, 0x08, 0x01, 0x12, 0x0d, 0x0a,
    0x07, 0x12, 0x05, 0x62, 0x61, 0x74, 0x63, 0x68, 0x0a, 0x02, 0x08, 0x02,
    0x62, 0x18, 0x0a, 0x01, 0x59, 0x12, 0x13, 0x0a, 0x11, 0x08, 0x01, 0x12,
    0x0d, 0x0a, 0x07, 0x12, 0x05, 0x62, 0x61, 0x74, 0x63, 0x68, 0x0a, 0x02,
    0x08, 0x02, 0x42, 0x04, 0x0a, 0x00, 0x10, 0x07,
};

static const uint8_t external_data_model[] = {
    0x08, 0x03, 0x12, 0x06, 0x63, 0x68, 0x65, 0x6e, 0x74, 0x61, 0x3a, 0x71,
    0x0a, 0x15, 0x0a, 0x01, 0x58, 0x0a, 0x01, 0x57, 0x12, 0x01, 0x59, 0x1a,
    0x05, 0x6d, 0x75, 0x6c, 0x5f, 0x31, 0x22, 0x03, 0x4d, 0x75, 0x6c, 0x12,
    0x08, 0x6d, 0x75, 0x6c, 0x20, 0x74, 0x65, 0x73, 0x74, 0x2a, 0x24, 0x08,
    0x03, 0x08, 0x02, 0x10, 0x01, 0x42, 0x01, 0x57, 0x6a, 0x17, 0x0a, 0x08,
    0x6c, 0x6f, 0x63, 0x61, 0x74, 0x69, 0x6f, 0x6e, 0x12, 0x0b, 0x77, 0x65,
    0x69, 0x67, 0x68, 0x74, 0x73, 0x2e, 0x62, 0x69, 0x6e, 0x70, 0x01, 0x5a,
    0x13, 0x0a, 0x01, 0x58, 0x12, 0x0e, 0x0a, 0x0c, 0x08, 0x01, 0x12, 0x08,
    0x0a, 0x02, 0x08, 0x03, 0x0a, 0x02, 0x08, 0x02, 0x62, 0x13, 0x0a, 0x01,
    0x59, 0x12, 0x0e, 0x0a, 0x0c, 0x08, 0x01, 0x12, 0x08, 0x0a, 0x02, 0x08,
    0x03, 0x0a, 0x02, 0x08, 0x02, 0x42, 0x04, 0x0a, 0x00, 0x10, 0x07,
};

static int check_ok(dort_status_t* status, const char* context) {
  if (status == NULL) {
    return 0;
  }
  fprintf(
      stderr,
      "FAIL %s: domain=%u code=%d ort=%d operation=%s message=%s\n",
      context,
      dort_status_domain(status),
      dort_status_code(status),
      dort_status_ort_code(status),
      dort_status_operation(status),
      dort_status_message(status));
  dort_status_release(status);
  return 1;
}

static int expect_code(dort_status_t* status, int32_t code) {
  CHECK(status != NULL, "expected an error status");
  CHECK(dort_status_code(status) == code, "unexpected error code");
  dort_status_release(status);
  return 0;
}

static dort_runtime_config_t runtime_config(
    const char* library,
    const char* root,
    const char* log_id) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = DORT_RUNTIME_SOURCE_FILE;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = log_id;
  config.library_path_utf8 = library;
  config.allowed_root_utf8 = root;
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
    const dort_provider_config_t* provider,
    const char* log_id) {
  dort_session_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_SESSION_CONFIG_V1_SIZE;
  config.graph_optimization_level = DORT_GRAPH_OPTIMIZATION_ALL;
  config.execution_mode = DORT_EXECUTION_SEQUENTIAL;
  config.enable_cpu_memory_arena = 1u;
  config.enable_memory_pattern = 1u;
  config.deterministic_compute = 1u;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = log_id;
  config.providers = provider;
  config.provider_count = 1u;
  return config;
}

static int read_file(const char* path, uint8_t** out_bytes, size_t* out_length) {
  FILE* file = fopen(path, "rb");
  long length = 0;
  uint8_t* bytes = NULL;
  *out_bytes = NULL;
  *out_length = 0u;
  CHECK(file != NULL, "could not open test model fixture");
  CHECK(fseek(file, 0, SEEK_END) == 0, "could not seek model fixture");
  length = ftell(file);
  CHECK(length > 0, "model fixture is empty");
  CHECK(fseek(file, 0, SEEK_SET) == 0, "could not rewind model fixture");
  bytes = (uint8_t*)malloc((size_t)length);
  CHECK(bytes != NULL, "could not allocate model fixture bytes");
  CHECK(
      fread(bytes, 1u, (size_t)length, file) == (size_t)length,
      "could not read model fixture");
  CHECK(fclose(file) == 0, "could not close model fixture");
  *out_bytes = bytes;
  *out_length = (size_t)length;
  return 0;
}

static int test_session_validation(
    dort_runtime_t* runtime,
    const dort_provider_config_t* cpu) {
  dort_session_config_t config = session_config(cpu, "phase2-validation");
  dort_session_options_t* options = (dort_session_options_t*)(uintptr_t)1u;
  dort_status_t* status = NULL;
  dort_provider_config_t invalid_provider = *cpu;
  dort_string_pair_t pair;
  char root_level_artifact[128];

  config.provider_count = 1u;
  config.providers = NULL;
  status = dort_session_options_create(runtime, &config, &options);
  CHECK(options == NULL, "invalid provider array returned options");
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "provider pointer/count assertion failed");

  config = session_config(&invalid_provider, "phase2-validation");
  invalid_provider.provider_id_utf8 = "cuda";
  status = dort_session_options_create(runtime, &config, &options);
  CHECK(options == NULL, "unsupported provider returned options");
  CHECK(
      expect_code(status, DORT_ERROR_PROVIDER_UNSUPPORTED) == 0,
      "unsupported provider assertion failed");

  memset(&pair, 0, sizeof(pair));
  pair.struct_size = DORT_STRING_PAIR_V1_SIZE;
  pair.key_utf8 = "arena_extend_strategy";
  pair.value_utf8 = "kSameAsRequested";
  invalid_provider = *cpu;
  invalid_provider.options = &pair;
  invalid_provider.option_count = 1u;
  config = session_config(&invalid_provider, "phase2-validation");
  status = dort_session_options_create(runtime, &config, &options);
  CHECK(options == NULL, "CPU provider options were accepted");
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "CPU provider-option assertion failed");

  invalid_provider = *cpu;
  invalid_provider.provider_id_utf8 = "qnn";
  config = session_config(&invalid_provider, "phase2-validation");
  status = dort_session_options_create(runtime, &config, &options);
  CHECK(options == NULL, "unavailable QNN provider error was swallowed");
  CHECK(status != NULL, "unavailable QNN provider returned false success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_PROVIDER_UNSUPPORTED,
      "unavailable QNN provider returned the wrong typed code");
  dort_status_release(status);

  config = session_config(cpu, "phase2-validation");
  config.config_entry_count = 1u;
  config.config_entries = NULL;
  status = dort_session_options_create(runtime, &config, &options);
  CHECK(options == NULL, "invalid config-entry array returned options");
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "config-entry pointer/count assertion failed");

  CHECK(
      snprintf(
          root_level_artifact,
          sizeof(root_level_artifact),
          "/fonix_phase2_artifact_path_probe_%ld.onnx",
          (long)getpid()) > 0,
      "could not build root-level artifact probe");
  config = session_config(cpu, "phase2-validation");
  config.optimized_model_path_utf8 = root_level_artifact;
  config.artifact_root_utf8 = "/";
  status = dort_session_options_create(runtime, &config, &options);
  CHECK(
      check_ok(status, "normalize root-level artifact path") == 0,
      "root-level artifact path failed");
  dort_session_options_release(options);
  return 0;
}

static int test_tensor_guards(
    dort_runtime_t* runtime,
    dort_runtime_t* other_runtime) {
  dort_buffer_t* buffer = NULL;
  dort_buffer_t* other_buffer = NULL;
  dort_value_t* value = NULL;
  dort_status_t* status = NULL;
  uint8_t bool_bytes[2] = {0u, 2u};
  uint8_t valid_bool_bytes[2] = {0u, 1u};
  int64_t bool_shape[1] = {2};
  int64_t float_shape[1] = {1};
  int64_t masked_shape[2] = {0, INT64_MAX};
  float one = 1.0f;

  status = dort_tensor_create_copy(
      runtime,
      NULL,
      0u,
      masked_shape,
      2u,
      DORT_TENSOR_FLOAT32,
      &value);
  CHECK(value == NULL, "masked absurd shape returned a value");
  CHECK(
      expect_code(status, DORT_ERROR_LIMIT_EXCEEDED) == 0,
      "masked absurd dimension assertion failed");
  status = dort_tensor_create_copy(
      runtime,
      bool_bytes,
      sizeof(bool_bytes),
      bool_shape,
      1u,
      DORT_TENSOR_BOOL,
      &value);
  CHECK(value == NULL, "invalid Boolean bytes returned a value");
  CHECK(
      expect_code(status, DORT_ERROR_TENSOR_INVALID) == 0,
      "Boolean byte-domain assertion failed");

  status = dort_buffer_allocate(runtime, 16u, 64u, &buffer);
  CHECK(check_ok(status, "allocate tensor guard buffer") == 0, "buffer failed");
  status = dort_buffer_write(buffer, 0u, &one, sizeof(one));
  CHECK(check_ok(status, "write tensor guard buffer") == 0, "write failed");
  status = dort_tensor_create_with_buffer(
      runtime,
      buffer,
      1u,
      sizeof(float),
      float_shape,
      1u,
      DORT_TENSOR_FLOAT32,
      &value);
  CHECK(value == NULL, "misaligned tensor returned a value");
  CHECK(
      expect_code(status, DORT_ERROR_TENSOR_INVALID) == 0,
      "tensor alignment assertion failed");

  status = dort_buffer_write(
      buffer, 0u, valid_bool_bytes, sizeof(valid_bool_bytes));
  CHECK(check_ok(status, "write Boolean buffer") == 0, "bool write failed");
  status = dort_tensor_create_with_buffer(
      runtime,
      buffer,
      0u,
      sizeof(valid_bool_bytes),
      bool_shape,
      1u,
      DORT_TENSOR_BOOL,
      &value);
  CHECK(check_ok(status, "create frozen Boolean tensor") == 0, "bool tensor failed");
  status = dort_buffer_write(buffer, 0u, bool_bytes, sizeof(bool_bytes));
  CHECK(
      expect_code(status, DORT_ERROR_TENSOR_INVALID) == 0,
      "attached native buffer remained mutable");
  dort_value_release(value);
  value = NULL;
  status = dort_buffer_write(buffer, 0u, valid_bool_bytes, sizeof(valid_bool_bytes));
  CHECK(check_ok(status, "write unfrozen Boolean buffer") == 0, "unfreeze failed");

  status = dort_buffer_allocate(other_runtime, 8u, 64u, &other_buffer);
  CHECK(check_ok(status, "allocate other runtime buffer") == 0, "other buffer failed");
  status = dort_tensor_create_with_buffer(
      runtime,
      other_buffer,
      0u,
      sizeof(float),
      float_shape,
      1u,
      DORT_TENSOR_FLOAT32,
      &value);
  CHECK(value == NULL, "cross-runtime buffer returned a value");
  CHECK(
      expect_code(status, DORT_ERROR_RUNTIME_IDENTITY_MISMATCH) == 0,
      "tensor runtime identity assertion failed");
  dort_buffer_release(other_buffer);
  dort_buffer_release(buffer);
  return 0;
}

static int test_file_security(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_path) {
  char temporary_template[] = "/private/tmp/fonix-phase2-XXXXXX";
  char* root = mkdtemp(temporary_template);
  char external_path[512];
  char sidecar_path[512];
  char symlink_path[512];
  FILE* file = NULL;
  dort_session_t* session = (dort_session_t*)(uintptr_t)1u;
  dort_status_t* status = NULL;
  const float weights[6] = {1.0f, 2.0f, 3.0f, 4.0f, 5.0f, 6.0f};
  CHECK(root != NULL, "could not create temporary security root");
  CHECK(
      snprintf(external_path, sizeof(external_path), "%s/external.onnx", root) > 0,
      "could not build external-data model path");
  CHECK(
      snprintf(sidecar_path, sizeof(sidecar_path), "%s/weights.bin", root) > 0,
      "could not build sidecar path");
  CHECK(
      snprintf(symlink_path, sizeof(symlink_path), "%s/link.onnx", root) > 0,
      "could not build symlink path");
  file = fopen(external_path, "wb");
  CHECK(file != NULL, "could not create external-data model");
  CHECK(
      fwrite(external_data_model, 1u, sizeof(external_data_model), file) ==
          sizeof(external_data_model),
      "could not write external-data model");
  CHECK(fclose(file) == 0, "could not close external-data model");
  file = fopen(sidecar_path, "wb");
  CHECK(file != NULL, "could not create external-data sidecar");
  CHECK(
      fwrite(weights, 1u, sizeof(weights), file) == sizeof(weights),
      "could not write external-data sidecar");
  CHECK(fclose(file) == 0, "could not close external-data sidecar");

  status = dort_session_create_from_file(
      runtime, options, external_path, root, &session);
  CHECK(session == NULL, "implicit external-data sidecar was loaded");
  CHECK(status != NULL, "external-data model unexpectedly succeeded");
  CHECK(
      strstr(dort_status_message(status), root) == NULL,
      "model failure exposed its private root path");
  dort_status_release(status);

  CHECK(symlink(fixture_path, symlink_path) == 0, "could not create model symlink");
  status = dort_session_create_from_file(
      runtime, options, symlink_path, root, &session);
  CHECK(session == NULL, "symlink traversal returned a session");
  CHECK(status != NULL, "symlink traversal unexpectedly succeeded");
  dort_status_release(status);

  CHECK(unlink(symlink_path) == 0, "could not remove test symlink");
  CHECK(unlink(sidecar_path) == 0, "could not remove test sidecar");
  CHECK(unlink(external_path) == 0, "could not remove external-data model");
  CHECK(rmdir(root) == 0, "could not remove temporary security root");
  return 0;
}

static int run_once(
    dort_session_t* session,
    dort_run_options_t* run_options,
    dort_value_t* input,
    dort_run_result_t** out_result) {
  dort_named_value_t named_input;
  const char* outputs[1] = {"Y"};
  memset(&named_input, 0, sizeof(named_input));
  named_input.struct_size = DORT_NAMED_VALUE_V1_SIZE;
  named_input.name_utf8 = "X";
  named_input.value = input;
  return check_ok(
      dort_session_run(
          session,
          run_options,
          &named_input,
          1u,
          outputs,
          1u,
          out_result),
      "numeric session run");
}

int main(int argc, char** argv) {
  dort_runtime_t* runtime = NULL;
  dort_runtime_t* other_runtime = NULL;
  dort_session_options_t* options = NULL;
  dort_session_options_t* other_options = NULL;
  dort_session_t* file_session = NULL;
  dort_session_t* session = NULL;
  dort_session_t* dynamic_session = NULL;
  dort_run_options_t* run_options = NULL;
  dort_run_options_t* other_run_options = NULL;
  dort_value_t* input = NULL;
  dort_value_t* wrong_input = NULL;
  dort_value_t* output = NULL;
  dort_run_result_t* result = NULL;
  dort_string_t metadata;
  dort_string_t output_name;
  dort_string_t tensor_info;
  dort_status_t* status = NULL;
  dort_runtime_config_t config;
  dort_runtime_config_t other_config;
  dort_provider_config_t cpu = cpu_provider();
  dort_session_config_t native_session_config =
      session_config(&cpu, "phase2-real-session");
  uint8_t* model_bytes = NULL;
  size_t model_length = 0u;
  int64_t shape[2] = {3, 2};
  int64_t wrong_shape[1] = {1};
  float input_data[6] = {2.0f, 2.0f, 2.0f, 2.0f, 2.0f, 2.0f};
  float output_data[6] = {0.0f};
  float expected[6] = {2.0f, 4.0f, 6.0f, 8.0f, 10.0f, 12.0f};
  size_t required = 0u;
  size_t index = 0u;

  CHECK(argc == 5, "expected real ORT/library root/model/model root paths");
  config = runtime_config(argv[1], argv[2], "phase2-real-runtime-a");
  other_config = runtime_config(argv[1], argv[2], "phase2-real-runtime-b");
  CHECK(
      check_ok(dort_runtime_open(&config, &runtime), "open real runtime") == 0,
      "real runtime open failed");
  CHECK(
      check_ok(
          dort_runtime_open(&other_config, &other_runtime),
          "open other real runtime") == 0,
      "other runtime open failed");
  CHECK(test_session_validation(runtime, &cpu) == 0, "session validation failed");
  CHECK(test_tensor_guards(runtime, other_runtime) == 0, "tensor guards failed");

  CHECK(
      check_ok(
          dort_session_options_create(
              runtime, &native_session_config, &options),
          "create real session options") == 0,
      "session options failed");
  CHECK(
      check_ok(
          dort_session_options_create(
              other_runtime, &native_session_config, &other_options),
          "create other session options") == 0,
      "other session options failed");
  status = dort_session_create_from_bytes(
      runtime, other_options, dynamic_mul_model, sizeof(dynamic_mul_model), &session);
  CHECK(session == NULL, "cross-runtime options returned a session");
  CHECK(
      expect_code(status, DORT_ERROR_RUNTIME_IDENTITY_MISMATCH) == 0,
      "session runtime identity assertion failed");

  CHECK(read_file(argv[3], &model_bytes, &model_length) == 0, "fixture read failed");
  CHECK(
      check_ok(
          dort_session_create_from_file(
              runtime, options, argv[3], argv[4], &file_session),
          "create session from bounded file") == 0,
      "file session failed");
  memset(&metadata, 0, sizeof(metadata));
  CHECK(
      check_ok(
          dort_session_metadata_json(file_session, &metadata),
          "copy static session metadata") == 0,
      "static metadata failed");
  CHECK(
      strcmp(
          (const char*)metadata.data,
          "{\"schemaVersion\":2,\"inputs\":[{\"name\":\"X\",\"kind\":\"tensor\",\"elementType\":1,\"hasShape\":true,\"dimensions\":[3,2],\"symbolicDimensions\":[null,null]}],\"outputs\":[{\"name\":\"Y\",\"kind\":\"tensor\",\"elementType\":1,\"hasShape\":true,\"dimensions\":[3,2],\"symbolicDimensions\":[null,null]}]}") == 0,
      "static metadata JSON schema mismatch");
  dort_string_release(&metadata);
  dort_session_release(file_session);

  CHECK(
      check_ok(
          dort_session_create_from_bytes(
              runtime, options, model_bytes, model_length, &session),
          "create session from bytes") == 0,
      "byte session failed");
  CHECK(
      check_ok(
          dort_session_create_from_bytes(
              runtime,
              options,
              dynamic_mul_model,
              sizeof(dynamic_mul_model),
              &dynamic_session),
          "create dynamic session") == 0,
      "dynamic session failed");
  memset(&metadata, 0, sizeof(metadata));
  CHECK(
      check_ok(
          dort_session_metadata_json(dynamic_session, &metadata),
          "copy dynamic metadata") == 0,
      "dynamic metadata failed");
  CHECK(
      strstr(
          (const char*)metadata.data,
          "\"dimensions\":[null,2],\"symbolicDimensions\":[\"batch\",null]") !=
          NULL,
      "dynamic metadata did not use canonical null representation");
  dort_string_release(&metadata);
  dort_session_release(dynamic_session);
  CHECK(
      test_file_security(runtime, options, argv[3]) == 0,
      "file security tests failed");

  dort_session_options_release(options);
  options = NULL;
  dort_session_options_release(other_options);
  other_options = NULL;

  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              input_data,
              sizeof(input_data),
              shape,
              2u,
              DORT_TENSOR_FLOAT32,
              &input),
          "create numeric input") == 0,
      "numeric input failed");
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              input_data,
              sizeof(float),
              wrong_shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &wrong_input),
          "create wrong-shape input") == 0,
      "wrong input construction failed");
  CHECK(
      check_ok(
          dort_run_options_create(runtime, &run_options),
          "create run options") == 0,
      "run options failed");
  CHECK(
      check_ok(
          dort_run_options_create(other_runtime, &other_run_options),
          "create other run options") == 0,
      "other run options failed");

  {
    dort_named_value_t named_input;
    const char* output_names[1] = {"Y"};
    memset(&named_input, 0, sizeof(named_input));
    named_input.struct_size = DORT_NAMED_VALUE_V1_SIZE;
    named_input.name_utf8 = "X";
    named_input.value = input;
    status = dort_session_run(
        session,
        other_run_options,
        &named_input,
        1u,
        output_names,
        1u,
        &result);
    CHECK(result == NULL, "cross-runtime run options returned a result");
    CHECK(
        expect_code(status, DORT_ERROR_RUNTIME_IDENTITY_MISMATCH) == 0,
        "run-options identity assertion failed");
  }

  CHECK(run_once(session, NULL, wrong_input, &result) != 0, "wrong shape run succeeded");
  CHECK(result == NULL, "failed run returned a result");
  CHECK(
      check_ok(
          dort_run_options_set_terminate(run_options),
          "set run termination") == 0,
      "set terminate failed");
  CHECK(run_once(session, run_options, input, &result) != 0, "terminated run succeeded");
  CHECK(result == NULL, "terminated run returned a result");
  CHECK(
      check_ok(
          dort_run_options_unset_terminate(run_options),
          "unset run termination") == 0,
      "unset terminate failed");
  CHECK(run_once(session, run_options, input, &result) == 0, "numeric run failed");
  CHECK(dort_run_result_count(result) == 1u, "numeric output count mismatch");
  memset(&output_name, 0, sizeof(output_name));
  CHECK(
      check_ok(
          dort_run_result_get(result, 0u, &output_name, &output),
          "get numeric output") == 0,
      "result get failed");
  CHECK(strcmp((const char*)output_name.data, "Y") == 0, "output name mismatch");
  dort_string_release(&output_name);
  {
    dort_string_t invalid_name;
    dort_value_t* invalid_value = (dort_value_t*)(uintptr_t)1u;
    memset(&invalid_name, 0xff, sizeof(invalid_name));
    status = dort_run_result_get(result, 1u, &invalid_name, &invalid_value);
    CHECK(
        expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "invalid result index assertion failed");
    CHECK(
        invalid_name.data == NULL && invalid_name.private_owner == NULL &&
            invalid_value == NULL,
        "failed result get did not initialize both outputs");
  }
  dort_run_result_retain(result);
  dort_run_result_release(result);
  dort_run_result_release(result);
  result = NULL;

  memset(&tensor_info, 0, sizeof(tensor_info));
  CHECK(
      check_ok(dort_tensor_info_json(output, &tensor_info), "output tensor info") == 0,
      "tensor info failed");
  CHECK(
      strcmp(
          (const char*)tensor_info.data,
          "{\"schemaVersion\":1,\"kind\":\"tensor\",\"elementType\":1,\"dimensions\":[3,2],\"byteLength\":24}") == 0,
      "tensor info JSON mismatch");
  dort_string_release(&tensor_info);
  CHECK(
      check_ok(
          dort_tensor_copy_data(output, NULL, 0u, &required),
          "query output tensor bytes") == 0,
      "tensor byte query failed");
  CHECK(required == sizeof(output_data), "tensor byte query mismatch");
  status = dort_tensor_copy_data(
      output, output_data, sizeof(output_data) - 1u, &required);
  CHECK(
      expect_code(status, DORT_ERROR_BUFFER_TOO_SMALL) == 0,
      "small output buffer assertion failed");
  CHECK(
      check_ok(
          dort_tensor_copy_data(
              output, output_data, sizeof(output_data), &required),
          "copy output tensor bytes") == 0,
      "tensor copy failed");
  for (index = 0u; index < 6u; ++index) {
    CHECK(output_data[index] == expected[index], "numeric output mismatch");
  }

  dort_value_release(wrong_input);
  dort_value_release(input);
  dort_run_options_release(other_run_options);
  dort_run_options_release(run_options);
  dort_session_release(session);
  free(model_bytes);
  dort_runtime_release(runtime);
  dort_runtime_release(other_runtime);
  CHECK(
      check_ok(
          dort_tensor_copy_data(
              output, output_data, sizeof(output_data), &required),
          "copy output after parent release") == 0,
      "output did not retain runtime after parent release");
  dort_value_release(output);
  printf("Fonix Phase 2 real ORT CPU tests passed.\n");
  return 0;
}
