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

struct dort_value {
  uint32_t reference_count;
  OrtValue* value;
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

typedef struct cancel_thread_context {
  dort_run_options_t* options;
  uint64_t token;
  int32_t status_code;
  uint32_t outcome;
  uint32_t started;
  uint32_t completed;
} cancel_thread_context_t;

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
static uint32_t profile_invalid_utf8 = 0u;
static uint32_t profile_disable_failures = 0u;
static uint32_t release_saw_profile_directory = 0u;
static uint32_t run_call_count = 0u;
static uint32_t fake_run_mode = 0u;
static size_t fake_value_owner_count = 0u;
static size_t fake_value_release_count = 0u;
static pthread_mutex_t cancel_gate_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cancel_gate_condition = PTHREAD_COND_INITIALIZER;
static uint32_t block_next_terminate_set = 0u;
static uint32_t terminate_set_entered = 0u;
static uint32_t allow_terminate_set = 0u;
static uint32_t block_next_terminate_unset = 0u;
static uint32_t terminate_unset_entered = 0u;
static uint32_t allow_terminate_unset = 0u;
static uint32_t terminate_gate_timed_out = 0u;
static uint32_t terminate_set_failures = 0u;
static uint32_t terminate_unset_failures = 0u;

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
  fake_run_options_t* fake = (fake_run_options_t*)options;
  if (fake->profile_prefix != NULL) {
    char directory[4096];
    char* separator = NULL;
    size_t length = strlen(fake->profile_prefix);
    if (length < sizeof(directory)) {
      memcpy(directory, fake->profile_prefix, length + 1u);
      separator = strrchr(directory, '/');
      if (separator != NULL) {
        *separator = '\0';
        if (access(directory, F_OK) == 0) {
          release_saw_profile_directory = 1u;
        }
      }
    }
  }
  free(fake->profile_prefix);
  free(options);
}

