#include "dort.h"

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

static dort_session_config_t session_config(void) {
  dort_session_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_SESSION_CONFIG_V1_SIZE;
  config.graph_optimization_level = DORT_GRAPH_OPTIMIZATION_ALL;
  config.execution_mode = DORT_EXECUTION_SEQUENTIAL;
  config.enable_cpu_memory_arena = 1u;
  config.enable_memory_pattern = 1u;
  config.deterministic_compute = 1u;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "phase2-incomplete-api";
  return config;
}

static int expect_code(dort_status_t* status, int32_t code) {
  CHECK(status != NULL, "expected an error status");
  CHECK(dort_status_code(status) == code, "unexpected error code");
  dort_status_release(status);
  return 0;
}

static int test_partial_output_neutralization(void) {
  dort_data_lease_t* lease = NULL;
  void* mutable_data = NULL;
  const void* const_data = NULL;
  size_t byte_length = 0u;
  dort_string_t name;
  dort_value_t* value = NULL;
  dort_status_t* status = NULL;

  mutable_data = (void*)(uintptr_t)1u;
  byte_length = 1u;
  status = dort_buffer_data_acquire(NULL, NULL, &mutable_data, &byte_length);
  CHECK(mutable_data == NULL && byte_length == 0u,
        "buffer acquire did not neutralize data outputs");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "buffer acquire null-lease assertion failed");

  lease = (dort_data_lease_t*)(uintptr_t)1u;
  byte_length = 1u;
  status = dort_buffer_data_acquire(NULL, &lease, NULL, &byte_length);
  CHECK(lease == NULL && byte_length == 0u,
        "buffer acquire did not neutralize lease/length outputs");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "buffer acquire null-data assertion failed");

  lease = (dort_data_lease_t*)(uintptr_t)1u;
  mutable_data = (void*)(uintptr_t)1u;
  status = dort_buffer_data_acquire(NULL, &lease, &mutable_data, NULL);
  CHECK(lease == NULL && mutable_data == NULL,
        "buffer acquire did not neutralize lease/data outputs");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "buffer acquire null-length assertion failed");

  const_data = (const void*)(uintptr_t)1u;
  byte_length = 1u;
  status = dort_tensor_data_acquire(NULL, NULL, &const_data, &byte_length);
  CHECK(const_data == NULL && byte_length == 0u,
        "tensor acquire did not neutralize data outputs");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "tensor acquire null-lease assertion failed");

  lease = (dort_data_lease_t*)(uintptr_t)1u;
  byte_length = 1u;
  status = dort_tensor_data_acquire(NULL, &lease, NULL, &byte_length);
  CHECK(lease == NULL && byte_length == 0u,
        "tensor acquire did not neutralize lease/length outputs");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "tensor acquire null-data assertion failed");

  lease = (dort_data_lease_t*)(uintptr_t)1u;
  const_data = (const void*)(uintptr_t)1u;
  status = dort_tensor_data_acquire(NULL, &lease, &const_data, NULL);
  CHECK(lease == NULL && const_data == NULL,
        "tensor acquire did not neutralize lease/data outputs");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "tensor acquire null-length assertion failed");

  value = (dort_value_t*)(uintptr_t)1u;
  status = dort_run_result_get(NULL, 0u, NULL, &value);
  CHECK(value == NULL, "run-result get did not neutralize the value output");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "run-result get null-name assertion failed");

  memset(&name, 0xff, sizeof(name));
  status = dort_run_result_get(NULL, 0u, &name, NULL);
  CHECK(name.struct_size == (uint32_t)sizeof(name) && name.data == NULL &&
            name.length == 0u && name.private_owner == NULL,
        "run-result get did not neutralize the name output");
  CHECK(expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
        "run-result get null-value assertion failed");
  return 0;
}

