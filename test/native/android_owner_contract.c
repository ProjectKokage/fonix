#include "dort_internal.h"

#include <stdio.h>
#include <string.h>

#define CHECK(condition, message)                                         \
  do {                                                                    \
    if (!(condition)) {                                                   \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message)); \
      return 1;                                                           \
    }                                                                     \
  } while (0)

int main(void) {
  dort_string_t manifest = {0};
  dort_status_t* status = dort_get_build_manifest_json(&manifest);
  CHECK(status == NULL, "sherpa build manifest failed");
  CHECK(manifest.data != NULL, "sherpa build manifest is missing");
  CHECK(
      strstr(
          (const char*)manifest.data,
          "\"androidRuntimeOwner\":\"sherpa\"") != NULL,
      "sherpa build manifest omitted its runtime owner");
  CHECK(
      strstr(
          (const char*)manifest.data,
          "\"allowedRuntimeSources\":[\"process\"]") != NULL,
      "sherpa build manifest reported the wrong source policy");
  dort_string_release(&manifest);
  CHECK(
      dort_runtime_profile_supports(DORT_RUNTIME_SOURCE_PROCESS),
      "sherpa-owned Android shim rejected process runtime source");
  CHECK(
      !dort_runtime_profile_supports(DORT_RUNTIME_SOURCE_FILE),
      "sherpa-owned Android shim accepted an explicit file runtime");
  CHECK(
      !dort_runtime_profile_supports(DORT_RUNTIME_SOURCE_BUNDLED),
      "sherpa-owned Android shim accepted a bundled runtime");
  CHECK(
      !dort_runtime_profile_supports(DORT_RUNTIME_SOURCE_LINKED),
      "sherpa-owned Android shim accepted a linked runtime");
  return 0;
}
