#include <onnxruntime_c_api.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if FONIX_FAKE_ORT_BEHAVIOR != 1 && FONIX_FAKE_ORT_BEHAVIOR != 2 && \
    FONIX_FAKE_ORT_BEHAVIOR != 3 && FONIX_FAKE_ORT_BEHAVIOR != 4
#error "FONIX_FAKE_ORT_BEHAVIOR must be 1 (good), 2 (unsupported API), 3 (bad discovery), or 4 (partial discovery error)"
#endif

#if FONIX_FAKE_ORT_BEHAVIOR == 1 || FONIX_FAKE_ORT_BEHAVIOR == 3 || \
    FONIX_FAKE_ORT_BEHAVIOR == 4
#define FONIX_FAKE_API fake_good_api
#define FONIX_FAKE_API_BASE fake_good_api_base
#define FONIX_FAKE_GET_API fake_good_get_api
#define FONIX_FAKE_GET_VERSION fake_good_get_version
#if FONIX_FAKE_ORT_BEHAVIOR == 4
static int fake_provider_owner_outstanding = 0;
static int fake_partial_provider_fault_emitted = 0;
#endif
#if FONIX_FAKE_ORT_BEHAVIOR == 3
static char* fake_oversized_provider_array[17] = {NULL};
#endif
static OrtStatus* ORT_API_CALL fake_create_env(
    OrtLoggingLevel log_severity_level,
    const char* log_id,
    OrtEnv** out) NO_EXCEPTION {
  (void)log_severity_level;
  (void)log_id;
  if (out != NULL) {
    *out = (OrtEnv*)malloc(1u);
  }
  return NULL;
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
  return "fake ONNX Runtime error";
}

static void ORT_API_CALL fake_release_status(OrtStatus* status) NO_EXCEPTION {
  (void)status;
}

typedef struct fake_run_options {
  uint32_t terminated;
  char* profile_prefix;
} fake_run_options_t;

static size_t fake_run_options_owner_count = 0u;

