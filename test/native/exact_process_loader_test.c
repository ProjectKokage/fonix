#include "dort.h"

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(condition, message)                                           \
  do {                                                                      \
    if (!(condition)) {                                                     \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message)); \
      return 1;                                                             \
    }                                                                       \
  } while (0)

#define LOAD_FUNCTION(handle, name, target)                              \
  do {                                                                   \
    void* symbol = NULL;                                                 \
    dlerror();                                                           \
    symbol = dlsym((handle), (name));                                    \
    CHECK(dlerror() == NULL && symbol != NULL, "missing shim export");  \
    _Static_assert(                                                      \
        sizeof(target) == sizeof(symbol),                                \
        "POSIX data and function pointers must have matching sizes");  \
    memcpy(&(target), &symbol, sizeof(symbol));                          \
  } while (0)

typedef dort_status_t* (DORT_CALL* runtime_open_function_t)(
    const dort_runtime_config_t*, dort_runtime_t**);
typedef void (DORT_CALL* runtime_release_function_t)(dort_runtime_t*);
typedef dort_status_t* (DORT_CALL* runtime_info_function_t)(
    const dort_runtime_t*, dort_string_t*);
typedef dort_status_t* (DORT_CALL* build_manifest_function_t)(dort_string_t*);
typedef void (DORT_CALL* string_release_function_t)(dort_string_t*);
typedef uint32_t (DORT_CALL* status_domain_function_t)(const dort_status_t*);
typedef int32_t (DORT_CALL* status_code_function_t)(const dort_status_t*);
typedef const char* (DORT_CALL* status_message_function_t)(
    const dort_status_t*);
typedef void (DORT_CALL* status_release_function_t)(dort_status_t*);

static dort_runtime_config_t process_config(void) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = DORT_RUNTIME_SOURCE_PROCESS;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "exact-process-loader-test";
  return config;
}

