#define _POSIX_C_SOURCE 200809L

#include "dort.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_FAULT_POINT 256u

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
  enum { fake_session_verify_metadata_owner = 5 };
  string_context_t manifest_context;
  runtime_context_t runtime_fault_context;
  provider_context_t provider_context;
  options_context_t options_context;
  session_context_t session_context;
  tensor_context_t tensor_context;
  dort_runtime_t* provider_runtime = NULL;
  dort_runtime_t* session_runtime = NULL;
  dort_session_options_t* session_options = NULL;
  dort_status_t* status = NULL;
  fault_case_t test_case;

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
                             NULL};
  CHECK(exercise_fault_case(&test_case) == 0,
        "build-manifest allocation faults failed");
  CHECK(test_emergency_status() == 0, "emergency status fault failed");

  test_case = (fault_case_t){"runtime open",       &runtime_fault_context,
                             invoke_runtime_open,  runtime_is_neutral,
                             runtime_is_success,   runtime_cleanup,
                             set_runtime_iteration};
  CHECK(exercise_fault_case(&test_case) == 0,
        "runtime allocation faults failed");

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
                             NULL};
  CHECK(exercise_fault_case(&test_case) == 0,
        "provider-discovery allocation faults failed");

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
                             NULL};
  CHECK(exercise_fault_case(&test_case) == 0,
        "session-options allocation faults failed");

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
                             NULL};
  CHECK(exercise_fault_case(&test_case) == 0,
        "session allocation faults failed");

  memset(&tensor_context, 0, sizeof(tensor_context));
  tensor_context.runtime = session_runtime;
  tensor_context.value = 1.0f;
  tensor_context.shape[0] = 1;
  test_case = (fault_case_t){
      "tensor creation", &tensor_context, invoke_tensor, tensor_is_neutral,
      tensor_is_success, tensor_cleanup,  NULL};
  CHECK(exercise_fault_case(&test_case) == 0,
        "tensor allocation faults failed");

  CHECK(configure_fault(0u), "could not disable final allocation fault");
  dort_session_options_release(session_options);
  dort_runtime_release(session_runtime);
  dort_runtime_release(provider_runtime);
  (void)unsetenv("FONIX_TEST_ALLOCATION_EPOCH");
  (void)unsetenv("FONIX_TEST_ALLOCATION_FAIL_AT");
  printf("Fonix shim allocation-fault tests passed.\n");
  return 0;
}
