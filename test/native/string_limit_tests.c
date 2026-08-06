#include "dort_internal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(condition, message)                                                \
  do {                                                                           \
    if (!(condition)) {                                                          \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));      \
      return 1;                                                                  \
    }                                                                            \
  } while (0)

int main(void) {
  char* exact = (char*)malloc(DORT_MAX_OWNED_STRING_BYTES + 1u);
  char* oversized = (char*)malloc(DORT_MAX_OWNED_STRING_BYTES + 2u);
  char* status_source = (char*)malloc(DORT_MAX_STATUS_MESSAGE_BYTES * 2u + 1u);
  dort_string_t value;
  dort_status_t* status = NULL;
  CHECK(exact != NULL && oversized != NULL && status_source != NULL, "allocation failed");
  memset(exact, 'a', DORT_MAX_OWNED_STRING_BYTES);
  exact[DORT_MAX_OWNED_STRING_BYTES] = '\0';
  memset(oversized, 'b', DORT_MAX_OWNED_STRING_BYTES + 1u);
  oversized[DORT_MAX_OWNED_STRING_BYTES + 1u] = '\0';
  memset(status_source, 'c', DORT_MAX_STATUS_MESSAGE_BYTES * 2u);
  status_source[DORT_MAX_STATUS_MESSAGE_BYTES * 2u] = '\0';

  memset(&value, 0, sizeof(value));
  status = dort_string_copy(exact, &value);
  CHECK(status == NULL, "exact owned-string limit was rejected");
  CHECK(value.length == DORT_MAX_OWNED_STRING_BYTES, "owned-string length changed");
  dort_string_release(&value);

  status = dort_string_copy(oversized, &value);
  CHECK(status != NULL, "oversized owned string was accepted");
  CHECK(dort_status_code(status) == DORT_ERROR_LIMIT_EXCEEDED, "wrong oversized code");
  CHECK(value.data == NULL && value.private_owner == NULL, "failure did not clear string");
  dort_status_release(status);

  status = dort_status_createf(
      DORT_ERROR_DOMAIN_SHIM,
      DORT_ERROR_INVALID_ARGUMENT,
      0,
      "string_limit_test",
      "%s",
      status_source);
  CHECK(status != NULL, "long status creation failed");
  CHECK(
      strlen(dort_status_message(status)) <= DORT_MAX_STATUS_MESSAGE_BYTES,
      "status exceeded its independent byte limit");
  CHECK(
      strstr(dort_status_message(status), "[truncated]") != NULL,
      "long status did not report truncation");
  dort_status_release(status);
  free(exact);
  free(oversized);
  free(status_source);
  printf("Fonix owned-string/status limit tests passed.\n");
  return 0;
}
