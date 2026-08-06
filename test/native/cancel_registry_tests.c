#include "dort.h"

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TEST_CANCEL_TOKEN_LIMIT 1024u

#define CHECK(condition, message)                                                \
  do {                                                                           \
    if (!(condition)) {                                                          \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));      \
      return 1;                                                                  \
    }                                                                            \
  } while (0)

typedef struct cancel_thread_context {
  uint64_t token;
  int32_t status_code;
  uint32_t did_request;
} cancel_thread_context_t;

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
  config.log_id_utf8 = "fonix-cancel-registry-test";
  config.library_path_utf8 = library;
  config.allowed_root_utf8 = root;
  return config;
}

static int expect_code(dort_status_t* status, int32_t code) {
  CHECK(status != NULL, "expected an error status");
  CHECK(dort_status_code(status) == code, "unexpected error code");
  dort_status_release(status);
  return 0;
}

static void* request_cancel(void* opaque_context) {
  cancel_thread_context_t* context =
      (cancel_thread_context_t*)opaque_context;
  dort_status_t* status = dort_cancel_token_request(
      context->token, &context->did_request);
  context->status_code =
      status == NULL ? DORT_ERROR_NONE : dort_status_code(status);
  dort_status_release(status);
  return NULL;
}

static int test_token_lifecycle(dort_runtime_t* runtime) {
  dort_run_options_t* options = NULL;
  dort_status_t* status = NULL;
  uint64_t token = UINT64_MAX;
  uint64_t duplicate = UINT64_MAX;
  uint32_t did_request = UINT32_MAX;
  pthread_t thread;
  cancel_thread_context_t context;

  status = dort_run_options_create(runtime, &options);
  CHECK(status == NULL && options != NULL, "could not create run options");
  status = dort_cancel_token_register(options, &token);
  CHECK(status == NULL && token != 0u, "could not register cancellation");
  status = dort_cancel_token_register(options, &duplicate);
  CHECK(duplicate == 0u, "duplicate registration did not clear output");
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "duplicate registration assertion failed");

  memset(&context, 0, sizeof(context));
  context.token = token;
  CHECK(
      pthread_create(&thread, NULL, request_cancel, &context) == 0,
      "could not create cancellation thread");
  CHECK(
      pthread_join(thread, NULL) == 0,
      "could not join cancellation thread");
  CHECK(
      context.status_code == DORT_ERROR_NONE && context.did_request == 1u,
      "cross-thread cancellation did not request termination");

  did_request = UINT32_MAX;
  status = dort_cancel_token_request(token, &did_request);
  CHECK(status == NULL, "double cancellation returned an error");
  CHECK(did_request == 0u, "double cancellation was not idempotent");

  /* The registry retain is authoritative through successful finish. */
  dort_run_options_release(options);
  options = NULL;
  did_request = UINT32_MAX;
  status = dort_cancel_token_finish(token, &did_request);
  CHECK(status == NULL, "could not finish cancellation after owner release");
  CHECK(did_request == 1u, "finish did not report requested termination");

  did_request = UINT32_MAX;
  status = dort_cancel_token_request(token, &did_request);
  CHECK(did_request == 0u, "unknown cancellation did not clear output");
  CHECK(
      expect_code(status, DORT_ERROR_CANCEL_TOKEN_UNKNOWN) == 0,
      "cancel-after-finish assertion failed");
  did_request = UINT32_MAX;
  status = dort_cancel_token_finish(token, &did_request);
  CHECK(did_request == 0u, "unknown finish did not clear settlement output");
  CHECK(
      expect_code(status, DORT_ERROR_CANCEL_TOKEN_UNKNOWN) == 0,
      "double-finish assertion failed");
  return 0;
}