int main(int argc, char** argv) {
  dort_runtime_t* first = NULL;
  dort_runtime_t* second = NULL;
  dort_buffer_t* buffer = NULL;
  dort_buffer_t* invalid_buffer = (dort_buffer_t*)(uintptr_t)1u;
  dort_value_t* value = (dort_value_t*)(uintptr_t)1u;
  dort_session_options_t* options = (dort_session_options_t*)(uintptr_t)1u;
  dort_status_t* status = NULL;
  dort_runtime_config_t first_config;
  dort_runtime_config_t second_config;
  dort_session_config_t native_session_config = session_config();
  int64_t absurd_zero_shape[2] = {0, INT64_MAX};
  int64_t scalar_shape[1] = {1};
  uint8_t bytes[16] = {0u};

  CHECK(argc == 3, "expected fake runtime library and root paths");
  CHECK(test_partial_output_neutralization() == 0,
        "partial output neutralization assertions failed");
  first_config = runtime_config(argv[1], argv[2], "phase2-identity-a");
  second_config = runtime_config(argv[1], argv[2], "phase2-identity-b");
  status = dort_runtime_open(&first_config, &first);
  CHECK(status == NULL && first != NULL, "could not open first fake runtime");
  status = dort_runtime_open(&second_config, &second);
  CHECK(status == NULL && second != NULL, "could not open second fake runtime");

  status = dort_session_options_create(
      first, &native_session_config, &options);
  CHECK(options == NULL, "incomplete API returned session options");
  CHECK(
      expect_code(status, DORT_ERROR_ORT_API_UNAVAILABLE) == 0,
      "incomplete API assertion failed");

  status = dort_buffer_allocate(first, sizeof(bytes), 64u, &buffer);
  CHECK(status == NULL && buffer != NULL, "buffer allocation failed");
  CHECK(dort_buffer_byte_length(buffer) == sizeof(bytes), "buffer length mismatch");
  status = dort_buffer_write(buffer, sizeof(bytes), bytes, 1u);
  CHECK(
      expect_code(status, DORT_ERROR_LIMIT_EXCEEDED) == 0,
      "buffer bounds assertion failed");
  status = dort_buffer_allocate(first, 8u, 3u, &invalid_buffer);
  CHECK(invalid_buffer == NULL, "invalid alignment returned a buffer");
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "invalid alignment assertion failed");

  status = dort_tensor_create_with_buffer(
      second,
      buffer,
      0u,
      sizeof(float),
      scalar_shape,
      1u,
      DORT_TENSOR_FLOAT32,
      &value);
  CHECK(value == NULL, "identity mismatch returned a tensor");
  CHECK(
      expect_code(status, DORT_ERROR_RUNTIME_IDENTITY_MISMATCH) == 0,
      "buffer identity assertion failed");
  status = dort_tensor_create_copy(
      first,
      NULL,
      0u,
      absurd_zero_shape,
      2u,
      DORT_TENSOR_FLOAT32,
      &value);
  CHECK(value == NULL, "absurd zero shape returned a tensor");
  CHECK(
      expect_code(status, DORT_ERROR_LIMIT_EXCEEDED) == 0,
      "zero dimension masked a later oversized dimension");
  {
    float one = 1.0f;
    status = dort_tensor_create_copy(
        first,
        &one,
        sizeof(one),
        scalar_shape,
        1u,
        DORT_TENSOR_FLOAT32,
        &value);
    CHECK(value == NULL, "incomplete tensor API returned a value");
    CHECK(
        expect_code(status, DORT_ERROR_ORT_API_UNAVAILABLE) == 0,
        "incomplete tensor API assertion failed");
  }

  dort_runtime_release(first);
  first = NULL;
  status = dort_buffer_write(buffer, 0u, bytes, sizeof(bytes));
  CHECK(status == NULL, "buffer did not retain its parent runtime");
  dort_buffer_release(buffer);
  dort_runtime_release(second);

  dort_session_options_retain(NULL);
  dort_session_options_release(NULL);
  dort_session_retain(NULL);
  dort_session_release(NULL);
  dort_buffer_retain(NULL);
  dort_buffer_release(NULL);
  dort_value_retain(NULL);
  dort_value_release(NULL);
  dort_run_options_retain(NULL);
  dort_run_options_release(NULL);
  dort_run_result_retain(NULL);
  dort_run_result_release(NULL);
  CHECK(dort_buffer_byte_length(NULL) == 0u, "null buffer length was not neutral");
  CHECK(dort_run_result_count(NULL) == 0u, "null result count was not neutral");
  printf("Fonix Phase 2 guard tests passed.\n");
  return 0;
}
