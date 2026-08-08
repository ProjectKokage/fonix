#include "dort_internal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char dort_emergency_operation[] = "allocation";
static char dort_emergency_message[] =
    "Native memory allocation failed while creating an error status.";
static dort_status_t dort_emergency_status = {
    DORT_ERROR_DOMAIN_ALLOCATION,
    DORT_ERROR_ALLOCATION_FAILED,
    0,
    dort_emergency_operation,
    dort_emergency_message,
    1,
};

static char* dort_copy_status_field(const char* value, const char* fallback) {
  const char* selected = value == NULL ? fallback : value;
  size_t length = 0u;
  int validation = dort_bounded_utf8_length(
      selected, DORT_MAX_STATUS_MESSAGE_BYTES, 1, &length);
  if (validation != DORT_ERROR_NONE) {
    selected = fallback;
    length = strlen(fallback);
  }
  return dort_copy_c_string(selected, length);
}

dort_status_t* dort_status_create(
    uint32_t domain,
    int32_t code,
    int32_t ort_code,
    const char* operation,
    const char* message) {
  dort_status_t* status = (dort_status_t*)dort_memory_allocate_zeroed(1u, sizeof(*status));
  if (status == NULL) {
    return &dort_emergency_status;
  }
  status->domain = domain;
  status->code = code;
  status->ort_code = ort_code;
  status->operation = dort_copy_status_field(operation, "unknown");
  status->message = dort_copy_status_field(message, "Native error details unavailable.");
  if (status->operation == NULL || status->message == NULL) {
    free(status->operation);
    free(status->message);
    free(status);
    return &dort_emergency_status;
  }
  return status;
}

dort_status_t* dort_status_vcreatef(
    uint32_t domain,
    int32_t code,
    int32_t ort_code,
    const char* operation,
    const char* format,
    va_list arguments) {
  char message[DORT_MAX_STATUS_MESSAGE_BYTES + 1u];
  int written = 0;

  if (format == NULL) {
    return dort_status_create(domain, code, ort_code, operation, NULL);
  }
  written = vsnprintf(message, sizeof(message), format, arguments);
  if (written < 0) {
    return dort_status_create(
        domain, code, ort_code, operation, "Native error formatting failed.");
  }
  if ((size_t)written >= sizeof(message)) {
    static const char suffix[] = " [truncated]";
    const size_t suffix_length = sizeof(suffix) - 1u;
    const size_t start = sizeof(message) - suffix_length - 1u;
    memcpy(message + start, suffix, suffix_length + 1u);
  }
  return dort_status_create(domain, code, ort_code, operation, message);
}

dort_status_t* dort_status_createf(
    uint32_t domain,
    int32_t code,
    int32_t ort_code,
    const char* operation,
    const char* format,
    ...) {
  dort_status_t* status = NULL;
  va_list arguments;
  va_start(arguments, format);
  status = dort_status_vcreatef(
      domain, code, ort_code, operation, format, arguments);
  va_end(arguments);
  return status;
}

uint32_t DORT_CALL dort_status_domain(const dort_status_t* status) {
  return status == NULL ? DORT_ERROR_DOMAIN_NONE : status->domain;
}

int32_t DORT_CALL dort_status_code(const dort_status_t* status) {
  return status == NULL ? DORT_ERROR_NONE : status->code;
}

int32_t DORT_CALL dort_status_ort_code(const dort_status_t* status) {
  return status == NULL ? 0 : status->ort_code;
}

const char* DORT_CALL dort_status_operation(const dort_status_t* status) {
  return status == NULL || status->operation == NULL ? "" : status->operation;
}

const char* DORT_CALL dort_status_message(const dort_status_t* status) {
  return status == NULL || status->message == NULL ? "" : status->message;
}

void DORT_CALL dort_status_release(dort_status_t* status) {
  if (status == NULL || status->is_static) {
    return;
  }
  free(status->operation);
  free(status->message);
  free(status);
}