static int test_finish_request_races(dort_runtime_t* runtime) {
  size_t iteration = 0u;
  for (iteration = 0u; iteration < 256u; ++iteration) {
    dort_run_options_t* options = NULL;
    dort_status_t* status = dort_run_options_create(runtime, &options);
    uint64_t token = 0u;
    uint32_t was_requested = UINT32_MAX;
    pthread_t thread;
    cancel_thread_context_t context;
    CHECK(status == NULL && options != NULL, "race options creation failed");
    status = dort_cancel_token_register(options, &token);
    CHECK(status == NULL && token != 0u, "race registration failed");
    memset(&context, 0, sizeof(context));
    context.token = token;
    CHECK(
        pthread_create(&thread, NULL, request_cancel, &context) == 0,
        "could not create race thread");
    status = dort_cancel_token_finish(token, &was_requested);
    CHECK(status == NULL, "finish lost the request race");
    CHECK(was_requested <= 1u, "finish returned an invalid settlement boolean");
    CHECK(
        pthread_join(thread, NULL) == 0,
        "could not join race thread");
    CHECK(
        context.status_code == DORT_ERROR_NONE ||
            context.status_code == DORT_ERROR_CANCEL_TOKEN_UNKNOWN,
        "request race returned an unexpected error");
    dort_run_options_release(options);
  }
  return 0;
}

static int test_registry_bound(dort_runtime_t* runtime) {
  dort_run_options_t** options = NULL;
  uint64_t* tokens = NULL;
  dort_run_options_t* overflow_options = NULL;
  dort_status_t* status = NULL;
  uint64_t overflow_token = UINT64_MAX;
  uint32_t was_requested = UINT32_MAX;
  size_t index = 0u;

  options = (dort_run_options_t**)calloc(
      TEST_CANCEL_TOKEN_LIMIT, sizeof(*options));
  tokens = (uint64_t*)calloc(TEST_CANCEL_TOKEN_LIMIT, sizeof(*tokens));
  CHECK(options != NULL && tokens != NULL, "could not allocate test vectors");
  for (index = 0u; index < TEST_CANCEL_TOKEN_LIMIT; ++index) {
    status = dort_run_options_create(runtime, &options[index]);
    CHECK(status == NULL && options[index] != NULL, "options creation failed");
    status = dort_cancel_token_register(options[index], &tokens[index]);
    CHECK(status == NULL && tokens[index] != 0u, "bounded registration failed");
  }
  status = dort_run_options_create(runtime, &overflow_options);
  CHECK(status == NULL && overflow_options != NULL, "overflow options failed");
  status = dort_cancel_token_register(overflow_options, &overflow_token);
  CHECK(overflow_token == 0u, "full registry did not clear output token");
  CHECK(
      expect_code(status, DORT_ERROR_CANCEL_REGISTRY_FULL) == 0,
      "registry bound assertion failed");
  dort_run_options_release(overflow_options);

  for (index = 0u; index < TEST_CANCEL_TOKEN_LIMIT; ++index) {
    was_requested = UINT32_MAX;
    status = dort_cancel_token_finish(tokens[index], &was_requested);
    CHECK(status == NULL, "bounded token finish failed");
    CHECK(was_requested == 0u, "uncancelled token settled as requested");
    dort_run_options_release(options[index]);
  }
  free(tokens);
  free(options);
  return 0;
}

int main(int argc, char** argv) {
  dort_runtime_t* runtime = NULL;
  dort_runtime_config_t config;
  dort_status_t* status = NULL;
  uint64_t token = UINT64_MAX;
  uint32_t did_request = UINT32_MAX;

  CHECK(argc == 3, "expected fake runtime library and root paths");
  config = runtime_config(argv[1], argv[2]);
  status = dort_runtime_open(&config, &runtime);
  CHECK(status == NULL && runtime != NULL, "could not open fake runtime");

  status = dort_cancel_token_register(NULL, &token);
  CHECK(token == 0u, "invalid registration did not clear output token");
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "invalid registration assertion failed");
  status = dort_cancel_token_register(NULL, NULL);
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "null registration output assertion failed");
  status = dort_cancel_token_request(0u, &did_request);
  CHECK(did_request == 0u, "unknown request did not clear output");
  CHECK(
      expect_code(status, DORT_ERROR_CANCEL_TOKEN_UNKNOWN) == 0,
      "unknown token assertion failed");
  status = dort_cancel_token_request(1u, NULL);
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "null request output assertion failed");
  status = dort_cancel_token_finish(1u, NULL);
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_ARGUMENT) == 0,
      "null finish output assertion failed");

  CHECK(test_token_lifecycle(runtime) == 0, "token lifecycle failed");
  CHECK(test_finish_request_races(runtime) == 0, "finish/request race failed");
  CHECK(test_registry_bound(runtime) == 0, "registry bound failed");

  dort_runtime_release(runtime);
  printf("Fonix cancellation-registry tests passed.\n");
  return 0;
}
