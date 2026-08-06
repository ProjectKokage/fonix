#include <onnxruntime_c_api.h>

#include <stdint.h>
#include <stdlib.h>

#if FONIX_FAKE_ORT_BEHAVIOR != 1 && FONIX_FAKE_ORT_BEHAVIOR != 2 && \
    FONIX_FAKE_ORT_BEHAVIOR != 3
#error "FONIX_FAKE_ORT_BEHAVIOR must be 1 (good), 2 (unsupported API), or 3 (bad discovery)"
#endif

#if FONIX_FAKE_ORT_BEHAVIOR == 1 || FONIX_FAKE_ORT_BEHAVIOR == 3
#define FONIX_FAKE_API fake_good_api
#define FONIX_FAKE_API_BASE fake_good_api_base
#define FONIX_FAKE_GET_API fake_good_get_api
#define FONIX_FAKE_GET_VERSION fake_good_get_version
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
} fake_run_options_t;

static OrtStatus* ORT_API_CALL fake_create_run_options(
    OrtRunOptions** out) NO_EXCEPTION {
  if (out != NULL) {
    *out = (OrtRunOptions*)calloc(1u, sizeof(fake_run_options_t));
  }
  return NULL;
}

static void ORT_API_CALL fake_release_run_options(
    OrtRunOptions* options) NO_EXCEPTION {
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
  int count = FONIX_FAKE_ORT_BEHAVIOR == 3 ? 17 : 2;
  int index = 0;
  if (out_ptr == NULL || provider_length == NULL) {
    return (OrtStatus*)(uintptr_t)1u;
  }
  *out_ptr = NULL;
  *provider_length = 0;
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
#if FONIX_FAKE_ORT_BEHAVIOR == 1 || FONIX_FAKE_ORT_BEHAVIOR == 3
  return version == 27u ? &FONIX_FAKE_API : NULL;
#else
  (void)version;
  return NULL;
#endif
}

static const char* ORT_API_CALL FONIX_FAKE_GET_VERSION(void) NO_EXCEPTION {
#if FONIX_FAKE_ORT_BEHAVIOR == 1 || FONIX_FAKE_ORT_BEHAVIOR == 3
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