int main(int argc, char** argv) {
  void* preload = NULL;
  void* shim = NULL;
  runtime_open_function_t runtime_open = NULL;
  runtime_release_function_t runtime_release = NULL;
  runtime_info_function_t runtime_info = NULL;
  build_manifest_function_t build_manifest = NULL;
  string_release_function_t string_release = NULL;
  status_domain_function_t status_domain = NULL;
  status_code_function_t status_code = NULL;
  status_message_function_t status_message = NULL;
  status_release_function_t status_release = NULL;
  dort_runtime_t* runtime = NULL;
  dort_status_t* status = NULL;
  dort_string_t text;
  dort_runtime_config_t config;
  const char* mode = NULL;
  const char* preferred_name = NULL;
  int expect_success = 0;

  CHECK(argc == 5, "expected shim, exact runtime, wrong runtime, and mode");
  mode = argv[4];
  if (strcmp(mode, "runtime-first") == 0) {
    preload = dlopen(argv[2], RTLD_NOW | RTLD_GLOBAL);
    CHECK(preload != NULL, "could not preload the exact runtime");
    expect_success = 1;
  } else if (strcmp(mode, "shim-first") == 0) {
    expect_success = 1;
  } else if (strcmp(mode, "exact-name") == 0) {
    preferred_name = FONIX_TEST_EXACT_PROCESS_RUNTIME_BASENAME;
    expect_success = 1;
  } else if (strcmp(mode, "wrong-first") == 0) {
    preload = dlopen(argv[3], RTLD_NOW | RTLD_GLOBAL);
    CHECK(preload != NULL, "could not preload the wrong runtime");
  } else if (strcmp(mode, "wrong-name") == 0) {
    preferred_name = FONIX_TEST_WRONG_PROCESS_RUNTIME_BASENAME;
  } else {
    CHECK(
        strcmp(mode, "search-only") == 0 ||
            strcmp(mode, "symlink-adjacent") == 0 ||
            strcmp(mode, "stale-build-info") == 0 ||
            strcmp(mode, "null-build-info") == 0 ||
            strcmp(mode, "oversize-build-info") == 0 ||
            strcmp(mode, "prefix-build-info") == 0 ||
            strcmp(mode, "suffix-build-info") == 0 ||
            strcmp(mode, "nonascii-build-info") == 0,
        "unknown exact-loader mode");
  }

  shim = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  CHECK(shim != NULL, "could not load the exact-process shim");
  LOAD_FUNCTION(shim, "dort_runtime_open", runtime_open);
  LOAD_FUNCTION(shim, "dort_runtime_release", runtime_release);
  LOAD_FUNCTION(shim, "dort_runtime_info_json", runtime_info);
  LOAD_FUNCTION(shim, "dort_get_build_manifest_json", build_manifest);
  LOAD_FUNCTION(shim, "dort_string_release", string_release);
  LOAD_FUNCTION(shim, "dort_status_domain", status_domain);
  LOAD_FUNCTION(shim, "dort_status_code", status_code);
  LOAD_FUNCTION(shim, "dort_status_message", status_message);
  LOAD_FUNCTION(shim, "dort_status_release", status_release);

  memset(&text, 0, sizeof(text));
  status = build_manifest(&text);
  CHECK(status == NULL, "could not read the exact-process build manifest");
  CHECK(text.data != NULL, "exact-process build manifest was empty");
  CHECK(
      strstr(
          (const char*)text.data,
          "\"buildId\":\"" FONIX_TEST_EXACT_PROCESS_BUILD_ID "\"") !=
          NULL,
      "build manifest did not bind the exact target and basename");
  CHECK(
      strstr(
          (const char*)text.data,
          "\"allowedRuntimeSources\":[\"process\"]") != NULL,
      "exact process build manifest allowed a bypass runtime source");
  string_release(&text);

  config = process_config();
  if (preferred_name != NULL) {
    config.preferred_library_names_utf8 = &preferred_name;
    config.preferred_library_name_count = 1u;
  }
  status = runtime_open(&config, &runtime);
  if (expect_success) {
    CHECK(status == NULL, "exact process runtime open failed");
    CHECK(runtime != NULL, "exact process runtime open returned null");
    memset(&text, 0, sizeof(text));
    status = runtime_info(runtime, &text);
    CHECK(status == NULL, "exact process runtime info failed");
    CHECK(
        strstr((const char*)text.data, "1.27.99-fonix-test") != NULL,
        "exact process loader selected the wrong runtime bytes");
    CHECK(
        strstr(
            (const char*)text.data,
            FONIX_TEST_EXACT_PROCESS_RUNTIME_BASENAME) != NULL,
        "exact process runtime diagnostics omitted the exact basename");
    string_release(&text);
    runtime_release(runtime);
  } else {
    CHECK(runtime == NULL, "rejected process candidate returned a runtime");
    CHECK(status != NULL, "rejected process candidate returned no status");
    if (strcmp(mode, "wrong-name") == 0) {
      CHECK(
          status_domain(status) == DORT_ERROR_DOMAIN_SHIM &&
              status_code(status) == DORT_ERROR_INVALID_ARGUMENT,
          "wrong runtime name returned the wrong status");
      CHECK(
          strstr(status_message(status), "compiled exact basename") != NULL,
          "wrong runtime name returned the wrong explanation");
    } else if (strstr(mode, "build-info") != NULL) {
      CHECK(
          status_domain(status) == DORT_ERROR_DOMAIN_ORT_API &&
              status_code(status) == DORT_ERROR_RUNTIME_IDENTITY_MISMATCH,
          "wrong build information returned the wrong status");
      CHECK(
          strstr(status_message(status), "build information") != NULL ||
              strstr(status_message(status), "build identity marker") != NULL,
          "wrong build information returned the wrong explanation");
    } else {
      CHECK(
          status_domain(status) == DORT_ERROR_DOMAIN_LOADER &&
              status_code(status) == DORT_ERROR_RUNTIME_NOT_FOUND,
          "wrong or searched runtime returned the wrong status");
    }
    status_release(status);
  }

  if (preload != NULL) {
    dlclose(preload);
  }
  /* A successful open installs the documented process-lifetime identity
   * registry. Keep its owning shim mapped as Dart does; unloading the shim
   * would orphan that registry while retaining the runtime library. */
  if (!expect_success) {
    dlclose(shim);
  }
  return 0;
}