static void ORT_API_CALL fake_release_status(OrtStatus* status) NO_EXCEPTION {
  free(status);
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
  if (profile_disable_failures != 0u) {
    --profile_disable_failures;
    return (OrtStatus*)malloc(1u);
  }
  if (fake->profile_prefix == NULL ||
      snprintf(path, sizeof(path), "%s_123.json", fake->profile_prefix) < 0) {
    return (OrtStatus*)malloc(1u);
  }
  output = fopen(path, "wb");
  if (output == NULL) {
    return (OrtStatus*)malloc(1u);
  }
  if (profile_invalid_utf8 != 0u) {
    static const uint8_t invalid_utf8[] = {0xc3u, 0x28u};
    if (fwrite(invalid_utf8, 1u, sizeof(invalid_utf8), output) !=
        sizeof(invalid_utf8)) {
      (void)fclose(output);
      return (OrtStatus*)malloc(1u);
    }
  } else if (fwrite("[]", 1u, 2u, output) != 2u) {
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
  struct timespec deadline;
  int wait_status = 0;
  (void)timespec_get(&deadline, TIME_UTC);
  deadline.tv_sec += 5;
  (void)pthread_mutex_lock(&cancel_gate_lock);
  if (block_next_terminate_set != 0u) {
    block_next_terminate_set = 0u;
    terminate_set_entered = 1u;
    (void)pthread_cond_broadcast(&cancel_gate_condition);
    while (allow_terminate_set == 0u && wait_status != ETIMEDOUT) {
      wait_status = pthread_cond_timedwait(
          &cancel_gate_condition, &cancel_gate_lock, &deadline);
    }
    if (wait_status == ETIMEDOUT) {
      terminate_gate_timed_out = 1u;
    }
  }
  if (terminate_set_failures != 0u) {
    --terminate_set_failures;
    (void)pthread_mutex_unlock(&cancel_gate_lock);
    return (OrtStatus*)malloc(1u);
  }
  (void)pthread_mutex_unlock(&cancel_gate_lock);
  (void)pthread_mutex_lock(&run_lock);
  fake->terminated = 1u;
  (void)pthread_cond_broadcast(&run_condition);
  (void)pthread_mutex_unlock(&run_lock);
  return NULL;
}

static OrtStatus* ORT_API_CALL fake_unset_terminate(OrtRunOptions* options)
    NO_EXCEPTION {
  fake_run_options_t* fake = (fake_run_options_t*)options;
  struct timespec deadline;
  int wait_status = 0;
  (void)timespec_get(&deadline, TIME_UTC);
  deadline.tv_sec += 5;
  (void)pthread_mutex_lock(&cancel_gate_lock);
  if (block_next_terminate_unset != 0u) {
    block_next_terminate_unset = 0u;
    terminate_unset_entered = 1u;
    (void)pthread_cond_broadcast(&cancel_gate_condition);
    while (allow_terminate_unset == 0u && wait_status != ETIMEDOUT) {
      wait_status = pthread_cond_timedwait(
          &cancel_gate_condition, &cancel_gate_lock, &deadline);
    }
    if (wait_status == ETIMEDOUT) {
      terminate_gate_timed_out = 1u;
    }
  }
  if (terminate_unset_failures != 0u) {
    --terminate_unset_failures;
    (void)pthread_mutex_unlock(&cancel_gate_lock);
    return (OrtStatus*)malloc(1u);
  }
  (void)pthread_mutex_unlock(&cancel_gate_lock);
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
  ++run_call_count;
  (void)session;
  (void)input_names;
  (void)inputs;
  (void)input_count;
  (void)output_names;

  if (fake_run_mode != 0u) {
    if (output_count != 1u || outputs == NULL) {
      return (OrtStatus*)malloc(1u);
    }
    outputs[0] = (OrtValue*)malloc(1u);
    if (outputs[0] == NULL) {
      return (OrtStatus*)malloc(1u);
    }
    ++fake_value_owner_count;
    return fake_run_mode == 1u ? (OrtStatus*)malloc(1u) : NULL;
  }

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
  if (value != NULL && fake_value_owner_count > 0u) {
    --fake_value_owner_count;
    ++fake_value_release_count;
  }
  free(value);
}

static const OrtApi test_api = {
    .ReleaseStatus = fake_release_status,
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
  return value == NULL ? NULL : &test_runtime;
}

OrtValue* dort_value_ort_for_run(const dort_value_t* value) {
  return value == NULL ? NULL : value->value;
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

dort_status_t* dort_value_validate_and_set_unpublished_depth(
    dort_value_t* value,
    const char* operation) {
  return dort_value_validate_supported(value, operation);
}

dort_status_t* DORT_CALL dort_value_kind(const dort_value_t* value,
                                         uint32_t* out_kind) {
  if (out_kind != NULL) {
    *out_kind = value == NULL ? 0u : DORT_VALUE_KIND_TENSOR;
  }
  return NULL;
}

void DORT_CALL dort_value_retain(dort_value_t* value) {
  if (value != NULL) {
    ++value->reference_count;
  }
}

void DORT_CALL dort_value_release(dort_value_t* value) {
  if (value == NULL || value->reference_count == 0u) {
    return;
  }
  --value->reference_count;
  if (value->reference_count == 0u) {
    fake_release_value(value->value);
    free(value);
  }
}

dort_status_t* dort_value_wrap_owned(dort_runtime_t* runtime,
                                     OrtValue* ort_value,
                                     dort_value_t** out_value) {
  dort_value_t* value = NULL;
  if (out_value == NULL) {
    fake_release_value(ort_value);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "value_wrap_owned", "The output value pointer is null.");
  }
  *out_value = NULL;
  if (runtime != &test_runtime || ort_value == NULL) {
    fake_release_value(ort_value);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "value_wrap_owned", "The test output value is invalid.");
  }
  value = (dort_value_t*)calloc(1u, sizeof(*value));
  if (value == NULL) {
    fake_release_value(ort_value);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "value_wrap_owned", "Could not allocate the test output wrapper.");
  }
  value->reference_count = 1u;
  value->value = ort_value;
  *out_value = value;
  return NULL;
}

