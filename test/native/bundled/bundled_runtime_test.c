#include "dort.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition, message)                                               \
  do {                                                                          \
    if (!(condition)) {                                                         \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));   \
      return 1;                                                                 \
    }                                                                           \
  } while (0)

static dort_runtime_config_t bundled_config(void) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = DORT_RUNTIME_SOURCE_BUNDLED;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "bundled-runtime-test";
  return config;
}

static int expect_invalid_config(dort_status_t* status) {
  CHECK(status != NULL, "bundled loader path override returned no status");
  CHECK(
      dort_status_domain(status) == DORT_ERROR_DOMAIN_SHIM,
      "bundled loader path override returned the wrong domain");
  CHECK(
      dort_status_code(status) == DORT_ERROR_INVALID_ARGUMENT,
      "bundled loader path override returned the wrong code");
  CHECK(
      strstr(dort_status_message(status), "must not contain loader paths") !=
          NULL,
      "bundled loader path override returned the wrong message");
  dort_status_release(status);
  return 0;
}

int main(void) {
  dort_runtime_config_t config = bundled_config();
  dort_runtime_t* runtime = NULL;
  dort_status_t* status = dort_runtime_open(&config, &runtime);
  dort_string_t info;

  if (status != NULL) {
    fprintf(
        stderr,
        "FAIL bundled open: domain=%u code=%d operation=%s message=%s\n",
        dort_status_domain(status),
        dort_status_code(status),
        dort_status_operation(status),
        dort_status_message(status));
    dort_status_release(status);
    return 1;
  }
  CHECK(runtime != NULL, "bundled open returned a null runtime");
  memset(&info, 0, sizeof(info));
  status = dort_runtime_info_json(runtime, &info);
  CHECK(status == NULL, "bundled runtime information failed");
  CHECK(info.data != NULL, "bundled runtime information is empty");
  CHECK(
      strstr((const char*)info.data, "\"runtimeProfile\":\"bundled\"") !=
          NULL,
      "bundled runtime profile was not reported");
  CHECK(
      strstr((const char*)info.data, "\"runtimeSource\":\"bundled\"") !=
          NULL,
      "bundled runtime source was not reported");
  CHECK(
      strstr((const char*)info.data, "1.27.99-fonix-test") != NULL,
      "adjacent fake runtime version was not negotiated");
  dort_string_release(&info);
  dort_runtime_release(runtime);

  config = bundled_config();
  config.library_path_utf8 = "C:\\untrusted\\onnxruntime.dll";
  config.allowed_root_utf8 = "C:\\untrusted";
  status = dort_runtime_open(&config, &runtime);
  CHECK(runtime == NULL, "bundled path override returned a runtime");
  CHECK(
      expect_invalid_config(status) == 0,
      "bundled path override validation failed");
  puts("Fonix adjacent bundled runtime test passed.");
  return 0;
}
