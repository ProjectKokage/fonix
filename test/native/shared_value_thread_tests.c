#if defined(__APPLE__)
#define _DARWIN_C_SOURCE 1
#endif
#define _POSIX_C_SOURCE 200809L

#include "dort.h"

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define SHARED_VALUE_ITERATIONS 128u
#define SHARED_VALUE_WAIT_SECONDS 5

#define CHECK(condition, message)                                               \
  do {                                                                          \
    if (!(condition)) {                                                         \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));    \
      result = 1;                                                               \
      goto cleanup;                                                             \
    }                                                                           \
  } while (0)

typedef struct shared_value_gate {
  pthread_mutex_t lock;
  pthread_cond_t condition;
  uint32_t arrivals;
  uint32_t generation;
  int abort_requested;
} shared_value_gate_t;

typedef struct shared_value_arguments {
  dort_runtime_t* runtime;
  dort_value_t* value;
  shared_value_gate_t* gate;
} shared_value_arguments_t;

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
  config.log_id_utf8 = "shared-value-thread-safety";
  config.library_path_utf8 = library;
  config.allowed_root_utf8 = root;
  return config;
}

static int shared_value_gate_abort(shared_value_gate_t* gate) {
  int failed = 0;
  if (pthread_mutex_lock(&gate->lock) != 0) {
    return 1;
  }
  gate->abort_requested = 1;
  if (pthread_cond_broadcast(&gate->condition) != 0) {
    failed = 1;
  }
  if (pthread_mutex_unlock(&gate->lock) != 0) {
    failed = 1;
  }
  return failed;
}

static int shared_value_gate_wait(shared_value_gate_t* gate) {
  struct timespec deadline;
  uint32_t generation = 0u;
  int condition_status = 0;
  int failed = 0;
  if (timespec_get(&deadline, TIME_UTC) != TIME_UTC) {
    (void)shared_value_gate_abort(gate);
    return 1;
  }
  deadline.tv_sec += SHARED_VALUE_WAIT_SECONDS;
  if (pthread_mutex_lock(&gate->lock) != 0) {
    return 1;
  }
  if (gate->abort_requested != 0) {
    (void)pthread_mutex_unlock(&gate->lock);
    return 1;
  }
  generation = gate->generation;
  ++gate->arrivals;
  if (gate->arrivals == 2u) {
    gate->arrivals = 0u;
    ++gate->generation;
    if (pthread_cond_broadcast(&gate->condition) != 0) {
      gate->abort_requested = 1;
      failed = 1;
    }
  } else {
    while (gate->generation == generation && gate->abort_requested == 0 &&
           condition_status == 0) {
      condition_status =
          pthread_cond_timedwait(&gate->condition, &gate->lock, &deadline);
    }
    if (condition_status != 0 || gate->abort_requested != 0) {
      gate->abort_requested = 1;
      (void)pthread_cond_broadcast(&gate->condition);
      failed = 1;
    }
  }
  if (pthread_mutex_unlock(&gate->lock) != 0) {
    failed = 1;
  }
  return failed;
}

static void* exercise_shared_value(void* raw_arguments) {
  shared_value_arguments_t* arguments =
      (shared_value_arguments_t*)raw_arguments;
  size_t iteration = 0u;
  for (iteration = 0u; iteration < SHARED_VALUE_ITERATIONS; ++iteration) {
    const dort_value_t* children[1] = {arguments->value};
    dort_value_t* sequence = NULL;
    dort_status_t* status = NULL;
    if (shared_value_gate_wait(arguments->gate) != 0) {
      return (void*)(uintptr_t)1u;
    }
    status = dort_sequence_create(
        arguments->runtime, children, 1u, &sequence);
    if (status == NULL || sequence != NULL ||
        dort_status_code(status) != DORT_ERROR_VALUE_KIND_UNSUPPORTED) {
      dort_status_release(status);
      dort_value_release(sequence);
      (void)shared_value_gate_abort(arguments->gate);
      return (void*)(uintptr_t)1u;
    }
    dort_status_release(status);
  }
  return NULL;
}

static int run_shared_value_threads(
    dort_runtime_t* runtime,
    dort_value_t* value) {
  pthread_t first_thread;
  pthread_t second_thread;
  void* first_result = NULL;
  void* second_result = NULL;
  int failed = 0;
  int first_created = 0;
  int second_created = 0;
  int first_joined = 0;
  int second_joined = 0;
  shared_value_gate_t gate;
  shared_value_arguments_t first_arguments;
  shared_value_arguments_t second_arguments;
  memset(&gate, 0, sizeof(gate));
  if (pthread_mutex_init(&gate.lock, NULL) != 0) {
    return 1;
  }
  if (pthread_cond_init(&gate.condition, NULL) != 0) {
    (void)pthread_mutex_destroy(&gate.lock);
    return 1;
  }
  first_arguments.runtime = runtime;
  first_arguments.value = value;
  first_arguments.gate = &gate;
  second_arguments = first_arguments;
  if (pthread_create(
          &first_thread, NULL, exercise_shared_value, &first_arguments) == 0) {
    first_created = 1;
  } else {
    failed = 1;
  }
  if (!failed &&
      pthread_create(
          &second_thread, NULL, exercise_shared_value, &second_arguments) == 0) {
    second_created = 1;
  } else if (!failed) {
    failed = 1;
  }
  if (!second_created && first_created) {
    if (shared_value_gate_abort(&gate) != 0) {
      failed = 1;
    }
  }
  if (first_created) {
    if (pthread_join(first_thread, &first_result) == 0) {
      first_joined = 1;
    } else {
      failed = 1;
    }
  }
  if (second_created) {
    if (pthread_join(second_thread, &second_result) == 0) {
      second_joined = 1;
    } else {
      failed = 1;
    }
  }
  if ((!first_created || first_joined) && (!second_created || second_joined)) {
    if (pthread_cond_destroy(&gate.condition) != 0) {
      failed = 1;
    }
    if (pthread_mutex_destroy(&gate.lock) != 0) {
      failed = 1;
    }
  }
  if (first_result != NULL || second_result != NULL) {
    failed = 1;
  }
  return failed;
}

int main(int argc, char** argv) {
  dort_runtime_t* runtime = NULL;
  dort_value_t* value = NULL;
  dort_status_t* status = NULL;
  dort_runtime_config_t config;
  const float data[1] = {1.0f};
  int64_t shape[1] = {1};
  int result = 0;

  CHECK(argc == 3, "expected fake runtime library and root paths");
  config = runtime_config(argv[1], argv[2]);
  status = dort_runtime_open(&config, &runtime);
  CHECK(status == NULL && runtime != NULL, "could not open fake runtime");
  status = dort_tensor_create_copy(
      runtime,
      data,
      sizeof(data),
      shape,
      1u,
      DORT_TENSOR_FLOAT32,
      &value);
  CHECK(status == NULL && value != NULL, "could not create shared tensor");
  CHECK(
      run_shared_value_threads(runtime, value) == 0,
      "concurrent shared-value validation failed");

cleanup:
  dort_status_release(status);
  dort_value_release(value);
  dort_runtime_release(runtime);
  if (result == 0) {
    printf("Fonix shared-value thread-safety test passed.\n");
  }
  return result;
}
