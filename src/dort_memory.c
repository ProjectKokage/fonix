#if !defined(_WIN32)
#define _POSIX_C_SOURCE 200112L
#endif

#include "dort_internal.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>

#if defined(_WIN32)
#include <malloc.h>
#endif

#if defined(FONIX_ALLOCATION_TESTING)
#include <stdatomic.h>

#define DORT_TEST_ALLOCATION_EPOCH "FONIX_TEST_ALLOCATION_EPOCH"
#define DORT_TEST_ALLOCATION_FAIL_AT "FONIX_TEST_ALLOCATION_FAIL_AT"

static atomic_uint_fast64_t dort_test_allocation_epoch = ATOMIC_VAR_INIT(0u);
static atomic_size_t dort_test_allocation_attempt = ATOMIC_VAR_INIT(0u);

static int dort_parse_test_size(const char* value, uint64_t* out_value) {
  char* end = NULL;
  unsigned long long parsed = 0u;
  if (value == NULL || value[0] == '\0' || out_value == NULL ||
      value[0] == '-' || value[0] == '+') {
    return 0;
  }
  errno = 0;
  parsed = strtoull(value, &end, 10);
  if (errno != 0 || end == value || *end != '\0') {
    return 0;
  }
  *out_value = (uint64_t)parsed;
  return 1;
}

static int dort_test_allocation_should_fail(void) {
  const char* epoch_value = getenv(DORT_TEST_ALLOCATION_EPOCH);
  const char* failure_value = getenv(DORT_TEST_ALLOCATION_FAIL_AT);
  uint64_t epoch = 0u;
  uint64_t fail_at = 0u;
  uint64_t observed_epoch = 0u;
  size_t attempt = 0u;

  if (!dort_parse_test_size(epoch_value, &epoch) || epoch == 0u ||
      !dort_parse_test_size(failure_value, &fail_at)) {
    return 0;
  }
  observed_epoch =
      atomic_load_explicit(&dort_test_allocation_epoch, memory_order_acquire);
  if (observed_epoch != epoch) {
    atomic_store_explicit(&dort_test_allocation_attempt, 0u,
                          memory_order_release);
    atomic_store_explicit(&dort_test_allocation_epoch, epoch,
                          memory_order_release);
  }
  attempt = atomic_fetch_add_explicit(&dort_test_allocation_attempt, 1u,
                                      memory_order_relaxed) +
            1u;
  return fail_at != 0u && fail_at <= (uint64_t)SIZE_MAX &&
         attempt == (size_t)fail_at;
}
#else
static int dort_test_allocation_should_fail(void) { return 0; }
#endif

void* dort_memory_allocate(size_t size) {
  if (dort_test_allocation_should_fail()) {
    return NULL;
  }
  return malloc(size);
}

void* dort_memory_allocate_zeroed(size_t count, size_t size) {
  if (dort_test_allocation_should_fail()) {
    return NULL;
  }
  return calloc(count, size);
}

void* dort_memory_reallocate(void* pointer, size_t size) {
  if (dort_test_allocation_should_fail()) {
    return NULL;
  }
  return realloc(pointer, size);
}

void* dort_memory_aligned_allocate(size_t size, size_t alignment) {
  void* result = NULL;
  if (dort_test_allocation_should_fail()) {
    return NULL;
  }
#if defined(_WIN32)
  result = _aligned_malloc(size, alignment);
#else
  if (posix_memalign(&result, alignment, size) != 0) {
    result = NULL;
  }
#endif
  return result;
}

void dort_memory_aligned_free(void* pointer) {
#if defined(_WIN32)
  _aligned_free(pointer);
#else
  free(pointer);
#endif
}