static OrtStatus* ORT_API_CALL fake_create_run_options(
    OrtRunOptions** out) NO_EXCEPTION {
  if (out == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  *out = NULL;
  if (getenv("FONIX_TEST_REQUIRE_RUN_OPTIONS_CLEANUP") != NULL &&
      fake_run_options_owner_count != 0u) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  *out = (OrtRunOptions*)calloc(1u, sizeof(fake_run_options_t));
  if (*out != NULL) {
    ++fake_run_options_owner_count;
  }
  return NULL;
}

static void ORT_API_CALL fake_release_run_options(
    OrtRunOptions* options) NO_EXCEPTION {
  if (options != NULL) {
    free(((fake_run_options_t*)options)->profile_prefix);
    if (fake_run_options_owner_count > 0u) {
      --fake_run_options_owner_count;
    }
  }
  free(options);
}

static OrtStatus* ORT_API_CALL fake_run_options_set_terminate(
    OrtRunOptions* options) NO_EXCEPTION {
  if (options != NULL) {
    ((fake_run_options_t*)options)->terminated = 1u;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_run_options_unset_terminate(
    OrtRunOptions* options) NO_EXCEPTION {
  if (options != NULL) {
    ((fake_run_options_t*)options)->terminated = 0u;
  }
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_run_options_enable_profiling(
    OrtRunOptions* options,
    const ORTCHAR_T* profile_file_prefix) NO_EXCEPTION {
  fake_run_options_t* fake = (fake_run_options_t*)options;
  size_t length = 0u;
  if (fake == NULL || profile_file_prefix == NULL ||
      fake->profile_prefix != NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  length = strlen(profile_file_prefix);
  fake->profile_prefix = (char*)malloc(length + 1u);
  if (fake->profile_prefix == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  memcpy(fake->profile_prefix, profile_file_prefix, length + 1u);
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_run_options_disable_profiling(
    OrtRunOptions* options) NO_EXCEPTION {
  fake_run_options_t* fake = (fake_run_options_t*)options;
  char path[4096];
  FILE* output = NULL;
  int written = 0;
  if (fake == NULL || fake->profile_prefix == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  written = snprintf(path, sizeof(path),
                     "%s_2026-08-06_17-09-03_125.json",
                     fake->profile_prefix);
  if (written <= 0 || (size_t)written >= sizeof(path)) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  output = fopen(path, "wb");
  if (output == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  if (fwrite("[]", 1u, 2u, output) != 2u) {
    (void)fclose(output);
    return (OrtStatus*)(uintptr_t)1u;
  }
  if (fclose(output) != 0) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  free(fake->profile_prefix);
  fake->profile_prefix = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_run(
    OrtSession* session,
    const OrtRunOptions* run_options,
    const char* const* input_names,
    const OrtValue* const* inputs,
    size_t input_len,
    const char* const* output_names,
    size_t output_names_len,
    OrtValue** outputs) NO_EXCEPTION {
  (void)session;
  (void)run_options;
  (void)input_names;
  (void)inputs;
  (void)input_len;
  (void)output_names;
  (void)output_names_len;
  (void)outputs;
  return NULL;
}

static void ORT_API_CALL fake_release_value(OrtValue* value) NO_EXCEPTION {
  free(value);
}

static char* fake_copy_string(const char* source) {
  size_t length = strlen(source);
  char* copy = (char*)malloc(length + 1u);
  if (copy != NULL) {
    memcpy(copy, source, length + 1u);
  }
  return copy;
}

static OrtStatus* ORT_API_CALL fake_get_available_providers(
    char*** out_ptr,
    int* provider_length) NO_EXCEPTION {
  char** providers = NULL;
  int count = 2;
  int index = 0;
  if (out_ptr == NULL || provider_length == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  *out_ptr = NULL;
  *provider_length = 0;
#if FONIX_FAKE_ORT_BEHAVIOR == 3
  *out_ptr = fake_oversized_provider_array;
  *provider_length = 17;
  return NULL;
#endif
#if FONIX_FAKE_ORT_BEHAVIOR == 4
  if (fake_partial_provider_fault_emitted != 0 &&
      fake_provider_owner_outstanding != 0) {
    return (OrtStatus*)(uintptr_t)1u;
  }
#endif
  providers = (char**)calloc((size_t)count, sizeof(*providers));
  if (providers == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  for (index = 0; index < count; ++index) {
    const char* name = index == 0 ? "CPUExecutionProvider"
                                  : "CoreMLExecutionProvider";
    providers[index] = fake_copy_string(name);
    if (providers[index] == NULL) {
      int prior = 0;
      for (prior = 0; prior < index; ++prior) {
        free(providers[prior]);
      }
      free(providers);
      return (OrtStatus*)(uintptr_t)1u;
    }
  }
  *out_ptr = providers;
  *provider_length = count;
#if FONIX_FAKE_ORT_BEHAVIOR == 4
  fake_provider_owner_outstanding = 1;
  if (fake_partial_provider_fault_emitted == 0) {
    fake_partial_provider_fault_emitted = 1;
    return (OrtStatus*)(uintptr_t)1u;
  }
#endif
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_release_available_providers(
    char** ptr,
    int providers_length) NO_EXCEPTION {
  int index = 0;
  for (index = 0; index < providers_length; ++index) {
    free(ptr[index]);
  }
  free(ptr);
#if FONIX_FAKE_ORT_BEHAVIOR == 4
  fake_provider_owner_outstanding = 0;
#endif
  return NULL;
}

static const OrtApi FONIX_FAKE_API = {
    .CreateEnv = fake_create_env,
    .ReleaseEnv = fake_release_env,
    .GetErrorCode = fake_get_error_code,
    .GetErrorMessage = fake_get_error_message,
    .ReleaseStatus = fake_release_status,
    .CreateRunOptions = fake_create_run_options,
    .ReleaseRunOptions = fake_release_run_options,
    .RunOptionsSetTerminate = fake_run_options_set_terminate,
    .RunOptionsUnsetTerminate = fake_run_options_unset_terminate,
    .RunOptionsEnableProfiling = fake_run_options_enable_profiling,
    .RunOptionsDisableProfiling = fake_run_options_disable_profiling,
    .Run = fake_run,
    .ReleaseValue = fake_release_value,
    .GetAvailableProviders = fake_get_available_providers,
    .ReleaseAvailableProviders = fake_release_available_providers,
};
#else
#define FONIX_FAKE_API_BASE fake_unsupported_api_base
#define FONIX_FAKE_GET_API fake_unsupported_get_api
#define FONIX_FAKE_GET_VERSION fake_unsupported_get_version
#endif

static const OrtApi* ORT_API_CALL FONIX_FAKE_GET_API(uint32_t version) NO_EXCEPTION {
#if FONIX_FAKE_ORT_BEHAVIOR == 1 || FONIX_FAKE_ORT_BEHAVIOR == 3 || \
    FONIX_FAKE_ORT_BEHAVIOR == 4
  return version == 27u ? &FONIX_FAKE_API : NULL;
#else
  (void)version;
  return NULL;
#endif
}

static const char* ORT_API_CALL FONIX_FAKE_GET_VERSION(void) NO_EXCEPTION {
#if FONIX_FAKE_ORT_BEHAVIOR == 1 || FONIX_FAKE_ORT_BEHAVIOR == 3 || \
    FONIX_FAKE_ORT_BEHAVIOR == 4
  return "1.27.99-fonix-test";
#else
  return "1.26.99-fonix-test";
#endif
}

static const OrtApiBase FONIX_FAKE_API_BASE = {
    FONIX_FAKE_GET_API,
    FONIX_FAKE_GET_VERSION,
};

#if defined(_WIN32)
__declspec(dllexport)
#else
__attribute__((visibility("default")))
#endif
const OrtApiBase* ORT_API_CALL OrtGetApiBase(void) NO_EXCEPTION {
  return &FONIX_FAKE_API_BASE;
}
