#define _POSIX_C_SOURCE 200809L
#if defined(__APPLE__)
#define _DARWIN_C_SOURCE 1
#endif

#include "dort_internal.h"

#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define CHECK(condition, message)                                              \
  do {                                                                         \
    if (!(condition)) {                                                        \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));      \
      return 1;                                                                \
    }                                                                          \
  } while (0)

struct dort_runtime {
  uint32_t marker;
};

struct dort_session {
  uint32_t marker;
};

typedef struct fake_run_options {
  uint32_t terminated;
  char* profile_prefix;
} fake_run_options_t;

typedef struct run_thread_context {
  dort_session_t* session;
  dort_run_options_t* options;
  dort_status_t* status;
  dort_run_result_t* result;
} run_thread_context_t;

static struct dort_runtime test_runtime = {0x52554e31u};
static struct dort_session test_session = {0x53455331u};
static pthread_mutex_t run_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t run_condition = PTHREAD_COND_INITIALIZER;
static uint32_t run_entered = 0u;
static uint32_t run_active = 0u;
static uint32_t run_timed_out = 0u;
static uint32_t unset_count = 0u;
static uint32_t unset_while_active = 0u;
static uint32_t profile_extra_file = 0u;

static OrtStatus* ORT_API_CALL
fake_create_run_options(OrtRunOptions** out_options) NO_EXCEPTION {
  if (out_options == NULL) {
    return (OrtStatus*)malloc(1u);
  }
  *out_options = (OrtRunOptions*)calloc(1u, sizeof(fake_run_options_t));
  return *out_options == NULL ? (OrtStatus*)malloc(1u) : NULL;
}

static void ORT_API_CALL fake_release_run_options(OrtRunOptions* options)
    NO_EXCEPTION {
  free(((fake_run_options_t*)options)->profile_prefix);
  free(options);
}

static OrtStatus* ORT_API_CALL fake_enable_run_profiling(
    OrtRunOptions* options, const ORTCHAR_T* profile_file_prefix) NO_EXCEPTION {
  fake_run_options_t* fake = (fake_run_options_t*)options;
  size_t length = strlen(profile_file_prefix);
  fake->profile_prefix = (char*)malloc(length + 1u);
  if (fake->profile_prefix == NULL) {
    return (OrtStatus*)malloc(1u);
  }
  memcpy(fake->profile_prefix, profile_file_prefix, length + 1u);
  return NULL;
}

