#define _POSIX_C_SOURCE 200809L
#if defined(__APPLE__)
#define _DARWIN_C_SOURCE 1
#endif

#include "dort.h"

#include <dirent.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_FAULT_POINT 256u
#define EXPECTED_TOTAL_ALLOCATION_FAULTS 76u

#define CHECK(condition, message)                                              \
  do {                                                                         \
    if (!(condition)) {                                                        \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));      \
      return 1;                                                                \
    }                                                                          \
  } while (0)

typedef dort_status_t* (*fault_invoke_fn)(void* context);
typedef int (*fault_state_fn)(const void* context);
typedef void (*fault_cleanup_fn)(void* context);
typedef void (*fault_iteration_fn)(void* context, size_t fault_point);

typedef struct fault_case {
  const char* name;
  void* context;
  fault_invoke_fn invoke;
  fault_state_fn is_neutral;
  fault_state_fn is_success;
  fault_cleanup_fn cleanup;
  fault_iteration_fn set_iteration;
  size_t expected_allocation_count;
} fault_case_t;

typedef struct string_context {
  dort_string_t output;
} string_context_t;

typedef struct runtime_context {
  dort_runtime_config_t config;
  dort_runtime_t* output;
  char log_id[64];
} runtime_context_t;

typedef struct provider_context {
  dort_runtime_t* runtime;
  dort_string_t output;
} provider_context_t;

typedef struct options_context {
  dort_runtime_t* runtime;
  dort_session_config_t config;
  dort_session_options_t* output;
} options_context_t;

typedef struct session_context {
  dort_runtime_t* runtime;
  dort_session_options_t* options;
  dort_session_t* output;
  uint8_t model_data[1];
} session_context_t;

typedef struct tensor_context {
  dort_runtime_t* runtime;
  dort_value_t* output;
  float value;
  int64_t shape[1];
} tensor_context_t;

typedef struct sequence_context {
  dort_runtime_t* runtime;
  dort_value_t* children[2];
  dort_value_t* output;
} sequence_context_t;

typedef struct external_data_context {
  dort_runtime_t* runtime;
  dort_session_options_t* options;
  dort_external_data_t entries[2];
  uint8_t model_data[1];
  uint8_t first_data[2];
  uint8_t second_data[3];
  dort_session_t* output;
} external_data_context_t;

typedef struct profile_context {
  dort_run_options_t* options;
  const char* root;
  dort_string_t output;
} profile_context_t;

typedef struct run_options_context {
  dort_runtime_t* runtime;
  dort_run_options_t* output;
} run_options_context_t;

typedef struct run_context {
  dort_session_t* session;
  dort_named_value_t input;
  const char* output_names[1];
  dort_run_result_t* output;
} run_context_t;

typedef struct run_result_get_context {
  dort_run_result_t* result;
  dort_string_t name;
  dort_value_t* value;
} run_result_get_context_t;

typedef struct cancel_register_context {
  dort_run_options_t* options;
  uint64_t token;
  int cleanup_ok;
} cancel_register_context_t;

typedef struct buffer_lease_context {
  dort_buffer_t* buffer;
  dort_data_lease_t* lease;
  void* data;
  size_t byte_length;
} buffer_lease_context_t;

typedef struct tensor_lease_context {
  dort_value_t* value;
  dort_data_lease_t* lease;
  const void* data;
  size_t byte_length;
} tensor_lease_context_t;

typedef struct tensor_info_context {
  dort_value_t* value;
  dort_string_t output;
} tensor_info_context_t;

static uint64_t fault_epoch = 0u;

static int configure_fault(size_t fault_point) {
  char epoch[32];
  char fail_at[32];
  int epoch_length = 0;
  int fail_length = 0;

  ++fault_epoch;
  epoch_length =
      snprintf(epoch, sizeof(epoch), "%llu", (unsigned long long)fault_epoch);
  fail_length = snprintf(fail_at, sizeof(fail_at), "%zu", fault_point);
  if (epoch_length <= 0 || (size_t)epoch_length >= sizeof(epoch) ||
      fail_length <= 0 || (size_t)fail_length >= sizeof(fail_at)) {
    return 0;
  }
  return setenv("FONIX_TEST_ALLOCATION_EPOCH", epoch, 1) == 0 &&
         setenv("FONIX_TEST_ALLOCATION_FAIL_AT", fail_at, 1) == 0;
}

static int is_allocation_status(const dort_status_t* status) {
  return status != NULL &&
         dort_status_domain(status) == DORT_ERROR_DOMAIN_ALLOCATION &&
         dort_status_code(status) == DORT_ERROR_ALLOCATION_FAILED &&
         dort_status_operation(status)[0] != '\0' &&
         dort_status_message(status)[0] != '\0';
}

