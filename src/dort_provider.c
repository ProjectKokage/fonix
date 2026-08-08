#include "dort_internal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DORT_MAX_DISCOVERED_PROVIDERS 16
#define DORT_MAX_REPORTED_PROVIDER_NAME_BYTES 128u
#define DORT_MAX_PROVIDER_DISCOVERY_JSON_BYTES (64u * 1024u)

dort_status_t* DORT_CALL dort_runtime_available_providers_json(
    const dort_runtime_t* runtime,
    dort_string_t* out_json) {
  const OrtApi* api = NULL;
  char** providers = NULL;
  char** escaped = NULL;
  int provider_count = 0;
  int index = 0;
  size_t json_length = 0u;
  char* json = NULL;
  char* cursor = NULL;
  size_t remaining = 0u;
  OrtStatus* ort_status = NULL;
  OrtStatus* release_status = NULL;
  dort_status_t* status = NULL;

  if (out_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_available_providers_json",
        "The output provider-discovery string is null.");
  }
  memset(out_json, 0, sizeof(*out_json));
  out_json->struct_size = (uint32_t)sizeof(*out_json);
  if (!dort_runtime_is_valid(runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_available_providers_json",
        "The runtime handle is null or invalid.");
  }
  api = dort_runtime_api(runtime);
  if (api == NULL || api->GetAvailableProviders == NULL ||
      api->ReleaseAvailableProviders == NULL || api->GetErrorCode == NULL ||
      api->GetErrorMessage == NULL || api->ReleaseStatus == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_ORT_API_UNAVAILABLE,
        0,
        "runtime_available_providers_json",
        "ONNX Runtime API 27 does not expose bounded provider discovery.");
  }

  ort_status = api->GetAvailableProviders(&providers, &provider_count);
  if (ort_status != NULL) {
    status = dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_PROVIDER_UNSUPPORTED,
        "runtime_available_providers_json");
    goto cleanup;
  }
  if (provider_count < 0 || provider_count > DORT_MAX_DISCOVERED_PROVIDERS ||
      (provider_count == 0) != (providers == NULL)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        provider_count > DORT_MAX_DISCOVERED_PROVIDERS
            ? DORT_ERROR_LIMIT_EXCEEDED
            : DORT_ERROR_ORT_API_UNAVAILABLE,
        0,
        "runtime_available_providers_json",
        "ONNX Runtime returned an invalid or oversized provider array.");
    goto cleanup;
  }

  if (provider_count > 0) {
    escaped = (char**)calloc((size_t)provider_count, sizeof(*escaped));
    if (escaped == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "runtime_available_providers_json",
          "Could not allocate bounded provider-discovery storage.");
      goto cleanup;
    }
  }
  json_length = strlen("{\"schemaVersion\":1,\"reportedNames\":[]}");
  for (index = 0; index < provider_count; ++index) {
    size_t name_length = 0u;
    size_t prior = 0u;
    int validation = dort_bounded_utf8_length(
        providers[index],
        DORT_MAX_REPORTED_PROVIDER_NAME_BYTES,
        0,
        &name_length);
    if (validation != DORT_ERROR_NONE) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          validation,
          0,
          "runtime_available_providers_json",
          "ONNX Runtime returned an invalid provider name.");
      goto cleanup;
    }
    for (prior = 0u; prior < (size_t)index; ++prior) {
      if (strcmp(providers[prior], providers[index]) == 0) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_ORT_API,
            DORT_ERROR_ORT_API_UNAVAILABLE,
            0,
            "runtime_available_providers_json",
            "ONNX Runtime returned a duplicate provider name.");
        goto cleanup;
      }
    }
    escaped[index] = dort_json_escape(providers[index], name_length);
    if (escaped[index] == NULL ||
        !dort_checked_add_size(json_length, strlen(escaped[index]), &json_length) ||
        !dort_checked_add_size(json_length, 3u, &json_length) ||
        json_length > DORT_MAX_PROVIDER_DISCOVERY_JSON_BYTES) {
      status = dort_status_create(
          escaped[index] == NULL ? DORT_ERROR_DOMAIN_ALLOCATION
                                 : DORT_ERROR_DOMAIN_ORT_API,
          escaped[index] == NULL ? DORT_ERROR_ALLOCATION_FAILED
                                 : DORT_ERROR_LIMIT_EXCEEDED,
          0,
          "runtime_available_providers_json",
          "Provider discovery exceeds its bounded JSON contract.");
      goto cleanup;
    }
  }
  if (!dort_checked_add_size(json_length, 1u, &json_length)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_OVERFLOW,
        0,
        "runtime_available_providers_json",
        "Provider discovery JSON size overflowed.");
    goto cleanup;
  }
  json = (char*)calloc(json_length, 1u);
  if (json == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_available_providers_json",
        "Could not allocate provider-discovery JSON.");
    goto cleanup;
  }
  cursor = json;
  remaining = json_length;
  {
    int written = snprintf(
        cursor, remaining, "{\"schemaVersion\":1,\"reportedNames\":[");
    if (written < 0 || (size_t)written >= remaining) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PLATFORM,
          0,
          "runtime_available_providers_json",
          "Could not encode provider-discovery JSON.");
      goto cleanup;
    }
    cursor += (size_t)written;
    remaining -= (size_t)written;
  }
  for (index = 0; index < provider_count; ++index) {
    int written = snprintf(
        cursor,
        remaining,
        "%s\"%s\"",
        index == 0 ? "" : ",",
        escaped[index]);
    if (written < 0 || (size_t)written >= remaining) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PLATFORM,
          0,
          "runtime_available_providers_json",
          "Could not encode a provider-discovery entry.");
      goto cleanup;
    }
    cursor += (size_t)written;
    remaining -= (size_t)written;
  }
  if (remaining < 3u) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_PLATFORM,
        0,
        "runtime_available_providers_json",
        "Provider-discovery JSON accounting was inconsistent.");
    goto cleanup;
  }
  memcpy(cursor, "]}", 3u);
  status = dort_string_copy(json, out_json);

cleanup:
  if (escaped != NULL) {
    for (index = 0; index < provider_count &&
                    index < DORT_MAX_DISCOVERED_PROVIDERS;
         ++index) {
      free(escaped[index]);
    }
  }
  free(escaped);
  free(json);
  if (provider_count >= 0 &&
      provider_count <= DORT_MAX_DISCOVERED_PROVIDERS &&
      ((provider_count == 0 && providers == NULL) ||
       (provider_count > 0 && providers != NULL))) {
    release_status = api->ReleaseAvailableProviders(providers, provider_count);
    if (release_status != NULL) {
      if (status == NULL) {
        status = dort_status_from_ort(
            runtime,
            release_status,
            DORT_ERROR_PROVIDER_UNSUPPORTED,
            "runtime_available_providers_release");
      } else {
        api->ReleaseStatus(release_status);
      }
    }
  }
  if (status != NULL) {
    dort_string_release(out_json);
    out_json->struct_size = (uint32_t)sizeof(*out_json);
  }
  return status;
}
