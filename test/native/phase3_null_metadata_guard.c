#include "dort.h"

#include <stdint.h>
#include <stdio.h>
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

#define CHECK(condition, message)                                               \
  do {                                                                          \
    if (!(condition)) {                                                         \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));    \
      result = 1;                                                               \
      goto cleanup;                                                             \
    }                                                                           \
  } while (0)

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
  config.log_id_utf8 = "phase3-null-metadata";
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
  config.log_id_utf8 = "phase3-null-metadata";
  return config;
}

int main(int argc, char** argv) {
  dort_runtime_t* runtime = NULL;
  dort_session_options_t* options = NULL;
  dort_session_t* session = NULL;
  dort_value_t* tensor = NULL;
  dort_data_lease_t* lease = NULL;
  dort_status_t* status = NULL;
  dort_runtime_config_t native_runtime_config;
  dort_session_config_t native_session_config = session_config();
  uint8_t model_data[1] = {0u};
  const float tensor_data[1] = {1.0f};
  int64_t tensor_shape[1] = {1};
  float copied_data[1] = {0.0f};
  const void* leased_data = (const void*)(uintptr_t)1u;
  size_t byte_length = SIZE_MAX;
  int result = 0;

  CHECK(argc == 3, "expected fake runtime library and root paths");
  native_runtime_config = runtime_config(argv[1], argv[2]);

  status = dort_runtime_open(&native_runtime_config, &runtime);
  CHECK(status == NULL && runtime != NULL, "could not open fake runtime");

  status = dort_tensor_create_copy(
      runtime,
      tensor_data,
      sizeof(tensor_data),
      tensor_shape,
      1u,
      DORT_TENSOR_FLOAT32,
      &tensor);
  CHECK(status == NULL && tensor != NULL, "could not create fake device tensor");
  status = dort_tensor_copy_data(
      tensor, copied_data, sizeof(copied_data), &byte_length);
  if (status == NULL ||
      dort_status_code(status) != DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED) {
    fprintf(
        stderr,
        "unexpected device-copy status: code=%d operation=%s message=%s\n",
        dort_status_code(status),
        dort_status_operation(status),
        dort_status_message(status));
  }
  CHECK(
      status != NULL &&
          dort_status_code(status) == DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED,
      "device tensor copy did not fail before dereferencing device storage");
  CHECK(byte_length == sizeof(float), "device tensor byte length was not reported");
  dort_status_release(status);
  status = NULL;
  byte_length = SIZE_MAX;
  status = dort_tensor_data_acquire(
      tensor, &lease, &leased_data, &byte_length);
  CHECK(
      status != NULL &&
          dort_status_code(status) == DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED,
      "device tensor lease returned host storage");
  CHECK(
      lease == NULL && leased_data == NULL && byte_length == 0u,
      "failed device tensor lease did not clear outputs");
  dort_status_release(status);
  status = NULL;
  dort_value_release(tensor);
  tensor = NULL;

  status = dort_session_options_create(
      runtime, &native_session_config, &options);
  CHECK(
      status == NULL && options != NULL,
      "could not create fake session options");

  model_data[0] = FAKE_SESSION_METADATA_ERROR_WITH_OWNER;
  session = (dort_session_t*)(uintptr_t)1u;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(session == NULL, "model-metadata owner error returned a session");
  CHECK(status != NULL, "model-metadata owner error returned success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_MODEL_INVALID,
      "model-metadata owner error returned the wrong error code");
  dort_status_release(status);
  status = NULL;

  model_data[0] = FAKE_SESSION_VERIFY_METADATA_OWNER;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(
      status == NULL && session != NULL,
      "model metadata was not released after an error with an owner");
  dort_session_release(session);
  session = NULL;

  model_data[0] = FAKE_SESSION_METADATA_KEYS_ERROR_WITH_OWNERS;
  session = (dort_session_t*)(uintptr_t)1u;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(session == NULL, "metadata-key owner error returned a session");
  CHECK(status != NULL, "metadata-key owner error returned success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_MODEL_INVALID,
      "metadata-key owner error returned the wrong error code");
  dort_status_release(status);
  status = NULL;

  model_data[0] = FAKE_SESSION_VERIFY_METADATA_KEY_OWNERS;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(
      status == NULL && session != NULL,
      "metadata keys were not released after an error with partial owners");
  dort_session_release(session);
  session = NULL;

  model_data[0] = FAKE_SESSION_OVERSIZED_METADATA_KEY_COUNT;
  session = (dort_session_t*)(uintptr_t)1u;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(session == NULL, "oversized metadata-key count returned a session");
  CHECK(status != NULL, "oversized metadata-key count returned success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_LIMIT_EXCEEDED,
      "oversized metadata-key count returned the wrong error code");
  dort_status_release(status);
  status = NULL;

  model_data[0] = FAKE_SESSION_VERIFY_METADATA_KEY_OWNERS;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(
      status == NULL && session != NULL,
      "oversized metadata-key cleanup retained its outer owner");
  dort_session_release(session);
  session = NULL;

  model_data[0] = FAKE_SESSION_OUTPUT_COUNT_ERROR;
  session = (dort_session_t*)(uintptr_t)1u;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(session == NULL, "output-count failure returned a session");
  CHECK(status != NULL, "output-count failure returned success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_MODEL_INVALID,
      "output-count failure returned the wrong error code");
  dort_status_release(status);
  status = NULL;

  model_data[0] = FAKE_SESSION_OVERSIZED_INPUT_COUNT;
  session = (dort_session_t*)(uintptr_t)1u;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(session == NULL, "oversized input count returned a session");
  CHECK(status != NULL, "oversized input count returned success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_LIMIT_EXCEEDED,
      "oversized input count returned the wrong error code");
  dort_status_release(status);
  status = NULL;

  model_data[0] = FAKE_SESSION_OVERSIZED_OUTPUT_COUNT;
  session = (dort_session_t*)(uintptr_t)1u;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(session == NULL, "oversized output count returned a session");
  CHECK(status != NULL, "oversized output count returned success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_LIMIT_EXCEEDED,
      "oversized output count returned the wrong error code");
  dort_status_release(status);
  status = NULL;

  model_data[0] = FAKE_SESSION_NULL_METADATA;
  status = dort_session_create_from_bytes(
      runtime, options, model_data, sizeof(model_data), &session);
  CHECK(session == NULL, "invalid null model metadata returned a session");
  CHECK(status != NULL, "invalid null model metadata returned success");
  CHECK(
      dort_status_code(status) == DORT_ERROR_MODEL_INVALID,
      "null model metadata returned the wrong error code");

cleanup:
  dort_status_release(status);
  dort_data_lease_release(lease);
  dort_value_release(tensor);
  dort_session_release(session);
  dort_session_options_release(options);
  dort_runtime_release(runtime);
  if (result == 0) {
    printf("Fonix Phase 3 null-metadata guard passed.\n");
  }
  return result;
}