static int exercise_fault_case(const fault_case_t* test_case) {
  size_t fault_point = 0u;
  for (fault_point = 1u; fault_point <= MAX_FAULT_POINT; ++fault_point) {
    dort_status_t* status = NULL;
    if (test_case->set_iteration != NULL) {
      test_case->set_iteration(test_case->context, fault_point);
    }
    CHECK(configure_fault(fault_point), "could not configure allocation fault");
    status = test_case->invoke(test_case->context);
    if (status == NULL) {
      CHECK(test_case->is_success(test_case->context),
            "fault sentinel returned malformed success output");
      test_case->cleanup(test_case->context);
      CHECK(fault_point > 1u, "operation did not exercise an allocation");
      CHECK(fault_point - 1u == test_case->expected_allocation_count,
            "operation allocation count changed unexpectedly");
      printf("%s: %zu success-path allocation faults verified.\n",
             test_case->name, fault_point - 1u);
      return 0;
    }

    CHECK(test_case->is_neutral(test_case->context),
          "allocation failure published a partial output");
    CHECK(is_allocation_status(status),
          "allocation failure returned a non-allocation status");
    dort_status_release(status);
    test_case->cleanup(test_case->context);

    CHECK(configure_fault(0u), "could not disable allocation fault");
    status = test_case->invoke(test_case->context);
    CHECK(status == NULL, "clean retry failed after allocation fault");
    CHECK(test_case->is_success(test_case->context),
          "clean retry returned malformed output");
    test_case->cleanup(test_case->context);
  }
  CHECK(0, "allocation fault enumeration exceeded its bound");
}

static dort_runtime_config_t
runtime_config(const char* library, const char* root, const char* log_id) {
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
  config.log_id_utf8 = "allocation-fault-session";
  return config;
}

static dort_status_t* invoke_manifest(void* opaque_context) {
  string_context_t* context = (string_context_t*)opaque_context;
  memset(&context->output, 0xff, sizeof(context->output));
  return dort_get_build_manifest_json(&context->output);
}

