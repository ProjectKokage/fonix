#define _POSIX_C_SOURCE 200809L

#include "dort_internal.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(condition, message)                                              \
  do {                                                                         \
    if (!(condition)) {                                                        \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));      \
      return 1;                                                                \
    }                                                                          \
  } while (0)

static uint64_t allocation_epoch = 0u;

static int configure_allocation_fault(size_t fault_point) {
  char epoch[32];
  char fail_at[32];
  int epoch_length = 0;
  int fail_length = 0;

  ++allocation_epoch;
  epoch_length = snprintf(
      epoch, sizeof(epoch), "%llu", (unsigned long long)allocation_epoch);
  fail_length = snprintf(fail_at, sizeof(fail_at), "%zu", fault_point);
  if (epoch_length <= 0 || (size_t)epoch_length >= sizeof(epoch) ||
      fail_length <= 0 || (size_t)fail_length >= sizeof(fail_at)) {
    return 0;
  }
  return setenv("FONIX_TEST_ALLOCATION_EPOCH", epoch, 1) == 0 &&
         setenv("FONIX_TEST_ALLOCATION_FAIL_AT", fail_at, 1) == 0;
}

static dort_status_t* create_test_status(void) {
  return dort_status_create(
      DORT_ERROR_DOMAIN_ORT_STATUS,
      DORT_ERROR_RUN_FAILED,
      73,
      "session_run",
      "ORT diagnostic");
}

static int expect_preserved_metadata(const dort_status_t* status) {
  CHECK(status != NULL, "status creation returned null");
  CHECK(
      dort_status_domain(status) == DORT_ERROR_DOMAIN_ORT_STATUS,
      "status domain was not preserved");
  CHECK(
      dort_status_code(status) == DORT_ERROR_RUN_FAILED,
      "status code was not preserved");
  CHECK(dort_status_ort_code(status) == 73, "ORT status code was not preserved");
  CHECK(!status->is_static, "field allocation fault returned emergency status");
  return 0;
}

static int expect_clean_retry(void) {
  dort_status_t* status = NULL;
  CHECK(configure_allocation_fault(0u), "could not disable allocation fault");
  status = create_test_status();
  CHECK(expect_preserved_metadata(status) == 0, "clean retry metadata changed");
  CHECK(
      strcmp(dort_status_operation(status), "session_run") == 0,
      "clean retry operation changed");
  CHECK(
      strcmp(dort_status_message(status), "ORT diagnostic") == 0,
      "clean retry message changed");
  CHECK(status->owns_operation, "clean retry did not own operation copy");
  CHECK(status->owns_message, "clean retry did not own message copy");
  dort_status_release(status);
  return 0;
}

static int test_status_struct_allocation_failure(void) {
  dort_status_t* status = NULL;
  CHECK(configure_allocation_fault(1u), "could not configure allocation fault");
  status = create_test_status();
  CHECK(status != NULL, "struct allocation failure returned null");
  CHECK(status->is_static, "struct allocation failure was not static");
  CHECK(
      dort_status_domain(status) == DORT_ERROR_DOMAIN_ALLOCATION,
      "struct allocation failure returned wrong domain");
  CHECK(
      dort_status_code(status) == DORT_ERROR_ALLOCATION_FAILED,
      "struct allocation failure returned wrong code");
  CHECK(dort_status_ort_code(status) == 0, "emergency status retained ORT code");
  CHECK(
      strcmp(dort_status_operation(status), "allocation") == 0,
      "emergency status operation changed");
  CHECK(
      dort_status_message(status)[0] != '\0',
      "emergency status message was empty");
  CHECK(!status->owns_operation, "emergency status owns static operation");
  CHECK(!status->owns_message, "emergency status owns static message");
  dort_status_release(status);
  dort_status_release(status);
  return expect_clean_retry();
}

static int test_operation_copy_allocation_failure(void) {
  dort_status_t* status = NULL;
  CHECK(configure_allocation_fault(2u), "could not configure allocation fault");
  status = create_test_status();
  CHECK(expect_preserved_metadata(status) == 0, "operation OOM metadata changed");
  CHECK(
      strcmp(dort_status_operation(status), "unknown") == 0,
      "operation OOM did not use safe fallback");
  CHECK(
      strcmp(dort_status_message(status), "ORT diagnostic") == 0,
      "operation OOM lost message diagnostic");
  CHECK(!status->owns_operation, "operation OOM owns static fallback");
  CHECK(status->owns_message, "operation OOM did not own message copy");
  dort_status_release(status);
  return expect_clean_retry();
}

static int test_message_copy_allocation_failure(void) {
  dort_status_t* status = NULL;
  CHECK(configure_allocation_fault(3u), "could not configure allocation fault");
  status = create_test_status();
  CHECK(expect_preserved_metadata(status) == 0, "message OOM metadata changed");
  CHECK(
      strcmp(dort_status_operation(status), "session_run") == 0,
      "message OOM lost operation diagnostic");
  CHECK(
      strcmp(dort_status_message(status), "Native error details unavailable.") ==
          0,
      "message OOM did not use safe fallback");
  CHECK(status->owns_operation, "message OOM did not own operation copy");
  CHECK(!status->owns_message, "message OOM owns static fallback");
  dort_status_release(status);
  return expect_clean_retry();
}

int main(void) {
  CHECK(
      test_status_struct_allocation_failure() == 0,
      "struct allocation failure test failed");
  CHECK(
      test_operation_copy_allocation_failure() == 0,
      "operation allocation failure test failed");
  CHECK(
      test_message_copy_allocation_failure() == 0,
      "message allocation failure test failed");
  (void)unsetenv("FONIX_TEST_ALLOCATION_EPOCH");
  (void)unsetenv("FONIX_TEST_ALLOCATION_FAIL_AT");
  printf("Fonix status allocation tests passed.\n");
  return 0;
}