dort_status_t* dort_value_wrap_optional_output(dort_runtime_t* runtime,
                                               OrtValue* ort_value,
                                               dort_value_t** out_value) {
  return dort_value_wrap_owned(runtime, ort_value, out_value);
}

static void* execute_run(void* opaque_context) {
  run_thread_context_t* context = (run_thread_context_t*)opaque_context;
  const char* output_names[] = {"Y"};
  context->status = dort_session_run(context->session, context->options, NULL,
                                     0u, output_names, 1u, &context->result);
  return NULL;
}

static int test_partial_output_error_cleanup(void) {
  const char* output_names[] = {"Y"};
  dort_run_options_t* options = NULL;
  dort_run_result_t* result = NULL;
  dort_status_t* status = NULL;
  size_t releases_before = fake_value_release_count;

  status = dort_run_options_create(&test_runtime, &options);
  CHECK(status == NULL && options != NULL,
        "could not create partial-output run options");
  fake_run_mode = 1u;
  result = (dort_run_result_t*)(uintptr_t)1u;
  status = dort_session_run(&test_session, options, NULL, 0u, output_names,
                            1u, &result);
  CHECK(status != NULL && result == NULL,
        "ORT error with a partial output returned a result");
  CHECK(dort_status_domain(status) == DORT_ERROR_DOMAIN_ORT_STATUS &&
            dort_status_code(status) == DORT_ERROR_RUN_FAILED,
        "partial-output error lost the authoritative ORT status");
  CHECK(fake_value_owner_count == 0u &&
            fake_value_release_count == releases_before + 1u,
        "partial ORT output was not released exactly once");
  dort_status_release(status);

  fake_run_mode = 2u;
  status = dort_session_run(&test_session, options, NULL, 0u, output_names,
                            1u, &result);
  CHECK(status == NULL && result != NULL,
        "clean run did not recover after partial-output failure");
  CHECK(fake_value_owner_count == 1u,
        "successful recovery did not retain its output owner");
  dort_run_result_release(result);
  CHECK(fake_value_owner_count == 0u &&
            fake_value_release_count == releases_before + 2u,
        "successful recovery output was not released exactly once");
  dort_run_options_release(options);
  fake_run_mode = 0u;
  return 0;
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

static void cancel_thread_mark_completed(cancel_thread_context_t* context) {
  (void)pthread_mutex_lock(&cancel_gate_lock);
  context->completed = 1u;
  (void)pthread_cond_broadcast(&cancel_gate_condition);
  (void)pthread_mutex_unlock(&cancel_gate_lock);
}

static void cancel_thread_mark_started(cancel_thread_context_t* context) {
  (void)pthread_mutex_lock(&cancel_gate_lock);
  context->started = 1u;
  (void)pthread_cond_broadcast(&cancel_gate_condition);
  (void)pthread_mutex_unlock(&cancel_gate_lock);
}

static int wait_for_cancel_flag(uint32_t* flag) {
  struct timespec deadline;
  int wait_result = 0;
  int observed = 0;
  if (timespec_get(&deadline, TIME_UTC) != TIME_UTC) {
    return 0;
  }
  deadline.tv_sec += 2;
  (void)pthread_mutex_lock(&cancel_gate_lock);
  while (*flag == 0u && wait_result != ETIMEDOUT) {
    wait_result = pthread_cond_timedwait(
        &cancel_gate_condition, &cancel_gate_lock, &deadline);
  }
  observed = *flag != 0u;
  (void)pthread_mutex_unlock(&cancel_gate_lock);
  return wait_result != ETIMEDOUT && observed;
}

static void* request_cancel_token(void* opaque_context) {
  cancel_thread_context_t* context =
      (cancel_thread_context_t*)opaque_context;
  cancel_thread_mark_started(context);
  dort_status_t* status =
      dort_cancel_token_request(context->token, &context->outcome);
  context->status_code =
      status == NULL ? DORT_ERROR_NONE : dort_status_code(status);
  dort_status_release(status);
  cancel_thread_mark_completed(context);
  return NULL;
}

static void* finish_cancel_token(void* opaque_context) {
  cancel_thread_context_t* context =
      (cancel_thread_context_t*)opaque_context;
  cancel_thread_mark_started(context);
  dort_status_t* status =
      dort_cancel_token_finish(context->token, &context->outcome);
  context->status_code =
      status == NULL ? DORT_ERROR_NONE : dort_status_code(status);
  dort_status_release(status);
  cancel_thread_mark_completed(context);
  return NULL;
}

static void* exercise_unrelated_cancel_token(void* opaque_context) {
  cancel_thread_context_t* context =
      (cancel_thread_context_t*)opaque_context;
  cancel_thread_mark_started(context);
  dort_status_t* status =
      dort_cancel_token_register(context->options, &context->token);
  uint32_t requested = 0u;
  uint32_t settled = 0u;
  context->status_code =
      status == NULL ? DORT_ERROR_NONE : dort_status_code(status);
  dort_status_release(status);
  if (context->status_code == DORT_ERROR_NONE) {
    status = dort_cancel_token_request(context->token, &requested);
    context->status_code =
        status == NULL ? DORT_ERROR_NONE : dort_status_code(status);
    dort_status_release(status);
  }
  if (context->token != 0u) {
    status = dort_cancel_token_finish(context->token, &settled);
    if (context->status_code == DORT_ERROR_NONE && status != NULL) {
      context->status_code = dort_status_code(status);
    }
    dort_status_release(status);
  }
  context->outcome = requested == 1u && settled == 1u ? 1u : 0u;
  cancel_thread_mark_completed(context);
  return NULL;
}

static void configure_blocked_terminate_set(void) {
  (void)pthread_mutex_lock(&cancel_gate_lock);
  block_next_terminate_set = 1u;
  terminate_set_entered = 0u;
  allow_terminate_set = 0u;
  terminate_gate_timed_out = 0u;
  (void)pthread_mutex_unlock(&cancel_gate_lock);
}

static void allow_blocked_terminate_set(void) {
  (void)pthread_mutex_lock(&cancel_gate_lock);
  allow_terminate_set = 1u;
  (void)pthread_cond_broadcast(&cancel_gate_condition);
  (void)pthread_mutex_unlock(&cancel_gate_lock);
}

static void configure_blocked_terminate_unset(void) {
  (void)pthread_mutex_lock(&cancel_gate_lock);
  block_next_terminate_unset = 1u;
  terminate_unset_entered = 0u;
  allow_terminate_unset = 0u;
  terminate_gate_timed_out = 0u;
  (void)pthread_mutex_unlock(&cancel_gate_lock);
}

static void allow_blocked_terminate_unset(void) {
  (void)pthread_mutex_lock(&cancel_gate_lock);
  allow_terminate_unset = 1u;
  (void)pthread_cond_broadcast(&cancel_gate_condition);
  (void)pthread_mutex_unlock(&cancel_gate_lock);
}

static int test_terminate_set_does_not_block_unrelated_tokens(void) {
  dort_run_options_t* blocked_options = NULL;
  dort_run_options_t* unrelated_options = NULL;
  dort_status_t* status = NULL;
  uint64_t blocked_token = 0u;
  uint32_t settled = 0u;
  pthread_t blocked_thread;
  pthread_t finish_thread;
  pthread_t unrelated_thread;
  cancel_thread_context_t blocked_context;
  cancel_thread_context_t finish_context;
  cancel_thread_context_t unrelated_context;
  int blocked_created = 0;
  int finish_created = 0;
  int unrelated_created = 0;
  int entered = 0;
  int finish_started = 0;
  int finish_completed_while_blocked = 0;
  int unrelated_completed_while_blocked = 0;
  int result = 0;

  memset(&blocked_context, 0, sizeof(blocked_context));
  memset(&finish_context, 0, sizeof(finish_context));
  memset(&unrelated_context, 0, sizeof(unrelated_context));
  status = dort_run_options_create(&test_runtime, &blocked_options);
  CHECK(status == NULL && blocked_options != NULL,
        "could not create blocked cancellation options");
  status = dort_run_options_create(&test_runtime, &unrelated_options);
  CHECK(status == NULL && unrelated_options != NULL,
        "could not create unrelated cancellation options");
  status = dort_cancel_token_register(blocked_options, &blocked_token);
  CHECK(status == NULL && blocked_token != 0u,
        "could not register blocked cancellation token");

  configure_blocked_terminate_set();
  blocked_context.token = blocked_token;
  if (pthread_create(
          &blocked_thread, NULL, request_cancel_token, &blocked_context) == 0) {
    blocked_created = 1;
    entered = wait_for_cancel_flag(&terminate_set_entered);
  }
  finish_context.token = blocked_token;
  if (entered &&
      pthread_create(
          &finish_thread, NULL, finish_cancel_token, &finish_context) == 0) {
    finish_created = 1;
    finish_started = wait_for_cancel_flag(&finish_context.started);
  }
  unrelated_context.options = unrelated_options;
  if (finish_started &&
      pthread_create(&unrelated_thread, NULL, exercise_unrelated_cancel_token,
                     &unrelated_context) == 0) {
    unrelated_created = 1;
    unrelated_completed_while_blocked =
        wait_for_cancel_flag(&unrelated_context.completed);
  }
  (void)pthread_mutex_lock(&cancel_gate_lock);
  finish_completed_while_blocked = finish_context.completed != 0u;
  (void)pthread_mutex_unlock(&cancel_gate_lock);
  allow_blocked_terminate_set();
  if (blocked_created && pthread_join(blocked_thread, NULL) != 0) {
    result = 1;
  }
  if (finish_created && pthread_join(finish_thread, NULL) != 0) {
    result = 1;
  }
  if (unrelated_created && pthread_join(unrelated_thread, NULL) != 0) {
    result = 1;
  }
  if (!finish_created) {
    status = dort_cancel_token_finish(blocked_token, &settled);
    if (status != NULL || settled > 1u) {
      result = 1;
    }
    dort_status_release(status);
  }
  if (!blocked_created || !finish_created || !unrelated_created || !entered ||
      !finish_started || finish_completed_while_blocked ||
      !unrelated_completed_while_blocked || terminate_gate_timed_out != 0u ||
      blocked_context.status_code != DORT_ERROR_NONE ||
      blocked_context.outcome != 1u ||
      finish_context.status_code != DORT_ERROR_NONE ||
      finish_context.outcome != 1u ||
      unrelated_context.status_code != DORT_ERROR_NONE ||
      unrelated_context.outcome != 1u) {
    result = 1;
  }
  dort_run_options_release(unrelated_options);
  dort_run_options_release(blocked_options);
  return result;
}

static int test_terminate_unset_does_not_block_unrelated_tokens(void) {
  dort_run_options_t* blocked_options = NULL;
  dort_run_options_t* unrelated_options = NULL;
  dort_status_t* status = NULL;
  uint64_t blocked_token = 0u;
  uint32_t requested = 0u;
  pthread_t blocked_thread;
  pthread_t unrelated_thread;
  pthread_t stale_thread;
  cancel_thread_context_t blocked_context;
  cancel_thread_context_t unrelated_context;
  cancel_thread_context_t stale_context;
  int blocked_created = 0;
  int unrelated_created = 0;
  int stale_created = 0;
  int entered = 0;
  int stale_started = 0;
  int stale_completed_while_blocked = 0;
  int unrelated_completed_while_blocked = 0;
  int result = 0;

  memset(&blocked_context, 0, sizeof(blocked_context));
  memset(&unrelated_context, 0, sizeof(unrelated_context));
  memset(&stale_context, 0, sizeof(stale_context));
  status = dort_run_options_create(&test_runtime, &blocked_options);
  CHECK(status == NULL && blocked_options != NULL,
        "could not create blocked settlement options");
  status = dort_run_options_create(&test_runtime, &unrelated_options);
  CHECK(status == NULL && unrelated_options != NULL,
        "could not create unrelated settlement options");
  status = dort_cancel_token_register(blocked_options, &blocked_token);
  CHECK(status == NULL && blocked_token != 0u,
        "could not register blocked settlement token");
  status = dort_cancel_token_request(blocked_token, &requested);
  CHECK(status == NULL && requested == 1u,
        "could not request blocked settlement token");

  configure_blocked_terminate_unset();
  blocked_context.token = blocked_token;
  if (pthread_create(
          &blocked_thread, NULL, finish_cancel_token, &blocked_context) == 0) {
    blocked_created = 1;
    entered = wait_for_cancel_flag(&terminate_unset_entered);
  }
  unrelated_context.options = unrelated_options;
  if (entered &&
      pthread_create(&unrelated_thread, NULL, exercise_unrelated_cancel_token,
                     &unrelated_context) == 0) {
    unrelated_created = 1;
  }
  stale_context.token = blocked_token;
  if (entered &&
      pthread_create(&stale_thread, NULL, request_cancel_token,
                     &stale_context) == 0) {
    stale_created = 1;
    stale_started = wait_for_cancel_flag(&stale_context.started);
  }
  if (unrelated_created) {
    unrelated_completed_while_blocked =
        wait_for_cancel_flag(&unrelated_context.completed);
  }
  (void)pthread_mutex_lock(&cancel_gate_lock);
  stale_completed_while_blocked = stale_context.completed != 0u;
  (void)pthread_mutex_unlock(&cancel_gate_lock);
  allow_blocked_terminate_unset();
  if (blocked_created && pthread_join(blocked_thread, NULL) != 0) {
    result = 1;
  }
  if (unrelated_created && pthread_join(unrelated_thread, NULL) != 0) {
    result = 1;
  }
  if (stale_created && pthread_join(stale_thread, NULL) != 0) {
    result = 1;
  }
  if (!blocked_created || !unrelated_created || !stale_created || !entered ||
      !stale_started || stale_completed_while_blocked ||
      !unrelated_completed_while_blocked || terminate_gate_timed_out != 0u ||
      blocked_context.status_code != DORT_ERROR_NONE ||
      blocked_context.outcome != 1u ||
      unrelated_context.status_code != DORT_ERROR_NONE ||
      unrelated_context.outcome != 1u ||
      stale_context.status_code != DORT_ERROR_CANCEL_TOKEN_UNKNOWN ||
      stale_context.outcome != 0u) {
    result = 1;
  }
  dort_run_options_release(unrelated_options);
  dort_run_options_release(blocked_options);
  return result;
}

static int test_failed_terminate_unset_preserves_registry_owner(void) {
  dort_run_options_t* options = NULL;
  dort_status_t* status = NULL;
  uint64_t token = 0u;
  uint32_t outcome = 0u;

  status = dort_run_options_create(&test_runtime, &options);
  CHECK(status == NULL && options != NULL,
        "could not create failed-unset options");
  status = dort_cancel_token_register(options, &token);
  CHECK(status == NULL && token != 0u,
        "could not register failed-unset token");
  status = dort_cancel_token_request(token, &outcome);
  CHECK(status == NULL && outcome == 1u,
        "could not request failed-unset token");
  dort_run_options_release(options);
  options = NULL;

  (void)pthread_mutex_lock(&cancel_gate_lock);
  terminate_unset_failures = 1u;
  (void)pthread_mutex_unlock(&cancel_gate_lock);
  outcome = UINT32_MAX;
  status = dort_cancel_token_finish(token, &outcome);
  CHECK(status != NULL && outcome == 0u,
        "native unset failure did not retain unsettled token");
  dort_status_release(status);

  outcome = UINT32_MAX;
  status = dort_cancel_token_request(token, &outcome);
  CHECK(status == NULL && outcome == 0u,
        "failed unset lost the requested token state");
  outcome = UINT32_MAX;
  status = dort_cancel_token_finish(token, &outcome);
  CHECK(status == NULL && outcome == 1u,
        "failed unset retry did not settle the retained token");
  outcome = UINT32_MAX;
  status = dort_cancel_token_request(token, &outcome);
  CHECK(status != NULL &&
            dort_status_code(status) == DORT_ERROR_CANCEL_TOKEN_UNKNOWN &&
            outcome == 0u,
        "settled token was accepted after unset retry");
  dort_status_release(status);
  return 0;
}

static int test_failed_terminate_set_restores_registered_state(void) {
  dort_run_options_t* options = NULL;
  dort_status_t* status = NULL;
  uint64_t token = 0u;
  uint32_t outcome = 0u;

  status = dort_run_options_create(&test_runtime, &options);
  CHECK(status == NULL && options != NULL,
        "could not create failed-set options");
  status = dort_cancel_token_register(options, &token);
  CHECK(status == NULL && token != 0u,
        "could not register failed-set token");

  (void)pthread_mutex_lock(&cancel_gate_lock);
  terminate_set_failures = 1u;
  (void)pthread_mutex_unlock(&cancel_gate_lock);
  outcome = UINT32_MAX;
  status = dort_cancel_token_request(token, &outcome);
  CHECK(status != NULL && outcome == 0u,
        "native set failure did not restore registered token state");
  dort_status_release(status);
  dort_run_options_release(options);
  options = NULL;

  outcome = UINT32_MAX;
  status = dort_cancel_token_request(token, &outcome);
  CHECK(status == NULL && outcome == 1u,
        "terminate-set retry did not request the retained token");
  outcome = UINT32_MAX;
  status = dort_cancel_token_finish(token, &outcome);
  CHECK(status == NULL && outcome == 1u,
        "terminate-set retry did not settle the retained token");
  outcome = UINT32_MAX;
  status = dort_cancel_token_finish(token, &outcome);
  CHECK(status != NULL &&
            dort_status_code(status) == DORT_ERROR_CANCEL_TOKEN_UNKNOWN &&
            outcome == 0u,
        "settled retry token was accepted by a stale finish");
  dort_status_release(status);
  return 0;
}

static int test_cancel_token_space_does_not_wrap(void) {
  dort_run_options_t* options = NULL;
  dort_status_t* status = NULL;
  uint64_t token = 0u;
  uint64_t stale_token = 0u;
  uint32_t outcome = 0u;
  size_t issued = 0u;
  int exhausted = 0;

  while (issued < 64u) {
    status = dort_run_options_create(&test_runtime, &options);
    CHECK(status == NULL && options != NULL,
          "could not create token-exhaustion options");
    token = UINT64_MAX;
    status = dort_cancel_token_register(options, &token);
    if (status != NULL) {
      CHECK(dort_status_code(status) == DORT_ERROR_CANCEL_REGISTRY_FULL &&
                token == 0u,
            "token exhaustion returned an unexpected result");
      dort_status_release(status);
      dort_run_options_release(options);
      options = NULL;
      exhausted = 1;
      break;
    }
    if (stale_token == 0u) {
      stale_token = token;
    }
    outcome = UINT32_MAX;
    status = dort_cancel_token_finish(token, &outcome);
    CHECK(status == NULL && outcome == 0u,
          "could not retire token before exhaustion");
    dort_run_options_release(options);
    options = NULL;
    ++issued;
  }
  CHECK(exhausted && issued != 0u,
        "bounded test token space wrapped instead of exhausting");

  outcome = UINT32_MAX;
  status = dort_cancel_token_request(stale_token, &outcome);
  CHECK(status != NULL &&
            dort_status_code(status) == DORT_ERROR_CANCEL_TOKEN_UNKNOWN &&
            outcome == 0u,
        "stale token became live after token-space exhaustion");
  dort_status_release(status);

  status = dort_run_options_create(&test_runtime, &options);
  CHECK(status == NULL && options != NULL,
        "could not create post-exhaustion options");
  token = UINT64_MAX;
  status = dort_cancel_token_register(options, &token);
  CHECK(status != NULL &&
            dort_status_code(status) == DORT_ERROR_CANCEL_REGISTRY_FULL &&
            token == 0u,
        "post-exhaustion registration restarted the token sequence");
  dort_status_release(status);
  dort_run_options_release(options);
  return 0;
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
  CHECK(test_partial_output_error_cleanup() == 0,
        "partial-output cleanup and recovery failed");
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
  CHECK(
      test_terminate_set_does_not_block_unrelated_tokens() == 0,
      "native terminate-set call blocked an unrelated cancellation token");
  CHECK(
      test_terminate_unset_does_not_block_unrelated_tokens() == 0,
      "native terminate-unset call blocked an unrelated cancellation token");
  CHECK(
      test_failed_terminate_unset_preserves_registry_owner() == 0,
      "failed native terminate-unset did not preserve registry ownership");
  CHECK(
      test_failed_terminate_set_restores_registered_state() == 0,
      "failed native terminate-set did not restore registered token state");
  CHECK(
      test_cancel_token_space_does_not_wrap() == 0,
      "bounded cancellation token sequence reused a stale token");

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

  profile_invalid_utf8 = 1u;
  status = dort_run_options_profiling_start(options, profile_root);
  CHECK(status == NULL, "could not start invalid-UTF-8 profiling");
  memset(&profile_json, 0xff, sizeof(profile_json));
  status = dort_run_options_profiling_finish(options, &profile_json);
  CHECK(status != NULL, "invalid-UTF-8 profile bytes were accepted");
  CHECK(dort_status_code(status) == DORT_ERROR_INVALID_UTF8,
        "invalid-UTF-8 profile returned an unexpected status");
  CHECK(profile_json.struct_size == (uint32_t)sizeof(profile_json) &&
            profile_json.data == NULL && profile_json.length == 0u &&
            profile_json.private_owner == NULL,
        "invalid-UTF-8 profile published partial output");
  dort_status_release(status);
  profile_invalid_utf8 = 0u;

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
  CHECK(status == NULL, "could not start profiling before disable failure");
  profile_disable_failures = 1u;
  memset(&profile_json, 0xff, sizeof(profile_json));
  status = dort_run_options_profiling_finish(options, &profile_json);
  CHECK(status != NULL, "profiling disable failure was swallowed");
  CHECK(profile_json.data == NULL && profile_json.length == 0u &&
            profile_json.private_owner == NULL,
        "profiling disable failure published partial bytes");
  dort_status_release(status);
  {
    uint32_t calls_before_reuse = run_call_count;
    const char* output_names[] = {"Y"};
    status = dort_session_run(&test_session, options, NULL, 0u, output_names,
                              1u, &context.result);
    CHECK(status != NULL && context.result == NULL,
          "unsettled profiling state allowed run-options reuse");
    CHECK(dort_status_code(status) == DORT_ERROR_RUN_FAILED,
          "unsettled profiling state returned the wrong error");
    CHECK(run_call_count == calls_before_reuse,
          "unsettled profiling state reached native Run");
    dort_status_release(status);
  }
  memset(&profile_json, 0, sizeof(profile_json));
  status = dort_run_options_profiling_finish(options, &profile_json);
  CHECK(status == NULL, "profiling disable retry did not recover");
  CHECK(profile_json.length == 2u &&
            memcmp(profile_json.data, "[]", profile_json.length) == 0,
        "profiling disable retry returned unexpected bytes");
  free(profile_json.private_owner);

  status = dort_run_options_profiling_start(options, profile_root);
  CHECK(status == NULL, "could not start profiling before release");
  profile_disable_failures = 1u;
  release_saw_profile_directory = 0u;
  dort_run_options_release(options);
  options = NULL;
  CHECK(release_saw_profile_directory == 1u,
        "native run-options release happened after profile cleanup");
  CHECK(rmdir(profile_root) == 0,
        "release did not clean the active private profile directory");

  printf("Fonix cancel-during-Run test passed.\n");
  return 0;
}
