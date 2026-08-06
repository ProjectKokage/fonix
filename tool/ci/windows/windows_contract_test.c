#include "dort.h"

#include <stdint.h>
#include <string.h>

int main(void) {
  dort_status_t* status = NULL;
  dort_string_t manifest = {0};

  if (dort_get_abi_version() != 1u ||
      dort_get_ort_api_compatibility_floor() != 27u) {
    return 1;
  }
  status = dort_get_build_manifest_json(&manifest);
  if (status != NULL || manifest.data == NULL ||
      strstr((const char*)manifest.data, "\"runtimeProfile\":\"external\"") ==
          NULL ||
      strstr(
          (const char*)manifest.data,
          "\"allowedRuntimeSources\":[\"process\",\"file\"]") == NULL ||
      strstr((const char*)manifest.data, "\"nativeIdentity\":\"fonix_shim\"") ==
          NULL) {
    dort_status_release(status);
    dort_string_release(&manifest);
    return 2;
  }
  dort_string_release(&manifest);
  if (manifest.data != NULL || manifest.private_owner != NULL) {
    return 3;
  }
  return 0;
}