static OrtStatus* ORT_API_CALL
fake_disable_run_profiling(OrtRunOptions* options) NO_EXCEPTION {
  fake_run_options_t* fake = (fake_run_options_t*)options;
  char path[4096];
  FILE* output = NULL;
  if (fake->profile_prefix == NULL ||
      snprintf(path, sizeof(path), "%s_123.json", fake->profile_prefix) < 0) {
    return (OrtStatus*)malloc(1u);
  }
  output = fopen(path, "wb");
  if (output == NULL) {
    return (OrtStatus*)malloc(1u);
  }
  if (fwrite("[]", 1u, 2u, output) != 2u) {
    (void)fclose(output);
    return (OrtStatus*)malloc(1u);
  }
  if (fclose(output) != 0) {
    return (OrtStatus*)malloc(1u);
  }
  if (profile_extra_file != 0u) {
    if (snprintf(path, sizeof(path), "%s_extra.json", fake->profile_prefix) <
        0) {
      return (OrtStatus*)malloc(1u);
    }
    output = fopen(path, "wb");
    if (output == NULL) {
      return (OrtStatus*)malloc(1u);
    }
    if (fwrite("[]", 1u, 2u, output) != 2u) {
      (void)fclose(output);
      return (OrtStatus*)malloc(1u);
    }
    if (fclose(output) != 0) {
      return (OrtStatus*)malloc(1u);
    }
  }
  free(fake->profile_prefix);
  fake->profile_prefix = NULL;
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_set_terminate(OrtRunOptions* options)
    NO_EXCEPTION {
  fake_run_options_t* fake = (fake_run_options_t*)options;
  (void)pthread_mutex_lock(&run_lock);
  fake->terminated = 1u;
  (void)pthread_cond_broadcast(&run_condition);
  (void)pthread_mutex_unlock(&run_lock);
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_unset_terminate(OrtRunOptions* options)
    NO_EXCEPTION {
  fake_run_options_t* fake = (fake_run_options_t*)options;
  (void)pthread_mutex_lock(&run_lock);
  if (run_active != 0u) {
    unset_while_active = 1u;
  }
  fake->terminated = 0u;
  ++unset_count;
  (void)pthread_mutex_unlock(&run_lock);
  return NULL;
}

static OrtStatus* ORT_API_CALL
fake_run(OrtSession* session, const OrtRunOptions* options,
         const char* const* input_names, const OrtValue* const* inputs,
         size_t input_count, const char* const* output_names,
         size_t output_count, OrtValue** outputs) NO_EXCEPTION {
  const fake_run_options_t* fake = (const fake_run_options_t*)options;
  struct timespec deadline;
  int wait_result = 0;
  (void)session;
  (void)input_names;
  (void)inputs;
  (void)input_count;
  (void)output_names;
  (void)output_count;
  (void)outputs;

  (void)timespec_get(&deadline, TIME_UTC);
  deadline.tv_sec += 5;
  (void)pthread_mutex_lock(&run_lock);
  run_entered = 1u;
  run_active = 1u;
  (void)pthread_cond_broadcast(&run_condition);
  while (fake->terminated == 0u && wait_result != ETIMEDOUT) {
    wait_result = pthread_cond_timedwait(&run_condition, &run_lock, &deadline);
  }
  if (wait_result == ETIMEDOUT) {
    run_timed_out = 1u;
  }
  run_active = 0u;
  (void)pthread_cond_broadcast(&run_condition);
  (void)pthread_mutex_unlock(&run_lock);
  return (OrtStatus*)malloc(1u);
}

static void ORT_API_CALL fake_release_value(OrtValue* value) NO_EXCEPTION {
  free(value);
}

static const OrtApi test_api = {
    .CreateRunOptions = fake_create_run_options,
    .ReleaseRunOptions = fake_release_run_options,
    .RunOptionsSetTerminate = fake_set_terminate,
    .RunOptionsUnsetTerminate = fake_unset_terminate,
    .RunOptionsEnableProfiling = fake_enable_run_profiling,
    .RunOptionsDisableProfiling = fake_disable_run_profiling,
    .Run = fake_run,
    .ReleaseValue = fake_release_value,
};

int dort_runtime_is_valid(const dort_runtime_t* runtime) {
  return runtime == &test_runtime;
}

const OrtApi* dort_runtime_api(const dort_runtime_t* runtime) {
  return runtime == &test_runtime ? &test_api : NULL;
}

OrtEnv* dort_runtime_env(const dort_runtime_t* runtime) {
  (void)runtime;
  return NULL;
}

void DORT_CALL dort_runtime_retain(dort_runtime_t* runtime) { (void)runtime; }

void DORT_CALL dort_runtime_release(dort_runtime_t* runtime) { (void)runtime; }

int dort_runtime_has_same_identity(const dort_runtime_t* left,
                                   const dort_runtime_t* right) {
  return left == right;
}

dort_status_t* dort_runtime_require_api(const dort_runtime_t* runtime,
                                        uint32_t features,
                                        const char* operation) {
  (void)features;
  (void)operation;
  return runtime == &test_runtime
             ? NULL
             : dort_status_create(
                   DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
                   "runtime_require_api", "The test runtime is invalid.");
}

dort_status_t* dort_status_from_ort(const dort_runtime_t* runtime,
                                    OrtStatus* ort_status, int32_t wrapper_code,
                                    const char* operation) {
  (void)runtime;
  free(ort_status);
  if (ort_status == NULL) {
    return NULL;
  }
  return dort_status_create(
      DORT_ERROR_DOMAIN_ORT_STATUS, wrapper_code, 1, operation,
      "Synthetic termination returned from blocking Run.");
}

int dort_session_internal_is_valid(const dort_session_t* session) {
  return session == &test_session;
}

dort_runtime_t* dort_session_internal_runtime(const dort_session_t* session) {
  return session == &test_session ? &test_runtime : NULL;
}

OrtSession* dort_session_internal_ort(const dort_session_t* session) {
  return session == &test_session ? (OrtSession*)&test_session : NULL;
}

size_t dort_session_internal_input_count(const dort_session_t* session) {
  return session == &test_session ? 0u : 0u;
}

size_t dort_session_internal_output_count(const dort_session_t* session) {
  return session == &test_session ? 1u : 0u;
}

const char* dort_session_internal_input_name(const dort_session_t* session,
                                             size_t index) {
  (void)session;
  (void)index;
  return NULL;
}

const char* dort_session_internal_output_name(const dort_session_t* session,
                                              size_t index) {
  return session == &test_session && index == 0u ? "Y" : NULL;
}

uint32_t dort_session_internal_input_kind(const dort_session_t* session,
                                          size_t index) {
  (void)session;
  (void)index;
  return 0u;
}

uint32_t dort_session_internal_output_kind(const dort_session_t* session,
                                           size_t index) {
  return session == &test_session && index == 0u ? DORT_VALUE_KIND_TENSOR : 0u;
}

void dort_session_internal_lock(dort_session_t* session) { (void)session; }

void dort_session_internal_unlock(dort_session_t* session) { (void)session; }

void DORT_CALL dort_session_retain(dort_session_t* session) { (void)session; }

void DORT_CALL dort_session_release(dort_session_t* session) { (void)session; }

dort_runtime_t* dort_value_runtime(const dort_value_t* value) {
  (void)value;
  return NULL;
}

OrtValue* dort_value_ort_for_run(const dort_value_t* value) {
  (void)value;
  return NULL;
}

int dort_value_is_optional_none(const dort_value_t* value) {
  (void)value;
  return 0;
}

dort_status_t* dort_value_validate_supported(const dort_value_t* value,
                                             const char* operation) {
  (void)value;
  (void)operation;
  return NULL;
}

dort_status_t* DORT_CALL dort_value_kind(const dort_value_t* value,
                                         uint32_t* out_kind) {
  (void)value;
  if (out_kind != NULL) {
    *out_kind = 0u;
  }
  return NULL;
}

void DORT_CALL dort_value_retain(dort_value_t* value) { (void)value; }

void DORT_CALL dort_value_release(dort_value_t* value) { (void)value; }

dort_status_t* dort_value_wrap_owned(dort_runtime_t* runtime,
                                     OrtValue* ort_value,
                                     dort_value_t** out_value) {
  (void)runtime;
  (void)ort_value;
  if (out_value != NULL) {
    *out_value = NULL;
  }
  return dort_status_create(
      DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
      "value_wrap_owned",
      "The blocking test must return before output wrapping.");
}

dort_status_t* dort_value_wrap_optional_output(dort_runtime_t* runtime,
                                               OrtValue* ort_value,
                                               dort_value_t** out_value) {
  return dort_value_wrap_owned(runtime, ort_value, out_value);
}

int dort_bounded_utf8_length(const char* value, size_t maximum, int allow_empty,
                             size_t* out_length) {
  size_t length = 0u;
  if (value == NULL || out_length == NULL) {
    return DORT_ERROR_INVALID_ARGUMENT;
  }
  length = strlen(value);
  if (length > maximum || (length == 0u && allow_empty == 0)) {
    return DORT_ERROR_INVALID_ARGUMENT;
  }
  *out_length = length;
  return DORT_ERROR_NONE;
}

char* dort_copy_c_string(const char* value, size_t length) {
  char* copy = NULL;
  if (value == NULL || length == SIZE_MAX) {
    return NULL;
  }
  copy = (char*)malloc(length + 1u);
  if (copy == NULL) {
    return NULL;
  }
  memcpy(copy, value, length);
  copy[length] = '\0';
  return copy;
}

dort_status_t* dort_string_copy(const char* value, dort_string_t* out_string) {
  size_t length = 0u;
  if (value == NULL || out_string == NULL) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, "string_copy",
                              "Invalid test string output.");
  }
  length = strlen(value);
  memset(out_string, 0, sizeof(*out_string));
  out_string->struct_size = (uint32_t)sizeof(*out_string);
  out_string->private_owner = dort_copy_c_string(value, length);
  out_string->data = (const uint8_t*)out_string->private_owner;
  out_string->length = length;
  return out_string->private_owner == NULL
             ? dort_status_create(
                   DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED,
                   0, "string_copy", "Could not copy the test string.")
             : NULL;
}