static int string_is_neutral(const void* opaque_context) {
  const string_context_t* context = (const string_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data == NULL && context->output.length == 0u &&
         context->output.private_owner == NULL;
}

static int string_is_success(const void* opaque_context) {
  const string_context_t* context = (const string_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data != NULL && context->output.length != 0u &&
         context->output.private_owner != NULL;
}

static void string_cleanup(void* opaque_context) {
  string_context_t* context = (string_context_t*)opaque_context;
  dort_string_release(&context->output);
}

static void set_runtime_iteration(void* opaque_context, size_t fault_point) {
  runtime_context_t* context = (runtime_context_t*)opaque_context;
  (void)snprintf(context->log_id, sizeof(context->log_id),
                 "allocation-fault-runtime-%zu", fault_point);
  context->config.log_id_utf8 = context->log_id;
}

static dort_status_t* invoke_runtime_open(void* opaque_context) {
  runtime_context_t* context = (runtime_context_t*)opaque_context;
  context->output = (dort_runtime_t*)(uintptr_t)1u;
  return dort_runtime_open(&context->config, &context->output);
}

static int runtime_is_neutral(const void* opaque_context) {
  const runtime_context_t* context = (const runtime_context_t*)opaque_context;
  return context->output == NULL;
}

static int runtime_is_success(const void* opaque_context) {
  const runtime_context_t* context = (const runtime_context_t*)opaque_context;
  return context->output != NULL;
}

static void runtime_cleanup(void* opaque_context) {
  runtime_context_t* context = (runtime_context_t*)opaque_context;
  dort_runtime_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_providers(void* opaque_context) {
  provider_context_t* context = (provider_context_t*)opaque_context;
  memset(&context->output, 0xff, sizeof(context->output));
  return dort_runtime_available_providers_json(context->runtime,
                                               &context->output);
}

static int provider_is_neutral(const void* opaque_context) {
  const provider_context_t* context = (const provider_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data == NULL && context->output.length == 0u &&
         context->output.private_owner == NULL;
}

static int provider_is_success(const void* opaque_context) {
  const provider_context_t* context = (const provider_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data != NULL && context->output.length != 0u &&
         context->output.private_owner != NULL;
}

static void provider_cleanup(void* opaque_context) {
  provider_context_t* context = (provider_context_t*)opaque_context;
  dort_string_release(&context->output);
}

static dort_status_t* invoke_options(void* opaque_context) {
  options_context_t* context = (options_context_t*)opaque_context;
  context->output = (dort_session_options_t*)(uintptr_t)1u;
  return dort_session_options_create(context->runtime, &context->config,
                                     &context->output);
}

static int options_is_neutral(const void* opaque_context) {
  const options_context_t* context = (const options_context_t*)opaque_context;
  return context->output == NULL;
}

static int options_is_success(const void* opaque_context) {
  const options_context_t* context = (const options_context_t*)opaque_context;
  return context->output != NULL;
}

static void options_cleanup(void* opaque_context) {
  options_context_t* context = (options_context_t*)opaque_context;
  dort_session_options_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_session(void* opaque_context) {
  session_context_t* context = (session_context_t*)opaque_context;
  context->output = (dort_session_t*)(uintptr_t)1u;
  return dort_session_create_from_bytes(
      context->runtime, context->options, context->model_data,
      sizeof(context->model_data), &context->output);
}

static int session_is_neutral(const void* opaque_context) {
  const session_context_t* context = (const session_context_t*)opaque_context;
  return context->output == NULL;
}

static int session_is_success(const void* opaque_context) {
  const session_context_t* context = (const session_context_t*)opaque_context;
  return context->output != NULL;
}

static void session_cleanup(void* opaque_context) {
  session_context_t* context = (session_context_t*)opaque_context;
  dort_session_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_tensor(void* opaque_context) {
  tensor_context_t* context = (tensor_context_t*)opaque_context;
  context->output = (dort_value_t*)(uintptr_t)1u;
  return dort_tensor_create_copy(context->runtime, &context->value,
                                 sizeof(context->value), context->shape, 1u,
                                 DORT_TENSOR_FLOAT32, &context->output);
}

static int tensor_is_neutral(const void* opaque_context) {
  const tensor_context_t* context = (const tensor_context_t*)opaque_context;
  return context->output == NULL;
}

static int tensor_is_success(const void* opaque_context) {
  const tensor_context_t* context = (const tensor_context_t*)opaque_context;
  return context->output != NULL;
}

static void tensor_cleanup(void* opaque_context) {
  tensor_context_t* context = (tensor_context_t*)opaque_context;
  dort_value_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_sequence(void* opaque_context) {
  sequence_context_t* context = (sequence_context_t*)opaque_context;
  const dort_value_t* children[2] = {context->children[0],
                                     context->children[1]};
  context->output = (dort_value_t*)(uintptr_t)1u;
  return dort_sequence_create(context->runtime, children, 2u,
                              &context->output);
}

static int sequence_is_neutral(const void* opaque_context) {
  const sequence_context_t* context =
      (const sequence_context_t*)opaque_context;
  return context->output == NULL;
}

static int sequence_is_success(const void* opaque_context) {
  const sequence_context_t* context =
      (const sequence_context_t*)opaque_context;
  return context->output != NULL;
}

static void sequence_cleanup(void* opaque_context) {
  sequence_context_t* context = (sequence_context_t*)opaque_context;
  dort_value_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_external_data(void* opaque_context) {
  external_data_context_t* context =
      (external_data_context_t*)opaque_context;
  context->output = (dort_session_t*)(uintptr_t)1u;
  return dort_session_create_from_bytes_with_external_data(
      context->runtime, context->options, context->model_data,
      sizeof(context->model_data), context->entries, 2u, &context->output);
}

static int external_data_is_neutral(const void* opaque_context) {
  const external_data_context_t* context =
      (const external_data_context_t*)opaque_context;
  return context->output == NULL;
}

static int external_data_is_success(const void* opaque_context) {
  const external_data_context_t* context =
      (const external_data_context_t*)opaque_context;
  return context->output != NULL;
}

static void external_data_cleanup(void* opaque_context) {
  external_data_context_t* context =
      (external_data_context_t*)opaque_context;
  dort_session_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_run_options(void* opaque_context) {
  run_options_context_t* context = (run_options_context_t*)opaque_context;
  context->output = (dort_run_options_t*)(uintptr_t)1u;
  return dort_run_options_create(context->runtime, &context->output);
}

static int run_options_is_neutral(const void* opaque_context) {
  const run_options_context_t* context =
      (const run_options_context_t*)opaque_context;
  return context->output == NULL;
}

static int run_options_is_success(const void* opaque_context) {
  const run_options_context_t* context =
      (const run_options_context_t*)opaque_context;
  return context->output != NULL;
}

static void run_options_cleanup(void* opaque_context) {
  run_options_context_t* context = (run_options_context_t*)opaque_context;
  dort_run_options_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_run(void* opaque_context) {
  run_context_t* context = (run_context_t*)opaque_context;
  context->output = (dort_run_result_t*)(uintptr_t)1u;
  return dort_session_run(context->session, NULL, &context->input, 1u,
                          context->output_names, 1u, &context->output);
}

static int run_is_neutral(const void* opaque_context) {
  const run_context_t* context = (const run_context_t*)opaque_context;
  return context->output == NULL;
}

static int run_is_success(const void* opaque_context) {
  const run_context_t* context = (const run_context_t*)opaque_context;
  return context->output != NULL &&
         dort_run_result_count(context->output) == 1u;
}

static void run_cleanup(void* opaque_context) {
  run_context_t* context = (run_context_t*)opaque_context;
  dort_run_result_release(context->output);
  context->output = NULL;
}

static dort_status_t* invoke_run_result_get(void* opaque_context) {
  run_result_get_context_t* context =
      (run_result_get_context_t*)opaque_context;
  memset(&context->name, 0xff, sizeof(context->name));
  context->value = (dort_value_t*)(uintptr_t)1u;
  return dort_run_result_get(
      context->result, 0u, &context->name, &context->value);
}

static int run_result_get_is_neutral(const void* opaque_context) {
  const run_result_get_context_t* context =
      (const run_result_get_context_t*)opaque_context;
  return context->name.struct_size == sizeof(context->name) &&
         context->name.data == NULL && context->name.length == 0u &&
         context->name.private_owner == NULL && context->value == NULL;
}

static int run_result_get_is_success(const void* opaque_context) {
  const run_result_get_context_t* context =
      (const run_result_get_context_t*)opaque_context;
  return context->name.struct_size == sizeof(context->name) &&
         context->name.data != NULL && context->name.length == 1u &&
         context->name.private_owner != NULL &&
         memcmp(context->name.data, "Y", 1u) == 0 && context->value != NULL;
}

static void run_result_get_cleanup(void* opaque_context) {
  run_result_get_context_t* context =
      (run_result_get_context_t*)opaque_context;
  dort_value_release(context->value);
  context->value = NULL;
  dort_string_release(&context->name);
}

static dort_status_t* invoke_cancel_register(void* opaque_context) {
  cancel_register_context_t* context =
      (cancel_register_context_t*)opaque_context;
  context->token = UINT64_MAX;
  return dort_cancel_token_register(context->options, &context->token);
}

static int cancel_register_is_neutral(const void* opaque_context) {
  const cancel_register_context_t* context =
      (const cancel_register_context_t*)opaque_context;
  return context->token == 0u && context->cleanup_ok;
}

static int cancel_register_is_success(const void* opaque_context) {
  const cancel_register_context_t* context =
      (const cancel_register_context_t*)opaque_context;
  return context->token != 0u && context->token != UINT64_MAX &&
         context->cleanup_ok;
}

static void cancel_register_cleanup(void* opaque_context) {
  cancel_register_context_t* context =
      (cancel_register_context_t*)opaque_context;
  if (context->token != 0u && context->token != UINT64_MAX) {
    uint32_t was_requested = UINT32_MAX;
    dort_status_t* status =
        dort_cancel_token_finish(context->token, &was_requested);
    if (status != NULL || was_requested != 0u) {
      context->cleanup_ok = 0;
    }
    dort_status_release(status);
  }
  context->token = 0u;
}

static dort_status_t* invoke_buffer_lease(void* opaque_context) {
  buffer_lease_context_t* context =
      (buffer_lease_context_t*)opaque_context;
  context->lease = (dort_data_lease_t*)(uintptr_t)1u;
  context->data = (void*)(uintptr_t)1u;
  context->byte_length = SIZE_MAX;
  return dort_buffer_data_acquire(context->buffer, &context->lease,
                                  &context->data, &context->byte_length);
}

static int buffer_lease_is_neutral(const void* opaque_context) {
  const buffer_lease_context_t* context =
      (const buffer_lease_context_t*)opaque_context;
  return context->lease == NULL && context->data == NULL &&
         context->byte_length == 0u;
}

static int buffer_lease_is_success(const void* opaque_context) {
  const buffer_lease_context_t* context =
      (const buffer_lease_context_t*)opaque_context;
  return context->lease != NULL && context->data != NULL &&
         context->byte_length == sizeof(float);
}

static void buffer_lease_cleanup(void* opaque_context) {
  buffer_lease_context_t* context =
      (buffer_lease_context_t*)opaque_context;
  dort_data_lease_release(context->lease);
  context->lease = NULL;
  context->data = NULL;
  context->byte_length = 0u;
}

static dort_status_t* invoke_tensor_lease(void* opaque_context) {
  tensor_lease_context_t* context =
      (tensor_lease_context_t*)opaque_context;
  context->lease = (dort_data_lease_t*)(uintptr_t)1u;
  context->data = (const void*)(uintptr_t)1u;
  context->byte_length = SIZE_MAX;
  return dort_tensor_data_acquire(context->value, &context->lease,
                                  &context->data, &context->byte_length);
}

static int tensor_lease_is_neutral(const void* opaque_context) {
  const tensor_lease_context_t* context =
      (const tensor_lease_context_t*)opaque_context;
  return context->lease == NULL && context->data == NULL &&
         context->byte_length == 0u;
}

static int tensor_lease_is_success(const void* opaque_context) {
  const tensor_lease_context_t* context =
      (const tensor_lease_context_t*)opaque_context;
  return context->lease != NULL && context->data != NULL &&
         context->byte_length == sizeof(float) &&
         *(const float*)context->data == 1.0f;
}

static void tensor_lease_cleanup(void* opaque_context) {
  tensor_lease_context_t* context =
      (tensor_lease_context_t*)opaque_context;
  dort_data_lease_release(context->lease);
  context->lease = NULL;
  context->data = NULL;
  context->byte_length = 0u;
}

static dort_status_t* invoke_tensor_info(void* opaque_context) {
  tensor_info_context_t* context = (tensor_info_context_t*)opaque_context;
  memset(&context->output, 0xff, sizeof(context->output));
  return dort_tensor_info_json(context->value, &context->output);
}

static int tensor_info_is_neutral(const void* opaque_context) {
  const tensor_info_context_t* context =
      (const tensor_info_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data == NULL && context->output.length == 0u &&
         context->output.private_owner == NULL;
}

static int tensor_info_is_success(const void* opaque_context) {
  static const char expected[] =
      "{\"schemaVersion\":1,\"kind\":\"tensor\",\"elementType\":1,"
      "\"dimensions\":[1],\"byteLength\":4}";
  const tensor_info_context_t* context =
      (const tensor_info_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data != NULL &&
         context->output.length == sizeof(expected) - 1u &&
         context->output.private_owner != NULL &&
         memcmp(context->output.data, expected, sizeof(expected) - 1u) == 0;
}

static void tensor_info_cleanup(void* opaque_context) {
  tensor_info_context_t* context = (tensor_info_context_t*)opaque_context;
  dort_string_release(&context->output);
}

static int profile_root_is_empty(const char* root) {
  DIR* stream = opendir(root);
  struct dirent* entry = NULL;
  int empty = 1;
  if (stream == NULL) {
    return 0;
  }
  for (;;) {
    errno = 0;
    entry = readdir(stream);
    if (entry == NULL) {
      if (errno != 0) {
        empty = 0;
      }
      break;
    }
    if (strcmp(entry->d_name, ".") != 0 &&
        strcmp(entry->d_name, "..") != 0) {
      empty = 0;
    }
  }
  if (closedir(stream) != 0) {
    empty = 0;
  }
  return empty;
}

static dort_status_t* invoke_profile_cycle(void* opaque_context) {
  profile_context_t* context = (profile_context_t*)opaque_context;
  dort_status_t* status = NULL;
  memset(&context->output, 0, sizeof(context->output));
  context->output.struct_size = (uint32_t)sizeof(context->output);
  status = dort_run_options_profiling_start(context->options, context->root);
  if (status != NULL) {
    return status;
  }
  memset(&context->output, 0xff, sizeof(context->output));
  return dort_run_options_profiling_finish(context->options, &context->output);
}

static int profile_is_neutral(const void* opaque_context) {
  const profile_context_t* context =
      (const profile_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data == NULL && context->output.length == 0u &&
         context->output.private_owner == NULL &&
         profile_root_is_empty(context->root);
}

static int profile_is_success(const void* opaque_context) {
  const profile_context_t* context =
      (const profile_context_t*)opaque_context;
  return context->output.struct_size == sizeof(context->output) &&
         context->output.data != NULL && context->output.length == 2u &&
         context->output.private_owner != NULL &&
         memcmp(context->output.data, "[]", 2u) == 0 &&
         profile_root_is_empty(context->root);
}

static void profile_cleanup(void* opaque_context) {
  profile_context_t* context = (profile_context_t*)opaque_context;
  dort_string_release(&context->output);
}

static int test_emergency_status(void) {
  dort_string_t output;
  dort_status_t* status = NULL;
  memset(&output, 0xff, sizeof(output));
  CHECK(configure_fault(1u), "could not configure emergency-status fault");
  status = dort_runtime_info_json(NULL, &output);
  CHECK(is_allocation_status(status),
        "status allocation fault did not return the emergency status");
  CHECK(output.struct_size == sizeof(output) && output.data == NULL &&
            output.length == 0u && output.private_owner == NULL,
        "emergency status path published output");
  dort_status_release(status);

  CHECK(configure_fault(0u), "could not disable emergency-status fault");
  status = dort_runtime_info_json(NULL, &output);
  CHECK(status != NULL &&
            dort_status_domain(status) == DORT_ERROR_DOMAIN_SHIM &&
            dort_status_code(status) == DORT_ERROR_INVALID_ARGUMENT,
        "status allocation fault did not permit a clean retry");
  dort_status_release(status);
  return 0;
}

int main(int argc, char** argv) {
  enum {
    fake_session_verify_metadata_owner = 5,
    fake_session_run_dense = 9,
  };
  string_context_t manifest_context;
  runtime_context_t runtime_fault_context;
  provider_context_t provider_context;
  options_context_t options_context;
  session_context_t session_context;
  tensor_context_t tensor_context;
  sequence_context_t sequence_context;
  external_data_context_t external_data_context;
  profile_context_t profile_context;
  run_options_context_t run_options_context;
  run_context_t run_context;
  run_result_get_context_t run_result_get_context;
  cancel_register_context_t cancel_register_context;
  buffer_lease_context_t buffer_lease_context;
  tensor_lease_context_t tensor_lease_context;
  tensor_info_context_t tensor_info_context;
  dort_runtime_t* provider_runtime = NULL;
  dort_runtime_t* session_runtime = NULL;
  dort_session_options_t* session_options = NULL;
  dort_session_t* dense_session = NULL;
  dort_value_t* dense_input = NULL;
  dort_buffer_t* lease_buffer = NULL;
  dort_run_options_t* profile_options = NULL;
  dort_status_t* status = NULL;
  fault_case_t test_case;
  size_t verified_total = 0u;
  uint8_t dense_model_data[1] = {fake_session_run_dense};
  float dense_input_data = 1.0f;
  int64_t dense_input_shape[1] = {1};
  char profile_root[] = "/tmp/fonix-allocation-profile-XXXXXX";

  CHECK(argc == 5,
        "expected good/runtime-root/session-fake/session-root paths");
  (void)unsetenv("FONIX_TEST_ALLOCATION_EPOCH");
  (void)unsetenv("FONIX_TEST_ALLOCATION_FAIL_AT");
  memset(&manifest_context, 0, sizeof(manifest_context));
  memset(&runtime_fault_context, 0, sizeof(runtime_fault_context));
  runtime_fault_context.config =
      runtime_config(argv[1], argv[2], runtime_fault_context.log_id);

  test_case = (fault_case_t){"build manifest",
                             &manifest_context,
                             invoke_manifest,
                             string_is_neutral,
                             string_is_success,
                             string_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "build-manifest allocation faults failed");
  verified_total += test_case.expected_allocation_count;
  CHECK(test_emergency_status() == 0, "emergency status fault failed");

  test_case = (fault_case_t){"runtime open",       &runtime_fault_context,
                             invoke_runtime_open,  runtime_is_neutral,
                             runtime_is_success,   runtime_cleanup,
                             set_runtime_iteration,
                             8u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "runtime allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(configure_fault(0u), "could not disable runtime setup fault");
  {
    dort_runtime_config_t config =
        runtime_config(argv[1], argv[2], "allocation-fault-provider");
    status = dort_runtime_open(&config, &provider_runtime);
  }
  CHECK(status == NULL && provider_runtime != NULL,
        "could not open provider-test runtime");
  provider_context.runtime = provider_runtime;
  memset(&provider_context.output, 0, sizeof(provider_context.output));
  test_case = (fault_case_t){"provider discovery",
                             &provider_context,
                             invoke_providers,
                             provider_is_neutral,
                             provider_is_success,
                             provider_cleanup,
                             NULL,
                             5u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "provider-discovery allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(setenv("FONIX_TEST_REQUIRE_RUN_OPTIONS_CLEANUP", "1", 1) == 0,
        "could not enable fake run-options owner accounting");
  memset(&run_options_context, 0, sizeof(run_options_context));
  run_options_context.runtime = provider_runtime;
  test_case = (fault_case_t){"run options",
                             &run_options_context,
                             invoke_run_options,
                             run_options_is_neutral,
                             run_options_is_success,
                             run_options_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "run-options allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(configure_fault(0u), "could not disable profile setup fault");
  status = dort_run_options_create(provider_runtime, &profile_options);
  CHECK(status == NULL && profile_options != NULL,
        "could not create persistent profile options");
  CHECK(unsetenv("FONIX_TEST_REQUIRE_RUN_OPTIONS_CLEANUP") == 0,
        "could not disable fake run-options owner accounting");
  CHECK(mkdtemp(profile_root) != NULL,
        "could not create private profile-test root");
  memset(&profile_context, 0, sizeof(profile_context));
  profile_context.options = profile_options;
  profile_context.root = profile_root;
  test_case = (fault_case_t){"profiling cycle",
                             &profile_context,
                             invoke_profile_cycle,
                             profile_is_neutral,
                             profile_is_success,
                             profile_cleanup,
                             NULL,
                             5u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "profiling allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(configure_fault(0u), "could not disable session setup fault");
  {
    dort_runtime_config_t config =
        runtime_config(argv[3], argv[4], "allocation-fault-session-runtime");
    status = dort_runtime_open(&config, &session_runtime);
  }
  CHECK(status == NULL && session_runtime != NULL,
        "could not open session-test runtime");
  memset(&options_context, 0, sizeof(options_context));
  options_context.runtime = session_runtime;
  options_context.config = session_config();
  test_case = (fault_case_t){"session options",
                             &options_context,
                             invoke_options,
                             options_is_neutral,
                             options_is_success,
                             options_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "session-options allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(configure_fault(0u), "could not disable persistent-options fault");
  status = dort_session_options_create(session_runtime, &options_context.config,
                                       &session_options);
  CHECK(status == NULL && session_options != NULL,
        "could not create persistent session options");
  memset(&session_context, 0, sizeof(session_context));
  session_context.runtime = session_runtime;
  session_context.options = session_options;
  session_context.model_data[0] = fake_session_verify_metadata_owner;
  test_case = (fault_case_t){"session creation",
                             &session_context,
                             invoke_session,
                             session_is_neutral,
                             session_is_success,
                             session_cleanup,
                             NULL,
                             14u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "session allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  memset(&external_data_context, 0, sizeof(external_data_context));
  external_data_context.runtime = session_runtime;
  external_data_context.options = session_options;
  external_data_context.model_data[0] = fake_session_verify_metadata_owner;
  memcpy(external_data_context.first_data, "ab", 2u);
  memcpy(external_data_context.second_data, "cde", 3u);
  external_data_context.entries[0].struct_size = DORT_EXTERNAL_DATA_V1_SIZE;
  external_data_context.entries[0].relative_name_utf8 =
      (const uint8_t*)"a.bin";
  external_data_context.entries[0].relative_name_length = 5u;
  external_data_context.entries[0].data = external_data_context.first_data;
  external_data_context.entries[0].data_length =
      sizeof(external_data_context.first_data);
  external_data_context.entries[1].struct_size = DORT_EXTERNAL_DATA_V1_SIZE;
  external_data_context.entries[1].relative_name_utf8 =
      (const uint8_t*)"nested/b.bin";
  external_data_context.entries[1].relative_name_length = 12u;
  external_data_context.entries[1].data = external_data_context.second_data;
  external_data_context.entries[1].data_length =
      sizeof(external_data_context.second_data);
  test_case = (fault_case_t){"external-data session",
                             &external_data_context,
                             invoke_external_data,
                             external_data_is_neutral,
                             external_data_is_success,
                             external_data_cleanup,
                             NULL,
                             22u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "external-data allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  memset(&tensor_context, 0, sizeof(tensor_context));
  tensor_context.runtime = session_runtime;
  tensor_context.value = 1.0f;
  tensor_context.shape[0] = 1;
  test_case = (fault_case_t){
      "tensor creation", &tensor_context, invoke_tensor, tensor_is_neutral,
      tensor_is_success, tensor_cleanup,  NULL, 3u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "tensor allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  memset(&sequence_context, 0, sizeof(sequence_context));
  sequence_context.runtime = session_runtime;
  CHECK(configure_fault(0u), "could not disable sequence setup fault");
  status = dort_tensor_create_copy(
      session_runtime, &tensor_context.value, sizeof(tensor_context.value),
      tensor_context.shape, 1u, DORT_TENSOR_FLOAT32,
      &sequence_context.children[0]);
  CHECK(status == NULL && sequence_context.children[0] != NULL,
        "could not create first persistent sequence child");
  status = dort_tensor_create_copy(
      session_runtime, &tensor_context.value, sizeof(tensor_context.value),
      tensor_context.shape, 1u, DORT_TENSOR_FLOAT32,
      &sequence_context.children[1]);
  CHECK(status == NULL && sequence_context.children[1] != NULL,
        "could not create second persistent sequence child");
  test_case = (fault_case_t){"sequence creation",
                             &sequence_context,
                             invoke_sequence,
                             sequence_is_neutral,
                             sequence_is_success,
                             sequence_cleanup,
                             NULL,
                             3u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "sequence allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(configure_fault(0u), "could not disable dense-run setup fault");
  status = dort_session_create_from_bytes(
      session_runtime, session_options, dense_model_data,
      sizeof(dense_model_data), &dense_session);
  CHECK(status == NULL && dense_session != NULL,
        "could not create persistent dense-run session");
  status = dort_tensor_create_copy(
      session_runtime, &dense_input_data, sizeof(dense_input_data),
      dense_input_shape, 1u, DORT_TENSOR_FLOAT32, &dense_input);
  CHECK(status == NULL && dense_input != NULL,
        "could not create persistent dense-run input");
  status = dort_buffer_allocate(
      session_runtime, sizeof(float), 64u, &lease_buffer);
  CHECK(status == NULL && lease_buffer != NULL,
        "could not create persistent lease buffer");

  memset(&run_context, 0, sizeof(run_context));
  run_context.session = dense_session;
  run_context.input.struct_size = DORT_NAMED_VALUE_V1_SIZE;
  run_context.input.name_utf8 = "X";
  run_context.input.value = dense_input;
  run_context.output_names[0] = "Y";
  test_case = (fault_case_t){"dense session run",
                             &run_context,
                             invoke_run,
                             run_is_neutral,
                             run_is_success,
                             run_cleanup,
                             NULL,
                             8u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "dense-run allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(configure_fault(0u), "could not disable run-result setup fault");
  status = invoke_run(&run_context);
  CHECK(status == NULL && run_is_success(&run_context),
        "could not create persistent run result");
  memset(&run_result_get_context, 0, sizeof(run_result_get_context));
  run_result_get_context.result = run_context.output;
  run_context.output = NULL;
  test_case = (fault_case_t){"run result get",
                             &run_result_get_context,
                             invoke_run_result_get,
                             run_result_get_is_neutral,
                             run_result_get_is_success,
                             run_result_get_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "run-result-get allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  memset(&cancel_register_context, 0, sizeof(cancel_register_context));
  cancel_register_context.options = profile_options;
  cancel_register_context.cleanup_ok = 1;
  test_case = (fault_case_t){"cancel registration",
                             &cancel_register_context,
                             invoke_cancel_register,
                             cancel_register_is_neutral,
                             cancel_register_is_success,
                             cancel_register_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "cancel-register allocation faults failed");
  CHECK(cancel_register_context.cleanup_ok &&
            cancel_register_context.token == 0u,
        "cancel-register cleanup did not retire its token");
  verified_total += test_case.expected_allocation_count;

  memset(&buffer_lease_context, 0, sizeof(buffer_lease_context));
  buffer_lease_context.buffer = lease_buffer;
  test_case = (fault_case_t){"buffer data lease",
                             &buffer_lease_context,
                             invoke_buffer_lease,
                             buffer_lease_is_neutral,
                             buffer_lease_is_success,
                             buffer_lease_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "buffer-lease allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  memset(&tensor_lease_context, 0, sizeof(tensor_lease_context));
  tensor_lease_context.value = dense_input;
  test_case = (fault_case_t){"tensor data lease",
                             &tensor_lease_context,
                             invoke_tensor_lease,
                             tensor_lease_is_neutral,
                             tensor_lease_is_success,
                             tensor_lease_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "tensor-lease allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  memset(&tensor_info_context, 0, sizeof(tensor_info_context));
  tensor_info_context.value = dense_input;
  test_case = (fault_case_t){"tensor info JSON",
                             &tensor_info_context,
                             invoke_tensor_info,
                             tensor_info_is_neutral,
                             tensor_info_is_success,
                             tensor_info_cleanup,
                             NULL,
                             1u};
  CHECK(exercise_fault_case(&test_case) == 0,
        "tensor-info allocation faults failed");
  verified_total += test_case.expected_allocation_count;

  CHECK(configure_fault(0u), "could not disable final allocation fault");
  CHECK(verified_total == EXPECTED_TOTAL_ALLOCATION_FAULTS,
        "allocation-fault total changed unexpectedly");
  dort_run_result_release(run_result_get_context.result);
  dort_buffer_release(lease_buffer);
  dort_value_release(dense_input);
  dort_session_release(dense_session);
  dort_value_release(sequence_context.children[1]);
  dort_value_release(sequence_context.children[0]);
  dort_run_options_release(profile_options);
  CHECK(profile_root_is_empty(profile_root),
        "profile-test root retained temporary artifacts");
  CHECK(rmdir(profile_root) == 0, "could not remove profile-test root");
  dort_session_options_release(session_options);
  dort_runtime_release(session_runtime);
  dort_runtime_release(provider_runtime);
  (void)unsetenv("FONIX_TEST_ALLOCATION_EPOCH");
  (void)unsetenv("FONIX_TEST_ALLOCATION_FAIL_AT");
  printf("Fonix shim allocation-fault tests passed (%u points).\n",
         EXPECTED_TOTAL_ALLOCATION_FAULTS);
  return 0;
}