static void* execute_run(void* opaque_context) {
  run_thread_context_t* context = (run_thread_context_t*)opaque_context;
  const char* output_names[] = {"Y"};
  context->status = dort_session_run(context->session, context->options, NULL,
                                     0u, output_names, 1u, &context->result);
  return NULL;
}

static int wait_until_run_entered(void) {
  struct timespec deadline;
  int wait_result = 0;
  (void)timespec_get(&deadline, TIME_UTC);
  deadline.tv_sec += 5;
  (void)pthread_mutex_lock(&run_lock);
  while (run_entered == 0u && wait_result != ETIMEDOUT) {
    wait_result = pthread_cond_timedwait(&run_condition, &run_lock, &deadline);
  }
  (void)pthread_mutex_unlock(&run_lock);
  return wait_result != ETIMEDOUT;
}

int main(void) {
  dort_run_options_t* options = NULL;
  dort_status_t* status = NULL;
  uint64_t token = 0u;
  uint32_t did_request = 0u;
  uint32_t was_requested = 0u;
  pthread_t run_thread;
  run_thread_context_t context;
  dort_string_t profile_json;
  char profile_root[] = "/tmp/fonix-profile-native-XXXXXX";

  memset(&context, 0, sizeof(context));
  status = dort_run_options_create(&test_runtime, &options);
  CHECK(status == NULL && options != NULL, "could not create run options");
  status = dort_cancel_token_register(options, &token);
  CHECK(status == NULL && token != 0u, "could not register cancellation");

  context.session = &test_session;
  context.options = options;
  CHECK(pthread_create(&run_thread, NULL, execute_run, &context) == 0,
        "could not create blocking Run thread");
  CHECK(wait_until_run_entered(), "fake Run did not enter before timeout");

  status = dort_cancel_token_request(token, &did_request);
  CHECK(status == NULL && did_request == 1u, "Run cancellation was not set");
  CHECK(pthread_join(run_thread, NULL) == 0,
        "could not join blocking Run thread");
  CHECK(run_timed_out == 0u, "fake Run timed out instead of being cancelled");
  CHECK(context.result == NULL, "a cancelled Run returned a result");
  CHECK(context.status != NULL, "a cancelled Run returned success");
  CHECK(dort_status_code(context.status) == DORT_ERROR_RUN_FAILED,
        "a cancelled Run returned an unexpected status");
  dort_status_release(context.status);

  status = dort_cancel_token_finish(token, &was_requested);
  CHECK(status == NULL, "could not finish Run cancellation");
  CHECK(was_requested == 1u, "settlement lost the cancellation request");
  CHECK(unset_count == 1u, "termination was not unset exactly once");
  CHECK(unset_while_active == 0u, "termination was unset before Run returned");

  CHECK(mkdtemp(profile_root) != NULL, "could not create profile root");
  status = dort_run_options_profiling_start(options, profile_root);
  CHECK(status == NULL, "could not start one-run profiling");
  memset(&profile_json, 0, sizeof(profile_json));
  status = dort_run_options_profiling_finish(options, &profile_json);
  CHECK(status == NULL, "could not finish one-run profiling");
  CHECK(profile_json.length == 2u &&
            memcmp(profile_json.data, "[]", profile_json.length) == 0,
        "one-run profile bytes were not copied exactly");
  free(profile_json.private_owner);
  status = dort_run_options_profiling_finish(options, &profile_json);
  CHECK(status != NULL, "a repeated profile finish was accepted");
  CHECK(profile_json.data == NULL && profile_json.length == 0u &&
            profile_json.private_owner == NULL,
        "a repeated profile finish published stale bytes");
  dort_status_release(status);

  profile_extra_file = 1u;
  status = dort_run_options_profiling_start(options, profile_root);
  CHECK(status == NULL, "could not restart one-run profiling");
  memset(&profile_json, 0, sizeof(profile_json));
  status = dort_run_options_profiling_finish(options, &profile_json);
  CHECK(status != NULL, "multiple profile files were accepted");
  CHECK(profile_json.data == NULL, "failed profile validation published bytes");
  dort_status_release(status);

  profile_extra_file = 0u;
  status = dort_run_options_profiling_start(options, profile_root);
  CHECK(status == NULL, "could not start profiling before release");
  dort_run_options_release(options);
  options = NULL;
  CHECK(rmdir(profile_root) == 0,
        "release did not clean the active private profile directory");

  printf("Fonix cancel-during-Run test passed.\n");
  return 0;
}
